from __future__ import annotations

from pathlib import Path

import aiosqlite
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph

from app.graph.nodes import (
    check_required,
    make_ask_missing,
    make_build_itinerary,
    make_extract_and_merge,
)
from app.graph.state import GraphState

ALLOWED_MSGPACK_MODULES: list[tuple[str, str]] = [("app.graph.state", "TravelRequest")]


def make_async_sqlite_checkpointer(db_path: str) -> AsyncSqliteSaver:
    """AsyncSqliteSaver 工厂：同步上下文即可调用（连接在首次异步操作时建立）。

    - 同步 SqliteSaver 不支持 async 图执行（实测 NotImplementedError），必须用 Async 版；
    - 显式 serde 注册 TravelRequest，消除 checkpoint 反序列化警告（未来版本会阻断）。
    """
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    serde = JsonPlusSerializer(allowed_msgpack_modules=ALLOWED_MSGPACK_MODULES)
    return AsyncSqliteSaver(aiosqlite.connect(db_path), serde=serde)


def route_after_check(state: GraphState) -> str:
    return "ask_missing" if state["missing_fields"] else "done"


def build_graph(extractor, summarizer, mcp, checkpointer: BaseCheckpointSaver):
    g = StateGraph(GraphState)
    g.add_node("extract_and_merge", make_extract_and_merge(extractor))
    g.add_node("check_required", check_required)
    g.add_node("ask_missing", make_ask_missing())
    g.add_node("build_itinerary", make_build_itinerary(summarizer, mcp))
    g.add_edge(START, "extract_and_merge")
    g.add_edge("extract_and_merge", "check_required")
    g.add_conditional_edges(
        "check_required",
        route_after_check,
        {"ask_missing": "ask_missing", "done": "build_itinerary"},
    )
    g.add_edge("ask_missing", "extract_and_merge")
    g.add_edge("build_itinerary", END)
    return g.compile(checkpointer=checkpointer)
