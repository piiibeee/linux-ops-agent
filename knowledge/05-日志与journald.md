# 日志与 journald

## 5.1 journalctl 常用查法（按需组合）

| 目的 | 命令 |
|---|---|
| 某服务最近 100 行 | `journalctl -u <svc> -n 100 --no-pager` |
| 只看错误以上级别 | `journalctl -p err -b --no-pager` |
| 本次开机以来 | `journalctl -b --no-pager` |
| 指定时间段 | `journalctl --since "2026-11-16 09:00" --until "2026-11-16 10:00"` |
| 跟随实时输出 | `journalctl -u <svc> -f` |
| 按关键字过滤 | `journalctl -u <svc> | grep -i timeout` |
| 内核日志 | `journalctl -k` |
| 查看磁盘占用 | `journalctl --disk-usage` |

**判定要点**：`-p err` 会把 warning 也带上（systemd 的优先级是数字越小越严重），排查告警噪声时注意级别对应关系：0 emerg、1 alert、2 crit、3 err、4 warning、5 notice、6 info、7 debug。

## 5.2 journal 日志占用磁盘过大

**现象**：`/var/log/journal` 体积达到数 GB，根分区被挤占。

**排查**：`journalctl --disk-usage` 确认实际占用；`cat /etc/systemd/journald.conf | grep -E "SystemMaxUse|MaxRetentionSec"` 看是否配置了上限。

**处置**
1. 立即回收：`sudo journalctl --vacuum-size=16M`（按容量封顶）或 `journalctl --vacuum-time=7d`（保留 7 天）。
   - **优先按容量封顶**：时间窗要人判断"留几天合适"，容量才是硬约束；日志暴涨时 7 天内的量也可能把盘写满
     （实测 257M 全落在最近 7 天内，按时间窗只回收 24M，按容量封顶才真正腾出空间）。
   - `/var/log/journal` 下的归档属 `root:systemd-journal`，普通用户删不掉，需 sudo；
     生产上给 sudoers 精确授权这一条命令即可，不要 `NOPASSWD: ALL`。
   - 只回收已归档文件，正在写入的活动日志不受影响，`systemd-journald` 无需重启。
2. 长期治理：在 `journald.conf` 中设置 `SystemMaxUse=500M`、`MaxRetentionSec=2week`，然后 `systemctl restart systemd-journald`。
3. **注意**：清理是治标，日志暴涨的根因（某服务疯狂刷错误日志）必须单独排查。

## 5.3 日志文件被删但磁盘没释放

**现象**：`rm` 掉了大日志文件，`df` 显示空间没变。

**原因**：进程仍持有该文件的句柄（典型是应用以追加模式打开的日志文件）。

**处置**：`lsof +L1` 找到持有进程后重启该进程；正确做法是 `> /var/log/xxx.log` 清空内容而不是 `rm` 删除文件。

## 5.4 日志轮转配置与验证

**配置要点**
- `/etc/logrotate.d/<app>` 中定义 `daily`/`size`、`rotate N`、`compress`、`copytruncate`。
- 应用不支持重开日志文件时用 `copytruncate`（有极短时间窗口丢日志，可接受）。
- `postrotate` 中通知应用重开句柄（如 `kill -USR1`）。

**验证**：`logrotate -d /etc/logrotate.d/<app>` 干跑看配置是否正确；`logrotate -f` 强制轮转一次验证实际效果。**只看配置文件不验证是常见疏漏**。

## 5.5 日志中出现高频重复错误

**判定**：先用 `journalctl -u <svc> --no-pager | awk '{print $5,$6,$7}' | sort | uniq -c | sort -rn | head` 聚类，找出重复最多的错误模式，再决定查哪一条。

**处置原则**：不要逐条看日志，先聚类再定位。高频重复错误通常是同一个根因在循环触发（如连接池耗尽后每次请求都报错），修一个点能消掉几千条。
