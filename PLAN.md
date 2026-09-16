# 旅游规划 Agent — 项目说明

## 1. 项目目标

开发一个**真实可用**的旅游规划 Agent，核心用途是：用户输入旅行需求（可能不完整），Agent 通过对话补全必填信息，调用高德地图 MCP 获取真实地理/天气/路线数据，最终产出一份逐日行程方案。

这个项目同时承担一个技术验证目标：**测试 A2A 协议对 Agent 中断（interrupt）/ 恢复（resume）流程的支持是否完善**。因此中断逻辑不是为了演示而硬加的，而是产品本身就需要的"信息不全时向用户追问"能力，这样测试出来的结论才有意义。

该 Agent 未来会作为自定义 Agent 接入到多 Agent 平台（通过 A2A 被 Hermes 网关调用），当前阶段先独立开发、独立测试。

## 2. 技术栈

- **编排框架**：LangGraph（Python）
- **后端服务**：FastAPI
- **外部工具**：高德地图 MCP Server（Streamable HTTP，官方托管）
- **协议**：A2A（Agent-to-Agent），对外暴露标准 AgentCard + JSON-RPC/HTTP 端点
- **鉴权**：Bearer Token / 自定义 Header（见第 6 节）
- **对话状态**：LangGraph Checkpointer（开发阶段用内存或 SQLite，需支持 `thread_id` 与 A2A 的 `task_id` 对齐）

## 3. 功能需求

### 3.1 必填信息

| 字段 | 说明 | 缺失时行为 |
|---|---|---|
| destination | 目的地 | 中断，追问 |
| dates | 出行日期（start_date + end_date） | 中断，追问 |
| budget | 预算总额（含币种，默认 CNY） | 中断，追问 |

### 3.2 可选信息

- travelers（出行人数，默认 1）
- preferences（偏好标签，如"自然""美食""文化"）

### 3.3 核心流程

1. 用户输入旅行需求（可能只包含部分信息）
2. Agent 用结构化抽取（function calling / structured output）解析并**增量合并**进状态
3. 检查必填字段：
   - 有缺失 → **中断**，用自然语言一次性问出所有缺失项 → 等待用户回复 → 回到第 2 步（循环，直到信息补全）
   - 齐全 → 进入行程生成
4. 调用高德 MCP 工具获取真实数据（天气、POI、路线），生成逐日行程草案，包含预算分配
5. （可选增强）若预算明显不足，触发第二类中断，询问是否调整预算或天数
6. 输出最终行程

## 4. 状态设计

```python
from pydantic import BaseModel
from datetime import date
from typing import TypedDict

class TravelRequest(BaseModel):
    destination: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    budget: float | None = None
    currency: str = "CNY"
    travelers: int | None = None
    preferences: list[str] = []

class GraphState(TypedDict):
    request: TravelRequest
    missing_fields: list[str]
    itinerary: dict | None
    messages: list
```

## 5. LangGraph 图结构

```
START
  → extract_and_merge      # 结构化抽取用户最新回复，增量合并进 request
  → check_required          # 条件边，产出 missing_fields
      ├─ 有缺失 → ask_missing (interrupt) → [resume] → extract_and_merge  # 循环
      └─ 齐全   → build_itinerary → present_draft → END
```

关键实现要点：

- `extract_and_merge` 每次只合并"新提到的字段"，不覆盖已有值，避免用户分多轮回答时丢信息
- `ask_missing` 内部调用 `interrupt({"missing": [...], "question": "..."})`，一次性把所有缺失字段组织成一句自然语言提问
- 如果用户只回答了部分缺失字段，图会自然再次进入 `ask_missing`，形成多轮中断 —— 这正是测试 A2A 多次 `input-required ↔ resume` 循环的关键场景
- `build_itinerary` 必须调用高德 MCP 工具产出真实数据，不能仅靠 LLM 编造

## 6. 高德地图 MCP 接入

**接入方式**（Streamable HTTP，官方托管，无需自建服务）：

```json
{
  "mcpServers": {
    "amap-maps": {
      "url": "https://mcp.amap.com/mcp?key=你的高德Key"
    }
  }
}
```

**用到的工具**：

