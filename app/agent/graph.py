"""LangGraph 图：ReAct 循环。

    ┌──────────┐   有 tool_calls   ┌────────┐
    │  agent   │ ───────────────►  │ tools  │
    │ (LLM)    │ ◄───────────────  │ (执行) │
    └────┬─────┘      回填结果      └────────┘
         │ 无 tool_calls / 预算用完
         ▼
     END / finalize（不带工具，强制收尾作答）

为什么手写 StateGraph 而不用 create_react_agent：
1. 需要在执行工具前后向前端推事件（SSE），自己控边更直接；
2. 需要自己控制工具节点：去重、轮数预算、异常兜底（见 loop_guard.py）；
3. 面试时能讲清每一步的状态流转，而不是"调了个封装"。
"""

from __future__ import annotations

import json
import time
from typing import Annotated, Any, AsyncIterator, TypedDict
from uuid import uuid4

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages

from app.agent.context import current_session_id
from app.agent.loop_guard import (
    DUPLICATE_HINT,
    TOO_MANY_HINT,
    budget_exhausted_hint,
    call_signature,
    is_recursion_error,
    recursion_error_message,
)
from app.agent.prompts import SYSTEM_PROMPT, history_to_messages
from app.agent.tools import ALL_TOOLS
from app.audit.store import audit_store
from app.config import get_settings
from app.memory.redis_store import store
from app.safety import command_guard

TOOL_MAP: dict[str, BaseTool] = {t.name: t for t in ALL_TOOLS}


class AgentState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    # 已经执行过的工具轮数（一轮 = 一次 AIMessage 发起的全部调用）
    tool_rounds: int
    # 本轮已执行过的调用指纹，用于拦截重复调用
    seen_calls: dict[str, str]


def _build_llm(*, with_tools: bool):
    settings = get_settings()
    llm = ChatOpenAI(
        model=settings.llm_model,
        api_key=settings.dashscope_api_key,
        base_url=settings.llm_base_url,
        temperature=settings.llm_temperature,
        streaming=True,
        timeout=60,
        max_retries=1,
    )
    return llm.bind_tools(ALL_TOOLS) if with_tools else llm


def _build_graph():
    llm_with_tools = _build_llm(with_tools=True)
    # 收尾节点用不绑工具的同一个模型：结构上就不可能再发起工具调用
    llm_plain = _build_llm(with_tools=False)

    async def call_model(state: AgentState) -> dict[str, Any]:
        messages = [SystemMessage(content=SYSTEM_PROMPT), *state["messages"]]
        response = await llm_with_tools.ainvoke(messages)
        return {"messages": [response]}

    async def run_tools(state: AgentState) -> dict[str, Any]:
        """执行工具调用。

        与 ToolNode 的差别（也是这次踩坑换来的）：
        - 同参数重复调用只执行一次，回一条说明让模型换个方向，而不是无休止重试；
        - 单轮调用数超限时丢弃多余部分；
        - 工具抛异常转成 ToolMessage，不让整个图崩掉。
        """
        settings = get_settings()
        last = state["messages"][-1]
        seen = dict(state.get("seen_calls") or {})
        rounds = int(state.get("tool_rounds") or 0) + 1

        outputs: list[ToolMessage] = []
        for index, call in enumerate(last.tool_calls):  # type: ignore[union-attr]
            name = call.get("name", "")
            args = call.get("args") or {}
            call_id = call.get("id") or f"call_{uuid4().hex[:8]}"

            if index >= settings.max_calls_per_round:
                outputs.append(
                    ToolMessage(
                        content=TOO_MANY_HINT.format(limit=settings.max_calls_per_round),
                        tool_call_id=call_id,
                        name=name,
                    )
                )
                continue

            signature = call_signature(name, args)
            if signature in seen:
                outputs.append(
                    ToolMessage(
                        content=DUPLICATE_HINT.format(name=name),
                        tool_call_id=call_id,
                        name=name,
                    )
                )
                continue

            tool = TOOL_MAP.get(name)
            if tool is None:
                outputs.append(
                    ToolMessage(
                        content=f"[未知工具] {name} 不存在，可用工具：{list(TOOL_MAP)}",
                        tool_call_id=call_id,
                        name=name,
                    )
                )
                continue

            try:
                result = await tool.ainvoke(args)
            except Exception as exc:  # noqa: BLE001 —— 工具失败要变成"观察结果"，不能中断图
                result = f"[工具执行失败] {name}: {exc}"

            text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
            seen[signature] = text[:300]
            outputs.append(ToolMessage(content=text, tool_call_id=call_id, name=name))

        return {"messages": outputs, "tool_rounds": rounds, "seen_calls": seen}

    async def finalize(state: AgentState) -> dict[str, Any]:
        """预算用尽后的收尾：不带工具再问一次，强制它把话说完。"""
        settings = get_settings()
        messages: list[AnyMessage] = [SystemMessage(content=SYSTEM_PROMPT), *state["messages"]]

        # 关键细节：进入这里的最后一条是带 tool_calls 的 AIMessage，但预算已用尽、调用没执行。
        # OpenAI 兼容接口要求每个 tool_call 后面必须有对应的 tool 消息，否则直接 400。
        # 所以补一批"未执行"的 ToolMessage，既满足协议，也如实告诉模型这一步被跳过了。
        last = messages[-1]
        if isinstance(last, AIMessage) and last.tool_calls:
            for call in last.tool_calls:
                messages.append(
                    ToolMessage(
                        content="[未执行] 工具轮数预算已用尽，本次调用被跳过。请基于已有证据作答。",
                        tool_call_id=call.get("id") or f"skipped_{uuid4().hex[:8]}",
                        name=call.get("name", ""),
                    )
                )

        messages.append(SystemMessage(content=budget_exhausted_hint(settings.max_tool_rounds)))
        response = await llm_plain.ainvoke(messages)
        return {"messages": [response]}

    def should_continue(state: AgentState) -> str:
        last = state["messages"][-1]
        if not (isinstance(last, AIMessage) and last.tool_calls):
            return END
        if int(state.get("tool_rounds") or 0) >= get_settings().max_tool_rounds:
            return "finalize"
        return "tools"

    builder = StateGraph(AgentState)
    builder.add_node("agent", call_model)
    builder.add_node("tools", run_tools)
    builder.add_node("finalize", finalize)
    builder.set_entry_point("agent")
    builder.add_conditional_edges(
        "agent", should_continue, {"tools": "tools", "finalize": "finalize", END: END}
    )
    builder.add_edge("tools", "agent")
    builder.add_edge("finalize", END)
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


