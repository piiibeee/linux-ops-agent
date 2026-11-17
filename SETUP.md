# SETUP · 从零跑起来

> 这份文档只讲一件事：**怎么让这个项目在你机器上真正跑起来**。
> 架构说明、设计取舍看 `README.md`；这里全是可复制的命令。
>
> 你机器上已经装了 Ubuntu，所以不用在 Windows 原生环境折腾——
> 这个项目本身就是给 Linux 写的，Windows 下 `journalctl` / `systemctl` / `ss` 都不存在。

---

## ⚠️ 如果你已经在 Windows 上建过 `.venv`，先看这段

项目目录里现在就有一个 `.venv`，是 2026-11-16 20:36 在 Windows 下建的：

```
home = C:\Python314
version = 3.14.3
```

**必须放弃这条路，两个原因：**

1. **Python 3.14 装不上本项目的依赖。** `requirements.txt` 锁的是 2024 年底的版本（`chromadb==0.5.23`、`langchain-core==0.3.29` 等），这些版本发布时 Python 3.14 还不存在，没有对应的预编译包，pip 会尝试现场编译然后失败。
2. **Windows 建的 venv 在 Linux 里完全不能用。** Windows 的虚拟环境是 `Scripts\` 目录 + `python.exe`，Linux 是 `bin/` 目录 + `python`。两者结构不兼容，在 WSL 里 `source .venv/bin/activate` 会直接说找不到文件。

**处理办法就一句：进 WSL 后删掉它重建**（见 A2 和 A4）。别在 Windows 上继续折腾，那是纯粹浪费时间。

---

## 第 0 步　先确认你的 Ubuntu 是哪种

在 **Windows** 的 PowerShell 或 cmd 里敲：

```powershell
wsl -l -v
```

| 结果 | 你的情况 | 走哪条路 |
|---|---|---|
| 列出了一个或多个发行版（如 `Ubuntu-22.04  Running  2`） | WSL | **路线 A** |
| 提示「没有已安装的分发」或命令不存在 | 双系统 / 虚拟机 | **路线 B** |
| 我在 Windows 里也没找到 Ubuntu，只在开始菜单有个图标 | 很可能是 WSL | 点开图标，能进到一个 `用户名@机器名:~$` 的终端就是 WSL |

不确定就直接点开 Ubuntu 图标，进入后敲 `uname -a`：
- 输出里有 `microsoft` 字样 → **WSL**（路线 A）
- 没有 → **双系统或虚拟机**（路线 B，更简单）

---

# 路线 A　WSL

## A1. 进入 Ubuntu

开始菜单搜 `Ubuntu` 打开，或 Windows 终端里敲 `wsl`。
确认进对了：

```bash
uname -a          # 应包含 microsoft-standard-WSL2
python3 -V        # 应输出 3.10 / 3.11 / 3.12
```

> WSL 里的 `python3` 与 Windows 上那个 `C:\Python314` 是**两套完全独立的东西**，Windows 装的版本再新也不影响这里。只管看这一条命令的输出。

> **如果 `python3 -V` 报 3.13 或更高**，说明版本太新，本项目的几个依赖没有对应 wheel。
> 先装个 3.11：
> ```bash
> sudo apt update && sudo apt install -y software-properties-common
> sudo add-apt-repository -y ppa:deadsnakes/ppa
> sudo apt update && sudo apt install -y python3.11 python3.11-venv python3.11-dev
> ```
> 后面所有 `python3` 都替换成 `python3.11`。

## A2. 把项目复制到 WSL 家目录（重要）

**不要直接在 `/mnt/c/...` 下跑。** WSL 跨文件系统访问走 9p 协议，Python 导入模块和 pip 装包会慢 5–10 倍，装依赖能装到你怀疑人生。

```bash
cp -r /mnt/c/Users/51265/WorkBuddy/2026-11-16-19-52-01/linux-ops-agent ~/linux-ops-agent
cd ~/linux-ops-agent
rm -rf .venv
ls        # 应该看到 app  knowledge  scripts  web  requirements.txt 等
```

那句 `rm -rf .venv` 是**必做**的：复制过来的 `.venv` 是 Windows 下建的（`Scripts/` 结构、指向 `C:\Python314`），在 Linux 里完全无法使用，留着只会在 A4 建环境时冲突。

> 复制之后，**以 `~/linux-ops-agent` 这一份为准**，Windows 桌面那份只作备份。
> 想改代码用 VSCode 打开 WSL 版本：
> ```bash
> code .        # 需要在 VSCode 里装 Remote - WSL 扩展；没装也可以先用 nano 改
> ```

## A3. 安装系统依赖

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip build-essential python3-dev
```

