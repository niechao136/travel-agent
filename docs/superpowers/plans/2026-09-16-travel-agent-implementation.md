# 旅游规划 Agent 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 subagent-driven-development（推荐）或 executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 构建"对话补全必填信息 → 调高德 MCP 取真实数据 → 产出逐日行程"的旅游规划 Agent，并以标准 A2A 服务暴露，重点验证多轮 `interrupt ↔ resume` 循环。

**架构：** LangGraph 图（extract_and_merge → check_required → ask_missing/build_itinerary → present_draft）承载业务逻辑，`interrupt()` 暂停等待用户输入；A2A `AgentExecutor` 把 `__interrupt__` 映射为 `input-required` 状态，用 `thread_id = A2A task_id` + `Command(resume=...)` 恢复；FastAPI 挂载 A2A Starlette 应用，外层 ASGI 中间件做 Bearer Token 校验。

**技术栈：** Python >= 3.11（uv 管理）、langgraph + langgraph-checkpoint-sqlite（SQLite 持久化）、langchain-openai（OpenAI 兼容接口）、mcp（官方 SDK，Streamable HTTP 连高德）、a2a-sdk（>=0.3）、FastAPI + uvicorn、pytest + pytest-asyncio、httpx。

**规格：** `PLAN.md`（本计划的论证依据，执行者两份都要读）。

---

## 全局约束

- Python >= 3.11；包管理用 uv；所有命令在仓库根目录 PowerShell 下执行。
- 图的每次调用必须带 SQLite `checkpointer`（`SqliteSaver`，经 `make_sqlite_checkpointer()` 创建，`build_graph` 必传该参数，不再提供内存默认），`thread_id` 一律等于 A2A `task_id`（避免同一会话多个 task 串线，对应 PLAN.md 7.2）。同步 saver 在 async 图执行中由 langgraph 自动线程化调用；测试统一用 `sqlite3.connect(":memory:")` 保证隔离；未来接入 Hermes 生产环境时可平滑替换为 Postgres saver。
- `extract_and_merge` 只合并"新提到的字段"（None 不覆盖），对应 PLAN.md 5。
- `build_itinerary` 必须调用高德 MCP 工具；MCP 失败时降级生成并加 warnings，**不得裸抛异常**（对应 PLAN.md 10-3）。
- token 只存 SHA256 哈希；`api_tokens` 表字段与 PLAN.md 8 一致。
- commit 用 Conventional Commits（feat/fix/test/chore/docs）。
- 每个任务结束必须 `uv run pytest -q` 全绿再 commit。
- **版本兼容注意**：a2a-sdk 0.2→0.3 有字段改名（如 `type`→`kind`、well-known 路径 `agent.json`→`agent-card.json`）；若某行 import 或字段报错，以安装版本源码（`a2a/types.py`、`a2a/server/tasks/task_updater.py`）为准做等价微调，不改变行为语义。mcp SDK 的 `streamablehttp_client` 返回三元组 `(read, write, get_session_id)`。

## 文件结构

```
travel-agent/
├── pyproject.toml                      # 依赖、pytest/ruff 配置
├── .env.example                        # 环境变量模板
├── app/
│   ├── __init__.py
│   ├── config.py                       # Settings（pydantic-settings，读 .env）
│   ├── llm.py                          # get_llm + extractor/summarizer 适配器
│   ├── graph/
│   │   ├── __init__.py
│   │   ├── state.py                    # TravelRequest/TravelRequestUpdate/GraphState/Itinerary/merge 逻辑
│   │   ├── nodes.py                    # check_required / ask_missing / extract_and_merge / build_itinerary / present_draft / ask_budget_adjust
│   │   ├── prompts.py                  # 抽取与行程生成 prompt
│   │   └── builder.py                  # build_graph 组装（图结构演进见任务 4/6/7）
│   ├── mcp_client.py                   # AmapMCPClient（Streamable HTTP）
│   ├── a2a_adapter.py                  # TravelAgentExecutor：interrupt ↔ input-required 映射
│   ├── auth.py                         # TokenStore + BearerAuthMiddleware
│   ├── agent_card.py                   # AgentCard 定义
│   └── main.py                         # create_app：FastAPI + A2A 挂载 + 鉴权
├── scripts/
│   ├── amap_probe.py                   # 列出高德 MCP 全部工具与参数（固化参数用）
│   ├── issue_token.py                  # 发放 token（CLI）
│   └── a2a_client_demo.py              # A2A 手工测试客户端（验收 4/5 用）
├── tests/
│   ├── fakes.py                        # FakeExtractor/FakeSummarizer/FakeMCP/FakeGraph 等共享假件
│   ├── test_smoke.py / test_state.py / test_nodes.py / test_interrupt_loop.py
│   ├── test_mcp_client.py / test_build_itinerary.py / test_full_graph.py
│   ├── test_a2a_adapter.py / test_main.py / test_auth.py
├── data/                               # tokens.db + checkpoints.db（gitignore）
└── docs/superpowers/plans/             # 本计划
```

与规格差异说明：PLAN.md 9 的 `graph/graph.py` 更名为 `graph/builder.py`（避免与包名重复），`Itinerary` 数据模型并入 `state.py`，新增 `config.py`/`llm.py`/`prompts.py`/`agent_card.py`（单一职责）。`src/`（uv init 生成的 hello world）删除。

## 任务总览

| 任务 | 交付物 | 对应 PLAN.md |
|---|---|---|
| 1 | 项目骨架与依赖 | 2 / 9 |
| 2 | 状态模型与增量合并 | 3.1 / 3.2 / 4 |
| 3 | check_required + ask_missing（interrupt 机制） | 5 |
| 4 | extract_and_merge + LLM 适配 + 多轮中断图 | 3.3 / 5 |
| 5 | 高德 MCP 客户端 | 6 |
| 6 | build_itinerary（真实数据 + 失败兜底） | 6 / 10-3 |
| 7 | present_draft + 预算超支第二类中断 | 3.3-5 / 6 |
| 8 | A2A AgentExecutor（中断↔状态映射） | 7.2 |
| 9 | FastAPI + AgentCard + JSON-RPC 端点 | 7.1 |
| 10 | 鉴权（TokenStore + 中间件 + CLI） | 8 / 10-6 |
| 11 | demo 客户端 + README + 验收核对 | 10 |

---

### 任务 1：项目骨架与依赖

**文件：**
- 修改：`pyproject.toml`
- 创建：`.env.example`、`app/__init__.py`、`app/config.py`、`tests/test_smoke.py`
- 删除：`src/`（hello world）

- [ ] **步骤 1：重写 pyproject.toml**

```toml
[project]
name = "travel-agent"
version = "0.1.0"
description = "旅游规划 Agent：LangGraph + FastAPI + 高德 MCP + A2A"
readme = "README.md"
requires-python = ">=3.11"
dependencies = [
    "langgraph>=0.4",
    "langgraph-checkpoint-sqlite>=2",
    "langchain-core>=0.3",
    "langchain-openai>=0.3",
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "a2a-sdk>=0.3.0",
    "mcp>=1.9",
    "pydantic>=2.7",
    "pydantic-settings>=2.3",
    "python-dotenv>=1.0",
]

[dependency-groups]
dev = [
    "pytest>=8",
    "pytest-asyncio>=0.24",
    "httpx>=0.27",
    "ruff>=0.6",
]

[tool.pytest.ini_options]
pythonpath = ["."]
asyncio_mode = "auto"

[tool.ruff]
line-length = 100
```

- [ ] **步骤 2：创建 .env.example 与包骨架**

`.env.example`：

```text
OPENAI_API_KEY=sk-xxx
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o-mini
AMAP_MCP_URL=https://mcp.amap.com/mcp?key=YOUR_AMAP_KEY
PUBLIC_BASE_URL=http://127.0.0.1:8000
CHECKPOINT_DB_PATH=./data/checkpoints.db
AUTH_DB_PATH=./data/tokens.db
```

`app/__init__.py` 与 `tests/` 目录（空 `__init__.py` 不需要）；确认 `.gitignore` 已包含 `.env`、`data/`、`__pycache__/`、`.venv/`（uv init 通常已生成，缺则补）。

- [ ] **步骤 3：编写 app/config.py**

```python
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    amap_mcp_url: str = "https://mcp.amap.com/mcp"
    public_base_url: str = "http://127.0.0.1:8000"
    checkpoint_db_path: str = "./data/checkpoints.db"
    auth_db_path: str = "./data/tokens.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **步骤 4：编写失败的测试 tests/test_smoke.py**

```python
def test_config_settings_cached():
    from app.config import get_settings

    assert get_settings() is get_settings()
