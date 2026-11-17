"""应用入口。

启动： uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.memory.redis_store import store

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("ops-agent")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await store.connect()
    logger.info("会话存储后端：%s", store.backend)
    if store.backend == "memory":
        logger.warning("未连上 Redis，已降级为进程内存储（仅适合本机演示）")
    yield
    await store.close()


app = FastAPI(
    title="Linux 运维 Agent",
    description="基于 LangGraph + RAG 的服务器智能运维助手",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/")
async def root() -> dict:
    return {"service": "linux-ops-agent", "docs": "/docs", "health": "/api/health"}
