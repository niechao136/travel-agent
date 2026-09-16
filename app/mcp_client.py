from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult


def extract_text(result: CallToolResult) -> str:
    """把 CallToolResult 的 content 拼成纯文本；is_error 时抛 RuntimeError（mcp 2.x 字段名）。"""
    if result.is_error:
        detail = extract_text(CallToolResult(content=result.content))
        raise RuntimeError(f"MCP tool error: {detail}")
    parts = [b.text for b in result.content if hasattr(b, "text")]
    return "\n".join(parts)


class AmapMCPClient:
    """高德 MCP 客户端（mcp 2.x）。每次调用独立建立连接（简单可靠，规避会话生命周期管理）。"""

    def __init__(self, url: str):
        self.url = url  # 形如 https://mcp.amap.com/mcp?key=xxx
        self._session_factory = self._default_session_factory

    @asynccontextmanager
    async def _default_session_factory(self) -> AsyncIterator[ClientSession]:
        async with streamable_http_client(self.url) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
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
