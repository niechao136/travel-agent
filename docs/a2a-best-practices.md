# A2A 封装与调用最佳实践

> 从三个项目的实战实现中提炼：`travel-agent`（Server，中断）、`news-agent`（Server，SDK 全栈）、`a2a-gateway`（Client，生产级）
>
> 每条实践 = **做法** + **依据**（项目出处）+（必要时）**反模式**

---

## 0. 十条军规（TL;DR）

| # | 实践 | 一句话理由 |
| --- | --- | --- |
| 1 | 卡片是唯一契约，地址从请求上下文派生 | 写死 `localhost` 在容器/反代下必然连不上 |
| 2 | 协议层交给 SDK，业务层只写 Executor | 路由/状态机/SSE/版本兼容都是 SDK 的活 |
| 3 | Executor 不抛裸异常，只有三个出口 | `rejected`（请求错）/ `failed`（执行败）/ `completed(degraded)`（部分成功） |
| 4 | 事件顺序：先 Task 本体，再 status/artifact | 1.0 硬性要求；增量文本走 `statusUpdate`，绝不发裸 `message` |
| 5 | Artifact 双形态：DataPart + TextPart | 同时服务"要结构的机器"与"只要文本的人" |
| 6 | 入参宽容、出参规范 | 解析兼容三种入参形态；输出 schema 稳定可校验 |
| 7 | 中断 = 状态 + 消息 + 同一 taskId | `task_id == thread_id`；业务状态必须持久化 |
| 8 | 客户端四分支全消费、三处找内容、终态补拉 | 少读一处就会丢追问或丢结果 |
| 9 | 错误分类 + 有条件重试 + 双层超时 | 已产出内容不重试；客户端和服务端各自有超时 |
| 10 | 发现公开、业务鉴权、凭据可撤销 | `/.well-known/*` 必须匿名可读 |

---

## 1. 通用原则（三条）

### 1.1 契约优先：先定 Card 与 Schema，再写实现

**做法**：动手写代码前，先确定：Agent Card 字段（name/description/skills/io modes）→ 每个 skill 的请求参数与结果 JSON Schema → 状态与错误语义。

**依据**：
- `news-agent` 把 Schema 写进 `card.py` 的扩展（`skill-schemas`），并用 `GET /skills` 直接暴露；
- `a2a-gateway` 的 `A2ATarget.description` 被写进 LLM 工具说明——**契约里的文字是会进调用方模型上下文的**，描述质量直接影响"目标选择"准确率。

**反模式**：先写业务代码、最后随手拼一张卡片 → skills 描述敷衍、schema 与实际不符、调用方无从判断何时该调你。

### 1.2 分层：协议归 SDK，业务归 Executor

**做法**：只实现 `AgentExecutor.execute()/cancel()`；其余（路由、任务状态机、SSE、任务存储、0.3 兼容）交给 SDK 组件：

```python
# 标准装配（news-agent / travel-agent 共用套路）
handler = DefaultRequestHandler(
    agent_executor=MyExecutor(...),      # ← 唯一需要写的业务类
    task_store=InMemoryTaskStore(),      # ← 换成 DatabaseTaskStore 即持久化
    agent_card=card,
)
add_a2a_routes_to_fastapi(
    app,
    jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url=..., enable_v0_3_compat=True),
    rest_routes=create_rest_routes(handler),        # 可选：HTTP+JSON 绑定
    agent_card_routes=[...],                        # 1.0 + 0.3 双路径
)
```

**例外**：只有"无状态转发"这种特例才自研协议层（`a2a-gateway` 的 `routes/a2a_server.py`），而且仍要遵循任务式语义（见 §2.2）。

### 1.3 永不裸抛：每个失败都有协议含义

| 情况 | 出口 | 语义 |
| --- | --- | --- |
| 请求非法（缺参/未知 skill） | `updater.reject(...)` | 我的请求错了，改参数可重试 |
| 执行失败（无可用结果/内部错误） | `updater.failed(...)` | 环境/服务问题，稍后可重试 |
| 部分成功（超时/降级） | `updater.complete(...)` + 结果标 `degraded=true` | 有部分结果可用，不要重试整任务 |

**依据**：`travel-agent` 用 `try/except` 兜底任何异常 → `updater.failed`；`news-agent` 用 13 个稳定 `ErrorCode` + `retryable` 标记 + `RunContext.partial`。

