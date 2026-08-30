import asyncio
import csv
import io
import json
from datetime import datetime
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from quart import Blueprint, Response, g, jsonify, request, websocket

from kirara_ai.config.global_config import GlobalConfig
from kirara_ai.im.delivery_timing_store import DeliveryTimingStore
from kirara_ai.internal import shutdown_event
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.logger import get_logger
from kirara_ai.tracing.llm_tracer import LLMTracer
from kirara_ai.tracing.manager import TracingManager
from kirara_ai.web.auth.middleware import require_auth
from kirara_ai.web.auth.services import AuthService

tracing_bp = Blueprint("tracing", __name__, url_prefix="/api/tracing")

logger = get_logger("Tracing-API")

_TRACE_SEARCH_FIELDS = (
    "trace_id",
    "correlation_id",
    "provider",
    "model_id",
    "backend_name",
    "status",
    "error_category",
    "usage_source",
)


def _timezone_or_error(name: Any, default: str):
    if name is not None and name != "" and not isinstance(name, str):
        return None, (jsonify({
            "error": "timezone must be a valid IANA timezone name"
        }), 400)
    timezone_name = name or default
    try:
        return ZoneInfo(timezone_name), None
    except (ZoneInfoNotFoundError, KeyError):
        return None, (jsonify({"error": f"Unknown timezone: {timezone_name}"}), 400)


def _parse_iso_datetime(value: Any, field: str, storage_timezone: ZoneInfo):
    if value is None:
        return None, None
    if not isinstance(value, str):
        return None, (jsonify({
            "error": f"{field} must be an ISO-8601 datetime with a timezone"
        }), 400)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
    if parsed is None or parsed.tzinfo is None or parsed.utcoffset() is None:
        return None, (jsonify({
            "error": f"{field} must be an ISO-8601 datetime with a timezone"
        }), 400)
    return parsed.astimezone(storage_timezone).replace(tzinfo=None), None


async def _trace_request_options(data: Mapping[str, Any], config: GlobalConfig):
    timezone, error = _timezone_or_error(data.get("timezone"), config.system.timezone)
    if error:
        return None, error
    storage_timezone, error = _timezone_or_error(config.system.timezone, config.system.timezone)
    if error:
        return None, error

    filters = {}
    aliases = (
        ("provider", "provider"),
        ("backend", "backend_name"),
        ("backend_name", "backend_name"),
        ("model", "model_id"),
        ("model_id", "model_id"),
        ("status", "status"),
        ("error_category", "error_category"),
        ("usage_source", "usage_source"),
        ("correlation_id", "correlation_id"),
    )
    for source, target in aliases:
        value = data.get(source)
        if value is not None and value != "":
            filters[target] = value

    # 「未标注」维度必须能被显式筛选。
    #
    # 空串在上面的循环里被当成「没填」丢掉，所以前端无法用 `provider=""`
    # 表达「只看没有 provider 的记录」——选了「未标注」却拿到全量数据，
    # 那比没有这个选项更糟：它给出一个错误的答案而不是拒绝回答。
    # 这里用一组独立的 `*_unset` 参数表达「该列为 NULL」。
    for source, target in (
        ("provider_unset", "provider"),
        ("backend_unset", "backend_name"),
        ("model_unset", "model_id"),
        ("error_category_unset", "error_category"),
        ("usage_source_unset", "usage_source"),
    ):
        raw = data.get(source)
        if isinstance(raw, str):
            enabled = raw.strip().lower() in {"1", "true", "yes"}
        else:
            enabled = bool(raw)
        if not enabled:
            continue
        if target in filters:
            return None, (
                jsonify({
                    "error": f"{source} cannot be combined with an explicit {target} filter"
                }),
                400,
            )
        filters[f"{target}__is_null"] = True

    start_time, error = _parse_iso_datetime(data.get("start_time"), "start_time", storage_timezone)
    if error:
        return None, error
    end_time, error = _parse_iso_datetime(data.get("end_time"), "end_time", storage_timezone)
    if error:
        return None, error
    if start_time is not None:
        filters["start_time"] = start_time
    if end_time is not None:
        filters["end_time"] = end_time
    if start_time is not None and end_time is not None and start_time >= end_time:
        return None, (jsonify({"error": "start_time must be earlier than end_time"}), 400)

    return {"filters": filters, "timezone": timezone.key}, None


def _tracer_or_error() -> tuple[Optional[LLMTracer], Optional[Response]]:
    container: DependencyContainer = g.container
    tracing_manager = container.resolve(TracingManager)
    llm_tracer = tracing_manager.get_tracer("llm")
    if not llm_tracer:
        return None, (jsonify({"error": "LLM tracer not found"}), 404)
    assert isinstance(llm_tracer, LLMTracer)
    return llm_tracer, None