def _judge_tool_status(text: str) -> str:
    """把工具返回的文本归成三态，供审计页筛选。

    为什么用关键字判断而不是结构化返回：工具返回本来就是给模型看的自然语言/JSON，
    改成统一结构要动 10 个工具函数，收益不抵风险。关键字是近似，但它只影响
    "审计页上的一个状态标签"，不影响任何执行逻辑——用错也没代价。
    """
    if text.startswith("[工具执行失败]"):
        return "error"
    blocked_marks = ("未通过安全校验", "不在白名单", "已被拒绝", "拒绝执行", "拦截")
    if any(mark in text for mark in blocked_marks):
        return "blocked"
    return "ok"


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
        "messages": [*history_to_messages(history), HumanMessage(content=user_text)],
        "tool_rounds": 0,
        "seen_calls": {},
    }
    config = {"recursion_limit": settings.recursion_limit}

    collected: list[str] = []
    tool_calls_trace: list[dict[str, Any]] = []

    # 审计配对用：按工具名分组，记下 start 时的审计 id 与时间戳。
    # 为什么按名字分队列而不是按调用 id：on_tool_start / on_tool_end 事件里
    # 不带 tool_call_id（只有 name），只能靠"先进先出"配对；
    # 同一轮并发调同一个工具时，队列顺序与实际完成顺序可能错位，
    # 所以耗时是近似的——这点误差换成"记得下来且不会丢"是划算的。
    open_calls: dict[str, list[tuple[str, float]]] = {}

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
                audit_id = await audit_store.start(
                    session_id=session_id, tool=name, args=payload["input"]
                )
                open_calls.setdefault(name, []).append((audit_id, time.time()))
                yield payload

            elif kind == "on_tool_end":
                output = event["data"].get("output")
                text = output.content if isinstance(output, ToolMessage) else str(output)
                pending = open_calls.get(name) or []
                if pending:
                    audit_id, started_at = pending.pop(0)
                    await audit_store.finish(
                        audit_id,
                        output=text,
                        status=_judge_tool_status(text),
                        elapsed_ms=int((time.time() - started_at) * 1000),
                    )
                yield {"type": "tool_end", "name": name, "output": text[:2000]}

    except Exception as exc:  # noqa: BLE001 —— 图执行异常要传给前端，而不是静默失败
        # 撞到框架步数上限时给中文提示；其他异常保留原文便于排查
        message = recursion_error_message() if is_recursion_error(exc) else f"Agent 执行异常：{exc}"
        yield {"type": "error", "message": message}
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
    """执行用户已确认的变更操作。

    状态流转：pending → executing → executed / failed
    中间那一步"executing"是给管理端看的：审批界面能显示"正在执行"，
    而不是点完确认之后干等到有结果。
    """
    action = await store.get_action(action_id)
    if not action:
        return {"ok": False, "error": "操作不存在或已过期"}
    if action.get("status") != "pending":
        return {"ok": False, "error": f"操作状态为 {action.get('status')}，不可重复执行"}

    await store.update_action(action_id, {"status": "executing", "started_at": time.time()})

    result = await command_guard.run(action["command"], timeout=30)
    status = "executed" if result.ok else "failed"
    await store.update_action(
        action_id,
        {"status": status, "exit_code": result.exit_code, "output": result.stdout[:2000],
         "error": result.error or result.stderr[:500], "finished_at": time.time()},
    )
    await audit_store.log_action_event(
        action_id=action_id,
        command=action["command"],
        actor="system",
        status=status,
        detail=result.to_text(),
    )
    return {
        "ok": result.ok,
        "action_id": action_id,
        "command": action["command"],
        "status": status,
        "output": result.to_text(),
    }