---

## 2. 封装 A2A Server 的最佳实践

### 2.1 Agent Card：四个必做

**P1 · 地址从请求上下文派生**

```python
# travel-agent：base_url 为空时卡片用相对路径，well-known 路由按请求补全
@app.get("/.well-known/agent-card.json")
async def agent_card_well_known(request: Request):
    return JSONResponse(agent_card_to_dict(build_agent_card(str(request.base_url))))

# a2a-gateway：支持反代（优先 X-Forwarded-*）
def _public_base_url(request):
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
    return f"{proto}://{host}".rstrip("/")
```

**P2 · capabilities 与真实行为一致**

支持流式就声明 `streaming=True`（`news-agent`），不支持就别声明——`travel-agent` 的 `SetInParent()` 是"显式声明空 capabilities"的写法，它并未承诺流式。**调用方会按你的声明选择调用方式**。

**P3 · skill 描述是给"调用方的 LLM"看的**

`news-agent` 的每个 skill 包含：做什么、是否调 LLM、特有输出、`examples`（自然语言示例，供模型 few-shot 参考）。网关把它注册为工具时，说明里还会拼上目标的 `description`——**这是决定 LLM 选不选你的关键输入**。

**P4 · 1.0 没有 skill schema 字段 → 用扩展承载**

```python
# news-agent/card.py
skill_schema_extension = a2a_pb2.AgentExtension(
    uri="https://news-agent.dev/a2a/skill-schemas",
    description="JSON schemas for the DataPart payload (requestSchema) and result artifact",
    required=False,
    params=_struct({"requestSchema": _REQUEST_SCHEMA, "resultSchema": OUTPUT_SCHEMA}),
)
```

扩展的双保险：不理解的客户端安全忽略；严格的平台可据此做发送前校验。**发现路径同时提供 1.0 与 0.3 两条**（`/.well-known/agent-card.json` + `/.well-known/agent.json`）。

### 2.2 路由与装配

| 事项 | 最佳实践 | 依据 |
| --- | --- | --- |
| 自定义/运维路由 | **注册在 SDK 路由之前**，否则被 `/{tenant}` 挂载吞掉 | `news-agent/server.py` 注释 |
| 版本兼容 | `enable_v0_3_compat=True`（一行开关） | 两 Server 均启用 |
| TaskStore | 单副本 `InMemoryTaskStore`；多副本/要重启不丢 → `DatabaseTaskStore` | `news-agent` README §10 |
| 业务路由路径 | 卡片 `supportedInterfaces[].url` 与 `rpc_url` 必须一致（如 `/a2a`） | `travel-agent` 的 `A2A_RPC_PATH` |
| 事件语义（自研 Server 时） | 首事件 `Task` → 增量走 `statusUpdate` → 终态 `completed`；**绝不发裸 `message`** | `a2a-gateway/a2a_server.py` 注释（SDK 客户端收到裸 message 会立即终止流） |

### 2.3 Executor：标准骨架与四个关键实现

**骨架**（虚线以上是流程，以下是要点）：

```python
async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
    task_id, context_id = context.task_id, context.context_id
    updater = TaskUpdater(event_queue, task_id, context_id)

    # ① 先发布 Task 本体（1.0 要求；续跑时 current_task 已存在，不重复发）
    if context.current_task is None:
        await event_queue.enqueue_event(Task(id=task_id, context_id=context_id,
                                             status=TaskStatus(state=TASK_STATE_SUBMITTED)))
    # ② 入参宽容解析；失败 → rejected（不抛异常）
    try:
        request = await self._build_request(context)
    except SkillRequestError as exc:
        await updater.reject(updater.new_agent_message([...text + 结构化错误...]))
        return

    await updater.start_work(...)          # ③ 进入 working
    result = await self._run(request)      # ④ 跑业务（进度/超时/取消见下）

    # ⑤ 产物 + 终态
    await updater.add_artifact(parts, artifact_id=f"{task_id}-result", name="...", metadata={...})
    await updater.complete(...)  # 或 updater.failed(...)
```

**四个关键实现**：

