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

### 如果这一步特别慢（国内网络）

Ubuntu 默认源在国外，`build-essential` 加依赖有一百多 MB，官方源 5–15 分钟是常态。

**先判断是慢还是卡死**：进度百分比在涨就只是慢，等着别中断；一直停在 `0%` / `Waiting for headers` / `Reading package lists...` 超过两三分钟不动，就是源的问题。

换阿里云镜像（先 `Ctrl+C` 中断，如果已中断先修一下 dpkg 状态）：

```bash
sudo dpkg --configure -a
```

然后执行换源，这段会自动判断你的 Ubuntu 版本，两种格式都覆盖：

```bash
# 22.04 及以前（/etc/apt/sources.list）
if [ -f /etc/apt/sources.list ]; then sudo cp /etc/apt/sources.list /etc/apt/sources.list.bak; sudo sed -i 's|http://archive.ubuntu.com|https://mirrors.aliyun.com|g; s|http://security.ubuntu.com|https://mirrors.aliyun.com|g; s|http://cn.archive.ubuntu.com|https://mirrors.aliyun.com|g' /etc/apt/sources.list; fi

# 24.04 及以后（deb822 格式，/etc/apt/sources.list.d/ubuntu.sources）
if [ -f /etc/apt/sources.list.d/ubuntu.sources ]; then sudo cp /etc/apt/sources.list.d/ubuntu.sources /etc/apt/sources.list.d/ubuntu.sources.bak; sudo sed -i 's|http://archive.ubuntu.com|https://mirrors.aliyun.com|g; s|http://security.ubuntu.com|https://mirrors.aliyun.com|g; s|http://cn.archive.ubuntu.com|https://mirrors.aliyun.com|g' /etc/apt/sources.list.d/ubuntu.sources; fi

sudo apt update
```

换完再重跑安装命令，通常一两分钟就完事。

> **想更省时间**：`python3-venv` 才是必需的（三十几 MB），`build-essential` 是保险。
> 可以先把 `python3-venv` 装上继续往下走，等 `pip install` 真报 gcc 错误了再补 `build-essential`。
> ```bash
> sudo apt install -y python3-venv
> ```

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

**先换国内镜像源，再装。** PyPI 官方源在国外，这一套依赖里 `chromadb` 会连带拉 `onnxruntime`、`tokenizers` 等大包，合计几百 MB，不换源会慢到以为卡死。

```bash
# 1. 升级 pip（新版下载并发更好、重试机制更稳）
python -m pip install -U pip -i https://mirrors.aliyun.com/pypi/simple/

# 2. 把镜像设为默认，之后所有 pip 命令自动走国内
pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/
pip config set global.timeout 120

# 3. 装依赖
pip install -r requirements.txt
```

换源后正常 **1–3 分钟**；不换源可能二三十分钟甚至超时失败。

不想改全局配置的，用一次性参数：

```bash
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/ --timeout 120 --retries 5
```

### 换源后还是慢 / 卡在某个包

先看卡在哪个包名上，单独装它试，能快速定位：

```bash
pip install chromadb==0.5.23 -i https://mirrors.aliyun.com/pypi/simple/ --timeout 120 -v
```

**镜像同步延迟**：如果报 `No matching distribution found for xxx==x.x.x`，说明这个镜像还没同步该版本。加上官方源做后备，让 pip 找不到时回落：

```bash
pip install -r requirements.txt \
  -i https://mirrors.aliyun.com/pypi/simple/ \
  --extra-index-url https://pypi.org/simple \
  --timeout 120
```

**换一个镜像**（下面耗时是在你这台机器上实测的，同一个 `chromadb` 索引页）：

| 镜像 | 地址 | 实测 |
|---|---|---|
| 华为云 | `https://mirrors.huaweicloud.com/repository/pypi/simple/` | 0.30s |
| 阿里云 | `https://mirrors.aliyun.com/pypi/simple/` | 0.43s |
| 腾讯云 | `https://mirrors.cloud.tencent.com/pypi/simple/` | 0.44s |
| 中科大 | `https://mirrors.ustc.edu.cn/pypi/simple/` | 0.74s |
| 清华 | `https://pypi.tuna.tsinghua.edu.cn/simple/` | 2.21s |
| 官方 PyPI | `https://pypi.org/simple/` | **12s 超时** |

```bash
pip config set global.index-url https://mirrors.huaweicloud.com/repository/pypi/simple/
```

> 中断过不用从头来，pip 有缓存（`~/.cache/pip`），已经下完的包不会重下。

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

## A10. 变更操作的 sudo 授权（演示第三幕必须做）

「清理 journal 日志」这个动作，普通用户执行会失败：

```
Failed to delete archived journal ...: Permission denied
Vacuuming done, freed 0B
```

原因：`/var/log/journal` 下的归档文件属 `root:systemd-journal`，只有 root 能删。
服务是以你自己的用户身份跑的，所以 Agent 点「确认」后同样会被拒。

**不要用 `sudo` 启动服务**（那等于给 Agent root，安全叙事就崩了）。
正确做法是只授权这一条命令：

