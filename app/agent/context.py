"""请求级上下文。

session_id 不能让模型去填（它可能编），用 contextvar 在请求入口注入，
工具内部直接读取，保证待确认操作一定挂到正确的会话上。
"""

from __future__ import annotations

from contextvars import ContextVar

current_session_id: ContextVar[str] = ContextVar("current_session_id", default="")