`build-essential` 必须装：`chromadb` 相关的包在没预编译 wheel 时需要现场编译，缺 gcc 会直接失败。

顺便确认运维命令都在（这是项目的工具层要用的）：

```bash
for c in df free ps ss top ping journalctl systemctl tail; do
  command -v $c >/dev/null && echo "OK   $c" || echo "缺少 $c"
done
```

九个全是 `OK` 才对。缺哪个就 `sudo apt install -y procps iproute2 iputils-ping systemd`。

## A4. 建虚拟环境

```bash
python3 -m venv .venv
source .venv/bin/activate
```

命令行前面出现 `(.venv)` 才算成功。之后**每开一个新终端都要重新 `source .venv/bin/activate`**。

## A5. 装 Python 依赖

```bash
pip install -U pip
pip install -r requirements.txt
```

这一步最慢，`chromadb` 比较大，正常要 **3–10 分钟**，不是卡死了。

**如果报错按这个顺序试：**

| 报错特征 | 处理 |
|---|---|
| `error: command 'gcc' failed` 或 `Python.h: No such file` | `sudo apt install -y build-essential python3-dev` 后重来 |
| `No matching distribution found for chromadb==0.5.23` | 单独装：`pip install chromadb` 不带版本号 |
| `Could not build wheels for ...` | 先把 pip 升到最新：`pip install -U pip setuptools wheel` |
| 版本冲突互相打架 | 放弃锁版本，直接装最新的：<br>`pip install fastapi uvicorn pydantic pydantic-settings python-dotenv langchain-core langchain-openai langchain-text-splitters langchain-chroma langgraph chromadb redis` |

## A6. 环境自检（不用 Key，先跑这个）

```bash
python -m scripts.selftest
```

**期望结果**：只有 `[2/6]` 里的 `DASHSCOPE_API_KEY 未填写` 一项是 FAIL，第 3 项应该显示 `[ OK ] 运行在 Linux 环境`，第 4 项显示 `27 条用例全部符合预期`。

如果第 1 项还有一堆 FAIL，说明依赖没装好，回 A5。

## A7. 填 API Key

先去拿 Key：

1. 浏览器打开 https://bailian.console.aliyun.com/
2. 阿里云账号登录（没有就注册，需要实名认证）
3. 首次进入会提示开通「大模型服务平台百炼」，点开通，**免费**
4. 左侧菜单「API-KEY 管理」→ 创建我的 API-KEY
5. 复制 `sk-` 开头那串

写进配置，二选一：

**方式一（一条命令搞定）：**

```bash
sed -i 's|^DASHSCOPE_API_KEY=.*|DASHSCOPE_API_KEY=sk-把这里换成你的key|' .env
```

**方式二（编辑器改）：**

```bash
nano .env
# 找到 DASHSCOPE_API_KEY= 这一行改掉
# Ctrl+O 保存 → 回车确认 → Ctrl+X 退出
```

再跑一次自检确认：

```bash
python -m scripts.selftest
```

这次应该显示「发现 0 个待处理项」。如果第 3 项还有 WARN 提示前端依赖未装，可以忽略，那只影响网页界面。

## A8. 构建知识库

```bash
python -m scripts.ingest --dry-run    # 先看分块效果，不写库
```

