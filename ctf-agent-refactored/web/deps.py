"""
Web 层依赖注入。

支持通过请求头 X-Platform-Id 选择平台（前端侧边栏切换）。
平台 Skill 从 ctf-platform-skill 的 PlatformRegistry 加载。
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import Request

from config import Settings
from core.skills import PlatformSkillAdapter

logger = logging.getLogger(__name__)
_settings: Optional[Settings] = None
_adapter_cache: dict[str, PlatformSkillAdapter] = {}


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


async def get_platform_adapter(request: Request) -> PlatformSkillAdapter:
    """
    获取平台适配器，优先使用请求头 X-Platform-Id 指定的平台。
    前端侧边栏切换平台时通过此头传递。
    """
    platform_id = request.headers.get("x-platform-id", "").strip()
    if not platform_id:
        platform_id = "mock-local"

    if platform_id in _adapter_cache:
        return _adapter_cache[platform_id]

    adapter = PlatformSkillAdapter(get_settings(), platform_name=platform_id)
    _adapter_cache[platform_id] = adapter
    return adapter


def list_available_platforms() -> list[dict]:
    """列出所有可用平台（供前端侧边栏展示）。"""
    try:
        from ctf_platform_skill.registry import PlatformRegistry
        return PlatformRegistry.get_instance().list_platforms()
    except Exception as e:
        logger.error("列出平台失败: %s", e)
        return []
