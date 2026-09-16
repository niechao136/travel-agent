from __future__ import annotations

from typing import Any, cast

from a2a.server.agent_execution import RequestContext
from a2a.server.events import Event, EventQueue
from a2a.types import Message, Part, Role
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from app.graph.state import GraphState, TravelRequestUpdate


def partial_state(**fields: Any) -> GraphState:
    """构造"部分 state"输入：LangGraph 首轮允许只给部分字段（其余保持未设置）。

    ainvoke 的静态类型是完整 GraphState，这里用一处 cast 显式表达"这是部分输入"，
    既不改变实际传给图的字段，也不用在每个用例里重复 cast。
    """
    partial: Any = fields
    return cast(GraphState, partial)


def thread_config(thread_id: str) -> RunnableConfig:
    """RunnableConfig：thread_id 即会话标识（A2A 场景下就是 task_id）。"""
    return {"configurable": {"thread_id": thread_id}}


class FakeExtractor:
    """按预设序列返回抽取结果；记录每次收到的用户文本。"""

    def __init__(self, updates: list[TravelRequestUpdate]):
        self.updates = list(updates)
        self.calls: list[str] = []

    async def __call__(self, user_text: str, today: str) -> TravelRequestUpdate:
        self.calls.append(user_text)
        return self.updates.pop(0)


from app.graph.state import Itinerary, TravelRequest


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
    """记录每次调用的 (方法名, 关键字) 元组：三次 search_pois 需按关键字区分。"""

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls: list[tuple[str, str]] = []

    async def get_weather(self, city: str) -> str:
        self.calls.append(("get_weather", city))
        if self.fail:
            raise RuntimeError("mcp down")
        return "晴 26℃"

    async def geocode(self, address: str) -> str:
        self.calls.append(("geocode", address))
        if self.fail:
            raise RuntimeError("mcp down")
        return "120.15,30.27"

    async def search_pois(self, keywords: str, city: str) -> str:
        self.calls.append(("search_pois", keywords))
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


from types import SimpleNamespace


class FakeGraph:
    """按顺序吐出 ainvoke 结果；paused 决定 aget_state().next 是否非空（模拟停在 interrupt 上）。"""

    def __init__(self, results: list[dict[str, Any]], paused: set[str] | None = None):
        self.results = list(results)
        # 保持传入对象身份：调用方可在用例中途 graph.paused.add(task_id) 模拟"仍停在中断上"
        self.paused = paused if paused is not None else set()
        self.invocations: list[tuple[Any, ...]] = []

    async def aget_state(self, config: RunnableConfig) -> SimpleNamespace:
        configurable = config.get("configurable") or {}
        tid = str(configurable.get("thread_id", ""))
        return SimpleNamespace(next=("pending",) if tid in self.paused else ())

    async def ainvoke(
        self, graph_input: GraphState | Command[Any] | None, config: RunnableConfig | None = None
    ) -> dict[str, Any]:
        self.invocations.append((graph_input, config))
        return self.results.pop(0)


class FakeEventQueue(EventQueue):
    """只实现 executor 用到的 enqueue_event 接口（继承 EventQueue 以便直接传入 execute）。"""

    def __init__(self):
        self.events: list[Any] = []  # 事件类型各异（Task / 状态事件 / artifact 事件）

    async def enqueue_event(self, event: Event) -> None:
        self.events.append(event)


def make_context(text: str, task_id: str = "task-1", current_task: Any = None) -> RequestContext:
    """executor 只用 task_id / context_id / current_task / message 四个字段，其余不必构造。"""
    msg = Message(
        role=Role.ROLE_USER,
        parts=[Part(text=text)],
        message_id=f"m-{task_id}",
        context_id=task_id,
        task_id=task_id,
    )
    fake: Any = SimpleNamespace(
        task_id=task_id, context_id=task_id, current_task=current_task, message=msg
    )
    return cast(RequestContext, fake)


def part_text(part: Part) -> str:
    return str(part.text)


def interrupt_result(question: str) -> dict[str, Any]:
    """构造含 __interrupt__ 的 ainvoke 返回值（任务 9 的 API 测试复用）。"""
    return {
        "__interrupt__": [
            type("I", (), {"value": {"type": "missing_info", "question": question}})()
        ]
    }


import uuid


def send_message(
    text: str, task_id: str | None = None, context_id: str | None = None
) -> dict[str, Any]:
    """构造 1.0 协议 SendMessage JSON-RPC 请求体（需配合 A2A-Version: 1.0 header）。"""
    msg = {
        "messageId": uuid.uuid4().hex,
        "role": "ROLE_USER",
        "parts": [{"text": text}],
    }
    if task_id:
        msg["taskId"] = task_id
    if context_id:
        msg["contextId"] = context_id
    return {"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": "SendMessage",
            "params": {"message": msg}}
