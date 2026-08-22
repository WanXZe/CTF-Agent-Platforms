"""
西湖论剑 slab-match 平台 Skill —— 直接 curl 调用形式。

每个函数对应一个平台接口，内部使用 curl 命令发起 HTTP 请求。
配置从同目录 config.yaml 读取（优先级最高）。

等价 curl 示例：
  curl -s -X GET "https://pro.dasctf.com/slab-match/api/v1/agent/ctf/exercise-list" \
    -H "X-Agent-AccessKey: your-key" -H "Content-Type: application/json"
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
    """加载平台级 config.yaml（优先级最高），缺失字段从环境变量兜底。"""
    cfg: dict[str, Any] = {}
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    # 环境变量兜底（.env）
    cfg.setdefault("api_base_url", os.environ.get("SLAB_API_BASE", "https://pro.dasctf.com"))
    cfg.setdefault("api_path", os.environ.get("SLAB_API_PATH", "/slab-match/api/v1/agent"))
    _env_key = cfg.get("access_key_env", "SLAB_ACCESS_KEY")
    cfg.setdefault("access_key", os.environ.get(_env_key, ""))
    cfg.setdefault("timeout", int(os.environ.get("SLAB_TIMEOUT", "30")))
    return cfg


def _curl(
    method: str,
    path: str,
    *,
    params: Optional[dict[str, Any]] = None,
    json_data: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """
    直接调用 curl 发起请求。

    Args:
        method: HTTP 方法（GET / POST）
        path:   相对 api_path 的路径，如 "/ctf/exercise-list"
        params: URL 查询参数
        json_data: 请求体 JSON
    """
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
            return {"success": False, "error": "curl_error", "message": (result.stderr or "").strip() or f"curl exit code {result.returncode}", "data": None}
        if not (result.stdout or "").strip():
            return {"success": False, "error": "empty_response", "message": "平台返回空响应", "data": None}
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        return {"success": False, "error": "invalid_json", "message": f"平台返回非JSON: {str(e)[:200]}", "data": None}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "timeout", "message": f"请求超时（{cfg.get('timeout', 30)}s）", "data": None}
    except Exception as e:
        return {"success": False, "error": "unknown", "message": str(e), "data": None}

    # 统一结构化返回
    code = str(payload.get("code", ""))
    message = str(payload.get("message") or payload.get("msg") or "")
    data = payload.get("data", payload)
    success = code == "00000"
    return {
        "success": success,
        "code": code,
        "message": message if success else (message or f"平台错误码 {code}"),
        "data": data,
        "error": None if success else "platform_error",
    }


# ═══════════════════════════════════════════════════════════════════════════
# 平台接口（每个函数 = 一条 curl 命令）
# ═══════════════════════════════════════════════════════════════════════════

def list_challenges() -> dict[str, Any]:
    """
    获取题目列表（分类→子题两级）。
    curl -s -X GET "{base}/slab-match/api/v1/agent/ctf/exercise-list" \
      -H "X-Agent-AccessKey: {key}"
    """
    return _curl("GET", "/ctf/exercise-list")


def get_challenge(exerciseId: str) -> dict[str, Any]:
    """
    获取题目详情（含附件/靶机信息）。
    curl -s -G "{base}/slab-match/api/v1/agent/ctf/exercise" \
      --data-urlencode "exerciseId={id}" \
      -H "X-Agent-AccessKey: {key}"
    """
    return _curl("GET", "/ctf/exercise", params={"exerciseId": exerciseId})


def start_container(exerciseId: str) -> dict[str, Any]:
    """
    启动题目容器环境（异步）。
    curl -s -X POST "{base}/slab-match/api/v1/agent/ctf/build-exercise-env" \
      -H "X-Agent-AccessKey: {key}" -H "Content-Type: application/json" \
      -d '{"exerciseId": {id}}'
    """
    return _curl("POST", "/ctf/build-exercise-env", json_data={"exerciseId": int(exerciseId)})


def stop_container(exerciseId: str) -> dict[str, Any]:
    """
    回收题目容器环境。
    curl -s -X POST "{base}/slab-match/api/v1/agent/ctf/recover-exercise-env" \
      -H "X-Agent-AccessKey: {key}" -H "Content-Type: application/json" \
      -d '{"exerciseId": {id}}'
    """
    return _curl("POST", "/ctf/recover-exercise-env", json_data={"exerciseId": int(exerciseId)})


def submit_flag(exerciseId: str, flag: str) -> dict[str, Any]:
    """
    提交 Flag。
    curl -s -X POST "{base}/slab-match/api/v1/agent/answer-panel/answer" \
      -H "X-Agent-AccessKey: {key}" -H "Content-Type: application/json" \
      -d '{"exerciseId": {id}, "flag": "{flag}"}'
    """
    return _curl("POST", "/answer-panel/answer", json_data={"exerciseId": int(exerciseId), "flag": flag[:256]})


def get_match_info() -> dict[str, Any]:
    """获取竞赛信息/规则。"""
    return _curl("GET", "/match/notice/match-info")


def get_overview() -> dict[str, Any]:
    """获取得分排名概览。"""
    return _curl("GET", "/answer-panel/overview")


def get_notices() -> dict[str, Any]:
    """获取公告列表。"""
    return _curl("GET", "/match/notice/now-list")


def get_notice_detail(notice_id: str) -> dict[str, Any]:
    """获取公告详情。"""
    return _curl("GET", "/match/notice/detail", params={"id": notice_id})


def download_attachment(exerciseId: str, filename: str = "") -> dict[str, Any]:
    """
    下载题目附件（返回二进制内容的 base64）。
    注意：附件下载走单独的 curl -O 逻辑，不经过统一 JSON 解析。
    """
    cfg = _load_config()
    base = cfg["api_base_url"].rstrip("/")
    # slab-match 附件通常在题目详情的 files 字段中给出 URL
    detail = get_challenge(exerciseId)
    if not detail["success"]:
        return detail
    files = detail["data"].get("files", []) if isinstance(detail["data"], dict) else []
    if not files:
        return {"success": False, "error": "no_files", "message": "该题目无附件", "data": None}
    target = files[0]
    file_url = target.get("url", "")
    if not file_url or file_url == "#":
        return {"success": False, "error": "no_download_url", "message": "附件无下载链接", "data": None}
    try:
        result = subprocess.run(
            ["curl", "-s", "-L", file_url, "-H", f"X-Agent-AccessKey: {cfg['access_key']}", "--max-time", "60"],
            capture_output=True, timeout=65,
        )
        return {"success": True, "code": "00000", "message": "ok", "data": {"content": result.stdout, "filename": target.get("name", filename or "attachment")}, "error": None}
    except Exception as e:
        return {"success": False, "error": "download_error", "message": str(e), "data": None}
