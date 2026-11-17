"""LangGraph 图：ReAct 循环。

    ┌──────────┐   有 tool_calls   ┌────────┐
    │  agent   │ ───────────────►  │ tools  │
    │ (LLM)    │ ◄───────────────  │ (执行) │
    └────┬─────┘      回填结果      └────────┘
         │ 无 tool_calls
         ▼
        END

为什么手写 StateGraph 而不用 create_react_agent：
1. 需要在执行工具前后向前端推事件（SSE），自己控边更直接；
2. 后续要插入人工确认节点、检索前置节点时，手写图更好扩展；
3. 面试时能讲清每一步的状态流转，而不是"调了个封装"。
"""

from __future__ import annotations

from typing import Annotated, Any, AsyncIterator, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langchain_core.messages import ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from app.agent.context import current_session_id
from app.agent.prompts import SYSTEM_PROMPT, history_to_messages
from app.agent.tools import ALL_TOOLS
from app.config import get_settings
from app.memory.redis_store import store
from app.safety import command_guard


class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]


def _build_llm():
    settings = get_settings()
    return ChatOpenAI(
        model=settings.llm_model,
        api_key=settings.dashscope_api_key,
        base_url=settings.llm_base_url,
        temperature=settings.llm_temperature,
        streaming=True,
        timeout=60,
        max_retries=1,
    )


def _build_graph():
    llm_with_tools = _build_llm().bind_tools(ALL_TOOLS)

    async def call_model(state: AgentState) -> dict[str, Any]:
        messages = [SystemMessage(content=SYSTEM_PROMPT), *state["messages"]]
        response = await llm_with_tools.ainvoke(messages)
        return {"messages": [response]}

    def should_continue(state: AgentState) -> str:
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            return "tools"
        return END

    builder = StateGraph(AgentState)
    builder.add_node("agent", call_model)
    builder.add_node("tools", ToolNode(ALL_TOOLS))
    builder.set_entry_point("agent")
    builder.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    builder.add_edge("tools", "agent")
    return builder.compile()


graph = _build_graph()


def _chunk_text(chunk: Any) -> str:
    """兼容 content 为 str / list 两种形态。"""
    content = getattr(chunk, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
            elif isinstance(item, str):
                parts.append(item)
        return "".join(parts)
    return ""


async def stream_chat(session_id: str, user_text: str) -> AsyncIterator[dict[str, Any]]:
    """执行一轮对话，边跑边产出事件。

    事件类型：
      tool_start / tool_end —— 工具调用轨迹（前端展示"它在查什么"）
      token                 —— 模型逐字输出
      done                  —— 本轮结束，附最终文本与待确认操作
    """
    settings = get_settings()
    session_token = current_session_id.set(session_id)

    history = await store.get_history(session_id)
    inputs: dict[str, Any] = {
        "messages": [*history_to_messages(history), HumanMessage(content=user_text)]
    }
    config = {"recursion_limit": settings.recursion_limit}

    collected: list[str] = []
    tool_calls_trace: list[dict[str, Any]] = []

    try:
        async for event in graph.astream_events(inputs, version="v2", config=config):
            kind = event.get("event")
            name = event.get("name", "")

            if kind == "on_chat_model_stream":
                piece = _chunk_text(event["data"].get("chunk"))
                if piece:
                    collected.append(piece)
                    yield {"type": "token", "text": piece}

            elif kind == "on_tool_start":
                payload = {
                    "type": "tool_start",
                    "name": name,
                    "input": event["data"].get("input", {}),
                }
                tool_calls_trace.append({"name": name, "input": payload["input"]})
                yield payload

            elif kind == "on_tool_end":
                output = event["data"].get("output")
                text = output.content if isinstance(output, ToolMessage) else str(output)
                yield {"type": "tool_end", "name": name, "output": text[:2000]}

    except Exception as exc:  # noqa: BLE001 —— 图执行异常要传给前端，而不是静默失败
        yield {"type": "error", "message": f"Agent 执行异常：{exc}"}
    finally:
        current_session_id.reset(session_token)

    answer = "".join(collected).strip()
    if answer:
        await store.append_turn(session_id, user_text, answer)

    pending = await store.list_pending(session_id)
    yield {
        "type": "done",
        "answer": answer,
        "tool_calls": tool_calls_trace,
        "pending_actions": pending,
    }


async def execute_action(action_id: str) -> dict[str, Any]:
    """执行用户已确认的变更操作。"""
    action = await store.get_action(action_id)
    if not action:
        return {"ok": False, "error": "操作不存在或已过期"}
    if action.get("status") != "pending":
        return {"ok": False, "error": f"操作状态为 {action.get('status')}，不可重复执行"}

    result = await command_guard.run(action["command"], timeout=30)
    status = "executed" if result.ok else "failed"
    await store.update_action(
        action_id,
        {"status": status, "exit_code": result.exit_code, "output": result.stdout[:2000],
         "error": result.error or result.stderr[:500]},
    )
    return {
        "ok": result.ok,
        "action_id": action_id,
        "command": action["command"],
        "status": status,
        "output": result.to_text(),
    }
