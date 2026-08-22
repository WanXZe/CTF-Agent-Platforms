"""
数据模型层 —— 统一的业务数据结构。

所有层之间传递的数据都使用这里定义的模型，避免裸 dict 传递。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class ContainerStatus(str, Enum):
    """容器状态枚举。"""
    NOT_STARTED = "not_started"
    STARTING = "starting"
    RUNNING = "running"
    STOPPED = "stopped"
    FAILED = "failed"


class SolveStatus(str, Enum):
    """解题状态枚举。"""
    PENDING = "pending"
    RUNNING = "running"
    SOLVED = "solved"
    FAILED = "failed"
    TIMEOUT = "timeout"
    NEEDS_HUMAN = "needs_human"


@dataclass
class ChallengeFile:
    """题目附件。"""
    name: str
    url: str
    ext: str = ""


@dataclass
class Challenge:
    """
    题目信息（标准化模型，所有平台客户端统一返回此结构）。
    """
    id: str
    name: str
    category: str = ""
    description: str = ""
    value: int = 0
    tags: list[str] = field(default_factory=list)
    solved: bool = False
    # 容器相关
    need_container: bool = False
    container_status: ContainerStatus = ContainerStatus.NOT_STARTED
    connection_info: str = ""  # host:port 或 URL
    # 附件
    files: list[ChallengeFile] = field(default_factory=list)
    # 平台原始数据（透传，不做语义解析）
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """序列化为 dict（供 Web 接口返回）。"""
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "description": self.description,
            "value": self.value,
            "tags": self.tags,
            "solved": self.solved,
            "need_container": self.need_container,
            "container_status": self.container_status.value,
            "connection_info": self.connection_info,
            "files": [{"name": f.name, "url": f.url, "ext": f.ext} for f in self.files],
        }


@dataclass
class ContainerInfo:
    """容器环境信息。"""
    challenge_id: str
    status: ContainerStatus
    connection_info: str = ""
    endpoints: Optional[list[dict[str, Any]]] = None
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "challenge_id": self.challenge_id,
            "status": self.status.value,
            "connection_info": self.connection_info,
            "endpoints": self.endpoints,
            "message": self.message,
        }


@dataclass
class SubmitResult:
    """Flag 提交结果。"""
    status: str  # "correct" | "incorrect" | "already_solved" | "rate_limited" | "unknown"
    message: str
    is_correct: bool = False
    flag: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "message": self.message,
            "is_correct": self.is_correct,
            "flag": self.flag,
        }


@dataclass
class ToolResult:
    """
    工具调用统一返回结果（适配 Agent 工具调用格式）。
    Agent 推理层只消费此结构，不直接处理 HTTP 响应。
    """
    success: bool
    data: Any = None
    error: Optional[str] = None
    error_type: Optional[str] = None
    message: str = ""

    @classmethod
    def ok(cls, data: Any = None, message: str = "ok") -> "ToolResult":
        return cls(success=True, data=data, message=message)

    @classmethod
    def fail(cls, error_type: str, message: str, data: Any = None) -> "ToolResult":
        return cls(success=False, error=message, error_type=error_type, data=data, message=message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "error_type": self.error_type,
            "message": self.message,
        }
