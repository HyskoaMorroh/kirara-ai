"""没有围栏的代码必须被识别成代码，而不是被当成正文改写（需求 6 / 19.1 / 19.3）。

现场报障里的 QQ 回复贴了一整段 Python，**一个反引号都没有**——模型直接把代码
写在正文里，这在实际对话里是常态而不是例外。当前实现把这段代码交给正文规则处理，
于是：

- `# ------------------- TSP 应用示例 -------------------` 被 ATX 标题规则吃掉，
  QQ 上变成 `■ ------------------- TSP 应用示例 -------------------`，
  企业微信上变成 `━━━ ... ━━━`——一行 Python 注释变成了一个标题；
- `_private_` 丢下划线、`*b*` 丢星号、`` `q` `` 变 `「q」`、`[a](b)` 变 `a（b）`、
  `mask = a | b | c` 被画成框线表格；
- 整段代码不进 `split_for_copyable_code` 的可复制路径，因此没有代码框、
  没有语言标识、没有复制指引，分页时也不被当成一个原子块。

19.3 要求「代码必须保持原始缩进和换行，使用明确的语言标识和代码边界」，
19.1 要求块结构由统一中间表示处理。两条在无围栏代码上都不成立。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kirara_ai.im.text_render import (
    TextBlockKind,
    fence_unfenced_code,
    parse_text_document,
    split_for_copyable_code,
    split_structured_text,
)
from kirara_ai.plugins.im_onebot_adapter.render import render_onebot_text
from kirara_ai.plugins.im_wecom_adapter.delegates import markdown_to_plain_text


#: 现场报障那段 Python 的最小可复现形态：顶格 import、顶格 class、
#: 顶格 `#` 注释、缩进正文。全都没有围栏。
REPORTED_CODE = """\
import numpy as np
from typing import Callable, Tuple


class SimulatedAnnealing:
    def __init__(self, cost_fn: Callable, T0: float = 1e3):
        self.cost_fn = cost_fn
        self.T0 = T0

    def optimize(self, init_state) -> Tuple[object, float]:
        best = init_state
        return best, self.cost_fn(best)


# ------------------- TSP 应用示例 -------------------
def solve_tsp(n_cities: int = 30):
    coords = rng.random((n_cities, 2)) * 100
    return coords
"""


def test_the_parser_reports_unfenced_python_as_one_code_block():
    blocks = parse_text_document(REPORTED_CODE).blocks
    kinds = [block.kind for block in blocks]
    assert TextBlockKind.CODE in kinds, f"无围栏代码没有被识别成代码块：{kinds}"
    assert TextBlockKind.HEADING not in kinds, (
        "代码里的 `# 注释` 被当成了 ATX 标题"
    )


def test_the_detected_code_block_carries_an_explicit_language():
    code_blocks = [
        block
        for block in parse_text_document(REPORTED_CODE).blocks
        if block.kind is TextBlockKind.CODE
    ]
    assert code_blocks, "没有识别出代码块"
    assert code_blocks[0].language == "python", (
        f"19.3 要求明确的语言标识，实际是 {code_blocks[0].language!r}"
    )


@pytest.mark.parametrize(
    "renderer, fence",
    [
        pytest.param(render_onebot_text, "```", id="onebot"),
        pytest.param(markdown_to_plain_text, "［代码", id="wecom"),
    ],
)
def test_the_comment_line_is_not_rendered_as_a_heading(renderer, fence):
    rendered = renderer(REPORTED_CODE)
    assert "# ------------------- TSP 应用示例 -------------------" in rendered, (
        f"注释行被改写了：\n{rendered}"
    )
    assert "■ -------" not in rendered
    assert "━━━ -------" not in rendered
    assert fence in rendered, f"无围栏代码没有被放进代码边界：\n{rendered}"


@pytest.mark.parametrize(
    "renderer",
    [
        pytest.param(render_onebot_text, id="onebot"),
        pytest.param(markdown_to_plain_text, id="wecom"),
    ],
)
def test_code_punctuation_survives_the_inline_rules(renderer):
    source = """\
def load(path):
    pattern = re.compile(r"_id_")
    doc = "[readme](https://example.invalid/z)"
    mask = a | b | c
    scale = 2 *factor* 3
    query = `SELECT 1`
    return pattern, doc
