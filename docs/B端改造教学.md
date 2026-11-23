# 从"聊天页"到"控制台"：B 端改造教学文档

> 这份文档解释这次改造**每一处为什么这么写**，不是 API 手册。
> 读完你应该能自己往这套架子里加第五个、第六个页面。

---

## 一、改造前后，到底差了什么

| | 改造前 | 改造后 |
|---|---|---|
| 页面数 | 1（一个 ChatPanel） | 5（布局 + 4 个视图） |
| 路由 | 无 | vue-router，嵌套路由 + 懒加载 |
| 状态管理 | 组件内 `ref` | Pinia（角色、跨页共享） |
| 数据形态 | 一条消息流 | 表格 + 状态机 + 图 |
| 权限 | 无 | 角色切换 + 按钮级控制 |
| 后端接口 | 4 个（对话相关） | 7 个（新增审计、审批、拓扑） |
| 前端依赖 | vue + element-plus | 再加 router / pinia / vue-flow |
| 打包 | 单包 1.2MB | 分包，业务代码 8KB，依赖独立缓存 |

**一句话概括**：原来只有一个"用 Agent 的人"会看的界面，现在多了"管 Agent 的人"要看的三个界面。

---

## 二、为什么加的是这四个页面

面试或答辩时被问"为什么做这些页面"，别答"因为 JD 要求"，要答**每个页面在解决什么管理问题**：

| 页面 | 解决的问题 | 对应 B 端能力 |
|---|---|---|
| 审计日志 | "三小时前那条命令是谁让它执行的？查了什么？" | 表格、分页、多条件筛选、详情追溯、导出 |
| 变更审批 | "危险操作不能由模型自己决定，得有人批，批了要留痕" | 状态机、权限、二次确认、异常态 |
| 流程编排 | "这个 Agent 到底怎么跑的？为什么卡在某一步？" | 图可视化、节点交互、动态数据渲染 |
| 对话 | 原有能力，保留 | 流式渲染（SSE） |

这四个刚好是 B 端的四类硬骨头：**表格、状态机、权限、图**。堆 15 个"表格+搜索框"的页面，不如把这四个做透。

---

## 三、后端改了什么（3 个文件新增 + 3 处修改）

### 3.1 `app/audit/store.py`：审计落库

**核心问题**：原来工具调用只存在于 SSE 事件流里，对话结束就没了。要能回溯，必须落盘。

**为什么用 JSONL 而不是数据库**：

```
单机 / 单写入者 / 按行追加 / 不需要事务
```

这四条决定了 JSONL 是够用的。换成 SQLite 会多一层依赖和迁移成本，而查询量（万级）线性扫描完全扛得住。**接口形状设计成能和数据库互换**：`query()` / `stats()` / `filters()`，将来换 SQLite 只改这一个文件。

**为什么一次调用写两行（start + end）而不是一行**：

```
{"id":"a1","stage":"start","tool":"disk_overview","status":"running"}
{"id":"a1","stage":"end","status":"ok","elapsed_ms":37}
```

查询时按 `id` 合并，保留最后一条的状态。这样做的好处是：**如果进程在工具执行中崩了，你能看到"有一条调用开始过但没结束"**——这比丢掉这条记录有价值得多。一行写完的话，崩溃就等于什么都没发生。

**一个诚实的近似**：`elapsed_ms` 是按工具名先进先出配对算出来的。LangGraph 的 `on_tool_start` / `on_tool_end` 事件里只带 `name`，不带 `tool_call_id`，同一轮并发调同一个工具时，配对可能错位。所以耗时是**近似值**。这个误差是知情选择：换来的是"一定记得下来"。

### 3.2 `app/api/admin_routes.py`：三个管理端接口

**为什么单独一个文件，不塞进 `routes.py`**：`routes.py` 是对话链路，改它风险高（SSE、异常处理、流式响应都在里面）。管理端以只读查询为主，分开放，出问题也不会连累对话。

三个接口：

| 接口 | 参数 | 返回 |
|---|---|---|
| `GET /api/audit` | page, page_size, tool, status, session_id, keyword | 分页列表 + 可选筛选项 |
| `GET /api/audit/stats` | — | total / today / failed / blocked / avg_elapsed_ms |
| `GET /api/actions` | status | 全量变更单 + 按状态计数 |
| `POST /api/actions/{id}/approve` | approver | 执行结果 + 审计留痕 |
| `GET /api/graph` | — | 节点与边 |

**`/api/graph` 的关键设计**：不手写一份"假拓扑"，而是试着从**编译后的图**里读：

```python
g = graph.get_graph()          # LangGraph 编译后自带拓扑
for node_id, node in g.nodes.items(): ...
for edge in g.edges: ...        # edge.conditional 区分条件边
```

读不到（版本变化导致内部结构改动）才回退到静态描述，并在返回里用 `source` 字段标明是 `runtime` 还是 `static`。**前端会把这个字段显示出来**——这样"这张图是实时读的还是硬编码的"，一眼可见，不会骗人。

**实测出来的三个细节**（langgraph 0.2.62，用一个结构和 `graph.py` 相同的小图跑出来的）：