```bash
echo 'pboio ALL=(root) NOPASSWD: /usr/bin/journalctl --vacuum-size=*' | sudo tee /etc/sudoers.d/ops-agent
sudo chmod 440 /etc/sudoers.d/ops-agent
sudo visudo -c          # 必须显示 parsed OK，否则先修好，别关终端
```

> 用户名不是 `pboio` 就换成 `whoami` 的输出。
> **`sudo visudo -c` 这步必须做**——sudoers 语法错了会让 sudo 整个失效。

验证（应该直接出结果，不提示输密码）：

```bash
sudo -n journalctl --vacuum-size=16M
```

再验一条不该放行的，必须被拒：

```bash
sudo -n systemctl restart nginx      # 期望：a password is required / 不在授权范围
```

> **为什么用 `--vacuum-size` 而不是 `--vacuum-time`**：时间窗要人判断"几天算合适"，
> 而容量是硬约束。日志能不能把盘写满，只取决于总量，不取决于它有多老。
> 顺带一个坑：`--vacuum-time=7d` 只删 7 天前的归档，如果日志是最近几天堆起来的
> （比如密集装包），它几乎删不动——257M 的日志可能只释放 24M。

`journalctl --vacuum-size=16M` 这条命令本身**仍然要过白名单**：`command_guard`
会把 `sudo` 前缀剥掉再校验 `journalctl`，所以 `sudo rm -rf /` 照样被拦（已验证）。

**两层控制，面试可以这么讲**：

> 白名单管「模型能表达出哪些命令」，sudoers 管「这些命令能以什么身份跑到什么程度」。
> 不是我给 Agent 开了 root，而是我给它开了一条最小授权规则——只能清理 7 天前的 journal，
> 别的一条都不行。

授权后重启后端，第三幕才能演示成功。

---

## A11. 代码改了怎么同步进 WSL（不用重拷整个目录）

项目在 Windows 侧改完后（`C:\Users\51265\WorkBuddy\2026-11-16-19-52-01\linux-ops-agent`），
只同步改过的文件，**不要**整个目录覆盖过去——WSL 里的 `.env` 有真实 Key、`data/chroma` 是建好的向量库，
全量覆盖会把它们冲掉。

```bash
cd ~/linux-ops-agent
SRC=/mnt/c/Users/51265/WorkBuddy/2026-11-16-19-52-01/linux-ops-agent

# 1) 同步代码（按需增删文件名，只拷改过的）
cp $SRC/app/agent/graph.py  $SRC/app/agent/loop_guard.py \
   $SRC/app/agent/tools.py  $SRC/app/agent/prompts.py  app/agent/
cp $SRC/app/config.py app/config.py
cp $SRC/tests/test_loop_guard.py $SRC/tests/test_tools_commands.py tests/
cp $SRC/scripts/eval_agent.py scripts/

# 2) 补 .env 里的新配置（幂等，重复执行也不会写重复行）
sed -i 's/^RECURSION_LIMIT=.*/RECURSION_LIMIT=40/' .env
grep -q '^MAX_TOOL_ROUNDS' .env || printf 'MAX_TOOL_ROUNDS=6\nMAX_CALLS_PER_ROUND=6\n' >> .env

# 3) 知识库内容改过就要重建（全量覆盖 collection，不会残留旧切片）
python -m scripts.ingest

# 4) 重启后端（`--reload` 会自动重载代码，但 .env 改动必须整进程重启才生效）
```

改完的验收方式：问一句「看一下 journal 占了多少空间」，应该**一次工具调用**就答出
`journalctl --disk-usage` 的数值，不再出现一串重复的 `read_journal`。

再跑一次评测集，确认改动没有破坏原有行为（**改提示词、换模型之后必跑**）：

```bash
python -m scripts.eval_agent
```

9 条用例、5 分钟左右，报告落在 `reports/eval-<时间戳>.md`。
第一次跑允许有几条不通过——那几条就是待修清单，修完再跑，通过率要写进 README。

---

## 常见报错速查

| 现象 | 原因 | 处理 |
|---|---|---|
| `ModuleNotFoundError: No module named 'app'` | 不在项目根目录，或没激活 venv | `cd ~/linux-ops-agent && source .venv/bin/activate` |
| `ToolNode` 导入报错 | 旧版本代码，已改为自实现工具节点 | 同步 `app/agent/graph.py`（见 A11） |
| `Recursion limit of N reached` | 模型陷入工具循环 | 新版已有去重 + 轮数预算兜底；仍出现就检查 `.env` 的 `MAX_TOOL_ROUNDS` / `RECURSION_LIMIT` |
| `astream_events` 报参数错误 | 同上 | 同上；仍报错把完整 traceback 发我 |
| 对话时提示 Redis 连不上 | 没起 Redis | **可以忽略**，代码会自动降级为内存存储 |
| `401 Unauthorized` / `InvalidApiKey` | Key 错或没填 | 回 A7，注意别把引号带进去 |
| `insufficient_quota` | 免费额度用完 | 百炼控制台充值，或换 `LLM_MODEL=qwen-turbo` |
| `df` 输出格式奇怪 / `ps` 报参数错误 | 你还在 Windows 跑，没进 WSL | 回 A1 |

---

**卡在哪一步就把那一步的完整报错贴给我**，包括命令行最后 20 行。不用自己猜。
