"""发布计划必须区分「本地已打但没推」与「远端已发布」。

需求 23.2 要求「智能升级必须重新核验本地和远端 Tag，自动跳过冲突版本，
……**不得把离线候选当作正式发布版本**」。

`occupied_git_versions()` 把 `_local_git_tags()` 与 `_remote_git_tags()` 合成
一个无区分的集合。碰撞检测本身没错——两种情况下那个版本号都不能再用——
但输出里丢掉了「它为什么被占用」，而这恰恰是那句要求的对象：

- **远端已有** = 已经发布过，不能重用，也不该删；
- **仅本地有** = 一次没推成功的打标（网络断了、门禁没过、或者被人手工
  `git tag` 试了一下）。它占住了一个号，但**没有任何发布产物与它对应**。

这个区别决定处置。运维看到「3.3.0b12 已被占用」时的下一步完全不同：
远端占用要接着往下找号；仅本地占用则可能是上一次发布中断留下的残留，
删掉那个本地 tag 再重试才是对的，而不是把版本号一路往上跳。

把两者显示成同一个词的实际后果是版本号被无谓地跳过：一次失败的打标之后，
每次重跑计划都会跳过那个号，而它其实从未发布。几次之后版本号里出现空洞，
而没有任何地方记录那些号去哪了。

三条边界：
1. **碰撞判定不变。** 两类都算占用，行为逐字节一致——这不是放宽，是把已知
   信息暴露出来。
2. **`--local-only` 下远端集合为空**，不是「未知」：那是显式声明「不查远端」。
3. **旧字段保留。** `occupied` 仍是全集，既有调用方与 JSON 消费者不受影响。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_version_module():
    spec = importlib.util.spec_from_file_location(
        "kirara_version_script", PROJECT_ROOT / "scripts" / "version.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


version_script = _load_version_module()


@pytest.fixture
def fake_tags(monkeypatch: pytest.MonkeyPatch):
    """Control both tag sources without touching the real repository."""

    state = {"local": set(), "remote": set()}

    monkeypatch.setattr(
        version_script, "_local_git_tags", lambda _root: set(state["local"])
    )
    monkeypatch.setattr(
        version_script, "_remote_git_tags", lambda _root, _remote: set(state["remote"])
    )
    monkeypatch.setattr(
        version_script, "resolve_release_remote", lambda _root: "origin"
    )
    return state


class TestOccupancyOriginIsReported:
    def test_remote_only_tag_is_reported_as_released(self, fake_tags):
        fake_tags["remote"] = {"v3.3.0b12"}

        report = version_script.occupied_release_versions(PROJECT_ROOT)

        assert report.released == ("3.3.0b12",)
        assert report.reserved_locally == ()
        # 全集保持不变：碰撞判定的行为逐字节一致。
        assert report.all_versions == ("3.3.0b12",)

    def test_local_only_tag_is_reported_as_reserved_not_released(self, fake_tags):
        fake_tags["local"] = {"v3.3.0b12"}

        report = version_script.occupied_release_versions(PROJECT_ROOT)

        # 这一条就是需求 23.2 那句「不得把离线候选当作正式发布版本」。
        assert report.reserved_locally == ("3.3.0b12",)
        assert report.released == ()
        assert report.all_versions == ("3.3.0b12",)

    def test_a_tag_present_on_both_sides_counts_as_released(self, fake_tags):
        fake_tags["local"] = {"v3.3.0b12"}
        fake_tags["remote"] = {"v3.3.0b12"}

        report = version_script.occupied_release_versions(PROJECT_ROOT)

        # 推成功之后本地那份不再是「仅本地」。已发布是更强的论断，取它。
        assert report.released == ("3.3.0b12",)
        assert report.reserved_locally == ()

    def test_local_only_mode_reports_nothing_as_released(self, fake_tags):
        fake_tags["local"] = {"v3.3.0b12"}
        fake_tags["remote"] = {"v3.3.0b13"}

        report = version_script.occupied_release_versions(
            PROJECT_ROOT, local_only=True
        )

        # `--local-only` 是显式声明「不查远端」，因此远端集合为空而不是未知。
        assert report.reserved_locally == ("3.3.0b12",)
        assert report.released == ()
        assert "3.3.0b13" not in report.all_versions

    def test_unparseable_tags_are_ignored_on_both_sides(self, fake_tags):
        fake_tags["local"] = {"nightly", "v3.3.0b12"}
        fake_tags["remote"] = {"latest"}

        report = version_script.occupied_release_versions(PROJECT_ROOT)

        assert report.all_versions == ("3.3.0b12",)
        assert report.reserved_locally == ("3.3.0b12",)

    def test_versions_are_sorted_by_release_order_not_lexically(self, fake_tags):
        # 字典序会把 b9 排在 b10 之后，读起来像「最高版本是 b9」。
        fake_tags["remote"] = {"v3.3.0b9", "v3.3.0b10"}

        report = version_script.occupied_release_versions(PROJECT_ROOT)

        assert report.released == ("3.3.0b9", "3.3.0b10")


class TestBackwardCompatibility:
    def test_the_legacy_helper_still_returns_the_full_set(self, fake_tags):
        fake_tags["local"] = {"v3.3.0b12"}
        fake_tags["remote"] = {"v3.3.0b11"}

        occupied = version_script.occupied_git_versions(PROJECT_ROOT)

        # 既有调用方拿到的仍是全集：这条改动只增加信息，不改变任何判定。
        assert occupied == {"3.3.0b11", "3.3.0b12"}

    def test_remote_and_local_only_together_still_rejected(self, fake_tags):
        with pytest.raises(ValueError):
            version_script.occupied_release_versions(
                PROJECT_ROOT, remote="origin", local_only=True
            )


class TestThePlanCarriesTheDistinction:
    def test_plan_exposes_reserved_and_released_separately(self, fake_tags):
        fake_tags["local"] = {"v3.3.0b12"}
        fake_tags["remote"] = {"v3.3.0b11"}

        plan = version_script.build_release_plan(PROJECT_ROOT, kind="beta")

        assert "3.3.0b12" in plan.reserved_locally
        assert "3.3.0b11" in plan.released
        # 候选仍然跳过两者：占用就是占用。
        assert plan.candidate not in plan.occupied

    def test_plan_payload_includes_both_lists(self, fake_tags):
        fake_tags["local"] = {"v3.3.0b12"}

        plan = version_script.build_release_plan(PROJECT_ROOT, kind="beta")
        payload = version_script._release_plan_payload(plan)

        # 运维读的是 `version.py plan` 的输出；只放进 NamedTuple 而不进 payload
        # 等于这条信息在产品上不存在。
        assert "reserved_locally" in payload
        assert "released" in payload
        assert "3.3.0b12" in payload["reserved_locally"]
