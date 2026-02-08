from types import SimpleNamespace

from app.routers.chat_routes import StructuredChatAgent
from app.services.llm.structured_response import LLMReply, LLMStructuredResponse


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


def _build_agent(*, tree: _FakeTree, user_message: str, current_task_id: int | None = None) -> StructuredChatAgent:
    agent = StructuredChatAgent.__new__(StructuredChatAgent)
    agent.plan_session = SimpleNamespace(
        plan_id=34,
        repo=SimpleNamespace(get_plan_tree=lambda _plan_id: tree),
    )
    agent.extra_context = {}
    if current_task_id is not None:
        agent.extra_context["current_task_id"] = current_task_id
    agent._current_user_message = user_message
    return agent


def test_followthrough_guardrail_infers_introduction_task_from_reply_promise() -> None:
    tree = _FakeTree(
        nodes={
            4: _FakeNode(4, "撰写论文摘要和引言", status="pending"),
            22: _FakeNode(22, "撰写论文摘要（Abstract）", status="completed", instruction="write abstract"),
            23: _FakeNode(23, "撰写引言章节（Introduction）", status="pending", instruction="write introduction"),
        },
        children={4: [22, 23]},
    )
    agent = _build_agent(tree=tree, user_message="现在完成了吗？")
    structured = LLMStructuredResponse(
        llm_reply=LLMReply(message="我将立即开始撰写引言并写入主文件。"),
        actions=[],
    )

    patched = agent._apply_task_execution_followthrough_guardrail(structured)

    assert len(patched.actions) == 1
    action = patched.actions[0]
    assert action.kind == "task_operation"
    assert action.name == "rerun_task"
    assert action.parameters.get("task_id") == 23


def test_followthrough_guardrail_uses_atomic_descendant_for_composite_current_task() -> None:
    tree = _FakeTree(
        nodes={
            4: _FakeNode(4, "撰写论文摘要和引言", status="pending"),
            23: _FakeNode(23, "撰写引言章节（Introduction）", status="pending"),
        },
        children={4: [23]},
    )
    agent = _build_agent(
        tree=tree,
        user_message="开始执行当前任务",
        current_task_id=4,
    )
    structured = LLMStructuredResponse(
        llm_reply=LLMReply(message="收到，我马上执行。"),
        actions=[],
    )

    patched = agent._apply_task_execution_followthrough_guardrail(structured)

    assert len(patched.actions) == 1
    assert patched.actions[0].parameters.get("task_id") == 23


def test_followthrough_guardrail_keeps_status_query_without_execution_intent() -> None:
    tree = _FakeTree(
        nodes={
            23: _FakeNode(23, "撰写引言章节（Introduction）", status="pending"),
        },
        children={},
    )
    agent = _build_agent(tree=tree, user_message="现在完成了吗？")
    structured = LLMStructuredResponse(
        llm_reply=LLMReply(message="引言章节尚未完成。"),
        actions=[],
    )

    patched = agent._apply_task_execution_followthrough_guardrail(structured)

    assert patched.actions == []
