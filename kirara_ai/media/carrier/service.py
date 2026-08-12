from typing import Any, Dict, List, Optional, Tuple

from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.media.manager import MediaManager
from kirara_ai.media.media_object import Media

from .registry import MediaCarrierRegistry


class MediaCarrierService:
    """媒体载体服务，负责媒体引用的管理"""
    
    def __init__(self, container: DependencyContainer, media_manager: MediaManager):
        self.container = container
        self.media_manager = media_manager
        self.registry = container.resolve(MediaCarrierRegistry)
        # 引用索引：reference_key -> (provider_name, media_id)
        self._reference_index: Dict[str, Tuple[str, str]] = {}
        self._build_reference_index()
    
    def _build_reference_index(self) -> None:
        """构建引用索引"""
        self._reference_index.clear()
        
        # 遍历所有媒体元数据，提取引用信息
        for media_id, metadata in self.media_manager.metadata_cache.items():
            for reference_key in metadata.references:
                # 尝试从引用键中提取提供者名称
                if ":" in reference_key:
                    provider_name, _ = reference_key.split(":", 1)
                    self._reference_index[reference_key] = (provider_name, media_id)
    
    def register_reference(self, media_id: str, provider_name: str, reference_key: str) -> None:
        """注册媒体引用"""
        # 检查媒体是否存在
        if media_id not in self.media_manager.metadata_cache:
            raise ValueError(f"媒体不存在: {media_id}")
        
        # 构造完整引用键
        full_reference_key = f"{provider_name}:{reference_key}"
        
        # 添加引用
        self.media_manager.add_reference(media_id, full_reference_key)
        
        # 更新引用索引
        self._reference_index[full_reference_key] = (provider_name, media_id)
    
    def remove_reference(self, media_id: str, provider_name: str, reference_key: str) -> None:
        """移除媒体引用"""
        # 检查媒体是否存在
        if media_id not in self.media_manager.metadata_cache:
            return
        
        # 构造完整引用键
        full_reference_key = f"{provider_name}:{reference_key}"
        
        # 移除引用
        self.media_manager.remove_reference(media_id, full_reference_key)
        
        # 更新引用索引
        if full_reference_key in self._reference_index:
            del self._reference_index[full_reference_key]
    
    def get_reference_owner(self, reference_key: str) -> Optional[Any]:
        """获取引用所有者"""
        if reference_key not in self._reference_index:
            return None
        
        provider_name, _ = self._reference_index[reference_key]
        try:
            provider = self.registry.get_provider(provider_name)
            return provider.get_reference_owner(reference_key.split(":", 1)[1])
        except (ValueError, IndexError):
            return None
    
    def get_media_by_reference(self, provider_name: str, reference_key: str) -> List[Media]:
        """根据引用键获取媒体对象"""
        full_reference_key = f"{provider_name}:{reference_key}"
        
        result = []
        for ref_key, (prov_name, media_id) in self._reference_index.items():
            if ref_key == full_reference_key:
                media = self.media_manager.get_media(media_id)
                if media:
                    result.append(media)
        
        return result
    
    def get_references_by_media(self, media_id: str) -> List[Tuple[str, str]]:
        """获取媒体的所有引用信息"""
        if media_id not in self.media_manager.metadata_cache:
            return []
        
        metadata = self.media_manager.metadata_cache[media_id]
        references = []
        
        for reference_key in metadata.references:
            if ":" in reference_key:
                provider_name, key = reference_key.split(":", 1)
                references.append((provider_name, key))
        
        return references
    
    def cleanup_orphaned_references(self) -> int:
        """清理孤立的引用（引用提供者不存在）"""
        count = 0
        all_providers = set(self.registry._providers.keys())
        
        for media_id, metadata in list(self.media_manager.metadata_cache.items()):
            orphaned_refs = set()
            
            for reference_key in metadata.references:
                if ":" in reference_key:
                    provider_name, _ = reference_key.split(":", 1)
                    if provider_name not in all_providers:
                        orphaned_refs.add(reference_key)
            
            # 移除孤立引用
            for ref in orphaned_refs:
                self.media_manager.remove_reference(media_id, ref)
                if ref in self._reference_index:
                    del self._reference_index[ref]
                count += 1
        
        return count
