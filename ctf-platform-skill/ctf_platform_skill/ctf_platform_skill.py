"""
CTF 靶场平台 Agent Skill —— 纯能力封装层。

本模块将 CTF 比赛靶场平台（西湖论剑 slab-match 等）的全部 REST 接口
抽象为独立、可被 Agent 直接 import 调用的函数。Agent 推理层只调用本
模块导出的函数，不直接写 HTTP 请求，从而实现「推理 ↔ 平台通信」解耦。

平台协议（slab-match AI Agent API）：
  Base   : {host}/slab-match/api/v1/agent
  Auth   : Header  X-Agent-AccessKey: <access_key>
  响应   : 统一 JSON  {code, message, data}，code == "00000" 表示成功
  题目模型: 分类(category) → 子题(corpus) 两级，所有操作使用 corpus.id 作为 exerciseId

返回值约定（适配 Agent 工具调用）：
  每个函数返回 dict，统一包含：
    {
      "success": bool,           # 调用是否成功
      "code": str,               # 平台返回码（"00000"=成功），失败时为错误码
      "message": str,            # 平台消息 / 错误描述
      "data": Any | None,        # 业务数据（成功时）
      "error": str | None        # 结构化错误信息（失败时，Agent 可感知）
    }

异常处理：
  - 网络异常（超时 / 连接失败 / DNS）→ 捕获并返回 success=False, error="network_error"
  - 平台接口报错（code != "00000"）→ 返回 success=False, error="platform_error"
  - 容器操作失败 → 返回 success=False, error="container_error"
  - 参数非法 → 返回 success=False, error="invalid_param"
  - HTTP 429 限流 → 返回 success=False, error="rate_limited"
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# 常量
# ─────────────────────────────────────────────────────────────────────────────

USER_AGENT = "CTF-Agent/1.0"
OK_CODE = "00000"
_DEFAULT_API_PATH = "/slab-match/api/v1/agent"
_DEFAULT_TIMEOUT = 30.0
_DEFAULT_ENV_POLL_INTERVAL = 3.0
_DEFAULT_ENV_TIMEOUT = 120.0
_RATE_LIMIT_COOLDOWN = 300.0  # 429 后冷却 5 分钟

# 容器状态枚举（供前端 / Agent 统一识别）
CONTAINER_STATUS = {
    "NOT_STARTED": "not_started",   # 未启动
    "STARTING": "starting",         # 启动中（loading）
    "RUNNING": "running",           # 运行中
    "STOPPED": "stopped",           # 已停止
    "FAILED": "failed",             # 操作失败
}


# ─────────────────────────────────────────────────────────────────────────────
# 统一返回构造器
# ─────────────────────────────────────────────────────────────────────────────

def _ok(data: Any = None, message: str = "ok") -> dict[str, Any]:
    """构造成功返回。"""
    return {
        "success": True,
        "code": OK_CODE,
        "message": message,
        "data": data,
        "error": None,
    }


def _fail(error_type: str, message: str, code: str = "", data: Any = None) -> dict[str, Any]:
    """构造失败返回（结构化错误，方便 Agent 感知失败原因）。"""
    return {
        "success": False,
        "code": code or error_type,
        "message": message,
        "data": data,
        "error": error_type,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Skill 主类
# ─────────────────────────────────────────────────────────────────────────────

class CTFPlatformSkill:
    """
    CTF 靶场平台 Skill 封装。

    用法（Agent 侧）：
        skill = CTFPlatformSkill(
            api_base_url="https://pro.dasctf.com",
            access_key="your_access_key",
        )
        result = await skill.list_challenges()
        if result["success"]:
            for ch in result["data"]:
                ...

    也可使用模块级便捷函数（见文件末尾）。
    """

    def __init__(
        self,
        api_base_url: str,
        access_key: str = "",
        *,
        api_path: str = _DEFAULT_API_PATH,
        timeout: float = _DEFAULT_TIMEOUT,
        rate_limit_rps: int = 5,
        verify_ssl: bool = False,
    ) -> None:
        """
        初始化靶场平台 Skill。

        Args:
            api_base_url: 靶场平台根地址，例如 "https://pro.dasctf.com"
            access_key:   AI Agent AccessKey（X-Agent-AccessKey header）
            api_path:     API 路径前缀，默认 "/slab-match/api/v1/agent"
            timeout:      HTTP 请求超时（秒）
            rate_limit_rps: 每秒最大请求数（令牌桶限流）
            verify_ssl:   是否校验 SSL 证书（自签证书场景设为 False）
        """
        self.api_base_url = api_base_url.rstrip("/")
        self.access_key = access_key
        self.api_path = api_path.rstrip("/")
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self._client: Optional[httpx.AsyncClient] = None
        self._last_error: str = ""
        # 429 冷却门
        self._cooldown_until: float = 0.0
        self._cooldown_seconds: float = _RATE_LIMIT_COOLDOWN
        # 简易令牌桶
        self._rps = max(1, rate_limit_rps)
        self._tokens: float = float(self._rps)
        self._last_refill: float = time.monotonic()
        # 题目缓存：exerciseId(str) -> 详情 dict
        self._challenge_cache: dict[str, dict[str, Any]] = {}

    # ── HTTP 客户端 ────────────────────────────────────────────────────────

    async def _ensure_client(self) -> httpx.AsyncClient:
        """懒初始化 httpx.AsyncClient。"""
        if self._client is None:
            headers = {"User-Agent": USER_AGENT}
            if self.access_key:
                headers["X-Agent-AccessKey"] = self.access_key
            self._client = httpx.AsyncClient(
                base_url=self.api_base_url,
                headers=headers,
                timeout=self.timeout,
                verify=self.verify_ssl,
                follow_redirects=True,
            )
        return self._client

    async def _acquire_token(self) -> None:
        """令牌桶限流：等待获取一个令牌。"""
        while True:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(float(self._rps), self._tokens + elapsed * self._rps)
            self._last_refill = now
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return
            await asyncio.sleep(0.1)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Optional[dict[str, Any]] = None,
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """
        发起 HTTP 请求并解析统一 JSON 响应。

        Args:
            method: HTTP 方法（GET / POST 等）
            path:   相对 api_path 的路径，例如 "/ctf/exercise-list"
            json:   请求体 JSON
            params: URL 查询参数

        Returns:
            统一结构 dict：{success, code, message, data, error}
        """
        # 冷却期快速失败
        now = time.time()
        if now < self._cooldown_until:
            remain = int(self._cooldown_until - now)
            return _fail("rate_limited", f"平台限流冷却中，剩余 {remain}s")

        url = f"{self.api_path}{path}"
        await self._acquire_token()
        client = await self._ensure_client()

        # 仅在非 None 时传 json / params，避免 httpx 发送非预期 Content-Type 或空体
        req_kwargs: dict[str, Any] = {}
        if json is not None:
            req_kwargs["json"] = json
        if params is not None:
            req_kwargs["params"] = params

        logger.debug("平台请求 %s %s params=%s json=%s", method, url, params, json)

        # 传输层错误重试（指数退避，最多 3 次）
        last_exc: Optional[Exception] = None
        for attempt in range(3):
            try:
                resp = await client.request(method, url, **req_kwargs)
                last_exc = None
                break
            except httpx.TransportError as e:
                last_exc = e
                if attempt < 2:
                    await asyncio.sleep(0.5 * (2 ** attempt))

        if last_exc is not None:
            self._last_error = f"network_error: {last_exc}"
            return _fail("network_error", f"网络请求失败: {last_exc}")

        logger.debug("平台响应 %s %s status=%s body=%s", method, url, resp.status_code, resp.text[:500])

        # HTTP 状态码处理
        if resp.status_code == 429:
            self._cooldown_until = time.time() + self._cooldown_seconds
            self._last_error = "rate_limited: HTTP 429"
            return _fail("rate_limited", "平台请求频率超限（HTTP 429），已进入冷却")

        if resp.status_code >= 500:
            self._last_error = f"server_error: HTTP {resp.status_code}"
            return _fail("server_error", f"平台服务器错误（HTTP {resp.status_code}）")

        # 解析 JSON
        try:
            payload = resp.json()
        except Exception:
            self._last_error = f"invalid_json: {resp.text[:200]}"
            return _fail("invalid_response", f"平台返回非 JSON 响应: {resp.text[:200]}")

        if not isinstance(payload, dict):
            return _fail("invalid_response", f"平台返回格式异常: {payload}")

        code = str(payload.get("code", ""))
        message = str(payload.get("message") or payload.get("msg") or "")
        data = payload.get("data", payload)

        if code != OK_CODE:
            self._last_error = f"platform_error {code}: {message}"
            # 携带原始响应，方便 Agent / 调用方诊断
            return _fail(
                "platform_error",
                message or f"平台错误码 {code}",
                code=code,
                data={"raw": payload, "request_url": url, "request_method": method},
            )

        return _ok(data=data, message=message or "ok")

    # ── 1. 题目列表 ────────────────────────────────────────────────────────

    async def list_challenges(self, light: bool = True) -> dict[str, Any]:
        """
        获取全部题目列表（分类 → 子题 两级结构）。

        Args:
            light: 轻量模式，只返回列表级信息（id/name/category/solved），
                   不逐题补详情，避免多连发触发平台 429。
                   需要附件/靶机信息时对目标题单独调用 get_challenge_detail。

        Returns:
            success=True 时 data 为题目列表：
            [
              {
                "id": "123",              # 题目 ID（corpus.id，exerciseId）
                "name": "题目名称",
                "category": "Web",        # 分类名称
                "solved": false,          # 是否已解出
                "need_container": false,  # 是否需要容器环境（详情模式下填充）
                "value": 0,               # 分值（详情模式下填充）
                "description": "",        # 描述（详情模式下填充）
              },
              ...
            ]
        """
        # 主调用：GET 无参（slab-match 标准方式）
        result = await self._request("GET", "/ctf/exercise-list")
        if not result["success"]:
            # 容错：部分平台版本要求分页参数或 POST 方式，自动降级尝试
            logger.warning("exercise-list GET 失败，尝试降级调用: %s", result.get("message"))
            fallback = await self._request("GET", "/ctf/exercise-list", params={"page": 1, "pageSize": 100})
            if fallback["success"]:
                result = fallback
            else:
                fallback2 = await self._request("POST", "/ctf/exercise-list", json={})
                if fallback2["success"]:
                    result = fallback2
        if not result["success"]:
            return result

        data = result["data"]
        categories = data if isinstance(data, list) else (
            data.get("list", data.get("data", [])) if isinstance(data, dict) else []
        )

        challenges: list[dict[str, Any]] = []
        for cat in categories if isinstance(categories, list) else []:
            if not isinstance(cat, dict):
                continue
            cat_name = str(cat.get("name") or "")
            for item in cat.get("corpus", []) if isinstance(cat, dict) else []:
                if not isinstance(item, dict):
                    continue
                cid = str(item.get("id", ""))
                if not cid:
                    continue
                ch: dict[str, Any] = {
                    "id": cid,
                    "name": str(item.get("name", cid)),
                    "category": cat_name,
                    "solved": bool(item.get("hasSolved", False)),
                    "need_container": False,
                    "value": 0,
                    "description": "",
                    "tags": [],
                    "files": [],
                    "connection_info": "",
                }
                # 非 light 模式：逐题补详情
                if not light:
                    detail = await self.get_challenge_detail(cid)
                    if detail["success"] and detail["data"]:
                        d = detail["data"]
                        ch.update({
                            "value": d.get("value", 0),
                            "description": d.get("description", ""),
                            "need_container": d.get("need_container", False),
                            "tags": d.get("tags", []),
                            "files": d.get("files", []),
                            "connection_info": d.get("connection_info", ""),
                            "solved": ch["solved"] or d.get("solved", False),
                        })
                self._challenge_cache[cid] = ch
                challenges.append(ch)

        return _ok(data=challenges, message=f"共 {len(challenges)} 道题目")

    # ── 2. 查询单个题目详情 ────────────────────────────────────────────────

    async def get_challenge_detail(self, challenge_id: str | int) -> dict[str, Any]:
        """
        获取单个题目的详细信息。

        Args:
            challenge_id: 题目 ID（exerciseId / corpus.id）

        Returns:
            success=True 时 data 为题目详情：
            {
              "id": "123",
              "name": "题目名称",
              "category": "",
              "description": "题目描述...",
              "value": 500,                    # 分值
              "tags": ["easy"],                # 难度/标签
              "solved": false,                 # 是否已解出
              "need_container": true,          # 是否需要容器环境（isNeedInit）
              "container_status": "not_started", # 容器状态
              "connection_info": "host:port",  # 容器访问地址（运行中时）
              "files": [                       # 附件列表
                {"name": "attach.zip", "url": "https://...", "ext": "zip"}
              ],
              "raw": {...}                     # 平台原始返回
            }
        """
        cid = str(challenge_id)
        if not cid:
            return _fail("invalid_param", "challenge_id 不能为空")

        result = await self._request(
            "GET", "/ctf/exercise", params={"exerciseId": cid}
        )
        if not result["success"]:
            # 失败时尝试返回缓存
            cached = self._challenge_cache.get(cid)
            if cached:
                return _ok(data=cached, message="使用缓存数据（平台请求失败）")
            return result

        data = result["data"]
        if not isinstance(data, dict):
            return _fail("invalid_response", "题目详情格式异常")

        need_container = bool(data.get("isNeedInit", False))
        endpoints = data.get("endpoints")
        connection_info = self._parse_connection_info(endpoints)
        is_checking = bool(data.get("isNeedCheck", False))

        # 推导容器状态
        if need_container and not connection_info and is_checking:
            container_status = CONTAINER_STATUS["STARTING"]
        elif connection_info:
            container_status = CONTAINER_STATUS["RUNNING"]
        elif need_container and not connection_info:
            container_status = CONTAINER_STATUS["NOT_STARTED"]
        else:
            container_status = CONTAINER_STATUS["STOPPED"]

        detail: dict[str, Any] = {
            "id": cid,
            "name": str(data.get("name") or cid),
            "category": str(data.get("category") or ""),
            "description": str(data.get("description") or ""),
            "value": int(float(str(data.get("score") or "0"))),
            "tags": [str(data.get("difficulty", ""))] if data.get("difficulty") else [],
            "solved": bool(data.get("hasSolved", False)),
            "need_container": need_container,
            "container_status": container_status,
            "connection_info": connection_info,
            "files": self._parse_attachment(data.get("attachment")),
            "raw": data,
        }
        self._challenge_cache[cid] = detail
        return _ok(data=detail)

    # ── 3. 下载题目附件 ────────────────────────────────────────────────────

    async def download_attachment(
        self,
        challenge_id: str | int,
        filename: str = "",
    ) -> dict[str, Any]:
        """
        下载题目附件文件。

        Args:
            challenge_id: 题目 ID
            filename:     附件文件名（从题目详情 files 中匹配 name）；
                          若直接传入完整 URL（http/https 开头）则直接下载。

        Returns:
            success=True 时 data 为：
            {
              "filename": "attach.zip",
              "content_type": "application/zip",
              "size": 12345,
              "content": <bytes>     # 文件二进制内容
            }
        """
        cid = str(challenge_id)
        if not cid:
            return _fail("invalid_param", "challenge_id 不能为空")

        # 确定下载 URL
        url: Optional[str] = None
        if filename.startswith(("http://", "https://")):
            url = filename
        else:
            # 从详情中找附件 URL
            detail = await self.get_challenge_detail(cid)
            if detail["success"] and detail["data"]:
                files = detail["data"].get("files", [])
                if filename:
                    url = next(
                        (f.get("url") for f in files if f.get("name") == filename),
                        None,
                    )
                elif files:
                    url = files[0].get("url")
                    filename = files[0].get("name", "attachment")

        if not url:
            return _fail("invalid_param", f"未找到附件 URL（challenge_id={cid}, filename={filename}）")

        await self._acquire_token()
        client = await self._ensure_client()
        try:
            resp = await client.get(url)
        except Exception as e:
            return _fail("network_error", f"附件下载失败: {e}")

        if resp.status_code != 200:
            return _fail("download_error", f"附件下载 HTTP {resp.status_code}")

        return _ok(data={
            "filename": filename or "attachment",
            "content_type": resp.headers.get("content-type", "application/octet-stream"),
            "size": len(resp.content),
            "content": resp.content,
        })

    # ── 4. 启动容器（开启靶机环境）──────────────────────────────────────────

    async def start_container(self, challenge_id: str | int) -> dict[str, Any]:
        """
        启动题目的动态容器环境（build-exercise-env）。

        该操作是异步的：POST 启动后轮询题目详情，直到容器就绪
        （isNeedCheck=false 且 endpoints 可用）或超时。

        Args:
            challenge_id: 题目 ID

        Returns:
            success=True 时 data 为：
            {
              "challenge_id": "123",
              "status": "running",
              "connection_info": "host:port",   # 容器访问地址
              "endpoints": [...],                # 原始端点信息
            }
            失败时 error 为 "container_error" / "timeout" / "network_error"
        """
        cid = str(challenge_id)
        if not cid:
            return _fail("invalid_param", "challenge_id 不能为空")

        # 先查详情，判断是否需要初始化
        detail = await self.get_challenge_detail(cid)
        if not detail["success"]:
            return _fail("container_error", f"获取题目详情失败: {detail['message']}")

        raw = detail["data"]["raw"] if detail["data"] else {}
        # 非独占题（endpointType != monopoly）可能不需要 build，直接返回已有 entry
        if not raw.get("isNeedInit"):
            entry = detail["data"].get("connection_info", "")
            return _ok(data={
                "challenge_id": cid,
                "status": CONTAINER_STATUS["RUNNING"] if entry else CONTAINER_STATUS["NOT_STARTED"],
                "connection_info": entry,
                "endpoints": raw.get("endpoints"),
                "message": "题目无需初始化或环境已存在",
            })

        # 发起 build 请求
        result = await self._request(
            "POST", "/ctf/build-exercise-env", json={"exerciseId": int(cid)}
        )
        if not result["success"]:
            return _fail("container_error", f"启动容器失败: {result['message']}", code=result["code"])

        # 轮询直到就绪
        deadline = time.monotonic() + _DEFAULT_ENV_TIMEOUT
        while time.monotonic() < deadline:
            await asyncio.sleep(_DEFAULT_ENV_POLL_INTERVAL)
            poll = await self.get_challenge_detail(cid)
            if poll["success"] and poll["data"]:
                d = poll["data"]
                raw = d.get("raw", {})
                if not raw.get("isNeedCheck") and d.get("connection_info"):
                    return _ok(data={
                        "challenge_id": cid,
                        "status": CONTAINER_STATUS["RUNNING"],
                        "connection_info": d["connection_info"],
                        "endpoints": raw.get("endpoints"),
                    })
        # 超时
        return _fail("timeout", f"容器启动超时（{_DEFAULT_ENV_TIMEOUT}s）", data={
            "challenge_id": cid,
            "status": CONTAINER_STATUS["FAILED"],
        })

    # ── 5. 停止容器（回收靶机环境）──────────────────────────────────────────

    async def stop_container(self, challenge_id: str | int) -> dict[str, Any]:
        """
        停止并回收题目的动态容器环境（recover-exercise-env）。

        Args:
            challenge_id: 题目 ID

        Returns:
            success=True 时 data 为：
            {
              "challenge_id": "123",
              "status": "stopped",
              "message": "容器已回收"
            }
        """
        cid = str(challenge_id)
        if not cid:
            return _fail("invalid_param", "challenge_id 不能为空")

        result = await self._request(
            "POST", "/ctf/recover-exercise-env", json={"exerciseId": int(cid)}
        )
        if not result["success"]:
            return _fail("container_error", f"停止容器失败: {result['message']}", code=result["code"])

        # 清除缓存中的容器状态
        if cid in self._challenge_cache:
            self._challenge_cache[cid]["container_status"] = CONTAINER_STATUS["STOPPED"]
            self._challenge_cache[cid]["connection_info"] = ""

        return _ok(data={
            "challenge_id": cid,
            "status": CONTAINER_STATUS["STOPPED"],
            "message": "容器已回收",
        })

    # ── 6. 查询容器运行状态 ────────────────────────────────────────────────

    async def get_container_status(self, challenge_id: str | int) -> dict[str, Any]:
        """
        查询题目的容器运行状态。

        Args:
            challenge_id: 题目 ID

        Returns:
            success=True 时 data 为：
            {
              "challenge_id": "123",
              "need_container": true,           # 该题是否需要容器
              "status": "running",              # not_started / starting / running / stopped / failed
              "connection_info": "host:port",   # 访问地址（运行中时）
              "is_need_check": false,           # 平台是否仍在检查（启动中标志）
              "endpoints": [...],               # 原始端点信息
            }
        """
        cid = str(challenge_id)
        if not cid:
            return _fail("invalid_param", "challenge_id 不能为空")

        detail = await self.get_challenge_detail(cid)
        if not detail["success"]:
            return _fail("container_error", f"查询容器状态失败: {detail['message']}")

        d = detail["data"]
        raw = d.get("raw", {})
        return _ok(data={
            "challenge_id": cid,
            "need_container": d.get("need_container", False),
            "status": d.get("container_status", CONTAINER_STATUS["NOT_STARTED"]),
            "connection_info": d.get("connection_info", ""),
            "is_need_check": bool(raw.get("isNeedCheck", False)),
            "endpoints": raw.get("endpoints"),
        })

    # ── 7. 提交 Flag ──────────────────────────────────────────────────────

    async def submit_flag(self, challenge_id: str | int, flag: str) -> dict[str, Any]:
        """
        提交 Flag 到平台。

        平台规则：flag 格式为 DASCTF{} / flag{}，提交时仅需提交 {} 内内容。
        本函数自动归一化（剥掉外壳）。

        Args:
            challenge_id: 题目 ID
            flag:         Flag 字符串（支持 DASCTF{xxx} / flag{xxx} / 裸 xxx）

        Returns:
            success=True 时 data 为：
            {
              "challenge_id": "123",
              "flag": "xxx",
              "is_correct": true,
              "message": "CORRECT — flag accepted"
            }
            注意：success=True 仅表示请求成功，is_correct 表示 flag 是否正确。
        """
        cid = str(challenge_id)
        if not cid:
            return _fail("invalid_param", "challenge_id 不能为空")
        if not flag or not flag.strip():
            return _fail("invalid_param", "flag 不能为空")

        normalized = self._normalize_flag(flag)
        if not normalized:
            return _fail("invalid_param", "flag 归一化后为空")

        result = await self._request(
            "POST", "/answer-panel/answer",
            json={"exerciseId": int(cid), "flag": normalized[:256]},
        )
        if not result["success"]:
            return result

        data = result["data"]
        is_correct = bool(data.get("isCorrect", False)) if isinstance(data, dict) else False
        return _ok(data={
            "challenge_id": cid,
            "flag": normalized,
            "is_correct": is_correct,
            "message": "CORRECT — flag accepted" if is_correct else "INCORRECT — flag rejected",
        })

    # ── 8. 竞赛信息 / 公告 / 排名（辅助能力）────────────────────────────────

    async def get_match_info(self) -> dict[str, Any]:
        """获取竞赛注意事项与规则。"""
        return await self._request("GET", "/match/notice/match-info")

    async def get_overview(self) -> dict[str, Any]:
        """获取得分与排名概览。"""
        return await self._request("GET", "/answer-panel/overview")

    async def list_notices(self) -> dict[str, Any]:
        """获取公告列表。"""
        result = await self._request("GET", "/match/notice/now-list")
        if not result["success"]:
            return result
        data = result["data"]
        if isinstance(data, list):
            return _ok(data=data)
        if isinstance(data, dict):
            return _ok(data=data.get("list", []))
        return _ok(data=[])

    async def get_notice_detail(self, notice_id: str | int) -> dict[str, Any]:
        """获取公告详情。"""
        nid = str(notice_id)
        if not nid:
            return _fail("invalid_param", "notice_id 不能为空")
        return await self._request(
            "GET", "/match/notice/detail", params={"id": nid}
        )

    # ── 诊断 / 关闭 ────────────────────────────────────────────────────────

    def diagnostics(self) -> dict[str, Any]:
        """返回 Skill 诊断信息（不发起网络请求）。"""
        return {
            "platform": "slab-match",
            "base_url": self.api_base_url,
            "api_path": self.api_path,
            "access_key_set": bool(self.access_key),
            "cached_challenges": len(self._challenge_cache),
            "last_error": self._last_error,
            "in_cooldown": time.time() < self._cooldown_until,
        }

    async def close(self) -> None:
        """关闭 HTTP 客户端，释放资源。"""
        if self._client:
            await self._client.aclose()
            self._client = None

    # ── 内部工具方法 ───────────────────────────────────────────────────────

    @staticmethod
    def _normalize_flag(flag: str) -> str:
        """
        归一化 Flag：剥掉 DASCTF{} / flag{} 外壳，仅提交 {} 内内容。
        非 DASCTF/flag 外壳的 flag 原样保留。
        """
        flag = flag.strip()
        for prefix in ("DASCTF{", "dasctf{", "flag{", "FLAG{", "CTF{", "ctf{"):
            if flag.startswith(prefix) and flag.endswith("}"):
                return flag[len(prefix):-1]
        return flag

    @staticmethod
    def _parse_attachment(attachment: Any) -> list[dict[str, str]]:
        """
        解析平台附件字段，统一返回 [{name, url, ext}]。
        兼容三种格式：
          - {"files": [{name, url, ext}, ...]}
          - 扁平对象 {key, signature, url, name, previewUrl}
          - [{name, url, ext}, ...]
        """
        out: list[dict[str, str]] = []

        def _pick(f: Any) -> None:
            if isinstance(f, dict) and f.get("url"):
                out.append({
                    "name": str(f.get("name") or "attachment"),
                    "url": str(f.get("url")),
                    "ext": str(f.get("ext") or ""),
                })

        if isinstance(attachment, dict):
            if attachment.get("url"):
                _pick(attachment)
            for f in attachment.get("files") or []:
                _pick(f)
        elif isinstance(attachment, list):
            for f in attachment:
                _pick(f)
        return out

    @staticmethod
    def _parse_connection_info(endpoints: Any) -> str:
        """
        解析平台 endpoints 字段，返回可连接的 host:port / URL 字符串。
        优先代理连接（isProxy=true），否则直连。
        """
        if not isinstance(endpoints, list) or not endpoints:
            return ""
        ep = endpoints[0]
        if not isinstance(ep, dict):
            return ""
        # 代理连接
        mappings = ep.get("portMappings") or []
        proxy_ips = ep.get("proxyIps") or []
        if mappings and proxy_ips:
            m = mappings[0]
            if isinstance(m, dict) and m.get("proxy"):
                return f"{proxy_ips[0]}:{m['proxy']}"
        # 直连
        ips = ep.get("exposeIps") or []
        ports = ep.get("ports") or []
        if ips and ports:
            return f"{ips[0]}:{ports[0]}"
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# 模块级便捷函数（无状态，每次创建临时 client）
# ─────────────────────────────────────────────────────────────────────────────

_skill_instance: Optional[CTFPlatformSkill] = None


def configure(
    api_base_url: str,
    access_key: str = "",
    **kwargs: Any,
) -> CTFPlatformSkill:
    """
    配置并返回全局 Skill 单例（Agent 启动时调用一次）。

    Args:
        api_base_url: 靶场平台根地址
        access_key:   AccessKey
        **kwargs:     透传给 CTFPlatformSkill 构造器

    Returns:
        配置好的 CTFPlatformSkill 实例
    """
    global _skill_instance
    _skill_instance = CTFPlatformSkill(api_base_url, access_key, **kwargs)
    return _skill_instance


def get_skill() -> CTFPlatformSkill:
    """获取全局 Skill 单例（需先调用 configure）。"""
    if _skill_instance is None:
        raise RuntimeError("CTFPlatformSkill 未初始化，请先调用 configure()")
    return _skill_instance


# 便捷函数（委托给全局单例）
async def list_challenges(light: bool = True) -> dict[str, Any]:
    """便捷函数：获取题目列表。"""
    return await get_skill().list_challenges(light=light)


async def get_challenge_detail(challenge_id: str | int) -> dict[str, Any]:
    """便捷函数：获取题目详情。"""
    return await get_skill().get_challenge_detail(challenge_id)


async def download_attachment(challenge_id: str | int, filename: str = "") -> dict[str, Any]:
    """便捷函数：下载附件。"""
    return await get_skill().download_attachment(challenge_id, filename)


async def start_container(challenge_id: str | int) -> dict[str, Any]:
    """便捷函数：启动容器。"""
    return await get_skill().start_container(challenge_id)


async def stop_container(challenge_id: str | int) -> dict[str, Any]:
    """便捷函数：停止容器。"""
    return await get_skill().stop_container(challenge_id)


async def get_container_status(challenge_id: str | int) -> dict[str, Any]:
    """便捷函数：查询容器状态。"""
    return await get_skill().get_container_status(challenge_id)


async def submit_flag(challenge_id: str | int, flag: str) -> dict[str, Any]:
    """便捷函数：提交 Flag。"""
    return await get_skill().submit_flag(challenge_id, flag)


async def get_match_info() -> dict[str, Any]:
    """便捷函数：获取竞赛信息。"""
    return await get_skill().get_match_info()


async def get_overview() -> dict[str, Any]:
    """便捷函数：获取排名概览。"""
    return await get_skill().get_overview()


async def list_notices() -> dict[str, Any]:
    """便捷函数：获取公告列表。"""
    return await get_skill().list_notices()


async def get_notice_detail(notice_id: str | int) -> dict[str, Any]:
    """便捷函数：获取公告详情。"""
    return await get_skill().get_notice_detail(notice_id)
