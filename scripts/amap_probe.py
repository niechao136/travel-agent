"""列出高德 MCP 服务的全部工具与参数 schema。
用法：uv run python scripts/amap_probe.py（需 .env 中 AMAP_MCP_URL 有效）
"""

import asyncio
import os

from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def main() -> None:
    load_dotenv()
    url = os.environ["AMAP_MCP_URL"]
    async with (
        streamable_http_client(url) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        tools = await session.list_tools()
        for t in tools.tools:
            print(f"\n=== {t.name} ===\n{t.description}\nparams: {t.input_schema}")


if __name__ == "__main__":
    asyncio.run(main())
