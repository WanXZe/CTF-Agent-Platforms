"""OpenAI-compatible model client: DasCTF LLM gateway / vLLM / Ollama / any /v1 endpoint.

Endpoint rules (see config.yaml llm.base_url):
  * gateway style  https://host/llm-gateway/proxy/e/<id>  -> POST directly (no suffix)
  * standard style http://host:port/v1                    -> POST <base>/chat/completions

API key resolution order:
  1. settings.llm_api_key        (env LLM_API_KEY / .env)
  2. os.environ[settings.llm_api_key_env]   (e.g. GATEWAY_API_KEY in .env)

A keyless loopback endpoint (local Ollama) is the only case allowed to run without a key.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_LOOPBACK_PREFIXES = ("http://127.0.0.1:", "http://localhost:", "http://[::1]:")
_ENV_FILES = (Path(".env"), Path(__file__).resolve().parents[2] / ".env")


def _read_env_file(name: str) -> str:
    """pydantic-settings 不会把 .env 灌进 os.environ，这里自己读一次。"""
    for path in _ENV_FILES:
        try:
            if not path.is_file():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                if key.strip() == name:
                    return value.strip().strip('"').strip("'")
        except OSError:
            continue
    return ""


class LocalModel:
    """Minimal async chat-completions client used by the solve loop."""

    def __init__(self, settings) -> None:
        self.settings = settings
        self.last_usage: dict[str, Any] = {}
        self.last_model: str = ""
        self.last_response = None
        self._client: httpx.AsyncClient | None = None

    def _http(self):
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.settings.llm_timeout, connect=10.0),
                trust_env=False,
            )
        return self._client

    async def aclose(self):
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _connection_error(self, exc):
        if not isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
            return ValueError(
                f"模型请求中断：{self._endpoint(self._base_url())}（{type(exc).__name__}）。"
                "请检查服务响应时间和网络稳定性；服务端可能仍在处理本次请求。"
            )
        return ValueError(
            f"模型连接失败：{self._endpoint(self._base_url())}（{type(exc).__name__}）。"
            "请检查服务是否启动、VM DNS 与宿主机中继是否可达；连接失败尚未进入模型推理。"
        )

    async def probe(self):
        """Read-only connectivity/auth/model check; never sends a completion."""
        base, key = self._base_url(), self._api_key()
        if "/llm-gateway/proxy/" in base:
            return {"reachable": False, "message": "此网关未提供只读模型列表，无法无消耗验证生成接口", "endpoint": self._endpoint(base)}
        root = base.removesuffix("/chat/completions")
        try:
            response = await self._http().get(root + "/models", headers={"Authorization": f"Bearer {key}"} if key else {}, timeout=12)
        except httpx.RequestError as exc:
            raise self._connection_error(exc) from exc
        if response.status_code >= 400:
            raise ValueError(f"模型检查 HTTP {response.status_code}：{self._http_error(response.status_code)}")
        names = [m.get("id") for m in response.json().get("data", [])]
        available = self.settings.llm_default_model in names
        return {"reachable": True, "available": available, "endpoint": self._endpoint(base),
                "message": "连接与鉴权正常，模型可用（未发起生成请求）" if available else "连接正常，但服务未列出所选模型，请检查模型名称"}

    @staticmethod
    def _http_error(code):
        return {401: "API Key 无效或缺失", 402: "账户余额不足", 404: "接口路径或模型名称不存在", 429: "请求限流，请稍后重试"}.get(code, "服务返回错误，请查看服务状态")

    # ---- config helpers -------------------------------------------------
    def _base_url(self) -> str:
        base = str(getattr(self.settings, "llm_base_url", "") or "").strip()
        if not base:
            base = str(getattr(self.settings, "local_llm_base_url", "") or "").strip()
        if not base:
            raise ValueError("LLM base url is empty (set llm.base_url in config.yaml)")
        return base.rstrip("/")

    def _api_key(self) -> str:
        key = str(getattr(self.settings, "llm_api_key", "") or "").strip()
        if key:
            return key
        env_name = str(getattr(self.settings, "llm_api_key_env", "") or "").strip() or "GATEWAY_API_KEY"
        key = os.environ.get(env_name, "").strip()
        if key:
            return key
        return _read_env_file(env_name)

    @staticmethod
    def _endpoint(base: str) -> str:
        if "/llm-gateway/proxy/" in base:
            return base
        if base.endswith("/chat/completions"):
            return base
        return base + "/chat/completions"

    # ---- main entry -----------------------------------------------------
    async def complete(self, messages: list[dict], tools: list[dict]) -> dict:
        self.last_response = None
        self.last_usage = {}
        base = self._base_url()
        key = self._api_key()
        if not key and not base.startswith(_LOOPBACK_PREFIXES):
            raise ValueError(
                "缺少 API Key：请在 ctf-agent-refactored/.env 中填写 "
                f"{getattr(self.settings, 'llm_api_key_env', 'GATEWAY_API_KEY')}=<你的key>"
            )
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        payload: dict[str, Any] = {
            "model": self.settings.llm_default_model,
            "messages": messages,
            "temperature": self.settings.llm_temperature,
            "max_tokens": self.settings.llm_max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        try:
            response = await self._http().post(self._endpoint(base), json=payload, headers=headers)
        except httpx.RequestError as exc:
            raise self._connection_error(exc) from exc
        if response.status_code >= 400:
            self.last_response = {'http_status': response.status_code, 'error': self._http_error(response.status_code)}
            raise ValueError(f"LLM HTTP {response.status_code}: {self._http_error(response.status_code)}")
        data = response.json()
        self.last_response = data
        choices = data.get("choices") or []
        message = (choices[0].get("message") if choices else None) or {}
        if not isinstance(message, dict):
            raise ValueError(f"LLM returned no chat message: {str(data)[:500]}")
        if not message.get("content") and not message.get("tool_calls"):
            reason = message.get("reasoning_content") or ""
            finish = (choices[0].get("finish_reason") if choices else "") or ""
            if reason:
                # 推理型模型（deepseek-flash / deepseek-v4-pro）可能整轮只在
                # reasoning_content 里输出；把思维链当正文回退，避免解题循环中断。
                message = dict(message)
                message["content"] = str(reason)
            elif finish == "length":
                raise ValueError(
                    "LLM 输出被 max_tokens 截断（finish_reason=length）："
                    "调大 config.yaml 的 llm.max_tokens"
                )
            else:
                raise ValueError(f"LLM returned empty message: {str(data)[:500]}")
        self.last_usage = data.get("usage") or {}
        self.last_model = data.get("model") or payload["model"]
        return message
