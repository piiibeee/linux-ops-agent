# 内存与 CPU

## 2.1 内存使用率高但系统正常

**现象**：`free -h` 显示 available 很低，但服务没有异常。

**判定要点**
- 不要看 `free` 列，要看 `available` 列。`free` 低是正常的，Linux 会把空闲内存用作 page cache，缓存可随时回收。
- `available` 低于总内存 10% 且 `swap` 开始被频繁使用，才是真正的内存压力。

**排查**
1. `free -h`：看 available 与 swap used。
2. `ps -eo pid,comm,rss --sort=-rss | head -15`：按实际物理内存排序，`rss` 才是真实占用，不要用 `vsz`（虚拟内存，Java 进程会虚高）。
3. `cat /proc/meminfo | grep -E "Slab|SReclaimable|Dirty"`：Slab 异常增长常见于内核对象泄漏。

**处置**：确认是业务进程合理占用则属于容量规划；确认是 page cache 占用则无需处理；确认是内核 Slab 泄漏则需要重启或升级内核。

## 2.2 OOM Killer 杀进程

**现象**：进程莫名消失；`dmesg` 出现 `Out of memory: Killed process`。

**排查**
1. `dmesg -T | grep -i -E "oom|killed process"` 找被杀进程名与占用内存。
2. `journalctl -k | grep -i oom` 交叉验证。
3. 该进程的 `RSS` 峰值是否超过容器/系统的限制（cgroup limit）。

**判定**：注意区分三种情况——进程真实内存泄漏；单次处理大数据量导致峰值超限；整机被其他进程挤爆（此时被杀的是"最胖的那个"而不是"有问题的那个"）。

**处置**
- 提高限制不是首选，先确认是否泄漏。
- 紧急情况可临时调低其他进程权重或迁移负载。
- 长期方案：为关键进程配置 `systemd` 的 `MemoryMax` 并加监控告警。

## 2.3 系统负载高但 CPU 使用率不高

**现象**：`uptime` 的 load average 很高（如 8.0），但 `top` 里 `%Cpu(s)` 的 us/sy 都不高。

**原因排查顺序**
1. **不可中断睡眠进程（D 状态）堆积**：`ps -eo stat,pid,comm | awk '$1 ~ /D/'`。这类进程在等 I/O，计入负载但不吃 CPU。这是最常被误判的情况。
2. 磁盘 I/O 瓶颈：配合 `iostat -x 1` 看 `%util`。
3. NFS/网络存储挂死：`mount | grep nfs`，检查存储侧连通性。

**判定要点**：load 高 + CPU 低 + 有大量 D 状态进程 = I/O 问题，不是 CPU 问题。此时扩容 CPU 无效。

## 2.4 CPU 使用率 100%

**排查**
1. `uptime` + `nproc` 对比：4 核机器 load 4.0 才刚到饱和。
2. `top -b -n1 | head -20` 找用户态占用最高的进程。
3. `pidstat -u 1 3` 或 `top -H -p <pid>` 找具体线程，定位到是哪个线程在空转。
4. 区分 `us`（用户态，业务计算）与 `sy`（内核态，系统调用/上下文切换）。`sy` 高通常意味着频繁系统调用或锁竞争。

**常见原因**：死循环、正则回溯爆炸、GC 频繁、加密计算、被挖矿（检查是否有异常外联）。

**处置**：确认是业务峰值则扩容或限流；确认是单进程异常则先摘流量再排查，必要时重启恢复。

## 2.5 swap 使用率持续升高

**现象**：`free -h` 中 swap used 持续增长，系统响应变慢。

**判定**：轻微使用 swap 是正常的；持续增长且 `si/so`（`vmstat 1`）非零，说明内存已经不够，正在频繁换页，性能会断崖式下降。

**处置**：优先找到内存增长源；临时缓解可调低 `vm.swappiness`（如 10）减少换出，但这是缓解不是解决。SSD 上频繁 swap 还会加速磨损。
