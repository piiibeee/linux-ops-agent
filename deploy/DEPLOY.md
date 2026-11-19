# 部署上线手册（从零到公网可访问）

> 目标不是"跑起来"，是**有一个别人能打开的地址，并且能从上面讲出服务器运维的真问题**。
> 全程约 1.5 小时，其中约 40 分钟是等安装。

---

## 0. 先把代码推上去（10 分钟，必做）

服务器是从 GitHub 拉代码的。你本地改了很多（loop_guard、eval_agent、部署文件），**不推就等于没改**。

```bash
cd ~/linux-ops-agent
git status --short          # 先看有没有 .env 之类的敏感文件混进来
git add -A
git commit -m "feat: 评测脚本 + 循环治理；deploy: 生产部署配置"
git push
```

⚠️ `git status` 里如果出现 `.env`、`data/`、`.venv`，说明 `.gitignore` 没盖住，**先别 add**，把它们加进 `.gitignore` 再提交。

---

## 1. 买服务器

| 项 | 选择 |
|---|---|
| 厂商 | 腾讯云 / 阿里云都可以，选「轻量应用服务器」 |
| 配置 | **2 核 2G**（chorma + uvicorn + node 构建够用；1G 会在 npm build 时 OOM） |
| 系统 | **Ubuntu 24.04 LTS**（22.04 也行，脚本都兼容） |
| 带宽 | 3M 以上 |
| 地域 | 就近，北京/上海 |
| 价格 | 新用户首年通常 60–150 元 |

买的时候顺手做两件事：

1. **记下公网 IP 和 root 密码**（首次登录用）
2. 控制台 → 防火墙/安全组：确认放行 **22、80、443**（轻量服务器默认有这几个规则，也要看一眼）

登录：

```bash
ssh root@<你的公网IP>
```

---

## 2. 部署（三条命令）

```bash
# ① 拉代码
git clone https://github.com/piiibeee/linux-ops-agent.git /opt/ops-agent

# ② 首次执行（会装依赖、建用户，然后停下来等填 Key）
bash /opt/ops-agent/deploy/deploy.sh

# ③ 填 Key 后再跑一次
nano /opt/ops-agent/.env        # 改 DASHSCOPE_API_KEY
bash /opt/ops-agent/deploy/deploy.sh
```

第二次跑完会打印访问地址。脚本是幂等的，以后更新代码只要再跑一次第 ③ 步。

**脚本替你处理了四个容易踩的坑**（这几个坑不处理，部署完功能会莫名其妙失效）：

| 坑 | 不处理的后果 | 脚本怎么做的 |
|---|---|---|
`ops` 用户不在 `systemd-journal` 组 | `read_journal` 返回空，Agent 说"查不到日志" | `usermod -aG systemd-journal ops` |
`ops` 用户不在 `adm` 组 | 读 `/var/log/` 下文件 Permission denied | `usermod -aG adm ops` |
sudo 提权被 systemd 拦 | 点"确认"后清理失败 | service 里**不加** `NoNewPrivileges`，只放行 `journalctl --vacuum-size=*` |
Nginx 缓冲 SSE | 前端"转很久然后一次性蹦出全部字" | `proxy_buffering off` |

---

## 3. 验收（这一步的截图就是"落地"证据）

打开 `http://<公网IP>/`，逐条走：

- [ ] 页面正常打开，能看到对话界面
- [ ] 问「看一下磁盘使用情况」→ **文字逐段出现**（不是一次性蹦出）
- [ ] 问「看一下 journal 占了多少空间」→ 回答里有 `take up xxx M`
- [ ] 问「帮我清理 journal 日志」→ **弹出待确认卡片，不自动执行**
- [ ] 点「确认」→ 终端 `journalctl --disk-usage` 数字真的变小
- [ ] 输入「执行 rm -rf /」→ 被白名单拒绝，没有生成待确认卡片
- [ ] **手机切 4G（不连 WiFi）打开同一地址** —— 能打开才算真上线

最后一条最重要：手机能打开，说明这不是你本机的 localhost。

顺手记下来：每一条的耗时、工具调用次数。这些数字后面写报告和面试都要用。

---

## 4. 发给别人之前，必须加口令（5 分钟）

现在任何人都能用你的 Key 跑 Agent。**在把地址发出去之前做这一步**。

```bash
# 装工具（脚本已经装过 apache2-utils，跳过）
htpasswd -c /etc/nginx/.htpasswd demo
# 输入两遍密码，比如 ops2026

# 给站点加认证
nano /etc/nginx/sites-available/ops-agent
```

