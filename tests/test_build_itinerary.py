from datetime import date

from app.graph.nodes import make_build_itinerary
from app.graph.state import TravelRequest
from tests.fakes import FakeMCP, FakeSummarizer, make_itinerary


def make_request(budget: float = 3000.0) -> TravelRequest:
    return TravelRequest(
        destination="杭州", start_date=date(2026, 10, 1), end_date=date(2026, 10, 3), budget=budget
    )


async def test_build_itinerary_calls_mcp_and_marks_verified():
    mcp, summary = FakeMCP(), FakeSummarizer(make_itinerary())
    node = make_build_itinerary(summarizer=summary, mcp=mcp)
    out = await node({"request": make_request(), "mcp_errors": []})

    # 5 次调用：天气 / 地理编码 / 景点 POI / 餐厅 POI / 酒店 POI（餐饮住宿也要真实数据）
    assert mcp.calls == [
        ("get_weather", "杭州"),
        ("geocode", "杭州"),
        ("search_pois", "景点"),
        ("search_pois", "餐厅"),
        ("search_pois", "酒店"),
    ]
    assert out["itinerary"].data_verified is True
    assert out["mcp_errors"] == []
    assert "西湖" in summary.contexts[0]  # MCP 真实数据进入汇总上下文


async def test_build_itinerary_degrades_when_mcp_fails():
    summary = FakeSummarizer(make_itinerary())
    node = make_build_itinerary(summarizer=summary, mcp=FakeMCP(fail=True))
    out = await node({"request": make_request(), "mcp_errors": []})

    assert out["itinerary"].data_verified is False  # 不裸抛异常
    assert len(out["mcp_errors"]) == 5  # 5 次 MCP 调用全部失败
    assert "weather" in out["mcp_errors"][0]
    assert any("未经核实" in w for w in out["itinerary"].warnings)
