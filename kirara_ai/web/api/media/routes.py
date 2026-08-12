import asyncio
import io
import os
from typing import Optional

import pytz
from quart import Blueprint, g, jsonify, request, send_file

from kirara_ai.config.global_config import GlobalConfig
from kirara_ai.media.manager import MediaManager
from kirara_ai.media.media_object import Media
from kirara_ai.media.types.media_type import MediaType

from ...auth.middleware import require_auth
from .models import MediaBatchDeleteRequest, MediaItem, MediaListResponse, MediaMetadata, MediaSearchParams

media_bp = Blueprint("media", __name__)


# 生成缩略图
async def generate_thumbnail(image_data: bytes) -> io.BytesIO:
    """生成图片缩略图，并返回BytesIO对象"""
    from PIL import Image

    def _generate_thumbnail(image_data: bytes) -> io.BytesIO:
        """在线程中运行的同步缩略图生成函数"""
        with Image.open(io.BytesIO(image_data)) as img:
            width, height = img.size
            if width > height:
                new_width = 300
                new_height = int(height * (300 / width))
            else:
                new_height = 300
                new_width = int(width * (300 / height))

            img.thumbnail((new_width, new_height))
            output = io.BytesIO()
            img = img.convert("RGB")
            img.save(output, format="WEBP", optimize=True, quality=65)
            output.seek(0)
            return output

    return await asyncio.to_thread(_generate_thumbnail, image_data)

def _get_media_manager() -> MediaManager:
    """获取媒体管理器实例"""
    return g.container.resolve(MediaManager)

def _convert_media_to_api_item(media: Media) -> Optional[MediaItem]:
    """将Media对象转换为API响应格式"""
    if not media or not media.metadata:
        return None
    
    metadata = media.metadata
    content_type = f"{metadata.media_type.value}/{metadata.format}" if metadata.media_type and metadata.format else "application/octet-stream"
        
    return MediaItem(
        id=media.media_id,
        url=f"",
        thumbnail_url="",
        metadata=MediaMetadata(
            filename=os.path.basename(metadata.path) if metadata.path else f"{media.media_id}.{metadata.format}",
            content_type=content_type,
            size=metadata.size or 0,
            upload_time=metadata.created_at,
            source=metadata.source,
            tags=list(metadata.tags),
            references=list(metadata.references),
        )
    )

@media_bp.route("/list", methods=["POST"])
@require_auth
async def list_media():
    """获取媒体列表，支持分页和搜索"""
    data = await request.get_json()
    search_params = MediaSearchParams(**data)
    
    manager = _get_media_manager()
    
    # 构建搜索条件
    all_media_ids = []
    
    # 如果有指定内容类型，筛选对应类型
    if search_params.content_type:
        if search_params.content_type.startswith("image/"):
            media_type = MediaType.IMAGE
        elif search_params.content_type.startswith("video/"):
            media_type = MediaType.VIDEO
        elif search_params.content_type.startswith("audio/"):
            media_type = MediaType.AUDIO
        else:
            media_type = MediaType.FILE
        
        all_media_ids = manager.search_by_type(media_type)
    else:
        all_media_ids = manager.get_all_media_ids()
    
    # 如果有指定标签，继续筛选
    if search_params.tags and len(search_params.tags) > 0:
        filtered_ids = []
        for media_id in all_media_ids:
            metadata = manager.get_metadata(media_id)
            if metadata and any(tag in metadata.tags for tag in search_params.tags):
                filtered_ids.append(media_id)
        all_media_ids = filtered_ids
    
    # 如果有搜索关键词，继续筛选
    if search_params.query:
        description_ids = manager.search_by_description(search_params.query)
        source_ids = manager.search_by_source(search_params.query)
        all_media_ids = [
            media_id for media_id in all_media_ids
            if media_id in description_ids or media_id in source_ids
        ]
    
    # 如果有日期范围，继续筛选
    if search_params.start_date or search_params.end_date:
        filtered_ids = []
        for media_id in all_media_ids:
            metadata = manager.get_metadata(media_id)
            if metadata:
                tz = pytz.timezone(g.container.resolve(GlobalConfig).system.timezone)
                created_at = metadata.created_at.replace(tzinfo=tz)
                start_date = search_params.start_date.replace(tzinfo=tz) if search_params.start_date else None
                if start_date and created_at < start_date:
                    continue
                end_date = search_params.end_date.replace(tzinfo=tz) if search_params.end_date else None
                if end_date and created_at > end_date:
                    continue
                filtered_ids.append(media_id)
        all_media_ids = filtered_ids
    
    # 计算分页
    total = len(all_media_ids)
    start_idx = (search_params.page - 1) * search_params.page_size
    end_idx = start_idx + search_params.page_size
    page_ids = all_media_ids[start_idx:end_idx]
    
    # 构建返回结果
    items = []
    for media_id in page_ids:
        if media := manager.get_media(media_id):
            if item:= _convert_media_to_api_item(media):
                items.append(item)
    
    return MediaListResponse(
        items=items,
        total=total,
        has_more=end_idx < total,
        page_size=search_params.page_size,
    ).model_dump()

@media_bp.route("/file/<media_id>", methods=["GET"])
@require_auth
async def get_media_file(media_id):
    """获取媒体文件"""
    manager = _get_media_manager()
    media = manager.get_media(media_id)
    if not media:
        return jsonify({"error": "Media not found"}), 404
    
    return await send_file(io.BytesIO(await media.get_data()), mimetype=media.metadata.mime_type)

@media_bp.route("/preview/<media_id>", methods=["GET"])
@require_auth
async def get_thumbnail(media_id):
    """获取缩略图"""
    config = g.container.resolve(GlobalConfig)
    media_manager = _get_media_manager()
    media = media_manager.get_media(media_id)
    if not media:
        return jsonify({"error": "Media not found"}), 404
    
    data = await media.get_data()
    
    if not data:
        return jsonify({"error": "Media not found"}), 404
    
    if media.metadata.media_type == MediaType.IMAGE:
        if media.metadata.format == "gif":
            return await send_file(io.BytesIO(data), mimetype="image/gif")
        thumbnail = await generate_thumbnail(data)
        return await send_file(thumbnail, mimetype="image/webp")
    elif media.metadata.media_type == MediaType.VIDEO:
        # 视频类型直接返回原始数据，不做缩略图处理
        return await send_file(io.BytesIO(data), mimetype="video/mp4")
    else:
        return jsonify({"error": "Unsupported media type"}), 400
    
@media_bp.route("/delete/<media_id>", methods=["DELETE"])
@require_auth
async def delete_media(media_id):
    """删除单个媒体文件"""
    manager = _get_media_manager()
    
    # 检查媒体是否存在
    if media_id not in manager.metadata_cache:
        return jsonify({"error": "File not found"}), 404
    
    # 删除媒体文件
    manager.delete_media(media_id)
    
    return jsonify({"success": True})

@media_bp.route("/batch-delete", methods=["POST"])
@require_auth
async def batch_delete():
    """批量删除媒体文件"""
    data = await request.get_json()
    delete_request = MediaBatchDeleteRequest(**data)
    
    manager = _get_media_manager()
    success_count = 0
    
    for media_id in delete_request.ids:
        if media_id in manager.metadata_cache:
            # 删除媒体文件
            manager.delete_media(media_id)
            success_count += 1
    
    return jsonify({"success": True, "deleted_count": success_count}) 