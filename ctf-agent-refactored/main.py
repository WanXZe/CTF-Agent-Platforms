"""
CTF-Agent 主入口（重构版）。

分层架构：
  config/      配置层 —— 统一 Settings
  core/
    agent/     Agent 大模型推理层 —— Coordinator / SolveSession
    skills/    Skill 能力插件层 —— PlatformSkillAdapter / SkillRegistry
    tools/     工具调用层 —— ToolRegistry / platform_tools
    models/    数据模型层 —— Challenge / ContainerInfo / ToolResult
  web/         Web 接口层 —— FastAPI 路由 / Schema / 全局异常
  utils/       工具层 —— 异常定义

启动：
  python main.py                  # 启动 Web API
  python main.py --solve          # 启动解题引擎
  uvicorn web.app:create_app --factory  # 仅 Web
"""

from __future__ import annotations

import argparse
import os
import sys


def _load_env_file() -> None:
    """加载项目根目录 .env 到 os.environ，让 platform skill 能读到密钥。"""
    from pathlib import Path
    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.exists():
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


_load_env_file()


def main() -> None:
    parser = argparse.ArgumentParser(description="CTF-Agent 智能解题平台（重构版）")
    parser.add_argument("--solve", action="store_true", help="启动解题引擎")
    parser.add_argument("--challenge", default=None, help="仅解单题（题目 ID）")
    parser.add_argument("--host", default=None, help="Web 监听地址")
    parser.add_argument("--port", type=int, default=None, help="Web 监听端口")
    parser.add_argument("-v", "--verbose", action="store_true", help="详细日志")
    args = parser.parse_args()

    import logging
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    if args.solve:
        _run_solver(args)
    else:
        _run_web(args)


def _run_web(args: argparse.Namespace) -> None:
    """启动 Web API 服务。"""
    import uvicorn
    from config import Settings
    settings = Settings()
    host = args.host or settings.web_host
    port = args.port or settings.web_port
    print(f"🚀 CTF-Agent Web API 启动: http://{host}:{port}")
    print(f"📖 API 文档: http://{host}:{port}/docs")
    uvicorn.run("web.app:create_app", factory=True, host=host, port=port, reload=False)


def _run_solver(args: argparse.Namespace) -> None:
    """启动解题引擎。"""
    import asyncio
    from config import Settings
    from core.agent import Coordinator
    from core.skills import PlatformSkillAdapter
    from core.tools import build_platform_tools

    async def _run() -> None:
        settings = Settings()
        adapter = PlatformSkillAdapter(settings)
        tools = build_platform_tools(adapter)
        coordinator = Coordinator(settings, tools)

        if args.challenge:
            challenge = await adapter.get_challenge(args.challenge)
            print(f"开始解题: {challenge.name}")
            result = await coordinator.solve_challenge(challenge)
            print(f"结果: {result}")
        else:
            challenges = await adapter.list_challenges(light=True)
            print(f"共 {len(challenges)} 道题目，开始并发解题...")
            tasks = [coordinator.solve_challenge(c) for c in challenges if not c.solved]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            solved = sum(1 for r in results if isinstance(r, dict) and r.get("status") == "solved")
            print(f"解题完成: 成功 {solved}/{len(tasks)}")

        await adapter.close()

    asyncio.run(_run())


if __name__ == "__main__":
    main()
