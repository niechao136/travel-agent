from datetime import date

from langgraph.types import Command

from app.graph.builder import build_graph
from app.graph.state import TravelRequest, TravelRequestUpdate
from tests.fakes import FakeExtractor, sqlite_checkpointer


async def test_multi_round_interrupt_merges_incrementally():
    ex = FakeExtractor(
        [
            TravelRequestUpdate(destination="杭州"),
            TravelRequestUpdate(start_date=date(2026, 10, 1), end_date=date(2026, 10, 3)),
            TravelRequestUpdate(budget=3000.0),
        ]
    )
    graph = build_graph(extractor=ex, checkpointer=sqlite_checkpointer())
    config = {"configurable": {"thread_id": "t-loop"}}

    r1 = await graph.ainvoke(
        {"request": TravelRequest(), "messages": [{"role": "user", "content": "我想去杭州玩"}]},
        config,
    )
    assert r1["__interrupt__"][0].value["missing"] == ["start_date", "end_date", "budget"]
    assert ex.calls == ["我想去杭州玩"]

    r2 = await graph.ainvoke(Command(resume="10月1日到3日出发"), config)
    assert r2["__interrupt__"][0].value["missing"] == ["budget"]
    assert r2["request"].destination == "杭州"  # 已有字段未丢
    assert r2["request"].start_date == date(2026, 10, 1)

    r3 = await graph.ainvoke(Command(resume="预算3000"), config)
    assert r3["request"].budget == 3000.0
    assert r3["request"].end_date == date(2026, 10, 3)
    assert "__interrupt__" not in r3  # 信息齐全，v1 图直达 END
