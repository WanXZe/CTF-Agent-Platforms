"""
统一异常定义。

所有层抛出的业务异常都继承自 CTFAgentError，
Web 层通过全局异常处理器捕获并返回结构化 JSON。
"""

from __future__ import annotations


class CTFAgentError(Exception):
    """基类异常。"""
    def __init__(self, message: str = "", *, error_type: str = "internal_error", details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.error_type = error_type
        self.details = details or {}

    def to_dict(self) -> dict:
        return {
            "success": False,
            "error": self.error_type,
            "message": self.message,
            "details": self.details,
        }


class PlatformError(CTFAgentError):
    """靶场平台接口错误。"""
    def __init__(self, message: str = "", *, code: str = "", details: dict | None = None) -> None:
        super().__init__(message, error_type="platform_error", details=details)
        self.code = code


class NetworkError(CTFAgentError):
    """网络异常（超时 / 连接失败 / DNS）。"""
    def __init__(self, message: str = "", *, details: dict | None = None) -> None:
        super().__init__(message, error_type="network_error", details=details)


class ContainerError(CTFAgentError):
    """容器操作失败。"""
    def __init__(self, message: str = "", *, challenge_id: str = "", details: dict | None = None) -> None:
        super().__init__(message, error_type="container_error", details=details)
        self.challenge_id = challenge_id


class InvalidParamError(CTFAgentError):
    """参数非法。"""
    def __init__(self, message: str = "", *, field: str = "", details: dict | None = None) -> None:
        super().__init__(message, error_type="invalid_param", details=details)
        self.field = field


class RateLimitError(CTFAgentError):
    """平台限流。"""
    def __init__(self, message: str = "", *, cooldown_seconds: int = 0, details: dict | None = None) -> None:
        super().__init__(message, error_type="rate_limited", details=details)
        self.cooldown_seconds = cooldown_seconds


class ChallengeNotFoundError(CTFAgentError):
    """题目不存在。"""
    def __init__(self, challenge_id: str = "", details: dict | None = None) -> None:
        super().__init__(f"题目不存在: {challenge_id}", error_type="not_found", details=details)
        self.challenge_id = challenge_id
