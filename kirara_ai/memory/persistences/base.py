import threading
from abc import ABC, abstractmethod
from queue import Empty, Queue
from typing import List, Tuple

from kirara_ai.logger import get_logger
from kirara_ai.memory.entry import MemoryEntry


class MemoryPersistence(ABC):
    """持久化层抽象类"""

    @abstractmethod
    def save(self, scope_key: str, entries: List[MemoryEntry]) -> None:
        pass

    @abstractmethod
    def load(self, scope_key: str) -> List[MemoryEntry]:
        pass

    @abstractmethod
    def flush(self) -> None:
        """确保所有数据都已持久化"""

logger = get_logger("MemoryPersistence")
class AsyncMemoryPersistence:
    """异步持久化管理器"""

    def __init__(self, persistence: MemoryPersistence):
        self.persistence = persistence
        self.queue: Queue[Tuple[str, List[MemoryEntry]]] = Queue()
        self.running = True
        self.worker = threading.Thread(target=self._worker, daemon=True)
        self.worker.start()

    def _worker(self):
        while self.running or not self.queue.empty():
            try:
                scope_key, entries = self.queue.get(timeout=1)
                try:
                    self.persistence.save(scope_key, entries)
                    logger.info(f"Saved {scope_key} with {len(entries)} entries")
                finally:
                    self.queue.task_done()
            except Empty:
                if not self.running:
                    break
                continue
            except Exception as e:
                logger.error(f"Error saving memory: {e}")
                continue

    def load(self, scope_key: str) -> List[MemoryEntry]:
        return self.persistence.load(scope_key)

    def save(self, scope_key: str, entries: List[MemoryEntry]):
        self.queue.put((scope_key, entries))

    def stop(self):
        self.running = False
        self.worker.join()
        # The worker exits only after every item present at shutdown has been
        # acknowledged. Keep this assertion local so future implementations
        # cannot silently drop queued writes.
        self.queue.join()
        self.persistence.flush()