"""
    rendered = renderer(source)
    for fragment in (
        'r"_id_"',
        '"[readme](https://example.invalid/z)"',
        "mask = a | b | c",
        "2 *factor* 3",
        "`SELECT 1`",
    ):
        assert fragment in rendered, f"{fragment!r} 被正文规则改写了：\n{rendered}"


def test_unfenced_code_reaches_the_copyable_code_path():
    rendered = render_onebot_text(REPORTED_CODE)
    parts = split_for_copyable_code(rendered)
    assert any(part.is_code for part in parts), (
        "无围栏代码没有进入可复制代码消息路径，因此 QQ 上没有代码框也没有复制指引"
    )
    code_part = next(part for part in parts if part.is_code)
    assert "import numpy as np" in (code_part.code or "")
    assert code_part.language == "python"


def test_unfenced_code_is_paginated_as_one_atomic_block():
    body = "前言：\n\n" + "\n".join(
        f"value_{index} = compute(index={index})" for index in range(200)
    )
    pages = split_structured_text(render_onebot_text(body), max_bytes=1200)
    # 每一页要么完全不含围栏，要么围栏成对：分页不能把代码边界切坏。
    for page in pages:
        assert page.count("```") % 2 == 0, f"分页把代码围栏切坏了：\n{page[:200]}"


def test_prose_is_not_mistaken_for_code():
    """反向保护：中文技术散文不得被判成代码。

    这条比正向识别更要紧——把正文当代码会给用户一段带围栏的说明文字，
    而且它会被塞进「长按可整段复制」的路径。
    """
    prose = """\
