"""
Web 层 Pydantic 请求/响应 Schema。
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


# ── 题目 ─────────────────────────────────────────────────────────────────────

class ChallengeListItem(BaseModel):
    """题目列表项。"""
    id: str
    name: str
    category: str = ""
    value: int = 0
    solved: bool = False
    need_container: bool = False
    container_status: str = "not_started"
    tags: list[str] = Field(default_factory=list)
    default_model: str = ''
    configured_default_model: Optional[str] = None
    model_source: str = 'global'
    model_category: str = ''


class ChallengeDetail(ChallengeListItem):
    """题目详情。"""
    description: str = ""
    connection_info: str = ""
    files: list[dict[str, str]] = Field(default_factory=list)


class ChallengeListResponse(BaseModel):
    """题目列表响应。"""
    success: bool = True
    data: list[ChallengeListItem]
    total: int = 0


class ChallengeDetailResponse(BaseModel):
    """题目详情响应。"""
    success: bool = True
    data: Optional[ChallengeDetail] = None


# ── 容器 ─────────────────────────────────────────────────────────────────────

class ContainerActionRequest(BaseModel):
    """容器操作请求（开启/关闭）。"""
    challenge_id: str = Field(..., description="题目 ID")


class ContainerStatusResponse(BaseModel):
    """容器状态响应。"""
    success: bool = True
    data: dict[str, Any]
    # data 字段：
    # {
    #   "challenge_id": "123",
    #   "need_container": true,
    #   "status": "running",          # not_started/starting/running/stopped/failed
    #   "connection_info": "host:port",
    #   "is_need_check": false
    # }


# ── Flag 提交 ────────────────────────────────────────────────────────────────

class SubmitFlagRequest(BaseModel):
    """Flag 提交请求。"""
    challenge_id: str
    flag: str


class SubmitFlagResponse(BaseModel):
    """Flag 提交响应。"""
    success: bool = True
    data: dict[str, Any]
    # data: {"status": "correct"/"incorrect", "is_correct": true/false, "message": "...", "flag": "..."}


# ── 下载 ─────────────────────────────────────────────────────────────────────

class DownloadRequest(BaseModel):
    """附件下载请求。"""
    challenge_id: str
    filename: str = ""


# ── 通用错误响应 ─────────────────────────────────────────────────────────────

class ErrorResponse(BaseModel):
    """统一错误响应。"""
    success: bool = False
    error: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
