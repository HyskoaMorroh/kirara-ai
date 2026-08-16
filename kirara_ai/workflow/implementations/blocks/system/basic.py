import re
from datetime import datetime
from typing import Annotated, Any, Dict, List

from kirara_ai.im.text_render import convert_markdown_tables
from kirara_ai.logger import get_logger
from kirara_ai.workflow.core.block import Block, Output, ParamMeta
from kirara_ai.workflow.core.block.input_output import Input


class TextBlock(Block):
    name = "text_block"
    description = "输出一段固定文本，常用于填写系统提示词、模板等固定内容。"
    outputs = {"text": Output("text", "文本", str, "文本")}

    def __init__(
        self, text: Annotated[str, ParamMeta(label="文本", description="要输出的文本")]
    ):
        self.text = text

    def execute(self) -> Dict[str, Any]:
        return {"text": self.text}


# 拼接文本
class TextConcatBlock(Block):
    name = "text_concat_block"
    description = "把两段文本按先后顺序直接拼接成一段。"
    inputs = {
        "text1": Input("text1", "文本1", str, "拼接后位于前面的文本"),
        "text2": Input("text2", "文本2", str, "拼接后位于后面的文本"),
    }
    outputs = {"text": Output("text", "拼接后的文本", str, "拼接后的完整文本")}

    def execute(self, text1: str, text2: str) -> Dict[str, Any]:
        return {"text": text1 + text2}


# 替换输入文本中的某一块文字为变量
class TextReplaceBlock(Block):
    name = "text_replace_block"
    description = "把原始文本中指定的占位文字全部替换为输入的新内容。"
    inputs = {
        "text": Input("text", "原始文本", str, "待处理的原始文本"),
        "new_text": Input("new_text", "新文本", Any, "用于替换占位文字的新内容"),  # type: ignore
    }
    outputs = {"text": Output("text", "替换后的文本", str, "完成替换后的文本")}

    def __init__(
        self, variable: Annotated[str, ParamMeta(label="被替换的文本", description="被替换的文本")]
    ):
        self.variable = variable

    def execute(self, text: str, new_text: Any) -> Dict[str, Any]:
        return {
            "text": text.replace(self.variable, str(new_text))
        }


# 正则表达式提取
class TextExtractByRegexBlock(Block):
    name = "text_extract_by_regex_block"
    description = "用正则表达式从文本中提取第一个捕获组的内容，未匹配时输出空字符串。"
    inputs = {"text": Input("text", "原始文本", str, "待提取的原始文本")}
    outputs = {"text": Output("text", "提取后的文本", str, "第一个捕获组匹配到的内容")}
    def __init__(
        self, regex: Annotated[str, ParamMeta(label="正则表达式", description="正则表达式")]
    ):
        self.regex = regex

    def execute(self, text: str) -> Dict[str, Any]:
        # 使用正则表达式提取文本
        regex = re.compile(self.regex)
        match = regex.search(text)
        # 如果匹配到，则返回匹配到的文本，否则返回空字符串
        if match and len(match.groups()) > 0:
            return {"text": match.group(1)}
        else:
            return {"text": ""}


