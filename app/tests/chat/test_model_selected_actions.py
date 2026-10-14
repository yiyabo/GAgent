"""Natural-language actions enter the existing model loop, not phrase-triggered dispatch."""

import asyncio, json
from types import SimpleNamespace
import pytest

from app.llm import NativeStreamResult, NativeToolCall
from app.services.deep_think_agent import DeepThinkAgent
from app.services.llm.structured_response import LLMStructuredResponse
from app.routers.chat.agent import StructuredChatAgent
from app.routers.chat.models import AgentStep
from app.routers.chat.model_selection import resolve_model_routing, update_model_context, MODEL_SELECTION
from app.routers.chat.task_context import _build_deep_think_task_context
from app.services.plans.edit_policy import agent_plan_edit_allowed, blocked_plan_edit
from app.tests.chat.test_unified_stream_event_golden import _build_agent, _patch_runtime, _run

PHRASES = [
    "task2之前失败了，再重新执行吧",
    "把第二个阶段接着做完吧",
    "定位和大纲那个阶段，再试一次",
    "能不能帮我把 task2 再跑一遍？",
    "刚才那个继续往下做",
    "Please resume the outline stage",
]
READ_REQUESTS = [
    "task2修好了吗？",
    "别执行task2，先解释失败原因",
    "我引用的是‘执行task2’，并没要求你执行",
    "介绍一下解聚酶",
    "是否需要重跑那个阶段？",
]


@pytest.fixture(autouse=True)
def enable_model_selection(monkeypatch):
    from app.services.foundation.settings import get_settings

    monkeypatch.setattr(get_settings(), "chat_model_action_selection_enabled", True)


@pytest.mark.parametrize("message", PHRASES + READ_REQUESTS)
def test_router_never_promotes_words_to_operations(monkeypatch, message):
    from app.routers.chat import request_routing

    for name in ("resolve_intent_type", "classify_request_signals", "resolve_plan_lifecycle_intent"):
        monkeypatch.setattr(request_routing, name, lambda **kw: pytest.fail("old phrase classifier ran"))
    decision = resolve_model_routing(message=message, plan_id=34, current_task_id=66)
    assert decision.effective_user_message == message
    assert decision.route_reason_codes == [MODEL_SELECTION]
    assert decision.use_deep_think and decision.intent_type == "chat"
    assert not any(
        (
            decision.explicit_task_override,
            decision.full_plan_execution,
            decision.plan_create_required,
            decision.plan_execute_required,
            decision.plan_review_required,
            decision.plan_optimize_required,
        )
    )


def test_context_binding_is_reference_not_assignment():
    agent = _build_agent(
        extra_context={
            "plan_execute_required": True,
            "_full_plan_executor_delegate": True,
            "explicit_task_override": True,
            "pending_scope_task_ids": [66],
        }
    )
    update_model_context(agent, resolve_model_routing(message="解释失败原因"))
    assert agent.extra_context["referenced_task_id"] == 66 and "current_task_id" not in agent.extra_context
    assert not agent.extra_context["plan_execute_required"]
    assert "_full_plan_executor_delegate" not in agent.extra_context
    assert _build_deep_think_task_context(agent, user_message="解释失败原因") is None
    assert agent.extra_context["model_plan_index"]["tasks"][0]["id"] == 66


def test_explicit_output_contract_is_not_discarded():
    spec = {"required_outputs": [{"kind": "document", "target_path": "answer.md"}]}
    agent = _build_agent(extra_context={"output_spec": spec})
    update_model_context(agent, resolve_model_routing(message="保存到answer.md"))
    assert agent.extra_context["output_spec"] is spec


def test_background_tree_fence_survives_model_semantics():
    agent = SimpleNamespace(extra_context={"action_selection": "model"}, _plan_tree_edit_allowed=False)
    assert agent_plan_edit_allowed(agent) is False
    assert blocked_plan_edit("decompose_task", agent_plan_edit_allowed(agent))["executed"] is False
    agent._plan_tree_edit_allowed = None
    assert agent_plan_edit_allowed(agent) is None


