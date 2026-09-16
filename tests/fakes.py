from __future__ import annotations

from typing import Any

from app.graph.builder import make_async_sqlite_checkpointer
from app.graph.state import TravelRequest, TravelRequestUpdate


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
