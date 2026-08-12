from abc import ABC, abstractmethod
from typing import Generic, Optional, TypeVar

T = TypeVar("T")

class MediaReferenceProvider(ABC, Generic[T]):
    """媒体引用提供者接口，由需要引用媒体的组件实现"""
    
    @abstractmethod
    def get_reference_owner(self, reference_key: str) -> Optional[T]:
        """根据引用键获取引用所有者"""
