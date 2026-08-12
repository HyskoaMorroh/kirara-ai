import asyncio
import json
import os
import re
import traceback
from collections import deque
from datetime import datetime
from typing import Any, Callable, Dict, List

from loguru import logger

# 创建 logs 文件夹
LOG_DIR = "logs"
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

# 配置日志格式和颜色
logger.remove()  # 移除默认的日志处理器

# 定义日志格式
log_format = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{extra[tag]: <12}</cyan> | "
    "<level>{message}</level>"
)

# 添加控制台日志处理器
logger.add(
    sink=lambda msg: print(msg.strip()),  # 输出到控制台
    format=log_format,
    level="DEBUG",
    colorize=True,
)

# 添加文件日志处理器，支持日志轮转
log_file = os.path.join(LOG_DIR, "log_{time:YYYY-MM-DD}.log")
logger.add(
    sink=log_file,
    format=log_format,
    level="DEBUG",
    rotation="00:00",  # 每天午夜轮转
    retention="7 days",  # 保留7天的日志
    compression="zip",  # 压缩旧日志文件
    colorize=False,
)

# 全局日志实例
_global_logger = logger

# 内存中保存最近的日志，用于新连接时推送历史日志
_recent_logs: deque[Dict] = deque(maxlen=500)  # 保存最近500条日志

LogBroadcasterCallback = Callable[[Dict | List[Dict]], None]
# 通用日志处理器管理类
class LogBroadcaster:
    """通用日志广播器，支持多种日志订阅方式"""
    
    _instance = None
    _subscribers: Dict[int, LogBroadcasterCallback] = {}
    _next_id = 0
    _initialized = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(LogBroadcaster, cls).__new__(cls)
            cls._instance._subscribers = {}  # 订阅者字典，键为订阅者ID，值为回调函数
            cls._instance._next_id = 0  # 下一个订阅者ID
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if not self._initialized:
            self._initialized = True
            # 添加日志处理器
            self._setup_log_handler()
    
    def _setup_log_handler(self):
        """设置日志处理器"""
        def log_sink(message):
            # 格式化日志消息
            # with {tag<12}
            tag = message.record["extra"]["tag"]
            log_entry = {
                "type": "log",
                "level": message.record["level"].name,
                "content": message.record['message'],
                "timestamp": datetime.now().isoformat(),
                "tag": tag
            }
            
            if message.record.get('exception'):
                exception = message.record['exception']
                if exception.value:
                    log_entry["content"] += "\n" + '\n'.join(traceback.format_exception(exception.value))
            
            # 保存到最近日志
            _recent_logs.append(log_entry)
            
            # 广播到所有订阅者
            self._broadcast_log(log_entry)
        
        # 添加日志处理器
        _global_logger.add(
            sink=log_sink,
            format=log_format,
            level="INFO",
            colorize=False,
        )
    
    def _broadcast_log(self, log_entry: Dict):
        """广播日志到所有订阅者"""
        to_remove = []
        for subscriber_id, callback in self._subscribers.items():
            try:
                callback(log_entry)
            except Exception:
                # 如果发送失败，标记该订阅者为断开
                to_remove.append(subscriber_id)
        
        # 移除断开的订阅者
        for subscriber_id in to_remove:
            self.unsubscribe(subscriber_id)
    
    def subscribe(self, callback: LogBroadcasterCallback) -> int:
        """
        添加日志订阅者
        :param callback: 回调函数，接收日志条目并处理
        :return: 订阅者ID，用于后续取消订阅
        """
        subscriber_id = self._next_id
        self._next_id += 1
        self._subscribers[subscriber_id] = callback
        return subscriber_id
    
    def unsubscribe(self, subscriber_id: int) -> bool:
        """
        取消日志订阅
        :param subscriber_id: 订阅者ID
        :return: 是否成功取消订阅
        """
        if subscriber_id in self._subscribers:
            del self._subscribers[subscriber_id]
            return True
        return False
    
    def send_recent_logs(self, callback: LogBroadcasterCallback):
        """
        发送最近的日志到指定回调
        :param callback: 回调函数，接收日志条目并处理
        """
        callback(list(_recent_logs))

# WebSocket日志处理器，作为LogBroadcaster的一个应用
class WebSocketLogHandler:
    """WebSocket日志处理器，用于将日志发送到WebSocket客户端"""
    
    # 存储所有活跃的WebSocket连接及其订阅ID
    _websockets: Dict[Any, int] = {}
    
    @classmethod
    def add_websocket(cls, ws, loop: asyncio.AbstractEventLoop):
        """
        添加WebSocket连接
        :param ws: WebSocket连接对象
        """
        # 创建回调函数
        def send_to_ws(log_entries: List[Dict] | Dict):
            loop.create_task(ws.send(json.dumps(log_entries)))
        
        # 获取日志广播器实例
        broadcaster = LogBroadcaster()
        
        # 先发送最近的日志
        broadcaster.send_recent_logs(send_to_ws)
        
        # 订阅新日志
        subscriber_id = broadcaster.subscribe(send_to_ws)
        cls._websockets[ws] = subscriber_id
    
    @classmethod
    def remove_websocket(cls, ws):
        """
        移除WebSocket连接
        :param ws: WebSocket连接对象
        """
        if ws in cls._websockets:
            # 取消订阅
            LogBroadcaster().unsubscribe(cls._websockets[ws])
            del cls._websockets[ws]

# 初始化日志广播器
def init_log_broadcaster():
    """初始化日志广播器"""
    LogBroadcaster()
    
init_log_broadcaster()

def get_logger(tag: str):
    """
    获取带有特定标签的日志记录器
    :param tag: 日志标签
    :return: 日志记录器
    """
    return _global_logger.bind(tag=tag)


class HypercornLoggerWrapper:
    def __init__(self, logger):
        self.logger = logger

    def critical(self, message: str, *args: Any, **kwargs: Any) -> None:
        self.logger.critical(message, *args, **kwargs)

    def error(self, message: str, *args: Any, **kwargs: Any) -> None:
        self.logger.error(message, *args, **kwargs)

    def warning(self, message: str, *args: Any, **kwargs: Any) -> None:
        self.logger.warning(message, *args, **kwargs)

    def info(self, message: str, *args: Any, **kwargs: Any) -> None:
        log_fmt = re.sub(r"%\((\w+)\)s", r"{\1}", message)
        atoms = args[0] if args else {}
        self.logger.info(log_fmt, **atoms)

    def debug(self, message: str, *args: Any, **kwargs: Any) -> None:
        self.logger.debug(message, *args, **kwargs)

    def exception(self, message: str, *args: Any, **kwargs: Any) -> None:
        self.logger.exception(message, *args, **kwargs)

    def log(self, level: int, message: str, *args: Any, **kwargs: Any) -> None:
        self.logger.log(level, message, *args, **kwargs)


def get_async_logger(tag: str):
    """
    获取带有特定标签的日志记录器
    :param tag: 日志标签
    :return: 日志记录器
    """
    return HypercornLoggerWrapper(_global_logger.bind(tag=tag))
