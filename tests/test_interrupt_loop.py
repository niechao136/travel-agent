from datetime import date

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from app.graph.builder import build_graph
from app.graph.state import Itinerary, TravelRequest, TravelRequestUpdate
from tests.fakes import (
    FakeExtractor,
    FakeMCP,
    FakeSummarizer,
    make_itinerary,
    partial_state,
    thread_config,
)


async def test_multi_round_interrupt_merges_incrementally(checkpointer: AsyncSqliteSaver):
    ex = FakeExtractor(
        [
            TravelRequestUpdate(destination="杭州"),
            TravelRequestUpdate(start_date=date(2026, 10, 1), end_date=date(2026, 10, 3)),
            TravelRequestUpdate(budget=3000.0),
        ]
    )
    graph = build_graph(
        extractor=ex,
        summarizer=FakeSummarizer(make_itinerary(total=2500.0)),
        mcp=FakeMCP(),
        checkpointer=checkpointer,
    )
    config = thread_config("t-loop")

    r1 = await graph.ainvoke(
        partial_state(
            request=TravelRequest(), messages=[{"role": "user", "content": "我想去杭州玩"}]
        ),
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
    assert "__interrupt__" not in r3  # 信息齐全 → build_itinerary → present_draft → END
    assert "response_text" in r3  # 图确实走到了展示草稿的节点
    assert r3["itinerary"].data_verified is True  # 图确实走到了取真实数据的节点

    # checkpoint 往返：Itinerary 必须命中 serde allowlist，否则会被静默降级为裸 dict
    snapshot = await graph.aget_state(config)
    assert isinstance(snapshot.values["itinerary"], Itinerary)
    assert snapshot.values["itinerary"].data_verified is True
