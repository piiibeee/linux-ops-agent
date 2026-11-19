"""ReAct 循环防护的单元测试（纯函数，不依赖大模型与系统命令）。"""

from __future__ import annotations

from app.agent.loop_guard import (
    budget_exhausted_hint,
    call_signature,
    is_recursion_error,
    recursion_error_message,
)


def test_signature_is_key_order_insensitive():
    """参数顺序不同不算两次不同的调用——否则去重会失效。"""
    assert call_signature("read_journal", {"unit": "", "lines": 60}) == call_signature(
        "read_journal", {"lines": 60, "unit": ""}
    )


def test_signature_distinguishes_real_changes():
    """参数真的变了要能区分，不能把正常的多角度取证也拦掉。"""
    base = call_signature("read_journal", {"lines": 60})
    assert base != call_signature("read_journal", {"lines": 120})
    assert base != call_signature("get_disk_usage", {"lines": 60})


def test_signature_handles_none_and_weird_values():
    assert call_signature("get_disk_usage", None) == "get_disk_usage::{}"
    # 不可 JSON 序列化的值不能抛异常，退化成 repr 即可
    assert call_signature("x", {"obj": object()}).startswith("x::")


def test_recursion_error_detected_by_text():
    """LangGraph 各版本异常类名不稳定，靠文本也能认出来。"""
    exc = RuntimeError(
        "Recursion limit of 16 reached without hitting a stop condition."
    )
    assert is_recursion_error(exc) is True
    assert is_recursion_error(ValueError("模型调用超时")) is False


def test_user_facing_messages_are_chinese_and_actionable():
    hint = budget_exhausted_hint(6)
    assert "6" in hint
    assert "禁止再调用任何工具" in hint
    message = recursion_error_message()
    assert "上限" in message and "recursion" not in message.lower()
