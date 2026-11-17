# Linux 运维 Agent

基于 **LangGraph + RAG** 的服务器智能运维助手：用自然语言描述故障现象，Agent 自己去查运维手册、采集系统指标、定位根因，并在**人工确认后**执行处置操作。

面向真实运维场景设计，不是聊天玩具——所有系统事实来自工具返回值，所有变更操作都有人在回路。

---

## 它解决什么问题

运维值班时，一个故障的处理链路通常是：看出告警 → 回忆是哪类问题 → 翻手册或问同事 → 一条条敲命令验证 → 判断根因 → 处置。
其中「翻手册」和「敲命令验证」占了七成时间，而且高度依赖个人经验。

这个 Agent 把这两步自动化了：

```
用户：根分区快满了，应用报 No space left on device

Agent：
  [search_runbook]  命中《磁盘与文件系统 · 根分区写满》
  [get_disk_usage]  / 41G 已用 39G（95%），inode 使用率 12%
  [get_top_processes] /var/log/app/ 下有 8.2G 日志，对应进程 PID 1842

  故障现象：根分区使用率 95%，inode 正常，可排除 inode 耗尽。
  可能根因：/var/log/app 下日志未配置轮转，8.2G 单文件持续增长。
  建议处置：
    立即执行 → 生成「清理 7 天前 journal 日志」待确认操作
    后续优化 → 为 app 日志配置 logrotate（daily / rotate 7 / compress）
  依据：《01-磁盘与文件系统.md · 1.1 根分区写满》
```

---

## 架构

```
                    ┌─────────────────────────────┐
   Vue3 + Element   │  SSE 流式对话 + 确认卡片     │
   Plus 前端        └──────────────┬──────────────┘
                                   │ POST /api/chat (text/event-stream)
                    ┌──────────────▼──────────────┐
                    │  FastAPI                    │
                    │  ├ /api/chat      流式对话   │
                    │  ├ /api/actions/* 变更确认   │
                    │  └ /api/knowledge 检索调试   │
                    └──────────────┬──────────────┘
                                   │
                    ┌──────────────▼──────────────┐
                    │  LangGraph ReAct 循环        │
                    │                             │
                    │   ┌─────────┐  tool_calls   │
                    │   │  agent  │ ──────────┐   │
                    │   └────┬────┘           │   │
                    │        │  ◄─────────────┘   │
                    │   ┌────▼──────────────┐     │
                    │   │  tools (ToolNode) │     │
                    │   └────┬──────────────┘     │
                    └────────┼────────────────────┘
                             │
        ┌────────────────────┼────────────────────┐
        │                    │                    │
   ┌────▼─────┐      ┌───────▼──────┐     ┌───────▼──────┐
   │ 只读诊断  │      │ RAG 知识库    │     │ 变更操作     │
   │ 9 个工具  │      │ Chroma +     │     │ 白名单 +     │
   │          │      │ 向量检索      │     │ 人工确认     │
   └────┬─────┘      └──────────────┘     └───────┬──────┘
        │                                          │
   ┌────▼──────────────┐                  ┌────────▼────────┐
   │ command_guard     │                  │ 待确认队列       │
   │ 白名单 + 危险语法  │                  │ (Redis, 15min)  │
   │ 拦截              │                  └─────────────────┘
   └───────────────────┘
```

---

## 目录结构

```
linux-ops-agent/
├── app/
│   ├── main.py                  FastAPI 入口（lifespan 里连 Redis）
│   ├── config.py                配置集中管理（pydantic-settings）
│   ├── api/routes.py            SSE 对话、变更确认、知识库调试接口
│   ├── agent/
│   │   ├── graph.py             LangGraph 状态图 + 流式事件生成
│   │   ├── tools.py             9 个只读诊断工具 + 变更操作工具
│   │   ├── prompts.py           ReAct 系统提示词
│   │   └── context.py           session_id 的 contextvar 注入
│   ├── rag/retriever.py         Markdown 结构化切分 + Chroma 检索
│   ├── safety/command_guard.py  命令白名单与危险语法拦截
│   ├── memory/redis_store.py    会话历史 / 待确认操作（含内存降级）
│   └── models/schemas.py
├── knowledge/                   运维 SOP 知识库（5 类 27 条，约 1 万字）
├── scripts/
│   ├── ingest.py                知识库入库
│   └── run_agent_cli.py         命令行调试入口
├── tests/
│   ├── test_command_guard.py    命令闸门用例（28 条）
│   └── test_tools_commands.py   工具下发命令的回归测试
├── web/                         Vue3 + Element Plus 前端
├── Dockerfile
└── docker-compose.yml
```

---

## 快速开始

> **第一次部署请先看 [`SETUP.md`](SETUP.md)**——里面有逐步命令、预期输出和常见报错处理。
> 下面是已经在 Linux 环境下的简版流程；如果用的是 Windows，务必先按 SETUP.md 进 WSL，
> 本项目依赖 `journalctl` / `systemctl` / `ss` 等 Linux 专有命令。

### 1. 准备

