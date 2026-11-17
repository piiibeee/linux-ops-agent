"""命令行调试入口：不启前端也能验证 Agent 是否跑通。

    python -m scripts.run_agent_cli
    python -m scripts.run_agent_cli --session demo1

用来做回归验证：固定几个问题，看工具选择与结论是否合理。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.agent.graph import execute_action, stream_chat  # noqa: E402
from app.memory.redis_store import store  # noqa: E402


async def once(session_id: str, question: str) -> None:
    print(f"\n{'=' * 60}\n你：{question}\n{'-' * 60}")
    answered = False
    async for event in stream_chat(session_id, question):
        if event["type"] == "token":
            print(event["text"], end="", flush=True)
            answered = True
        elif event["type"] == "tool_start":
            print(f"\n  [调用工具] {event['name']} {event['input']}")
        elif event["type"] == "done":
            if not answered:
                print(event.get("answer") or "(无输出)")
            print()
            for action in event.get("pending_actions", []):
                print(f"\n  [待确认操作] {action['command']}")
                print(f"  说明：{action['description']}")
                choice = input("  现在执行？(y/N) ").strip().lower()
                if choice == "y":
                    result = await execute_action(action["action_id"])
                    print(f"  执行结果：{result.get('output') or result.get('error')}")
                else:
                    print("  已跳过（操作保留在待确认队列中）")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", default=f"cli-{uuid.uuid4().hex[:6]}")
    args = parser.parse_args()

    await store.connect()
    print(f"会话 ID：{args.session}　存储后端：{store.backend}")
    print("输入问题后回车，输入 exit 退出。")

    while True:
        try:
            question = input("\n你：").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not question:
            continue
        if question.lower() in {"exit", "quit", "q"}:
            break
        await once(args.session, question)

    await store.close()


if __name__ == "__main__":
    asyncio.run(main())
