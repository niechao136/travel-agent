import uuid
from datetime import date
from typing import Any

import httpx
import pytest

from app.auth import TokenStore
from app.main import create_app
from tests.fakes import (
    FakeExtractor,
    FakeGraph,
    FakeMCP,
    FakeSummarizer,
    interrupt_result,
    make_itinerary,
    send_message,
)


@pytest.fixture
def auth(tmp_path):
    store = TokenStore(str(tmp_path / "t.db"))
    return store, store.issue("it")


async def test_send_message_completes_with_artifact(auth):
    store, token = auth
    graph = FakeGraph([{"response_text": "# 杭州 逐日行程", "itinerary": {"destination": "杭州"}}])
    app = create_app(graph=graph, auth_store=store)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post(
            "/a2a",
            json=send_message("杭州三日游，预算3000"),
            headers={"Authorization": f"Bearer {token}", "A2A-Version": "1.0"},
        )
    assert resp.status_code == 200
    task = resp.json()["result"]["task"]
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"
    assert task["artifacts"][0]["parts"][0]["text"].startswith("# 杭州")


async def test_send_message_input_required_then_resume_same_task(auth):
    store, token = auth
    graph = FakeGraph(
        [interrupt_result("请补充日期与预算。"), {"response_text": "# OK", "itinerary": {}}]
    )
    app = create_app(graph=graph, auth_store=store)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r1 = await c.post(
            "/a2a",
            json=send_message("我想去杭州"),
            headers={"Authorization": f"Bearer {token}", "A2A-Version": "1.0"},
        )
        task = r1.json()["result"]["task"]
        assert task["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"
        assert "预算" in task["status"]["message"]["parts"][0]["text"]
        task_id, context_id = task["id"], task["contextId"]

        graph.paused = {task_id}  # 模拟仍停在 interrupt 上（aget_state().next 非空）
        r2 = await c.post(
            "/a2a",
            json=send_message("10月1日到3日，预算3000", task_id=task_id, context_id=context_id),
            headers={"Authorization": f"Bearer {token}", "A2A-Version": "1.0"},
        )
    task2 = r2.json()["result"]["task"]
    assert task2["id"] == task_id
    assert task2["status"]["state"] == "TASK_STATE_COMPLETED"


async def test_v0_3_compat_message_send_and_resume(auth):
    """0.3 兼容模式（enable_v0_3_compat=True）：无 header + message/send + 0.3 格式。"""
    store, token = auth
    graph = FakeGraph([interrupt_result("请补充预算。"), {"response_text": "# OK", "itinerary": {}}])

    def body(
        text: str, task_id: str | None = None, context_id: str | None = None
    ) -> dict[str, Any]:
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

    app = create_app(graph=graph, auth_store=store)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r1 = await c.post(
            "/a2a", json=body("我想去西安"), headers={"Authorization": f"Bearer {token}"}
        )
        task = r1.json()["result"]
        assert task["status"]["state"] == "input-required"
        task_id, context_id = task["id"], task["contextId"]

        graph.paused = {task_id}
        r2 = await c.post(
            "/a2a",
            json=body("预算 5000", task_id=task_id, context_id=context_id),
            headers={"Authorization": f"Bearer {token}"},
        )
    task2 = r2.json()["result"]
    assert task2["id"] == task_id  # resume 复用同一 task
    assert task2["status"]["state"] == "completed"


async def test_agent_card_wellknown(auth):
    store, _token = auth
    app = create_app(graph=FakeGraph([]), auth_store=store)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/.well-known/agent-card.json")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "travel-planner-agent"
    # 地址由请求的 Host/Scheme 推导，不再依赖 PUBLIC_BASE_URL
    assert data["supportedInterfaces"][0]["url"] == "http://t/a2a"


async def test_real_graph_multi_round_resume_via_api(checkpointer, auth):
    """本项目核心验证点：真实图 + 真实 A2A handler 的多轮中断-恢复（同一 task 连续两轮）。

    与 FakeGraph 版不同，这里装配的是现成的 build_graph（fake 依赖，无需 Key），
    验证 executor ↔ 真实图 的接口契合：aget_state().next 判断 resume、thread_id 稳定、
    中断问句来自 format_question 的真实文案、最后一轮带 artifact。
    """
    from app.graph.builder import build_graph
    from app.graph.state import TravelRequestUpdate

    ex = FakeExtractor(
        [
            TravelRequestUpdate(destination="杭州"),
            TravelRequestUpdate(start_date=date(2026, 10, 1), end_date=date(2026, 10, 3)),
            TravelRequestUpdate(budget=3000.0),
        ]
    )
    graph = build_graph(
        extractor=ex,
        summarizer=FakeSummarizer(make_itinerary(total=2500.0)),
        mcp=FakeMCP(),
        checkpointer=checkpointer,
    )
    store, token = auth
    app = create_app(graph=graph, auth_store=store)
    headers = {"Authorization": f"Bearer {token}", "A2A-Version": "1.0"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        # 第 1 轮：新 task，图在缺字段中断上暂停
        r1 = await c.post("/a2a", json=send_message("我想去杭州玩"), headers=headers)
        task1 = r1.json()["result"]["task"]
        assert task1["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"
        # 真实问句来自 format_question：缺 出发日期/返程日期/预算总额
        assert "出发日期" in task1["status"]["message"]["parts"][0]["text"]
        task_id, context_id = task1["id"], task1["contextId"]

        # 第 2 轮：resume → 再次中断（多轮循环），task id 必须一致
        r2 = await c.post(
            "/a2a",
            json=send_message("10月1日到3日出发", task_id=task_id, context_id=context_id),
            headers=headers,
        )
        task2 = r2.json()["result"]["task"]
        assert task2["id"] == task_id
        assert task2["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"
        assert "预算总额" in task2["status"]["message"]["parts"][0]["text"]

        # 第 3 轮：resume → 信息齐全，完成并带 artifact
        r3 = await c.post(
            "/a2a",
            json=send_message("预算3000", task_id=task_id, context_id=context_id),
            headers=headers,
        )
        task3 = r3.json()["result"]["task"]
        assert task3["id"] == task_id
        assert task3["status"]["state"] == "TASK_STATE_COMPLETED"
        assert task3["artifacts"]
        assert task3["artifacts"][0]["parts"][0]["text"].startswith("# 杭州")
