from enum import Enum
from typing import Dict, List, Optional
from pydantic import BaseModel, Field
from datetime import datetime


class ServerStatus(str, Enum):
    """服务器状态枚举"""

    ONLINE = "online"
    OFFLINE = "offline"
    BUSY = "busy"
    MAINTENANCE = "maintenance"


class ServerCapacity(BaseModel):
    """服务器容量模型"""

    max_sessions: int = Field(default=100, description="最大会话数")
    current_sessions: int = Field(default=0, description="当前会话数")
    cpu_usage: float = Field(default=0.0, description="CPU使用率")
    memory_usage: float = Field(default=0.0, description="内存使用率")
    last_updated: datetime = Field(
        default_factory=datetime.now, description="最后更新时间"
    )


class RuntimeServer(BaseModel):
    """运行时服务器信息"""

    server_id: str = Field(..., description="服务器ID")
    host: str = Field(..., description="服务器主机地址")
    port: int = Field(..., description="服务器端口")
    status: ServerStatus = Field(default=ServerStatus.ONLINE, description="服务器状态")
    capacity: ServerCapacity = Field(
        default_factory=ServerCapacity, description="服务器容量"
    )
    metadata: Dict = Field(default_factory=dict, description="服务器元数据")


class SessionInfo(BaseModel):
    """会话信息"""

    session_id: str = Field(..., description="会话ID")
    server_id: str = Field(..., description="服务器ID")
    created_at: datetime = Field(default_factory=datetime.now, description="创建时间")
    last_active: datetime = Field(
        default_factory=datetime.now, description="最后活动时间"
    )
    metadata: Dict = Field(default_factory=dict, description="会话元数据")


class ProxyConfig(BaseModel):
    """代理配置"""

    api_key: str = Field(..., description="API密钥")
    health_check_interval: int = Field(default=30, description="健康检查间隔(秒)")
    session_timeout: int = Field(default=3600, description="会话超时时间(秒)")