```

- [ ] **步骤 5：安装依赖并运行**

```powershell
git rm -r src
uv sync --all-groups
uv run pytest -q
```

预期：1 passed。

- [ ] **步骤 6：Commit**

```powershell
git add -A
git commit -m "chore: project skeleton with deps and settings"
```

---

### 任务 2：状态模型与增量合并

**文件：**
- 创建：`app/graph/__init__.py`、`app/graph/state.py`、`tests/test_state.py`

- [ ] **步骤 1：编写失败的测试 tests/test_state.py**

```python
from datetime import date

from app.graph.state import (
    TravelRequest,
    TravelRequestUpdate,
    merge_request,
    missing_fields,
)


def test_merge_keeps_existing_and_adds_new():
    base = TravelRequest(destination="杭州")
    merged = merge_request(base, TravelRequestUpdate(start_date=date(2026, 10, 1)))
    assert merged.destination == "杭州"
    assert merged.start_date == date(2026, 10, 1)


def test_merge_none_does_not_overwrite():
    base = TravelRequest(destination="杭州", budget=3000.0)
    merged = merge_request(base, TravelRequestUpdate())  # 全 None
    assert merged.destination == "杭州"
    assert merged.budget == 3000.0


def test_merge_preferences_dedup():
    base = TravelRequest(preferences=["自然"])
    merged = merge_request(base, TravelRequestUpdate(preferences=["自然", "美食"]))
    assert merged.preferences == ["自然", "美食"]


def test_missing_fields_order():
    assert missing_fields(TravelRequest()) == ["destination", "start_date", "end_date", "budget"]


def test_missing_fields_empty_when_complete():
    full = TravelRequest(
        destination="杭州", start_date=date(2026, 10, 1), end_date=date(2026, 10, 3), budget=3000.0
    )
    assert missing_fields(full) == []
```

- [ ] **步骤 2：运行验证失败**

运行：`uv run pytest tests/test_state.py -q`
预期：FAIL，`ModuleNotFoundError: No module named 'app.graph'`

- [ ] **步骤 3：编写 app/graph/state.py**

```python
from __future__ import annotations

from datetime import date
from typing import Any, TypedDict

from pydantic import BaseModel, Field

REQUIRED_FIELDS: tuple[str, ...] = ("destination", "start_date", "end_date", "budget")


class TravelRequest(BaseModel):
    destination: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    budget: float | None = None
    currency: str = "CNY"
    travelers: int | None = None
    preferences: list[str] = Field(default_factory=list)


class TravelRequestUpdate(BaseModel):
    """LLM 单轮抽取结果：None 表示本轮未提到，合并时不覆盖已有值。"""

    destination: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    budget: float | None = None
    travelers: int | None = None
    preferences: list[str] | None = None


class GraphState(TypedDict):
    request: TravelRequest
    missing_fields: list[str]
    messages: list[dict[str, str]]
    itinerary: "Itinerary | None"
    response_text: str
    mcp_errors: list[str]
    budget_adjust_count: int


def merge_request(current: TravelRequest, update: TravelRequestUpdate) -> TravelRequest:
    """增量合并：只覆盖本轮新提到的字段；preferences 去重追加。"""
    merged = current.model_copy()
    for key, value in update.model_dump().items():
        if value is None:
            continue
        if key == "preferences":
            seen = set(merged.preferences)
            merged.preferences = merged.preferences + [p for p in value if p not in seen]
        else:
            setattr(merged, key, value)
    return merged


def missing_fields(request: TravelRequest) -> list[str]:
    result: list[str] = []
    for field in REQUIRED_FIELDS:
        value = getattr(request, field)
        if value is None:
            result.append(field)
    return result
```

注：`Itinerary` 在任务 6 追加到本文件；此处 `"Itinerary | None"` 为前向引用字符串，先保证导入不报错（TypedDict 注解惰性求值，`from __future__ import annotations` 已生效）。

- [ ] **步骤 4：运行验证通过**

运行：`uv run pytest tests/test_state.py -q`
预期：5 passed

- [ ] **步骤 5：Commit**

```powershell
git add app/graph tests/test_state.py
git commit -m "feat: travel request state model with incremental merge"
```

---

### 任务 3：check_required 与 ask_missing（interrupt 机制）

**文件：**
- 创建：`app/graph/nodes.py`、`tests/test_nodes.py`
- 依赖：langgraph 的 `interrupt`/`Command`/`SqliteSaver`

- [ ] **步骤 1：编写失败的测试 tests/test_nodes.py**

```python
import sqlite3

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from app.graph.nodes import check_required, format_question, make_ask_missing
from app.graph.state import GraphState, TravelRequest


def test_check_required_writes_missing_fields():
    out = check_required({"request": TravelRequest(), "missing_fields": []})
    assert out["missing_fields"] == ["destination", "start_date", "end_date", "budget"]


def test_format_question_lists_all_fields():
    text = format_question(["destination", "budget"])
    assert "目的地" in text and "预算总额" in text


async def test_ask_missing_interrupts_then_resumes():
    g = StateGraph(GraphState)
    g.add_node("check_required", check_required)
    g.add_node("ask_missing", make_ask_missing())
    g.add_edge(START, "check_required")
    g.add_conditional_edges(
        "check_required",
        lambda s: "ask" if s["missing_fields"] else END,
        {"ask": "ask_missing", END: END},
    )
    g.add_edge("ask_missing", END)
    graph = g.compile(checkpointer=SqliteSaver(sqlite3.connect(":memory:")))
    config = {"configurable": {"thread_id": "t1"}}

    r1 = await graph.ainvoke(
        {"request": TravelRequest(), "messages": [], "missing_fields": []}, config
    )
    payload = r1["__interrupt__"][0].value
    assert payload["type"] == "missing_info"
    assert "目的地" in payload["question"]

    r2 = await graph.ainvoke(Command(resume="回复文本"), config)
    assert r2["messages"][-1] == {"role": "user", "content": "回复文本"}
```

- [ ] **步骤 2：运行验证失败**

运行：`uv run pytest tests/test_nodes.py -q`
预期：FAIL，`No module named 'app.graph.nodes'`

- [ ] **步骤 3：编写 app/graph/nodes.py（第一批节点）**

```python
from __future__ import annotations

from typing import Any

from langgraph.types import interrupt

from app.graph.state import GraphState

FIELD_LABELS: dict[str, str] = {
    "destination": "目的地",
    "start_date": "出发日期",
    "end_date": "返程日期",
    "budget": "预算总额（元，含币种）",
}


def check_required(state: GraphState) -> dict[str, Any]:
    from app.graph.state import missing_fields

    return {"missing_fields": missing_fields(state["request"])}


def format_question(missing: list[str]) -> str:
    names = [FIELD_LABELS.get(f, f) for f in missing]
    return "为了帮你制定行程，请补充以下信息：" + "、".join(names) + "。"


def make_ask_missing():
    """一次性问出所有缺失字段；resume 值作为新的用户消息回流给 extract_and_merge。"""

    async def ask_missing(state: GraphState) -> dict[str, Any]:
        question = format_question(state["missing_fields"])
        payload = {"type": "missing_info", "missing": state["missing_fields"], "question": question}
        reply = interrupt(payload)
        return {
            "messages": state["messages"] + [{"role": "user", "content": str(reply)}],
        }

    return ask_missing
```

- [ ] **步骤 4：运行验证通过**

运行：`uv run pytest tests/test_nodes.py -q`
预期：3 passed

- [ ] **步骤 5：Commit**

```powershell
git add app/graph/nodes.py tests/test_nodes.py
git commit -m "feat: check_required and ask_missing interrupt node"
```

---

### 任务 4：extract_and_merge + LLM 适配 + 多轮中断图

**文件：**
- 创建：`app/graph/prompts.py`、`app/llm.py`、`app/graph/builder.py`、`tests/fakes.py`、`tests/test_interrupt_loop.py`
- 修改：`app/graph/nodes.py`（追加 extract 节点）

- [ ] **步骤 1：编写失败的测试**

`tests/fakes.py`（共享假件，后续任务持续追加）：

```python
from __future__ import annotations

import sqlite3
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver

from app.graph.state import TravelRequest, TravelRequestUpdate


def sqlite_checkpointer() -> SqliteSaver:
    """每个测试用独立 :memory: SQLite，保证隔离且不写 data/ 目录。"""
    return SqliteSaver(sqlite3.connect(":memory:"))


class FakeExtractor:
    """按预设序列返回抽取结果；记录每次收到的用户文本。"""

    def __init__(self, updates: list[TravelRequestUpdate]):
        self.updates = list(updates)
        self.calls: list[str] = []

    async def __call__(self, user_text: str, today: str) -> TravelRequestUpdate:
        self.calls.append(user_text)
        return self.updates.pop(0)
```

`tests/test_interrupt_loop.py`：

```python
from datetime import date

from langgraph.types import Command

from app.graph.builder import build_graph
from app.graph.state import TravelRequest, TravelRequestUpdate
from tests.fakes import FakeExtractor, sqlite_checkpointer