预期能看到 5 个 md 文件被切成几十个块，每块带来源标注。确认没问题再真正写入：

```bash
python -m scripts.ingest
```

会调用 embedding 接口，消耗很少（几十条文本），几秒钟到一分钟完成。成功后 `data/chroma/` 下会出现向量库文件。

## A9. 命令行跑通对话（先别急着开网页）

```bash
python -m scripts.run_agent_cli
```

按顺序试这四个问题，覆盖四条能力线：

| 你输入 | 应该发生什么 | 对应简历里的点 |
|---|---|---|
| `磁盘使用率怎么样` | 调用 `df -h`，报出各挂载点用量 | 工具层封装 + Function Calling |
| `哪个进程占 CPU 最高` | 调用 `ps`，列出前几名 | 参数化工具与输出截断 |
| `nginx 服务起不来，帮我看看日志` | 调 `systemctl` + `journalctl`，输出「现象—根因—建议」 | 日志智能诊断 |
| `帮我清理一下 journal 日志` | 生成一条**待确认**记录，不直接执行 | 人在回路 |

> 第四个问题是最该讲的部分：模型没有执行权限，只能提交待确认操作，你确认后才落地。

想验证安全闸门真能拦，直接在对话里要求它执行 `rm -rf /`，它会拒绝。

## A10. 起 Web 界面

终端一（后端）：

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

终端二（前端）：

```bash
cd web
node -v          # 需要 18 以上；没有就先装 Node
npm install
npm run dev
```

没装 Node 的话：

```bash
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs
```

然后浏览器打开 `http://localhost:5173`。

---

# 路线 B　双系统 / 虚拟机

比 WSL 简单，因为本身就是干净的 Linux。

1. 把项目拷进去（U 盘、`git clone`、或共享文件夹都行），放在 `~/linux-ops-agent`。
2. 直接从 **A3** 开始照着做，A1、A2 跳过。
3. 所有命令完全一致。

虚拟机注意一点：如果内存只给 1–2G，`pip install chromadb` 可能被 OOM 杀掉，建议给到 4G。

---

# 跑起来之后

## 推上 GitHub（简历里那个链接）

```bash
cd ~/linux-ops-agent
git init
git add .
git commit -m "feat: Linux 运维 Agent（工具调用 + RAG 日志诊断 + 人在回路）"
git branch -M main
git remote add origin git@github.com:你的用户名/linux-ops-agent.git
git push -u origin main
```

`.env` 已经在 `.gitignore` 里，**不会**被提交，Key 不会泄露。

## 录屏前自查

- [ ] `python -m scripts.selftest` 全绿
- [ ] A9 的四个问题都能跑出预期结果
- [ ] Web 界面能正常对话、流式输出
- [ ] 待确认操作卡片能点「确认」并真的执行
- [ ] GitHub 仓库公开可访问，README 里的截图/说明与代码一致

## 常见报错速查

| 现象 | 原因 | 处理 |
|---|---|---|
| `ModuleNotFoundError: No module named 'app'` | 不在项目根目录，或没激活 venv | `cd ~/linux-ops-agent && source .venv/bin/activate` |
| `ToolNode` 导入报错 | langchain 版本差异 | `pip install -U langgraph langchain-core` |
| `astream_events` 报参数错误 | 同上 | 同上；仍报错把完整 traceback 发我 |
| 对话时提示 Redis 连不上 | 没起 Redis | **可以忽略**，代码会自动降级为内存存储 |
| `401 Unauthorized` / `InvalidApiKey` | Key 错或没填 | 回 A7，注意别把引号带进去 |
| `insufficient_quota` | 免费额度用完 | 百炼控制台充值，或换 `LLM_MODEL=qwen-turbo` |
| `df` 输出格式奇怪 / `ps` 报参数错误 | 你还在 Windows 跑，没进 WSL | 回 A1 |

---

**卡在哪一步就把那一步的完整报错贴给我**，包括命令行最后 20 行。不用自己猜。
