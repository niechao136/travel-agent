from collections.abc import AsyncIterator

import pytest_asyncio
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.graph.builder import make_async_sqlite_checkpointer


@pytest_asyncio.fixture
async def checkpointer() -> AsyncIterator[AsyncSqliteSaver]:
    """每个测试一个独立 :memory: SQLite；测试结束显式关闭连接。

    AsyncSqliteSaver 的底层 aiosqlite 连接若不关闭，其工作线程会挂住进程/命令捕获。
    """
    saver = make_async_sqlite_checkpointer(":memory:")
    yield saver
    await saver.conn.close()
