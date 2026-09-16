from datetime import date

from app.graph.state import (
    TravelRequest,
    TravelRequestUpdate,
    merge_request,
    missing_fields,
)


def test_merge_keeps_existing_and_adds_new():
    base = TravelRequest(destination="杭州")
    merged = merge_request(base, TravelRequestUpdate(start_date=date(2026, 10, 1)))
    assert merged.destination == "杭州"
    assert merged.start_date == date(2026, 10, 1)


def test_merge_none_does_not_overwrite():
    base = TravelRequest(destination="杭州", budget=3000.0)
    merged = merge_request(base, TravelRequestUpdate())  # 全 None
    assert merged.destination == "杭州"
    assert merged.budget == 3000.0


def test_merge_preferences_dedup():
    base = TravelRequest(preferences=["自然"])
    merged = merge_request(base, TravelRequestUpdate(preferences=["自然", "美食"]))
    assert merged.preferences == ["自然", "美食"]


def test_missing_fields_order():
    assert missing_fields(TravelRequest()) == ["destination", "start_date", "end_date", "budget"]


def test_missing_fields_empty_when_complete():
    full = TravelRequest(
        destination="杭州", start_date=date(2026, 10, 1), end_date=date(2026, 10, 3), budget=3000.0
    )
    assert missing_fields(full) == []