```
nodes: __start__, agent, tools, finalize, __end__
edges: __start__→agent        conditional=False
       agent→tools            conditional=True
       agent→finalize         conditional=True
       agent→__end__          conditional=True
       tools→agent            conditional=False
       finalize→__end__       conditional=False
```

1. **`__start__` / `__end__` 是虚拟节点**，也会出现在 `nodes` 里。不能过滤掉——过滤了画布上就没有入口和出口，箭头会指向虚空。
2. **`edge.data` 是 `None`**，"什么条件下走这条边"的信息**读不到**（条件函数在编译时被吃掉了）。所以边的文字标签只能靠 `_EDGE_LABELS` 按 `(source, target)` 补——这是运行时读取唯一补不齐的部分，必须手写。
3. **节点 `label` 原始值是 node id**（`agent`、`__start__`）。直接用，画布上就是一串英文标识符；所以加了 `_NODE_LABELS` 统一翻成人话，让 runtime 和 static 两条路径长得一样。

> 这三点合起来说明一件事：**"自动读取"不等于"零手工"**。拓扑能自动对齐代码，语义（边的条件、节点的用途）必须人来写。判断一个"自动生成"的功能靠不靠谱，就看它有没有把补不齐的部分标出来，而不是假装全能。

### 3.3 三处小修改

| 文件 | 改动 | 为什么 |
|---|---|---|
| `agent/graph.py` | `stream_chat` 里 tool_start/tool_end 落审计 | 审计数据的唯一来源就是这里 |
| `agent/graph.py` | `execute_action` 增加 `executing` 中间状态 | 审批页能显示"正在执行"，而不是点完干等 |
| `agent/graph.py` | 新增 `_judge_tool_status()` | 把工具输出归成 ok/error/blocked 三态，供审计筛选 |
| `agent/tools.py` | 变更单加 `created_at` | 审批列表要排序、要算耗时 |
| `memory/redis_store.py` | 新增 `list_actions()` | `list_pending` 只记 pending，执行完就查不到了 |
| `main.py` | 注册 `admin_router` | — |

`_judge_tool_status()` 用关键字判断（"不在白名单"→blocked），是**近似**。为什么不改成结构化返回：那要动 10 个工具函数的返回格式，风险远大于收益。而它只影响"审计页上的一个标签颜色"，判错了也没有后果。

---

## 四、前端改了什么

### 4.1 架构：为什么必须换成嵌套路由

改造前 `App.vue` 里直接写死了"顶栏 + ChatPanel"。加到第二个页面时就会出问题：顶栏要么复制两遍，要么每个页面自己画。

```js
// router/index.js
{ path: '/', component: AdminLayout, children: [chat, audit, actions, graph] }
```

- **布局只渲染一次**，切视图不重建侧边栏 → 待审批角标不会闪
- **懒加载**（`() => import(...)`）：审计页和画布页的代码不会拖慢对话页首屏
- **路由 meta 带 title**，面包屑和 `document.title` 从同一处取，不用两处维护

### 4.2 状态：为什么角色要进 Pinia

角色要跨三个页面用（审批页决定按钮能不能点、审计页决定参数要不要打码）。放组件里就得靠 props 层层传或事件总线。

```js
// stores/auth.js
const canApprove = computed(() => role.value === 'admin')
```

**关键在于：整个项目只有一个权限判断入口。** 以后加"只读审计员"角色，只改这一行，不用去三个页面里搜 `role === 'admin'`。

⚠️ **这只是演示用的角色开关，不是鉴权。** 真正的 RBAC 必须后端校验。代码里 `approve` 接口显式接收 `approver` 参数并写进审计——将来接真实登录，把参数改成从 token 解析，前端不用动。

### 4.3 审计页：B 端表格的四个必备件

写惯了聊天界面，第一次写表格容易漏掉这些：

1. **筛选条件要落到后端参数**，不是前端 filter 一遍数组。种子是 `filters` 对象，`qs()` 会自动丢掉空值——不然会把 `tool=undefined` 发过去，后端当成字符串 "undefined" 去匹配。
2. **分页要有 total 和 page_size 切换**，10/20/50/100。
3. **空态要写人话**：`empty-text="暂无审计记录，先去对话页让 Agent 查一次服务器"`，而不是"暂无数据"。
4. **详情要能看到原话**：抽屉里展示完整参数 JSON 和完整输出。审计的价值在追溯，截断就失去意义了。

**两个细节值得记**：

- 导出 CSV 加 `\ufeff` BOM，否则 Excel 打开中文乱码
- `el-table` 的 `@row-click` 和行内按钮的 `@click.stop` 要配合，否则点"详情"会触发两次

### 4.4 审批页：状态机为什么画在最上面

表格里的状态标签只能告诉你"现在是什么"，说不清"接下来会怎样"。所以页面顶部画了流转：

```
待确认 → 执行中 → 已执行 / 失败
```

每个节点带当前数量，用的是 `/api/actions` 返回的 `counts`。这样值班的人一眼能看出"现在压了 3 单"。

**权限控制的具体做法**：

```html
<el-button :disabled="!auth.canApprove || row.status !== 'pending'">批准执行</el-button>
```

