"""
容器控制路由 —— 开启/关闭/查询题目容器环境。

前端容器 UI 组件对接这些接口。
严格按 need_container 条件：非容器题目不渲染容器控件。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from core.skills import PlatformSkillAdapter
from web.deps import get_platform_adapter
from web.schemas import ContainerStatusResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/containers", tags=["containers"])


@router.get("/{challenge_id}", response_model=ContainerStatusResponse)
async def get_container_status(
    challenge_id: str,
    adapter: PlatformSkillAdapter = Depends(get_platform_adapter),
) -> ContainerStatusResponse:
    """
    查询题目容器运行状态。

    返回状态：not_started / starting / running / stopped / failed
    前端据此渲染状态标签和按钮可用性。
    """
    try:
        # 先查题目，判断是否需要容器
        challenge = await adapter.get_challenge(challenge_id)
        if not challenge.need_container:
            return ContainerStatusResponse(data={
                "challenge_id": challenge_id,
                "need_container": False,
                "status": "not_started",
                "connection_info": "",
                "is_need_check": False,
                "message": "该题目不需要容器环境",
            })

        info = await adapter.get_container_status(challenge_id)
        return ContainerStatusResponse(data={
            "challenge_id": challenge_id,
            "need_container": True,
            "status": info.status.value,
            "connection_info": info.connection_info,
            "is_need_check": False,
            "endpoints": info.endpoints,
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("查询容器状态失败: %s", challenge_id)
        raise HTTPException(status_code=502, detail=f"查询失败: {e}") from e


@router.post("/{challenge_id}/start", response_model=ContainerStatusResponse)
async def start_container(
    challenge_id: str,
    adapter: PlatformSkillAdapter = Depends(get_platform_adapter),
) -> ContainerStatusResponse:
    """
    开启题目容器环境。

    异步操作：内部轮询直到容器就绪或超时。
    成功后返回 connection_info（容器访问地址）。
    """
    try:
        challenge = await adapter.get_challenge(challenge_id)
        if not challenge.need_container:
            raise HTTPException(status_code=400, detail="该题目不需要容器环境")

        info = await adapter.start_container(challenge_id)
        return ContainerStatusResponse(data={
            "challenge_id": challenge_id,
            "need_container": True,
            "status": info.status.value,
            "connection_info": info.connection_info,
            "is_need_check": False,
            "endpoints": info.endpoints,
            "message": info.message,
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("启动容器失败: %s", challenge_id)
        raise HTTPException(status_code=502, detail=f"启动失败: {e}") from e


@router.post("/{challenge_id}/stop", response_model=ContainerStatusResponse)
async def stop_container(
    challenge_id: str,
    adapter: PlatformSkillAdapter = Depends(get_platform_adapter),
) -> ContainerStatusResponse:
    """
    关闭并回收题目容器环境。
    """
    try:
        challenge = await adapter.get_challenge(challenge_id)
        if not challenge.need_container:
            raise HTTPException(status_code=400, detail="该题目不需要容器环境")

        info = await adapter.stop_container(challenge_id)
        return ContainerStatusResponse(data={
            "challenge_id": challenge_id,
            "need_container": True,
            "status": info.status.value,
            "connection_info": "",
            "is_need_check": False,
            "message": info.message,
        })
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("停止容器失败: %s", challenge_id)
        raise HTTPException(status_code=502, detail=f"停止失败: {e}") from e