async def test_multi_round_interrupt_merges_incrementally():
    ex = FakeExtractor(
        [
            TravelRequestUpdate(destination="杭州"),
            TravelRequestUpdate(start_date=date(2026, 10, 1), end_date=date(2026, 10, 3)),
            TravelRequestUpdate(budget=3000.0),
        ]
    )
    graph = build_graph(extractor=ex, checkpointer=sqlite_checkpointer())
    config = {"configurable": {"thread_id": "t-loop"}}

    r1 = await graph.ainvoke(
        {"request": TravelRequest(), "messages": [{"role": "user", "content": "我想去杭州玩"}]},
        config,
    )
    assert r1["__interrupt__"][0].value["missing"] == ["start_date", "end_date", "budget"]
    assert ex.calls == ["我想去杭州玩"]

    r2 = await graph.ainvoke(Command(resume="10月1日到3日出发"), config)
    assert r2["__interrupt__"][0].value["missing"] == ["budget"]
    assert r2["request"].destination == "杭州"  # 已有字段未丢
    assert r2["request"].start_date == date(2026, 10, 1)

    r3 = await graph.ainvoke(Command(resume="预算3000"), config)
    assert r3["request"].budget == 3000.0
    assert r3["request"].end_date == date(2026, 10, 3)
    assert "__interrupt__" not in r3  # 信息齐全，v1 图直达 END
```

- [ ] **步骤 2：运行验证失败**

运行：`uv run pytest tests/test_interrupt_loop.py -q`
预期：FAIL，`No module named 'app.graph.builder'`

- [ ] **步骤 3：实现**

`app/graph/prompts.py`：

```python
EXTRACTION_PROMPT = """你是旅行信息抽取助手。今天是 {today}。
从用户输入中抽取旅行计划字段：destination、start_date、end_date、budget、travelers、preferences。
规则：
1. 只填用户明确提到的内容，未提到的字段保持 null，禁止臆造。
2. 相对日期（如"下周六""十一"）按今天 {today} 换算为 ISO 日期（YYYY-MM-DD）。
3. budget 提取为数字（单位：元）。
4. preferences 输出偏好标签列表（如 ["自然", "美食"]）；未提到时输出 null。"""
```

`app/llm.py`（真 LLM 适配器；本任务不单测，端到端由任务 11 验证）：

```python
from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.config import get_settings
from app.graph.prompts import EXTRACTION_PROMPT, ITINERARY_PROMPT
from app.graph.state import Itinerary, TravelRequest, TravelRequestUpdate


def get_llm() -> BaseChatModel:
    s = get_settings()
    return ChatOpenAI(
        api_key=s.openai_api_key,
        base_url=s.openai_base_url,
        model=s.openai_model,
        temperature=0,
    )


def llm_extractor(llm: BaseChatModel):
    """协议：async (user_text, today) -> TravelRequestUpdate"""

    async def extractor(user_text: str, today: str) -> TravelRequestUpdate:
        messages = [
            SystemMessage(content=EXTRACTION_PROMPT.format(today=today)),
            HumanMessage(content=user_text),
        ]
        return await llm.with_structured_output(TravelRequestUpdate).ainvoke(messages)

    return extractor


def llm_summarizer(llm: BaseChatModel):
    """协议：async (context_text, request) -> Itinerary（任务 6 接入）"""

    async def summarizer(context_text: str, request: TravelRequest) -> Itinerary:
        days = (request.end_date - request.start_date).days + 1
        daily_budget = round(request.budget / days, 2)
        prompt = ITINERARY_PROMPT.format(
            destination=request.destination,
            start=request.start_date.isoformat(),
            end=request.end_date.isoformat(),
            days=days,
            daily_budget=daily_budget,
            preferences="、".join(request.preferences) or "无",
            mcp_data=context_text,
        )
        return await llm.with_structured_output(Itinerary).ainvoke([HumanMessage(content=prompt)])

    return summarizer
```

`app/graph/nodes.py` 追加：

```python
def _last_user_text(messages: list[dict[str, str]]) -> str | None:
    for msg in reversed(messages):
        if msg.get("role") == "user":
            return msg.get("content", "")
    return None


def make_extract_and_merge(extractor):
    """取最后一条用户消息做结构化抽取并增量合并；无用户消息时不动作。"""

    async def extract_and_merge(state: GraphState) -> dict[str, Any]:
        user_text = _last_user_text(state["messages"])
        if not user_text:
            return {}
        update = await extractor(user_text, date.today().isoformat())
        return {"request": merge_request(state["request"], update)}

    return extract_and_merge
```

（文件顶部补 `from datetime import date` 与 `from app.graph.state import merge_request`。）

`app/graph/builder.py`（v1：齐全分支暂接 END，任务 6/7 逐步接入 build_itinerary/present_draft）：

```python
from __future__ import annotations

import sqlite3
from pathlib import Path

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from app.graph.nodes import check_required, make_ask_missing, make_extract_and_merge
from app.graph.state import GraphState


def make_sqlite_checkpointer(db_path: str) -> SqliteSaver:
    """SQLite checkpointer 工厂；check_same_thread=False 允许 langgraph 在线程池中调用。"""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return SqliteSaver(sqlite3.connect(db_path, check_same_thread=False))


def route_after_check(state: GraphState) -> str:
    return "ask_missing" if state["missing_fields"] else "done"


def build_graph(extractor, checkpointer: BaseCheckpointSaver):
    g = StateGraph(GraphState)
    g.add_node("extract_and_merge", make_extract_and_merge(extractor))
    g.add_node("check_required", check_required)
    g.add_node("ask_missing", make_ask_missing())
    g.add_edge(START, "extract_and_merge")
    g.add_edge("extract_and_merge", "check_required")
    g.add_conditional_edges(
        "check_required", route_after_check, {"ask_missing": "ask_missing", "done": END}
    )
    g.add_edge("ask_missing", "extract_and_merge")
    return g.compile(checkpointer=checkpointer)
```

- [ ] **步骤 4：运行验证通过**

运行：`uv run pytest tests/test_interrupt_loop.py tests/test_nodes.py -q`
预期：4 passed

- [ ] **步骤 5：Commit**

```powershell
git add app tests
git commit -m "feat: extract_and_merge node, llm adapters and multi-round interrupt graph"
```

---

### 任务 5：高德 MCP 客户端

**文件：**
- 创建：`app/mcp_client.py`、`scripts/amap_probe.py`、`tests/test_mcp_client.py`
- 修改：`tests/fakes.py`（追加 FakeMCPSession）

- [ ] **步骤 1：编写失败的测试 tests/test_mcp_client.py**

```python
from contextlib import asynccontextmanager

from mcp.types import CallToolResult, TextContent

from app.mcp_client import AmapMCPClient, extract_text


def test_extract_text_joins_text_blocks():
    result = CallToolResult(
        content=[TextContent(type="text", text="杭州"), TextContent(type="text", text="晴")]
    )
    assert extract_text(result) == "杭州\n晴"


def test_extract_text_empty():
    assert extract_text(CallToolResult(content=[])) == ""


class FakeSession:
    def __init__(self, responses: dict[str, str]):
        self.responses = responses
        self.calls: list[tuple[str, dict]] = []

    async def initialize(self):
        pass

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return CallToolResult(content=[TextContent(type="text", text=self.responses[name])])


def make_client_with(session: FakeSession) -> AmapMCPClient:
    @asynccontextmanager
    async def fake_session():
        yield session

    client = AmapMCPClient(url="http://fake")
    client._session_factory = fake_session  # 测试注入点
    return client


async def test_get_weather_and_search_pois_pass_arguments():
    session = FakeSession({"maps_weather": "今天晴 26℃", "maps_text_search": "1. 西湖"})
    client = make_client_with(session)

    assert await client.get_weather("杭州") == "今天晴 26℃"
    assert await client.search_pois("景点", "杭州") == "1. 西湖"
    assert session.calls[0] == ("maps_weather", {"city": "杭州"})
    assert session.calls[1] == ("maps_text_search", {"keywords": "景点", "city": "杭州"})


async def test_error_result_raises_runtime_error():
    from mcp.types import CallToolResult, TextContent

    class ErrorSession(FakeSession):
        async def call_tool(self, name, arguments):
            return CallToolResult(content=[TextContent(type="text", text="invalid key")], isError=True)

    client = make_client_with(ErrorSession({}))
    try:
        await client.get_weather("杭州")
        raised = False
    except RuntimeError:
        raised = True
    assert raised
```

- [ ] **步骤 2：运行验证失败**

运行：`uv run pytest tests/test_mcp_client.py -q`
预期：FAIL，`No module named 'app.mcp_client'`

- [ ] **步骤 3：实现 app/mcp_client.py**

```python
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.types import CallToolResult


