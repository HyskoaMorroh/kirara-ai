from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class MediaMetadata(BaseModel):
    """媒体元数据"""
    filename: str
    content_type: str
    size: int  # 文件大小，单位字节
    upload_time: datetime  # 上传时间
    source: Optional[str] = None  # 来源
    tags: List[str] = []  # 标签
    references: List[str] = []


class MediaItem(BaseModel):
    """媒体项"""
    id: str  # 媒体ID
    url: str  # 媒体URL
    thumbnail_url: Optional[str] = None  # 缩略图URL
    metadata: MediaMetadata


class MediaListResponse(BaseModel):
    """媒体列表响应"""
    items: List[MediaItem]
    total: int
    has_more: bool  # 是否有更多数据
    page_size: int  # 每页数量


class MediaSearchParams(BaseModel):
    """媒体搜索参数"""
    query: Optional[str] = None  # 搜索关键词
    content_type: Optional[str] = None  # 媒体类型
    start_date: Optional[datetime] = None  # 开始日期
    end_date: Optional[datetime] = None  # 结束日期
    tags: List[str] = []  # 标签
    page: int = 1  # 页码
    page_size: int = 20  # 每页数量


class MediaBatchDeleteRequest(BaseModel):
    """批量删除请求"""
    ids: List[str]  # 要删除的媒体ID列表 