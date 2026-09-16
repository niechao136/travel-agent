from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.config import get_settings
from app.graph.prompts import EXTRACTION_PROMPT
from app.graph.state import TravelRequestUpdate


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
