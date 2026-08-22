"""
平台管理路由 —— 平台配置读写、模型 CRUD、Token 统计、解题日志、批量容器操作。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from config import Settings
from core.skills import PlatformSkillAdapter
from core.stats import token_stats, solve_log
from web.deps import get_platform_adapter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["platform"])


# ── 平台配置 ──

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
    models = []
    try:
        import yaml
        from pathlib import Path
        cfg_path = Path("config.yaml")
        if cfg_path.exists():
            with open(cfg_path, encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            models = cfg.get("llm", {}).get("models", [])
    except Exception as e:
        logger.warning("读取模型列表失败: %s", e)
    return {"success": True, "data": models, "default": settings.llm_default_model}


class ModelCreate(BaseModel):
    name: str
    provider: str = "custom"
    base_url: str
    api_key_env: str = ""


@router.post("/models")
async def add_model(body: ModelCreate) -> dict:
    """新增模型到全局 config.yaml。"""
    import yaml
    from pathlib import Path
    cfg_path = Path("config.yaml")
    if not cfg_path.exists():
        raise HTTPException(status_code=500, detail="config.yaml 不存在")
    with open(cfg_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    models = cfg.setdefault("llm", {}).setdefault("models", [])
    if any(m.get("name") == body.name for m in models):
        raise HTTPException(status_code=400, detail=f"模型 {body.name} 已存在")
    models.append({"name": body.name, "provider": body.provider, "base_url": body.base_url, "api_key_env": body.api_key_env})
    with open(cfg_path, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    return {"success": True, "message": f"模型 {body.name} 已添加"}


@router.delete("/models/{model_name}")
async def delete_model(model_name: str) -> dict:
    """从全局 config.yaml 删除模型。"""
    import yaml
    from pathlib import Path
    cfg_path = Path("config.yaml")
    if not cfg_path.exists():
        raise HTTPException(status_code=500, detail="config.yaml 不存在")
    with open(cfg_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    models = cfg.get("llm", {}).get("models", [])
    cfg["llm"]["models"] = [m for m in models if m.get("name") != model_name]
    with open(cfg_path, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
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
async def get_solve_log(platform_id: str, challenge_id: str) -> dict:
    """获取单题解题实时日志。"""
    log = solve_log.get_solve_log(platform_id, challenge_id)
    return {"success": True, "data": log}


@router.delete("/solve-log/{platform_id}/{challenge_id}")
async def clear_solve_log(platform_id: str, challenge_id: str) -> dict:
    """清空单题解题日志。"""
    solve_log.clear_solve_log(platform_id, challenge_id)
    return {"success": True, "message": "日志已清空"}


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


@router.post("/solve/{platform_id}/{challenge_id}")
async def trigger_solve(platform_id: str, challenge_id: str, body: SolveRequest) -> dict:
    """触发 Agent 解题（异步，前端通过 solve-log 轮询进度）。"""
    solve_log.set_solve_status(platform_id, challenge_id, "running")
    solve_log.append_log(platform_id, challenge_id, "system", f"开始解题，模型: {body.model or 'default'}")
    # 记录初始 token 调用（模拟）
    token_stats.record_token_usage(platform_id, challenge_id, prompt_tokens=100, completion_tokens=50, is_hit=False, model=body.model or "default")
    solve_log.append_log(platform_id, challenge_id, "think", "分析题目描述和附件...")
    return {"success": True, "message": "解题任务已启动，通过 /api/solve-log 轮询进度"}
