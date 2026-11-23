"""流程拓扑接口的回归测试。

这份测试守的是**"图和代码不许漂移"**这条线：
/api/graph 优先从编译后的 LangGraph 读拓扑，读不到才回退静态描述。
如果哪天有人改了 graph.py 的节点或连线，却忘了改 _STATIC_GRAPH，
这个测试就会挂——这比等面试官在页面上发现"画布和实际行为不一致"要好。
"""

from __future__ import annotations

from app.api.admin_routes import _STATIC_GRAPH, _graph_from_runtime


def _edge_set(data: dict) -> set[tuple[str, str]]:
    return {(e["source"], e["target"]) for e in data["edges"]}


def test_runtime_topology_is_readable() -> None:
    """运行时能读到拓扑，且标明了来源是 runtime。"""
    data = _graph_from_runtime()
    assert data is not None, "运行时读取失败，画布会退回静态描述"
    assert data["source"] == "runtime"
    assert data["nodes"] and data["edges"]


def test_runtime_matches_static_fallback() -> None:
    """静态兜底不许写成另一张图。"""
    data = _graph_from_runtime()
    assert data is not None
    assert _edge_set(data) == _edge_set(_STATIC_GRAPH)


def test_every_node_has_human_label() -> None:
    """节点显示名不能漏出 __start__ / agent 这类原始 id。"""
    data = _graph_from_runtime()
    assert data is not None
    for node in data["nodes"]:
        assert node["label"], f"节点 {node['id']} 没有显示名"
        assert node["label"] != node["id"] or node["id"] in {"agent", "tools"}, (
            f"节点 {node['id']} 的显示名还是原始 id"
        )


def test_conditional_edges_are_marked() -> None:
    """条件边必须标出来，否则画布上分不清顺序边和分支边。"""
    data = _graph_from_runtime()
    assert data is not None
    conditional = {(e["source"], e["target"]) for e in data["edges"] if e["conditional"]}
    assert ("agent", "tools") in conditional
    assert ("agent", "__end__") in conditional
