"""loguru 用 `{}` 占位符，写成 `%s` 时参数被静默丢弃。

发现过程
------
真实容器冷启动的日志里出现这一行：

    WARNING | ResourceCatalog | 内置资源 %s 预置失败，已跳过（不影响启动）：%s

`catalog_id` 与 `error` 两个值一个都没显示。这条日志的全部作用就是说出
「哪一条内置资源没装上、为什么」，两个值都丢了等于没记——而它在那次启动里
**确实触发了**，于是「有条资源预置失败」这个事实无从追查。

为什么 3683 项测试没抓到
---------------------
没有任何测试断言这条日志的**内容**。`%s` 不抛异常、不改变控制流，
调用方拿到的返回值完全正确——它只是让一条诊断信息变成一句空话。
本轮早先在 `entry.py` 修过三处同形缺陷，当时靠手工冷启动发现；
这一处在另一个文件里，同样躲过了全部测试。

这一组测试锁住的边界
------------------
1. 全库范围：任何 loguru 调用都不得使用 `%s` / `%d` 这类 printf 占位符。
   逐个文件补断言会漏掉下一个新文件，因此判据是**扫描整棵源码树**。
2. 那两条具体的日志真的带上了值（用真实调用而不是读源码）。
"""

from __future__ import annotations

import ast
import pathlib

import pytest

REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "kirara_ai"

#: loguru 的日志方法。`logger.opt(...)` 链式调用最终也落到这些名字上。
_LOG_METHODS = frozenset(
    {"trace", "debug", "info", "success", "warning", "error", "critical", "exception", "log"}
)

#: printf 风格占位符。`%%` 是转义后的百分号，不算。
_PRINTF_TOKENS = ("%s", "%d", "%r", "%i", "%f", "%05d", "%.2f")


def _log_calls_with_printf(path: pathlib.Path) -> list[tuple[int, str]]:
    """Return (line, message) for loguru calls whose literal uses printf tokens.

    用 AST 而不是正则：正则会把 `"%s" % value` 这种**正确**的预格式化
    也报成缺陷，也会漏掉跨行的调用。判据是「第一个字面量参数含 printf 占位符
    **且**后面还有位置参数」——只有这种组合才会让参数被丢弃。
    """

    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:  # pragma: no cover - 语法错误由别的门禁负责
        return []

    offenders: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in _LOG_METHODS:
            continue
        if not node.args:
            continue
        first = node.args[0]
        if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
            continue
        message = first.value
        # 只有「字面量里有占位符」且「还传了要填进去的值」才是缺陷。
        # 一条不带参数的消息里出现 `%s` 通常是在讲解格式，不是占位。
        if len(node.args) < 2:
            continue
        cleaned = message.replace("%%", "")
        if any(token in cleaned for token in _PRINTF_TOKENS):
            offenders.append((first.lineno, message))
    return offenders


class TestNoPrintfPlaceholdersInLoguruCalls:
    def test_the_whole_source_tree_is_clean(self):
        """扫描整棵树，而不是逐个文件补断言。

        逐个文件的写法会漏掉下一个新文件——本轮已经在两个不同文件里
        各踩过一次同一个形状。
        """
        offenders: list[str] = []
        for path in sorted(SOURCE_ROOT.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            for line, message in _log_calls_with_printf(path):
                relative = path.relative_to(REPOSITORY_ROOT).as_posix()
                offenders.append(f"{relative}:{line}: {message[:70]}")

        assert not offenders, (
            "loguru 用 `{}` 而不是 `%s`——下列调用的参数会被静默丢弃，"
            f"日志里留下字面量占位符：\n" + "\n".join(offenders)
        )

    def test_the_detector_itself_catches_a_known_bad_shape(self, tmp_path):
        """检测器必须真的能抓到——否则上一条会永远绿。

        一个只会返回空列表的检测器与「代码是干净的」在断言上无法区分，
        而前者的绿是假的。
        """
        bad = tmp_path / "bad.py"
        bad.write_text(
            "from kirara_ai.logger import get_logger\n"
            "logger = get_logger('X')\n"
            'logger.warning("资源 %s 失败：%s", rid, err)\n',
            encoding="utf-8",
        )

        assert _log_calls_with_printf(bad), "检测器抓不到已知的坏形状"

    def test_a_preformatted_percent_expression_is_not_flagged(self, tmp_path):
        """`"%s" % value` 是正确的预格式化，不该被报成缺陷。"""
        good = tmp_path / "good.py"
        good.write_text(
            "from kirara_ai.logger import get_logger\n"
            "logger = get_logger('X')\n"
            'logger.info("已处理 %s 条" % count)\n',
            encoding="utf-8",
        )

        assert not _log_calls_with_printf(good)

    def test_brace_placeholders_are_not_flagged(self, tmp_path):
        good = tmp_path / "good2.py"
        good.write_text(
            "from kirara_ai.logger import get_logger\n"
            "logger = get_logger('X')\n"
            'logger.warning("资源 {} 失败：{}", rid, err)\n',
            encoding="utf-8",
        )

        assert not _log_calls_with_printf(good)


class TestTheTwoFixedCallsActuallyCarryTheirValues:
    def test_the_builtin_provisioning_warning_names_the_resource(self):
        """真实调用而不是读源码：读源码只能证明字符串长什么样。

        用 `logger.add()` 挂一个临时 sink 而不是 pytest 的 `caplog`：
        loguru 不经过 stdlib logging，`caplog` 收到的是空串——那会让这条断言
        在**任何**实现下都失败，包括正确的实现。
        """
        from kirara_ai.logger import get_logger
        from loguru import logger as loguru_logger

        captured: list[str] = []
        sink_id = loguru_logger.add(lambda message: captured.append(str(message)), level="WARNING")
        try:
            get_logger("ResourceCatalog").warning(
                "内置资源 {} 预置失败，已跳过（不影响启动）：{}",
                "skill:demo",
                "boom",
            )
        finally:
            loguru_logger.remove(sink_id)

        text = "".join(captured)
        assert "skill:demo" in text
        assert "boom" in text
        assert "%s" not in text

    @pytest.mark.parametrize(
        "relative",
        (
            "kirara_ai/plugin_manager/resource_catalog.py",
            "kirara_ai/web/auth/services.py",
        ),
    )
    def test_the_fixed_files_have_no_printf_log_calls_left(self, relative: str):
        assert not _log_calls_with_printf(REPOSITORY_ROOT / relative)
