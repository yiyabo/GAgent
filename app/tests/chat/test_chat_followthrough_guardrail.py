from types import SimpleNamespace

from app.routers.chat_routes import StructuredChatAgent
from app.services.llm.structured_response import LLMAction, LLMReply, LLMStructuredResponse


class _FakeNode:
    def __init__(self, node_id: int, name: str, *, status: str = "pending", instruction: str = "") -> None:
        self.id = node_id
        self.name = name
        self.status = status
        self.instruction = instruction

    def display_name(self) -> str:
        return self.name


class _FakeTree:
    def __init__(self, nodes: dict[int, _FakeNode], children: dict[int, list[int]]) -> None:
        self.nodes = nodes
        self._children = children

    def has_node(self, node_id: int) -> bool:
        return node_id in self.nodes

    def get_node(self, node_id: int) -> _FakeNode:
        return self.nodes[node_id]

    def children_ids(self, node_id: int) -> list[int]:
        return list(self._children.get(node_id, []))


def _build_agent(
    *,
    tree: _FakeTree,
    user_message: str,
    current_task_id: int | None = None,
    followthrough_guardrail_enabled: bool = False,
    request_tier: str | None = None,
    intent_type: str | None = None,
) -> StructuredChatAgent:
    agent = StructuredChatAgent.__new__(StructuredChatAgent)
    agent.plan_session = SimpleNamespace(
        plan_id=34,
        repo=SimpleNamespace(get_plan_tree=lambda _plan_id: tree),
    )
    agent.extra_context = {}
    if followthrough_guardrail_enabled:
        agent.extra_context["followthrough_guardrail_enabled"] = True
    if request_tier is not None:
        agent.extra_context["request_tier"] = request_tier
    if intent_type is not None:
        agent.extra_context["intent_type"] = intent_type
    if current_task_id is not None:
        agent.extra_context["current_task_id"] = current_task_id
    agent._current_user_message = user_message
    return agent


def test_followthrough_guardrail_infers_introduction_task_from_reply_promise() -> None:
    tree = _FakeTree(
        nodes={
            4: _FakeNode(4, "summary", status="pending"),
            22: _FakeNode(22, "summary(Abstract)", status="completed", instruction="write abstract"),
            23: _FakeNode(23, "(Introduction)", status="pending", instruction="write introduction"),
        },
        children={4: [22, 23]},
    )
    agent = _build_agent(tree=tree, user_message="completed？")
    structured = LLMStructuredResponse(
        llm_reply=LLMReply(message="writefile. "),
        actions=[],
    )

    patched = agent._apply_task_execution_followthrough_guardrail(structured)

    assert patched.actions == []


def test_followthrough_guardrail_uses_atomic_descendant_for_composite_current_task() -> None:
    tree = _FakeTree(
        nodes={
            4: _FakeNode(4, "summary", status="pending"),
            23: _FakeNode(23, "(Introduction)", status="pending"),
        },
        children={4: [23]},
    )
    agent = _build_agent(
        tree=tree,
        user_message="executetask",
        current_task_id=4,
    )
    structured = LLMStructuredResponse(
        llm_reply=LLMReply(message=", execute. "),
        actions=[],
    )

    patched = agent._apply_task_execution_followthrough_guardrail(structured)

    assert patched.actions == []


def test_followthrough_target_prefers_explicit_chinese_task_reference_over_stale_current_task() -> None:
    tree = _FakeTree(
        nodes={
            1: _FakeNode(1, "root", status="pending"),
            3: _FakeNode(3, "Task 3", status="pending"),
            4: _FakeNode(4, "Task 4", status="pending"),
        },
        children={1: [3, 4]},
    )
    agent = _build_agent(
        tree=tree,
        user_message="可以的，那你开始完成任务3吧，补齐之前的一些问题",
        current_task_id=1,
    )

    target = agent._resolve_followthrough_target_task_id(
        tree=tree,
        user_message=agent._current_user_message,
        reply_text="我现在开始执行。",
    )

    assert target == 3


def test_followthrough_guardrail_keeps_status_query_without_execution_intent() -> None:
    tree = _FakeTree(
        nodes={
            23: _FakeNode(23, "(Introduction)", status="pending"),
        },
        children={},
    )
    agent = _build_agent(tree=tree, user_message="completed？")
    structured = LLMStructuredResponse(
        llm_reply=LLMReply(message="completed. "),
        actions=[],
    )

    patched = agent._apply_task_execution_followthrough_guardrail(structured)

    assert patched.actions == []


def test_followthrough_guardrail_replaces_exploratory_file_operations_when_enabled() -> None:
    tree = _FakeTree(
        nodes={
            23: _FakeNode(23, "(Introduction)", status="pending"),
        },
        children={},
    )
    agent = _build_agent(
        tree=tree,
        user_message="请继续执行当前任务",
        current_task_id=23,
        followthrough_guardrail_enabled=True,
        request_tier="execute",
        intent_type="execute_task",
    )
    structured = LLMStructuredResponse(
        llm_reply=LLMReply(message="I will inspect the outputs first."),
        actions=[
            LLMAction(
                kind="tool_operation",
                name="file_operations",
                parameters={"operation": "list", "path": "/tmp/results"},
                order=1,
            )
        ],
    )

    patched = agent._apply_task_execution_followthrough_guardrail(structured)

    assert len(patched.actions) == 1
    action = patched.actions[0]
    assert action.kind == "task_operation"
    assert action.name == "rerun_task"
    assert action.parameters == {"task_id": 23}


def test_followthrough_guardrail_keeps_non_exploratory_actions_when_enabled() -> None:
    tree = _FakeTree(
        nodes={
            23: _FakeNode(23, "(Introduction)", status="pending"),
        },
        children={},
    )
    agent = _build_agent(
        tree=tree,
        user_message="continue task 23 now",
        current_task_id=23,
        followthrough_guardrail_enabled=True,
    )
    structured = LLMStructuredResponse(
        llm_reply=LLMReply(message="I will write the output now."),
        actions=[
            LLMAction(
                kind="tool_operation",
                name="file_operations",
                parameters={
                    "operation": "write",
                    "path": "/tmp/results.txt",
                    "content": "done",
                },
                order=1,
            )
        ],
    )

    patched = agent._apply_task_execution_followthrough_guardrail(structured)

    assert len(patched.actions) == 1
    action = patched.actions[0]
    assert action.kind == "tool_operation"
    assert action.name == "file_operations"
    assert action.parameters["operation"] == "write"
