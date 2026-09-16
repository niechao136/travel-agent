from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from mcp.types import CallToolResult, TextContent

from app.mcp_client import AmapMCPClient, MCPSession, extract_text


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
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def initialize(self) -> None:
        pass

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> CallToolResult:
        self.calls.append((name, arguments))
        return CallToolResult(content=[TextContent(type="text", text=self.responses[name])])


def make_client_with(session: FakeSession) -> AmapMCPClient:
    @asynccontextmanager
    async def fake_session() -> AsyncIterator[MCPSession]:
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
        async def call_tool(self, name: str, arguments: dict[str, Any]) -> CallToolResult:
            return CallToolResult(content=[TextContent(type="text", text="invalid key")], is_error=True)

    client = make_client_with(ErrorSession({}))
    try:
        await client.get_weather("杭州")
        raised = False
    except RuntimeError:
        raised = True
    assert raised