1. **入参宽容解析**（`news-agent/_extract_payload`）：按 `DataPart → text 里的 JSON → metadata → 纯文本` 依次尝试；纯文本还支持 `"skill: query"` 前缀约定。**出参则严格**（固定 schema + 稳定字段名）。
2. **进度发布**（`news-agent/_publish_progress`）：`updater.update_status(WORKING, message=..., metadata={"stage": ..., "kind": ..., "data": ...})`——**用标准事件的 metadata 承载扩展信息**，并在卡片 extensions 里声明该约定。
3. **取消 + 竞态**（`news-agent/cancel`）：把 `asyncio.current_task()` 存进 `self._running[task_id]`；`cancel()` 里 `handle.cancel()` 后 `updater.cancel(...)`；**捕获 `RuntimeError`**（任务已终态是正常竞态，不是错误）。
4. **超时给部分结果**（`news-agent/_timeout_result`）：`asyncio.wait_for(...)` 超时后返回 `RunContext.partial`（已完成的阶段结果），标 `degraded=True`、附 `task_timeout` 错误码——**比"无响应"或"整任务失败"都有用**。

**artifact 规格建议**：`artifact_id`（如 `{task_id}-result`）、`name`（如 `news-result`）、`metadata`（query/mode/counts/degraded 等摘要字段），parts 采用 DataPart + TextPart 双形态。

### 2.4 中断（input-required）：五条实现纪律

**映射关系**（LangGraph ↔ A2A，`travel-agent` 实证）：

| 业务框架概念 | A2A 概念 |
| --- | --- |
| `interrupt(payload)` 触发挂起 | `TASK_STATE_INPUT_REQUIRED` + `status.message` 承载追问 |
| `Command(resume=user_text)` | 调用方带**同一 taskId** 再次 `SendMessage` |
| `thread_id` | **`= task_id`**（恢复的索引） |
| `snapshot.next` 非空 | "上次是中断挂起"的判定条件 |
| checkpointer（持久化） | 中断恢复能力的真正依赖 |

**五条纪律**：

1. **`task_id == thread_id`**——一个 A2A 任务对应一条对话线程，恢复时无需任何会话查找逻辑；
2. **恢复判定只看"图是否还有待执行节点"**（`snapshot.next`），不要靠自定义标志位；
3. **追问文本必须放 `status.message.parts`**（可加 DataPart 给机器解析），中断阶段没有 artifact；
4. **resume 值容错解析 + 中断次数上限**：非法输入吞掉保持原值（`buy=五千` 不能炸成永久 failed），上限（如 `MAX_BUDGET_ADJUSTS=2`）防死循环；
5. **业务状态持久化，协议任务可选**：恢复靠 checkpointer（必须落盘）；`GetTask` 查询能力靠 TaskStore（内存可接受，换 DB 更强）。

**必测用例**：单轮中断 → 多轮中断循环 → 并发任务互不串线 → 中断后不带 taskId 应开新任务 → 重启后带原 taskId 仍可恢复。

### 2.5 鉴权：中间件三原则 + 凭据三原则

**中间件三原则**（`news-agent/auth.py`、`travel-agent/auth.py`）：

1. **用 ASGI 中间件，不用 FastAPI 依赖**——A2A 路由由 SDK 动态注册，`Depends()` 覆盖不到；
2. **白名单豁免**：`/.well-known/*` + 健康检查永远公开（发现先于鉴权）；
3. **鉴权失败返回标准 401 + `WWW-Authenticate`**，不泄露内部细节。

**凭据三原则**（`travel-agent` 的 `TokenStore` 范本）：

1. **只存哈希**（SHA256），明文仅在发放时返回一次；
2. **常量时间比较**（`hmac.compare_digest`）+ 过期时间（解析为 datetime 比较，别用字符串字典序）；
3. **按调用方撤销**（`caller_name` 维度），便于审计与单独吊销。

**声明义务**：启用鉴权时，卡片必须带上 `securitySchemes`（bearer / api_key）与 `security` 要求，并在描述里写清凭据怎么带（`Authorization: Bearer` 或 `X-API-Key`）。

### 2.6 错误契约：两层分离

| 层 | 载体 | 规范 | 项目实例 |
| --- | --- | --- | --- |
| 协议错误 | JSON-RPC error | 标准码：`-32601` 方法不存在 / `-32602` 参数不合法 / `-32001` 任务不存在 / `-32002` 不可取消 | SDK 自动返回；`a2a-gateway` 手写时用 `build_error_response` |
| 业务错误 | 结果数据内 `errors[]` | **稳定错误码 + message + stage + retryable** | `news-agent` 的 13 个 `ErrorCode` |

