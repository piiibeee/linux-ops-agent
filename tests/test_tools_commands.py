"""确保每个工具实际执行的命令都能通过自己的安全闸门。

这个测试的由来（真实踩过的坑）：
`get_disk_usage` 原本写成 `df -h && df -i`，`get_load_and_cpu` 写成
`uptime && nproc`，而闸门明确禁止 `&&`。结果模型正确调用了工具，
工具却被自己的闸门拒掉，只回一句「执行失败：包含不被允许的 shell 语法」。
这类自锁只有在真正运行时才会暴露，所以用测试把每条实际命令都过一遍闸门。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agent import tools as tools_module  # noqa: E402
from app.safety.command_guard import validate  # noqa: E402


def test_all_tool_commands_pass_guard(monkeypatch) -> None:
    """捕获所有工具真正下发的命令，逐条过闸门。"""
    captured: list[str] = []

    async def fake_exec(command: str) -> str:
        captured.append(command)
        return "[stub]"

    # 工具内部调用的是模块级 _exec，patch 模块属性即可拦截
    monkeypatch.setattr(tools_module, "_exec", fake_exec)

    async def exercise() -> None:
        await tools_module.get_disk_usage.ainvoke({})
        await tools_module.get_memory_usage.ainvoke({})
        await tools_module.get_load_and_cpu.ainvoke({})
        await tools_module.get_top_processes.ainvoke({"limit": 5})
        await tools_module.get_service_status.ainvoke({"service": "nginx"})
        await tools_module.get_listening_ports.ainvoke({})
        await tools_module.ping_host.ainvoke({"host": "127.0.0.1", "count": 1})
        await tools_module.read_journal.ainvoke(
            {"unit": "nginx", "lines": 20, "priority": "err"}
        )

    asyncio.run(exercise())

    assert captured, "没有捕获到任何命令，检查工具是否真的调用了 _exec"
    for command in captured:
        # 抛 CommandRejected 即说明该工具会被闸门拦下
        validate(command)


def test_tool_commands_are_readonly(monkeypatch) -> None:
    """诊断工具不应产生变更动作——只读是这套白名单的前提。"""
    captured: list[str] = []

    async def fake_exec(command: str) -> str:
        captured.append(command)
        return "[stub]"

    monkeypatch.setattr(tools_module, "_exec", fake_exec)

    async def exercise() -> None:
        await tools_module.get_disk_usage.ainvoke({})
        await tools_module.get_memory_usage.ainvoke({})
        await tools_module.get_load_and_cpu.ainvoke({})
        await tools_module.get_top_processes.ainvoke({"limit": 5})
        await tools_module.get_listening_ports.ainvoke({})
        await tools_module.read_journal.ainvoke({"lines": 20})

    asyncio.run(exercise())

    forbidden = ("restart", "stop", "start", "kill", "disable", "enable", "rm ", "mv ")
    for command in captured:
        lowered = command.lower()
        for word in forbidden:
            assert word not in lowered, f"只读工具里出现了变更动作：{command}"


def test_no_concatenation_in_tool_commands(monkeypatch) -> None:
    """显式禁止 && / ; 串联——闸门禁止它，工具也不该依赖它。"""
    captured: list[str] = []

    async def fake_exec(command: str) -> str:
        captured.append(command)
        return "[stub]"

    monkeypatch.setattr(tools_module, "_exec", fake_exec)

    async def exercise() -> None:
        await tools_module.get_disk_usage.ainvoke({})
        await tools_module.get_load_and_cpu.ainvoke({})

    asyncio.run(exercise())

    for command in captured:
        assert "&&" not in command, f"命令里出现了 &&：{command}"
        assert ";" not in command, f"命令里出现了 ;：{command}"
