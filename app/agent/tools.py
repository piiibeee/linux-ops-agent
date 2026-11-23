"""Agent 可调用的运维工具集。

分两类：
- 只读诊断工具：直接执行，返回结构化文本结果（对应 JD 的"AI 能力与运维工具链深度集成"）。
- 变更操作：不直接执行，只生成「待确认操作」，由前端/接口二次确认后才落地
  （对应 JD 的"复杂故障自动化处置"，同时保证人在回路）。

所有命令都经 safety.command_guard 白名单校验，模型无法绕过去拼危险命令。
"""

from __future__ import annotations

import json
import time
import re
import uuid

from langchain_core.tools import tool

from app.agent.context import current_session_id
from app.memory.redis_store import store
from app.rag.retriever import format_hits, search
from app.safety import command_guard

SAFE_NAME = re.compile(r"^[a-zA-Z0-9@._:-]{1,64}$")

# 可执行的变更操作注册表：动作名 → 命令模板。
# 只登记"范围明确、可回滚、影响可控"的操作，绝不提供任意命令执行入口。
ACTIONS: dict[str, dict[str, str]] = {
    "vacuum_journal": {
        # 按容量封顶而不是按时间窗：时间窗要人判断"几天合适"，容量才是硬约束，
        # 能真正防止日志把盘写满。这也是生产上更常用的做法。
        # /var/log/journal 下的归档属 root:systemd-journal，普通用户删不掉，所以带 sudo。
        # 安全性由两层保证：
        #   1) 命令白名单（command_guard 会剥掉 sudo 再校验 journalctl 本身）
        #   2) /etc/sudoers.d/ops-agent 只授权这一条命令，别的一律拒绝
        "template": "sudo journalctl --vacuum-size=16M",
        "description": "把 systemd journal 日志压缩到 16MB 以内，释放 /var/log 空间（需 sudoers 精确授权）",
        "needs_target": "false",
    },
    "restart_service": {
        "template": "systemctl restart {target}",
        "description": "重启指定的 systemd 服务（用于服务假死、端口未监听）",
        "needs_target": "true",
    },
}


async def _exec(command: str) -> str:
    result = await command_guard.run(command)
    return result.to_text()


async def _journal_usage() -> str:
    """journal 占用空间的单行摘要。

    踩过的坑：模型想知道"日志占了多少"，但工具里只有读日志正文的 read_journal，
    于是它反复调 read_journal 试图从正文里找容量，直到撞上 recursion_limit。
    工具缺失导致的行为异常，只能靠补工具解决，提示词约束不住。

    走 _exec 而不是直接 command_guard.run：这样这条命令同样会被
    tests/test_tools_commands.py 抓出来过一遍闸门，不会成为校验盲区。
    """
    text = await _exec("journalctl --disk-usage")
    if text.startswith("[执行失败]"):
        return "[未能采集] 查询 journal 占用失败：" + text[len("[执行失败]"):].strip()
    body = text.partition("\n")[2].strip()
    # 原输出形如：Archived and active journals take up 256.0M in the file system.
    return body.replace("\n", " ") if body else "[未能采集] journalctl --disk-usage 无输出"


# ─────────────────── df 输出判读（避免把正常现象当故障）───────────────────
# 这些文件系统类型容量固定或按需分配，df 里显示 100% 是正常状态。
# 注意：9p/drvfs（/mnt/c 等 Windows 盘映射）和 overlay（容器根）不在此列——
# 它们对应真实存储，写满会真的影响使用，必须保留在监控范围内。
_VIRTUAL_FS: frozenset[str] = frozenset(
    {
        "tmpfs", "devtmpfs", "squashfs", "ramfs", "none",
        "cgroup", "cgroup2", "efivarfs", "debugfs", "nsfs",
    }
)

# WSL / 容器 / snap 的系统挂载点，属只读镜像或宿主机映射，不该判为磁盘满
_SYSTEM_MOUNT_PREFIXES: tuple[str, ...] = (
    "/usr/src/linux-headers",
    "/usr/lib/wsl",
    "/mnt/wslg",
    "/mnt/wsl",
    "/snap/",
    "/sys/",
    "/proc/",
    "/run/",
)


