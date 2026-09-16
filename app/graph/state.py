from __future__ import annotations

from datetime import date
from typing import Literal, TypedDict

from pydantic import BaseModel, Field

REQUIRED_FIELDS: tuple[str, ...] = ("destination", "start_date", "end_date", "budget")


class TravelRequest(BaseModel):
    destination: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    budget: float | None = None
    currency: str = "CNY"
    travelers: int | None = None
    preferences: list[str] = Field(default_factory=list)


class TravelRequestUpdate(BaseModel):
    """LLM 单轮抽取结果：None 表示本轮未提到，合并时不覆盖已有值。"""

    destination: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    budget: float | None = None
    travelers: int | None = None
    preferences: list[str] | None = None


class GraphState(TypedDict):
    request: TravelRequest
    missing_fields: list[str]
    messages: list[dict[str, str]]
    itinerary: Itinerary | None
    response_text: str
    mcp_errors: list[str]
    budget_adjust_count: int


def merge_request(current: TravelRequest, update: TravelRequestUpdate) -> TravelRequest:
    """增量合并：只覆盖本轮新提到的字段；preferences 去重追加。"""
    merged = current.model_copy()
    for key, value in update.model_dump().items():
        if value is None:
            continue
        if key == "preferences":
            seen = set(merged.preferences)
            merged.preferences = merged.preferences + [p for p in value if p not in seen]
        else:
            setattr(merged, key, value)
    return merged


def missing_fields(request: TravelRequest) -> list[str]:
    result: list[str] = []
    for field in REQUIRED_FIELDS:
        value = getattr(request, field)
        if value is None:
            result.append(field)
    return result


class ItineraryItem(BaseModel):
    time: str
    type: Literal["attraction", "meal", "hotel", "transit", "free"]
    name: str
    location: str | None = None
    est_cost_cny: float = 0.0
    notes: str | None = None


class ItineraryDay(BaseModel):
    day: int
    date: str
    weather: str | None = None
    items: list[ItineraryItem] = Field(default_factory=list)
    daily_est_cost_cny: float = 0.0


class Itinerary(BaseModel):
    destination: str
    days: list[ItineraryDay] = Field(default_factory=list)
    total_est_cost_cny: float = 0.0
    data_verified: bool = False
    warnings: list[str] = Field(default_factory=list)
