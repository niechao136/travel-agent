from __future__ import annotations

from pathlib import Path
from typing import Any

import aiosqlite
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph

from app.graph.nodes import (
    check_required,
    make_ask_budget_adjust,
    make_ask_missing,
    make_build_itinerary,
    make_extract_and_merge,
    present_draft,
    route_after_build,
)
from app.graph.state import GraphState
from app.protocols import AmapTools, Extractor, Summarizer

ALLOWED_MSGPACK_MODULES: list[tuple[str, str]] = [
    ("app.graph.state", "TravelRequest"),
    ("app.graph.state", "Itinerary"),
]


def make_async_sqlite_checkpointer(db_path: str) -> AsyncSqliteSaver:
    """AsyncSqliteSaver 工厂：同步上下文即可调用（连接在首次异步操作时建立）。

    - 同步 SqliteSaver 不支持 async 图执行（实测 NotImplementedError），必须用 Async 版；
    - 显式 serde 注册 TravelRequest，消除 checkpoint 反序列化警告（未来版本会阻断）。
    """
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    serde = JsonPlusSerializer(allowed_msgpack_modules=ALLOWED_MSGPACK_MODULES)
    return AsyncSqliteSaver(aiosqlite.connect(db_path), serde=serde)


def route_after_check(state: GraphState) -> str:
    return "ask_missing" if state["missing_fields"] else "build_itinerary"


def build_graph(
    extractor: Extractor,
    summarizer: Summarizer,
    mcp: AmapTools,
    checkpointer: BaseCheckpointSaver[Any],
):
    g = StateGraph(GraphState)
    g.add_node("extract_and_merge", make_extract_and_merge(extractor))
    g.add_node("check_required", check_required)
    g.add_node("ask_missing", make_ask_missing())
    g.add_node("build_itinerary", make_build_itinerary(summarizer, mcp))
    g.add_node("ask_budget_adjust", make_ask_budget_adjust())
    g.add_node("present_draft", present_draft)
    g.add_edge(START, "extract_and_merge")
    g.add_edge("extract_and_merge", "check_required")
    g.add_conditional_edges(
        "check_required", route_after_check, {"ask_missing": "ask_missing", "build_itinerary": "build_itinerary"}
    )
    g.add_edge("ask_missing", "extract_and_merge")
    g.add_conditional_edges(
        "build_itinerary",
        route_after_build,
        {"ask_budget_adjust": "ask_budget_adjust", "present_draft": "present_draft"},
    )
    g.add_edge("ask_budget_adjust", "build_itinerary")
    g.add_edge("present_draft", END)
    return g.compile(checkpointer=checkpointer)


def default_graph():
    from app.config import get_settings
    from app.llm import get_llm, llm_extractor, llm_summarizer
    from app.mcp_client import AmapMCPClient

    llm = get_llm()
    mcp = AmapMCPClient(get_settings().amap_mcp_url)
    return build_graph(
        extractor=llm_extractor(llm),
        summarizer=llm_summarizer(llm),
        mcp=mcp,
        checkpointer=make_async_sqlite_checkpointer(get_settings().checkpoint_db_path),
    )