def extract_text(result: CallToolResult) -> str:
    """把 CallToolResult 的 content 拼成纯文本；isError 时抛 RuntimeError。"""
    if result.isError:
        detail = extract_text(CallToolResult(content=result.content))
        raise RuntimeError(f"MCP tool error: {detail}")
    parts = [b.text for b in result.content if hasattr(b, "text")]
    return "\n".join(parts)


class AmapMCPClient:
    """高德 MCP 客户端。每次调用独立建立连接（简单可靠，规避会话生命周期管理）。"""

    def __init__(self, url: str):
        self.url = url
        self._session_factory = self._default_session_factory

    @asynccontextmanager
    async def _default_session_factory(self) -> AsyncIterator[ClientSession]:
        async with streamablehttp_client(self.url) as (read, write, _get_session_id):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session

    async def call(self, tool_name: str, arguments: dict[str, Any]) -> str:
        async with self._session_factory() as session:
            result = await session.call_tool(tool_name, arguments)
            return extract_text(result)

    # ---- 高层语义方法（当前流程用到的三个工具；其余工具走通用 call()）----

    async def get_weather(self, city: str) -> str:
        return await self.call("maps_weather", {"city": city})

    async def geocode(self, address: str) -> str:
        return await self.call("maps_geo", {"address": address})

    async def search_pois(self, keywords: str, city: str) -> str:
        return await self.call("maps_text_search", {"keywords": keywords, "city": city})
```

`scripts/amap_probe.py`（一次性探明真实参数名，结果用于校正高层方法参数）：

```python
"""列出高德 MCP 服务的全部工具与参数 schema。
用法：uv run python scripts/amap_probe.py（需 .env 中 AMAP_MCP_URL 有效）
"""

import asyncio
import os

from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def main() -> None:
    load_dotenv()
    url = os.environ["AMAP_MCP_URL"]
    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            for t in tools.tools:
                print(f"\n=== {t.name} ===\n{t.description}\nparams: {t.inputSchema}")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **步骤 4：运行验证 + 真实冒烟**

```powershell
uv run pytest tests/test_mcp_client.py -q
```
预期：5 passed。

配置 `.env`（真实 AMAP Key）后运行 `uv run python scripts/amap_probe.py`，确认工具清单包含 `maps_weather`/`maps_geo`/`maps_text_search`；若参数名与实现不符（如 `city`→`keywords`），以探针输出为准修正 `AmapMCPClient` 高层方法参数并重跑测试。

- [ ] **步骤 5：Commit**

```powershell
git add app/mcp_client.py scripts/amap_probe.py tests/test_mcp_client.py tests/fakes.py
git commit -m "feat: amap mcp client over streamable http with probe script"
```

---

### 任务 6：build_itinerary（真实数据 + 失败兜底）

**文件：**
- 修改：`app/graph/state.py`（追加 Itinerary 模型）、`app/graph/nodes.py`、`app/graph/builder.py`、`tests/fakes.py`
- 创建：`tests/test_build_itinerary.py`

- [ ] **步骤 1：编写失败的测试**

`tests/fakes.py` 追加：

```python
from app.graph.state import Itinerary


class FakeSummarizer:
    def __init__(self, result: Itinerary):
        self.result = result
        self.contexts: list[str] = []
        self.requests: list[TravelRequest] = []

    async def __call__(self, context_text: str, request: TravelRequest) -> Itinerary:
        self.contexts.append(context_text)
        self.requests.append(request)
        return self.result


class FakeMCP:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls: list[str] = []

    async def get_weather(self, city: str) -> str:
        self.calls.append("get_weather")
        if self.fail:
            raise RuntimeError("mcp down")
        return "晴 26℃"

    async def geocode(self, address: str) -> str:
        self.calls.append("geocode")
        if self.fail:
            raise RuntimeError("mcp down")
        return "120.15,30.27"

    async def search_pois(self, keywords: str, city: str) -> str:
        self.calls.append("search_pois")
        if self.fail:
            raise RuntimeError("mcp down")
        return "1. 西湖 (120.15,30.25)\n2. 灵隐寺 (120.10,30.24)"


def make_itinerary(total: float = 4000.0) -> Itinerary:
    from app.graph.state import ItineraryDay, ItineraryItem

    return Itinerary(
        destination="杭州",
        days=[
            ItineraryDay(
                day=1,
                date="2026-10-01",
                weather="晴",
                items=[
                    ItineraryItem(time="09:00", type="attraction", name="西湖", est_cost_cny=0.0)
                ],
                daily_est_cost_cny=round(total / 3, 2),
            )
        ],
        total_est_cost_cny=total,
        data_verified=False,
    )
```

`tests/test_build_itinerary.py`：

```python
from datetime import date

from app.graph.nodes import make_build_itinerary
from app.graph.state import TravelRequest
from tests.fakes import FakeMCP, FakeSummarizer, make_itinerary


def make_request(budget: float = 3000.0) -> TravelRequest:
    return TravelRequest(
        destination="杭州", start_date=date(2026, 10, 1), end_date=date(2026, 10, 3), budget=budget
    )


async def test_build_itinerary_calls_mcp_and_marks_verified():
    mcp, summary = FakeMCP(), FakeSummarizer(make_itinerary())
    node = make_build_itinerary(summarizer=summary, mcp=mcp)
    out = await node({"request": make_request(), "mcp_errors": []})

    assert mcp.calls == ["get_weather", "geocode", "search_pois"]
    assert out["itinerary"].data_verified is True
    assert out["mcp_errors"] == []
    assert "西湖" in summary.contexts[0]  # MCP 真实数据进入汇总上下文


async def test_build_itinerary_degrades_when_mcp_fails():
    summary = FakeSummarizer(make_itinerary())
    node = make_build_itinerary(summarizer=summary, mcp=FakeMCP(fail=True))
    out = await node({"request": make_request(), "mcp_errors": []})

    assert out["itinerary"].data_verified is False  # 不裸抛异常
    assert out["mcp_errors"] and "weather" in out["mcp_errors"][0]
    assert any("未经核实" in w for w in out["itinerary"].warnings)
```

- [ ] **步骤 2：运行验证失败**

运行：`uv run pytest tests/test_build_itinerary.py -q`
预期：FAIL，`ImportError: cannot import name 'Itinerary'`

- [ ] **步骤 3：实现**

`app/graph/state.py` 追加（同时把 `GraphState.itinerary` 的前向引用替换为真实类型）：

```python
from typing import Literal


class ItineraryItem(BaseModel):
    time: str
    type: Literal["attraction", "meal", "hotel", "transit", "free"]
    name: str
    location: str | None = None
    est_cost_cny: float = 0.0
    notes: str | None = None


class ItineraryDay(BaseModel):
    day: int
    date: str
    weather: str | None = None
    items: list[ItineraryItem] = Field(default_factory=list)
    daily_est_cost_cny: float = 0.0


class Itinerary(BaseModel):
    destination: str
    days: list[ItineraryDay] = Field(default_factory=list)
    total_est_cost_cny: float = 0.0
    data_verified: bool = False
    warnings: list[str] = Field(default_factory=list)
```

`app/graph/nodes.py` 追加：

```python
MCP_TOOL_FAILURE = "部分地图数据获取失败，行程基于通用知识生成，请人工核实：{errors}"


def make_build_itinerary(summarizer, mcp):
    """调高德 MCP 取真实数据 → 交给 summarizer 生成结构化行程。

    任一 MCP 调用失败都不抛异常：记录 mcp_errors、data_verified=False 并附 warnings。
    """

    async def build_itinerary(state: GraphState) -> dict[str, Any]:
        request = state["request"]
        errors: list[str] = []

        async def safe(name: str, coro) -> str:
            try:
                return await coro
            except Exception as exc:  # noqa: BLE001 —— 兜底要求：任何工具失败不中断流程
                errors.append(f"{name} failed: {exc}")
                return ""

        weather = await safe("weather", mcp.get_weather(request.destination))
        geo = await safe("geo", mcp.geocode(request.destination))
        pois = await safe("poi", mcp.search_pois("景点", request.destination))

        context_text = f"【地理编码】\n{geo}\n\n【天气】\n{weather}\n\n【景点 POI】\n{pois}"
        itinerary = await summarizer(context_text, request)
        itinerary.data_verified = not errors
        if errors:
            itinerary.warnings.append(MCP_TOOL_FAILURE.format(errors="; ".join(errors)))
        return {"itinerary": itinerary, "mcp_errors": errors}

    return build_itinerary
```

`app/graph/builder.py` 修改：`build_graph` 签名改为 `build_graph(extractor, summarizer, mcp, checkpointer)`（checkpointer 仍为必传的 SQLite saver），新增节点与边（`route_after_check` 的 `"done"` 分支暂仍接 END，任务 7 改）：

```python
from app.graph.nodes import check_required, make_ask_missing, make_build_itinerary, make_extract_and_merge

# build_graph 内：
g.add_node("build_itinerary", make_build_itinerary(summarizer, mcp))
# route_after_check 的映射改为 {"ask_missing": "ask_missing", "done": "build_itinerary"}
g.add_edge("build_itinerary", END)
```

