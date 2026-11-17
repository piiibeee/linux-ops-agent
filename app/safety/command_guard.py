"""命令执行的安全闸门。

设计原则（面试可直接讲）：
1. 白名单优先 —— 只允许明确列出的可执行文件，未知一律拒绝，而不是"黑名单拦已知危险命令"。
2. 逐段校验 —— 允许管道 `|`，但必须拆开后**每一段**的首个 token 都在白名单内。
3. 语义型危险字符直接禁 —— `;` `&&` `||` 重定向、命令替换、换行一律不放行，
   从根本上避免"合法命令 + 注入的第二条命令"。
4. 执行与校验分离 —— validate() 只判断，run() 只执行；任何执行前必须过 validate()。
"""

from __future__ import annotations

import asyncio
import re
import shlex
from dataclasses import dataclass, field


class CommandRejected(Exception):
    """命令未通过安全校验。"""


# 允许执行的二进制（全部为系统自带只读诊断工具 + 少量受控变更操作）
ALLOWED_BINARIES: frozenset[str] = frozenset(
    {
        # 磁盘 / 内存 / CPU
        "df", "du", "free", "vmstat", "uptime", "lscpu", "lsblk", "iostat", "mpstat", "nproc",
        # 进程 / 服务
        "ps", "top", "systemctl", "service",
        # 网络
        "ss", "netstat", "ip", "ping", "dig", "nslookup", "curl", "traceroute",
        # 日志
        "journalctl", "dmesg", "tail", "head", "cat", "grep", "awk", "sed", "wc", "sort", "uniq",
        # 其他
        "who", "last", "find", "date", "hostname", "uname", "echo",
    }
)

# 任何位置出现即拒绝的片段（大小写不敏感）
FORBIDDEN_FRAGMENTS: tuple[str, ...] = (
    "rm -rf /", "rm -fr /", "rm -rf ~", "mkfs", "dd if=", "dd of=",
    "shutdown", "reboot", "poweroff", "init 0", "init 6",
    "> /dev/sd", "> /etc/", "chmod -R 777 /", "chown -R",
    ":(){", "fork bomb", "truncate -s 0 /",
    "systemctl disable", "systemctl mask",
)

# 命令替换 / 串联 / 重定向：不做语法分析，直接拒
FORBIDDEN_SHELL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"[;&]"),          # 命令分隔、后台执行
    re.compile(r"\|\|"),          # 短路或（上面已覆盖 &，此处显式表达意图）
    re.compile(r"(?<![0-9])>"),   # 输出重定向（排除 2>/dev/null 这类误伤需逐段判断，这里从严）
    re.compile(r"<"),
    re.compile(r"`"),             # 反引号命令替换
    re.compile(r"\$\("),          # $() 命令替换
    re.compile(r"\$\{"),
    re.compile(r"\n|\r"),
)


@dataclass
class CommandResult:
    ok: bool
    command: str
    exit_code: int = -1
    stdout: str = ""
    stderr: str = ""
    truncated: bool = False
    error: str | None = field(default=None)

    def to_text(self) -> str:
        """给模型看的纯文本结果。"""
        if self.error:
            return f"[执行失败] {self.error}"
        body = self.stdout.strip() or self.stderr.strip() or "(无输出)"
        if self.truncated:
            body += "\n……（输出过长已截断）"
        return f"$ {self.command}\n{body}"


def _first_token(segment: str) -> str:
    segment = segment.strip()
    if not segment:
        return ""
    try:
        parts = shlex.split(segment)
    except ValueError as exc:
        raise CommandRejected(f"命令无法解析：{exc}") from exc
    if not parts:
        return ""
    # 允许 sudo 前缀（容器内常以 root 运行，保留兼容）
    return parts[1] if parts[0] == "sudo" and len(parts) > 1 else parts[0]


def validate(command: str) -> None:
    """校验命令，不通过则抛 CommandRejected。"""
    if not command or not command.strip():
        raise CommandRejected("命令为空")

    lowered = command.lower().replace("\t", " ")
    for frag in FORBIDDEN_FRAGMENTS:
        if frag in lowered:
            raise CommandRejected(f"命中禁止片段：{frag}")

    # 先按管道拆段，再逐段检查危险字符
    segments = command.split("|")
    for segment in segments:
        for pattern in FORBIDDEN_SHELL_PATTERNS:
            if pattern.search(segment):
                raise CommandRejected(f"包含不被允许的 shell 语法：{pattern.pattern}")

        binary = _first_token(segment)
        if not binary:
            raise CommandRejected("存在空管道段")
        if binary not in ALLOWED_BINARIES:
            raise CommandRejected(
                f"命令 `{binary}` 不在白名单内（可用范围：只读诊断类系统命令）"
            )


async def run(command: str, timeout: int | None = None) -> CommandResult:
    """校验通过后执行，永不抛异常，统一用 CommandResult 返回。"""
    from app.config import get_settings  # 延迟导入：validate() 可以脱离配置独立使用/测试

    settings = get_settings()

    try:
        validate(command)
    except CommandRejected as exc:
        return CommandResult(ok=False, command=command, error=str(exc))

    if not settings.allow_execute:
        return CommandResult(
            ok=False,
            command=command,
            error="当前为只读演示模式（ALLOW_EXECUTE=false），未实际执行命令",
        )

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(), timeout=timeout or settings.cmd_timeout
        )
    except asyncio.TimeoutError:
        return CommandResult(
            ok=False, command=command, error=f"执行超时（>{timeout or settings.cmd_timeout}s）"
        )
    except Exception as exc:  # noqa: BLE001 —— 边界处兜底，保证 Agent 不因工具崩溃而中断
        return CommandResult(ok=False, command=command, error=f"执行异常：{exc}")

    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")

    limit = settings.max_output_chars
    truncated = len(stdout) > limit
    if truncated:
        stdout = stdout[:limit]

    return CommandResult(
        ok=proc.returncode == 0,
        command=command,
        exit_code=proc.returncode or 0,
        stdout=stdout,
        stderr=stderr[:1000],
        truncated=truncated,
    )
