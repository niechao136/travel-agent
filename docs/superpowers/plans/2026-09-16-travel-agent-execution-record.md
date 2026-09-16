# 执行记录 — 旅游规划 Agent 实现计划

> 本文是 SDD（子代理驱动开发）工作区账本的持久化副本，记录 11 个任务执行期间的全部裁决、探针实测事实与最终审查结论。
> 分支：`feat/travel-agent-implementation`；起点：`89e26a8`；最终 HEAD：`b5271a8`（33 个提交）。
> 计划文件：`docs/superpowers/plans/2026-09-16-travel-agent-implementation.md`；规格：`PLAN.md`。

## 预检冲突扫描

| 任务对 | 产出 → 消费 | 发现 / 裁决 |
|---|---|---|
| 1 → 9 | `config.Settings`（checkpoint_db_path / auth_db_path）→ `default_graph` / `create_app` | 无冲突 |
| 2 → 3/4/6/7 | `state.py` 类型（TravelRequest / TravelRequestUpdate / GraphState / Itinerary）→ 各节点与图 | 无冲突；Itinerary 由任务 6 追加到 state.py |
| 3 → 4 | `make_ask_missing` / `check_required` → builder v1 | 无冲突 |
| 4 → 6/7 | builder v1 签名 + `tests/fakes.py` → 图演进；`test_interrupt_loop.py` 需在任务 6/7 同步更新 | 计划已内置更新步骤 |
| 5 → 6 | `AmapMCPClient.get_weather/geocode/search_pois` → build_itinerary | 无冲突 |
| 6 → 7 | `route_after_check` 的 "done" 分支 → 任务 7 改为 "build_itinerary" | 计划已内置 |
| 7 → 8 | `response_text` → executor artifact | 无冲突 |
| 8 → 9 | `TravelAgentExecutor` → `create_app` | 无冲突 |
| 9 → 10 | `create_app` 增加 auth_store；任务 9 的 API 测试需同步更新 | 计划已内置 |
| 10 → 11 | TokenStore / issue_token → demo 携带 token | 无冲突 |

## 裁决清单（Rulings）

1. 使用当前目录特性分支 `feat/travel-agent-implementation` 而非 git worktree 作为隔离工作区 — 用户在 IDE 中打开本目录、需要可见进度，分支已提供 master 隔离 — 若错：用户同时在 master 有改动时可能冲突（当时工作树干净，风险低）。
2. 任务 5 步骤 4 后半的真实高德 Key 网络冒烟暂缓到人工验收 — 子代理环境无真实 Key，验收标准 7 本就要求真实 Key 人工验证 — 若错：MCP 参数名偏差要到人工验收才暴露（探针脚本可快速修正）。
3. "真实 Key 人工验收"不派子代理自动化，作为交接中列给用户的操作项 — 需要用户凭据 — 若错：自动化覆盖少一条（测试已覆盖其余 6 条）。
4. **a2a-sdk 保持 1.1.2（不降级 0.3.x）**，任务 8/9/10/11 的 A2A 代码按 1.x（protobuf）API 重写 — 探针已在 1.1.2 上完整验证 `interrupt→input-required→resume→completed` 闭环（含终态任务保护 -32602），1.x task-mode 建模更贴近协议本质，且 `enable_v0_3_compat=True` 提供同端点 0.3 兼容 — 若错：任务 8/9/10 需再改一轮（概率低，原型已跑通）。
5. **mcp SDK 保持 2.2.0（不收紧 <2）**，任务 5 客户端与探针脚本按实测 API 更新（`streamable_http_client(url)` 二元组解包 + `is_error` 字段名）— 与 a2a 同策略（当前版本 + 探针实测适配）— 若错：真实 Key 冒烟时若尚有 API 细节偏差需再修一轮。
6. **`itinerary` 状态字段用 `dict | None`**（PLAN.md §4 原始类型），任务 6 定义 `Itinerary` 后升级为 `Itinerary | None` — 依据：langgraph `StateGraph(GraphState)` 会 `get_type_hints` 运行时解析注解，未定义名直接 `NameError`（审查者实测复现）— 若错：任务 6 需改一行。
7. **checkpointer 改用 `AsyncSqliteSaver`**（同包 langgraph-checkpoint-sqlite，非 MemorySaver，符合 SQLite 决策），并显式传 `serde=JsonPlusSerializer(allowed_msgpack_modules=...)`；生产工厂 `make_async_sqlite_checkpointer(db_path)` 用裸 `aiosqlite.connect(path)`（同步上下文可构造）— 依据：同步 `SqliteSaver` 与 async 图执行不兼容（实测 `NotImplementedError`）— 若错：生产路径首启报错（探针已先行验证）。
8. 计划缺陷修正（任务 4）：原计划 `llm.py` 引用 `Itinerary`/`ITINERARY_PROMPT`（任务 6 才产出，ImportError 火种）→ 本任务只做 `get_llm`+`llm_extractor`，`llm_summarizer`/`ITINERARY_PROMPT` 移到任务 6 追加（commit 46cde10）。
9. 统一 lint 清理轮（commit 894cb28）：计划逐字代码携带 6 处 ruff 问题（DTZ011/UP035/SIM117×2/F401×2）一次清零；DTZ011 修法适配为 `datetime.UTC` 别名（原裁定 `timezone.utc` 会触发 UP017）— 依据：计划验收要求 ruff 干净，债务不宜累积。
10. **checkpointer 连接生命周期**（用户报告 subagent 运行 `tests/test_interrupt_loop.py` 卡死）：根因确认为 `sqlite_checkpointer()` 创建的 aiosqlite 连接从不关闭，其工作线程挂住进程/命令捕获 → 改用 `tests/conftest.py` 的 `checkpointer` async fixture（yield + `await saver.conn.close()`），fakes 删除该函数；计划文档 10 处同步。同轮修复 `Itinerary` 未注册 serde allowlist（checkpoint 读回被静默降级为 dict，会卡任务 7）。
11. 任务 8 回到计划补需求：修正 `FakeGraph` 的 `paused is not None` 语义 + 追加 `test_multi_round_input_required_then_resume_completes`（同 task 连续三轮，断言 thread_id 恒定、第 2/3 轮走 `Command(resume)`、不重发 Task）— 依据：全局约束「必须验证」的多轮中断-恢复循环原本零覆盖。
12. 任务 10 修复：`expires_at` 字符串字典序比较会让非 UTC 偏移静默误判过期 → 新增 `_is_expired`（解析为 datetime 比较；解析失败按过期拒绝）。
13. 最终审查 6 条 Important 的统一修复（commit b5271a8）：补餐厅/酒店 POI 调用、补 executor↔真实图 API 集成测试、修正第二类中断问句金额措辞、resume 解析容错、必填校验拒绝零/负预算与日期倒挂、过期回归测试改为能区分新旧实现的构造；另有卫生项 6 项。

