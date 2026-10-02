"""LocalAdapter —— 把本地题库目录（0xgame 布局）伪装成一个「平台」。

接口与 PlatformSkillAdapter 保持一致，因此 web 层的所有既有路由
（/api/challenges、/api/containers、/api/solve-log ...）在
X-Platform-Id: local 时可以直接复用。

判题：本地题库没有自动判题接口，submit_flag 永远返回未验证，
Flag 由用户拿到候选后自行提交（比赛平台）。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from config import Settings
from core.agent.local_challenges import load_local_challenges
from core.models import Challenge, ContainerInfo, ContainerStatus, SubmitResult

logger = logging.getLogger(__name__)


class LocalAdapter:
    """本地题库适配器（只读题目文件 + 目录内附件下载）。"""

    platform_id = "local"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.root = Path(settings.local_root).expanduser()
        self._cache: dict[str, Challenge] = {}
        self._loaded = False

    # ---- 内部 ----
    def _load(self) -> dict[str, Challenge]:
        if not self._loaded:
            try:
                challenges = load_local_challenges(str(self.root))
            except Exception as exc:
                logger.error("加载本地题库失败（%s）: %s", self.root, exc)
                challenges = []
            self._cache = {c.id: c for c in challenges}
            self._loaded = True
        return self._cache

    # ---- 平台接口 ----
    async def list_challenges(self, light: bool = True) -> list[Challenge]:
        challenges = list(self._load().values())
        return sorted(challenges, key=lambda c: (c.category, c.name))

    async def get_challenge(self, challenge_id: str) -> Challenge:
        challenge = self._load().get(str(challenge_id))
        if challenge is None:
            raise KeyError(f"本地题库中找不到题目: {challenge_id}")
        return challenge

    async def get_container_status(self, challenge_id: str) -> ContainerInfo:
        return ContainerInfo(challenge_id=challenge_id, status=ContainerStatus.NOT_STARTED,
                             message="本地题库无平台容器（使用沙箱容器执行命令）")

    async def start_container(self, challenge_id: str) -> ContainerInfo:
        raise RuntimeError("本地题库不启动比赛容器（按用户要求不开启题目环境）")

    async def stop_container(self, challenge_id: str) -> ContainerInfo:
        return ContainerInfo(challenge_id=challenge_id, status=ContainerStatus.NOT_STARTED,
                             message="本地题库无平台容器")

    async def submit_flag(self, challenge_id: str, flag: str) -> SubmitResult:
        return SubmitResult(status="unknown", is_correct=False, flag=flag or "",
                            message="本地题库无判题接口：flag 仅作为候选输出，需人工到比赛平台提交")

    async def download_attachment(self, challenge_id: str, filename: str = "") -> bytes:
        challenge = await self.get_challenge(challenge_id)
        base = Path(challenge.raw.get("local_dir", "")).resolve()
        candidates = [f for f in challenge.files if not filename or f.name == filename]
        if not candidates:
            raise FileNotFoundError(f"题目 {challenge_id} 没有附件 {filename!r}")
        name = candidates[0].name
        search = [base / name, *sorted(base.rglob(name))]
        for path in search:
            if path.is_file() and path.resolve().is_relative_to(base):
                return path.read_bytes()
        raise FileNotFoundError(f"附件不存在: {name}")

    async def close(self) -> None:
        return None

    # ---- 辅助 ----
    def describe(self) -> dict[str, Any]:
        data = self._load()
        return {"root": str(self.root), "count": len(data),
                "categories": sorted({c.category for c in data.values()})}
