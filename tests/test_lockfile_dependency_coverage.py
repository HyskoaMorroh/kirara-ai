"""`pyproject.toml` 声明的每个依赖都必须出现在 `uv.lock` 里。

为什么需要这条门禁
----------------
本轮给 `pyproject.toml` 加了 12 个随包技能依赖（`lxml` / `openpyxl` /
`python-pptx` 等）后忘了跑 `uv lock`。预期是 CI 的 `uv sync --frozen` 会拦下来，
**实测没有**：那次 `Release Preflight` 是 success。

原因是 `uv sync --frozen` 同步的是项目自身，锁文件里缺少的新依赖不触发校验失败。
于是缺口的形状是：

* 本机装得起来（环境里早就有那些包）；
* CI 绿灯（`--frozen` 不检查这一项）；
* 只有用户拿 `uv sync` 或 `pip install` 从干净环境装完、真去用文档技能时，
  才在 `ImportError` 里发现。

同一个形状本轮已经踩过一次：`yarn.lock` 与 `package.json` 对不上，
CI 从干净环境 `--frozen-lockfile` 装不起来。那次是 CI 拦住了，这次没有。
两次都说明「锁文件与声明一致」需要被**显式断言**，不能指望某个工具顺带发现。

判据的几处细节
------------
* **名称规范化**：`pyproject` 写 `PyYAML`，锁文件里是 `pyyaml`；
  `python_magic` 与 `python-magic` 下划线连字符混用。按 PEP 503 归一化后再比。
* **extras 要剥掉**：`redis[hiredis]` 的包名是 `redis`。
* **环境标记要剥掉**：`python-magic-bin ; platform_system == 'Windows'`
  的包名是 `python-magic-bin`；它在锁文件里存在（uv 会解析所有平台的分支），
  因此仍然参与断言。
"""

from __future__ import annotations

import pathlib
import re
import tomllib

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _normalize(name: str) -> str:
    """PEP 503 名称归一化：小写，且 `-`/`_`/`.` 视为同一个分隔符。"""
    return re.sub(r"[-_.]+", "-", name.strip().lower())


def _declared_dependencies() -> set[str]:
    """从 `pyproject.toml` 取出声明的依赖包名（剥掉 extras、版本与环境标记）。"""
    data = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    names: set[str] = set()
    for raw in data["project"]["dependencies"]:
        # 先去环境标记（`; platform_system == 'Windows'`），再去 extras 与版本约束。
        spec = str(raw).split(";", 1)[0]
        spec = spec.split("[", 1)[0]
        spec = re.split(r"[<>=!~ ]", spec, maxsplit=1)[0]
        if spec.strip():
            names.add(_normalize(spec))
    return names


def _locked_packages() -> set[str]:
    """取 `uv.lock` 里所有 `[[package]]` 的名字。"""
    lock = (PROJECT_ROOT / "uv.lock").read_text(encoding="utf-8")
    return {_normalize(name) for name in re.findall(r'^name = "([^"]+)"', lock, re.M)}


def test_every_declared_dependency_is_locked():
    """这一条拦的正是本轮犯过的错：加了依赖没跑 `uv lock`。

    失败信息直接给出缺失的包名与修复命令——不写清楚的话，
    下一个人看到的是一个红色断言和一份需要自己比对的两份清单。
    """
    declared = _declared_dependencies()
    locked = _locked_packages()
    missing = sorted(declared - locked)

    assert not missing, (
        "以下依赖在 pyproject.toml 里声明了但不在 uv.lock 里："
        f"{missing}。执行 `uv lock` 重新解析锁文件。"
        "（`uv sync --frozen` 不会发现这个——它同步的是项目自身。）"
    )


def test_the_detector_reads_a_meaningful_number_of_names():
    """两份清单都必须非空。

    一个解析失败返回空集合的实现会让上一条永远绿——那种绿是假的，
    与「锁文件确实齐全」在断言上无法区分。
    """
    declared = _declared_dependencies()
    locked = _locked_packages()

    assert len(declared) >= 30, f"只解析出 {len(declared)} 个声明依赖，解析逻辑可能坏了"
    assert len(locked) >= 100, f"只解析出 {len(locked)} 个锁定包，解析逻辑可能坏了"


def test_the_bundled_skill_dependencies_are_locked():
    """随包技能的依赖单独钉一遍。

    它们与 `kirara_ai` 自身的依赖不同：**没有任何产品代码 import 它们**，
    因此少一个不会让任何测试或启动失败——只在用户使用某个技能时报 ImportError。
    正是这个性质让它们最容易在锁文件里被漏掉。
    """
    locked = _locked_packages()
    for package in (
        "lxml",
        "defusedxml",
        "openpyxl",
        "python-docx",
        "python-pptx",
        "pypdf",
        "pdfplumber",
        "pdf2image",
        "orjson",
        "pyyaml",
        "rich",
        "numpy",
    ):
        assert _normalize(package) in locked, (
            f"随包技能依赖 {package} 不在 uv.lock 里；"
            "缺了它那个技能装上之后一使用就 ImportError"
        )
