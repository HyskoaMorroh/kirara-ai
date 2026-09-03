"""仓库列表要能看出「这个仓库里有多少技能」（需求 10）。

需求 10 点名「Skills 管理（含发现技能）」。参考界面的仓库管理页每一行是
「仓库全名 + 分支 + 灰底徽章 `识别到 N 个技能`」
（`docs/superpowers/plans/ccs-ui-inventory.md` 的 4.2.2 一节，
截图实际数据是 864 / 22 / 20 / 11 四个数）。

本项目的仓库记录只有四个字段（owner / name / branch / enabled）。缺这个数的
后果不是「少一个装饰」：注册一个仓库之后，界面上完全看不出它有没有用——
一个 owner/name 拼错、分支写错、或者压根不含 `SKILL.md` 的仓库，
与一个装着几百个技能的仓库长得一模一样，都是「已启用」。
用户要点进「发现」才知道，而那要出一次网、下载整个仓库归档。

`discover_repository()` 本来就会返回逐条清单——数量是它的自然副产品。
缺的只是把它记下来：发现一次之后，那个数字就该留在仓库行上。

这组测试锁住的边界：

1. **`None` 与 `0` 严格分开。** `None` 是「还没发现过」，`0` 是「发现过、里面
   一个技能都没有」——后者才是「这个仓库配错了」的信号，而前者只是还没查。
   合成一个数会让刚注册的仓库看起来是配错的。
2. **发现之后自动记下来**，不需要用户再点一次别的按钮。
3. **失败的发现不写数**：一次网络错误不该把「有 864 个」改写成 0。
4. **记数不改变启用状态**：这两件事无关，而顺带改状态会让一次只读的查询
   变成一次配置写入。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kirara_ai.plugin_manager.resource_lifecycle import ResourceLifecycleService
from kirara_ai.plugin_manager.resource_sources import ResourceSourceService


@pytest.fixture()
def lifecycle(tmp_path: Path) -> ResourceLifecycleService:
    return ResourceLifecycleService(tmp_path / "data")


def _repository(lifecycle: ResourceLifecycleService, **overrides) -> dict:
    payload = {"owner": "anthropics", "name": "skills", "branch": "main", "enabled": True}
    payload.update(overrides)
    return lifecycle.upsert_source_repository(
        payload["owner"], payload["name"], payload["branch"], enabled=payload["enabled"]
    )


class TestTheField:
    def test_a_new_repository_has_no_count_yet(self, lifecycle: ResourceLifecycleService):
        """刚注册的仓库是「还没发现过」，不是「里面有 0 个」。

        写成 0 会让它看起来像一个配错的仓库——而那正是这个数字要区分的东西。
        """
        record = _repository(lifecycle)

        assert record["discovered_skills"] is None

    def test_the_count_survives_a_reload(self, lifecycle: ResourceLifecycleService, tmp_path: Path):
        _repository(lifecycle)
        lifecycle.record_repository_discovery("anthropics", "skills", "main", count=20)

        reloaded = ResourceLifecycleService(tmp_path / "data")
        rows = {
            (item["owner"], item["name"], item["branch"]): item
            for item in reloaded.list_source_repositories()
        }

        assert rows[("anthropics", "skills", "main")]["discovered_skills"] == 20

    def test_zero_is_recorded_as_zero_not_dropped(self, lifecycle: ResourceLifecycleService):
        """发现过、里面一个都没有——这是「仓库配错了」唯一的信号。"""
        _repository(lifecycle)

        lifecycle.record_repository_discovery("anthropics", "skills", "main", count=0)

        assert lifecycle.list_source_repositories()[0]["discovered_skills"] == 0

    def test_a_negative_count_is_refused(self, lifecycle: ResourceLifecycleService):
        """负数没有含义；接受它等于让界面显示一个不可能的数。"""
        _repository(lifecycle)

        with pytest.raises(ValueError):
            lifecycle.record_repository_discovery("anthropics", "skills", "main", count=-1)

    def test_recording_for_an_unknown_repository_is_refused(
        self, lifecycle: ResourceLifecycleService
    ):
        """未注册的仓库不能凭一次记数被凭空创建出来。"""
        with pytest.raises(KeyError):
            lifecycle.record_repository_discovery("nobody", "nothing", "main", count=3)

    def test_recording_does_not_change_the_enabled_state(
        self, lifecycle: ResourceLifecycleService
    ):
        """记数与启用无关。顺带改状态会让一次只读查询变成一次配置写入。"""
        _repository(lifecycle, enabled=False)

        lifecycle.record_repository_discovery("anthropics", "skills", "main", count=7)

        row = lifecycle.list_source_repositories()[0]
        assert row["enabled"] is False
        assert row["discovered_skills"] == 7

    def test_it_only_touches_the_named_repository(self, lifecycle: ResourceLifecycleService):
        """同一个 owner/name 的不同分支是两条记录，不能一起被改。"""
        _repository(lifecycle, branch="main")
        _repository(lifecycle, branch="master")

        lifecycle.record_repository_discovery("anthropics", "skills", "main", count=20)

        rows = {item["branch"]: item["discovered_skills"] for item in lifecycle.list_source_repositories()}
        assert rows == {"main": 20, "master": None}

    def test_an_older_registry_without_the_field_still_loads(self, tmp_path: Path):
        """升级前写入的注册表没有这个字段，读进来必须照旧可用。

        新增字段最常见的破坏方式是把它做成必填：升级之后注册表直接载入失败，
        而那时用户手里已经没有可用的仓库清单了。
        """
        import json

        lifecycle = ResourceLifecycleService(tmp_path / "data")
        _repository(lifecycle)
        path = lifecycle.registry_path
        document = json.loads(path.read_text(encoding="utf-8"))
        for item in document["repositories"]:
            item.pop("discovered_skills", None)
        path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

        reloaded = ResourceLifecycleService(tmp_path / "data")

        assert reloaded.list_source_repositories()[0]["discovered_skills"] is None


class TestDiscoveryRecordsIt:
    def test_a_successful_discovery_records_the_count(
        self, lifecycle: ResourceLifecycleService, monkeypatch: pytest.MonkeyPatch
    ):
        """发现一次之后数字就该留在仓库行上，不需要用户再点一次别的按钮。"""
        _repository(lifecycle)
        sources = ResourceSourceService(lifecycle)
        monkeypatch.setattr(
            sources,
            "_download_bytes",
            lambda _url: _zip_with_skills(("alpha", "beta", "gamma")),
        )

        discovered = sources.discover_repository("anthropics", "skills", "main")

        assert len(discovered) == 3
        assert lifecycle.list_source_repositories()[0]["discovered_skills"] == 3

    def test_a_failed_discovery_leaves_the_previous_count(
        self, lifecycle: ResourceLifecycleService, monkeypatch: pytest.MonkeyPatch
    ):
        """一次网络错误不该把「有 3 个」改写成 0。

        把失败写成 0 比不写更糟：0 是「这个仓库配错了」的信号，
        而实际情况只是这一次没连上。
        """
        _repository(lifecycle)
        sources = ResourceSourceService(lifecycle)
        monkeypatch.setattr(
            sources, "_download_bytes", lambda _url: _zip_with_skills(("alpha", "beta", "gamma"))
        )
        sources.discover_repository("anthropics", "skills", "main")

        def _boom(_url):
            raise RuntimeError("network unreachable")

        monkeypatch.setattr(sources, "_download_bytes", _boom)
        with pytest.raises(Exception):
            sources.discover_repository("anthropics", "skills", "main")

        assert lifecycle.list_source_repositories()[0]["discovered_skills"] == 3

    def test_discovering_an_unregistered_repository_does_not_fail(
        self, lifecycle: ResourceLifecycleService, monkeypatch: pytest.MonkeyPatch
    ):
        """直查一个没登记的仓库是允许的，记数只是顺带——不能因此报错。

        `discover_repository` 的既有语义是「给一个坐标就能看里面有什么」，
        不要求先登记。为了记一个数而拒绝这条路径，是用一个新特性削掉一个旧能力。
        """
        sources = ResourceSourceService(lifecycle)
        monkeypatch.setattr(
            sources, "_download_bytes", lambda _url: _zip_with_skills(("alpha",))
        )

        discovered = sources.discover_repository("someone", "unregistered", "main")

        assert len(discovered) == 1
        assert lifecycle.list_source_repositories() == []


def _zip_with_skills(directories: tuple[str, ...]) -> bytes:
    """造一个与 GitHub 归档同形的 ZIP：顶层一个目录，下面各有 SKILL.md。"""
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("skills-main/README.md", "root readme\n")
        for directory in directories:
            archive.writestr(
                f"skills-main/{directory}/SKILL.md",
                f"---\nname: {directory}\ndescription: d\n---\nbody\n",
            )
    return buffer.getvalue()
