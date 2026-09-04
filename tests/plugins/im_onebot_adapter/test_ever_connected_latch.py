"""上游连上过这件事，不能靠「有人读过状态面板」来记住。

发现过程
------
需求 1 的报障是「`docker compose down && pull && up -d` 之后 QQ 显示未连接」。
`reconnecting` 状态就是为它加的：上游反向 WebSocket 掉线后自己会回连，
这段时间报「正在重连」而不是「未连接」，否则操作者会去重查地址与 Token，
而那两项从来没错。

但那条路径有一个前提没被满足。`_ever_connected` 此前**只在
`get_health_snapshot()` 里**被置位——也就是说，它记的不是「上游连上过」，
而是「上游连着的时候有人读过一次状态」。而 `_note_upstream_disconnected()`
在 `_ever_connected` 为假时直接返回、不开重连窗口。

于是：上游连上、随后掉线，期间没有任何一次快照读取 → 窗口没开 →
状态是 `disconnected` 而不是 `reconnecting`。

现场恰好落在这个组合里：compose 重启发生在没人看面板的时候。
`down` 之后进程重建、计数器归零，上游拨入、几分钟后镜像 pull 完又断开重连——
整个过程没有一次 HTTP 读取，于是面板打开时看到的是「未连接」。

这一组测试锁住的边界
------------------
1. 连上过就是连上过，与有没有人观察无关（这一条是核心）。
2. 心跳同样算「连上过」：它和 lifecycle 一样证明链路活着。
3. 从未连上时不能进 `reconnecting`——那种处境叫「还在等第一次连接」，
   处置完全不同。
4. 读取快照仍然不改变结论（只是不再是**唯一**的置位点）。
"""

from __future__ import annotations

import pytest

from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.ioc.inject import Inject
from kirara_ai.plugins.im_onebot_adapter.adapter import OneBotAdapter
from kirara_ai.plugins.im_onebot_adapter.config import OneBotConfig


def make_adapter(config: OneBotConfig | None = None) -> OneBotAdapter:
    container = DependencyContainer()
    container.register(OneBotConfig, config or OneBotConfig())
    adapter = Inject(container).create(OneBotAdapter)()
    adapter._started = True
    adapter._ever_started = True
    return adapter


class _Event(dict):
    """`aiocqhttp` 的事件对象在取值上等价于 dict。"""


def _lifecycle_connect(self_id: str = "100") -> _Event:
    return _Event(
        post_type="meta_event",
        meta_event_type="lifecycle",
        sub_type="connect",
        self_id=self_id,
    )


def _lifecycle_disconnect(self_id: str = "100") -> _Event:
    return _Event(
        post_type="meta_event",
        meta_event_type="lifecycle",
        sub_type="disconnect",
        self_id=self_id,
    )


def _heartbeat(self_id: str = "100") -> _Event:
    return _Event(
        post_type="meta_event",
        meta_event_type="heartbeat",
        self_id=self_id,
    )


class TestConnectedOnceIsRememberedWithoutBeingObserved:
    @pytest.mark.asyncio
    async def test_connect_then_drop_without_any_snapshot_read_is_reconnecting(self):
        """核心用例：整个过程一次都不读快照。

        坏版本在这一条上返回 `disconnected`——而这正是「重启后显示未连接」
        那个报障的形状。
        """
        adapter = make_adapter()

        await adapter._handle_meta(_lifecycle_connect())
        await adapter._handle_meta(_lifecycle_disconnect())

        # 第一次读取发生在掉线之后，也就是运维打开面板的那一刻。
        assert adapter.get_health_snapshot().status == "reconnecting"

    @pytest.mark.asyncio
    async def test_a_heartbeat_alone_also_counts_as_having_connected(self):
        """心跳与 lifecycle 一样证明链路活着。

        只认 lifecycle 会漏掉一种真实形态：Kirara 重启后上游没有重发
        `lifecycle connect`，而是直接继续发心跳。
        """
        adapter = make_adapter()

        await adapter._handle_meta(_heartbeat())
        await adapter._handle_meta(_lifecycle_disconnect())

        assert adapter.get_health_snapshot().status == "reconnecting"

    @pytest.mark.asyncio
    async def test_reading_the_snapshot_while_connected_still_works(self):
        """读快照仍然置位——它只是不再是唯一的置位点。"""
        adapter = make_adapter()

        await adapter._handle_meta(_lifecycle_connect())
        assert adapter.get_health_snapshot().status == "connected"
        await adapter._handle_meta(_lifecycle_disconnect())

        assert adapter.get_health_snapshot().status == "reconnecting"


class TestNeverConnectedIsStillADifferentSituation:
    @pytest.mark.asyncio
    async def test_a_drop_without_ever_connecting_does_not_become_reconnecting(self):
        """从未连上过时不存在「重连」，只有「还在等第一次连接」。

        把它显示成「正在重连」会让操作者等一件不会发生的事：
        上游根本没拨进来过，要查的是地址与 Token。
        """
        adapter = make_adapter()
        adapter._note_started(now=0.0)

        # 没有任何 connect / heartbeat，直接来一条 disconnect。
        await adapter._handle_meta(_lifecycle_disconnect())

        # 报 `disconnected`：上游确实够到了我们并说它要断开，但从未建立起可用连接。
        # 这里唯一的硬边界是**不能**是 `reconnecting`——那会让操作者等一件
        # 不会发生的事。具体落在 disconnected 还是 waiting 由既有的
        # `_connection_status` 优先级决定，不属于本次改动。
        assert adapter.get_health_snapshot(now=1.0).status != "reconnecting"
        grace = adapter.config.initial_connect_grace_seconds
        assert adapter.get_health_snapshot(now=grace + 1.0).status != "reconnecting"

    @pytest.mark.asyncio
    async def test_the_reconnect_window_is_not_opened_before_a_first_connection(self):
        adapter = make_adapter()

        await adapter._handle_meta(_lifecycle_disconnect())

        assert adapter._reconnect_window_opened_at is None
