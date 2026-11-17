"""接口出入参模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=64, description="会话 ID，前端生成")
    message: str = Field(..., min_length=1, max_length=2000)


class ResetRequest(BaseModel):
    session_id: str


class ActionResult(BaseModel):
    ok: bool
    action_id: str | None = None
    command: str | None = None
    status: str | None = None
    output: str | None = None
    error: str | None = None
