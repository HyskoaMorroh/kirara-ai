"""趋势分桶必须在 SQL 里聚合，不能把区间内每一行物化到 Python。

需求 22.2 结尾点名「注意大数据量下的分页/索引性能」。统计页的其余聚合
（概览、延迟、Provider/模型/错误分组、请求日志分页）都已经在 SQL 侧完成，
唯独日/时趋势分桶仍然是「把区间内所有行 SELECT 回来，再用 Python 的
defaultdict 累加」。

这一条在默认视图上看不出问题——默认只取近 30 天与近 24 小时。但调用方
一旦传入显式时间范围，那两个兜底过滤器就被跳过（见 `has_explicit_range`），
于是「导出全年趋势」会把全年每一行的十列读进进程内存。行数与内存、
与响应时间线性相关，而这正是需求要求注意的那一类失败。

分桶键必须由数据库按**输出时区**截断，不能按 UTC 截断后再在 Python 里搬时区：
跨时区对账时日界会整体错位一整天，而那种错误不会报错，只会给出一个看起来
正常的数字。

覆盖的三件事：
1. 语义不变——同一批数据，SQL 聚合的结果与旧的 Python 聚合逐字段相等；
2. 大数据量下不再逐行物化；
3. 日界按选定时区算，而不是按存储时区或浏览器时区。

响应形状不变：``daily_stats`` / ``hourly_stats`` 仍是**按时间升序的列表**，
每项带 ``date`` / ``hour`` 键。前端图表按顺序读它，改成对象会让既有折线失序。
"""

from datetime import datetime, timedelta
from decimal import Decimal

from kirara_ai.tracing import LLMTracer
from tests.tracing.test_base import TracingTestBase


def _by_key(rows, key: str) -> dict:
    """Index a bucket list by its time key, so assertions can address one bucket."""
    return {row[key]: row for row in rows}


