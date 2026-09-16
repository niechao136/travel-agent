from datetime import date

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from app.graph.builder import build_graph
from app.graph.state import TravelRequest, TravelRequestUpdate
from tests.fakes import (
    FakeExtractor,
    FakeMCP,
    FakeSummarizer,
    make_itinerary,
    partial_state,
    thread_config,
)


def make_graph(summary: FakeSummarizer, checkpointer: AsyncSqliteSaver):
    ex = FakeExtractor([TravelRequestUpdate(destination="杭州")])
    return build_graph(
        extractor=ex, summarizer=summary, mcp=FakeMCP(), checkpointer=checkpointer
    )


FULL_INPUT = partial_state(
    request=TravelRequest(
        destination="杭州", start_date=date(2026, 10, 1), end_date=date(2026, 10, 3), budget=3000.0
    ),
    messages=[{"role": "user", "content": "杭州三日游"}],
)
CONFIG = thread_config("t-full")


async def test_complete_flow_presents_draft(checkpointer):
    graph = make_graph(FakeSummarizer(make_itinerary(total=2500.0)), checkpointer)
    result = await graph.ainvoke(FULL_INPUT, CONFIG)
    assert result["response_text"].startswith("# 杭州")
    assert "西湖" in result["response_text"]
    assert result["itinerary"].total_est_cost_cny == 2500.0


async def test_budget_overrun_interrupts_and_resume_budget(checkpointer):
    graph = make_graph(FakeSummarizer(make_itinerary(total=4000.0)), checkpointer)  # 4000 > 3000*1.2
    r1 = await graph.ainvoke(FULL_INPUT, CONFIG)
    payload = r1["__interrupt__"][0].value
    assert payload["type"] == "budget_overrun"
    # 超出额与预算额必须分清：总花费 4000 超预算 3000，超 1000 元
    assert "超出预算" in payload["question"]
    assert "超出预算 1000 元" in payload["question"]
    assert "预算 3000 元" in payload["question"]

    r2 = await graph.ainvoke(Command(resume="budget=5000"), CONFIG)
    assert r2["request"].budget == 5000.0
    assert "__interrupt__" not in r2
    assert r2["response_text"].startswith("# 杭州")


async def test_budget_overrun_gives_up_after_two_adjusts(checkpointer):
    graph = make_graph(FakeSummarizer(make_itinerary(total=4000.0)), checkpointer)
    await graph.ainvoke(FULL_INPUT, CONFIG)
    r2 = await graph.ainvoke(Command(resume="days=+1"), CONFIG)  # 第一次调整，仍超支 → 再中断
    assert r2["__interrupt__"][0].value["type"] == "budget_overrun"
    r3 = await graph.ainvoke(Command(resume="keep"), CONFIG)  # 达调整上限 → 强制展示
    assert "__interrupt__" not in r3
    assert r3["response_text"].startswith("# 杭州")


async def test_budget_overrun_invalid_resume_is_ignored(checkpointer):
    """resume 文本无法解析（如 budget=五千）时按 keep 处理，不得让任务永久 failed。"""
    graph = make_graph(FakeSummarizer(make_itinerary(total=4000.0)), checkpointer)
    await graph.ainvoke(FULL_INPUT, CONFIG)
    r2 = await graph.ainvoke(Command(resume="budget=五千"), CONFIG)  # 非法值 → 等价 keep
    assert r2["request"].budget == 3000.0  # 预算未被破坏
    assert r2["__interrupt__"][0].value["type"] == "budget_overrun"  # 仍超支，再次中断
    r3 = await graph.ainvoke(Command(resume="keep"), CONFIG)
    assert "__interrupt__" not in r3
    assert r3["response_text"].startswith("# 杭州")
