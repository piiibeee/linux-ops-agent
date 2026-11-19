"""ReAct 循环的防护逻辑（纯函数，便于单测）。

为什么需要它：
模型在"想知道 journal 占多少空间"时，如果没有对应工具，会反复调用 read_journal
试图从日志正文里找答案，直到撞上 LangGraph 的 recursion_limit 报错退出。
提示词里写"同一工具不要超过两次"约束不住这种情况，所以必须由代码兜底：

1. 同一轮里"同名 + 同参数"的调用只执行一次，重复的直接回一条说明（去重）；
2. 工具轮数有预算，超了就强制进入收尾节点，用已有证据作答（预算）；
3. 真的撞上 recursion_limit 时，翻译成人话而不是把英文栈信息抛给用户（兜底）。
"""

from __future__ import annotations

import json
from typing import Any

# 重复调用时回给模型的内容：既说明"不再执行"，也给出下一步该怎么做
DUPLICATE_HINT = (
    "[重复调用已拦截] 本轮已经用完全相同的参数调用过 {name}，结果与上一条返回一致，"
    "不再重复执行。请直接基于已有结果作答；如果确实还缺数据，请换一个工具，"
    "或明确写「该项数据未能采集到」。"
)

# 单轮工具调用数量上限：防止模型一次性发起十几个并行调用把上下文撑爆
TOO_MANY_HINT = (
    "[已跳过] 单轮工具调用数量超过 {limit} 个，本次调用未执行。"
    "下一轮请只保留最必要的两三个调用。"
)


def call_signature(name: str, args: dict[str, Any] | None) -> str:
    """生成"工具名 + 规范化参数"的指纹，用于判断是否重复调用。

    参数按 key 排序，避免 {"a":1,"b":2} 和 {"b":2,"a":1} 被当成两次不同调用。
    """
    try:
        normalized = json.dumps(args or {}, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        normalized = repr(args)
    return f"{name}::{normalized}"


def budget_exhausted_hint(rounds: int) -> str:
    """预算耗尽时追加到模型上下文里的硬约束。"""
    return (
        f"【系统约束】本轮工具调用已达到上限（{rounds} 轮）。"
        "禁止再调用任何工具，请立刻基于已经拿到的证据给出诊断结论。"
        "没有采集到的数据，直接写「未能采集到」并说明需要什么权限或工具补齐，不要猜测数值。"
    )


def is_recursion_error(exc: BaseException) -> bool:
    """判断是否为 LangGraph 的 recursion_limit 异常。

    不同小版本的异常类名不一样（GraphRecursionError / 消息文本），
    所以既看类名也看文本，避免版本升级后判断失效。
    """
    if type(exc).__name__ == "GraphRecursionError":
        return True
    return "recursion limit" in str(exc).lower()


def recursion_error_message() -> str:
    """给用户看的中文提示，不带英文栈信息。"""
    return (
        "本轮工具调用步数达到上限，已停止继续尝试。"
        "可以换一种更具体的问法（例如直接说「看一下 journal 占用」），"
        "或点右上角「清空会话」后重试。"
    )
