
# LLM 响应转纯文本
from typing import Any, Dict

from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.llm.format.response import LLMChatResponse
from kirara_ai.workflow.core.block.base import Block
from kirara_ai.workflow.core.block.input_output import Input, Output


class LLMResponseToText(Block):
    """LLM 响应转纯文本"""

    name = "llm_response_to_text"
    container: DependencyContainer
    inputs = {"response": Input("response", "LLM 响应", LLMChatResponse, "LLM 响应")}
    outputs = {"text": Output("text", "纯文本", str, "纯文本")}

    def execute(self, response: LLMChatResponse) -> Dict[str, Any]:
        content = ""
        if response.message:
            for part in response.message.content:
                if part.type == "text":
                    content = content + part.text
                elif part.type == "image":
                    content = content + f"<media_msg id={part.media_id} />"

        return {"text": content}

