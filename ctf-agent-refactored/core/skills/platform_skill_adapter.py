"""
靶场平台 Skill 适配器。

通过 ctf-platform-skill 的 PlatformRegistry 加载平台独立文件夹中的 curl 形式 Skill。
每个平台有独立的 skill.py（直接 curl 调用）和 config.yaml（平台级配置，优先级最高）。

Agent 推理层只通过本适配器调用平台能力，不直接写 HTTP 请求。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from config import Settings
from core.models import (
    Challenge,
    ChallengeFile,
    ContainerInfo,
    ContainerStatus,
    SubmitResult,
)
from utils.exceptions import (
    ContainerError,
    InvalidParamError,
    PlatformError,
)

logger = logging.getLogger(__name__)


def _get_registry():
    """延迟导入平台注册中心（ctf-platform-skill 包）。"""
    from ctf_platform_skill.registry import PlatformRegistry
    return PlatformRegistry.get_instance()


class PlatformSkillAdapter:
    """
    靶场平台 Skill 适配器。

    通过 PlatformRegistry 加载指定平台的 curl 形式 Skill，
    提供标准化的 Challenge / ContainerInfo / SubmitResult 数据模型。
    """

    def __init__(self, settings: Settings, platform_name: Optional[str] = None):
        self.settings = settings
        self.platform_name = platform_name
        self._challenge_cache: dict[str, Challenge] = {}
        self._platform = None

    def _get_platform(self):
        """获取当前平台 Skill（懒加载）。"""
        if self._platform is None:
            registry = _get_registry()
            self._platform = registry.get_or_default(self.platform_name)
            logger.info("使用平台: %s (%s)", self._platform.name, self._platform.display_name)
        return self._platform

    def switch_platform(self, name: str) -> None:
        """切换到指定平台。"""
        registry = _get_registry()
        platform = registry.get(name)
        if platform is None:
            raise PlatformError(f"平台 {name} 不存在", code="PLATFORM_NOT_FOUND")
        self.platform_name = name
        self._platform = platform
        self._challenge_cache.clear()
        logger.info("切换到平台: %s", name)

    def list_available_platforms(self) -> list[dict[str, Any]]:
        """列出所有可用平台。"""
        return _get_registry().list_platforms()

    def _to_challenge(self, data: dict[str, Any]) -> Challenge:
        """将平台返回的 dict 转换为 Challenge 模型。"""
        return Challenge(
            id=str(data.get("id", "")),
            name=str(data.get("name", "")),
            category=str(data.get("category", "")),
            description=str(data.get("description", "")),
            value=int(data.get("value", 0)),
            tags=list(data.get("tags", [])),
            solved=bool(data.get("solved", data.get("hasSolved", False))),
            need_container=bool(data.get("need_container", data.get("isNeedInit", False))),
            container_status=ContainerStatus(data.get("container_status", "not_started")),
            connection_info=str(data.get("connection_info", "")),
            files=[
                ChallengeFile(name=f.get("name", ""), url=f.get("url", ""), ext=f.get("ext", ""))
                for f in data.get("files", [])
            ],
            raw=dict(data.get("raw", {})),
        )

    # ── 题目能力 ──

    async def list_challenges(self, light: bool = True) -> list[Challenge]:
        """获取全部题目列表。"""
        platform = self._get_platform()
        result = platform.call("list_challenges")
        if not result.get("success"):
            raise PlatformError((result.get("message") or "获取题目列表失败"), code=result.get("code", ""))

        data = result.get("data", [])
        challenges: list[Challenge] = []
        if isinstance(data, list):
            for cat in data:
                if not isinstance(cat, dict):
                    continue
                cat_name = str(cat.get("name", ""))
                corpus = cat.get("corpus", [])
                if isinstance(corpus, list) and corpus:
                    for item in corpus:
                        if not isinstance(item, dict):
                            continue
                        item["category"] = cat_name
                        challenges.append(self._to_challenge(item))
                elif "id" in cat:
                    challenges.append(self._to_challenge(cat))

        if not light:
            for ch in challenges:
                try:
                    detail = await self.get_challenge(ch.id)
                    ch.description = detail.description
                    ch.value = detail.value
                    ch.need_container = detail.need_container
                    ch.tags = detail.tags
                    ch.files = detail.files
                    ch.connection_info = detail.connection_info
                    ch.solved = ch.solved or detail.solved
                except Exception as e:
                    logger.warning("补全题目 %s 详情失败: %s", ch.id, e)

        for ch in challenges:
            self._challenge_cache[ch.id] = ch
        return challenges

    async def get_challenge(self, challenge_id: str) -> Challenge:
        """获取单个题目详情。"""
        if not challenge_id:
            raise InvalidParamError("challenge_id 不能为空", field="challenge_id")
        platform = self._get_platform()
        result = platform.call("get_challenge", challenge_id)
        if not result.get("success"):
            cached = self._challenge_cache.get(challenge_id)
            if cached:
                return cached
            raise PlatformError((result.get("message") or "获取题目详情失败"), code=result.get("code", ""))
        data = result.get("data", {})
        if isinstance(data, dict):
            data.setdefault("id", challenge_id)
        ch = self._to_challenge(data)
        self._challenge_cache[ch.id] = ch
        return ch

    async def download_attachment(self, challenge_id: str, filename: str = "") -> bytes:
        """下载题目附件，返回二进制内容。"""
        if not challenge_id:
            raise InvalidParamError("challenge_id 不能为空", field="challenge_id")
        platform = self._get_platform()
        result = platform.call("download_attachment", challenge_id, filename)
        if not result.get("success"):
            raise PlatformError((result.get("message") or "下载失败"))
        data = result.get("data", {})
        content = data.get("content", b"") if isinstance(data, dict) else b""
        if isinstance(content, str):
            content = content.encode("utf-8", errors="replace")
        return content

    # ── 容器能力 ──

    async def start_container(self, challenge_id: str) -> ContainerInfo:
        """启动题目容器环境。"""
        if not challenge_id:
            raise InvalidParamError("challenge_id 不能为空", field="challenge_id")
        platform = self._get_platform()
        result = platform.call("start_container", challenge_id)
        if not result.get("success"):
            raise ContainerError((result.get("message") or "启动容器失败"), challenge_id=challenge_id)
        data = result.get("data", {})
        if challenge_id in self._challenge_cache:
            self._challenge_cache[challenge_id].container_status = ContainerStatus.STARTING
        return ContainerInfo(
            challenge_id=challenge_id,
            status=ContainerStatus(data.get("status", "starting")),
            connection_info=data.get("connection_info", ""),
            endpoints=data.get("endpoints"),
            message=data.get("message", ""),
        )

    async def stop_container(self, challenge_id: str) -> ContainerInfo:
        """停止并回收题目容器环境。"""
        if not challenge_id:
            raise InvalidParamError("challenge_id 不能为空", field="challenge_id")
        platform = self._get_platform()
        result = platform.call("stop_container", challenge_id)
        if not result.get("success"):
            raise ContainerError((result.get("message") or "关闭容器失败"), challenge_id=challenge_id)
        if challenge_id in self._challenge_cache:
            self._challenge_cache[challenge_id].container_status = ContainerStatus.STOPPED
            self._challenge_cache[challenge_id].connection_info = ""
        data = result.get("data", {})
        return ContainerInfo(
            challenge_id=challenge_id,
            status=ContainerStatus.STOPPED,
            message=data.get("message", "容器已回收"),
        )

    async def get_container_status(self, challenge_id: str) -> ContainerInfo:
        """查询题目容器运行状态（通过题目详情接口推断）。"""
        if not challenge_id:
            raise InvalidParamError("challenge_id 不能为空", field="challenge_id")
        try:
            # 优先检查缓存：刚启动时容器处于异步启动中，避免误判为未启动
            cached = self._challenge_cache.get(challenge_id)
            if cached and cached.container_status == ContainerStatus.STARTING:
                return ContainerInfo(
                    challenge_id=challenge_id,
                    status=ContainerStatus.STARTING,
                    connection_info="",
                    endpoints=None,
                )
            ch = await self.get_challenge(challenge_id)
            raw = ch.raw or {}
            is_need_check = raw.get("isNeedCheck", False)
            endpoints = raw.get("endpoints", [])
            if is_need_check:
                status = ContainerStatus.STARTING
                connection_info = ""
            elif endpoints:
                status = ContainerStatus.RUNNING
                ep = endpoints[0] if isinstance(endpoints, list) else {}
                if isinstance(ep, dict):
                    host = ep.get("host", "localhost")
                    port = ep.get("port", "")
                    path = ep.get("path", "")
                    connection_info = f"http://{host}:{port}{path}" if port else str(ep)
                else:
                    connection_info = str(ep)
            else:
                status = ContainerStatus.NOT_STARTED
                connection_info = ""
            return ContainerInfo(
                challenge_id=challenge_id,
                status=status,
                connection_info=connection_info,
                endpoints=endpoints if isinstance(endpoints, list) else None,
            )
        except Exception as e:
            raise ContainerError(f"查询容器状态失败: {e}", challenge_id=challenge_id)

    # ── Flag 提交 ──

    async def submit_flag(self, challenge_id: str, flag: str) -> SubmitResult:
        """提交 Flag。"""
        if not challenge_id:
            raise InvalidParamError("challenge_id 不能为空", field="challenge_id")
        if not flag or not flag.strip():
            raise InvalidParamError("flag 不能为空", field="flag")
        platform = self._get_platform()
        result = platform.call("submit_flag", challenge_id, flag)
        if not result.get("success"):
            return SubmitResult(status="unknown", message=(result.get("message") or "提交失败"), is_correct=False)
        data = result.get("data", {})
        is_correct = bool(data.get("is_correct", data.get("isCorrect", False)))
        return SubmitResult(
            status="correct" if is_correct else "incorrect",
            message=data.get("message", ""),
            is_correct=is_correct,
            flag=data.get("flag", flag),
        )

    async def close(self) -> None:
        """关闭（curl 形式无持久连接，无需特殊处理）。"""
        pass
