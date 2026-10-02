"""
平台管理路由 —— 平台配置读写、模型 CRUD、Token 统计、解题日志、批量容器操作。
"""
from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path
from typing import Any, Optional, Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from config import Settings
from core.skills import PlatformSkillAdapter
from core.stats import token_stats, solve_log
from web.deps import get_platform_adapter, get_platform_id
from core.agent import model_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["platform"])


# ── 平台配置 ──

class PlatformCreate(BaseModel):
    id: str
    name: str
    api_base_url: str
    access_key_env: str = "CTFD_TOKEN"


@router.post("/platforms")
async def create_platform(body: PlatformCreate) -> dict:
    """Register a CTFd instance from the bundled adapter template."""
    from ctf_platform_skill.registry import PLATFORMS_DIR, PlatformRegistry
    platform_id = body.id.strip().lower()
    url = body.api_base_url.strip().rstrip("/")
    parsed = urlparse(url)
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,31}", platform_id):
        raise HTTPException(400, "平台 ID 只能使用 2-32 位小写字母、数字和连字符")
    if not body.name.strip() or len(body.name) > 80:
        raise HTTPException(400, "平台名称不能为空且不能超过 80 字")
    if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username or parsed.password:
        raise HTTPException(400, "请输入有效的 HTTP(S) 平台地址")
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,63}", body.access_key_env):
        raise HTTPException(400, "密钥环境变量名格式无效")
    registry = PlatformRegistry.get_instance()
    folder = PLATFORMS_DIR / platform_id
    if folder.exists() or registry.get(platform_id):
        raise HTTPException(409, "平台 ID 已存在")
    template = PLATFORMS_DIR / "ctfd" / "skill.py"
    if not template.is_file():
        raise HTTPException(500, "CTFd 模板不存在")
    import yaml
    try:
        folder.mkdir()
        shutil.copyfile(template, folder / "skill.py")
        config = {
            "api_base_url": url, "api_path": "/api/v1",
            "access_key_env": body.access_key_env, "timeout": 30,
            "meta": {"name": body.name.strip(), "icon": "🏁", "description": url, "type": "ctfd"},
            "auto_solve": {"enabled": False, "auto_download": True,
                           "auto_start_container": False, "batch_start_unsolved": False},
        }
        (folder / "config.yaml").write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
        registry.reload()
        if registry.get(platform_id) is None:
            raise RuntimeError("平台加载失败")
    except Exception as exc:
        shutil.rmtree(folder, ignore_errors=True)
        registry.reload()
        raise HTTPException(500, f"平台注册失败: {exc}") from exc
    return {"success": True, "data": {"id": platform_id, "name": body.name.strip(), "access_key_env": body.access_key_env}}

@router.get("/platforms/{platform_id}/config")
async def get_platform_config(platform_id: str) -> dict:
    """获取平台 config.yaml 完整内容。"""
    from ctf_platform_skill.registry import PlatformRegistry
    reg = PlatformRegistry.get_instance()
    config = reg.get_platform_config(platform_id)
    if config is None:
        raise HTTPException(status_code=404, detail=f"平台 {platform_id} 不存在")
    return {"success": True, "data": config}


class PlatformConfigUpdate(BaseModel):
    config: dict[str, Any]


@router.put("/platforms/{platform_id}/config")
async def update_platform_config(platform_id: str, body: PlatformConfigUpdate) -> dict:
    """更新平台 config.yaml（前端修改 auto_solve 等设置后调用）。"""
    from ctf_platform_skill.registry import PlatformRegistry
    reg = PlatformRegistry.get_instance()
    if reg.get(platform_id) is None:
        raise HTTPException(status_code=404, detail=f"平台 {platform_id} 不存在")
    ok = reg.save_platform_config(platform_id, body.config)
    if not ok:
        raise HTTPException(status_code=500, detail="保存配置失败")
    return {"success": True, "message": "配置已保存"}


# ── 模型管理 ──

@router.get("/models")
async def list_models() -> dict:
    """列出全局 config.yaml 中配置的所有模型。"""
    settings = Settings()
    models = model_config.list_models()
    return {"success": True, "data": models, "default": settings.llm_default_model}


class ModelCreate(BaseModel):
    name: str
    provider: str = "custom"
    base_url: str
    api_key_env: str = ""


@router.post("/models")
async def add_model(body: ModelCreate) -> dict:
    """新增模型到全局 config.yaml。"""
    try:
        item = model_config.save_model(body.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"success": True, "data": item, "message": "模型已添加"}


@router.put('/models/{model_name:path}')
async def update_model(model_name: str, body: ModelCreate) -> dict:
    try:
        item = model_config.save_model(body.model_dump(), original_name=model_name)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {'success': True, 'data': item, 'message': '模型已修改'}


class CategoryModelUpdate(BaseModel):
    model: Optional[str] = None


@router.get('/category-models')
async def list_category_models(adapter: PlatformSkillAdapter = Depends(get_platform_adapter)):
    challenges = await adapter.list_challenges(light=True)
    return {'success': True, 'data': model_config.list_category_defaults([c.category for c in challenges]),
            'default': Settings().llm_default_model}


@router.put('/category-models/{category:path}')
async def update_category_model(category: str, body: CategoryModelUpdate):
    try:
        data = model_config.set_category_default(category, body.model)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {'success': True, 'data': {'category': model_config.category_name(category), **data}, 'message': '方向默认模型已保存'}


@router.delete("/models/{model_name:path}")
async def delete_model(model_name: str) -> dict:
    """从全局 config.yaml 删除模型。"""
    try:
        model_config.delete_model(model_name)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"success": True, "message": f"模型 {model_name} 已删除"}


# ── Token 统计 ──

