"""操作文档里的命令必须能在本机跑起来（需求 23.4）。

23.4 明确点名两条：

> Windows 后端使用 `.venv-win/Scripts/python.exe`；WebUI 使用实际存在的
> `test:unit`，不得写成不存在的 `npm test`。

这两条不是排版偏好，是**文档可执行性**。一份写着 `npm test` 的文档，读者照抄会得到
`npm ERR! Missing script: "test"`——他此刻正在按文档验证一次升级或一次故障排查，
一条跑不起来的命令会让他怀疑是自己的环境坏了，而真正坏的是文档。

同理，`python -m pytest` 在这台机器上跑的是系统 Python 而不是项目虚拟环境
（后端依赖装在 `.venv-win` 里），得到的是一串 `ModuleNotFoundError`。

## 判据

**只检查操作文档**（README 与 `docs/*.md` 的第一层）。刻意排除：

- `docs/superpowers/`：那是历史交接与计划材料，其中大量句子是**在讨论**
  「不要用 `npm test`」这条约束本身，把它们当违规会让这条测试自相矛盾；
- `CHANGELOG.md`：历史条目按定义记录的是当时的写法。

23.1 对版本号也有同样的「历史可以保留、操作文档必须当前」的分野，这里沿用它。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: 操作文档：读者会照着敲命令的那些。
#:
#: 只取 `docs/` 的第一层——`docs/superpowers/` 下是计划与交接材料，
#: 它们讨论这条约束本身，不是给人照抄的操作步骤。
def _operator_docs() -> list[Path]:
    docs = sorted(path for path in (ROOT / "docs").glob("*.md"))
    readme = ROOT / "README.md"
    return ([readme] if readme.exists() else []) + docs


#: `npm test` 这个脚本在 `webui/package.json` 里不存在（只有 `test:unit`）。
#:
#: 边界：`npm test:unit` 不是违规（虽然正确写法是 `npm run test:unit`，
#: 但那属于另一条规则），因此断言后面必须**不是** `:`。
_NPM_TEST = re.compile(r"\bnpm\s+test(?![:\w-])")

#: 裸 `python` / `python3` 跑测试。后端依赖装在 `.venv-win` 里，
#: 用系统解释器会得到一串 ModuleNotFoundError。
#:
#: **只管验证类命令（`-m pytest`）**，不管启动应用（`-m kirara_ai`）：
#: 后者是部署命令，Linux/VPS 上 `python3 -m kirara_ai` 在激活了自己的 venv 之后
#: 完全正确，而 23.4 关心的是「读者照着文档验证时那条命令跑不跑得起来」。
#: 把启动命令也算进来会把一份正确的跨平台部署文档判成违规。
#:
#: 允许的形态：`.venv-win/Scripts/python.exe`、`docker compose exec ... python`
#: （容器内解释器）、`uv run`（uv 托管环境）。
_BARE_PYTEST = re.compile(r"(?<![\w./\\$-])python3?(?:\.exe)?\s+-m\s+pytest")


def _command_lines(text: str) -> list[tuple[int, str]]:
    """取出所有围栏代码块里的行——命令只在那里。

    正文里提到 `npm test` 通常是在**说明**它不存在，那不是让人照抄的命令。
    """
    lines = text.split("\n")
    inside = False
    result: list[tuple[int, str]] = []
    for index, line in enumerate(lines, start=1):
        if line.lstrip().startswith("```"):
            inside = not inside
            continue
        if inside:
            result.append((index, line))
    return result


@pytest.mark.parametrize(
    "doc", _operator_docs(), ids=lambda path: path.relative_to(ROOT).as_posix()
)
def test_no_nonexistent_npm_test_script(doc: Path):
    """`npm test` 会报 Missing script；正确写法是 `npm run test:unit`。"""
    offenders = [
        (number, line.strip())
        for number, line in _command_lines(doc.read_text(encoding="utf-8"))
        if _NPM_TEST.search(line)
    ]

    assert not offenders, f"{doc.name} 里有跑不起来的 npm test：{offenders}"


@pytest.mark.parametrize(
    "doc", _operator_docs(), ids=lambda path: path.relative_to(ROOT).as_posix()
)
def test_test_commands_use_the_project_interpreter(doc: Path):
    """裸 `python -m pytest` 用的是系统解释器，后端依赖不在那里。"""
    offenders = []
    for number, line in _command_lines(doc.read_text(encoding="utf-8")):
        if "docker" in line or "uv run" in line:
            # 容器内与 uv 托管的解释器各自自带环境。
            continue
        if _BARE_PYTEST.search(line):
            offenders.append((number, line.strip()))

    assert not offenders, (
        f"{doc.name} 里的测试命令没走 .venv-win/Scripts/python.exe：{offenders}"
    )


def test_the_webui_script_actually_exists():
    """文档推荐 `test:unit`，那它必须真的在 package.json 里。

    反向守护：脚本改名而文档没跟上时，上面两条都还是绿的。
    """
    import json

    scripts = json.loads(
        (ROOT / "webui" / "package.json").read_text(encoding="utf-8")
    )["scripts"]

    assert "test:unit" in scripts
    assert "test" not in scripts, (
        "package.json 新增了 test 脚本，上面那条禁用规则需要重新评估"
    )


def test_the_windows_interpreter_path_exists_in_the_repo_layout():
    """文档里那条路径是相对仓库根的；拼错了读者会得到「找不到文件」。"""
    docs_using_it = [
        doc
        for doc in _operator_docs()
        if ".venv-win" in doc.read_text(encoding="utf-8")
    ]

    assert docs_using_it, "没有任何操作文档给出 Windows 后端解释器路径"
    for doc in docs_using_it:
        content = doc.read_text(encoding="utf-8")
        # 只接受这两种分隔符写法，避免出现 `.venv_win` 或 `venv-win/bin` 这类笔误。
        assert re.search(r"\.venv-win[/\\]Scripts[/\\]python\.exe", content), (
            f"{doc.name} 里的解释器路径形态不对"
        )