class ScriptedProvider:
    def __init__(self, query, operation):
        self.query, self.operation = query, operation
        self.calls = []

    def next(self, messages):
        self.calls.append(messages)
        assert self.query in messages[-1]["content"] if len(self.calls) == 1 else True
        assert "PLAN REFERENCE INDEX" in messages[0]["content"]
        assert "Only when the user explicitly asks" in messages[0]["content"]
        assert "Task ID: 66" not in messages[0]["content"]
        if len(self.calls) == 1 and self.operation:
            params = {"operation": self.operation, "plan_id": 34}
            if self.operation == "execute_task":
                params["target_task_ids"] = [66]
            return {
                "thinking": "Select the requested Plan operation from context.",
                "action": {"tool": "plan_operation", "params": params},
                "final_answer": None,
            }
        return {
            "thinking": "Answer the current request.",
            "action": None,
            "final_answer": {
                "answer": (
                    "Task #66 当前失败；这次只读取了状态，没有启动执行。"
                    if self.operation == "get"
                    else "解聚酶可以降解多糖；这里是在讨论概念。"
                ),
                "confidence": 0.9,
            },
        }

    async def stream_chat_async(self, **kwargs):
        yield json.dumps(self.next(kwargs["messages"]))

    async def chat_async(self, **kwargs):
        return "Completed the selected operation."


class NativeProvider(ScriptedProvider):
    async def stream_chat_with_tools_async(self, **kwargs):
        value = self.next(kwargs["messages"])
        action = value["action"]
        call = (
            NativeToolCall(f"op_{len(self.calls)}", action["tool"], action["params"])
            if action
            else NativeToolCall(f"final_{len(self.calls)}", "submit_final_answer", value["final_answer"])
        )
        return NativeStreamResult(content=value["thinking"], tool_calls=[call], finish_reason="tool_calls")


@pytest.mark.parametrize("native", [True, False])
@pytest.mark.parametrize("query", PHRASES)
def test_paraphrased_execution_requires_model_call_and_only_submits_background(monkeypatch, native, query):
    _patch_runtime(monkeypatch)
    agent = _build_agent()
    agent.extra_context["owner_id"] = "alice"
    provider = (NativeProvider if native else ScriptedProvider)(query, "execute_task")
    submitted = []

    async def handler(name, tool_context=None, **params):
        assert name == "plan_operation" and params["target_task_ids"] == [66]
        submitted.append(params)
        return {
            "success": True,
            "operation": "execute_task",
            "plan_id": 34,
            "background_submitted": True,
            "job_id": "one",
        }

    monkeypatch.setattr("app.routers.chat.agent.execute_tool", handler)

    class Agent(DeepThinkAgent):
        def __init__(self, request_profile=None, **kwargs):
            super().__init__(**{**kwargs, "llm_client": provider, "request_profile": request_profile})

    monkeypatch.setattr("app.routers.chat_routes.DeepThinkAgent", Agent)
    monkeypatch.setattr(
        "app.routers.chat.agent.resolve_plan_execution_target", lambda *a: pytest.fail("regex shortcut ran")
    )
    events = _run(agent, query)
    assert len(provider.calls) == 1 and len(submitted) == 1
    assert events[-1]["type"] == "final" and "one" in events[-1]["payload"]["response"]
    assert agent.extra_context.get("current_task_id") is None


@pytest.mark.parametrize("native", [True, False])
@pytest.mark.parametrize("query", READ_REQUESTS)
def test_queries_negation_and_quoted_commands_do_not_force_model_actions(monkeypatch, native, query):
    _patch_runtime(monkeypatch)
    agent = _build_agent()
    operation = "get" if query != "介绍一下解聚酶" else None
    provider = (NativeProvider if native else ScriptedProvider)(query, operation)
    operations = []

    async def handler(name, tool_context=None, **params):
        operations.append(params["operation"])
        assert name == "plan_operation" and params["operation"] == "get"
        return {"success": True, "operation": "get", "plan_id": 34, "task_tree": [{"id": 66, "status": "failed"}]}

    monkeypatch.setattr("app.routers.chat.agent.execute_tool", handler)

    class Agent(DeepThinkAgent):
        def __init__(self, request_profile=None, **kwargs):
            super().__init__(**{**kwargs, "llm_client": provider, "request_profile": request_profile})

    monkeypatch.setattr("app.routers.chat_routes.DeepThinkAgent", Agent)
    monkeypatch.setattr(
        "app.routers.chat.agent.task_status_question", lambda *a: pytest.fail("status phrase shortcut ran")
    )
    events = _run(agent, query)
    assert operations == (["get"] if operation else [])
    assert len(provider.calls) == (2 if operation else 1)
    assert events[-1]["type"] == "final" and "background_plan_job_id" not in events[-1]["payload"].get("metadata", {})