| 工具 | 用途 | 使用节点 |
|---|---|---|
| `maps_weather` | 查目的地天气，辅助行程建议 | build_itinerary |
| `maps_text_search` / `maps_around_search` | 搜索景点/餐厅/酒店 POI | build_itinerary |
| `maps_geo` | 地址转坐标 | build_itinerary（前置） |
| `maps_direction_driving` / `walking` / `transit` / `bicycling` | 计算景点间通勤方式与耗时 | build_itinerary |
| `maps_distance` | 辅助排序邻近景点，规划每日路线 | build_itinerary |

预算分配建议逻辑：`每日预算上限 = budget / 天数`，据此筛选餐饮/住宿档次；如结果超支，触发第二类中断询问用户是否调整。

## 7. 对外暴露：A2A 服务

### 7.1 AgentCard（示例骨架）

```json
{
  "name": "travel-planner-agent",
  "description": "根据目的地/日期/预算生成真实可执行的逐日旅行方案",
  "endpoint": "https://<your-domain>/a2a/travel-planner",
  "skills": [
    {"id": "plan_trip", "description": "生成旅行行程，缺少必填信息时会中断询问"}
  ],
  "securitySchemes": {
    "bearerAuth": {"type": "http", "scheme": "bearer"}
  },
  "security": [{"bearerAuth": []}]
}
```

### 7.2 中断 ↔ A2A 状态映射

- LangGraph `interrupt()` 的返回值 → 映射为 A2A task 的 `input-required` state，`question` 字段放入 `message.parts`
- 客户端收到 `input-required` 后展示问题，用户回答 → 网关用 `Command(resume=user_reply)` 恢复对应 `thread_id`（务必与 A2A 的 `task_id` 保持一致映射）
- **必须验证**：同一个 task 连续多次进入 `input-required → resume` 循环时，状态是否正确延续（这是本项目最核心的验证点）

## 8. 鉴权设计

采用 Bearer Token / 自定义 Header：

- Token 哈希存储（SHA256），不存明文，数据库只保留 `token_hash`
- `api_tokens` 表字段：`id, token_hash, caller_name, scopes, status, created_at, expires_at, last_used_at`
- 每个调用方（如测试客户端、未来的 Hermes 网关）单独发放 token，便于按 `caller_name` 审计和单独撤销
- 发放方式：命令行脚本即可，不需要单独做管理 UI
- FastAPI 用 `APIKeyHeader` 或 `HTTPBearer` 做依赖注入校验

## 9. 建议目录结构

```
travel-agent/
├── app/
│   ├── main.py                # FastAPI 入口，挂载 A2A 端点
│   ├── graph/
│   │   ├── state.py           # GraphState / TravelRequest 定义
│   │   ├── nodes.py           # extract_and_merge / check_required / ask_missing / build_itinerary
│   │   └── graph.py           # 组装 LangGraph
│   ├── mcp_client.py          # 高德 MCP 客户端封装
│   ├── auth.py                # token 校验依赖
│   └── a2a_adapter.py         # interrupt ↔ input-required 状态映射
├── scripts/
│   └── issue_token.py         # 发放新 token
├── tests/
│   └── test_a2a_interrupt_loop.py   # 多轮中断恢复测试
├── .env.example
└── README.md
```

## 10. 验收标准 / 测试计划

1. 用户只提供目的地 → Agent 正确中断两次分别追问日期和预算（或一次性问全）
2. 用户分多轮陆续补全信息 → 每轮 `extract_and_merge` 正确增量合并，不丢已有字段
3. 高德 MCP 调用失败时（如 key 无效）→ Agent 有兜底提示，不裸抛异常
4. A2A 客户端能正确识别 `input-required` 状态并在用户回复后成功 `resume` 同一个 task
5. 同一 task 连续两次以上中断-恢复循环，状态不丢失、不串线
6. 无效/已撤销 token 请求被正确拒绝（401）
7. 最终产出的行程包含真实景点名称、天气信息、每日预估花费

## 11. 开发顺序建议

1. 先在无 A2A 包装的情况下，用 LangGraph 本地调试跑通"多轮缺字段中断 + 高德数据产出行程"的完整逻辑
2. 再接入 FastAPI + AgentCard，包装成标准 A2A 服务
3. 最后接入鉴权层
4. 用一个简单的 A2A 测试客户端脚本，模拟"提供部分信息 → 收到 input-required → 补充信息 → resume"的完整往返，验证第 10 节的验收标准