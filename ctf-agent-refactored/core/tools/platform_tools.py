"""
工具调用层 —— Agent 可调用的工具集。

每个工具是一个独立函数，接收结构化参数，返回 ToolResult。
工具内部只调用 Skill 适配器，不直接写 HTTP 请求。

Agent 推理层通过工具注册表调用这些工具，实现「推理 ↔ 工具」解耦。
"""

from __future__ import annotations

import logging
import base64
from typing import Any, Callable, Optional

from core.models import ToolResult
from core.skills import PlatformSkillAdapter

logger = logging.getLogger(__name__)


class ToolRegistry:
    """工具注册表 —— Agent 通过名称调用工具。"""

    def __init__(self) -> None:
        self._tools: dict[str, Callable[..., Any]] = {}
        self._descriptions: dict[str, str] = {}

    def register(self, name: str, func: Callable[..., Any], description: str = "") -> None:
        self._tools[name] = func
        self._descriptions[name] = description

    def get(self, name: str) -> Optional[Callable[..., Any]]:
        return self._tools.get(name)

    def list_tools(self) -> list[dict[str, str]]:
        return [{"name": n, "description": d} for n, d in self._descriptions.items()]

    async def call(self, name: str, **kwargs: Any) -> ToolResult:
        func = self._tools.get(name)
        if func is None:
            return ToolResult.fail("tool_not_found", f"工具 '{name}' 未注册")
        try:
            return await func(**kwargs)
        except Exception as e:
            logger.exception("Tool %s 执行异常", name)
            return ToolResult.fail("tool_exception", f"{type(e).__name__}: {e}")


def build_platform_tools(adapter: PlatformSkillAdapter) -> ToolRegistry:
    """
    基于平台 Skill 适配器构建 Agent 可用的工具集。

    Agent 只看到这些工具名和描述，不感知底层 HTTP 实现。
    """
    registry = ToolRegistry()

    async def tool_list_challenges(light: bool = True) -> ToolResult:
        """获取题目列表。参数：light(bool)=是否轻量模式。"""
        try:
            challenges = await adapter.list_challenges(light=light)
            return ToolResult.ok(data=[c.to_dict() for c in challenges])
        except Exception as e:
            return ToolResult.fail("platform_error", str(e))

    async def tool_get_challenge(challenge_id: str) -> ToolResult:
        """获取题目详情。参数：challenge_id(str)。"""
        try:
            ch = await adapter.get_challenge(challenge_id)
            return ToolResult.ok(data=ch.to_dict())
        except Exception as e:
            return ToolResult.fail("platform_error", str(e))

    async def tool_start_container(challenge_id: str) -> ToolResult:
        """启动题目容器环境。参数：challenge_id(str)。"""
        try:
            info = await adapter.start_container(challenge_id)
            return ToolResult.ok(data=info.to_dict())
        except Exception as e:
            return ToolResult.fail("container_error", str(e))

    async def tool_stop_container(challenge_id: str) -> ToolResult:
        """停止题目容器环境。参数：challenge_id(str)。"""
        try:
            info = await adapter.stop_container(challenge_id)
            return ToolResult.ok(data=info.to_dict())
        except Exception as e:
            return ToolResult.fail("container_error", str(e))

    async def tool_get_container_status(challenge_id: str) -> ToolResult:
        """查询容器状态。参数：challenge_id(str)。"""
        try:
            info = await adapter.get_container_status(challenge_id)
            return ToolResult.ok(data=info.to_dict())
        except Exception as e:
            return ToolResult.fail("container_error", str(e))

    async def tool_submit_flag(challenge_id: str, flag: str) -> ToolResult:
        """提交 Flag。参数：challenge_id(str), flag(str)。"""
        try:
            result = await adapter.submit_flag(challenge_id, flag)
            return ToolResult.ok(data=result.to_dict())
        except Exception as e:
            return ToolResult.fail("submit_error", str(e))

    async def tool_download_attachment(challenge_id: str, filename: str = "") -> ToolResult:
        """下载题目附件。参数：challenge_id(str), filename(str)。"""
        try:
            content = await adapter.download_attachment(challenge_id, filename)
            if len(content) > 65536:
                return ToolResult.fail("attachment_too_large", "Attachment exceeds 64 KiB model transfer limit", data={"size": len(content)})
            if b"\x00" in content:
                preview = {"encoding": "base64", "content": base64.b64encode(content).decode("ascii")}
            else:
                preview = {"encoding": "utf-8", "content": content.decode("utf-8", errors="replace")}
            return ToolResult.ok(data={"size": len(content), "filename": filename or "attachment", **preview})
        except Exception as e:
            return ToolResult.fail("download_error", str(e))

    registry.register("list_challenges", tool_list_challenges, "获取全部题目列表")
    registry.register("get_challenge", tool_get_challenge, "获取单个题目详情")
    registry.register("start_container", tool_start_container, "启动题目容器环境")
    registry.register("stop_container", tool_stop_container, "停止题目容器环境")
    registry.register("get_container_status", tool_get_container_status, "查询容器运行状态")
    registry.register("submit_flag", tool_submit_flag, "提交 Flag")
    registry.register("download_attachment", tool_download_attachment, "下载题目附件")

    return registry
