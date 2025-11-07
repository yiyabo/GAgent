from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers.chat_routes import AgentResult, AgentStep, StructuredChatAgent
from app.services.llm.structured_response import LLMAction, LLMReply, LLMStructuredResponse
from app.services.plans.plan_executor import ExecutionConfig, ExecutionResponse, PlanExecutor
from app.services.plans.plan_session import PlanSession


@pytest.fixture()
def chat_client() -> TestClient:
    return TestClient(app)


def _parse_task_id(prompt: str) -> int:
    match = re.search(r"Task ID:\s*(\d+)", prompt)
    if not match:
        raise AssertionError(f"prompt missing task id: {prompt!r}")
    return int(match.group(1))


class StubExecutorLLM:
    """LLM stub that reports successful execution for every task."""

    def __init__(self) -> None:
        self.calls: list[int] = []

    def generate(self, prompt: str, config: ExecutionConfig) -> ExecutionResponse:
        task_id = _parse_task_id(prompt)
        self.calls.append(task_id)
        return ExecutionResponse(
            status="success",
            content=f"completed {task_id}",
            notes=[],
            metadata={"stub": True},
        )


@pytest.mark.asyncio
async def test_structured_agent_creates_task_and_executes_plan(monkeypatch, plan_repo):
    """End-to-end validation: create_task followed by execute_plan."""
    plan = plan_repo.create_plan("Integration Plan")
    plan_id = plan.id
    root = plan_repo.create_task(plan_id, name="Root")
    plan_repo.create_task(plan_id, name="Child", parent_id=root.id)

    session = PlanSession(repo=plan_repo, plan_id=plan_id)
    session.refresh()

    executor = PlanExecutor(repo=plan_repo, llm_service=StubExecutorLLM())
    agent = StructuredChatAgent(
        plan_session=session,
        plan_decomposer=None,
        plan_executor=executor,
        extra_context={"plan_id": plan_id},
    )

    structured = LLMStructuredResponse(
        llm_reply=LLMReply(message="计划执行完毕"),
        actions=[
            LLMAction(
                kind="task_operation",
                name="create_task",
                order=1,
                parameters={
                    "task_name": "Follow-up analysis",
                    "instruction": "整理执行结果并撰写总结",
                    "parent_id": root.id,
                },
            ),
            LLMAction(
                kind="plan_operation",
                name="execute_plan",
                order=2,
                parameters={},
            ),
        ],
    )

    async def fake_invoke(self, user_message: str) -> LLMStructuredResponse:
        return structured

    monkeypatch.setattr(StructuredChatAgent, "_invoke_llm", fake_invoke)

    result = await agent.handle("请继续执行计划")

    assert result.reply.startswith("计划执行完毕")
    assert "Action summary:" in result.reply
    assert result.success is True
    assert len(result.steps) == 2

    # Step 1: create_task details should contain new node information
    create_step = result.steps[0]
    assert create_step.action.name == "create_task"
    assert create_step.success is True
    created_task = create_step.details["task"]
    assert created_task["name"] == "Follow-up analysis"
    created_task_id = created_task["id"]

    # Step 2: execute_plan returns execution summary
    exec_step = result.steps[1]
    assert exec_step.action.name == "execute_plan"
    assert exec_step.success is True
    executed_ids = exec_step.details["executed_task_ids"]
    assert created_task_id in executed_ids

    refreshed = plan_repo.get_plan_tree(plan_id)
    for task_id in executed_ids:
        node = refreshed.get_node(task_id)
        assert node.status == "completed"
        payload = json.loads(node.execution_result)
        assert payload["status"] == "success"

    # Newly created node should persist after agent run
    assert refreshed.has_node(created_task_id)
    assert result.plan_persisted is True


