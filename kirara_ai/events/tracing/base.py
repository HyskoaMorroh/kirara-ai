
import abc
from datetime import datetime


class TraceEvent(abc.ABC):
    """跟踪事件基类"""
    
    def __init__(self, trace_id: str):
        self.trace_id = trace_id
        self.timestamp = datetime.now()
    
    def __repr__(self):
        return f"{self.__class__.__name__}(trace_id={self.trace_id})"


class TraceStartEvent(TraceEvent):
    """跟踪开始事件"""


class TraceCompleteEvent(TraceEvent):
    """跟踪完成事件"""


class TraceFailEvent(TraceEvent):
    """跟踪失败事件"""
