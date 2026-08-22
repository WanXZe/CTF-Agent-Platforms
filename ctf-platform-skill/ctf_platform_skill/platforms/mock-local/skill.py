"""
本地 Mock 靶场平台 Skill —— 直接 curl 调用形式。

与 slab-match 协议完全一致，指向本地 Mock 服务器。
用于开发调试，无需真实靶场平台。

等价 curl 示例：
  curl -s -X GET "http://localhost:8080/slab-match/api/v1/agent/ctf/exercise-list" \
    -H "X-Agent-AccessKey: test-key-123"
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Optional

import yaml

_CONFIG_PATH = Path(__file__).parent / "config.yaml"


def _load_config() -> dict[str, Any]:
    cfg: dict[str, Any] = {}
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    cfg.setdefault("api_base_url", os.environ.get("MOCK_API_BASE", "http://localhost:8080"))
    cfg.setdefault("api_path", os.environ.get("MOCK_API_PATH", "/slab-match/api/v1/agent"))
    _env_key = cfg.get("access_key_env", "MOCK_ACCESS_KEY")
    cfg.setdefault("access_key", os.environ.get(_env_key, "test-key-123"))
    cfg.setdefault("timeout", int(os.environ.get("MOCK_TIMEOUT", "30")))
    return cfg


def _curl(method: str, path: str, *, params: Optional[dict[str, Any]] = None, json_data: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    cfg = _load_config()
    base = cfg["api_base_url"].rstrip("/")
    url = f"{base}{cfg['api_path']}{path}"
    cmd = ["curl", "-s", "-X", method, url]
    cmd += ["-H", f"X-Agent-AccessKey: {cfg['access_key']}"]
    cmd += ["-H", "Content-Type: application/json"]
    cmd += ["--max-time", str(cfg.get("timeout", 30))]
    if method == "GET" and params:
        cmd += ["-G"]
        for k, v in params.items():
            cmd += ["--data-urlencode", f"{k}={v}"]
    if json_data is not None:
        cmd += ["-d", json.dumps(json_data, ensure_ascii=False)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=cfg.get("timeout", 30) + 5)
        if result.returncode != 0:
            return {"success": False, "error": "curl_error", "message": (result.stderr or "").strip() or f"curl exit {result.returncode}", "data": None}
        if not (result.stdout or "").strip():
            return {"success": False, "error": "empty_response", "message": "平台返回空响应", "data": None}
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        return {"success": False, "error": "invalid_json", "message": f"非JSON: {str(e)[:200]}", "data": None}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "timeout", "message": "请求超时", "data": None}
    except Exception as e:
        return {"success": False, "error": "unknown", "message": str(e), "data": None}
    code = str(payload.get("code", ""))
    message = str(payload.get("message") or payload.get("msg") or "")
    data = payload.get("data", payload)
    success = code == "00000"
    return {"success": success, "code": code, "message": message if success else (message or f"错误码 {code}"), "data": data, "error": None if success else "platform_error"}


def list_challenges() -> dict[str, Any]:
    """获取题目列表。"""
    return _curl("GET", "/ctf/exercise-list")

def get_challenge(exerciseId: str) -> dict[str, Any]:
    """获取题目详情。"""
    return _curl("GET", "/ctf/exercise", params={"exerciseId": exerciseId})

def start_container(exerciseId: str) -> dict[str, Any]:
    """启动容器。"""
    return _curl("POST", "/ctf/build-exercise-env", json_data={"exerciseId": int(exerciseId)})

def stop_container(exerciseId: str) -> dict[str, Any]:
    """回收容器。"""
    return _curl("POST", "/ctf/recover-exercise-env", json_data={"exerciseId": int(exerciseId)})

def submit_flag(exerciseId: str, flag: str) -> dict[str, Any]:
    """提交Flag。"""
    return _curl("POST", "/answer-panel/answer", json_data={"exerciseId": int(exerciseId), "flag": flag[:256]})

def get_match_info() -> dict[str, Any]:
    """获取竞赛信息。"""
    return _curl("GET", "/match/notice/match-info")

def get_overview() -> dict[str, Any]:
    """获取得分排名。"""
    return _curl("GET", "/answer-panel/overview")

def get_notices() -> dict[str, Any]:
    """获取公告列表。"""
    return _curl("GET", "/match/notice/now-list")

def get_notice_detail(notice_id: str) -> dict[str, Any]:
    """获取公告详情。"""
    return _curl("GET", "/match/notice/detail", params={"id": notice_id})

def download_attachment(exerciseId: str, filename: str = "") -> dict[str, Any]:
    """下载附件（Mock平台返回模拟内容）。"""
    return {"success": True, "code": "00000", "message": "ok", "data": {"content": b"mock attachment content", "filename": filename or f"{exerciseId}.zip"}, "error": None}
