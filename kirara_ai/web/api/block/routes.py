import asyncio
import json
from typing import Any

from quart import Blueprint, g, jsonify, websocket

from kirara_ai.logger import get_logger
from kirara_ai.workflow.core.block import BlockRegistry

from ...auth.middleware import require_auth
from .models import BlockType, BlockTypeList, BlockTypeResponse
from .python_lsp import PythonLanguageServer, QuartWsTransport

block_bp = Blueprint("block", __name__)

logger = get_logger("Web.Block")

@block_bp.route("/types", methods=["GET"])
@require_auth
async def list_block_types() -> Any:
    """获取所有可用的Block类型"""
    registry: BlockRegistry = g.container.resolve(BlockRegistry)

    types = []
    for block_type in registry.get_all_types():
        try:
            inputs, outputs, configs = registry.extract_block_info(block_type)
            type_name = registry.get_block_type_name(block_type)

            for config in configs.values():
                if config.has_options:
                    config.options = config.options_provider(g.container, block_type) # type: ignore

            block_type_info = BlockType(
                type_name=type_name,
                    name=block_type.name,
                    label=registry.get_localized_name(type_name) or block_type.name,
                    description=getattr(block_type, "description", ""),
                    inputs=list(inputs.values()),
                    outputs=list(outputs.values()),
                    configs=list(configs.values()),
            )
            types.append(block_type_info)
        except Exception as e:
            logger.error(f"获取Block类型失败: {e}")

    return BlockTypeList(types=types).model_dump()


@block_bp.route("/types/<type_name>", methods=["GET"])
@require_auth
async def get_block_type(type_name: str) -> Any:
    """获取特定Block类型的详细信息"""
    registry: BlockRegistry = g.container.resolve(BlockRegistry)

    block_type = registry.get(type_name)
    if not block_type:
        return jsonify({"error": "Block type not found"}), 404

    # 获取Block类的输入输出定义
    inputs, outputs, configs = registry.extract_block_info(block_type)

    for config in configs.values():
        if config.has_options:
            config.options = config.options_provider(g.container, block_type) # type: ignore

    block_type_info = BlockType(
        type_name=type_name,
        name=block_type.name,
        label=registry.get_localized_name(type_name) or block_type.name,
        description=getattr(block_type, "description", ""),
        inputs=list(inputs.values()),
        outputs=list(outputs.values()),
        configs=list(configs.values()),
    )

    return BlockTypeResponse(type=block_type_info).model_dump()


@block_bp.route("/types/compatibility", methods=["GET"])
@require_auth
async def get_type_compatibility() -> Any:
    """获取所有类型的兼容性映射"""
    registry: BlockRegistry = g.container.resolve(BlockRegistry)
    return jsonify(registry.get_type_compatibility_map())

@block_bp.websocket("/code/lsp")
async def code_lsp():
    """处理代码编辑器的语言服务器协议 WebSocket 连接"""
    lsp_server = PythonLanguageServer(loop=asyncio.get_event_loop())
    logger = get_logger("Web.Block.LSP")

    queue = asyncio.Queue()
    transport = QuartWsTransport(queue)

    lsp_server.lsp.connection_made(transport)
    lsp_server.lsp._send_only_body = True

    logger.info("LSP WebSocket connection established")
    
    async def sender():
        while True:
            message = await queue.get()
            if message is None:
                break
            await websocket.send(message)
            
    async def receiver():
        while True:
            message_str = await websocket.receive()            
            try:
                parsed_message = json.loads(
                    message_str,
                    object_hook=lsp_server.lsp._deserialize_message
                )
                lsp_server.lsp._procedure_handler(parsed_message)

            except json.JSONDecodeError:
                logger.error(f"Unable to parse received LSP message: {message_str}", exc_info=True)
            except Exception as e:
                logger.error(f"Error processing LSP message: {e}", exc_info=True)

    receive_task = asyncio.create_task(receiver())
    send_task = asyncio.create_task(sender())
    logger.debug("Created LSP WebSocket sender and receiver tasks")

    try:
        await asyncio.gather(receive_task, send_task)
    except asyncio.CancelledError:
        logger.info("LSP WebSocket task cancelled")
    except Exception as e:
        logger.error(f"LSP WebSocket connection error: {e}", exc_info=True)
    finally:
        send_task.cancel()
        try:
            await send_task
        except asyncio.CancelledError:
            logger.debug("LSP WebSocket sender task cancelled")

        receive_task.cancel()
        try:
            await receive_task
        except asyncio.CancelledError:
            logger.debug("LSP WebSocket receiver task cancelled")
        loop = asyncio.get_event_loop()
        loop.run_in_executor(None, lsp_server.shutdown)
        logger.info("websocket connection closed")