（测试文件同步更新：`test_interrupt_loop.py` 的 `build_graph(extractor=ex, checkpointer=sqlite_checkpointer())` 改为 `build_graph(extractor=ex, summarizer=FakeSummarizer(make_itinerary()), mcp=FakeMCP(), checkpointer=sqlite_checkpointer())`。）

- [ ] **步骤 4：运行验证通过**

运行：`uv run pytest -q`
预期：全部通过（此前任务测试 + 新增 2 个）

- [ ] **步骤 5：Commit**

```powershell
git add app tests
git commit -m "feat: build_itinerary node with amap data and graceful degradation"
```

---

### 任务 7：present_draft + 预算超支第二类中断

**文件：**
- 修改：`app/graph/nodes.py`、`app/graph/builder.py`
- 创建：`tests/test_full_graph.py`

- [ ] **步骤 1：编写失败的测试 tests/test_full_graph.py**

```python
from datetime import date

from langgraph.types import Command

from app.graph.builder import build_graph
from app.graph.state import TravelRequest, TravelRequestUpdate
from tests.fakes import (
    FakeExtractor,
    FakeMCP,
    FakeSummarizer,
    make_itinerary,
    sqlite_checkpointer,
)


def make_graph(summary: FakeSummarizer):
    ex = FakeExtractor([TravelRequestUpdate(destination="杭州")])
    return build_graph(
        extractor=ex, summarizer=summary, mcp=FakeMCP(), checkpointer=sqlite_checkpointer()
    )


FULL_INPUT = {
    "request": TravelRequest(
        destination="杭州", start_date=date(2026, 10, 1), end_date=date(2026, 10, 3), budget=3000.0
    ),
    "messages": [{"role": "user", "content": "杭州三日游"}],
}
CONFIG = {"configurable": {"thread_id": "t-full"}}


async def test_complete_flow_presents_draft():
    graph = make_graph(FakeSummarizer(make_itinerary(total=2500.0)))
    result = await graph.ainvoke(FULL_INPUT, CONFIG)
    assert result["response_text"].startswith("# 杭州")
    assert "西湖" in result["response_text"]
    assert result["itinerary"].total_est_cost_cny == 2500.0


async def test_budget_overrun_interrupts_and_resume_budget():
    graph = make_graph(FakeSummarizer(make_itinerary(total=4000.0)))  # 4000 > 3000*1.2
    r1 = await graph.ainvoke(FULL_INPUT, CONFIG)
    payload = r1["__interrupt__"][0].value
    assert payload["type"] == "budget_overrun"
    assert "超出预算" in payload["question"]

    r2 = await graph.ainvoke(Command(resume="budget=5000"), CONFIG)
    assert r2["request"].budget == 5000.0
    assert "__interrupt__" not in r2
    assert r2["response_text"].startswith("# 杭州")


async def test_budget_overrun_gives_up_after_two_adjusts():
    graph = make_graph(FakeSummarizer(make_itinerary(total=4000.0)))
    await graph.ainvoke(FULL_INPUT, CONFIG)
    r2 = await graph.ainvoke(Command(resume="days=+1"), CONFIG)  # 第一次调整，仍超支 → 再中断
    assert r2["__interrupt__"][0].value["type"] == "budget_overrun"
    r3 = await graph.ainvoke(Command(resume="keep"), CONFIG)  # 达调整上限 → 强制展示
    assert "__interrupt__" not in r3
    assert r3["response_text"].startswith("# 杭州")
```

- [ ] **步骤 2：运行验证失败**

运行：`uv run pytest tests/test_full_graph.py -q`
预期：FAIL，`KeyError: 'response_text'`（节点尚不存在）

- [ ] **步骤 3：实现**

`app/graph/nodes.py` 追加：

```python
BUDGET_OVERRUN_TOLERANCE = 1.2  # 超出预算 20% 才触发第二类中断
MAX_BUDGET_ADJUSTS = 2

TYPE_LABELS: dict[str, str] = {
    "attraction": "游览",
    "meal": "餐饮",
    "hotel": "住宿",
    "transit": "交通",
    "free": "自由活动",
}


def make_ask_budget_adjust():
    """第二类中断：预估超支时询问调整方式。

    resume 约定：keep（维持现状）| budget=<新预算数字> | days=+N（延长 N 天）。
    """

    async def ask_budget_adjust(state: GraphState) -> dict[str, Any]:
        it, req = state["itinerary"], state["request"]
        question = (
            f"预估总花费 {it.total_est_cost_cny:.0f} 元，已超出预算 {req.budget:.0f} 元。"
            "回复 keep 维持本方案；budget=新预算（如 budget=5000）；days=+1 延长行程。"
        )
        reply = interrupt(
            {
                "type": "budget_overrun",
                "question": question,
                "estimated_total": it.total_est_cost_cny,
                "budget": req.budget,
            }
        )
        updated = req.model_copy()
        text = str(reply).strip()
        if text.startswith("budget="):
            updated.budget = float(text.split("=", 1)[1])
        elif text.startswith("days="):
            updated.end_date = req.end_date + timedelta(days=int(text.split("=", 1)[1].lstrip("+")))
        return {"request": updated, "budget_adjust_count": state.get("budget_adjust_count", 0) + 1}

    return ask_budget_adjust


def format_itinerary(it) -> str:
    lines = [f"# {it.destination} 逐日行程", f"预估总花费：{it.total_est_cost_cny:.0f} 元"]
    lines += [f"> ⚠ {w}" for w in it.warnings]
    for d in it.days:
        header = f"## Day {d.day}（{d.date}）" + (f" 天气：{d.weather}" if d.weather else "")
        lines.append(header)
        for item in d.items:
            label = TYPE_LABELS.get(item.type, item.type)
            lines.append(f"- {item.time} {label}：{item.name}（约 {item.est_cost_cny:.0f} 元）")
        lines.append(f"- 当日小计：约 {d.daily_est_cost_cny:.0f} 元")
    return "\n".join(lines)


def present_draft(state: GraphState) -> dict[str, Any]:
    text = format_itinerary(state["itinerary"])
    return {
        "response_text": text,
        "messages": state["messages"] + [{"role": "assistant", "content": text}],
    }


def route_after_build(state: GraphState) -> str:
    it, req = state.get("itinerary"), state["request"]
    over_budget = bool(it and req.budget and it.total_est_cost_cny > req.budget * BUDGET_OVERRUN_TOLERANCE)
    if over_budget and state.get("budget_adjust_count", 0) < MAX_BUDGET_ADJUSTS:
        return "ask_budget_adjust"
    return "present_draft"
```

（顶部补 `from datetime import timedelta`。）

`app/graph/builder.py` 最终形态（整文件替换）：

```python
from __future__ import annotations

import sqlite3
from pathlib import Path

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from app.graph.nodes import (
    check_required,
    make_ask_budget_adjust,
    make_ask_missing,
    make_build_itinerary,
    make_extract_and_merge,
    present_draft,
    route_after_build,
)
from app.graph.state import GraphState


def make_sqlite_checkpointer(db_path: str) -> SqliteSaver:
    """SQLite checkpointer 工厂；check_same_thread=False 允许 langgraph 在线程池中调用。"""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return SqliteSaver(sqlite3.connect(db_path, check_same_thread=False))


def route_after_check(state: GraphState) -> str:
    return "ask_missing" if state["missing_fields"] else "build_itinerary"


def build_graph(
    extractor, summarizer, mcp, checkpointer: BaseCheckpointSaver
):
    g = StateGraph(GraphState)
    g.add_node("extract_and_merge", make_extract_and_merge(extractor))
    g.add_node("check_required", check_required)
    g.add_node("ask_missing", make_ask_missing())
    g.add_node("build_itinerary", make_build_itinerary(summarizer, mcp))
    g.add_node("ask_budget_adjust", make_ask_budget_adjust())
    g.add_node("present_draft", present_draft)
    g.add_edge(START, "extract_and_merge")
    g.add_edge("extract_and_merge", "check_required")
    g.add_conditional_edges(
        "check_required", route_after_check, {"ask_missing": "ask_missing", "build_itinerary": "build_itinerary"}
    )
    g.add_edge("ask_missing", "extract_and_merge")
    g.add_conditional_edges(
        "build_itinerary",
        route_after_build,
        {"ask_budget_adjust": "ask_budget_adjust", "present_draft": "present_draft"},
    )
    g.add_edge("ask_budget_adjust", "build_itinerary")
    g.add_edge("present_draft", END)
    return g.compile(checkpointer=checkpointer)
```

同时更新 `tests/test_interrupt_loop.py`：信息齐全后应产出 `response_text`（把 `assert "__interrupt__" not in r3` 后追加 `assert "response_text" in r3`，并传入 FakeSummarizer/FakeMCP）。

