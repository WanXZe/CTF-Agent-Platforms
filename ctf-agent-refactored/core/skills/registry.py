"""
Skill 插件注册中心。

所有外部能力 Skill 在此注册，Agent 通过 registry 调用 Skill 暴露的接口，
不直接 import 具体 Skill 实现，实现「推理层 ↔ Skill 层」解耦。

子项目A（ctf-platform-skill）作为外部靶场平台 Skill 插件在此注册。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from core.models import ToolResult

logger = logging.getLogger(__name__)


class SkillRegistry:
    """
    Skill 注册中心。

    用法：
        registry = SkillRegistry()
        registry.register("platform", platform_skill_instance)
        result = registry.call("platform", "list_challenges", light=True)
    """

    def __init__(self) -> None:
        self._skills: dict[str, Any] = {}
        self._skill_meta: dict[str, dict[str, str]] = {}

    def register(self, name: str, skill: Any, description: str = "") -> None:
        """
        注册一个 Skill 实例。

        Args:
            name: Skill 名称（唯一标识）
            skill: Skill 实例（需具备可调用的能力方法）
            description: Skill 描述
        """
        self._skills[name] = skill
        self._skill_meta[name] = {"description": description}
        logger.info("Skill registered: %s (%s)", name, description or type(skill).__name__)

    def unregister(self, name: str) -> None:
        """注销 Skill。"""
        self._skills.pop(name, None)
        self._skill_meta.pop(name, None)

    def get(self, name: str) -> Optional[Any]:
        """获取 Skill 实例。"""
        return self._skills.get(name)

    def has(self, name: str) -> bool:
        return name in self._skills

    def list_skills(self) -> list[dict[str, str]]:
        """列出所有已注册 Skill。"""
        return [{"name": n, **meta} for n, meta in self._skill_meta.items()]

    async def call(self, skill_name: str, method: str, **kwargs: Any) -> ToolResult:
        """
        调用 Skill 的指定方法。

        Args:
            skill_name: 已注册的 Skill 名称
            method: 方法名
            **kwargs: 透传给方法的参数

        Returns:
            ToolResult 统一结果
        """
        skill = self._skills.get(skill_name)
        if skill is None:
            return ToolResult.fail("skill_not_found", f"Skill '{skill_name}' 未注册")

        func = getattr(skill, method, None)
        if func is None or not callable(func):
            return ToolResult.fail("method_not_found", f"Skill '{skill_name}' 无方法 '{method}'")

        try:
            result = await func(**kwargs)
            # Skill 返回的是结构化 dict，转换为 ToolResult
            if isinstance(result, dict) and "success" in result:
                if result["success"]:
                    return ToolResult.ok(data=result.get("data"), message=result.get("message", "ok"))
                return ToolResult.fail(
                    error_type=result.get("error", "skill_error"),
                    message=result.get("message", "Skill 调用失败"),
                    data=result.get("data"),
                )
            return ToolResult.ok(data=result)
        except Exception as e:
            logger.exception("Skill %s.%s 调用异常", skill_name, method)
            return ToolResult.fail("skill_exception", f"{type(e).__name__}: {e}")


# 全局单例
_registry: Optional[SkillRegistry] = None


def get_registry() -> SkillRegistry:
    """获取全局 SkillRegistry 单例。"""
    global _registry
    if _registry is None:
        _registry = SkillRegistry()
    return _registry