要求：
- 错误码稳定、可枚举（调用方据此决策重试/降级/告警）；
- `retryable` 显式标注（如 `no_results` 可重试、`invalid_request` 不可）；
- `rejected` 任务的 `status.message` 同时给**文本说明 + 结构化数据**（`{"error":"invalid_request","validSkills":[...]}`）。

### 2.7 运维：三件套

1. **健康/就绪分离**：`/healthz` 只报告已知状态（永不建重对象）；`/readyz` 按需预热（`news-agent` 会预热 agent，'第一次请求快'）；容器健康检查探 `/healthz`；
2. **可观测字段固定化**：阶段耗时（`timings_ms`）、任务计数（`a2a_tasks_submitted/completed/failed/rejected/canceled/timeouts`）、降级标记（`degraded`）、LLM token 数；失败路径要能告警（`ALERT_WEBHOOK_URL`，未配置时退化为 ERROR 日志）；
3. **部署形态匹配存储**：单副本 → 内存 TaskStore；多副本 → `DatabaseTaskStore` 共享库；业务状态（checkpointer）与协议任务（TaskStore）分开选型。

---

## 3. 调用 A2A Agent 的最佳实践

### 3.1 发现阶段：解析卡片 + 两个必要的修正

**标准解析**：`A2ACardResolver(http, base_url).get_agent_card()`（1.0 路径 `/.well-known/agent-card.json`，0.3 目标用 `/.well-known/agent.json`）。

**修正 1 · 解析失败回退 origin**（用户可能把 RPC 端点误填为服务地址）：

```python
try:
    card = await A2ACardResolver(http, url).get_agent_card()
except (AgentCardResolutionError, httpx.HTTPError):
    origin = _origin_url(url)          # http://host:10101/a2a → http://host:10101
    if origin == url: raise
    card = await A2ACardResolver(http, origin).get_agent_card()
```

**修正 2 · 把卡片里的"内部地址"改写为"可达地址"**（跨容器必做）：

```python
def _merge_interface_url(iface_url: str, target_url: str) -> str:
    """规则：
    - target_url 自带路径（非 /）→ 用户显式指定 → 原样使用；
    - 否则 → 以 target_url 的 host 为基准，路径取卡片声明，保留 target_url 的查询串。"""
```

要点：**替换 host/port、保留卡片声明的 path**（否则丢 `/a2a` 打到根路径 404）、保留 query（query 式鉴权参数挂在那里）、支持 `X-Forwarded-*` 场景。

**调试场景的第三条修正**：显式覆盖接口地址（`news-agent` 客户端的 `interface_url=args.base_url`）——端口映射/代理下"卡片地址"与"我该连的地址"不一致时使用。

### 3.2 建连：按卡片声明做协商

```python
config = ClientConfig(
    streaming=True,                                # 期望流式（对应卡片 capabilities.streaming）
    polling=False,
    httpx_client=http,                             # 复用连接、统一超时
    supported_protocol_bindings=[TransportProtocol.JSONRPC],  # 我支持的绑定
)
client = ClientFactory(config).create(card)        # 和服务端声明求交集
```

实践：
- 客户端实例**按目标缓存**（`a2a-gateway` 的 `agent_factory` 按 `(id, updated_at)` 缓存 wrapper 与图，配置变更自动失效）；
- HTTP 客户端复用（`httpx.AsyncClient`），超时按任务类型设定（60s 网关 / 300s 长任务）。

### 3.3 发送消息：三个字段语义 + 双写 Part

| 字段 | 语义 | 实践 |
| --- | --- | --- |
| `messageId` | 本条消息唯一标识 | 每次调用 `uuid4().hex` 新生成 |
| `taskId` | 延续哪个任务 | **仅中断续跑/重试时携带**；首次调用不要带 |
| `contextId` | 延续哪个上下文 | 可跨任务；服务端应沿用（`news-agent` 明确支持按 context 过滤 ListTasks） |

```python
message = a2a_pb2.Message(
    message_id=uuid.uuid4().hex,
    role=a2a_pb2.ROLE_USER,
    parts=[
        new_data_part(payload, media_type="application/json"),          # 机器可解析（业务参数）
        new_text_part(f"{skill}: {query}", media_type="text/plain"),    # 人可读 + 兼容纯文本 Agent
    ],
)
```

