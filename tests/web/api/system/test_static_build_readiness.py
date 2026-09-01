"""readiness 要报出「静态构建与后端版本不一致」（需求 14 的可观测性）。

`static_build_freshness` 已经能判定落差，但判定不接进任何出口就等于没有：
运维看不到它，CI 也拿不到它。

readiness 是这个信息的正确位置——它就是"这台机器现在能不能正常服务"的清单。
落差的后果恰好符合 readiness 的语义：服务能起、API 能通、但用户看到的界面
不是这个后端配套的那一份。

三条判定纪律：

- **不一致是 warn 而不是 fail。** 服务确实在正常响应，只是界面旧了。判成 fail
  会让健康检查把一个可用的实例摘下线。
- **读不到版本是 skip 而不是 warn。** 纯 API 部署没有静态目录，那是合法形态；
  报 warn 会让运维去修一个不存在的故障。
- **修复建议要说出具体动作。** "版本不一致"没有信息量，"在 webui/ 下重新构建
  并把 dist 拷到 web/"才是可执行的。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kirara_ai.web.api.system.readiness import CHECK_IDS


def test_the_check_id_is_registered():
    """自检：新检查项必须进 CHECK_IDS，否则它不会被 run_readiness_checks 调用。"""
    assert "static_build_current" in CHECK_IDS


class TestStaticBuildCheck:
    """`_static_build_current` 把版本落差翻成一条 readiness 结论。"""

    def test_matching_versions_pass(self, tmp_path: Path):
        from kirara_ai.web.api.system.readiness import _static_build_current

        result = _static_build_current(installed="3.3.0-b14", expected="3.3.0-b14")

        assert result.status == "pass"
        assert result.id == "static_build_current"

    def test_a_stale_build_warns_rather_than_fails(self):
        """服务在正常响应，只是界面旧了。fail 会让健康检查摘下线一个可用实例。"""
        from kirara_ai.web.api.system.readiness import _static_build_current

        result = _static_build_current(installed="0.1.1-beta.3", expected="3.3.0-b14")

        assert result.status == "warn"

    def test_the_warning_names_both_versions(self):
        """只说"不一致"没有信息量：要能看出旧的是哪一版、应该是哪一版。"""
        from kirara_ai.web.api.system.readiness import _static_build_current

        result = _static_build_current(installed="0.1.1-beta.3", expected="3.3.0-b14")
        payload = result.model_dump(mode="json")
        text = f"{result.summary} {result.remediation} {payload}"

        assert "0.1.1-beta.3" in text
        assert "3.3.0-b14" in text

    def test_the_remediation_says_what_to_do(self):
        """修复建议要给具体动作，而不是复述现象。"""
        from kirara_ai.web.api.system.readiness import _static_build_current

        result = _static_build_current(installed="0.1.1-beta.3", expected="3.3.0-b14")

        assert "构建" in result.remediation or "build" in result.remediation.lower()

    def test_a_missing_build_is_skipped(self):
        """纯 API 部署没有静态目录，这是合法形态，不该报成问题。"""
        from kirara_ai.web.api.system.readiness import _static_build_current

        result = _static_build_current(installed="unknown", expected="3.3.0-b14")

        assert result.status == "skip"

    def test_an_unknown_backend_version_is_skipped(self):
        """后端版本读不到时无从判断落差。"""
        from kirara_ai.web.api.system.readiness import _static_build_current

        result = _static_build_current(installed="3.3.0-b14", expected="unknown")

        assert result.status == "skip"

    def test_the_details_are_structured_not_only_prose(self):
        """结构化字段让 CI 能判断，而不是靠人肉读一句话。"""
        from kirara_ai.web.api.system.readiness import _static_build_current

        payload = _static_build_current(
            installed="0.1.1-beta.3", expected="3.3.0-b14"
        ).model_dump(mode="json")

        evidence = payload["evidence"]
        assert evidence.get("installed_version") == "0.1.1-beta.3"
        assert evidence.get("expected_version") == "3.3.0-b14"
