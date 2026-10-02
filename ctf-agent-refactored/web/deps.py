"""
Web 层依赖注入。

支持通过请求头 X-Platform-Id 选择平台（前端侧边栏切换）。
平台 Skill 从 ctf-platform-skill 的 PlatformRegistry 加载；
额外内置一个 "local" 平台：直接读取本地题库目录（0xgame 布局）。
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import Request

from config import Settings
from core.skills import PlatformSkillAdapter
from core.skills.local_adapter import LocalAdapter

logger = logging.getLogger(__name__)
_settings: Optional[Settings] = None
_adapter_cache: dict[str, object] = {}
LOCAL_PLATFORM_ID = "local"


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def get_adapter_by_id(platform_id: str):
    """按平台 ID 取适配器（local = 本地题库，其余 = ctf-platform-skill 平台）。"""
    platform_id = (platform_id or LOCAL_PLATFORM_ID).strip()
    if platform_id in _adapter_cache:
        return _adapter_cache[platform_id]
    if platform_id == LOCAL_PLATFORM_ID:
        adapter: object = LocalAdapter(get_settings())
    else:
        from ctf_platform_skill.registry import PlatformRegistry
        if PlatformRegistry.get_instance().get(platform_id) is None:
            raise ValueError(f"未知平台: {platform_id}")
        adapter = PlatformSkillAdapter(get_settings(), platform_name=platform_id)
    _adapter_cache[platform_id] = adapter
    return adapter


def get_platform_id(request: Request):
    platform_id = request.headers.get('x-platform-id', '').strip()
    return platform_id or (LOCAL_PLATFORM_ID if get_settings().local_root else 'mock-local')


async def get_platform_adapter(request: Request):
    """
    获取平台适配器，优先使用请求头 X-Platform-Id 指定的平台。
    前端侧边栏切换平台时通过此头传递。
    """
    return get_adapter_by_id(get_platform_id(request))


def list_available_platforms() -> list[dict]:
    """列出所有可用平台（本地题库 + 已注册平台），供前端侧边栏展示。"""
    platforms: list[dict] = []
    try:
        settings = get_settings()
        if settings.local_root:
            from pathlib import Path

            root = Path(settings.local_root).expanduser()
            count = len(list(root.glob("*/*/QuestionInfo.json"))) if root.is_dir() else 0
            platforms.append({
                "id": LOCAL_PLATFORM_ID,
                "name": "本地题库",
                "icon": "🗂️",
                "type": "local",
                "description": f"0xgame 本地题库（{count} 题）",
            })
    except Exception as exc:
        logger.warning("列出本地题库失败: %s", exc)
    try:
        from ctf_platform_skill.registry import PlatformRegistry
        platforms.extend(PlatformRegistry.get_instance().list_platforms())
    except Exception as e:
        logger.error("列出平台失败: %s", e)
    return platforms