- [ ] **步骤 4：运行验证通过**

运行：`uv run pytest -q`
预期：全部通过

- [ ] **步骤 5：Commit**

```powershell
git add app tests
git commit -m "feat: present_draft and budget overrun second interrupt"
```

---

### 任务 8：A2A AgentExecutor（中断 ↔ 状态映射）

**文件：**
- 创建：`app/a2a_adapter.py`、`tests/test_a2a_adapter.py`
- 修改：`tests/fakes.py`（追加 FakeGraph）

- [ ] **步骤 1：编写失败的测试**

`tests/fakes.py` 追加：

```python
from types import SimpleNamespace

from a2a.types import Message, Part, Role, TextPart


class FakeGraph:
    """按顺序吐出 ainvoke 结果；paused 决定 aget_state().next 是否非空（模拟停在 interrupt 上）。"""

    def __init__(self, results: list[dict], paused: set[str] | None = None):
        self.results = list(results)
        self.paused = paused or set()
        self.invocations: list[tuple] = []

    async def aget_state(self, config):
        tid = config["configurable"]["thread_id"]
        return SimpleNamespace(next=("pending",) if tid in self.paused else ())

    async def ainvoke(self, graph_input, config):
        self.invocations.append((graph_input, config))
        return self.results.pop(0)


def make_context(text: str, task_id: str = "task-1", current_task=None):
    msg = Message(
        role=Role.user,
        parts=[Part(root=TextPart(text=text))],
        message_id=f"m-{task_id}",
        context_id=task_id,
        task_id=task_id,
    )
    return SimpleNamespace(
        task_id=task_id, context_id=task_id, current_task=current_task, message=msg
    )


def part_text(part: Part) -> str:
    return part.root.text


def interrupt_result(question: str) -> dict:
    """构造含 __interrupt__ 的 ainvoke 返回值（任务 9 的 API 测试复用）。"""
    return {
        "__interrupt__": [
            type("I", (), {"value": {"type": "missing_info", "question": question}})()
        ]
    }


def drain_events(event_queue) -> list:
    import asyncio

    events = []
    while True:
        try:
            events.append(event_queue.dequeue_event_nowait())
        except (asyncio.QueueEmpty, AttributeError):
            break
    return events
```

`tests/test_a2a_adapter.py`：

```python
from app.a2a_adapter import TravelAgentExecutor
from a2a.server.events import EventQueue
from a2a.types import TaskState
from tests.fakes import FakeGraph, drain_events, interrupt_result, make_context, part_text


async def test_new_task_with_interrupt_emits_input_required():
    graph = FakeGraph([interrupt_result("请补充目的地、日期与预算。")])
    executor = TravelAgentExecutor(graph)
    queue = EventQueue()

    await executor.execute(make_context("我想出去玩"), queue)
    events = drain_events(queue)
    last = events[-1]
    assert last.status.state == TaskState.input_required
    assert "请补充" in part_text(last.status.message.parts[0])
    # 初始输入应包含 user 消息与空 TravelRequest
    first_input = graph.invocations[0][0]
    assert first_input["messages"][0]["content"] == "我想出去玩"


async def test_resume_uses_command_with_thread_id_task_id():
    graph = FakeGraph(
        [interrupt_result("问"), {"response_text": "# 行程", "itinerary": {"destination": "杭州"}}],
        paused={"task-9"},
    )
    executor = TravelAgentExecutor(graph)
    queue = EventQueue()

    await executor.execute(make_context("杭州", task_id="task-9"), queue)
    resumed_input, config = graph.invocations[0]
    assert isinstance(resumed_input, Command) and resumed_input.resume == "杭州"
    assert config["configurable"]["thread_id"] == "task-9"

    events = drain_events(queue)
    last = events[-1]
    assert last.status.state == TaskState.completed


async def test_graph_exception_emits_failed():
    class BoomGraph(FakeGraph):
        async def ainvoke(self, graph_input, config):
            raise RuntimeError("boom")

    executor = TravelAgentExecutor(BoomGraph([]))
    queue = EventQueue()
    await executor.execute(make_context("hi"), queue)
    last = drain_events(queue)[-1]
    assert last.status.state == TaskState.failed
    assert "boom" in part_text(last.status.message.parts[0])
```

- [ ] **步骤 2：运行验证失败**

运行：`uv run pytest tests/test_a2a_adapter.py -q`
预期：FAIL，`No module named 'app.a2a_adapter'`

- [ ] **步骤 3：实现 app/a2a_adapter.py**

```python
from __future__ import annotations

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Part, TextPart
from a2a.utils.message import get_message_text
from langgraph.types import Command

from app.graph.state import TravelRequest

INITIAL_STATE: dict = {
    "request": TravelRequest(),
    "missing_fields": [],
    "messages": [],
    "itinerary": None,
    "response_text": "",
    "mcp_errors": [],
    "budget_adjust_count": 0,
}


def _text_part(text: str) -> Part:
    return Part(root=TextPart(text=text))


class TravelAgentExecutor(AgentExecutor):
    """把 LangGraph 执行映射为 A2A task 状态：

    - `__interrupt__` → `input-required`（question 进 message.parts）
    - 恢复 → `Command(resume=用户回复)`，`thread_id = A2A task_id`
    - 完成 → artifact（行程 markdown）+ `completed`
    """

    def __init__(self, graph):
        self.graph = graph

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        if context.current_task is None:
            await updater.submit()
        await updater.start_work()

        user_text = get_message_text(context.message)
        config = {"configurable": {"thread_id": context.task_id}}
        try:
            snapshot = await self.graph.aget_state(config)
            if snapshot.next:
                result = await self.graph.ainvoke(Command(resume=user_text), config)
            else:
                initial = {**INITIAL_STATE, "messages": [{"role": "user", "content": user_text}]}
                result = await self.graph.ainvoke(initial, config)
        except Exception as exc:  # noqa: BLE001 —— 兜底：任何内部错误映射为 task failed
            await updater.failed(
                message=updater.new_agent_message(parts=[_text_part(f"生成行程失败：{exc}")])
            )
            return

        if "__interrupt__" in result:
            payload = result["__interrupt__"][-1].value
            await updater.requires_input(
                message=updater.new_agent_message(parts=[_text_part(payload["question"])])
            )
            return

        await updater.add_artifact([_text_part(result.get("response_text", ""))], name="itinerary")
        await updater.complete(
            message=updater.new_agent_message(parts=[_text_part("行程方案已生成完毕。")])
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("cancel not supported")
```

- [ ] **步骤 4：运行验证通过**

运行：`uv run pytest tests/test_a2a_adapter.py -q`
预期：3 passed

- [ ] **步骤 5：Commit**

```powershell
git add app/a2a_adapter.py tests
git commit -m "feat: a2a executor mapping langgraph interrupt to input-required"
```

---

### 任务 9：FastAPI + AgentCard + JSON-RPC 端点

**文件：**
- 创建：`app/agent_card.py`、`app/main.py`、`tests/test_main.py`
- 修改：`app/graph/builder.py`（追加 default_graph 工厂）

- [ ] **步骤 1：编写失败的测试**

`tests/fakes.py` 追加（A2A 请求构造 helper，任务 10 的 `test_auth.py` 也要复用；`interrupt_result` 已在任务 8 存在）：

```python
import uuid


def send_message(text: str, task_id: str | None = None, context_id: str | None = None) -> dict:
    """构造 message/send JSON-RPC 请求体。"""
    msg = {
        "role": "user",
        "kind": "message",
        "messageId": uuid.uuid4().hex,
        "parts": [{"kind": "text", "text": text}],
    }
    if task_id:
        msg["taskId"] = task_id
    if context_id:
        msg["contextId"] = context_id
    return {"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": "message/send",
            "params": {"message": msg}}
```

`tests/test_main.py`：

```python
import httpx

from app.main import create_app
from tests.fakes import FakeGraph, interrupt_result, send_message


async def test_message_send_completes_with_artifact():
    graph = FakeGraph([{"response_text": "# 杭州 逐日行程", "itinerary": {"destination": "杭州"}}])
    app = create_app(graph=graph)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post("/a2a/travel-planner/", json=send_message("杭州三日游，预算3000"))
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["status"]["state"] == "completed"
    assert result["artifacts"][0]["parts"][0]["text"].startswith("# 杭州")


async def test_message_send_input_required_then_resume_same_task():
    graph = FakeGraph(
        [interrupt_result("请补充日期与预算。"), {"response_text": "# OK", "itinerary": {}}],
        paused={"task-x"},
    )
    app = create_app(graph=graph)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r1 = await c.post("/a2a/travel-planner/", json=send_message("我想去杭州"))
        task = r1.json()["result"]
        assert task["status"]["state"] == "input-required"
        assert "预算" in task["status"]["message"]["parts"][0]["text"]
        task_id, context_id = task["id"], task["contextId"]

        graph.paused = {task_id}  # 模拟仍停在 interrupt
        r2 = await c.post(
            "/a2a/travel-planner/",
            json=send_message("10月1日到3日，预算3000", task_id=task_id, context_id=context_id),
        )
    task2 = r2.json()["result"]
    assert task2["id"] == task_id
    assert task2["status"]["state"] == "completed"


async def test_agent_card_wellknown():
    app = create_app(graph=FakeGraph([]))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/a2a/travel-planner/.well-known/agent-card.json")
    assert resp.status_code == 200
    assert resp.json()["name"] == "travel-planner-agent"
```

