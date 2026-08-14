import mimetypes
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from kirara_ai.media.types.media_type import MediaType


class MediaMetadata:
    """媒体元数据类"""
    
    def __init__(
        self,
        media_id: str,
        media_type: MediaType,
        format: str,
        size: Optional[int] = None,
        created_at: Optional[datetime] = None,
        source: Optional[str] = None,
        description: Optional[str] = None,
        tags: Optional[List[str]] = None,
        references: Optional[Set[str]] = None,
        url: Optional[str] = None,
        path: Optional[str] = None
    ):
        self.media_id = media_id
        self.media_type = media_type
        self.format = format
        self.size = size
        self.created_at = created_at or datetime.now()
        self.source = source
        self.description = description
        self.tags: List[str] = tags or []
        self.references: Set[str] = references or set()
        self.url = url
        self.path = path
        
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        result = {
            "media_id": self.media_id,
            "created_at": self.created_at.isoformat(),
            "source": self.source,
            "description": self.description,
            "tags": self.tags,
            "references": list(self.references),
        }
        
        # 添加可选字段
        if self.media_type:
            result["media_type"] = self.media_type.value
        if self.format:
            result["format"] = self.format
        if self.size is not None:
            result["size"] = self.size
        if self.url:
            result["url"] = self.url
        if self.path:
            result["path"] = self.path
            
        return result


    @property
    def mime_type(self) -> str:
        """获取 MIME 类型"""
        return f"{self.media_type.value}/{self.format}"
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'MediaMetadata':
        """从字典创建元数据"""
        # 兼容 3.2.0 及更早版本写入的元数据：这些文件在 media_type/format 为空时不会写入对应字段。
        # 此处做兜底推断，避免旧数据在升级后加载失败而丢失媒体索引。
        format = data.get("format")
        if not format:
            path = data.get("path") or data.get("url") or ""
            format = path.rsplit(".", 1)[-1].split("?")[0] if "." in path else "bin"
        if data.get("media_type"):
            media_type = MediaType(data["media_type"])
        else:
            # 由扩展名反推 mime，进而得到媒体大类，避免出现 file/png 这类错误的 mime
            guessed_mime, _ = mimetypes.guess_type(f"x.{format}")
            media_type = MediaType.from_mime(guessed_mime) if guessed_mime else MediaType.FILE
        return cls(
            media_id=data["media_id"],
            media_type=media_type,
            format=format,
            size=data.get("size"),
            created_at=datetime.fromisoformat(data["created_at"]),
            source=data.get("source"),
            description=data.get("description"),
            tags=data.get("tags", []),
            references=set(data.get("references", [])),
            url=data.get("url"),
            path=data.get("path")
        ) 