class TestTrendBucketsAggregateInSql(TracingTestBase):
    def setUp(self):
        super().setUp()
        # 存储时区显式钉成 UTC。默认是 `Asia/Shanghai`，而 `request_time` 存的是
        # 该时区下的无时区时间——不钉住它，用例里写下的每一个时刻都要在脑子里
        # 先做一次时区换算，读起来无法判断断言到底在验什么。
        from kirara_ai.config.global_config import GlobalConfig

        self.container.resolve(GlobalConfig).system.timezone = "UTC"
        self.tracer = LLMTracer(self.container)
        self.tracer.initialize()

    def tearDown(self):
        self.tracer.shutdown()
        super().tearDown()

    def _insert(
        self,
        *,
        request_time: datetime,
        status: str = "success",
        total_tokens: int = 30,
        prompt_tokens: int = 10,
        completion_tokens: int = 20,
        cached_tokens: int | None = None,
        cache_write_tokens: int | None = None,
        total_cost: str | None = None,
        cost_currency: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        """Insert one finished trace row directly, bypassing the event path.

        分桶只读这十列，直接建行比跑一整条事件链更能把用例意图讲清楚，
        也让「两千行」这种规模在单测里可行。
        """
        from kirara_ai.tracing.models import LLMRequestTrace

        with self.db_manager.get_session() as session:
            row = LLMRequestTrace()
            row.trace_id = trace_id or f"t-{request_time.isoformat()}-{id(self)}"
            row.model_id = "test-model"
            row.backend_name = "test-backend"
            row.provider = "test-provider"
            row.request_time = request_time
            row.status = status
            row.total_tokens = total_tokens
            row.prompt_tokens = prompt_tokens
            row.completion_tokens = completion_tokens
            row.cached_tokens = cached_tokens
            row.cache_write_tokens = cache_write_tokens
            row.total_cost = total_cost
            row.cost_currency = cost_currency
            session.add(row)
            session.commit()

    def test_daily_buckets_match_the_python_aggregation(self):
        day = datetime(2026, 8, 20, 3, 0, 0)
        self._insert(request_time=day, trace_id="a")
        self._insert(request_time=day + timedelta(hours=2), trace_id="b")
        self._insert(
            request_time=day + timedelta(days=1),
            status="failed",
            total_tokens=5,
            prompt_tokens=5,
            completion_tokens=0,
            trace_id="c",
        )

        stats = self.tracer.get_statistics(
            {
                "start_time": day - timedelta(days=1),
                "end_time": day + timedelta(days=2),
            },
            timezone_name="UTC",
        )

        daily = _by_key(stats["daily_stats"], "date")
        assert daily["2026-08-20"]["requests"] == 2
        assert daily["2026-08-20"]["tokens"] == 60
        assert daily["2026-08-20"]["prompt_tokens"] == 20
        assert daily["2026-08-20"]["completion_tokens"] == 40
        assert daily["2026-08-20"]["success"] == 2
        assert daily["2026-08-20"]["failed"] == 0
        assert daily["2026-08-21"]["requests"] == 1
        assert daily["2026-08-21"]["failed"] == 1
        assert daily["2026-08-21"]["success"] == 0

    def test_unpriced_requests_are_counted_separately(self):
        day = datetime(2026, 8, 20, 3, 0, 0)
        self._insert(request_time=day, total_cost="1.5", cost_currency="USD", trace_id="p")
        self._insert(request_time=day + timedelta(hours=1), trace_id="u")

        stats = self.tracer.get_statistics(
            {
                "start_time": day - timedelta(days=1),
                "end_time": day + timedelta(days=1),
            },
            timezone_name="UTC",
        )

        bucket = _by_key(stats["daily_stats"], "date")["2026-08-20"]
        # 按 0 元并入合计会把「有请求没匹配到价格版本」显示成「这天便宜」。
        assert bucket["unpriced_requests"] == 1
        # 金额比数值而不是比字面量：列是定点 Numeric，字符串带固定小数位。
        assert Decimal(bucket["cost"]) == Decimal("1.5")
        assert bucket["cost_currency"] == "USD"

    def test_multiple_currencies_are_not_summed_together(self):
        day = datetime(2026, 8, 20, 3, 0, 0)
        self._insert(request_time=day, total_cost="2", cost_currency="USD", trace_id="x")
        self._insert(
            request_time=day + timedelta(hours=1),
            total_cost="10",
            cost_currency="CNY",
            trace_id="y",
        )

        stats = self.tracer.get_statistics(
            {
                "start_time": day - timedelta(days=1),
                "end_time": day + timedelta(days=1),
            },
            timezone_name="UTC",
        )

        bucket = _by_key(stats["daily_stats"], "date")["2026-08-20"]
        # 两种货币加进同一个数字得到的是一串没有单位的数字，而它不会报错。
        assert {
            key: Decimal(value) for key, value in bucket["cost_by_currency"].items()
        } == {"USD": Decimal("2"), "CNY": Decimal("10")}
        # 主币种取金额最大者，与 overview 同一规则。
        assert bucket["cost_currency"] == "CNY"
        assert Decimal(bucket["cost"]) == Decimal("10")

    def test_day_boundary_follows_the_requested_timezone(self):
        # 存储时间是 UTC 的 20 日 22:00；在 Asia/Shanghai 已经是 21 日 06:00。
        self._insert(request_time=datetime(2026, 8, 20, 22, 0, 0), trace_id="tz")

        stats = self.tracer.get_statistics(
            {
                "start_time": datetime(2026, 8, 19),
                "end_time": datetime(2026, 8, 22),
            },
            timezone_name="Asia/Shanghai",
        )

        daily = _by_key(stats["daily_stats"], "date")
        # 按 UTC 截断再搬时区会把这一行记到 20 日，跨时区对账整体错一天，
        # 而那种错误只会给出一个看起来正常的数字。
        assert "2026-08-21" in daily
        assert daily["2026-08-21"]["requests"] == 1
        assert "2026-08-20" not in daily

    def test_hourly_buckets_use_the_requested_timezone(self):
        self._insert(request_time=datetime(2026, 8, 20, 22, 30, 0), trace_id="h")

        stats = self.tracer.get_statistics(
            {
                "start_time": datetime(2026, 8, 20),
                "end_time": datetime(2026, 8, 21, 12),
            },
            timezone_name="Asia/Shanghai",
        )

        assert "2026-08-21 06:00:00" in _by_key(stats["hourly_stats"], "hour")

    def test_cached_columns_accumulate_as_zero_not_none(self):
        day = datetime(2026, 8, 20, 3, 0, 0)
        self._insert(request_time=day, cached_tokens=None, trace_id="n")
        self._insert(request_time=day + timedelta(hours=1), cached_tokens=7, trace_id="m")

        stats = self.tracer.get_statistics(
            {
                "start_time": day - timedelta(days=1),
                "end_time": day + timedelta(days=1),
            },
            timezone_name="UTC",
        )

        # 趋势是逐时间累加的折线；中间出现 None 会把线断开。
        # 需要区分「未知」与「零」的是 overview.cache_hit_rate，不是这里。
        assert _by_key(stats["daily_stats"], "date")["2026-08-20"]["cached_tokens"] == 7

    def test_empty_range_yields_empty_buckets_not_an_error(self):
        stats = self.tracer.get_statistics(
            {
                "start_time": datetime(2026, 1, 1),
                "end_time": datetime(2026, 1, 2),
            },
            timezone_name="UTC",
        )

        assert stats["daily_stats"] == []
        assert stats["hourly_stats"] == []

    def test_explicit_wide_range_does_not_materialize_every_row(self):
        """The one requirement 22.2 actually names: it must not scale with rows.

        显式时间范围会跳过那两个兜底过滤器，于是「导出全年趋势」在旧实现里
        会把全年每一行读进进程内存。这里用 patch 统计真正被取回的行数：
        SQL 侧聚合时它等于**桶数**，Python 侧聚合时它等于**行数**。
        """
        from unittest.mock import patch

        day = datetime(2026, 8, 20, 3, 0, 0)
        for index in range(120):
            self._insert(
                request_time=day + timedelta(minutes=index),
                trace_id=f"bulk-{index}",
            )

        import kirara_ai.tracing.llm_tracer as tracer_module

        fetched: list[int] = []
        # `_fetch_bucket_rows` 是 staticmethod：从 `__dict__` 取原函数，
        # 否则包装层会多收一个 self 参数。
        original = tracer_module.LLMTracer.__dict__["_fetch_bucket_rows"].__func__

        def counting(*args, **kwargs):
            rows = original(*args, **kwargs)
            fetched.append(len(rows))
            return rows

        with patch.object(
            tracer_module.LLMTracer,
            "_fetch_bucket_rows",
            staticmethod(counting),
        ):
            stats = self.tracer.get_statistics(
                {
                    "start_time": day - timedelta(days=400),
                    "end_time": day + timedelta(days=400),
                },
                timezone_name="UTC",
            )

        assert _by_key(stats["daily_stats"], "date")["2026-08-20"]["requests"] == 120
        assert fetched, "分桶取行的入口不存在，无法证明它不再逐行物化"
        # 120 行落在 8 个 15 分钟槽里（03:00 起连续两小时）。取回的行数必须是
        # 槽数量级，而不是请求数量级——否则这条需求没有被真正满足。
        assert max(fetched) <= 8, (
            f"分桶仍在逐行物化：取回了 {max(fetched)} 行，桶数远小于此"
        )