def _split_mounts(raw: str, *, type_col: int | None, use_col: int, label: str) -> str:
    """把 df 原始输出拆成「可写文件系统」与「已忽略的虚拟挂载」两段，并标出真实风险。

    模型不该自己解析 df：只读镜像挂载显示 100% 时，它会判成磁盘满并建议清理，
    而那种挂载点根本不可写（真实踩过的误报）。所以在工具层就把语义做出来。
    """
    if raw.startswith("[执行失败]"):
        return raw
    _, _, body = raw.partition("\n")
    lines = [ln for ln in body.strip().splitlines() if ln.strip()]
    if not lines:
        return raw

    kept: list[str] = []
    ignored: list[str] = []
    risky: list[str] = []

    for ln in lines:
        parts = ln.split()
        # df -hT 是 7 列（含 Type），df -i 是 6 列（无 Type），用 use_col 反推最少列数
        if len(parts) < use_col + 2:
            kept.append(ln)                     # 表头或异常行，原样保留
            continue
        if parts[0] == "Filesystem":            # 表头
            kept.append(ln)
            continue
        target = " ".join(parts[use_col + 1:])
        fstype = parts[type_col].lower() if type_col is not None else ""
        if (fstype and fstype in _VIRTUAL_FS) or any(
            target.startswith(p) for p in _SYSTEM_MOUNT_PREFIXES
        ):
            ignored.append(f"{target}（{fstype or '虚拟挂载'}）")
            continue
        kept.append(ln)
        use = parts[use_col].rstrip("%")
        if use.isdigit() and int(use) >= 85:
            risky.append(f"{target} {parts[use_col]}")

    out = [f"【可写文件系统 · {label}】", *kept]
    if risky:
        out.append("【需关注】" + "；".join(risky))
    else:
        out.append("【需关注】无，可写文件系统均未达到 85%。")
    if ignored:
        out.append(
            "【已忽略的虚拟/只读挂载（容量固定，显示满 100% 属正常，不可作为磁盘满的依据）】"
            + "；".join(ignored)
        )
    return "\n".join(out)


# ─────────────────────────── 只读诊断工具 ───────────────────────────

@tool
async def get_disk_usage() -> str:
    """查看各挂载点的磁盘使用率、inode 使用率。用于排查"磁盘满""写不进去""日志暴涨"。
    返回值已区分为可写文件系统与虚拟/只读挂载，只读挂载显示 100% 不代表磁盘满。"""
    # 闸门禁止 `&&` 串联，所以分两次执行、在 Python 侧合并结果
    capacity = await _exec("df -hT")
    inodes = await _exec("df -i")
    journal = await _journal_usage()
    return (
        f"{_split_mounts(capacity, type_col=1, use_col=5, label='容量 df -hT')}\n\n"
        f"{_split_mounts(inodes, type_col=None, use_col=4, label='inode df -i')}\n\n"
        f"【日志占用 journalctl --disk-usage】\n{journal}"
    )


@tool
async def get_memory_usage() -> str:
    """查看内存与 swap 使用情况。用于排查 OOM、内存泄漏、系统变慢。"""
    return await _exec("free -h")


@tool
async def get_load_and_cpu() -> str:
    """查看系统负载、运行时长与 CPU 核数。用于判断是否 CPU 饱和或负载异常。"""
    # 同样不能用 `&&` 串联
    load = await _exec("uptime")
    cores = await _exec("nproc")
    return f"【负载与运行时长 uptime】\n{load}\n\n【CPU 核数 nproc】\n{cores}"


@tool
async def get_top_processes(limit: int = 10) -> str:
    """按 CPU 与内存占用列出前若干进程。用于定位"谁把机器吃满"。"""
    limit = max(3, min(int(limit), 30))
    return await _exec(f"ps -eo pid,ppid,user,pcpu,pmem,etime,cmd --sort=-pcpu | head -n {limit + 1}")


@tool
async def get_service_status(service: str) -> str:
    """查看某个 systemd 服务的运行状态与最近日志。service 为服务名，如 nginx、sshd。"""
    if not SAFE_NAME.match(service):
        return "[拒绝] 服务名包含非法字符"
    return await _exec(f"systemctl status {service} --no-pager -l")