@router.get("/token-stats")
async def get_token_stats(platform_id: Optional[str] = Query(default=None)) -> dict:
    """获取 Token 使用统计（全局总计 + 每题目明细）。"""
    stats = token_stats.get_stats(platform_id)
    return {"success": True, "data": stats}


@router.get("/token-stats/{platform_id}/{challenge_id}")
async def get_challenge_token_stats(platform_id: str, challenge_id: str) -> dict:
    """获取单题 Token 统计。"""
    stats = token_stats.get_challenge_stats(platform_id, challenge_id)
    return {"success": True, "data": stats}


@router.delete("/token-stats")
async def reset_token_stats() -> dict:
    """清空所有 Token 统计。"""
    token_stats.reset_stats()
    return {"success": True, "message": "统计已清空"}


# ── 解题日志 ──

@router.get("/solve-log/{platform_id}/{challenge_id}")
async def get_solve_log(platform_id: str, challenge_id: str,
                        limit: int = Query(default=200, ge=1, le=2000),
                        kind: Literal['solve', 'agent'] = Query(default='solve')) -> dict:
    """获取单题解题实时日志。"""
    getter = solve_log.get_agent_log if kind == 'agent' else solve_log.get_solve_log
    log = getter(platform_id, challenge_id, limit=limit)
    return {"success": True, "data": log}


@router.get('/solve-log/{platform_id}/{challenge_id}/download')
async def download_solve_log(platform_id: str, challenge_id: str,
                             kind: Literal['solve', 'agent'] = Query(default='solve')):
    return StreamingResponse(solve_log.iter_journal(platform_id, challenge_id, kind),
        media_type='application/x-ndjson', headers={'Content-Disposition': f'attachment; filename="{kind}-log.jsonl"'})


@router.delete("/solve-log/{platform_id}/{challenge_id}")
async def clear_solve_log(platform_id: str, challenge_id: str) -> dict:
    """清空单题解题日志。"""
    from core.agent.solve_runner import is_running
    if is_running(platform_id, challenge_id):
        raise HTTPException(status_code=409, detail="请先取消解题，待清理完成后再清空日志")
    solve_log.clear_solve_log(platform_id, challenge_id)
    return {"success": True, "message": "显示已清空，完整日志保留"}


@router.get("/solve-sessions")
async def list_solve_sessions(platform_id: Optional[str] = Query(default=None)) -> dict:
    """列出所有解题会话摘要。"""
    sessions = solve_log.get_all_sessions(platform_id)
    return {"success": True, "data": sessions}


# ── 批量容器操作 ──

@router.post("/platforms/{platform_id}/start-unsolved-containers")
async def start_unsolved_containers(
    platform_id: str,
    adapter: PlatformSkillAdapter = Depends(get_platform_adapter),
) -> dict:
    """一键启动当前平台所有未解出且需要容器的题目容器。"""
    try:
        challenges = await adapter.list_challenges(light=False)
        targets = [c for c in challenges if c.need_container and not c.solved]
        results = []
        for ch in targets:
            try:
                info = await adapter.start_container(ch.id)
                results.append({"id": ch.id, "name": ch.name, "status": info.status.value, "success": True})
            except Exception as e:
                results.append({"id": ch.id, "name": ch.name, "status": "failed", "success": False, "error": str(e)})
        return {"success": True, "data": {"started": len([r for r in results if r["success"]]), "total": len(targets), "results": results}}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"批量启动失败: {e}") from e


# ── 触发解题 ──

class SolveRequest(BaseModel):
    challenge_id: str
    model: Optional[str] = None


@router.post("/models/check")
async def check_model(body: SolveRequest, request: Request) -> dict:
    from core.agent.solve_runner import selected_settings
    from core.agent.local_llm import LocalModel
    try:
        selected = body.model
        if not selected:
            from web.deps import get_adapter_by_id
            challenge = await get_adapter_by_id(get_platform_id(request)).get_challenge(body.challenge_id)
            selected = model_config.category_defaults(challenge.category)['default_model']
        model = LocalModel(selected_settings(selected))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        return {"success": True, "data": await model.probe()}
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        await model.aclose()


@router.post("/solve/{platform_id}/{challenge_id}/{operation}")
async def control_task(platform_id: str, challenge_id: str, operation: str) -> dict:
    from core.agent.solve_runner import control_solve
    if operation not in ("pause", "resume", "cancel"):
        raise HTTPException(status_code=400, detail="操作须为 pause、resume 或 cancel")
    try:
        status = control_solve(platform_id, challenge_id, operation)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"success": True, "data": {"status": status}}


@router.post("/solve/{platform_id}/{challenge_id}")
async def trigger_solve(platform_id: str, challenge_id: str, body: SolveRequest) -> dict:
    """触发 Agent 真实解题（后台任务，前端通过 solve-log 轮询进度）。"""
    from core.agent.solve_runner import is_running, start_solve
    from web.deps import get_adapter_by_id

    try:
        adapter = get_adapter_by_id(platform_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"未知平台 {platform_id}: {exc}") from exc
    if is_running(platform_id, challenge_id):
        return {"success": True, "data": {"started": False}, "message": "该题正在解题中"}
    try:
        started = start_solve(platform_id, challenge_id, adapter, model=body.model)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"success": True, "data": {"started": started},
            "message": "解题任务已启动，通过 /api/solve-log 轮询进度" if started else "该题正在解题中"}


@router.get("/local/info")
async def local_info() -> dict:
    """本地题库概况（根目录 / 题目数 / 分类）。"""
    from web.deps import get_adapter_by_id

    try:
        adapter = get_adapter_by_id("local")
        return {"success": True, "data": adapter.describe()}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"本地题库不可用: {exc}") from exc
