"""跨层依赖的结构化协议（Protocol）。

图节点只依赖"被调用方具备什么能力"，不绑定具体实现（LLM / 高德 MCP / Fake），
这样类型检查器能精确推断，测试注入替身也不必迁就具体类。
"""

from __future__ import annotations

from typing import Protocol

from app.graph.state import Itinerary, TravelRequest, TravelRequestUpdate


class Extractor(Protocol):
    """单轮抽取：async (user_text, today) -> TravelRequestUpdate"""

    async def __call__(self, user_text: str, today: str) -> TravelRequestUpdate: ...


class Summarizer(Protocol):
    """行程生成：async (context_text, request) -> Itinerary"""

    async def __call__(self, context_text: str, request: TravelRequest) -> Itinerary: ...


class AmapTools(Protocol):
    """高德 MCP 当前流程用到的三个工具方法。"""

    async def get_weather(self, city: str) -> str: ...

    async def geocode(self, address: str) -> str: ...

    async def search_pois(self, keywords: str, city: str) -> str: ...
