"""会话状态存储：对话历史、待确认操作。

优先用 Redis；连不上时自动降级为进程内字典（方便本机不开 Docker 直接跑通）。
生产环境务必用 Redis —— 多 worker 之间才能共享会话状态。
"""

from __future__ import annotations

import json
import time
from typing import Any

from app.config import get_settings

try:  # redis 未安装时也不影响本地演示
    import redis.asyncio as aioredis
except ImportError:  # pragma: no cover
    aioredis = None  # type: ignore[assignment]


class MemoryStore:
    def __init__(self) -> None:
        self._redis: Any = None
        self._local: dict[str, tuple[float, str]] = {}  # key -> (expire_at, json)
        self.backend = "memory"

    async def connect(self) -> None:
        settings = get_settings()
        if aioredis is None:
            return
        try:
            client = aioredis.from_url(
                settings.redis_url, encoding="utf-8", decode_responses=True
            )
            await client.ping()
            self._redis = client
            self.backend = "redis"
        except Exception:  # noqa: BLE001
            self._redis = None
            self.backend = "memory"

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()

    # ── 底层读写 ────────────────────────────────────────────
    async def _get(self, key: str) -> str | None:
        if self._redis is not None:
            return await self._redis.get(key)
        item = self._local.get(key)
        if not item:
            return None
        expire_at, value = item
        if expire_at and expire_at < time.time():
            self._local.pop(key, None)
            return None
        return value

    async def _set(self, key: str, value: str, ttl: int) -> None:
        if self._redis is not None:
            await self._redis.set(key, value, ex=ttl)
        else:
            self._local[key] = (time.time() + ttl, value)

    async def _delete(self, key: str) -> None:
        if self._redis is not None:
            await self._redis.delete(key)
        else:
            self._local.pop(key, None)

    # ── 对话历史 ────────────────────────────────────────────
    def _history_key(self, session_id: str) -> str:
        return f"chat:history:{session_id}"

    async def get_history(self, session_id: str) -> list[dict[str, str]]:
        raw = await self._get(self._history_key(session_id))
        if not raw:
            return []
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return []

    async def append_turn(self, session_id: str, user_text: str, ai_text: str) -> None:
        settings = get_settings()
        history = await self.get_history(session_id)
        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": ai_text})

        # 只保留最近 N 轮（1 轮 = user + assistant）
        max_items = settings.history_rounds * 2
        if len(history) > max_items:
            history = history[-max_items:]

        await self._set(
            self._history_key(session_id),
            json.dumps(history, ensure_ascii=False),
            settings.session_ttl,
        )

    async def reset(self, session_id: str) -> None:
        await self._delete(self._history_key(session_id))

    # ── 待确认的变更操作 ────────────────────────────────────
    def _action_key(self, action_id: str) -> str:
        return f"action:{action_id}"

    async def save_action(self, action: dict[str, Any], ttl: int = 900) -> None:
        await self._set(
            self._action_key(action["action_id"]),
            json.dumps(action, ensure_ascii=False),
            ttl,
        )
        if action.get("session_id"):
            index_key = f"action:pending:{action['session_id']}"
            ids = await self._get(index_key)
            id_list = json.loads(ids) if ids else []
            id_list.append(action["action_id"])
            await self._set(index_key, json.dumps(id_list), ttl)

    async def get_action(self, action_id: str) -> dict[str, Any] | None:
        raw = await self._get(self._action_key(action_id))
        return json.loads(raw) if raw else None

    async def update_action(self, action_id: str, patch: dict[str, Any]) -> None:
        action = await self.get_action(action_id)
        if not action:
            return
        action.update(patch)
        await self._set(
            self._action_key(action_id),
            json.dumps(action, ensure_ascii=False),
            get_settings().session_ttl,
        )

    async def list_pending(self, session_id: str) -> list[dict[str, Any]]:
        raw = await self._get(f"action:pending:{session_id}")
        if not raw:
            return []
        result = []
        for action_id in json.loads(raw):
            action = await self.get_action(action_id)
            if action and action.get("status") == "pending":
                result.append(action)
        return result


store = MemoryStore()
