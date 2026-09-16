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


async def test_multi_round_input_required_then_resume_completes():
    """核心验证点：同一 task 连续多轮 input-required → resume，状态正确延续。"""
    graph = FakeGraph(
        [
            interrupt_result("请补充日期与预算。"),
            interrupt_result("请补充预算。"),
            {"response_text": "# 行程", "itinerary": {"destination": "杭州"}},
        ]
    )
    executor = TravelAgentExecutor(graph)
    existing_task = type("T", (), {"id": "task-9"})()

    # 第 1 轮：新 task，图在缺字段中断上暂停
    queue1 = FakeEventQueue()
    await executor.execute(make_context("我想去杭州", task_id="task-9"), queue1)
    assert queue1.events[-1].status.state == TaskState.TASK_STATE_INPUT_REQUIRED

    # 第 2 轮：resume → 再次中断（多轮循环）
    graph.paused.add("task-9")
    queue2 = FakeEventQueue()
    await executor.execute(
        make_context("10月1日到3日", task_id="task-9", current_task=existing_task), queue2
    )
    assert queue2.events[-1].status.state == TaskState.TASK_STATE_INPUT_REQUIRED
    assert not any(isinstance(e, Task) for e in queue2.events)  # 已有 task，不重发 Task 事件

    # 第 3 轮：resume → 完成
    queue3 = FakeEventQueue()
    await executor.execute(
        make_context("预算3000", task_id="task-9", current_task=existing_task), queue3
    )
    assert queue3.events[-1].status.state == TaskState.TASK_STATE_COMPLETED

    # 三轮的 thread_id 恒为 task_id，且第 2/3 轮都走 Command(resume=...)（不是重新初始化）
    assert [c["configurable"]["thread_id"] for _, c in graph.invocations] == ["task-9"] * 3
    assert isinstance(graph.invocations[1][0], Command)
    assert isinstance(graph.invocations[2][0], Command)
    assert graph.invocations[1][0].resume == "10月1日到3日"
