"""
Token 使用统计管理器。
记录每道题目的 token 消耗（命中/未命中），以及全局总计。
数据持久化到 data/token_stats.json。
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_STATS_FILE = _DATA_DIR / "token_stats.json"

_lock = threading.Lock()


def _load() -> dict[str, Any]:
    if not _STATS_FILE.exists():
        return {"challenges": {}, "total": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "hit": 0, "miss": 0, "calls": 0}}
    try:
        with open(_STATS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("加载 token 统计失败: %s", e)
        return {"challenges": {}, "total": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "hit": 0, "miss": 0, "calls": 0}}


def _save(data: dict[str, Any]) -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(_STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def record_token_usage(
    platform_id: str,
    challenge_id: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    is_hit: bool = False,
    model: str = "",
) -> None:
    """记录一次 LLM 调用的 token 消耗。

    Args:
        platform_id: 平台 ID
        challenge_id: 题目 ID
        prompt_tokens: 提示 token 数
        completion_tokens: 补全 token 数
        is_hit: 是否命中（解题成功/Flag 正确）
        model: 使用的模型名
    """
    with _lock:
        data = _load()
        key = f"{platform_id}:{challenge_id}"
        ch = data["challenges"].setdefault(key, {
            "platform_id": platform_id,
            "challenge_id": challenge_id,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "hit": 0,
            "miss": 0,
            "calls": 0,
            "model": model,
        })
        total = prompt_tokens + completion_tokens
        ch["prompt_tokens"] += prompt_tokens
        ch["completion_tokens"] += completion_tokens
        ch["total_tokens"] += total
        ch["calls"] += 1
        if is_hit:
            ch["hit"] += 1
        else:
            ch["miss"] += 1
        if model:
            ch["model"] = model

        data["total"]["prompt_tokens"] += prompt_tokens
        data["total"]["completion_tokens"] += completion_tokens
        data["total"]["total_tokens"] += total
        data["total"]["calls"] += 1
        if is_hit:
            data["total"]["hit"] += 1
        else:
            data["total"]["miss"] += 1

        _save(data)


def get_stats(platform_id: str | None = None) -> dict[str, Any]:
    """获取 token 统计。

    Args:
        platform_id: 可选，按平台过滤

    Returns:
        {total: {...}, challenges: [...]}
    """
    with _lock:
        data = _load()
    challenges = list(data["challenges"].values())
    if platform_id:
        challenges = [c for c in challenges if c.get("platform_id") == platform_id]
    return {"total": data["total"], "challenges": challenges}


def get_challenge_stats(platform_id: str, challenge_id: str) -> dict[str, Any]:
    """获取单题 token 统计。"""
    with _lock:
        data = _load()
    key = f"{platform_id}:{challenge_id}"
    return data["challenges"].get(key, {
        "platform_id": platform_id,
        "challenge_id": challenge_id,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "hit": 0,
        "miss": 0,
        "calls": 0,
    })


def reset_stats() -> None:
    """清空所有统计。"""
    with _lock:
        _save({"challenges": {}, "total": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "hit": 0, "miss": 0, "calls": 0}})
