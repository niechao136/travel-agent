from __future__ import annotations

import logging

from a2a.helpers.proto_helpers import new_data_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Message, Part, Task, TaskState, TaskStatus
from langgraph.types import Command

from app.graph.state import GraphState, TravelRequest

logger = logging.getLogger(__name__)

INITIAL_STATE: GraphState = {
    "request": TravelRequest(),
    "missing_fields": [],
    "messages": [],
    "itinerary": None,
    "response_text": "",
    "mcp_errors": [],
    "budget_adjust_count": 0,
}


def _text_part(text: str) -> Part:
    return Part(text=text)


def _data_part(payload: dict[str, object]) -> Part:
    return new_data_part(payload, media_type="application/json")


def extract_user_text(message: Message | None) -> str:
    """取用户文本：按 AgentCard 声明的 text/plain 输入模式挑选 part。

    调用方（如 a2a-gateway）可能同时发送 data part 与 text part 且顺序不定，
    不能假定 parts[0] 就是文本，否则会读到空串而误判为"信息不足"。
    """
    if message is None:
        return ""
    for part in message.parts:
        if part.HasField("text") and part.text.strip():
            return part.text
    return ""


class TravelAgentExecutor(AgentExecutor):
    """把 LangGraph 执行映射为 A2A task 状态（a2a-sdk 1.x，task mode）：

    - 首次调用先入队 `Task`（1.x 要求：TaskStatusUpdateEvent 之前必须有 Task）
    - `__interrupt__` → `TASK_STATE_INPUT_REQUIRED`（question 进 message.parts）
    - 恢复 → `Command(resume=用户回复)`，`thread_id = A2A task_id`
    - 完成 → artifact（行程 markdown）+ `TASK_STATE_COMPLETED`
    """

    def __init__(self, graph):
        self.graph = graph

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id, context_id = context.task_id, context.context_id
        if task_id is None or context_id is None:
            raise ValueError("RequestContext 缺少 task_id/context_id，无法映射 LangGraph 线程")
        updater = TaskUpdater(event_queue, task_id, context_id)
        if context.current_task is None:
            await event_queue.enqueue_event(
                Task(
                    id=task_id,
                    context_id=context_id,
                    status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
                )
            )
        await updater.start_work()

        user_text = extract_user_text(context.message)
        config = {"configurable": {"thread_id": task_id}}
        try:
            snapshot = await self.graph.aget_state(config)
            if snapshot.next:
                result = await self.graph.ainvoke(Command(resume=user_text), config)
            else:
                initial = {**INITIAL_STATE, "messages": [{"role": "user", "content": user_text}]}
                result = await self.graph.ainvoke(initial, config)
        except Exception as exc:  # noqa: BLE001 —— 兜底：任何内部错误映射为 task failed
            logger.exception("生成行程失败 task_id=%s", task_id)
            await updater.failed(
                message=updater.new_agent_message(
                    parts=[
                        _text_part(f"生成行程失败：{exc}"),
                        # 结构化错误（稳定错误码 + retryable），便于调用方决策是否重试
                        _data_part(
                            {"error": "internal_error", "message": str(exc), "retryable": True}
                        ),
                    ]
                )
            )
            return

        if "__interrupt__" in result:
            payload = result["__interrupt__"][-1].value
            await updater.requires_input(
                message=updater.new_agent_message(parts=[_text_part(payload["question"])])
            )
            return

        await updater.add_artifact([_text_part(result.get("response_text", ""))], name="itinerary")
        await updater.complete(
            message=updater.new_agent_message(parts=[_text_part("行程方案已生成完毕。")])
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("cancel not supported")
