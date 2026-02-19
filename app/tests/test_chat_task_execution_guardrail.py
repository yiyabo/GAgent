from app.routers.chat_routes import StructuredChatAgent
from app.services.llm.structured_response import LLMReply, LLMStructuredResponse
from app.services.plans.plan_models import PlanNode, PlanTree


class _DummyRepo:
    def __init__(self, tree: PlanTree) -> None:
        self._tree = tree

    def get_plan_tree(self, plan_id: int) -> PlanTree:
        return self._tree


class _DummyPlanSession:
    def __init__(self, *, plan_id: int, tree: PlanTree) -> None:
        self.plan_id = plan_id
        self.repo = _DummyRepo(tree)


def _build_agent(user_message: str, *, current_task_id: int = 23) -> StructuredChatAgent:
    node = PlanNode(
        id=current_task_id,
        plan_id=34,
        name="",
        status="pending",
    )
    tree = PlanTree(
        id=34,
        title="plan",
        nodes={current_task_id: node},
        adjacency={None: [current_task_id]},
    )
    agent = StructuredChatAgent.__new__(StructuredChatAgent)
    agent.plan_session = _DummyPlanSession(plan_id=34, tree=tree)
    agent.extra_context = {"current_task_id": current_task_id}
    agent._current_user_message = user_message
    return agent


def test_followthrough_guardrail_injects_rerun_action_for_execute_intent():
    agent = _build_agent("pleaseexecutetask 23")
    structured = LLMStructuredResponse(
        llm_reply=LLMReply(message=", . "),
        actions=[],
    )

    result = agent._apply_task_execution_followthrough_guardrail(structured)

    assert result.actions == []


def test_followthrough_guardrail_keeps_status_query_without_promise():
    agent = _build_agent("completed？")
    structured = LLMStructuredResponse(
        llm_reply=LLMReply(message="taskmedium. "),
        actions=[],
    )

    result = agent._apply_task_execution_followthrough_guardrail(structured)

    assert result.actions == []


def test_followthrough_guardrail_executes_when_reply_promises_start():
    agent = _build_agent("completed？")
    structured = LLMStructuredResponse(
        llm_reply=LLMReply(message=", executetask. "),
        actions=[],
    )

    result = agent._apply_task_execution_followthrough_guardrail(structured)

    assert result.actions == []
