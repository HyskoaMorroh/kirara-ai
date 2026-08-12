import asyncio
import json

from quart import Blueprint, g, jsonify, request, websocket

from kirara_ai.internal import shutdown_event
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.logger import get_logger
from kirara_ai.tracing.llm_tracer import LLMTracer
from kirara_ai.tracing.manager import TracingManager
from kirara_ai.web.auth.middleware import require_auth
from kirara_ai.web.auth.services import AuthService

tracing_bp = Blueprint("tracing", __name__, url_prefix="/api/tracing")

logger = get_logger("Tracing-API")


@tracing_bp.route("/types", methods=["GET"])
@require_auth
async def get_trace_types():
    """获取所有可用的追踪器类型"""
    container: DependencyContainer = g.container
    tracing_manager = container.resolve(TracingManager)

    return jsonify({
        "types": tracing_manager.get_tracer_types()
    })


@tracing_bp.route("/llm/traces", methods=["POST"])
@require_auth
async def get_llm_traces():
    """获取LLM追踪记录，支持筛选和分页"""
    # 获取查询参数
    data = await request.json
    page = data.get("page", 1)
    page_size = data.get("page_size", 20)
    model_id = data.get("model_id")
    backend_name = data.get("backend_name")
    status = data.get("status")

    # 构建过滤条件
    filters = {}
    if model_id:
        filters["model_id"] = model_id
    if backend_name:
        filters["backend_name"] = backend_name
    if status:
        filters["status"] = status

    container: DependencyContainer = g.container
    tracing_manager = container.resolve(TracingManager)
    llm_tracer = tracing_manager.get_tracer("llm")

    if not llm_tracer:
        return jsonify({"error": "LLM tracer not found"}), 404

    # 使用统一的查询接口
    records, total = llm_tracer.get_traces(
        filters=filters,
        page=page,
        page_size=page_size
    )

    return jsonify({
        "items": [record.to_dict() for record in records],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size
    })


@tracing_bp.route("/llm/detail/<trace_id>", methods=["GET"])
@require_auth
async def get_llm_trace_detail(trace_id: str):
    """获取特定LLM请求的详细信息"""
    container: DependencyContainer = g.container
    tracing_manager = container.resolve(TracingManager)
    llm_tracer = tracing_manager.get_tracer("llm")

    if not llm_tracer:
        return jsonify({"error": "LLM tracer not found"}), 404

    trace = llm_tracer.get_trace_by_id(trace_id)
    if not trace:
        return jsonify({"error": "Trace not found"}), 404

    return jsonify(trace.to_detail_dict())


@tracing_bp.route("/llm/statistics", methods=["GET"])
@require_auth
async def get_llm_statistics():
    """获取LLM统计信息"""
    container: DependencyContainer = g.container
    tracing_manager = container.resolve(TracingManager)
    llm_tracer = tracing_manager.get_tracer("llm")

    if not llm_tracer:
        return jsonify({"error": "LLM tracer not found"}), 404
    assert isinstance(llm_tracer, LLMTracer)
    stats = llm_tracer.get_statistics()
    return jsonify(stats)


@tracing_bp.websocket("/ws")
async def tracing_ws():
    """WebSocket接口，用于实时推送追踪日志"""
    container: DependencyContainer = g.container
    tracing_manager = container.resolve(TracingManager)
    auth_service: AuthService = container.resolve(AuthService)

    # 获取所有追踪器类型
    tracer_types = tracing_manager.get_tracer_types()

    # 发送欢迎消息
    await websocket.send(json.dumps({
        "type": "connected",
        "message": "Connected to tracing websocket",
        "data": {
            "available_tracers": tracer_types
        }
    }))

    # 验证token
    try:
        token_data = await websocket.receive()
        token = json.loads(token_data)["token"]

        if not auth_service.verify_token(token):
            await websocket.close(code=1008, reason="Invalid token")
            return
    except Exception as e:
        logger.error(f"WebSocket连接错误: {e}")
        await websocket.close(code=1008, reason="Invalid token")
        return

    # 接收命令
    cmd = await websocket.receive()
    cmd = json.loads(cmd)

    # 订阅
    if cmd.get("action") == "subscribe":
        if tracer_type := cmd.get("tracer_type"):
            tracer = tracing_manager.get_tracer(tracer_type)
            if tracer:
                # 注册WebSocket客户端
                queue: asyncio.Queue = tracer.register_ws_client()
                await websocket.send(json.dumps({
                    "type": "subscribe_success",
                    "message": "Subscribed to tracing websocket",
                    "data": {
                        "tracer_type": tracer_type
                    }
                }))
            else:
                await websocket.close(code=1008, reason="Tracer not found")
                return
        else:
            await websocket.close(code=1008, reason="Invalid tracer type")
            return
    else:
        await websocket.close(code=1008, reason="Invalid action")
        return

    try:
        # 保持连接打开状态，直到客户端断开连接
        while not shutdown_event.is_set():
            # 摸鱼
            message = await queue.get()
            if message is None:
                break
            await websocket.send(json.dumps(message))
    finally:
        if tracer:
            tracer.unregister_ws_client(queue)
