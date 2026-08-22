"""
Mock CTF 靶场平台服务器 —— 仿西湖论剑 slab-match AI Agent API。

用途：本地开发 / 调试 / 演示，无需真实靶场平台即可联调 CTF-Agent。

协议完全对齐 slab-match：
  Base   : {host}:{port}/slab-match/api/v1/agent
  Auth   : Header  X-Agent-AccessKey: <access_key>
  响应   : 统一 JSON {code, message, data}，code=="00000" 成功

启动：
  python mock_server.py --port 8080 --access-key test-key-123
  或：uvicorn mock_server:app --host 0.0.0.0 --port 8080

然后在 CTF-Agent 的 .env 中：
  SLAB_ACCESS_KEY=test-key-123
  config.yaml platform.api_base_url=http://localhost:8080
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import time
from typing import Any, Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("mock-platform")

# ─────────────────────────────────────────────────────────────────────────────
# 配置
# ─────────────────────────────────────────────────────────────────────────────
ACCESS_KEY = "test-key-123"  # 可通过 CLI 参数覆盖
API_PREFIX = "/slab-match/api/v1/agent"
OK_CODE = "00000"

# ─────────────────────────────────────────────────────────────────────────────
# Mock 数据 —— 真实 CTF 题目结构（分类 → 子题 两级）
# ─────────────────────────────────────────────────────────────────────────────
MOCK_CHALLENGES: list[dict[str, Any]] = [
    {
        "id": "1001",
        "name": "入门 Web：SQL 注入",
        "category": "Web",
        "hasSolved": False,
        "value": 100,
        "description": "一个简单的登录页面，存在 SQL 注入漏洞。\n目标：绕过登录获取管理员权限。\n提示：用户名输入 `admin' OR '1'='1`，密码任意。",
        "isNeedInit": True,
        "isNeedCheck": False,
        "tags": ["SQL注入", "入门"],
        "files": [{"name": "web-sqli.zip", "url": "#", "size": "12KB"}],
        "flag": "DASCTF{mock_sql_injection_123}",
        "endpoints": [{"type": "http", "port": 8080, "path": "/"}],
    },
    {
        "id": "1002",
        "name": "Pwn：栈溢出基础",
        "category": "Pwn",
        "hasSolved": False,
        "value": 200,
        "description": "32位 ELF 程序，存在明显的栈溢出。\n目标：覆盖返回地址，跳转到后门函数。\n附件包含二进制和源码。",
        "isNeedInit": True,
        "isNeedCheck": False,
        "tags": ["栈溢出", "ret2text"],
        "files": [{"name": "pwn-stack.zip", "url": "#", "size": "45KB"}],
        "flag": "DASCTF{mock_stack_overflow_456}",
        "endpoints": [{"type": "tcp", "port": 10001, "host": "localhost"}],
    },
    {
        "id": "1003",
        "name": "Crypto：凯撒密码",
        "category": "Crypto",
        "hasSolved": True,
        "value": 50,
        "description": "经典凯撒密码，偏移量为 3。\n密文：GUR DHVPX OEBJA SBK WHZCF BIRE GUR YNML QBT。\n解密后提交 flag。",
        "isNeedInit": False,
        "isNeedCheck": False,
        "tags": ["凯撒密码", "古典密码"],
        "files": [],
        "flag": "DASCTF{mock_caesar_cipher_789}",
        "endpoints": [],
    },
    {
        "id": "1004",
        "name": "Reverse：简单 CrackMe",
        "category": "Reverse",
        "hasSolved": False,
        "value": 150,
        "description": "Windows CrackMe 程序，输入正确序列号即弹出成功。\n目标：逆向分析序列号验证逻辑。\n提示：使用 IDA 或 x64dbg。",
        "isNeedInit": False,
        "isNeedCheck": False,
        "tags": ["CrackMe", "静态分析"],
        "files": [{"name": "rev-crackme.exe", "url": "#", "size": "128KB"}],
        "flag": "DASCTF{mock_reverse_abc}",
        "endpoints": [],
    },
    {
        "id": "1005",
        "name": "Misc：隐写术",
        "category": "Misc",
        "hasSolved": False,
        "value": 80,
        "description": "一张 PNG 图片中隐藏了 flag。\n目标：使用隐写工具提取隐藏信息。\n提示：LSB 隐写。",
        "isNeedInit": False,
        "isNeedCheck": False,
        "tags": ["隐写", "LSB"],
        "files": [{"name": "misc-stego.png", "url": "#", "size": "256KB"}],
        "flag": "DASCTF{mock_steganography_def}",
        "endpoints": [],
    },
    {
        "id": "1006",
        "name": "Web：文件上传绕过",
        "category": "Web",
        "hasSolved": False,
        "value": 180,
        "description": "文件上传功能存在绕过，可上传 webshell。\n目标：上传 PHP webshell 并执行命令获取 flag。\n提示：修改 Content-Type 或使用 .phtml 后缀。",
        "isNeedInit": True,
        "isNeedCheck": False,
        "tags": ["文件上传", "webshell"],
        "files": [],
        "flag": "DASCTF{mock_file_upload_ghi}",
        "endpoints": [{"type": "http", "port": 8081, "path": "/upload"}],
    },
]

# 容器运行时状态（exerciseId -> state）
_container_state: dict[str, dict[str, Any]] = {}

# ─────────────────────────────────────────────────────────────────────────────
# FastAPI 应用
# ─────────────────────────────────────────────────────────────────────────────
app = FastAPI(title="Mock CTF Platform (slab-match compatible)", version="1.0.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)


def _ok(data: Any = None, message: str = "ok") -> dict[str, Any]:
    return {"code": OK_CODE, "message": message, "data": data}


def _fail(code: str, message: str) -> dict[str, Any]:
    return {"code": code, "message": message, "data": None}


def _verify_auth(x_agent_accesskey: Optional[str]) -> None:
    """校验 X-Agent-AccessKey。"""
    if not x_agent_accesskey:
        raise HTTPException(status_code=401, detail=_fail("AUTH_FAIL", "缺少 X-Agent-AccessKey"))
    if x_agent_accesskey != ACCESS_KEY:
        raise HTTPException(status_code=403, detail=_fail("AUTH_FAIL", "AccessKey 无效"))


def _find_challenge(cid: str) -> Optional[dict[str, Any]]:
    return next((c for c in MOCK_CHALLENGES if str(c["id"]) == str(cid)), None)


# ─────────────────────────────────────────────────────────────────────────────
# 接口实现
# ─────────────────────────────────────────────────────────────────────────────

@app.get(f"{API_PREFIX}/ctf/exercise-list")
async def exercise_list(x_agent_accesskey: Optional[str] = Header(None)):
    """题目列表（分类 → 子题 两级）。"""
    _verify_auth(x_agent_accesskey)
    logger.info("[exercise-list] 返回 %d 道题目", len(MOCK_CHALLENGES))
    # 按分类分组
    categories: dict[str, list[dict[str, Any]]] = {}
    for ch in MOCK_CHALLENGES:
        cat = ch["category"]
        categories.setdefault(cat, []).append({
            "id": ch["id"],
            "name": ch["name"],
            "hasSolved": ch["hasSolved"],
        })
    data = [{"id": f"cat-{i}", "name": cat, "corpus": items} for i, (cat, items) in enumerate(categories.items())]
    return _ok(data)


@app.get(f"{API_PREFIX}/ctf/exercise")
async def exercise_detail(exerciseId: str, x_agent_accesskey: Optional[str] = Header(None)):
    """题目详情（含附件/靶机信息）。"""
    _verify_auth(x_agent_accesskey)
    ch = _find_challenge(exerciseId)
    if not ch:
        return _fail("NOT_FOUND", f"题目 {exerciseId} 不存在")
    state = _container_state.get(exerciseId, {})
    logger.info("[exercise-detail] id=%s name=%s", exerciseId, ch["name"])
    return _ok({
        "id": ch["id"],
        "name": ch["name"],
        "category": ch["category"],
        "value": ch["value"],
        "description": ch["description"],
        "isNeedInit": ch["isNeedInit"],
        "isNeedCheck": state.get("status") == "starting",
        "tags": ch["tags"],
        "files": ch["files"],
        "endpoints": ch["endpoints"] if state.get("status") == "running" else [],
        "hasSolved": ch["hasSolved"],
    })


class BuildEnvReq(BaseModel):
    exerciseId: int


@app.post(f"{API_PREFIX}/ctf/build-exercise-env")
async def build_env(req: BuildEnvReq, x_agent_accesskey: Optional[str] = Header(None)):
    """启动容器环境（异步，立即返回，需轮询 exercise 接口查状态）。"""
    _verify_auth(x_agent_accesskey)
    cid = str(req.exerciseId)
    ch = _find_challenge(cid)
    if not ch:
        return _fail("NOT_FOUND", f"题目 {cid} 不存在")
    if not ch["isNeedInit"]:
        return _fail("NO_ENV", "该题目不需要容器环境")
    _container_state[cid] = {"status": "starting", "started_at": time.time()}
    logger.info("[build-env] id=%s 容器启动中", cid)
    # 模拟异步启动：2秒后变为 running
    asyncio.create_task(_simulate_start(cid))
    return _ok({"exerciseId": cid, "status": "starting", "message": "环境正在启动"})


async def _simulate_start(cid: str) -> None:
    await asyncio.sleep(2.0)
    if cid in _container_state:
        _container_state[cid]["status"] = "running"
        logger.info("[build-env] id=%s 容器已就绪", cid)


class RecoverEnvReq(BaseModel):
    exerciseId: int


@app.post(f"{API_PREFIX}/ctf/recover-exercise-env")
async def recover_env(req: RecoverEnvReq, x_agent_accesskey: Optional[str] = Header(None)):
    """回收容器环境。"""
    _verify_auth(x_agent_accesskey)
    cid = str(req.exerciseId)
    if cid in _container_state:
        del _container_state[cid]
    logger.info("[recover-env] id=%s 容器已回收", cid)
    return _ok({"exerciseId": cid, "status": "stopped", "message": "环境已回收"})


class SubmitReq(BaseModel):
    exerciseId: int
    flag: str


@app.post(f"{API_PREFIX}/answer-panel/answer")
async def submit_flag(req: SubmitReq, x_agent_accesskey: Optional[str] = Header(None)):
    """提交 Flag。"""
    _verify_auth(x_agent_accesskey)
    cid = str(req.exerciseId)
    ch = _find_challenge(cid)
    if not ch:
        return _fail("NOT_FOUND", f"题目 {cid} 不存在")
    is_correct = req.flag.strip() == ch["flag"]
    if is_correct:
        ch["hasSolved"] = True
        logger.info("[submit] id=%s flag 正确 ✓", cid)
    else:
        logger.info("[submit] id=%s flag 错误 ✗ (输入: %s)", cid, req.flag[:30])
    return _ok({
        "exerciseId": cid,
        "isCorrect": is_correct,
        "message": "回答正确！" if is_correct else "回答错误，请重试",
        "score": ch["value"] if is_correct else 0,
    })


@app.get(f"{API_PREFIX}/match/notice/match-info")
async def match_info(x_agent_accesskey: Optional[str] = Header(None)):
    """竞赛信息/规则。"""
    _verify_auth(x_agent_accesskey)
    return _ok({
        "title": "Mock CTF 测试赛",
        "startTime": "2026-08-01 09:00:00",
        "endTime": "2026-12-31 23:59:59",
        "rules": "1. 禁止攻击平台基础设施\n2. Flag 格式: DASCTF{...}\n3. 每题最多提交50次",
        "organizer": "Mock CTF Platform",
    })


@app.get(f"{API_PREFIX}/answer-panel/overview")
async def overview(x_agent_accesskey: Optional[str] = Header(None)):
    """得分排名概览。"""
    _verify_auth(x_agent_accesskey)
    solved = sum(1 for c in MOCK_CHALLENGES if c["hasSolved"])
    total_score = sum(c["value"] for c in MOCK_CHALLENGES if c["hasSolved"])
    return _ok({
        "rank": 1,
        "totalTeams": 5,
        "solvedCount": solved,
        "totalScore": total_score,
        "scoreList": [
            {"team": "MockTeam", "score": total_score, "solved": solved},
            {"team": "TeamB", "score": 100, "solved": 1},
            {"team": "TeamC", "score": 0, "solved": 0},
        ],
    })


@app.get(f"{API_PREFIX}/match/notice/now-list")
async def notice_list(x_agent_accesskey: Optional[str] = Header(None)):
    """公告列表。"""
    _verify_auth(x_agent_accesskey)
    return _ok([
        {"id": 1, "title": "欢迎参加 Mock CTF", "time": "2026-08-01 09:00:00", "type": "info"},
        {"id": 2, "title": "Pwn 题目环境已修复", "time": "2026-08-02 14:30:00", "type": "update"},
    ])


@app.get(f"{API_PREFIX}/match/notice/detail")
async def notice_detail(id: int, x_agent_accesskey: Optional[str] = Header(None)):
    """公告详情。"""
    _verify_auth(x_agent_accesskey)
    notices = {
        1: {"id": 1, "title": "欢迎参加 Mock CTF", "content": "这是一个本地 Mock 靶场，用于 CTF-Agent 开发测试。", "time": "2026-08-01 09:00:00"},
        2: {"id": 2, "title": "Pwn 题目环境已修复", "content": "Pwn 题目的容器环境已更新，现在可以正常启动。", "time": "2026-08-02 14:30:00"},
    }
    return _ok(notices.get(id, {}))


# 健康检查（无需鉴权）
@app.get("/health")
async def health():
    return {"status": "ok", "service": "mock-ctf-platform", "access_key_configured": bool(ACCESS_KEY)}


# ─────────────────────────────────────────────────────────────────────────────
# CLI 入口
# ─────────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Mock CTF Platform (slab-match compatible)")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址")
    parser.add_argument("--port", type=int, default=8080, help="监听端口")
    parser.add_argument("--access-key", default="test-key-123", help="X-Agent-AccessKey")
    args = parser.parse_args()

    global ACCESS_KEY
    ACCESS_KEY = args.access_key

    import uvicorn
    logger.info("=" * 60)
    logger.info("Mock CTF Platform 启动")
    logger.info("  API Base : http://%s:%d%s", args.host, args.port, API_PREFIX)
    logger.info("  AccessKey: %s", ACCESS_KEY)
    logger.info("  题目数   : %d", len(MOCK_CHALLENGES))
    logger.info("=" * 60)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