在 `server {` 下面第一行加两行：

```nginx
    auth_basic           "Ops Agent Demo";
    auth_basic_user_file /etc/nginx/.htpasswd;
```

```bash
nginx -t && systemctl reload nginx
```

之后打开网站会弹登录框，用户名 `demo`，密码你刚设的。发消息时一起给出。

**更重要的兜底**：去[百炼控制台](https://bailian.console.aliyun.com/)给这个 API Key 设**每月额度上限**（比如 100 元）。口令可能被分享出去，额度上限是最后一道闸。

---

## 5. 可选：给它一个 HTTPS 域名（10 分钟）

没有域名也能签证书——用 `sslip.io` 这个免费泛解析：`1.2.3.4.sslip.io` 会直接解析到 `1.2.3.4`。

```bash
apt install -y certbot python3-certbot-nginx
# 把 server_name 改成你的 sslip 域名
sed -i 's/server_name _;/server_name '"$(curl -s https://api.ipify.org)"'.sslip.io;/' /etc/nginx/sites-available/ops-agent
nginx -t && systemctl reload nginx
certbot --nginx -d $(curl -s https://api.ipify.org).sslip.io --agree-tos -m 512650689@qq.com --redirect
```

成功后地址变成 `https://xxx.xxx.xxx.xxx.sslip.io`，浏览器不再提示不安全。

> 这条不是必须的。国内访问 sslip.io 的解析偶尔慢，如果打不开就退回用 IP + HTTP。

---

## 6. 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
网页空白 / 404 | 前端没构建成功 | `ls /opt/ops-agent/web/dist/index.html`，没有就 `cd web && npm run build` |
回答"未连上 Redis，已降级" | redis 没起 | `systemctl status redis-server`，`systemctl start redis-server` |
工具返回「未能采集」 | 权限或命令被闸门拒 | `sudo -u ops journalctl -n 5` 手动试；看 `journalctl -u ops-agent -n 50` |
点确认后报权限错误 | sudoers 没生效 | `sudo -u ops -n -l \| grep journalctl` 检查 |
页面能开但接口 502 | 后端挂了 | `journalctl -u ops-agent -n 50 --no-pager` |
文字一次性蹦出 | SSE 被缓冲 | 确认 Nginx 里 `proxy_buffering off;` |
`401 InvalidApiKey` | Key 没填对 | `grep DASHSCOPE /opt/ops-agent/.env` |

常用命令：

```bash
systemctl status ops-agent          # 服务状态
journalctl -u ops-agent -f          # 实时日志
systemctl restart ops-agent         # 重启
df -h /                             # 磁盘
```

---

## 7. 上线之后写这一页（面试直接用）

部署完当天就写，趁记忆新鲜。放到 `docs/上线报告.md`：

```markdown
# Linux Ops Agent 上线报告

## 环境
- 云厂商/配置：____，Ubuntu 24.04
- 部署形态：Nginx 反代 + uvicorn（systemd 托管）+ Redis 本机
- 为什么不用容器：Agent 要执行 df/journalctl，容器内看不到宿主机

## 部署过程中遇到并解决的问题
1. 现象：____ ｜ 原因：____ ｜ 处理：____
2. 现象：____ ｜ 原因：____ ｜ 处理：____
（至少写 3 条，这是最有价值的部分）

## 验收结果
| 场景 | 预期 | 实际 | 耗时 |
|---|---|---|---|
| 磁盘诊断 | 调 get_disk_usage | | |
| journal 占用 | 1 次工具调用给出数值 | | |
| 清理日志 | 生成待确认卡片 | | |
| 越权命令 | 被白名单拒绝 | | |

## 试用反馈（找 3 个人之后补）
- 试用人数：__
- 提出的问题：__ 条，能正确回答 __ 条，拒答 __ 条
- 发现的问题与修复：____
```

**第 2 节最值钱。** 面试官不关心你部署成功了，关心"你遇到过什么、怎么定位的"。这也是这个项目和别人的 demo 唯一的区别。

---

## 8. 部署完之后

1. 把地址和口令存进 `output/job-hunt/投递清单.md` 的二触话术
2. README 顶部加一行「在线演示：<地址>（口令：xxx）」
3. 简历不用改——地址放话术和 README 就够
4. 找 3 个人真用（明天半天），补第 7 节的"试用反馈"