- [ ] **步骤 2：运行验证失败**

运行：`uv run pytest tests/test_main.py -q`
预期：FAIL，`No module named 'app.main'`

- [ ] **步骤 3：实现**

`app/agent_card.py`：

```python
from a2a.types import AgentCapabilities, AgentCard, AgentSkill


def build_agent_card(base_url: str) -> AgentCard:
    endpoint = f"{base_url.rstrip('/')}/a2a/travel-planner"
    return AgentCard(
        name="travel-planner-agent",
        description="根据目的地/日期/预算生成真实可执行的逐日旅行方案",
        url=endpoint,
        version="0.1.0",
        capabilities=AgentCapabilities(streaming=False),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        preferred_transport="JSONRPC",
        skills=[
            AgentSkill(
                id="plan_trip",
                name="plan_trip",
                tags=["travel"],
                description="生成旅行行程，缺少必填信息时会中断询问",
            )
        ],
    )
```

`app/graph/builder.py` 追加（生产装配；测试不触发）：

```python
def default_graph():
    from app.config import get_settings
    from app.llm import get_llm, llm_extractor, llm_summarizer
    from app.mcp_client import AmapMCPClient

    llm = get_llm()
    mcp = AmapMCPClient(get_settings().amap_mcp_url)
    return build_graph(
        extractor=llm_extractor(llm),
        summarizer=llm_summarizer(llm),
        mcp=mcp,
        checkpointer=make_sqlite_checkpointer(get_settings().checkpoint_db_path),
    )
```

`app/main.py`（本任务不含鉴权，任务 10 接入）：

```python
from __future__ import annotations

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from fastapi import FastAPI

from app.a2a_adapter import TravelAgentExecutor
from app.agent_card import build_agent_card
from app.config import get_settings


def create_app(graph=None) -> FastAPI:
    if graph is None:
        from app.graph.builder import default_graph

        graph = default_graph()

    card = build_agent_card(get_settings().public_base_url)
    handler = DefaultRequestHandler(
        agent_executor=TravelAgentExecutor(graph), task_store=InMemoryTaskStore()
    )
    a2a_asgi = A2AStarletteApplication(agent_card=card, http_handler=handler).build()

    app = FastAPI(title="travel-planner-agent")
    app.mount("/a2a/travel-planner", a2a_asgi)
    return app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(create_app(), host="0.0.0.0", port=8000)
```

- [ ] **步骤 4：运行验证通过**

运行：`uv run pytest -q`
预期：全部通过。若 `well-known` 路径 404（SDK 0.2 为 `agent.json`），按安装版本的 `A2AStarletteApplication.routes` 调整断言路径。

- [ ] **步骤 5：Commit**

```powershell
git add app tests
git commit -m "feat: fastapi app exposing a2a jsonrpc endpoint with agent card"
```

---

### 任务 10：鉴权（TokenStore + 中间件 + CLI）

**文件：**
- 创建：`app/auth.py`、`scripts/issue_token.py`、`tests/test_auth.py`
- 修改：`app/main.py`（create_app 接入鉴权）

- [ ] **步骤 1：编写失败的测试 tests/test_auth.py**

```python
import uuid

import httpx

from app.auth import BearerAuthMiddleware, TokenStore
from app.main import create_app
from tests.fakes import FakeGraph, send_message


def make_store(tmp_path) -> TokenStore:
    return TokenStore(str(tmp_path / "tokens.db"))


def test_issue_and_verify(tmp_path):
    store = make_store(tmp_path)
    token = store.issue("test-caller")
    info = store.verify(token)
    assert info is not None and info.caller_name == "test-caller"
    assert store.verify("wrong-token") is None
    # 数据库中不存明文
    rows = store.conn.execute("SELECT token_hash FROM api_tokens").fetchall()
    assert token.encode() not in rows[0][0].encode()


def test_revoke_rejects_token(tmp_path):
    store = make_store(tmp_path)
    token = store.issue("test-caller")
    store.revoke("test-caller")
    assert store.verify(token) is None


def test_expired_token_rejected(tmp_path):
    store = make_store(tmp_path)
    token = store.issue("test-caller", expires_at="2000-01-01T00:00:00+00:00")
    assert store.verify(token) is None


async def test_api_401_without_token(tmp_path):
    app = create_app(graph=FakeGraph([]), auth_store=make_store(tmp_path))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post("/a2a/travel-planner/", json=send_message("hi"))
    assert resp.status_code == 401


async def test_api_200_with_valid_token(tmp_path):
    store = make_store(tmp_path)
    token = store.issue("tester")
    app = create_app(graph=FakeGraph([{"response_text": "# OK", "itinerary": {}}]), auth_store=store)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post(
            "/a2a/travel-planner/",
            json=send_message("hi"),
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200


async def test_wellknown_exempt_from_auth(tmp_path):
    app = create_app(graph=FakeGraph([]), auth_store=make_store(tmp_path))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/a2a/travel-planner/.well-known/agent-card.json")
    assert resp.status_code == 200
```

- [ ] **步骤 2：运行验证失败**

运行：`uv run pytest tests/test_auth.py -q`
预期：FAIL，`No module named 'app.auth'`

- [ ] **步骤 3：实现 app/auth.py**

```python
from __future__ import annotations

import hashlib
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def sha256_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TokenInfo:
    caller_name: str
    scopes: list[str]


class TokenStore:
    """api_tokens 表（对齐 PLAN.md 8）：id, token_hash, caller_name, scopes, status, created_at, expires_at, last_used_at"""

    def __init__(self, db_path: str):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS api_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_hash TEXT UNIQUE NOT NULL,
                caller_name TEXT NOT NULL,
                scopes TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                expires_at TEXT,
                last_used_at TEXT
            )
            """
        )
        self.conn.commit()

    def issue(self, caller_name: str, scopes: list[str] | None = None,
              expires_at: str | None = None) -> str:
        token = secrets.token_urlsafe(32)
        self.conn.execute(
            "INSERT INTO api_tokens (token_hash, caller_name, scopes, status, created_at, expires_at)"
            " VALUES (?, ?, ?, 'active', ?, ?)",
            (sha256_hash(token), caller_name, ",".join(scopes or []), _now_iso(), expires_at),
        )
        self.conn.commit()
        return token  # 明文只在发放时返回一次

    def verify(self, token: str) -> TokenInfo | None:
        row = self.conn.execute(
            "SELECT caller_name, scopes, expires_at FROM api_tokens"
            " WHERE token_hash = ? AND status = 'active'",
            (sha256_hash(token),),
        ).fetchone()
        if row is None:
            return None
        caller, scopes, expires_at = row
        if expires_at and expires_at <= _now_iso():
            return None
        self.conn.execute(
            "UPDATE api_tokens SET last_used_at = ? WHERE token_hash = ?",
            (_now_iso(), sha256_hash(token)),
        )
        self.conn.commit()
        return TokenInfo(caller_name=caller, scopes=[s for s in scopes.split(",") if s])

    def revoke(self, caller_name: str) -> int:
        cur = self.conn.execute(
            "UPDATE api_tokens SET status = 'revoked' WHERE caller_name = ?", (caller_name,)
        )
        self.conn.commit()
        return cur.rowcount


async def _send_401(send):
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"www-authenticate", b"Bearer"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": b'{"detail":"invalid or missing token"}'})


class BearerAuthMiddleware:
    """纯 ASGI 中间件：保护挂载进来的 A2A Starlette 应用；well-known 发现路径豁免。"""

    def __init__(self, app, store: TokenStore, exempt_prefixes: tuple[str, ...] = ("/.well-known",)):
        self.app = app
        self.store = store
        self.exempt_prefixes = exempt_prefixes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"].startswith(self.exempt_prefixes):
            return await self.app(scope, receive, send)
        headers = {k: v for k, v in scope.get("headers", [])}
        auth = headers.get(b"authorization", b"").decode("latin-1")
        if not auth.startswith("Bearer "):
            return await _send_401(send)
        info = self.store.verify(auth.removeprefix("Bearer "))
        if info is None:
            return await _send_401(send)
        await self.app(scope, receive, send)
```

`app/main.py` 修改（`create_app` 增加鉴权）：