**鉴权承载要可配置**（`a2a-gateway/auth_scheme.py` 的 5 种）：`bearer`（Authorization 头）/ `header`（自定义头名）/ `query`（URL 参数）/ `basic` / `none`——现实世界的 A2A 目标不都遵循同一种凭据位置。

### 3.4 消费响应：四分支 + 三处内容 + 一次补拉

**四分支全覆盖**（`StreamResponse` 是 oneof）：

```python
if response.HasField("task"):            # 任务快照（含聚合后的最终态）
elif response.HasField("status_update"): # 状态流转（+ 进度 metadata）
elif response.HasField("artifact_update"): # 产物增量
elif response.HasField("message"):       # 裸消息（兼容一问一答式实现）
```

**三处内容都要检查**（漏一处就丢内容）：

| 位置 | 承载什么 |
| --- | --- |
| `task.status.message` | 追问文本 / 完成说明 / 错误说明 |
| `status_update.status.message` | 进度文本 |
| `artifact.parts[]` | 结果（`data` 读结构、`text` 读文本） |

**一次终态补拉**：`status_update` 到终态时，**再调 `GetTask` 拉完整 Task**（补上可能缺失的 artifacts）：

```python
if update.status.state in TERMINAL_STATES:
    task = await self._safe_get_task(update.task_id)   # 失败也不抛（产物尽力而为）
    if task is not None:
        yield TaskUpdate(kind="task", state=..., task=task)
    return
```

**两条数据修正**：
- 兼容"SDK 聚合单快照"的返回形态（`a2a-gateway` 注释：对 `task` 直接跳过会丢掉全部内容）；
- 读取 `Part.data` 时**还原整数**（protobuf double → `10.0` 变回 `10`）。

### 3.5 健壮性：错误三分类 + 有条件重试 + 双层超时

```python
# 错误分类（a2a-gateway/_classify）：network / timeout / target_error 三档
#   AgentCardResolutionError、httpx.HTTPError        → network（目标不可达/未发布）
#   A2AClientTimeoutError、httpx.TimeoutException    → timeout
#   A2AClientError                                   → target_error（目标内部错误）

# 有条件重试（stream_message）
for attempt in range(retries + 1):
    yielded = False
    try:
        async for chunk in ...:
            yielded = True
            yield chunk
        return
    except Exception as error:
        classified = self._classify(error)
        # 关键三条：已产出不重试 / 次数上限 / 只重试 network|timeout
        if yielded or attempt >= retries or classified.kind not in ("network", "timeout"):
            raise classified from error
        await asyncio.sleep(backoff * (attempt + 1))
```

**双层超时**：客户端 HTTP 超时（60s/300s）之外，服务端必须有任务级超时（`TASK_TIMEOUT_S`）并返回部分结果——最大风险是"客户端超了、服务端还在跑"。

### 3.6 与 LLM 集成（把 A2A 目标变成模型可用的工具）

`a2a-gateway` 的实践（对其他平台通用）：

1. **一个目标 = 一个工具**：只有单个目标时沿用 `a2a_call`；多目标时按目标名生成唯一工具名（中文名规整后可能重复，用序号兜底）；
2. **目标描述进工具说明**——"否则大模型无法判断该调用哪个目标"；必要时拼上该目标的能力与适用场景；
3. **工具失败转成"模型可读的文本"**而不是抛异常：捕获 `A2ATargetError` → 告警（`notify_alert`）→ 返回 `"A2A 调用失败：..."`，让模型有机会换策略或向用户解释；
4. **结果聚合返回**：把流式片段拼成完整文本给 LLM（注意中断场景下这个模式需要改造，见 §2.4）。

---

## 4. 双边契约约定（联调前对齐清单）

