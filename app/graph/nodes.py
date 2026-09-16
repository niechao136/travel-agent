from __future__ import annotations

from datetime import datetime
from typing import Any

from langgraph.types import interrupt

from app.graph.state import GraphState, merge_request

FIELD_LABELS: dict[str, str] = {
    "destination": "目的地",
    "start_date": "出发日期",
    "end_date": "返程日期",
    "budget": "预算总额（元，含币种）",
}


def check_required(state: GraphState) -> dict[str, Any]:
    from app.graph.state import missing_fields

    return {"missing_fields": missing_fields(state["request"])}


def format_question(missing: list[str]) -> str:
    names = [FIELD_LABELS.get(f, f) for f in missing]
    return "为了帮你制定行程，请补充以下信息：" + "、".join(names) + "。"


def make_ask_missing():
    """一次性问出所有缺失字段；resume 值作为新的用户消息回流给 extract_and_merge。"""

    async def ask_missing(state: GraphState) -> dict[str, Any]:
        question = format_question(state["missing_fields"])
        payload = {"type": "missing_info", "missing": state["missing_fields"], "question": question}
        reply = interrupt(payload)
        return {
            "messages": state["messages"] + [{"role": "user", "content": str(reply)}],
        }

    return ask_missing


def _last_user_text(messages: list[dict[str, str]]) -> str | None:
    for msg in reversed(messages):
        if msg.get("role") == "user":
            return msg.get("content", "")
    return None


def make_extract_and_merge(extractor):
    """取最后一条用户消息做结构化抽取并增量合并；无用户消息时不动作。"""

    async def extract_and_merge(state: GraphState) -> dict[str, Any]:
        user_text = _last_user_text(state["messages"])
        if not user_text:
            return {}
        update = await extractor(user_text, datetime.now().astimezone().date().isoformat())
        return {"request": merge_request(state["request"], update)}

    return extract_and_merge


MCP_TOOL_FAILURE = "部分地图数据获取失败，行程未经核实，基于通用知识生成，请人工核实：{errors}"


def make_build_itinerary(summarizer, mcp):
    """调高德 MCP 取真实数据 → 交给 summarizer 生成结构化行程。

    任一 MCP 调用失败都不抛异常：记录 mcp_errors、data_verified=False 并附 warnings。
    """

    async def build_itinerary(state: GraphState) -> dict[str, Any]:
        request = state["request"]
        errors: list[str] = []

        async def safe(name: str, coro) -> str:
            try:
                return await coro
            except Exception as exc:  # noqa: BLE001 —— 兜底要求：任何工具失败不中断流程
                errors.append(f"{name} failed: {exc}")
                return ""

        weather = await safe("weather", mcp.get_weather(request.destination))
        geo = await safe("geo", mcp.geocode(request.destination))
        pois = await safe("poi", mcp.search_pois("景点", request.destination))

        context_text = f"【地理编码】\n{geo}\n\n【天气】\n{weather}\n\n【景点 POI】\n{pois}"
        itinerary = await summarizer(context_text, request)
        itinerary.data_verified = not errors
        if errors:
            itinerary.warnings.append(MCP_TOOL_FAILURE.format(errors="; ".join(errors)))
        return {"itinerary": itinerary, "mcp_errors": errors}

    return build_itinerary
