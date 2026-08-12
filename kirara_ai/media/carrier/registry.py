from typing import Dict

from kirara_ai.ioc.container import DependencyContainer

from .provider import MediaReferenceProvider


class MediaCarrierRegistry:
    """媒体载体注册表，管理所有媒体引用提供者"""
    
    def __init__(self, container: DependencyContainer):
        self.container = container
        self._providers: Dict[str, MediaReferenceProvider] = {}
    
    def register(self, provider_name: str, provider_instance: MediaReferenceProvider) -> None:
        """注册媒体引用提供者"""
        self._providers[provider_name] = provider_instance
    
    def unregister(self, provider_name: str) -> None:
        """注销媒体引用提供者"""
        if provider_name in self._providers:
            del self._providers[provider_name]
    
    def get_provider(self, provider_name: str) -> MediaReferenceProvider:
        """获取媒体引用提供者实例"""
        if provider_name not in self._providers:
            raise ValueError(f"Provider not found: {provider_name}")
        return self._providers[provider_name]
    
    def get_all_providers(self) -> Dict[str, MediaReferenceProvider]:
        """获取所有媒体引用提供者实例"""
        return self._providers