```python
# import 区追加：
from app.auth import BearerAuthMiddleware, TokenStore

# create_app 签名改为 def create_app(graph=None, auth_store: TokenStore | None = None) -> FastAPI:
    if auth_store is None:
        auth_store = TokenStore(get_settings().auth_db_path)
    ...
    a2a_asgi = A2AStarletteApplication(agent_card=card, http_handler=handler).build()
    a2a_asgi = BearerAuthMiddleware(a2a_asgi, auth_store)
    app.mount("/a2a/travel-planner", a2a_asgi)
```

**同步更新任务 9 的 API 测试**：`create_app` 未显式传 `auth_store` 时会按 Settings 创建真实 TokenStore，未带 token 的请求将得到 401。因此给 `test_main.py` 注入临时 store 并携带 token（`test_agent_card_wellknown` 走豁免路径，无需改动）：

```python
import pytest

from app.auth import TokenStore


@pytest.fixture
def auth(tmp_path):
    store = TokenStore(str(tmp_path / "t.db"))
    return store, store.issue("it")


# test_message_send_completes_with_artifact 与
# test_message_send_input_required_then_resume_same_task 两个测试
# 签名加 auth 参数，create_app 传 auth_store=store，每次 c.post 加 headers。
# 以第一个为例（第二个做同样处理）：

async def test_message_send_completes_with_artifact(auth):
    store, token = auth
    graph = FakeGraph([{"response_text": "# 杭州 逐日行程", "itinerary": {"destination": "杭州"}}])
    app = create_app(graph=graph, auth_store=store)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post(
            "/a2a/travel-planner/",
            json=send_message("杭州三日游，预算3000"),
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["status"]["state"] == "completed"
    assert result["artifacts"][0]["parts"][0]["text"].startswith("# 杭州")
```

`scripts/issue_token.py`：

```python
"""发放 API token（对齐 PLAN.md 8：CLI 发放，按 caller 审计）。
用法：uv run python scripts/issue_token.py --caller hermes-gateway [--expires 2026-12-31T00:00:00+00:00]
"""

import argparse

from app.auth import TokenStore
from app.config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description="issue api token")
    parser.add_argument("--caller", required=True, help="调用方名称（如 hermes-gateway）")
    parser.add_argument("--scopes", default="", help="逗号分隔，如 plan_trip")
    parser.add_argument("--expires", default=None, help="ISO 8601 过期时间")
    args = parser.parse_args()

    store = TokenStore(get_settings().auth_db_path)
    token = store.issue(
        args.caller, scopes=[s for s in args.scopes.split(",") if s], expires_at=args.expires
    )
    print(f"caller={args.caller}\ntoken（仅显示一次，请妥善保存）:\n{token}")


if __name__ == "__main__":
    main()
```

- [ ] **步骤 4：运行验证 + CLI 冒烟**

```powershell
uv run pytest -q
uv run python scripts/issue_token.py --caller smoke-test
```
预期：pytest 全绿；脚本打印一次性 token。

- [ ] **步骤 5：Commit**

```powershell
git add app scripts tests
git commit -m "feat: bearer token auth with sqlite store and asgi middleware"
```

---

### 任务 11：demo 客户端 + README + 验收核对

**文件：**
- 创建：`scripts/a2a_client_demo.py`
- 修改：`README.md`（当前为空文件）

- [ ] **步骤 1：编写 scripts/a2a_client_demo.py（裸 JSON-RPC，直观演示协议往返）**

```python
"""A2A 手工测试客户端：模拟"部分信息 → input-required → 补充 → resume"完整往返。
用法：
  uv run uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000   # 终端 1
  uv run python scripts/a2a_client_demo.py --base http://127.0.0.1:8000 --token <TOKEN>  # 终端 2
"""

import argparse
import uuid

import httpx

TIMEOUT = 300.0


def send(base: str, token: str, text: str, task_id: str | None = None,
         context_id: str | None = None) -> dict:
    msg = {
        "role": "user",
        "kind": "message",
        "messageId": uuid.uuid4().hex,
        "parts": [{"kind": "text", "text": text}],
    }
    if task_id:
        msg["taskId"] = task_id
    if context_id:
        msg["contextId"] = context_id
    payload = {
        "jsonrpc": "2.0",
        "id": uuid.uuid4().hex,
        "method": "message/send",
        "params": {"message": msg},
    }
    resp = httpx.post(
        f"{base}/a2a/travel-planner",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
        timeout=TIMEOUT,
        follow_redirects=True,  # Mount 对无尾斜杠路径可能 307
    )
    resp.raise_for_status()
    return resp.json()["result"]


def main() -> None:
    parser = argparse.ArgumentParser(description="a2a demo client")
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--token", required=True)
    args = parser.parse_args()

    task = send(args.base, args.token, "我想出去玩，帮规划一下")  # 只给模糊需求
    while task["status"]["state"] == "input-required":
        question = task["status"]["message"]["parts"][0]["text"]
        print(f"\nAGENT: {question}")
        reply = input("YOU> ").strip()
        task = send(args.base, args.token, reply, task_id=task["id"], context_id=task["contextId"])

    print(f"\n最终状态: {task['status']['state']}")
    for artifact in task.get("artifacts", []):
        for part in artifact["parts"]:
            print(part["text"])


if __name__ == "__main__":
    main()
```

- [ ] **步骤 2：重写 README.md**

```markdown
# 旅游规划 Agent

对话式旅游规划 Agent：信息不全时中断追问，调高德地图 MCP 取真实数据，产出逐日行程。
完整规格见 `PLAN.md`，执行计划见 `docs/superpowers/plans/2026-09-16-travel-agent-implementation.md`。

## 快速开始

```powershell
uv sync --all-groups
Copy-Item .env.example .env   # 填入 OPENAI_API_KEY / AMAP_MCP_URL
uv run pytest -q              # 跑全部测试

uv run python scripts/issue_token.py --caller local-test   # 发放 token（记下输出）
uv run uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000

# 另开终端：
uv run python scripts/a2a_client_demo.py --base http://127.0.0.1:8000 --token <TOKEN>
```

## 架构

- `app/graph/`：LangGraph 图（extract_and_merge → check_required → ask_missing/build_itinerary → present_draft），`interrupt()` 承载"追问"与"预算超支"两类中断；状态经 SQLite checkpointer 持久化到 `data/checkpoints.db`（`thread_id = A2A task_id`）。
- `app/a2a_adapter.py`：LangGraph `__interrupt__` → A2A `input-required`；`thread_id = A2A task_id`；恢复用 `Command(resume=...)`。
- `app/mcp_client.py`：高德 MCP（Streamable HTTP）：天气/地理编码/POI。
- `app/auth.py` + `scripts/issue_token.py`：SHA256 哈希存储的 Bearer Token 鉴权。
```

- [ ] **步骤 3：全量验证与验收核对**

```powershell
uv run pytest -q
uv run ruff check app tests scripts
```

按 PLAN.md 第 10 节逐条核对（前 6 条有自动化测试；第 7 条配合真实 Key 用 demo 客户端人工确认）：

| 验收标准（PLAN.md 10） | 证据 |
|---|---|
| 1 只给目的地 → 中断追问 | `test_multi_round_interrupt_merges_incrementally` |
| 2 多轮补全不丢字段 | `test_state.py` + 同上集成测试 |
| 3 MCP 失败有兜底不裸抛 | `test_build_itinerary_degrades_when_mcp_fails` |
| 4 识别 input-required 并 resume 同 task | `test_message_send_input_required_then_resume_same_task` |
| 5 同 task 连续 2+ 次中断-恢复不串线 | 任务 4（2 轮）+ `test_budget_overrun_gives_up_after_two_adjusts`（3 轮）+ demo 实测 |
| 6 无效/撤销 token 拒绝 401 | `test_api_401_without_token` / `test_revoke_rejects_token` |
| 7 行程含真实景点/天气/每日花费 | demo 客户端 + 真实 Key 人工验收；`data_verified` 标记佐证 |

- [ ] **步骤 4：Commit**

```powershell
git add scripts/a2a_client_demo.py README.md
git commit -m "docs: add a2a demo client and readme with acceptance mapping"
```

---

## 执行注意

1. **执行顺序即任务编号**；任务 3-7 是图逻辑演进（builder.py 分三次扩展到最终形态），不要跳步合并。
2. **每个 fake 只进不改语义**：`tests/fakes.py` 是共享假件库，追加时保持既有类签名不变。
3. **a2a-sdk / mcp 版本差异**：遇到 import 或字段报错时，以安装版本源码为准做等价调整（如 `a2a.utils.message.get_message_text` 在部分版本位于 `a2a.utils`），并在 commit message 中注明适配点。
4. **真实 Key 冒烟**：任务 5 步骤 4 与任务 11 步骤 3 需要有效的高德 Key 与 LLM Key；其余任务全部离线可跑。