# 获取当前时间
class CurrentTimeBlock(Block):
    name = "current_time_block"
    description = "输出服务器当前时间，格式为 YYYY-MM-DD HH:MM:SS。"
    outputs = {"time": Output("time", "当前时间", str, "当前时间")}

    def execute(self) -> Dict[str, Any]:
        return {"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}


# 清除 Markdown 标记，输出适合 IM 平台阅读的纯文本
class TextStripMarkdownBlock(Block):
    name = "text_strip_markdown_block"
    description = "去除 Markdown 标记，把模型回复整理成适合聊天平台阅读的纯文本。"
    inputs = {"text": Input("text", "原始文本", str, "含 Markdown 标记的文本")}
    outputs = {"text": Output("text", "纯文本", str, "去除 Markdown 标记后的文本")}

    def __init__(
        self,
        keep_heading_text: Annotated[
            bool, ParamMeta(label="保留标题文字", description="去掉 # 号但保留标题内容")
        ] = True,
        bullet_char: Annotated[
            str, ParamMeta(label="列表符号", description="无序列表统一替换成的符号，留空则删除")
        ] = "·",
        table_style: Annotated[
            str,
            ParamMeta(
                label="表格样式",
                description="box：渲染为等宽框线表格（推荐）；plain：转为空格分隔的纯文本",
                options_provider=lambda container, block: ["box", "plain"],
            ),
        ] = "box",
    ):
        self.keep_heading_text = keep_heading_text
        self.bullet_char = bullet_char
        self.table_style = table_style

    def execute(self, text: str) -> Dict[str, Any]:
        if not text:
            return {"text": text}

        result = text

        # 表格：默认先渲染成等宽框线表格，避免后续规则把表格打散成纯文字
        if self.table_style == "box":
            result = convert_markdown_tables(result)

        # 代码块：去掉围栏，保留内部代码
        result = re.sub(r"```[a-zA-Z0-9_+-]*\n?", "", result)
        result = result.replace("~~~", "")

        # 行内代码：去掉反引号
        result = re.sub(r"`([^`\n]+)`", r"\1", result)

        # 标题：# ## ### 等
        if self.keep_heading_text:
            result = re.sub(r"^\s{0,3}#{1,6}\s*", "", result, flags=re.MULTILINE)
        else:
            result = re.sub(r"^\s{0,3}#{1,6}\s*.*$", "", result, flags=re.MULTILINE)

        # 加粗与斜体：**text** __text__ *text* _text_
        result = re.sub(r"\*\*\*(.+?)\*\*\*", r"\1", result, flags=re.DOTALL)
        result = re.sub(r"\*\*(.+?)\*\*", r"\1", result, flags=re.DOTALL)
        result = re.sub(r"___(.+?)___", r"\1", result, flags=re.DOTALL)
        result = re.sub(r"__(.+?)__", r"\1", result, flags=re.DOTALL)
        result = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", result, flags=re.DOTALL)
        result = re.sub(r"(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?![\w_])", r"\1", result, flags=re.DOTALL)

        # 删除线：~~text~~
        result = re.sub(r"~~(.+?)~~", r"\1", result, flags=re.DOTALL)

        # 链接与图片：[text](url) ![alt](url)
        result = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", result)
        result = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", result)

        # 引用块：> text
        result = re.sub(r"^\s{0,3}>\s?", "", result, flags=re.MULTILINE)

        # 无序列表：- * + 开头
        if self.bullet_char:
            result = re.sub(r"^(\s*)[-*+]\s+", r"\g<1>" + self.bullet_char + " ", result, flags=re.MULTILINE)
        else:
            result = re.sub(r"^(\s*)[-*+]\s+", r"\g<1>", result, flags=re.MULTILINE)

        # 分割线：--- *** ___
        result = re.sub(r"^\s{0,3}([-*_])\s*(\1\s*){2,}$", "", result, flags=re.MULTILINE)

        if self.table_style != "box":
            # 表格分隔行：|---|---|
            result = re.sub(r"^\s*\|?[\s:|-]{3,}\|?\s*$", "", result, flags=re.MULTILINE)

            # 表格竖线：转为空格分隔
            result = re.sub(r"^\s*\|(.+)\|\s*$", lambda m: m.group(1).replace("|", "  ").strip(), result, flags=re.MULTILINE)

        # 压缩多余空行：连续 3 个以上换行压成 2 个
        result = re.sub(r"\n{3,}", "\n\n", result)

        # 去掉每行尾部空白
        result = "\n".join(line.rstrip() for line in result.split("\n"))

        return {"text": result.strip()}


class CodeBlock(Block):
    name = "code_block"
    description = "运行自定义 Python 代码，入口为 execute(...) 函数，输入输出端口可自行增删。"
    inputs = {}
    outputs = {}

    def __init__(self,
                 inputs: Annotated[List[Dict[str, str]], ParamMeta(label="输入参数", description="输入参数")],
                 outputs: Annotated[List[Dict[str, str]], ParamMeta(label="输出参数", description="输出参数")],
                 code: Annotated[str, ParamMeta(label="代码", description="代码")]):
        # 初始化实例的 inputs 和 outputs
        self.inputs = {}
        self.outputs = {}
        for input_spec in inputs:
            self.inputs[input_spec["name"]] = Input(input_spec["name"], input_spec["label"], Any, 'user-specified object') # type: ignore
        for output_spec in outputs:
            self.outputs[output_spec["name"]] = Output(output_spec["name"], output_spec["label"], Any, 'user-specified object') # type: ignore
        self.code = code

    def execute(self, **kwargs: Any) -> Dict[str, Any]: # 使用 Any 兼容各种输入类型
        logger = get_logger("Block.Code")

        exec_globals = globals().copy()
        exec_locals: Dict[str, Any] = {}

        logger.debug(f"Executing code definition:\n{self.code}")
        try:
            exec(self.code, exec_globals, exec_locals)
        except Exception as e:
            logger.error(f"Error during code definition execution: {e}", exc_info=True)
            raise RuntimeError(f"Error in provided code definition: {e}") from e

        if 'execute' not in exec_locals or not callable(exec_locals['execute']):
            raise ValueError("Provided code must define a callable function named 'execute'")

        exec_locals['__input_kwargs__'] = kwargs
        exec_globals.update(exec_locals)
        call_code = "__result__ = execute(**__input_kwargs__)"

        logger.debug(f"Executing function call: execute(**{list(kwargs.keys())})")
        try:
            exec(call_code, exec_globals, exec_locals)
        except Exception as e:
            logger.error(f"Error during user function 'execute' execution: {e}", exc_info=True)
            raise RuntimeError(f"Error during execution of user function 'execute': {e}") from e

        if '__result__' not in exec_locals:
             # 如果 exec(call_code) 成功但没有 __result__，说明有内部问题
             logger.error("Internal error: Result '__result__' not found after executing user code call.")
             raise RuntimeError("Failed to retrieve result from user code execution.")

        result = exec_locals['__result__']

        return result

