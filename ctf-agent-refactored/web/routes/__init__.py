"""Web 路由层。"""
from .challenges import router as challenges_router
from .containers import router as containers_router
from .platform import router as platform_router

__all__ = ["challenges_router", "containers_router", "platform_router"]
