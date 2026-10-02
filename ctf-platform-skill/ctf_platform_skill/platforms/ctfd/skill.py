"""
CTFd 平台 Skill —— 直接 curl 调用形式。

CTFd 标准 REST API，使用 Authorization: Bearer <token> 鉴权。

等价 curl 示例：
  curl -s -X GET "http://localhost:8000/api/v1/challenges" \
    -H "Authorization: Bearer your-token" -H "Content-Type: application/json"
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Optional

import yaml

_CONFIG_PATH = Path(__file__).parent / "config.yaml"


def _secret(name: str) -> str:
    env_path = Path.cwd() / ".env"
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if "=" not in line or line.lstrip().startswith("#"):
                continue
            key, raw = line.split("=", 1)
            if key.strip() == name:
                return raw.strip().strip('"').strip("'")
    return os.environ.get(name, "")


def _load_config() -> dict[str, Any]:
    cfg: dict[str, Any] = {}
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    cfg.setdefault("api_base_url", os.environ.get("CTFD_URL", "http://localhost:8000"))
    cfg.setdefault("api_path", os.environ.get("CTFD_API_PATH", "/api/v1"))
    _env_key = cfg.get("access_key_env", "CTFD_TOKEN")
    cfg.setdefault("token", _secret(_env_key))
    cfg.setdefault("timeout", int(os.environ.get("CTFD_TIMEOUT", "30")))
    return cfg


def _curl(method: str, path: str, *, params: Optional[dict[str, Any]] = None, json_data: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    cfg = _load_config()
    base = cfg["api_base_url"].rstrip("/")
    url = f"{base}{cfg['api_path']}{path}"
    cmd = ["curl", "-s", "-X", method, url]
    cmd += ["-H", f"Authorization: Bearer {cfg['token']}"]
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
            return {"success": False, "error": "curl_error", "message": (result.stderr or "").strip(), "data": None}
        if not (result.stdout or "").strip():
            return {"success": False, "error": "empty_response", "message": "空响应", "data": None}
        payload = json.loads(result.stdout)
    except Exception as e:
        return {"success": False, "error": "request_error", "message": str(e), "data": None}
    # CTFd 返回 {success: true/false, data: ...}
    is_success = payload.get("success", False)
    return {
        "success": is_success,
        "code": "00000" if is_success else "CTFD_ERROR",
        "message": "ok" if is_success else payload.get("message", "请求失败"),
        "data": payload.get("data", payload),
        "error": None if is_success else "platform_error",
    }


def list_challenges() -> dict[str, Any]:
    """获取题目列表。curl -X GET {base}/api/v1/challenges"""
    return _curl("GET", "/challenges")

def get_challenge(challenge_id: str) -> dict[str, Any]:
    """获取题目详情。"""
    return _curl("GET", f"/challenges/{challenge_id}")

def start_container(challenge_id: str) -> dict[str, Any]:
    """CTFd 标准平台无容器概念，返回不支持。"""
    return {"success": False, "error": "not_supported", "message": "CTFd 标准平台不支持容器启停", "data": None}

def stop_container(challenge_id: str) -> dict[str, Any]:
    return {"success": False, "error": "not_supported", "message": "CTFd 标准平台不支持容器启停", "data": None}

def submit_flag(challenge_id: str, flag: str) -> dict[str, Any]:
    """提交Flag。curl -X POST {base}/api/v1/challenges/attempt -d '{"challenge_id":id,"submission":flag}'"""
    return _curl("POST", "/challenges/attempt", json_data={"challenge_id": int(challenge_id), "submission": flag})

def download_attachment(challenge_id: str, filename: str = "") -> dict[str, Any]:
    """下载附件。"""
    detail = get_challenge(challenge_id)
    if not detail["success"]:
        return detail
    files = detail["data"].get("files", []) if isinstance(detail["data"], dict) else []
    if not files:
        return {"success": False, "error": "no_files", "message": "无附件", "data": None}
    cfg = _load_config()
    file_url = files[0] if isinstance(files[0], str) else files[0].get("url", "")
    if not file_url.startswith("http"):
        file_url = f"{cfg['api_base_url'].rstrip('/')}{file_url}"
    try:
        result = subprocess.run(["curl", "-s", "-L", file_url, "-H", f"Authorization: Bearer {cfg['token']}", "--max-time", "60"], capture_output=True, timeout=65)
        return {"success": True, "code": "00000", "message": "ok", "data": {"content": result.stdout, "filename": filename or file_url.split("/")[-1]}, "error": None}
    except Exception as e:
        return {"success": False, "error": "download_error", "message": str(e), "data": None}
