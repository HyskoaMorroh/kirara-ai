"""卡住的出站队列必须在面板上看得见（需求 1「无缝衔接」、需求 5「回复慢」）。

`recover_on_startup()` 把上次进程被杀时留在 `sending` 的投递全部改成 `ambiguous`，
理由是正确的：那些动作可能已经到了对方，重发会造成重复消息。但 `ambiguous` 属于
`TERMINAL_STATUSES`，而 `_deliver_recipient_through` 有一条更强的规则——同一收件人
序列里只要存在更早的 `ambiguous` 或 `dead_letter`，后续投递**直接返回、不再发送**
（`outbox.py:372-376`）。

于是 `docker compose down` 之后可能出现这样的局面：某个群或某个人从此收不到任何回复，
而适配器状态是 `connected`、日志里没有错误、投递接口每次都「成功返回」。用户看到的是
「机器人不理我了」，运维看到的是一切正常。

队列计数其实已经采集了（`_outbox_counts()` 每次读健康快照时都算一遍，
并顺带当作存储写入探针），也已经放进 `AdapterHealthSnapshot.outbox`。缺的是
**没有任何地方把它变成一句话**：readiness 不看它，WebUI 除了类型声明外零渲染。
一个采集了却无人消费的指标，与没有采集没有区别。

## 判据

`ambiguous` 与 `dead_letter` 不是「计数偏高」这类需要判断阈值的指标——**只要大于 0
就必然有人收不到消息**，因为它们会挡住同一收件人后面所有的投递。因此：

1. **readiness 必须报**，且要说清处置：这两类都需要人去确认那一页到底有没有发出去，
   重试不会解决（重试正是被刻意禁止的那件事）。
2. **不能盖住更紧急的状态**：凭据被拒、存储不可写都要求先改配置，
   队列卡住排在它们之后。
3. **`retry_wait` 不算卡住**：那是正在退避重试，会自己好。
4. **读不到队列时不报**：没配数据库的部署 `outbox` 为 `None`，
   那是「没有这个能力」而不是「队列空的」。
"""

from __future__ import annotations

from kirara_ai.im.adapter import AdapterHealthSnapshot
from kirara_ai.web.api.system.readiness import _im_availability
from tests.web.api.system.test_readiness_im_states import make_config, make_manager


def _check(**snapshot_kwargs):
    return _im_availability(
        make_config("qq"),
        make_manager({"qq": AdapterHealthSnapshot(**snapshot_kwargs)}),
    )


class TestAStuckQueueIsReported:
    def test_an_ambiguous_delivery_downgrades_the_check(self):
        """链路是通的，但那个收件人后面所有消息都发不出去了。"""
        check = _check(
            status="connected",
            outbox={"queued": 0, "accepted": 12, "ambiguous": 1, "dead_letter": 0},
        )

        assert check.status == "warn"

    def test_a_dead_letter_downgrades_the_check(self):
        check = _check(
            status="connected",
            outbox={"queued": 0, "accepted": 12, "ambiguous": 0, "dead_letter": 2},
        )

        assert check.status == "warn"

    def test_the_summary_says_the_queue_is_blocked(self):
        check = _check(
            status="connected",
            outbox={"accepted": 3, "ambiguous": 1, "dead_letter": 0},
        )

        assert "投递" in check.summary or "队列" in check.summary

    def test_the_remediation_does_not_say_to_retry(self):
        """重发可能造成重复消息，那正是这个状态被刻意隔离的原因。"""
        check = _check(
            status="connected",
            outbox={"accepted": 3, "ambiguous": 1, "dead_letter": 0},
        )

        assert "重试" not in check.remediation

    def test_the_remediation_says_to_confirm_on_the_client(self):
        check = _check(
            status="connected",
            outbox={"accepted": 3, "ambiguous": 1, "dead_letter": 0},
        )

        assert "确认" in check.remediation

    def test_the_counts_are_in_the_evidence(self):
        check = _check(
            status="connected",
            outbox={"accepted": 3, "ambiguous": 1, "dead_letter": 2},
        )

        assert check.evidence["outbox_ambiguous_count"] == 1
        assert check.evidence["outbox_dead_letter_count"] == 2


class TestNormalQueuesArePassing:
    def test_an_empty_queue_keeps_the_check_passing(self):
        check = _check(
            status="connected",
            outbox={"queued": 0, "accepted": 0, "ambiguous": 0, "dead_letter": 0},
        )

        assert check.status == "pass"

    def test_retry_wait_is_not_a_stuck_queue(self):
        """那是正在退避重试，会自己好。"""
        check = _check(
            status="connected",
            outbox={"queued": 1, "retry_wait": 2, "ambiguous": 0, "dead_letter": 0},
        )

        assert check.status == "pass"

    def test_a_missing_outbox_is_not_treated_as_stuck(self):
        """没配数据库的部署没有这个能力，不是「队列空的」。"""
        check = _check(status="connected", outbox=None)

        assert check.status == "pass"

    def test_an_adapter_without_health_is_unaffected(self):
        check = _im_availability(make_config("qq"), make_manager({"qq": None}))

        assert check.status == "pass"


class TestMoreUrgentStatesWin:
    def test_a_rejected_credential_still_wins(self):
        """凭据被拒要求先改配置；队列卡住排在它之后。"""
        check = _im_availability(
            make_config("qq", "qq2"),
            make_manager(
                {
                    "qq": AdapterHealthSnapshot(status="credential_rejected"),
                    "qq2": AdapterHealthSnapshot(
                        status="connected", outbox={"ambiguous": 1}
                    ),
                }
            ),
        )

        assert "被拒绝" in check.summary

    def test_storage_unavailable_still_wins(self):
        check = _im_availability(
            make_config("qq", "qq2"),
            make_manager(
                {
                    "qq": AdapterHealthSnapshot(status="storage_unavailable"),
                    "qq2": AdapterHealthSnapshot(
                        status="connected", outbox={"ambiguous": 1}
                    ),
                }
            ),
        )

        assert "不可写" in check.summary
