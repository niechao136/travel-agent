from __future__ import annotations

from collections.abc import Awaitable
from datetime import datetime, timedelta
from typing import Any

from langgraph.types import interrupt

from app.graph.state import GraphState, Itinerary, TravelRequest, merge_request
from app.protocols import AmapTools, Extractor, Summarizer

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


def make_extract_and_merge(extractor: Extractor):
    """取最后一条用户消息做结构化抽取并增量合并；无用户消息时不动作。"""

    async def extract_and_merge(state: GraphState) -> dict[str, Any]:
        user_text = _last_user_text(state["messages"])
        if not user_text:
            return {}
        update = await extractor(user_text, datetime.now().astimezone().date().isoformat())
        return {"request": merge_request(state["request"], update)}

    return extract_and_merge


MCP_TOOL_FAILURE = "部分地图数据获取失败，行程未经核实，基于通用知识生成，请人工核实：{errors}"


def make_build_itinerary(summarizer: Summarizer, mcp: AmapTools):
    """调高德 MCP 取真实数据 → 交给 summarizer 生成结构化行程。

    任一 MCP 调用失败都不抛异常：记录 mcp_errors、data_verified=False 并附 warnings。
    """

    async def build_itinerary(state: GraphState) -> dict[str, Any]:
        request = state["request"]
        destination = request.destination
        if destination is None:
            raise RuntimeError("build_itinerary 需要明确的目的地")
        errors: list[str] = []

        async def safe(name: str, coro: Awaitable[str]) -> str:
            try:
                return await coro
            except Exception as exc:  # noqa: BLE001 —— 兜底要求：任何工具失败不中断流程
                errors.append(f"{name} failed: {exc}")
                return ""

        weather = await safe("weather", mcp.get_weather(destination))
        geo = await safe("geo", mcp.geocode(destination))
        pois = await safe("poi", mcp.search_pois("景点", destination))
        restaurants = await safe("poi_restaurant", mcp.search_pois("餐厅", destination))
        hotels = await safe("poi_hotel", mcp.search_pois("酒店", destination))

        context_text = (
            f"【地理编码】\n{geo}\n\n【天气】\n{weather}\n\n【景点 POI】\n{pois}"
            f"\n\n【餐厅 POI】\n{restaurants}\n\n【酒店 POI】\n{hotels}"
        )
        itinerary = await summarizer(context_text, request)
        itinerary.data_verified = not errors
        if errors:
            itinerary.warnings.append(MCP_TOOL_FAILURE.format(errors="; ".join(errors)))
        return {"itinerary": itinerary, "mcp_errors": errors}

    return build_itinerary


BUDGET_OVERRUN_TOLERANCE = 1.2  # 超出预算 20% 才触发第二类中断
MAX_BUDGET_ADJUSTS = 2

TYPE_LABELS: dict[str, str] = {
    "attraction": "游览",
    "meal": "餐饮",
    "hotel": "住宿",
    "transit": "交通",
    "free": "自由活动",
}


def _apply_budget_adjust(updated: TravelRequest, req: TravelRequest, text: str) -> None:
    """解析 resume 文本并就地更新 request（budget=/days=+N）；无法解析时按 keep 语义处理。

    容错要点：用户可能回复 `budget=五千` 之类非法值，直接 float()/int() 会抛 ValueError，
    使任务永久 failed。此处吞掉解析错误、保持原值（等价 keep），由调整计数上限兜底退出。
    """
    if text.startswith("budget="):
        try:
            updated.budget = float(text.split("=", 1)[1])
        except ValueError:
            pass
    elif text.startswith("days="):
        try:
            extra_days = int(text.split("=", 1)[1].lstrip("+"))
        except ValueError:
            return
        if req.end_date is not None:
            updated.end_date = req.end_date + timedelta(days=extra_days)


def make_ask_budget_adjust():
    """第二类中断：预估超支时询问调整方式。

    resume 约定：keep（维持现状）| budget=<新预算数字> | days=+N（延长 N 天）。
    """

    async def ask_budget_adjust(state: GraphState) -> dict[str, Any]:
        it, req = state["itinerary"], state["request"]
        budget = req.budget
        if it is None or budget is None:
            # 只有 route_after_build 判定超支时才会进入本节点，二者届时必然已就绪
            raise RuntimeError("ask_budget_adjust 需要已生成的行程与预算")
        question = (
            f"预估总花费 {it.total_est_cost_cny:.0f} 元，"
            f"超出预算 {it.total_est_cost_cny - budget:.0f} 元（预算 {budget:.0f} 元）。"
            "回复 keep 维持本方案；budget=新预算（如 budget=5000）；days=+1 延长行程。"
        )
        reply = interrupt(
            {
                "type": "budget_overrun",
                "question": question,
                "estimated_total": it.total_est_cost_cny,
                "budget": budget,
            }
        )
        updated = req.model_copy()
        text = str(reply).strip()
        _apply_budget_adjust(updated, req, text)
        return {"request": updated, "budget_adjust_count": state.get("budget_adjust_count", 0) + 1}

    return ask_budget_adjust


def format_itinerary(it: Itinerary) -> str:
    lines = [f"# {it.destination} 逐日行程", f"预估总花费：{it.total_est_cost_cny:.0f} 元"]
    lines += [f"> ⚠ {w}" for w in it.warnings]
    for d in it.days:
        header = f"## Day {d.day}（{d.date}）" + (f" 天气：{d.weather}" if d.weather else "")
        lines.append(header)
        for item in d.items:
            label = TYPE_LABELS.get(item.type, item.type)
            lines.append(f"- {item.time} {label}：{item.name}（约 {item.est_cost_cny:.0f} 元）")
        lines.append(f"- 当日小计：约 {d.daily_est_cost_cny:.0f} 元")
    return "\n".join(lines)


def present_draft(state: GraphState) -> dict[str, Any]:
    it = state["itinerary"]
    if it is None:
        raise RuntimeError("present_draft 需要已生成的行程")
    text = format_itinerary(it)
    return {
        "response_text": text,
        "messages": state["messages"] + [{"role": "assistant", "content": text}],
    }


def route_after_build(state: GraphState) -> str:
    it, req = state.get("itinerary"), state["request"]
    over_budget = bool(it and req.budget and it.total_est_cost_cny > req.budget * BUDGET_OVERRUN_TOLERANCE)
    if over_budget and state.get("budget_adjust_count", 0) < MAX_BUDGET_ADJUSTS:
        return "ask_budget_adjust"
    return "present_draft"
