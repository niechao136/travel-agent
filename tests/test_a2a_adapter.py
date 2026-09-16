from a2a.types import Task, TaskArtifactUpdateEvent, TaskState, TaskStatusUpdateEvent
from langgraph.types import Command

from app.a2a_adapter import TravelAgentExecutor
from tests.fakes import FakeEventQueue, FakeGraph, interrupt_result, make_context, part_text


async def test_new_task_with_interrupt_emits_task_then_input_required():
    graph = FakeGraph([interrupt_result("请补充目的地、日期与预算。")])
    executor = TravelAgentExecutor(graph)
    queue = FakeEventQueue()

    await executor.execute(make_context("我想出去玩"), queue)
    # a2a 1.x task mode：首事件必须是 Task，随后才是状态事件
    assert isinstance(queue.events[0], Task)
    assert queue.events[0].status.state == TaskState.TASK_STATE_SUBMITTED
    last = queue.events[-1]
    assert isinstance(last, TaskStatusUpdateEvent)
    assert last.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
    assert "请补充" in part_text(last.status.message.parts[0])
    # 初始输入应包含 user 消息与空 TravelRequest
    first_input = graph.invocations[0][0]
    assert first_input["messages"][0]["content"] == "我想出去玩"


async def test_resume_uses_command_with_thread_id_task_id():
    graph = FakeGraph(
        [{"response_text": "# 行程", "itinerary": {"destination": "杭州"}}],
        paused={"task-9"},
    )
    executor = TravelAgentExecutor(graph)
    queue = FakeEventQueue()
    existing_task = type("T", (), {"id": "task-9"})()

    await executor.execute(make_context("杭州", task_id="task-9", current_task=existing_task), queue)
    resumed_input, config = graph.invocations[0]
    assert isinstance(resumed_input, Command) and resumed_input.resume == "杭州"
    assert config["configurable"]["thread_id"] == "task-9"
    # resume 场景已有 task，不再重发 Task 事件
    assert not any(isinstance(e, Task) for e in queue.events)
    assert any(isinstance(e, TaskArtifactUpdateEvent) for e in queue.events)
    last = queue.events[-1]
    assert last.status.state == TaskState.TASK_STATE_COMPLETED


async def test_graph_exception_emits_failed():
    class BoomGraph(FakeGraph):
        async def ainvoke(self, graph_input, config):
            raise RuntimeError("boom")

    executor = TravelAgentExecutor(BoomGraph([]))
    queue = FakeEventQueue()
    await executor.execute(make_context("hi"), queue)
    last = queue.events[-1]
    assert last.status.state == TaskState.TASK_STATE_FAILED
    assert "boom" in part_text(last.status.message.parts[0])