def _export_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def _delivery_timing_store():
    """Resolve the delivery timing store, or ``None`` when it is not wired."""
    container: DependencyContainer = g.container
    if not container.has(DeliveryTimingStore):
        return None
    return container.resolve(DeliveryTimingStore)


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
    data = await request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object"}), 400

    page = data.get("page", 1)
    page_size = data.get("page_size", 20)
    if type(page) is not int or page < 1:
        return jsonify({"error": "page must be an integer greater than or equal to 1"}), 400
    if type(page_size) is not int or not 1 <= page_size <= 200:
        return jsonify({"error": "page_size must be an integer between 1 and 200"}), 400
    query = data.get("query")

    config: GlobalConfig = g.container.resolve(GlobalConfig)
    options, error = await _trace_request_options(data, config)
    if error:
        return error

    # 构建过滤条件
    filters = options["filters"]

    llm_tracer, error = _tracer_or_error()
    if error:
        return error
    assert llm_tracer is not None

    # 使用统一的查询接口
    records, total = llm_tracer.get_traces(
        filters=filters,
        query=query,
        search_fields=_TRACE_SEARCH_FIELDS,
        page=page,
        page_size=page_size
    )

    return jsonify({
        "items": [record.to_dict() for record in records],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size,
        "timezone": options["timezone"],
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
    config: GlobalConfig = g.container.resolve(GlobalConfig)
    options, error = await _trace_request_options(request.args, config)
    if error:
        return error
    llm_tracer, error = _tracer_or_error()
    if error:
        return error
    assert llm_tracer is not None
    stats = llm_tracer.get_statistics(
        filters=options["filters"],
        timezone_name=options["timezone"],
    )
    return jsonify(stats)


@tracing_bp.route("/llm/export", methods=["POST"])
@require_auth
async def export_llm_traces():
    """导出筛选后的 LLM 追踪记录，限制单次导出大小。"""
    data = await request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object"}), 400

    export_format = data.get("format", "json")
    if export_format not in {"json", "csv"}:
        return jsonify({"error": "format must be either json or csv"}), 400
    limit = data.get("limit", 1000)
    if type(limit) is not int or not 1 <= limit <= 10000:
        return jsonify({"error": "limit must be an integer between 1 and 10000"}), 400

    config: GlobalConfig = g.container.resolve(GlobalConfig)
    options, error = await _trace_request_options(data, config)
    if error:
        return error
    llm_tracer, error = _tracer_or_error()
    if error:
        return error
    assert llm_tracer is not None
    records, total = llm_tracer.get_traces(
        filters=options["filters"],
        query=data.get("query"),
        search_fields=_TRACE_SEARCH_FIELDS,
        page=1,
        page_size=limit,
    )
    items = [record.to_dict() for record in records]
    truncated = total > limit

    if export_format == "json":
        response = jsonify({
            "items": items,
            "exported": len(items),
            "total": total,
            "truncated": truncated,
            "timezone": options["timezone"],
        })
        response.headers["Content-Disposition"] = 'attachment; filename="llm-traces.json"'
        return response

    fields = (
        "id",
        "trace_id",
        "correlation_id",
        "provider",
        "backend_name",
        "model_id",
        "request_time",
        "response_time",
        "duration",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "cached_tokens",
        "cache_write_tokens",
        "usage_source",
        "ttft_ms",
        "attempt_count",
        # 重试与故障转移是两项：同一家重试 3 次与切换 3 家在 `attempt_count`
        # 上完全一样，而处置相反。导出的账单要能分开看。
        "retry_count",
        "failover_count",
        "status",
        "error_category",
        # 成本快照此前不在导出列里：导出的账单看不到钱，
        # 只能回到界面上逐条点开，等于没有导出。
        "cost_snapshot",
        "error",
    )
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for item in items:
        writer.writerow({field: _export_value(item.get(field)) for field in fields})
    response = Response("\ufeff" + output.getvalue(), mimetype="text/csv; charset=utf-8")
    response.headers["Content-Disposition"] = 'attachment; filename="llm-traces.csv"'
    response.headers["X-Exported-Count"] = str(len(items))
    response.headers["X-Total-Count"] = str(total)
    response.headers["X-Export-Truncated"] = str(truncated).lower()
    return response


@tracing_bp.route("/delivery/summary", methods=["GET"])
@require_auth
async def get_delivery_summary():
    """Aggregate reply latency per channel over a time range.

    这是「上周二 QQ 慢是模型还是发送」的答案来源。每个阶段的平均值只对
    **测到该阶段**的记录求平均，并给出样本数：非流式请求没有首字节，
    把它们按 0 计入会把平均值拉低成一个不存在的数字。

    除阶段耗时外还给出 ``counts``（分段数量、重试次数）——需求 19.5 九项里的
    后两项。它们一直被落库，此前只出现在逐条记录里，于是「这批慢投递是不是因为
    分了很多页」只能逐条翻。口径与阶段一致，且一个都没测到时给 ``null`` 而非 0。
    """
    store = _delivery_timing_store()
    if store is None:
        return jsonify({"error": "delivery timing store is not configured"}), 503

    config: GlobalConfig = g.container.resolve(GlobalConfig)
    storage_timezone, error = _timezone_or_error(
        config.system.timezone, config.system.timezone
    )
    if error:
        return error
    assert storage_timezone is not None

    start_time, error = _parse_iso_datetime(
        request.args.get("start_time"), "start_time", storage_timezone
    )
    if error:
        return error
    end_time, error = _parse_iso_datetime(
        request.args.get("end_time"), "end_time", storage_timezone
    )
    if error:
        return error

    channel = request.args.get("channel") or None
    try:
        summary = store.summarize(
            channel=channel,
            start_time=start_time,
            end_time=end_time,
        )
    except Exception:
        logger.opt(exception=True).error("Failed to summarize delivery timings")
        return jsonify({"error": "Failed to summarize delivery timings"}), 500
    summary["channels"] = store.list_channels()
    return jsonify(summary)


@tracing_bp.route("/delivery/recent", methods=["GET"])
@require_auth
async def get_recent_deliveries():
    """List recent per-reply timings. Contains durations only, never message text."""
    store = _delivery_timing_store()
    if store is None:
        return jsonify({"error": "delivery timing store is not configured"}), 503
    try:
        limit = int(request.args.get("limit", 50))
    except (TypeError, ValueError):
        return jsonify({"error": "limit must be an integer"}), 400
    try:
        items = store.recent(channel=request.args.get("channel") or None, limit=limit)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    except Exception:
        logger.opt(exception=True).error("Failed to list delivery timings")
        return jsonify({"error": "Failed to list delivery timings"}), 500
    return jsonify({"items": items})


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
