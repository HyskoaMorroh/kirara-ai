"""需求 18.1 的第五类状态：数据目录挂载错误。

18.1 要求把五种情况**分别**表达出来：容器刚启动、WebSocket 仍在重连、
凭据或设备身份丢失、**数据目录挂载错误**、上游拒绝连接。

前四类在 `AdapterHealthSnapshot.status` 上都有独立取值，唯独「数据目录挂载错误」
没有——它只存在于两个够不到的地方：

- `kirara_ai/config/__init__.py` 的启动期检查：只读挂载时进程直接起不来，
  此时 HTTP readiness 接口也读不到（进程没起），只剩 stdout 文本；
- `readiness.py` 的 `data_directories_writable`：只在进程已经起来时探测
  `DATA_PATH` 本身。

真正会被漏掉的是**运行期**的那一类：容器起来时卷是可写的，之后卷被重新挂成
只读、或磁盘写满。此时 WebSocket 还连着，适配器报 `connected`，
而每一条要落库的投递都在失败——面板上一切正常，消息在丢。

这些用例把那一类钉成一个独立状态。
"""

from __future__ import annotations

from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.ioc.inject import Inject
from kirara_ai.plugins.im_onebot_adapter.adapter import OneBotAdapter
from kirara_ai.plugins.im_onebot_adapter.config import OneBotConfig


class _BrokenOutbox:
    """A queue whose storage has gone away after a successful start."""

    def status_counts(self) -> dict[str, int]:
        raise OSError(30, "Read-only file system")


class _WorkingOutbox:
    def status_counts(self) -> dict[str, int]:
        return {
            "queued": 0,
            "sending": 0,
            "accepted": 3,
            "retry_wait": 0,
            "ambiguous": 0,
            "dead_letter": 0,
        }


def _adapter() -> OneBotAdapter:
    container = DependencyContainer()
    container.register(OneBotConfig, OneBotConfig())
    adapter = Inject(container).create(OneBotAdapter)()
    # 有 database_manager 才会去读队列状态；这里只需要它非 None。
    adapter.database_manager = object()
    return adapter


def test_a_connected_adapter_whose_storage_died_is_not_reported_as_healthy():
    adapter = _adapter()
    adapter._started = True
    adapter.connections["10001"] = {"last_heartbeat": 1.0}
    adapter._outbox = _WorkingOutbox()
    assert adapter.get_health_snapshot(now=1.0).status == "connected"

    # 卷被重新挂成只读：链路还在，但任何落库都会失败。
    adapter._outbox = _BrokenOutbox()

    snapshot = adapter.get_health_snapshot(now=1.0)

    assert snapshot.status == "storage_unavailable"
    assert snapshot.last_disconnect_reason == "data_directory_unwritable"
    # 链路本身仍然是连着的，这两个字段不能因为存储故障而说谎。
    assert snapshot.websocket_connected is True
    assert snapshot.connected_account_count == 1


def test_storage_recovery_returns_the_adapter_to_its_real_link_state():
    adapter = _adapter()
    adapter._started = True
    adapter.connections["10001"] = {"last_heartbeat": 1.0}
    adapter._outbox = _BrokenOutbox()
    assert adapter.get_health_snapshot(now=1.0).status == "storage_unavailable"

    adapter._outbox = _WorkingOutbox()

    snapshot = adapter.get_health_snapshot(now=1.0)

    assert snapshot.status == "connected"
    # 存储恢复后旧原因码必须清掉，否则面板会一直显示一个已经不成立的故障。
    assert snapshot.last_disconnect_reason is None


def test_storage_failure_does_not_mask_a_real_connection_failure():
    """凭据被拒 + 存储故障同时发生时，先报凭据——那是用户唯一能修的一件事。

    反过来（存储故障盖住凭据被拒）会让操作者去查磁盘，而真正的原因是 Token。
    """
    adapter = _adapter()
    adapter._started = True
    adapter._record_connection_failure(
        "access_token_mismatch", status="credential_rejected"
    )
    adapter._outbox = _BrokenOutbox()

    snapshot = adapter.get_health_snapshot(now=1.0)

    assert snapshot.status == "credential_rejected"
    assert snapshot.last_disconnect_reason == "access_token_mismatch"


def test_an_adapter_without_persistence_never_reports_storage_failure():
    """没有 DatabaseManager 的部署不落库，也就没有「存储不可用」这回事。"""
    adapter = _adapter()
    adapter.database_manager = None
    adapter._started = True
    adapter.connections["10001"] = {"last_heartbeat": 1.0}

    snapshot = adapter.get_health_snapshot(now=1.0)

    assert snapshot.status == "connected"
    assert snapshot.outbox is None