@pytest.mark.asyncio
async def test_structured_agent_collects_action_errors(monkeypatch, plan_repo):
    """Ensure action failures are captured and reported."""
    plan = plan_repo.create_plan("Failure Plan")
    plan_id = plan.id
    plan_repo.create_task(plan_id, name="Solo Task")

    session = PlanSession(repo=plan_repo, plan_id=plan_id)
    session.refresh()

    agent = StructuredChatAgent(
        plan_session=session,
        plan_decomposer=None,
        plan_executor=None,
        extra_context={"plan_id": plan_id},
    )

    structured = LLMStructuredResponse(
        llm_reply=LLMReply(message="尝试更新任务"),
        actions=[
            LLMAction(
                kind="task_operation",
                name="update_task",
                order=1,
                parameters={"task_id": 999, "instruction": "should fail"},
            )
        ],
    )

    async def fake_invoke(self, user_message: str) -> LLMStructuredResponse:
        return structured

    monkeypatch.setattr(StructuredChatAgent, "_invoke_llm", fake_invoke)

    result = await agent.handle("更新任务")

    assert result.reply.startswith("尝试更新任务")
    assert result.success is False
    assert len(result.steps) == 1
    step = result.steps[0]
    assert step.success is False
    assert "failed" in step.message.lower()
    assert result.errors  # error list populated
    assert result.plan_persisted is False


@pytest.mark.asyncio
async def test_structured_agent_inserts_between_tasks(monkeypatch, plan_repo):
    plan = plan_repo.create_plan("Insert Plan")
    plan_id = plan.id
    first = plan_repo.create_task(plan_id, name="First Root")
    second = plan_repo.create_task(plan_id, name="Second Root")

    session = PlanSession(repo=plan_repo, plan_id=plan_id)
    session.refresh()

    agent = StructuredChatAgent(
        plan_session=session,
        plan_decomposer=None,
        plan_executor=None,
        extra_context={"plan_id": plan_id},
    )

    structured = LLMStructuredResponse(
        llm_reply=LLMReply(message="已插入任务。"),
        actions=[
            LLMAction(
                kind="task_operation",
                name="create_task",
                order=1,
                parameters={
                    "plan_id": plan_id,
                    "task_name": "Inserted",
                    "parent_id": None,
                    "insert_after": first.id,
                    "insert_before": second.id,
                },
            )
        ],
    )

    async def fake_invoke(self, user_message: str) -> LLMStructuredResponse:
        return structured

    monkeypatch.setattr(StructuredChatAgent, "_invoke_llm", fake_invoke)

    result = await agent.handle("插入任务")

    assert result.success is True
    assert len(result.steps) == 1
    step = result.steps[0]
    assert step.success is True
    assert step.action.name == "create_task"

    tree = plan_repo.get_plan_tree(plan_id)
    root_names = [tree.get_node(node_id).name for node_id in tree.root_node_ids()]
    assert root_names == ["First Root", "Inserted", "Second Root"]


def test_chat_message_returns_pending_and_completes(monkeypatch, chat_client):
    """API should return pending response and complete actions in background."""
    structured = LLMStructuredResponse(
        llm_reply=LLMReply(message="计划已创建，稍后为你分解任务。"),
        actions=[
            LLMAction(
                kind="plan_operation",
                name="create_plan",
                order=1,
                parameters={"title": "Async Plan"},
            )
        ],
    )
    step = AgentStep(
        action=structured.actions[0],
        success=True,
        message="计划创建完成",
        details={"plan_id": 1},
    )
    agent_result = AgentResult(
        reply="计划已创建，稍后为你分解任务。",
        steps=[step],
        suggestions=["下一步可继续规划任务。"],
        primary_intent="create_plan",
        success=True,
        bound_plan_id=None,
        plan_outline=None,
        plan_persisted=False,
        errors=[],
    )

    async def fake_structured(self, user_message: str) -> LLMStructuredResponse:
        return structured

    async def fake_execute(self, structured_input: LLMStructuredResponse) -> AgentResult:
        return agent_result

    monkeypatch.setattr(
        StructuredChatAgent, "get_structured_response", fake_structured
    )
    monkeypatch.setattr(StructuredChatAgent, "execute_structured", fake_execute)

    response = chat_client.post(
        "/chat/message",
        json={"message": "创建一个计划", "session_id": "sess-integration"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["metadata"]["status"] == "pending"
    tracking_id = payload["metadata"]["tracking_id"]
    assert tracking_id.startswith("act_")
    assert payload["actions"][0]["status"] == "pending"

    status_response = chat_client.get(f"/chat/actions/{tracking_id}")
    assert status_response.status_code == 200
    status_payload = status_response.json()
    assert status_payload["status"] == "completed"
    assert status_payload["actions"][0]["status"] == "completed"
    assert status_payload["actions"][0]["message"] == "计划创建完成"
    assert status_payload["plan_id"] is None
