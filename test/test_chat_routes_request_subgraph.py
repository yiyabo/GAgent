from __future__ import annotations

from app.routers.chat_routes import StructuredChatAgent
from app.services.llm.structured_response import LLMAction
from app.services.plans.plan_session import PlanSession


def test_request_subgraph_uses_plan_tree(plan_repo):
    plan = plan_repo.create_plan("Graph Plan")
    root = plan_repo.create_task(plan.id, name="Root")
    child = plan_repo.create_task(plan.id, name="Child", parent_id=root.id)

    session = PlanSession(repo=plan_repo)
    session.bind(plan.id)

    agent = StructuredChatAgent(
        plan_session=session,
        plan_decomposer=None,
    )

    action = LLMAction(
        kind="context_request",
        name="request_subgraph",
        parameters={"task_id": root.id, "max_depth": 1},
    )

    step = agent._handle_context_request(action)

    assert step.success is True
    nodes = step.details["nodes"]
    node_ids = {node["id"] for node in nodes}
    assert {root.id, child.id}.issubset(node_ids)
    # execution_result 字段应存在，即使为 None
    for node in nodes:
        assert "execution_result" in node
