from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from app.graph.nodes import check_required, format_question, make_ask_missing
from app.graph.state import GraphState, TravelRequest


def test_check_required_writes_missing_fields():
    out = check_required({"request": TravelRequest(), "missing_fields": []})
    assert out["missing_fields"] == ["destination", "start_date", "end_date", "budget"]


def test_format_question_lists_all_fields():
    text = format_question(["destination", "budget"])
    assert "目的地" in text and "预算总额" in text


async def test_ask_missing_interrupts_then_resumes():
    g = StateGraph(GraphState)
    g.add_node("check_required", check_required)
    g.add_node("ask_missing", make_ask_missing())
    g.add_edge(START, "check_required")
    g.add_conditional_edges(
        "check_required",
        lambda s: "ask" if s["missing_fields"] else END,
        {"ask": "ask_missing", END: END},
    )
    g.add_edge("ask_missing", END)
    async with AsyncSqliteSaver.from_conn_string(":memory:") as saver:
        graph = g.compile(checkpointer=saver)
        config = {"configurable": {"thread_id": "t1"}}

        r1 = await graph.ainvoke(
            {"request": TravelRequest(), "messages": [], "missing_fields": []}, config
        )
        payload = r1["__interrupt__"][0].value
        assert payload["type"] == "missing_info"
        assert "目的地" in payload["question"]

        r2 = await graph.ainvoke(Command(resume="回复文本"), config)
        assert r2["messages"][-1] == {"role": "user", "content": "回复文本"}
