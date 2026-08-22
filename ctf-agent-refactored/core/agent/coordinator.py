"""
Agent 大模型推理层 —— 解题协调器。

职责：
  - 接收题目，构建 prompt 调用 LLM
  - 解析 LLM 输出的工具调用指令
  - 通过 ToolRegistry 调用工具（不直接写 HTTP）
  - 循环推理直到解题成功或超时

关键约束：
  - Agent 只调用工具注册表暴露的接口，不直接 import 平台客户端
  - 工具返回结构化 ToolResult，Agent 根据 success 字段判断下一步
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from config import Settings
from core.models import Challenge, SolveStatus, ToolResult
from core.tools import ToolRegistry

logger = logging.getLogger(__name__)


class SolveSession:
    """单次解题会话 —— 管理工具调用历史和推理循环。"""

    def __init__(self, challenge: Challenge, tools: ToolRegistry, settings: Settings) -> None:
        self.challenge = challenge
        self.tools = tools
        self.settings = settings
        self.status: SolveStatus = SolveStatus.PENDING
        self.tool_history: list[dict[str, Any]] = []
        self.flag: Optional[str] = None
        self._max_rounds = 20
        self._round = 0

    async def run(self) -> dict[str, Any]:
        """
        执行解题推理循环。

        Returns:
            {status, flag, message, tool_calls}
        """
        self.status = SolveStatus.RUNNING
        logger.info("开始解题: %s (%s)", self.challenge.name, self.challenge.id)

        # 如果题目需要容器，先启动
        if self.challenge.need_container:
            logger.info("题目需要容器，启动中...")
            result = await self.tools.call("start_container", challenge_id=self.challenge.id)
            if result.success:
                self.challenge.connection_info = result.data.get("connection_info", "")
                logger.info("容器已启动: %s", self.challenge.connection_info)
            else:
                logger.warning("容器启动失败: %s", result.message)

        # 推理循环（此处为简化版，实际项目中调用 LLM）
        try:
            while self._round < self._max_rounds:
                self._round += 1
                logger.debug("推理轮次 %d/%d", self._round, self._max_rounds)

                # TODO: 调用 LLM 生成下一步动作
                # 实际实现中，这里会调用 LLM，解析工具调用指令，
                # 然后通过 self.tools.call() 执行
                await asyncio.sleep(0.1)  # 占位

                # 简化：直接标记为需要人工介入
                self.status = SolveStatus.NEEDS_HUMAN
                break

        except Exception as e:
            logger.exception("解题异常: %s", e)
            self.status = SolveStatus.FAILED
            return {"status": self.status.value, "flag": None, "message": str(e), "tool_calls": len(self.tool_history)}

        # 解题结束，回收容器
        if self.challenge.need_container and self.settings.container_auto_recover:
            await self.tools.call("stop_container", challenge_id=self.challenge.id)

        return {
            "status": self.status.value,
            "flag": self.flag,
            "message": f"完成 {self._round} 轮推理",
            "tool_calls": len(self.tool_history),
        }

    def record_tool_call(self, tool_name: str, args: dict, result: ToolResult) -> None:
        """记录工具调用历史。"""
        self.tool_history.append({
            "tool": tool_name,
            "args": args,
            "success": result.success,
            "message": result.message,
        })


class Coordinator:
    """
    解题协调器 —— 管理多题并发解题。

    Agent 推理层的入口，负责：
      - 拉取题目列表
      - 为每道题创建 SolveSession
      - 并发控制
      - 结果汇总
    """

    def __init__(self, settings: Settings, tools: ToolRegistry) -> None:
        self.settings = settings
        self.tools = tools
        self._sessions: dict[str, SolveSession] = {}
        self._semaphore: Optional[asyncio.Semaphore] = None

    @property
    def semaphore(self) -> asyncio.Semaphore:
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self.settings.max_concurrent_challenges)
        return self._semaphore

    async def solve_challenge(self, challenge: Challenge) -> dict[str, Any]:
        """解单道题（带并发控制）。"""
        async with self.semaphore:
            session = SolveSession(challenge, self.tools, self.settings)
            self._sessions[challenge.id] = session
            result = await session.run()
            return result

    def get_session(self, challenge_id: str) -> Optional[SolveSession]:
        return self._sessions.get(challenge_id)

    def list_sessions(self) -> list[dict[str, Any]]:
        return [
            {"challenge_id": sid, "status": s.status.value, "round": s._round}
            for sid, s in self._sessions.items()
        ]
