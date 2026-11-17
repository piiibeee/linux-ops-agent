# 服务与 systemd

## 3.1 服务启动失败

**排查顺序（从外到内）**

1. `systemctl status <svc> --no-pager -l`：看 `Active` 状态与最近几行日志，多数情况这里就有答案。
2. `journalctl -u <svc> -n 100 --no-pager`：看完整启动日志，重点找第一个 ERROR。
3. `systemctl cat <svc>`：确认 `ExecStart` 路径、`User`、`WorkingDirectory`、`EnvironmentFile` 是否正确。
4. 常见失败类型：
   - `status=203/EXEC` → 可执行文件不存在或没有执行权限。
   - `status=200/CHDIR` → 工作目录不存在。
   - `status=1/FAILURE` → 应用自身报错，回日志找原因。
   - `status=226/NAMESPACE` → 沙箱/权限配置问题。
   - 端口被占用 → 结合 `ss -tulnp` 确认。

**注意**：`systemctl start` 成功不等于服务可用，必须再验证端口监听与健康检查接口。

## 3.2 服务频繁自动重启

**现象**：`systemctl status` 显示 `restarted` 计数很高；`NRestarts` 持续增长。

**排查**
1. `systemctl show <svc> -p NRestarts -p Restart -p RestartSec`：确认重启策略与次数。
2. 抓取崩溃瞬间的日志：`journalctl -u <svc> --since "10 min ago" -p err`。
3. 判断是进程崩溃（exit code 非 0）还是被 OOM 杀（`dmesg | grep -i oom`）。

**处置**：先修根因，**不要简单改成 `Restart=no` 掩盖问题**。频繁重启会掩盖真实故障，也会拖垮依赖它的上游。

## 3.3 服务假死（进程在但无响应）

**现象**：`systemctl status` 显示 `active (running)`，但请求超时；端口仍在监听但不返回数据。

**判定要点**：这是最容易误判的一类。进程存活 ≠ 服务可用。

**排查**
1. `ss -tulnp | grep <port>` 确认端口在监听。
2. `curl -m 5 http://127.0.0.1:<port>/health` 看应用层是否响应。
3. 用 `gdb` 或 `jstack`（Java）抓线程栈，判断是否死锁或卡在某个 I/O 上。
4. 检查连接数是否打满：`ss -s`、`netstat -an | grep -c ESTABLISHED`。

**处置**：确认假死后重启服务恢复可用，同时保留线程栈用于事后分析。长期方案是补上健康检查与自动摘流。

## 3.4 修改配置后 reload 还是 restart

**判定原则**
- 支持 `reload` 的服务（nginx、systemd 自身、部分数据库）优先 reload，不中断连接。
- 修改了监听端口、运行用户、依赖关系时，必须 `restart`。
- systemd unit 文件本身被修改后，必须先 `systemctl daemon-reload`，否则改动不生效——这是最常见的"改了没反应"。

**验证**：reload 后必须确认配置真的生效（查进程的配置加载时间或打一条测试请求），不能只看命令返回 0。

## 3.5 开机自启失效

**排查**
1. `systemctl is-enabled <svc>` 看是否 enabled。
2. `systemctl list-dependencies multi-user.target | grep <svc>` 确认挂到了哪个 target。
3. 服务在启动时依赖的网络/磁盘尚未就绪：加 `After=network-online.target` 与 `Wants=network-online.target`。
4. 检查是否有 `ConditionPathExists` 等条件导致跳过启动。

## 3.6 定时任务未执行

**排查**
1. `systemctl status cron`（或 `crond`）确认调度器本身在跑。
2. `journalctl -u cron -n 50` 看是否有执行记录与报错。
3. 手动用任务所属用户执行一遍脚本，排除环境变量缺失（cron 的 PATH 与登录 shell 不同，这是最高频原因）。
4. 检查脚本内的相对路径——cron 的工作目录不是脚本所在目录，必须写绝对路径。
