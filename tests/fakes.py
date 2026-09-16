from __future__ import annotations

from app.graph.state import TravelRequestUpdate


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


from types import SimpleNamespace

from a2a.types import Message, Part, Role


class FakeGraph:
    """按顺序吐出 ainvoke 结果；paused 决定 aget_state().next 是否非空（模拟停在 interrupt 上）。"""

    def __init__(self, results: list[dict], paused: set[str] | None = None):
        self.results = list(results)
        # 保持传入对象身份：调用方可在用例中途 graph.paused.add(task_id) 模拟"仍停在中断上"
        self.paused = paused if paused is not None else set()
        self.invocations: list[tuple] = []

    async def aget_state(self, config):
        tid = config["configurable"]["thread_id"]
        return SimpleNamespace(next=("pending",) if tid in self.paused else ())

    async def ainvoke(self, graph_input, config):
        self.invocations.append((graph_input, config))
        return self.results.pop(0)


class FakeEventQueue:
    """只实现 executor 用到的 enqueue_event 接口。"""

    def __init__(self):
        self.events: list = []

    async def enqueue_event(self, event) -> None:
        self.events.append(event)


def make_context(text: str, task_id: str = "task-1", current_task=None):
    msg = Message(
        role=Role.ROLE_USER,
        parts=[Part(text=text)],
        message_id=f"m-{task_id}",
        context_id=task_id,
        task_id=task_id,
    )
    return SimpleNamespace(
        task_id=task_id, context_id=task_id, current_task=current_task, message=msg
    )


def part_text(part) -> str:
    return part.text


def interrupt_result(question: str) -> dict:
    """构造含 __interrupt__ 的 ainvoke 返回值（任务 9 的 API 测试复用）。"""
    return {
        "__interrupt__": [
            type("I", (), {"value": {"type": "missing_info", "question": question}})()
        ]
    }


import uuid


def send_message(text: str, task_id: str | None = None, context_id: str | None = None) -> dict:
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
