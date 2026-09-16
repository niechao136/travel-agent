from __future__ import annotations

from typing import cast

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from app.config import get_settings
from app.graph.prompts import EXTRACTION_PROMPT, ITINERARY_PROMPT
from app.graph.state import Itinerary, TravelRequest, TravelRequestUpdate
from app.protocols import Extractor, Summarizer


def get_llm() -> BaseChatModel:
    s = get_settings()
    return ChatOpenAI(
        api_key=SecretStr(s.openai_api_key),
        base_url=s.openai_base_url,
        model=s.openai_model,
        temperature=0,
    )


def llm_extractor(llm: BaseChatModel) -> Extractor:
    """协议：async (user_text, today) -> TravelRequestUpdate"""

    async def extractor(user_text: str, today: str) -> TravelRequestUpdate:
        messages = [
            SystemMessage(content=EXTRACTION_PROMPT.format(today=today)),
            HumanMessage(content=user_text),
        ]
        result = await llm.with_structured_output(TravelRequestUpdate).ainvoke(messages)
        return cast(TravelRequestUpdate, result)

    return extractor


def llm_summarizer(llm: BaseChatModel) -> Summarizer:
    """协议：async (context_text, request) -> Itinerary"""

    async def summarizer(context_text: str, request: TravelRequest) -> Itinerary:
        start, end, budget = request.start_date, request.end_date, request.budget
        if start is None or end is None or budget is None:
            raise ValueError("生成行程前必须补齐 start_date / end_date / budget")
        days = (end - start).days + 1
        daily_budget = round(budget / days, 2)
        prompt = ITINERARY_PROMPT.format(
            destination=request.destination,
            start=start.isoformat(),
            end=end.isoformat(),
            days=days,
            daily_budget=daily_budget,
            preferences="、".join(request.preferences) or "无",
            mcp_data=context_text,
        )
        result = await llm.with_structured_output(Itinerary).ainvoke([HumanMessage(content=prompt)])
        return cast(Itinerary, result)

    return summarizer
