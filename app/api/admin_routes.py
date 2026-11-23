"""管理端接口：审计日志、变更审批、流程拓扑。

这三个接口对应 B 端的三个核心视图，和 /api/chat 的区别在于：
    /api/chat   面向"用 Agent 的人"——一问一答，流式
    本文件      面向"管 Agent 的人"——表格、状态、权限

拆成独立文件而不是塞进 routes.py：routes.py 是对话链路，改它风险高；
管理端是只读查询为主，分开后即使这里出问题也不会影响对话。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.agent.graph import execute_action
from app.audit.store import audit_store
from app.memory.redis_store import store

router = APIRouter(prefix="/api", tags=["admin"])


# ── 审计日志 ────────────────────────────────────────────
@router.get("/audit")
async def list_audit(
    page: int = 1,
    page_size: int = 20,
    tool: str | None = None,
    status: str | None = None,
    session_id: str | None = None,
    keyword: str | None = None,
) -> dict[str, Any]:
    """分页查询工具调用审计。

    参数按"页面实际会用的筛选项"设计：工具、状态、会话、关键字。
    没做时间范围筛选是因为单机场景数据量小，加了反而没人用；
    真上规模再加 start_ts / end_ts 两个参数即可，接口形状不用变。
    """
    page = max(1, page)
    page_size = min(max(1, page_size), 200)  # 上限防前端一次拉爆
    result = audit_store.query(
        page=page,
        page_size=page_size,
        tool=tool or None,
        status=status or None,
        session_id=session_id or None,
        keyword=keyword or None,
    )
    result["filters"] = audit_store.filters()
    return result


@router.get("/audit/stats")
async def audit_stats() -> dict[str, Any]:
    """概览卡片用的聚合数据：总量、今日、失败、拦截、平均耗时。"""
    return audit_store.stats()


# ── 变更审批 ────────────────────────────────────────────
@router.get("/actions")
async def list_actions(status: str | None = None) -> dict[str, Any]:
    """全部变更单（含已执行/失败），供审批列表与历史回溯。

    与 /api/actions/pending 的区别：那个只返回当前会话待确认的，给对话页用；
    这个返回全量，给管理端审批表用。
    """
    items = await store.list_actions(status=status or None)
    counts: dict[str, int] = {}
    for item in items:
        key = item.get("status", "unknown")
        counts[key] = counts.get(key, 0) + 1
    return {"items": items, "total": len(items), "counts": counts}


@router.post("/actions/{action_id}/approve")
async def approve_action(action_id: str, approver: str = "oncall") -> dict[str, Any]:
    """管理端审批入口：和对话页的 confirm 走同一个执行器，只是多记一条审计。

    权限校验特意放在前端做（按钮级），后端这里只记录"谁批的"——
    单机演示环境没有真实用户体系，硬造一个 RBAC 反而是假的；
    代码里把 approver 明确传进来，将来接真实登录只需换成从 token 里取。
    """
    action = await store.get_action(action_id)
    if not action:
        raise HTTPException(status_code=404, detail="操作不存在或已过期")
    if action.get("status") != "pending":
        raise HTTPException(status_code=400, detail=f"当前状态为 {action.get('status')}，不可重复审批")

    result = await execute_action(action_id)
    await audit_store.log_action_event(
        action_id=action_id,
        command=action.get("command", ""),
        actor=approver,
        status="approved" if result.get("ok") else "failed",
        detail=result.get("output") or result.get("error") or "",
    )
    return result


# ── 流程拓扑 ────────────────────────────────────────────
# 边的语义标签：运行时读出来的边只带 source/target（部分版本带 data），
# 这里补一层"人话标签"，让画布上的箭头能说清"什么条件下走这条"。
_EDGE_LABELS = {
    ("agent", "tools"): "有 tool_calls",
    ("agent", "finalize"): "工具轮数用尽",
    ("agent", "__end__"): "无工具调用",
    ("tools", "agent"): "回填观察结果",
    ("__start__", "agent"): "",
    ("finalize", "__end__"): "",
}


# 节点的显示名。运行时读出来的只是 node id（agent / __start__ ...），
# 这里统一翻成人话，让"实时读取"和"静态兜底"两条路径的画布长得一样。
_NODE_LABELS = {
    "__start__": "START",
    "__end__": "END",
    "agent": "agent（LLM 决策）",
    "tools": "tools（执行工具）",
    "finalize": "finalize（预算用尽收尾）",
}


def _node_label(node_id: str, fallback: str) -> str:
    return _NODE_LABELS.get(node_id, fallback)


def _edge_label(source: str, target: str, data: object) -> str:
    if isinstance(data, str) and data:
        return data
    if isinstance(data, dict):
        for key in ("label", "condition", "name"):
            if data.get(key):
                return str(data[key])
    return _EDGE_LABELS.get((source, target), "")


def _graph_from_runtime() -> dict[str, Any] | None:
    """从编译后的 LangGraph 里读真实拓扑，避免手写一份会和代码漂移的假图。"""
    try:
        from app.agent.graph import graph

        g = graph.get_graph()
    except Exception:  # noqa: BLE001 —— 取不到就退回静态描述，不让画布拖垮整个管理端
        return None

    try:
        nodes = []
        for node_id, node in g.nodes.items():
            nodes.append(
                {"id": node_id, "label": _node_label(node_id, getattr(node, "name", node_id))}
            )

        edges = []
        for edge in g.edges:
            edges.append(
                {
                    "source": edge.source,
                    "target": edge.target,
                    "conditional": bool(getattr(edge, "conditional", False)),
                    "label": _edge_label(edge.source, edge.target, getattr(edge, "data", None)),
                }
            )
        return {"nodes": nodes, "edges": edges, "source": "runtime"}
    except Exception:  # noqa: BLE001
        return None


# 静态兜底：与 app/agent/graph.py 的 builder 结构一一对应。
# 只有当运行时读取失败（比如 langgraph 版本变化改了内部结构）才会用到，
# 届时画布仍然能画出来，只是在 source 字段里标明是静态描述。
_STATIC_GRAPH = {
    "nodes": [
        {"id": "agent", "label": "agent（LLM 决策）"},
        {"id": "tools", "label": "tools（执行工具）"},
        {"id": "finalize", "label": "finalize（预算用尽收尾）"},
        {"id": "__start__", "label": "START"},
        {"id": "__end__", "label": "END"},
    ],
    "edges": [
        {"source": "__start__", "target": "agent", "conditional": False, "label": ""},
        {"source": "agent", "target": "tools", "conditional": True, "label": "有 tool_calls"},
        {"source": "agent", "target": "finalize", "conditional": True, "label": "预算用尽"},
        {"source": "agent", "target": "__end__", "conditional": True, "label": "无工具调用"},
        {"source": "tools", "target": "agent", "conditional": False, "label": "回填结果"},
        {"source": "finalize", "target": "__end__", "conditional": False, "label": ""},
    ],
    "source": "static",
}


@router.get("/graph")
async def graph_topology() -> dict[str, Any]:
    """返回 Agent 的节点与边，供前端画布渲染。"""
    data = _graph_from_runtime()
    if data and data["nodes"]:
        return data
    return _STATIC_GRAPH
