"""
题目相关路由 —— 列表、详情、下载、Flag 提交。

Web 路由只负责：接收请求、调用业务层、返回响应。
不写复杂业务逻辑，业务逻辑在 core 层。
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from core.skills import PlatformSkillAdapter
from web.deps import get_platform_adapter
from core.agent.model_config import category_defaults, snapshot
from config import Settings
from web.schemas import (
    ChallengeDetailResponse,
    ChallengeListResponse,
    SubmitFlagRequest,
    SubmitFlagResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/challenges", tags=["challenges"])


@router.get("", response_model=ChallengeListResponse)
async def list_challenges(
    light: bool = Query(default=True, description="轻量模式（不逐题补详情）"),
    category: Optional[str] = Query(default=None, description="按分类筛选"),
    keyword: Optional[str] = Query(default=None, description="按名称关键词筛选"),
    adapter: PlatformSkillAdapter = Depends(get_platform_adapter),
) -> ChallengeListResponse:
    """
    获取题目列表。

    支持按分类和关键词筛选。每条题目包含 need_container 标记，
    前端据此判断是否渲染容器控制组件。
    """
    try:
        challenges = await adapter.list_challenges(light=light)
    except Exception as e:
        logger.exception("获取题目列表失败")
        raise HTTPException(status_code=502, detail=f"平台错误: {e}") from e

    # 筛选
    if category:
        challenges = [c for c in challenges if c.category == category]
    if keyword:
        kw = keyword.lower()
        challenges = [c for c in challenges if kw in c.name.lower()]

    model_snapshot = snapshot()
    global_model = Settings().llm_default_model
    items = [
        {
            "id": c.id,
            "name": c.name,
            "category": c.category,
            "value": c.value,
            "solved": c.solved,
            "need_container": c.need_container,
            "container_status": c.container_status.value,
            "tags": c.tags,
            **category_defaults(c.category, model_snapshot, global_model),
        }
        for c in challenges
    ]
    return ChallengeListResponse(data=items, total=len(items))


@router.get("/{challenge_id}", response_model=ChallengeDetailResponse)
async def get_challenge(
    challenge_id: str,
    adapter: PlatformSkillAdapter = Depends(get_platform_adapter),
) -> ChallengeDetailResponse:
    """获取单个题目详情（含容器状态、附件列表）。"""
    try:
        ch = await adapter.get_challenge(challenge_id)
    except Exception as e:
        logger.exception("获取题目详情失败: %s", challenge_id)
        raise HTTPException(status_code=502, detail=f"平台错误: {e}") from e

    return ChallengeDetailResponse(data={
        "id": ch.id,
        "name": ch.name,
        "category": ch.category,
        "value": ch.value,
        "solved": ch.solved,
        "need_container": ch.need_container,
        "container_status": ch.container_status.value,
        "tags": ch.tags,
        "description": ch.description,
        "connection_info": ch.connection_info,
        "files": [{"name": f.name, "url": f.url, "ext": f.ext} for f in ch.files],
        **category_defaults(ch.category),
    })


@router.post("/{challenge_id}/download")
async def download_challenge(
    challenge_id: str,
    filename: str = Query(default="", description="附件文件名，为空则下载第一个"),
    adapter: PlatformSkillAdapter = Depends(get_platform_adapter),
):
    """下载题目附件。"""
    try:
        content = await adapter.download_attachment(challenge_id, filename)
    except Exception as e:
        logger.exception("下载附件失败: %s", challenge_id)
        raise HTTPException(status_code=502, detail=f"下载失败: {e}") from e

    from fastapi.responses import Response
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename or "attachment"}"'},
    )


@router.post("/{challenge_id}/submit", response_model=SubmitFlagResponse)
async def submit_flag(
    challenge_id: str,
    body: SubmitFlagRequest,
    adapter: PlatformSkillAdapter = Depends(get_platform_adapter),
) -> SubmitFlagResponse:
    """
    提交 Flag。

    成功提交后，如果 flag 验证正确，触发自动生成 WP（Writeup）。
    """
    try:
        result = await adapter.submit_flag(challenge_id, body.flag)
    except Exception as e:
        logger.exception("提交 Flag 失败: %s", challenge_id)
        raise HTTPException(status_code=502, detail=f"提交失败: {e}") from e

    data = result.to_dict()

    # Flag 验证成功 → 自动生成 WP
    if result.is_correct:
        logger.info("Flag 验证成功，自动生成 WP: %s", challenge_id)
        try:
            from core.agent.writeup import auto_generate_writeup
            wp_result = await auto_generate_writeup(challenge_id, adapter)
            data["writeup"] = wp_result
        except Exception as e:
            logger.warning("自动生成 WP 失败: %s", e)
            data["writeup_error"] = str(e)

    return SubmitFlagResponse(data=data)
