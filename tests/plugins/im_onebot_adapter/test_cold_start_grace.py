"""冷启动窗口内「还没连上」不是故障（需求 1 的现场报障）。

现场：`docker compose down && pull && up -d` 之后面板显示 QQ「未连接」，而
重启前是「已连接」。用户的登录态一直在 `./QQ` 与 `./llonebot` 卷里，没有丢。

时序（取自现场日志）：容器起来 → QQ 冷启动 → 扫码/免扫码登录 → LLOneBot 才
`Trying to connect to the websocket server` 拨进 Kirara。这一段在现场日志里跨了
**19 分钟**（`05:37:19` 启动，`05:56` 才 `QQ 登录成功`），最快也要 90 秒以上。
这段时间里 Kirara 侧不可能有任何连接——它是反向 WebSocket 的服务端，只能等。

此前 `get_health_snapshot()` 在这段时间返回 `waiting`，readiness 随之给出
「检查 IM 适配器运行状态、登录状态和连接心跳」——那是这个窗口里**最不该给的
建议**：心跳没有问题，上游还没起来而已。运维照着去查心跳、查令牌、查地址，
而这三项从来没错。

## 与 `reconnecting` 的区别

`reconnecting` 的前提是**曾经连上过**（`_ever_connected`）：链路刚断、上游会自己
回来。进程刚重启时那个闩锁是 `False`，所以 `reconnecting` 在冷启动路径上不可达。
两者是不同的处境，需要各自的状态：

- `initializing`：本进程还没等到第一次连接，且还在宽限期内 —— **等就行**；
- `waiting`：宽限期已过仍然没有任何上游连进来 —— **该去查了**；
- `reconnecting`：连上过、刚断、还在重连宽限期内 —— **等就行**。

## 四条边界

1. **有上限。** 等了半小时还没有上游，就是真的有问题，不能一直显示「正在启动」。
2. **可关闭。** 配 0 拿回旧行为（立刻显示 `waiting`）。
3. **只覆盖「从未连上」。** 连上过又断开的那条路径归 `reconnecting`，
   两者的宽限期长度也不同（首次连接要等 QQ 冷启动，重连不用）。
4. **`stop()` 之后不适用。** 手动停掉的适配器显示 `disconnected`，
   不能因为「刚 stop 完还在宽限期内」就说它正在启动。
"""

from __future__ import annotations

from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.ioc.inject import Inject
from kirara_ai.plugins.im_onebot_adapter.adapter import OneBotAdapter
from kirara_ai.plugins.im_onebot_adapter.config import OneBotConfig


def make_adapter(config: OneBotConfig | None = None) -> OneBotAdapter:
    container = DependencyContainer()
    container.register(OneBotConfig, config or OneBotConfig())
    return Inject(container).create(OneBotAdapter)()


def _freshly_started(now: float = 100.0, **config_kwargs) -> OneBotAdapter:
    """一个刚刚 `start()` 完、还没有任何上游连进来的适配器。"""
    adapter = make_adapter(OneBotConfig(**config_kwargs) if config_kwargs else None)
    adapter._started = True
    adapter._ever_started = True
    adapter._connection_status = "waiting"
    adapter._note_started(now=now)
    return adapter


class TestTheGraceWindowIsConfigurable:
    def test_the_config_declares_a_first_connect_grace(self):
        config = OneBotConfig()

        assert config.initial_connect_grace_seconds > 0

    def test_the_default_covers_a_qq_cold_start(self):
        """QQ 冷启动加登录在现场日志里超过 90 秒；默认必须覆盖到它。"""
        assert OneBotConfig().initial_connect_grace_seconds >= 120

    def test_it_can_be_turned_off(self):
        config = OneBotConfig(initial_connect_grace_seconds=0)

        assert config.initial_connect_grace_seconds == 0


class TestColdStartReportsInitializing:
    def test_inside_the_window_the_status_is_initializing(self):
        adapter = _freshly_started(now=100.0)

        snapshot = adapter.get_health_snapshot(now=130.0)

        assert snapshot.status == "initializing"

    def test_no_disconnect_reason_is_invented_during_the_window(self):
        """这个窗口里没有任何东西「断开」过，给原因码等于凭空造一个故障。"""
        adapter = _freshly_started(now=100.0)

        assert adapter.get_health_snapshot(now=130.0).last_disconnect_reason is None

    def test_past_the_window_it_becomes_waiting(self):
        """等了很久也没有上游，就该去查了——继续显示「正在启动」是掩盖故障。"""
        adapter = _freshly_started(now=100.0)
        grace = adapter.config.initial_connect_grace_seconds

        snapshot = adapter.get_health_snapshot(now=100.0 + grace + 1.0)

        assert snapshot.status == "waiting"

    def test_a_zero_grace_keeps_the_previous_behaviour(self):
        adapter = _freshly_started(now=100.0, initial_connect_grace_seconds=0)

        assert adapter.get_health_snapshot(now=101.0).status == "waiting"

    def test_a_connection_inside_the_window_reports_connected(self):
        """上游在宽限期内连进来时立刻显示已连接，不能被「正在启动」盖住。"""
        adapter = _freshly_started(now=100.0)
        adapter.connections["100"] = {"last_heartbeat": 110.0}

        assert adapter.get_health_snapshot(now=110.0).status == "connected"

    def test_the_window_does_not_reopen_after_the_first_connection(self):
        """连上过之后再断开归 `reconnecting` / `disconnected`，不能倒回「正在启动」。"""
        adapter = _freshly_started(now=100.0)
        adapter.connections["100"] = {"last_heartbeat": 110.0}
        assert adapter.get_health_snapshot(now=110.0).status == "connected"

        adapter.connections.pop("100", None)
        adapter._connection_status = "disconnected"
        adapter._last_disconnect_reason = "upstream_lifecycle_disconnect"

        status = adapter.get_health_snapshot(now=120.0).status
        assert status != "initializing"


class TestStopIsNotColdStart:
    def test_a_stopped_adapter_reports_disconnected_even_inside_the_window(self):
        """手动停掉的适配器不是「正在启动」，运维需要知道它被停了。"""
        adapter = _freshly_started(now=100.0)
        adapter._started = False
        adapter._connection_status = "disconnected"

        assert adapter.get_health_snapshot(now=110.0).status == "disconnected"

    def test_stopping_clears_the_start_baseline(self):
        """基线留着会让下一次 `start()` 之前的快照落在旧窗口里。"""
        adapter = _freshly_started(now=100.0)
        adapter._note_stopped()

        assert adapter._start_monotonic is None


class TestBeforeStartIsStillInitializing:
    def test_an_adapter_that_never_started_is_initializing(self):
        """既有行为不变：`start()` 还没跑完时也是「正在启动」。"""
        adapter = make_adapter()

        assert adapter.get_health_snapshot(now=1.0).status == "initializing"
