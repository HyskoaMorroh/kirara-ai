import random
from typing import Annotated, Dict, Optional

from kirara_ai.im.message import IMMessage, TextMessage
from kirara_ai.im.sender import ChatSender
from kirara_ai.workflow.core.block import Block, ParamMeta
from kirara_ai.workflow.core.block.input_output import Input, Output


class GachaSimulator(Block):
    """抽卡模拟器 block"""

    name = "gacha_simulator"
    description = "模拟抽卡，消息中含「十连」时抽 10 次，否则抽 1 次，并给出稀有度统计。"
    inputs = {
        "message": Input("message", "输入消息", IMMessage, "包含抽卡命令的消息")
    }
    outputs = {
        "response": Output("response", "响应消息", IMMessage, "包含抽卡结果与统计的消息")
    }

    def __init__(
        self,
        rates: Annotated[
            Optional[Dict[str, float]],
            ParamMeta(label="稀有度概率", description="稀有度到概率的映射，留空则使用默认的 SSR 3% / SR 12% / R 85%"),
        ] = None,
    ):
        # 默认抽卡概率
        self.rates = rates or {"SSR": 0.03, "SR": 0.12, "R": 0.85}  # 3%  # 12%  # 85%

    def _single_pull(self) -> str:
        rand = random.random()
        cumulative = 0
        for rarity, rate in self.rates.items():
            cumulative += rate
            if rand <= cumulative:
                return rarity
        return list(self.rates.keys())[-1]  # 保底

    def execute(self, message: IMMessage) -> Dict[str, IMMessage]:
        # 解析命令
        command = message.content
        is_ten_pull = "十连" in command
        pulls = 10 if is_ten_pull else 1

        # 抽卡
        results = [self._single_pull() for _ in range(pulls)]

        # 生成结果统计
        stats = {rarity: results.count(rarity) for rarity in self.rates.keys()}

        # 生成详细信息
        details = []
        for rarity in results:
            if rarity == "SSR":
                details.append("🌟 SSR")
            elif rarity == "SR":
                details.append("✨ SR")
            else:
                details.append("⭐ R")

        result_text = f"抽卡结果: {'、'.join(details)}"
        stats_text = "统计:\n" + "\n".join(
            f"{rarity}: {count}" for rarity, count in stats.items()
        )

        return {
            "response": IMMessage(
                sender=ChatSender.get_bot_sender(),
                message_elements=[TextMessage(result_text), TextMessage(stats_text)],
            )
        }
