"""后台解题执行器 —— Web / CLI 共用的真实解题入口。

- 每个 (platform_id, challenge_id) 同时只跑一个任务
- 全过程写入 core.stats.solve_log（前端终端轮询展示）
- 结束后回收沙箱容器（docker 模式）
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from config import Settings
from core.agent import Coordinator
from core.tools import build_platform_tools
from core.tools.local_tools import build_local_tools
from core.agent.solve_control import SolveControl

logger = logging.getLogger(__name__)

_tasks: dict[str, asyncio.Task] = {}
_controls: dict[str, SolveControl] = {}


def task_key(platform_id: str, challenge_id: str) -> str:
    return f"{platform_id}:{challenge_id}"


def is_running(platform_id: str, challenge_id: str) -> bool:
    task = _tasks.get(task_key(platform_id, challenge_id))
    return bool(task and not task.done())


def running_tasks() -> list[str]:
    return [key for key, task in _tasks.items() if not task.done()]


async def shutdown_tasks():
    tasks = list(_tasks.values())
    for key, task in list(_tasks.items()):
        if not task.done() and _controls[key].status != "cancelling":
            _controls[key].status = "cancelling"
            task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def reconcile_sessions():
    from core.stats import solve_log
    for session in solve_log.get_all_sessions():
        if session["status"] in ("running", "paused", "pausing", "cancelling"):
            solve_log.set_solve_status(session["platform_id"], session["challenge_id"], "interrupted")
            solve_log.append_log(session["platform_id"], session["challenge_id"], "system", "服务已重启，原任务已中断；可重新开始解题。")


def selected_settings(model: Optional[str] = None, config_snapshot=None, base_settings=None) -> Settings:
    settings = base_settings.model_copy() if base_settings is not None else Settings()
    from core.agent.model_config import list_models
    target = model or settings.llm_default_model
    choices = config_snapshot.get('models', []) if config_snapshot is not None else list_models()
    selected = next((item for item in choices if item.get("name") == target), None)
    if model or selected:
        if selected is None:
            raise ValueError(f"模型 {target} 未配置")
        settings.llm_default_model = target
        settings.llm_base_url = selected.get("base_url") or settings.llm_base_url
        settings.llm_api_key_env = selected.get("api_key_env") or ""
        settings.llm_api_key = ""
    return settings


def control_solve(platform_id: str, challenge_id: str, action: str) -> str:
    key = task_key(platform_id, challenge_id)
    task, control = _tasks.get(key), _controls.get(key)
    if not task or task.done() or not control:
        raise ValueError("该题没有运行中的解题任务")
    if action == "cancel":
        if control.status != "cancelling":
            control._status("cancelling", "正在取消解题并清理沙箱…")
            task.cancel()
    elif control.status == "cancelling":
        raise ValueError("任务正在取消，请等待清理完成")
    elif action == "pause":
        control.pause()
    elif action == "resume":
        control.resume()
    else:
        raise ValueError("不支持的操作")
    return control.status


async def _execute(platform_id: str, challenge_id: str, model: Optional[str], adapter: Any, control: SolveControl, settings=None, config_snapshot=None) -> dict[str, Any]:
    try:
        settings = settings or selected_settings(model)
        await control.checkpoint()
        challenge = await adapter.get_challenge(challenge_id)
        from core.agent.model_config import category_defaults
        from core.stats import solve_log
        chosen = model or category_defaults(challenge.category, config_snapshot, settings.llm_default_model)['default_model']
        settings = selected_settings(chosen, config_snapshot, settings)
        solve_log.set_solve_status(platform_id, challenge_id, 'running', model=chosen)
        solve_log.append_log(platform_id, challenge_id, 'system', f'方向：{challenge.category or "未分类"}；本轮模型：{chosen}')
        if platform_id == "local":
            tools = build_local_tools(challenge, settings)
            if getattr(tools, 'workspace', None):
                tools.workspace.audit_context = (platform_id, challenge_id)
        else:
            tools = build_platform_tools(adapter)
        coordinator = Coordinator(settings, tools, platform_id=platform_id, control=control)
        try:
            return await coordinator.solve_challenge(challenge)
        finally:
            cleanup = getattr(tools, "cleanup", None)
            if cleanup:
                try:
                    await cleanup()
                except Exception:
                    control.cleanup_failed = True
                    from core.stats import solve_log
                    solve_log.append_log(platform_id, challenge_id, "error", "沙箱清理失败，请检查服务日志与残留容器。")
                    logger.warning("sandbox cleanup failed", exc_info=True)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("solve task failed: %s", challenge_id)
        try:
            from core.stats import solve_log

            solve_log.append_log(platform_id, challenge_id, "error", f"解题任务异常：{type(exc).__name__}: {exc}")
            solve_log.set_solve_status(platform_id, challenge_id, "failed")
        except Exception:
            pass
        return {"status": "failed", "message": str(exc)}


def start_solve(platform_id: str, challenge_id: str, adapter: Any, model: Optional[str] = None) -> bool:
    """启动后台解题任务；若同题正在跑则返回 False。"""
    key = task_key(platform_id, challenge_id)
    if is_running(platform_id, challenge_id):
        return False
    if len(running_tasks()) >= Settings().max_concurrent_challenges:
        raise ValueError("同时解题数量已达上限，请先取消其他任务或等待完成；暂停的任务仍占用名额")
    from core.agent.model_config import snapshot
    config_snapshot = snapshot()
    settings = selected_settings(model, config_snapshot)
    control = SolveControl(platform_id, challenge_id)
    _controls[key] = control
    from core.stats import solve_log
    control.run_id = solve_log.begin_run(platform_id, challenge_id, model or '')
    task = asyncio.create_task(_execute(platform_id, challenge_id, model, adapter, control, settings, config_snapshot))
    _tasks[key] = task

    def _done(_: asyncio.Task) -> None:
        if _tasks.get(key) is not task:
            return
        _tasks.pop(key, None)
        _controls.pop(key, None)
        try:
            from core.stats import solve_log

            if task.cancelled():
                solve_log.set_solve_status(platform_id, challenge_id, "cleanup_failed" if control.cleanup_failed else "cancelled")
                solve_log.append_log(platform_id, challenge_id, "system", "解题已取消，但沙箱清理失败，请检查残留容器。" if control.cleanup_failed else "解题已取消，运行资源已清理；日志和工作区文件保留。")
            else:
                result = task.result()
                status = "success" if str(result.get("status")) == "solved" else "needs_human"
                if str(result.get("status")) in ("failed", "timeout"):
                    status = "failed"
                solve_log.set_solve_status(platform_id, challenge_id, status)
                candidates = result.get("candidates") or []
                if candidates:
                    solve_log.append_log(platform_id, challenge_id, "flag",
                                         "候选 Flag：" + " | ".join(candidates))
        except Exception:
            logger.debug("solve task finalize failed", exc_info=True)

    task.add_done_callback(_done)
    return True