两个条件缺一不可：角色够 + 状态对。**光判断角色不够**——已执行的单子再点一次，后端会拒（`status !== pending`），但前端不该让人点得下去。

二次确认用 `ElMessageBox`，把完整命令放进去。这不是走形式：这条命令真的会在服务器上跑。

### 4.5 画布页：三个坑

1. **VueFlow 必须有确定高度的父容器**，否则画布塌成 0 高度白屏。这里写的是 `height: calc(100vh - 190px)`。
2. **节点样式要用 `:deep()`**，因为 VueFlow 渲染的节点在组件作用域之外，scoped 样式够不到。
3. **不用自动布局**。5 个节点，手排坐标比引 dagre 更快更好看。节点超过 15 个再换。

节点说明 `NODE_DESC` 是手写的，和 `graph.py` 里的函数一一对应。**这是唯一需要手工同步的地方**（拓扑是自动读的，说明文字不是）。改节点函数时记得改这里。

---

## 五、怎么跑起来

```bash
# 1) 后端
cd linux-ops-agent
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env      # 填 DASHSCOPE_API_KEY
python scripts/ingest.py  # 建知识库（首次）
uvicorn app.main:app --port 8000

# 2) 前端
cd web
npm install
npm run dev               # http://localhost:5173
```

**怎么造数据让页面有东西看**：

1. 去对话页问「根分区快满了，怎么排查」→ 产生几条审计记录
2. 让它处理一次清理 → 产生一条待审批变更单
3. 回审批页批准 → 状态走完 pending → executing → executed

没有数据时三个页面都是空态，**这是正常的**，不是坏了。

**怎么确认画布读的是真图**：打开画布页，看右上角那个标签——`实时读取` 表示 `/api/graph` 成功读到了编译后的 LangGraph，`静态兜底` 表示读取失败、用了手写描述。两种情况页面都能用，但只有前者能保证"画布 = 实际执行"。

**有一条测试专门守这件事**：

```bash
pytest tests/test_graph_topology.py -q     # 4 passed
```

它检查四件事：运行时能否读到拓扑、静态兜底是否和运行时一致、节点有没有漏出 `__start__` 这类原始 id、条件边有没有被标出来。**改 `graph.py` 却忘了改 `_STATIC_GRAPH`，它会立刻挂**——这就是"防漂移"从口号变成机制的地方。

---

## 六、留给你的五个练习

由易到难，每个都能直接验证：

1. **给审计页加时间范围筛选**：后端 `query()` 加 `start_ts` / `end_ts`，前端加两个 `el-date-picker`。（最简单，走一遍链路）
2. **把导出改成全量**：现在只导当前页。改成后端加 `/api/audit/export` 流式写 CSV。
3. **加第五个角色**：新加"只读审计员"，只能看审计页和画布页。挑战在于：路由守卫要拦、侧边栏要隐藏、按钮要禁用——三处都要改，**这会让你真正理解权限系统的分层**。
4. **审计详情里加"会话回放"**：按 `session_id` 把所有记录串起来，还原这一轮 Agent 到底做了哪几步。（这个做完，你的项目就有了别人没有的东西）
5. **画布加实时高亮**：对话进行中，让当前执行的节点亮起来。需要 SSE 事件和画布联动——**这是最难的一个，也是最能讲的一个**。

---

## 七、已知限制（面试被问时要主动说）

| 限制 | 说明 | 真实做法 |
|---|---|---|
| 权限只在前端 | 改前端就能绕过 | 后端接 JWT，`approver` 从 token 取，接口加 `Depends(require_admin)` |
| 审计查询整文件加载 | 每次查询都读全部 JSONL | 加内存索引或换 SQLite；10 万条以上必须换 |
| 导出只有当前页 | 全量导出会拉爆浏览器 | 后端流式 CSV |
| Element Plus 全量引入 | 主包 948KB | 用 `unplugin-vue-components` 按需引入，能砍到 300KB 以内 |
| 单机单用户 | 没有多租户、没有并发审批锁 | 加 `tenant_id` 字段 + 分布式锁 |
| 状态机没有"回滚" | JD 里提到灰度回滚，这里没实现 | 需要执行前快照 + 回滚脚本 |
| 依赖没锁 posthog | chromadb 0.5 与 posthog 4+ 签名不兼容，刷 `capture() takes 1 positional argument` | requirements 里已锁 `posthog<4` |

**这份清单本身就是加分项**：说明你知道边界在哪，而不是以为自己做完了。

### 附：这类"看起来像自己代码的错"怎么定位

那三条 telemetry 报错第一次看到时很像自己写错了，但它其实一行不在你的代码里。判断方法：

1. 报错信息里的函数名（`capture`）**在你的代码里搜不到** → 一定是第三方库
2. 搜 `site-packages` 或看报错栈顶 → 定位到 chromadb 调 posthog
3. 查两个库的版本约束：`posthog>=2.4.0` 没有上限，装上 4+ 就崩

**结论**：不是代码问题，是依赖没锁版本。修法是在 requirements 里加约束，而不是到处 `try/except` 把它藏起来。
