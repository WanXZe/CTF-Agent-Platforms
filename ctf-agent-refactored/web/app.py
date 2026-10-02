"""
FastAPI 应用入口。

职责：
  - 创建 FastAPI 实例
  - 挂载 API 路由
  - 托管前端静态文件（内网一键部署）
  - 注册全局异常处理器
  - 配置 CORS
  - 生命周期管理（启动/关闭）

Web 层只负责接收请求、返回响应，不写复杂业务。
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from config import Settings
from utils.exceptions import CTFAgentError
from web.routes import challenges_router, containers_router, platform_router

logger = logging.getLogger(__name__)

# 前端静态文件目录（与 main.py 同级的 frontend/）
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时初始化，关闭时释放资源。"""
    settings = Settings()
    from core.agent.solve_runner import reconcile_sessions, shutdown_tasks
    reconcile_sessions()
    logging.basicConfig(level=settings.logging_level)
    logger.info("CTF-Agent Web 启动: %s:%s", settings.web_host, settings.web_port)
    if FRONTEND_DIR.exists():
        logger.info("前端静态文件: %s", FRONTEND_DIR)
    yield
    await shutdown_tasks()
    # 关闭时释放所有平台适配器资源（默认 + 运行时切换的）
    from web.deps import _adapter_cache
    for adapter in _adapter_cache.values():
        await adapter.close()
    _adapter_cache.clear()
    logger.info("CTF-Agent Web 已关闭")


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用。"""
    settings = Settings()
    app = FastAPI(
        title="CTF-Agent API",
        description="CTF 智能体解题平台 —— 题目管理 / 容器控制 / Flag 提交",
        version="2.0.0",
        lifespan=lifespan,
    )

    # CORS（内网部署默认允许所有来源）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.web_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # API 路由
    app.include_router(challenges_router)
    app.include_router(containers_router)
    app.include_router(platform_router)

    # 全局异常处理器 —— 结构化错误响应
    @app.exception_handler(CTFAgentError)
    async def ctf_agent_error_handler(request: Request, exc: CTFAgentError) -> JSONResponse:
        return JSONResponse(status_code=400, content=exc.to_dict())

    @app.exception_handler(Exception)
    async def generic_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("未处理异常: %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": "internal_error", "message": str(exc), "details": {}},
        )

    # 健康检查
    @app.get("/api/health")
    async def health() -> dict:
        return {"success": True, "status": "ok", "service": "ctf-agent"}

    # 可用平台列表（前端侧边栏展示）
    @app.get("/api/platforms")
    async def platforms() -> dict:
        from web.deps import list_available_platforms
        return {"success": True, "data": list_available_platforms()}

    # 托管前端静态文件（内网一键部署：访问 http://host:port/ 即为控制台）
    if FRONTEND_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

        @app.get("/")
        async def index() -> FileResponse:
            return FileResponse(str(FRONTEND_DIR / "index.html"))

    return app