## 探针实测事实（关键，供后续维护参考）

**a2a-sdk 1.1.2**：协议版本经 HTTP header `A2A-Version: 1.0` 协商（缺失按 0.3 拒绝 -32009）；方法名 gRPC 风格 `SendMessage`；请求 `params.message{messageId,role:"ROLE_USER",parts:[{text}],taskId?,contextId?}`；响应 `result.task{...}`、状态 `TASK_STATE_*`；executor 首次调用必须先 `enqueue_event(Task(...TASK_STATE_SUBMITTED))` 再发状态事件（否则 `InvalidAgentResponseError`）；`TaskUpdater` 提供 `start_work/requires_input/complete/failed/add_artifact/new_agent_message`；server 用 `add_a2a_routes_to_fastapi(app, agent_card_routes=create_agent_card_routes(card), jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url, enable_v0_3_compat=True))`；`AgentCard` 为 protobuf（无 `url` 字段，用 `supported_interfaces`）；`DefaultRequestHandler(agent_executor, task_store, agent_card)` 三参必传；well-known 路径 `/.well-known/agent-card.json`；`EventQueue` 仅有 `enqueue_event`。**0.3 兼容路径**（`enable_v0_3_compat=True`）：`message/send` + 无 header 的 interrupt→resume→completed 完整工作，响应 `result` 平铺、状态短名。

**mcp 2.2.0**：入口 `streamable_http_client(url)` 返回二元组 `(read_stream, write_stream)`；结果字段 snake_case `is_error`；`Tool.input_schema`（非 `inputSchema`）。

**langgraph 1.2.11**：`StateGraph(GraphState)` 运行时用 `get_type_hints` 解析 TypedDict 全部注解（未定义名 `NameError`）；同步 checkpointer 不支持 async 图执行；显式 serde 白名单「未注册即阻断」（`Blocked deserialization` 只进 log，pytest 不报 warning），降级为裸 dict。

## 任务执行状态

- 任务 1（骨架）→ 完成；任务 2（状态模型）→ 完成（1 轮修复）；任务 3（interrupt 机制）→ 完成
- 任务 4（抽取+多轮中断图）→ 完成；任务 5（MCP 客户端）→ 完成（1 轮修复）
- 任务 6（build_itinerary）→ 完成（1 轮修复：serde 白名单 + 连接生命周期）
- 任务 7（present_draft + 预算超支中断）→ 完成；任务 8（A2A Executor）→ 完成（1 轮修复：多轮循环测试）
- 任务 9（FastAPI + AgentCard）→ 完成；任务 10（鉴权）→ 完成（1 轮修复：过期比较）
- 任务 11（demo + README）→ 完成
- 最终整分支审查 → 6 条 Important → 一轮统一修复（`b5271a8`）→ 定向复审 A–G 全部 ADDRESSED

## 最终审查结论与残留项

最终审查评估为「修完再合」，修复后复审干净。自动化验收覆盖 PLAN.md 第 10 节前 6 条（40 passed）；**第 7 条（真实景点/天气/花费 + 餐饮住宿来自真实 POI）需真实高德/LLM Key 人工验收**。

已裁定不再修复的残留 Minor（留给后续维护）：

- README 的重启语义措辞与 SDK 实际行为不符（`InMemoryTaskStore` 重启后带原 `task_id` 会返回 TaskNotFound，即使图状态仍在 checkpoints.db）。
- `days=` 解析未吞 `OverflowError`（超量级值仍会让任务失败）。
- 预算调整阶段的负数输入（`days=-3`、`budget=-5000`）仍可能触发 `ZeroDivisionError` 或负预算入 prompt。
- `test_build_itinerary` 未断言上下文中的【餐厅 POI】/【酒店 POI】标签。
- `Bearer ` 前缀匹配大小写敏感（RFC 7235 规定 scheme 不敏感）；`scopes` 字段已存储但未消费。
- `uv.lock` 绑定清华镜像源而 `pyproject.toml` 未显式声明 index。
