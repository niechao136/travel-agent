from __future__ import annotations

from app.graph.builder import make_async_sqlite_checkpointer
from app.graph.state import TravelRequestUpdate


def sqlite_checkpointer():
    """每个测试用独立 :memory: SQLite（AsyncSqliteSaver），保证隔离且不写 data/ 目录。"""
    return make_async_sqlite_checkpointer(":memory:")


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