模拟回火本质上是对 Metropolis-Hastings 采样在温度参数上施加冷却调度的全局优化策略。
其核心在于通过 Boltzmann 接受准则允许在高温阶段以一定概率接受劣解，从而跳出局部极小。
工程实践中，初始温度应标定至使初始接受率约 0.8–0.9，而非拍脑袋设定。
若需进一步提升性能，可考虑自适应降温、重加热以逃逸深度局部阱，或并行回火。
"""
    kinds = [block.kind for block in parse_text_document(prose).blocks]
    assert TextBlockKind.CODE not in kinds, f"中文散文被判成代码：{kinds}"


def test_a_single_keyword_line_is_not_code():
    prose = "return 的值必须是一个 dict。\n\n否则工作流会报错。"
    kinds = [block.kind for block in parse_text_document(prose).blocks]
    assert TextBlockKind.CODE not in kinds, f"单行关键字被判成代码：{kinds}"


def test_the_reported_reply_keeps_every_code_line_verbatim():
    """用户附件里那段回复的代码部分，逐行必须原样存活。"""
    attachment = Path.home() / "OneDrive" / "Desktop" / "1.txt"
    if not attachment.exists():
        pytest.skip("现场附件不在本机上；上面的最小复现已覆盖同一形态")
    lines = attachment.read_text(encoding="utf-8", errors="replace").split("\n")
    # L973..L1071（1-indexed）是被贴进 QQ 的那段 Python。
    reported = "\n".join(lines[972:1071])
    for renderer in (render_onebot_text, markdown_to_plain_text):
        rendered = renderer(reported)
        original = {line for line in reported.split("\n") if line.strip()}
        corrupted = [
            line
            for line in rendered.split("\n")
            if line.strip() and line not in original and "```" not in line
            and not line.startswith("［代码")
            and line != "［/代码］"
        ]
        assert not corrupted, f"{renderer.__name__} 改写了代码行：{corrupted}"


def test_telegram_keeps_the_indentation_of_unfenced_code():
    """Telegram 的缺口比 QQ 更重：markdownify 会把行首空格整个吃掉。

    Telegram 不走块渲染，它把源文本交给 MarkdownV2 转换器。一段没有围栏的
    缩进代码因此被压成全部顶格的一堆行——Python 的块结构就是缩进。
    """
    from kirara_ai.plugins.im_telegram_adapter.adapter import TelegramAdapter

    rendered = TelegramAdapter.render_text(REPORTED_CODE)
    assert "    def __init__" in rendered, f"缩进被吃掉了：\n{rendered}"
    assert "        self.cost_fn" in rendered
    # MarkdownV2 转义不得进到代码内容里：复制走 `\(` 粘进编辑器就是坏代码。
    assert "\\(" not in rendered, f"代码内容被 MarkdownV2 转义了：\n{rendered}"
    assert "```" in rendered


def test_fencing_is_idempotent_on_already_fenced_code():
    """已经带围栏的代码不得再被套一层围栏。

    套第二层时内层的三反引号会把外层提前闭合，后面的正文被当成代码。
    """
    source = "先看：\n\n```python\nvalue = _a_\n    nested = 2\n```\n\n完。"
    assert fence_unfenced_code(source) == source


def test_fencing_leaves_tilde_fences_alone():
    source = "先看：\n\n~~~python\nvalue = _a_\n~~~\n\n完。"
    assert fence_unfenced_code(source) == source


def test_a_table_inside_unfenced_code_is_not_rendered_as_a_table():
    """`mask = a | b | c` 不能被画成框线表格。"""
    source = "def build():\n    mask = alpha | beta | gamma\n    return mask\n"
    rendered = render_onebot_text(source)
    assert "mask = alpha | beta | gamma" in rendered
    assert "┌" not in rendered, f"代码里的竖线被当成表格：\n{rendered}"


def test_a_markdown_table_in_prose_still_renders_as_a_table():
    """反向保护：正文里的真表格不能因为这次改动失去渲染。"""
    source = "对比如下：\n\n| 项 | 值 |\n| --- | --- |\n| a | 1 |\n"
    rendered = render_onebot_text(source)
    assert "┌" in rendered, f"正文表格没有被渲染：\n{rendered}"


#: 一批「形状像代码但其实是正文」的样本。误判成代码的代价比漏判高：
#: 用户会收到一段带围栏的说明文字，还会被告知「长按可整段复制」。
PROSE_SAMPLES = {
    "chinese-technical-prose": (
        "初始温度 T_0 应标定至使初始接受率约 0.8-0.9。\n"
        "通常通过预采样一批数据并反解得到，而非拍脑袋设定。"
    ),
    "english-prose": (
        "The cooling schedule is the core tradeoff.\n"
        "Geman and Geman proved convergence in 1984.\n"
        "In practice this is rarely usable."
    ),
    "english-equation-sentences": (
        "Cost = benefit minus risk.\n"
        "Profit = revenue minus cost.\n"
        "That is the whole idea."
    ),
    "degraded-math": "T(k) >= (c)/(ln(1+k))\nwhere c is a constant.",
    "single-assignment": "answer = 42",
    "url-list": "https://a.example/x\nhttps://b.example/y",
    "faq": "Q: 为什么慢？\nA: 因为上游限流。",
    "adapter-log": (
        "[I] qq-protocol QQ 登录成功\n[E] qq-protocol PMHQ WebSocket 连接错误"
    ),
}


@pytest.mark.parametrize("sample", PROSE_SAMPLES.values(), ids=PROSE_SAMPLES.keys())
def test_prose_shaped_like_code_is_not_fenced(sample: str):
    kinds = [block.kind for block in parse_text_document(sample).blocks]
    assert TextBlockKind.CODE not in kinds, f"正文被判成代码：{kinds}\n{sample}"
    assert fence_unfenced_code(sample) == sample


#: 一批**必须**被识别成代码的样本。配置片段与命令行也算：把它们原样保住
#: （不吃缩进、不改标点）正是 19.3 的要求，而正文规则做不到。
CODE_SAMPLES = {
    "python-calls": 'total = sum(values)\nlabel = format(total, ".2f")',
    "config-block": "host = 0.0.0.0\nport = 8080\ntimeout = 30",
    "shell-session": "$ docker compose up -d\n$ docker compose logs --tail=180",
    "sql": "SELECT id, name\nFROM users\nWHERE id = 1;",
    "javascript": "const handler = (event) => {\n  console.log(event.type);\n};",
}


@pytest.mark.parametrize("sample", CODE_SAMPLES.values(), ids=CODE_SAMPLES.keys())
def test_code_shaped_content_is_detected(sample: str):
    kinds = [block.kind for block in parse_text_document(sample).blocks]
    assert TextBlockKind.CODE in kinds, f"代码没有被识别：{kinds}\n{sample}"


def test_the_reported_reply_isolates_its_code_into_one_copyable_message():
    """19.3 的复制路径：那段无围栏 Python 必须成为**一条**可复制的代码消息。"""
    attachment = Path.home() / "OneDrive" / "Desktop" / "1.txt"
    if not attachment.exists():
        pytest.skip("现场附件不在本机上")
    lines = attachment.read_text(encoding="utf-8", errors="replace").split("\n")
    reported = "\n".join(lines[970:1111])
    parts = split_for_copyable_code(render_onebot_text(reported))
    code_parts = [part for part in parts if part.is_code]
    assert len(code_parts) == 1, f"代码没有收成一条消息：{len(code_parts)} 条"
    assert code_parts[0].language == "python"
    assert "class SimulatedAnnealing:" in (code_parts[0].code or "")
    # 代码内容里的注释行必须原样，不能变成标题。
    assert (
        "# ------------------- TSP 应用示例 -------------------"
        in (code_parts[0].code or "")
    )