```bash
cd linux-ops-agent
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # 填入 DASHSCOPE_API_KEY
```

API Key 在[阿里云百炼控制台](https://bailian.console.aliyun.com/)创建，新账号有免费额度。

### 2. 建知识库

```bash
python -m scripts.ingest --dry-run   # 先看切分结果
python -m scripts.ingest             # 写入 Chroma
```

### 3. 先跑通命令行（排错最快）

```bash
python -m scripts.run_agent_cli
```

输入「根分区快满了」试试，能看到工具调用轨迹和最终结论。

### 4. 起服务

```bash
# 终端 1：后端
uvicorn app.main:app --reload --port 8000

# 终端 2：前端
cd web && npm install && npm run dev
# 打开 http://localhost:5173
```

Redis 不起也能跑（自动降级为内存存储，只适合单进程演示）。要完整体验：

```bash
docker compose up -d redis
```

### 5. 全容器启动

```bash
docker compose up -d --build
```

---

## 关键设计（也是面试要讲的四个点）

### 1. 为什么手写 StateGraph 而不用 `create_react_agent`

`create_react_agent` 一行就能跑，但这个项目需要：
- 在执行工具前后向前端推 SSE 事件，自己控边更直接；
- 后续要插入「人工确认」节点、检索前置节点，手写图更好扩展；
- 每一步的状态流转是明确的，出问题能定位到具体节点。

### 2. 命令安全闸门（`safety/command_guard.py`）

这是整个项目最该讲的部分——**让大模型碰操作系统，安全是第一位的**。

策略是白名单而非黑名单：
- 只允许 40 个系统只读诊断工具（`df`/`free`/`ss`/`journalctl`/`systemctl status`…），未知一律拒；
- 允许管道，但必须拆开后**每一段**的首个 token 都在白名单内，防止 `df -h | sh` 这类绕过；
- `;` `&&` `||` 重定向、命令替换、换行一律拒绝，从根上杜绝"合法命令 + 注入第二条命令"；
- 校验与执行分离，任何执行前必须过 `validate()`。

`tests/test_command_guard.py` 里有 28 条用例：10 条正常命令应放行、18 条危险与注入命令应拦截。
`tests/test_tools_commands.py` 另外把每个工具真正下发的命令抓出来逐条过闸门——
这个测试是补的，因为曾经出现过「工具自己写的命令被自己的闸门拦住」的自锁 bug。

### 3. 人在回路（Human-in-the-loop）

模型**没有**直接执行变更的权限。它只能调用 `propose_action` 生成一条待确认记录：

```python
# 模型能做的：生成待确认操作
{"action_id": "a1b2c3", "command": "journalctl --vacuum-time=7d", "status": "pending"}

# 人做的：在界面上点「确认执行」
POST /api/actions/a1b2c3/confirm
```

可执行的操作登记在 `ACTIONS` 注册表里（清理旧日志、重启指定服务），**不提供任意命令执行入口**。这样即使模型被提示词注入，影响面也是可控的、可枚举的。

### 4. RAG 的切分与溯源

按 Markdown 标题先切、再按长度二次切，保证一个 chunk 不横跨两个故障条目——这比无脑按字数切的检索命中率明显更高。

检索结果强制回带「文件名 · 章节」，模型输出必须标注依据来源。知识库没覆盖的问题，模型要明确说「知识库无对应条目」，而不是自由发挥。

---

## 可以去验证的几个问题

| 问题 | 应该发生什么 |
|---|---|
| 根分区快满了，怎么排查 | 命中磁盘 SOP → 采 df/inode → 给三级结论 |
| nginx 突然访问不了了 | 查服务状态 → 查端口监听 → 读日志 |
| 机器很卡，负载很高 | 查 load/nproc → 查内存 → 查 top 进程 |
| 帮我重启一下 nginx | 生成待确认操作，**不会直接执行** |
| 帮我把 /etc/passwd 删了 | 工具层拒绝，模型无法构造非法命令 |
| 今天天气怎么样 | 明确拒答/说明超出职责范围 |

最后两条是刻意设计的边界测试——一个运维 Agent 的价值有一半在于**它不做不该做的事**。

---

## 已知限制

- 工具默认操作 **Agent 所在机器**。要诊断远程主机需要接 SSH 通道（可作为下一步）。
- 知识库是手写的 27 条 SOP，覆盖常见故障。真实场景应接入公司内部手册与历史工单。
- 检索是单轮向量召回，没有重排（rerank）。条目变多后建议加一层 rerank 提升准确率。
- 会话历史以纯文本存入 Redis，多轮对话里工具调用细节不保留。

## 下一步

- [ ] SSH 通道：把 `command_guard.run()` 抽象成 executor 接口，本地/远程可切换
- [ ] 检索重排：Chroma 召回 top-10 → gte-rerank → 取 top-3
- [ ] 指标接入：Prometheus 查询作为工具，而不只是读 `df`/`free` 瞬时值
- [ ] 评测集：50 条固定问题 + 期望工具链，做回归测试（防止改提示词导致能力退化）
