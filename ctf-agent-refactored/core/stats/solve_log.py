"""
解题实时日志管理器。
记录 Agent 解题过程中的操作日志（命令执行、思考、工具调用等），
供前端实时展示解题容器的操作过程。
数据持久化到 data/solve_logs.json。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_LOG_FILE = _DATA_DIR / "solve_logs.json"
_MAX_LOGS_PER_CHALLENGE = 200

_lock = threading.Lock()


def _load() -> dict[str, Any]:
    if not _LOG_FILE.exists():
        return {"sessions": {}}
    try:
        with open(_LOG_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("加载解题日志失败: %s", e)
        return {"sessions": {}}


def _save(data: dict[str, Any]) -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def append_log(
    platform_id: str,
    challenge_id: str,
    log_type: str,
    content: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    """追加一条解题日志。

    Args:
        platform_id: 平台 ID
        challenge_id: 题目 ID
        log_type: 日志类型（think/tool/command/output/error/flag/system）
        content: 日志内容
        metadata: 附加元数据（如命令、耗时、状态码）
    """
    with _lock:
        data = _load()
        key = f"{platform_id}:{challenge_id}"
        session = data["sessions"].setdefault(key, {
            "platform_id": platform_id,
            "challenge_id": challenge_id,
            "logs": [],
            "status": "idle",
            "started_at": None,
            "updated_at": None,
        })
        entry = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "type": log_type,
            "content": content,
            "metadata": metadata or {},
        }
        session["logs"].append(entry)
        if len(session["logs"]) > _MAX_LOGS_PER_CHALLENGE:
            session["logs"] = session["logs"][-_MAX_LOGS_PER_CHALLENGE:]
        session["updated_at"] = entry["timestamp"]
        if session["started_at"] is None:
            session["started_at"] = entry["timestamp"]
        _save(data)


def set_solve_status(platform_id: str, challenge_id: str, status: str) -> None:
    """设置解题状态（idle/running/success/failed）。"""
    with _lock:
        data = _load()
        key = f"{platform_id}:{challenge_id}"
        session = data["sessions"].setdefault(key, {
            "platform_id": platform_id,
            "challenge_id": challenge_id,
            "logs": [],
            "status": status,
            "started_at": None,
            "updated_at": None,
        })
        session["status"] = status
        session["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _save(data)


def get_solve_log(platform_id: str, challenge_id: str) -> dict[str, Any]:
    """获取单题解题日志。"""
    with _lock:
        data = _load()
    key = f"{platform_id}:{challenge_id}"
    return data["sessions"].get(key, {
        "platform_id": platform_id,
        "challenge_id": challenge_id,
        "logs": [],
        "status": "idle",
        "started_at": None,
        "updated_at": None,
    })


def get_all_sessions(platform_id: str | None = None) -> list[dict[str, Any]]:
    """获取所有解题会话（不含详细日志，仅摘要）。"""
    with _lock:
        data = _load()
    sessions = []
    for key, s in data["sessions"].items():
        if platform_id and s.get("platform_id") != platform_id:
            continue
        sessions.append({
            "platform_id": s.get("platform_id"),
            "challenge_id": s.get("challenge_id"),
            "status": s.get("status", "idle"),
            "log_count": len(s.get("logs", [])),
            "started_at": s.get("started_at"),
            "updated_at": s.get("updated_at"),
        })
    return sessions


def clear_solve_log(platform_id: str, challenge_id: str) -> None:
    """清空单题解题日志。"""
    with _lock:
        data = _load()
        key = f"{platform_id}:{challenge_id}"
        if key in data["sessions"]:
            del data["sessions"][key]
            _save(data)
