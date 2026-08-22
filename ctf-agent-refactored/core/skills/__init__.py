"""Skill 插件层。"""
from .platform_skill_adapter import PlatformSkillAdapter
from .registry import SkillRegistry, get_registry

__all__ = ["PlatformSkillAdapter", "SkillRegistry", "get_registry"]
