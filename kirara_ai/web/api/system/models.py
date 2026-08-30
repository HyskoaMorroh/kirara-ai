from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel


class SystemStatus(BaseModel):
    """系统状态信息"""

    version: str
    uptime: float
    active_adapters: int
    active_backends: int
    loaded_plugins: int
    workflow_count: int
    memory_usage: Dict[str, float]
    cpu_usage: float
    cpu_info: str
    python_version: str
    platform: str
    has_proxy: bool



class SystemStatusResponse(BaseModel):
    """系统状态响应"""

    status: SystemStatus


class UpdateStatus(BaseModel):
    status: str
    message: str


class UpdateCheckResponse(BaseModel):
    """更新检查响应"""
    current_backend_version: str
    latest_backend_version: str
    backend_update_available: bool
    backend_download_url: Optional[str]
    latest_webui_version: str
    webui_download_url: Optional[str]
    #: 这次到底有没有真的去问注册表。
    #:
    #: `update.disable_auto_check` 打开时自动检查不外呼，于是
    #: `backend_update_available=False` 有两种完全不同的含义：「问过了，没有新版本」
    #: 和「没问」。少了这个字段界面只能从中选一种谎报——报「已是最新」会让用户
    #: 以为自己在最新版上，而实际上根本没查。
    #:
    #: 默认 `True`：正常路径就是查过了，旧的调用点不必改也不会误报成「没查」。
    checked: bool = True


ReadinessStatus = Literal["pass", "warn", "fail", "skip"]


class ReadinessCheck(BaseModel):
    """One stable, non-secret first-run diagnostic."""

    id: str
    status: ReadinessStatus
    summary: str
    remediation: str
    evidence: Dict[str, Any]


class ReadinessResponse(BaseModel):
    """Bounded readiness snapshot returned to authenticated administrators."""

    ready: bool
    timestamp: datetime
    checks: List[ReadinessCheck]
