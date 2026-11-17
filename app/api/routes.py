"""HTTP 接口层。

SSE 用 POST + StreamingResponse 实现：EventSource 只支持 GET，
而对话需要 POST 传参，所以前端用 fetch + ReadableStream 解析。
"""

from __future__ import annotations

import json
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.agent.graph import execute_action, stream_chat
from app.config import get_settings
from app.memory.redis_store import store
from app.models.schemas import ChatRequest, ResetRequest
from app.rag.retriever import format_hits, search
from app.safety.command_guard import ALLOWED_BINARIES

router = APIRouter(prefix="/api")


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.get("/health")
async def health() -> dict:
    settings = get_settings()
    return {
        "status": "ok",
        "memory_backend": store.backend,
        "llm_model": settings.llm_model,
        "allow_execute": settings.allow_execute,
        "allowed_binaries": len(ALLOWED_BINARIES),
    }


@router.post("/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    async def event_stream() -> AsyncIterator[str]:
        try:
            async for event in stream_chat(req.session_id, req.message):
                yield _sse(event["type"], event)
        except Exception as exc:  # noqa: BLE001
            yield _sse("error", {"type": "error", "message": str(exc)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 关掉 nginx 缓冲，否则流式会攒着一起吐
        },
    )


@router.post("/session/reset")
async def reset_session(req: ResetRequest) -> dict:
    await store.reset(req.session_id)
    return {"ok": True}


@router.get("/actions/pending")
async def pending_actions(session_id: str) -> dict:
    return {"items": await store.list_pending(session_id)}


@router.post("/actions/{action_id}/confirm")
async def confirm_action(action_id: str) -> dict:
    result = await execute_action(action_id)
    if not result["ok"] and result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.get("/knowledge/search")
async def knowledge_search(q: str, k: int = 4) -> dict:
    """知识库检索调试接口：用来核对"某个问题能不能命中对应手册"。"""
    docs = search(q, k=k)
    return {
        "query": q,
        "hits": [
            {"citation": d.metadata.get("citation"), "content": d.page_content[:400]}
            for d in docs
        ],
        "preview": format_hits(docs),
    }
