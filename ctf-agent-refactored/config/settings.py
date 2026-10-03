"""
统一配置层 —— 单一真相源。

优先级（高 → 低）：
  1. 环境变量 / .env 文件（敏感凭证 + 本地覆盖）
  2. config.yaml（非敏感结构配置）
  3. 代码内默认值

设计原则：
  - config.yaml：提交到 git 的非敏感配置（平台端点、参数、开关、LLM地址）
  - .env：不提交的敏感凭证（API Key、Token、密码）
  - 所有硬编码值全部抽离到此处，代码中只引用 Settings 字段。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

logger = logging.getLogger(__name__)


class YamlConfigSource(PydanticBaseSettingsSource):
    """从 config.yaml 加载非敏感配置，优先级低于 .env。"""

    _SECTION_MAP: dict[str, dict[str, str]] = {
        "platform": {
            "type": "platform_type",
            "api_base_url": "platform_api_base_url",
            "api_path": "platform_api_path",
            "access_key_env": "platform_access_key_env",
        },
        "llm": {
            "default_model": "llm_default_model",
            "base_url": "llm_base_url",
            "proxy_url": "llm_proxy_url",
            "api_key_env": "llm_api_key_env",
            "local_base_url": "local_llm_base_url",
            "temperature": "llm_temperature",
            "max_tokens": "llm_max_tokens",
            "timeout": "llm_timeout",
            "bailian_base_url": "bailian_base_url",
            "deepseek_base_url": "deepseek_base_url",
            "gateway_base_url": "gateway_base_url",
            "gateway_bailian_base_url": "gateway_bailian_base_url",
        },
        "solver": {
            "max_concurrent_challenges": "max_concurrent_challenges",
            "token_budget": "solver_token_budget",
            "max_rounds": "solver_max_rounds",
            "max_attempts_per_challenge": "max_attempts_per_challenge",
            "models": "models",
            "coordinator": "coordinator",
            "coordinator_model": "coordinator_model",
        },
        "container": {
            "default_timeout": "container_default_timeout",
            "poll_interval": "container_poll_interval",
            "auto_recover": "container_auto_recover",
        },
        "flag": {
            "pattern": "flag_pattern",
            "min_length": "flag_min_length",
            "max_submit": "flag_max_submit",
        },
        "rate_limit": {
            "enabled": "rate_limit_enabled",
            "requests_per_second": "rate_limit_rps",
            "burst_size": "rate_limit_burst",
        },
        "sandbox": {
            "mode": "sandbox_mode",
            "dns": "sandbox_dns",
            "docker_url": "sandbox_docker_url",
            "image": "sandbox_image",
            "memory_limit": "sandbox_memory_limit",
            "cpu_limit": "sandbox_cpu_limit",
        },
        "web": {
            "host": "web_host",
            "port": "web_port",
            "cors_origins": "web_cors_origins",
        },
        "local": {
            "root": "local_root",
        },
        "logging": {
            "level": "logging_level",
        },
    }

    def __init__(self, settings_cls: type[BaseSettings], yaml_path: str = "config.yaml") -> None:
        super().__init__(settings_cls)
        self.yaml_path = Path(yaml_path)
        self._data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.yaml_path.exists():
            return {}
        try:
            with open(self.yaml_path, encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
        except Exception as e:
            logger.warning("Failed to load %s: %s", self.yaml_path, e)
            return {}
        result: dict[str, Any] = {}
        for section, mapping in self._SECTION_MAP.items():
            section_data = cfg.get(section, {})
            if not isinstance(section_data, dict):
                continue
            for yaml_key, field_name in mapping.items():
                if yaml_key in section_data:
                    result[field_name] = section_data[yaml_key]
        return result

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        return self._data.get(field_name), field_name, False

    def __call__(self) -> dict[str, Any]:
        return {k: v for k, v in self._data.items() if v is not None}


class Settings(BaseSettings):
    """统一配置入口。非敏感来自 config.yaml，SECRET 来自 .env。"""

    # ========== 靶场平台（非敏感来自 config.yaml，SECRET 来自 .env）==========
    platform_type: str = "slab"
    platform_api_base_url: str = "https://pro.dasctf.com"
    platform_api_path: str = "/slab-match/api/v1/agent"
    platform_access_key_env: str = "SLAB_ACCESS_KEY"
    # 以下均为 SECRET，来自 .env
    platform_access_key: str = ""
    platform_auth_credential: str = ""
    ctfd_url: str = ""
    ctfd_token: str = ""
    ctfd_user: str = ""
    ctfd_pass: str = ""
    gzctf_token: str = ""
    gzctf_username: str = ""
    gzctf_password: str = ""

    # ========== LLM（地址来自 config.yaml，Key 全部来自 .env）==========
    llm_default_model: str = "qwen3:4b"
    llm_base_url: str = ""
    llm_proxy_url: str = ""  # Explicit proxy for public endpoints; private/loopback bypass it.
    llm_api_key_env: str = "GATEWAY_API_KEY"
    llm_api_key: str = ""
    local_llm_base_url: str = "http://127.0.0.1:11434/v1"
    llm_temperature: float = 0.7
    llm_max_tokens: int = 4096
    llm_timeout: int = 120
    # LLM 基础地址（非敏感，config.yaml）
    bailian_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    deepseek_base_url: str = "https://api.deepseek.com"
    gateway_base_url: str = ""
    gateway_bailian_base_url: str = ""
    # LLM API Keys（全部 SECRET，.env）
    bailian_api_key: str = ""
    deepseek_api_key: str = ""
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    gemini_api_key: str = ""
    gateway_api_key: str = ""
    gateway_bailian_api_key: str = ""
    # 云厂商（SECRET，.env）
    aws_bearer_token: str = ""
    aws_region: str = "us-east-1"
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    opencode_zen_api_key: str = ""

    # ========== Web 门禁 / Docker（SECRET，来自 .env）==========
    muteki_password: str = ""
    docker_registry_user: str = ""
    docker_registry_pass: str = ""

    # ========== 求解器 ==========
    max_concurrent_challenges: int = 2
    solver_token_budget: int = 100000
    solver_allow_flag_submit: bool = False
    solver_allow_container_start: bool = False
    solver_max_rounds: int = 20
    max_attempts_per_challenge: int = 3
    models: str = ""
    coordinator: str = "auto"
    coordinator_model: str = ""

    # ========== 容器（靶机环境）==========
    container_default_timeout: int = 120
    container_poll_interval: float = 3.0
    container_auto_recover: bool = True

    # ========== Flag ==========
    flag_pattern: str = ""
    flag_min_length: int = 1
    flag_max_submit: int = 50

    # ========== 速率限制 ==========
    rate_limit_enabled: bool = True
    rate_limit_rps: int = 3
    rate_limit_burst: int = 10

    # ========== 本地题库 ==========
    local_root: str = ""

    # ========== 沙箱 ==========
    sandbox_mode: str = "auto"          # auto | docker | host
    sandbox_dns: str = ""
    sandbox_docker_url: str = "unix:///var/run/docker.sock"
    sandbox_image: str = "ctf-sandbox"
    sandbox_memory_limit: str = "16g"
    sandbox_cpu_limit: int = 2

    # ========== Web ==========
    web_host: str = "0.0.0.0"
    web_port: int = 12346
    web_cors_origins: list[str] = ["*"]

    # ========== 日志 ==========
    logging_level: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings, env_settings, dotenv_settings, YamlConfigSource(settings_cls), file_secret_settings)

    def resolve_platform_access_key(self) -> str:
        """从环境变量解析平台 AccessKey（优先直接配置，其次从 access_key_env 指定的变量读取）。"""
        if self.platform_access_key:
            return self.platform_access_key
        return os.environ.get(self.platform_access_key_env, "")