def test_structured_pipeline_does_not_rewrite_model_reply_into_execution(monkeypatch):
    agent = _build_agent()
    seen = []
    response = LLMStructuredResponse.model_validate(
        {"llm_reply": {"message": "目标有歧义，请指定阶段。"}, "actions": []}
    )

    async def invoke(message):
        seen.append(message)
        return response

    agent._invoke_llm = invoke
    for method in (
        "_build_deterministic_execute_task_structured",
        "_apply_plan_first_guardrail",
        "_apply_task_execution_followthrough_guardrail",
    ):
        monkeypatch.setattr(agent, method, lambda *a: pytest.fail("phrase-based action rewrite ran"))
    result = asyncio.run(agent.get_structured_response("请把刚才那个继续推进"))
    assert seen == ["请把刚才那个继续推进"] and result.actions == []


def test_rollback_restores_legacy_context_policy(monkeypatch):
    from app.services.foundation.settings import get_settings

    agent = _build_agent()
    decision, _ = agent._resolve_request_routing("task66修好了吗？")
    agent._update_routing_context(decision)
    assert agent.extra_context["action_selection"] == "model"
    monkeypatch.setattr(get_settings(), "chat_model_action_selection_enabled", False)
    legacy, _ = agent._resolve_request_routing("执行Task66")
    agent._update_routing_context(legacy)
    assert MODEL_SELECTION not in legacy.route_reason_codes
    assert "action_selection" not in agent.extra_context
    assert "model_plan_index" not in agent.extra_context
    assert agent.extra_context["current_task_id"] == 66


@pytest.mark.parametrize("native", [True, False])
def test_ordinary_file_work_stays_in_chat_and_preserves_tool_receipt(monkeypatch, native):
    _patch_runtime(monkeypatch)
    agent = _build_agent()
    operations = []

    class Files(ScriptedProvider):
        def next(self, messages):
            self.calls.append(messages)
            if len(self.calls) == 1:
                return {
                    "thinking": "Read the requested local file in chat.",
                    "action": {"tool": "file_operations", "params": {"operation": "read", "path": "note.md"}},
                    "final_answer": None,
                }
            assert any("file contents" in str(message["content"]) for message in messages)
            return {
                "thinking": "Summarize the file.",
                "action": None,
                "final_answer": {"answer": "文件内容已读取。", "confidence": 0.9},
            }

    class NativeFiles(Files, NativeProvider):
        pass

    provider = (NativeFiles if native else Files)("阅读note.md", None)

    async def handle(action):
        operations.append(action.name)
        assert action.name == "file_operations"
        return AgentStep(
            action=action,
            success=True,
            message="read",
            details={"result": {"success": True, "content": "file contents"}},
        )

    agent._handle_tool_action = handle

    class Agent(DeepThinkAgent):
        def __init__(self, request_profile=None, **kwargs):
            super().__init__(**{**kwargs, "llm_client": provider, "request_profile": request_profile})

    monkeypatch.setattr("app.routers.chat_routes.DeepThinkAgent", Agent)
    events = _run(agent, "阅读note.md")
    assert operations == ["file_operations"] and len(provider.calls) == 2
    assert events[-1]["type"] == "final"
    assert "background" not in events[-1]["payload"]["metadata"].get("termination_reason", "")


def test_quoted_artifact_error_does_not_require_a_plan_rubric_review():
    query = ("Execution failed. Artifact preflight failed for plan #187: Task #16 requires artifact alias "
             "'phage_depoly.review_outline_md', but no in-plan producer or canonical manifest entry was found. "
             "但是这个不是缺少一个嘛？")
    decision = resolve_model_routing(message=query, plan_id=187, current_task_id=16)
    assert not decision.plan_review_required and not decision.plan_execute_required
    assert decision.route_reason_codes == [MODEL_SELECTION]


def test_legacy_rollout_also_does_not_force_review_from_a_filename(monkeypatch):
    from app.services.foundation.settings import get_settings
    monkeypatch.setattr(get_settings(),'chat_model_action_selection_enabled',False)
    agent=_build_agent()
    decision,_=agent._resolve_request_routing("Artifact preflight failed: requires 'phage_depoly.review_outline_md'. 不是有文件吗？")
    assert not decision.plan_review_required and 'plan_review' not in decision.route_reason_codes
    assert not decision.plan_optimize_required


def test_chat_fallback_cannot_execute_the_highlighted_task_inline():
    agent=_build_agent(extra_context={'chat_plan_execution_background_only':True})
    assert _build_deep_think_task_context(agent,user_message='这个不是有文件吗？') is None
