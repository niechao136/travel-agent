import uuid

import httpx

from app.main import create_app
from tests.fakes import FakeGraph, interrupt_result, send_message

V1_HEADERS = {"A2A-Version": "1.0"}


async def test_send_message_completes_with_artifact():
    graph = FakeGraph([{"response_text": "# 杭州 逐日行程", "itinerary": {"destination": "杭州"}}])
    app = create_app(graph=graph)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post("/a2a", json=send_message("杭州三日游，预算3000"), headers=V1_HEADERS)
    assert resp.status_code == 200
    task = resp.json()["result"]["task"]
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"
    assert task["artifacts"][0]["parts"][0]["text"].startswith("# 杭州")


async def test_send_message_input_required_then_resume_same_task():
    graph = FakeGraph(
        [interrupt_result("请补充日期与预算。"), {"response_text": "# OK", "itinerary": {}}]
    )
    app = create_app(graph=graph)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r1 = await c.post("/a2a", json=send_message("我想去杭州"), headers=V1_HEADERS)
        task = r1.json()["result"]["task"]
        assert task["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"
        assert "预算" in task["status"]["message"]["parts"][0]["text"]
        task_id, context_id = task["id"], task["contextId"]

        graph.paused = {task_id}  # 模拟仍停在 interrupt 上（aget_state().next 非空）
        r2 = await c.post(
            "/a2a",
            json=send_message("10月1日到3日，预算3000", task_id=task_id, context_id=context_id),
            headers=V1_HEADERS,
        )
    task2 = r2.json()["result"]["task"]
    assert task2["id"] == task_id
    assert task2["status"]["state"] == "TASK_STATE_COMPLETED"


async def test_v0_3_compat_message_send_and_resume():
    """0.3 兼容模式（enable_v0_3_compat=True）：无 header + message/send + 0.3 格式。"""
    graph = FakeGraph([interrupt_result("请补充预算。"), {"response_text": "# OK", "itinerary": {}}])

    def body(text: str, task_id: str | None = None, context_id: str | None = None) -> dict:
        m = {
            "messageId": uuid.uuid4().hex,
            "role": "user",
            "kind": "message",
            "parts": [{"kind": "text", "text": text}],
        }
        if task_id:
            m["taskId"] = task_id
        if context_id:
            m["contextId"] = context_id
        return {"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": "message/send",
                "params": {"message": m}}

    app = create_app(graph=graph)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r1 = await c.post("/a2a", json=body("我想去西安"))
        task = r1.json()["result"]
        assert task["status"]["state"] == "input-required"
        task_id, context_id = task["id"], task["contextId"]

        graph.paused = {task_id}
        r2 = await c.post("/a2a", json=body("预算 5000", task_id=task_id, context_id=context_id))
    assert r2.json()["result"]["status"]["state"] == "completed"


async def test_agent_card_wellknown():
    app = create_app(graph=FakeGraph([]))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/.well-known/agent-card.json")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "travel-planner-agent"
    assert data["supportedInterfaces"][0]["url"].endswith("/a2a")
