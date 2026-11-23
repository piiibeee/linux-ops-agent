"""审计日志落库。

为什么单独做一个存储，而不是塞进 Redis 会话：
  会话是有 TTL 的（6 小时过期），但审计记录不能过期——"三小时前那条命令是谁批的"
  这类问题必须能回溯。所以审计用 append-only 的 JSONL 文件，进程重启不丢。

为什么用 JSONL 而不是数据库：
  单机、单写入者、按行追加，JSONL 是最小可用的形态；查询靠内存索引 + 线性扫描，
  10 万条以内完全够用。真上规模再换 SQLite —— 接口形状不变，替换成本很低。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

# 相对项目根目录：app/audit/store.py -> ../../logs
ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = ROOT / "logs"
LOG_FILE = LOG_DIR / "audit.jsonl"


class AuditStore:
    """一次工具调用 = 一条审计记录。

    记录分两个阶段写：start 时写一行 running，finish 时写一行终态。
    查询时按 id 合并，保留最后一条的状态 —— 这样即使进程在工具执行中崩掉，
    也能看到"有一条调用开始过但没结束"，这比丢掉记录更有价值。
    """

    def __init__(self, path: Path = LOG_FILE) -> None:
        self.path = path
        self._lock = asyncio.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # ── 写入 ────────────────────────────────────────────────
    async def _append(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False, default=str)
        async with self._lock:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")

    async def start(
        self,
        *,
        session_id: str,
        tool: str,
        args: dict[str, Any] | None = None,
        actor: str = "agent",
    ) -> str:
        record_id = uuid4().hex[:12]
        await self._append(
            {
                "id": record_id,
                "ts": time.time(),
                "stage": "start",
                "session_id": session_id,
                "tool": tool,
                "actor": actor,
                "args": _trim(args or {}, 600),
                "status": "running",
            }
        )
        return record_id

    async def finish(
        self,
        record_id: str,
        *,
        output: str = "",
        status: str = "ok",
        error: str = "",
        elapsed_ms: int | None = None,
    ) -> None:
        await self._append(
            {
                "id": record_id,
                "ts": time.time(),
                "stage": "end",
                "status": status,
                "output": _trim_text(output, 1200),
                "error": _trim_text(error, 400),
                "elapsed_ms": elapsed_ms,
            }
        )

    async def log_action_event(
        self, *, action_id: str, command: str, actor: str, status: str, detail: str = ""
    ) -> None:
        """变更操作的审批与执行也进审计 —— 这是 B 端"操作留痕"的核心。"""
        await self._append(
            {
                "id": uuid4().hex[:12],
                "ts": time.time(),
                "stage": "action",
                "session_id": "-",
                "tool": "change_action",
                "actor": actor,
                "args": {"action_id": action_id, "command": _trim_text(command, 300)},
                "status": status,
                "output": _trim_text(detail, 600),
            }
        )

    # ── 查询 ────────────────────────────────────────────────
    def _load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        merged: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue  # 坏行跳过，不让一行脏数据毁掉整次查询
                rid = rec.get("id", "")
                if rid not in merged:
                    merged[rid] = {}
                    order.append(rid)
                merged[rid].update(rec)
        return [merged[rid] for rid in order]

    def query(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        tool: str | None = None,
        status: str | None = None,
        session_id: str | None = None,
        keyword: str | None = None,
    ) -> dict[str, Any]:
        rows = self._load()

        if tool:
            rows = [r for r in rows if r.get("tool") == tool]
        if status:
            rows = [r for r in rows if r.get("status") == status]
        if session_id:
            rows = [r for r in rows if r.get("session_id") == session_id]
        if keyword:
            kw = keyword.lower()
            rows = [
                r
                for r in rows
                if kw in json.dumps(r.get("args", {}), ensure_ascii=False).lower()
                or kw in str(r.get("output", "")).lower()
            ]

        rows.sort(key=lambda r: r.get("ts", 0), reverse=True)  # 最新在前
        total = len(rows)
        start = max(0, (page - 1) * page_size)
        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": rows[start : start + page_size],
        }

    def filters(self) -> dict[str, Any]:
        rows = self._load()
        return {
            "tools": sorted({r.get("tool", "") for r in rows if r.get("tool")}),
            "statuses": sorted({r.get("status", "") for r in rows if r.get("status")}),
        }

    def stats(self) -> dict[str, Any]:
        rows = self._load()
        today = time.strftime("%Y-%m-%d")
        today_rows = [
            r for r in rows if time.strftime("%Y-%m-%d", time.localtime(r.get("ts", 0))) == today
        ]
        durations = [r["elapsed_ms"] for r in rows if isinstance(r.get("elapsed_ms"), int)]
        return {
            "total": len(rows),
            "today": len(today_rows),
            "failed": sum(1 for r in rows if r.get("status") == "error"),
            "blocked": sum(1 for r in rows if r.get("status") == "blocked"),
            "running": sum(1 for r in rows if r.get("status") == "running"),
            "avg_elapsed_ms": int(sum(durations) / len(durations)) if durations else 0,
        }


def _trim(mapping: dict[str, Any], limit: int) -> dict[str, Any]:
    """参数里可能有超长字符串（比如整个日志片段），落库前截断，避免单行过大。"""
    out: dict[str, Any] = {}
    for k, v in mapping.items():
        if isinstance(v, str) and len(v) > limit:
            out[k] = v[:limit] + f"...[截断，原长 {len(v)}]"
        else:
            out[k] = v
    return out


def _trim_text(text: str, limit: int) -> str:
    if text is None:
        return ""
    text = str(text)
    return text if len(text) <= limit else text[:limit] + f"...[截断，原长 {len(text)}]"


audit_store = AuditStore()