| 约定项 | 服务端义务 | 客户端义务 | 项目做法 |
| --- | --- | --- | --- |
| 卡片路径 | 同时提供 1.0 与 0.3 两条 well-known | 先试 1.0，失败回退 0.3 | `news-agent` 双路由 |
| 版本 | `enable_v0_3_compat=True` | 1.0 调用带 `A2A-Version: 1.0` | `travel-agent` |
| 地址 | 卡片地址 = 调用方可达地址 | 不可达时按规则改写（host 换、path 留） | `a2a-gateway` |
| 入参 | 宽容解析（DataPart/文本/metadata） | DataPart + TextPart 双写 | 全部项目 |
| 出参 | schema 稳定（可在 extensions 声明） | 读 `artifact.parts`，还原整数 | `news-agent` |
| 进度 | `statusUpdate.metadata`（声明扩展） | 按扩展约定解读 | `news-agent` |
| 中断 | `input-required` + `status.message`；同 taskId 续跑 | 识别状态、带回 taskId/contextId | `travel-agent` |
| 错误 | 协议错误用标准码；业务错误码稳定 + retryable | 分类处理 + 有条件重试 | 两者 |
| 鉴权 | 卡片声明 securitySchemes；发现路径公开 | 按声明的方案带凭据 | 三者 |

---

## 5. 测试清单

**Server 端**（参考 `news-agent` 68 用例 + `travel-agent` 12 用例的覆盖面）：

- [ ] Agent Card：双 well-known 路径、扩展字段、securitySchemes（启用鉴权时）
- [ ] JSON-RPC：1.0 方法名（带 `A2A-Version`）、0.3 方法名、未知方法 `-32601`、参数不合法、任务不存在 `-32001`
- [ ] REST 绑定：`/message:send`、`/tasks/{id}`、`/tasks/{id}:cancel`
- [ ] SSE：事件序列（task → statusUpdate… → artifact → completed）、进度 metadata 的 stage 值
- [ ] 参数校验 → `rejected`；内部错误 → `failed`；超时 → `completed(degraded)` + 部分结果
- [ ] 取消：运行中取消成功；终态后取消不报错
- [ ] 中断：单轮/多轮循环、并发隔离、重启恢复（带原 taskId）
- [ ] 鉴权：无凭据 401、错误凭据 401、发现路径免鉴权

**Client 端**（参考 `a2a-gateway` 的 40 用例）：

- [ ] 错误三分类（含 httpx 超时被正确归为 timeout 的回归用例）
- [ ] 重试：瞬时错误重试 2 次；**已产出内容不重试**；非网络错误不重试
- [ ] 卡片解析：正常 / 带路径误配回退 origin / 地址改写（host 替换 + path 保留）
- [ ] 流消费：四分支、终态补拉失败不崩、整数还原
- [ ] 连通性测试接口可用（管理面"测试连接"）

**离线测试策略**：三个项目的测试全部离线可跑——mock 新闻源 / mock 图 / 不连库不联网，这是"协议层可测"的前提。

---

## 6. 上线检查表

**Server 上线前**：

- [ ] 卡片地址动态派生（无 `localhost` 硬编码）；容器内 `AGENT_URL` 指向调用方可达地址
- [ ] `capabilities` 与实际行为一致；skills 描述完整（含 examples）
- [ ] 发现路径公开；业务端点鉴权；凭据哈希存储 + 可撤销
- [ ] 事件顺序正确（Task 先行）；异常全部落到三出口
- [ ] 超时 → 部分结果；取消支持与否已明确（不支持就明示）
- [ ] 业务状态持久化（换重启恢复测试）；多副本时换共享 TaskStore
- [ ] 可观测：任务计数、阶段耗时、降级标记、失败告警

**Client 上线前**：

- [ ] 卡片解析带回退；地址改写规则明确；超时分层设置
- [ ] 错误分类 + 有条件重试；重试不产生重复内容
- [ ] 流消费四分支全覆盖；终态补拉；数据修正（整数）
- [ ] 多目标时工具命名唯一、描述齐全；失败转可读文本 + 告警
- [ ] 中断场景：识别 `input-required` 并能原样续跑（本轮不支持就要显式降级提示）

**联调验收**（三步）：

```bash
# ① 发现：读得到卡片（curl /.well-known/agent-card.json）
# ② 单轮：SendMessage 一次拿到 completed + artifact
# ③ 多轮：中断 → 追问可见 → 带 taskId 续跑 → completed（travel-agent demo 脚本就是这条）
```

---

> 一句话总结：**封装时让 SDK 挡住协议复杂度、让 Executor 只表达业务语义；调用时假设一切都会出问题（地址、版本、错误、超时、中断），并为每种问题准备好确定的处理路径。**