@tool
async def get_listening_ports() -> str:
    """列出当前监听中的 TCP/UDP 端口及对应进程。用于排查"端口不通""服务没起来"。"""
    return await _exec("ss -tulnp")


@tool
async def ping_host(host: str, count: int = 3) -> str:
    """测试到目标主机的网络连通性。host 为 IP 或域名。"""
    if not SAFE_NAME.match(host):
        return "[拒绝] 主机名包含非法字符"
    count = max(1, min(int(count), 5))
    return await _exec(f"ping -c {count} {host}")


@tool
async def read_journal(unit: str = "", lines: int = 60, priority: str = "", usage_only: bool = False) -> str:
    """读取 systemd journal 日志。unit 为空则读全系统；priority 可填 err/warn 只看告警以上。
    usage_only=True 时只返回 journal 占用了多少磁盘、不读正文——想判断"要不要清理日志"时用它，
    不要为了找占用量反复读日志正文。返回结果头部始终带一行当前占用。"""
    usage = await _journal_usage()
    if usage_only:
        return f"【journal 磁盘占用】{usage}"

    lines = max(10, min(int(lines or 60), 200))
    cmd = f"journalctl --no-pager -n {lines}"
    if unit:
        if not SAFE_NAME.match(unit):
            return "[拒绝] unit 名包含非法字符"
        cmd += f" -u {unit}"
    if priority in {"emerg", "alert", "crit", "err", "warning", "warn"}:
        cmd += f" -p {priority}"
    return f"【journal 磁盘占用】{usage}\n{await _exec(cmd)}"


@tool
async def search_runbook(query: str) -> str:
    """检索运维知识库中的故障处置 SOP（按故障现象或错误关键词检索）。
    遇到不确定的故障、或不熟悉的报错，先调这个工具查处置手册，再给结论。"""
    docs = search(query)
    return format_hits(docs)


# ─────────────────────────── 变更操作 ───────────────────────────

@tool
async def propose_action(action: str, target: str = "", reason: str = "") -> str:
    """提交一个需要人工确认的变更操作。仅在诊断明确、且必须实际处置时才调用。
    可用 action：vacuum_journal（清理旧日志）、restart_service（重启服务，需传 target=服务名）。
    不要假设已经执行成功——必须等用户在界面上确认。"""
    spec = ACTIONS.get(action)
    if not spec:
        return json.dumps(
            {"ok": False, "error": f"未知操作 {action}，仅支持：{list(ACTIONS)}"},
            ensure_ascii=False,
        )

    if spec["needs_target"] == "true":
        if not target:
            return json.dumps({"ok": False, "error": f"操作 {action} 必须提供 target"}, ensure_ascii=False)
        if not SAFE_NAME.match(target):
            return json.dumps({"ok": False, "error": "target 包含非法字符"}, ensure_ascii=False)

    command = spec["template"].format(target=target)
    try:
        command_guard.validate(command)
    except command_guard.CommandRejected as exc:
        return json.dumps({"ok": False, "error": f"生成的操作未通过安全校验：{exc}"}, ensure_ascii=False)

    action_record = {
        "action_id": uuid.uuid4().hex[:12],
        # 时间戳供管理端审批列表排序与耗时统计用
        "created_at": time.time(),
        "session_id": current_session_id.get(),
        "action": action,
        "target": target,
        "reason": reason,
        "command": command,
        "description": spec["description"],
        "status": "pending",
    }
    await store.save_action(action_record)

    return json.dumps(
        {
            "ok": True,
            "requires_confirmation": True,
            "action_id": action_record["action_id"],
            "command": command,
            "description": spec["description"],
            "instruction": "已生成待确认操作。请在回复中说明该操作的影响范围，并告知用户需要点击确认后才会执行。",
        },
        ensure_ascii=False,
    )


READ_TOOLS = [
    get_disk_usage,
    get_memory_usage,
    get_load_and_cpu,
    get_top_processes,
    get_service_status,
    get_listening_ports,
    ping_host,
    read_journal,
    search_runbook,
]

ALL_TOOLS = [*READ_TOOLS, propose_action]
