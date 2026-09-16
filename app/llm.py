from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.config import get_settings
from app.graph.prompts import EXTRACTION_PROMPT, ITINERARY_PROMPT
from app.graph.state import Itinerary, TravelRequest, TravelRequestUpdate


def get_llm() -> BaseChatModel:
    s = get_settings()
    return ChatOpenAI(
        api_key=s.openai_api_key,
        base_url=s.openai_base_url,
        model=s.openai_model,
        temperature=0,
    )


def llm_extractor(llm: BaseChatModel):
    """协议：async (user_text, today) -> TravelRequestUpdate"""

    async def extractor(user_text: str, today: str) -> TravelRequestUpdate:
        messages = [
            SystemMessage(content=EXTRACTION_PROMPT.format(today=today)),
            HumanMessage(content=user_text),
        ]
        return await llm.with_structured_output(TravelRequestUpdate).ainvoke(messages)

    return extractor


def llm_summarizer(llm):
    """协议：async (context_text, request) -> Itinerary"""

    async def summarizer(context_text: str, request: TravelRequest) -> Itinerary:
        days = (request.end_date - request.start_date).days + 1
        daily_budget = round(request.budget / days, 2)
        prompt = ITINERARY_PROMPT.format(
            destination=request.destination,
            start=request.start_date.isoformat(),
            end=request.end_date.isoformat(),
            days=days,
            daily_budget=daily_budget,
            preferences="、".join(request.preferences) or "无",
            mcp_data=context_text,
        )
        return await llm.with_structured_output(Itinerary).ainvoke([HumanMessage(content=prompt)])

    return summarizer
