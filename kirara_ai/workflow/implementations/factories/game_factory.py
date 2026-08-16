from kirara_ai.workflow.core.workflow.builder import WorkflowBuilder
from kirara_ai.workflow.implementations.blocks.game.dice import DiceRoll
from kirara_ai.workflow.implementations.blocks.game.gacha import GachaSimulator
from kirara_ai.workflow.implementations.blocks.im.messages import GetIMMessage, SendIMMessage


class GameWorkflowFactory:
    """游戏相关工作流工厂"""

    @staticmethod
    def create_dice_workflow() -> WorkflowBuilder:
        """创建骰子游戏工作流"""
        builder = (
            WorkflowBuilder("骰子游戏")
            .use(GetIMMessage)
            .chain(DiceRoll)
            .chain(SendIMMessage)
        )
        builder.description = "识别 .roll 1d100 这类指令并掷骰，把结果回复到聊天中。"
        return builder

    @staticmethod
    def create_gacha_workflow() -> WorkflowBuilder:
        """创建抽卡游戏工作流"""
        builder = (
            WorkflowBuilder("抽卡游戏")
            .use(GetIMMessage)
            .chain(GachaSimulator)
            .chain(SendIMMessage)
        )
        builder.description = "模拟抽卡，说「抽卡」抽一次、说「十连」抽十次，并给出稀有度统计。"
        return builder
