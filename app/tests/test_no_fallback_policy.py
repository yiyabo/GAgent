import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException
from starlette.requests import Request

from app.routers import chat_routes
from app.execution.assemblers import CompositeAssembler
from app.execution.adversarial_execution_strategy import AdversarialExecutionStrategy
from app.routers.chat_routes import (
    AgentStep,
    ChatRequest,
    StructuredChatAgent,
    chat_message,
    chat_stream,
)
from app.services.deep_think_agent import (
    DeepThinkAgent,
    DeepThinkProtocolError,
    DeepThinkResult,
    ThinkingStep,
)
from app.services.evaluation.llm_evaluator import LLMEvaluator
from app.services.evaluation.meta_evaluator import MetaEvaluator
from app.services.evaluation.expert_evaluator import MultiExpertEvaluator
from app.services.evaluation.phage_evaluator import PhageEvaluator
from app.services.evaluation.adversarial_evaluator import ContentCritic, ContentGenerator
from app.services.plans.plan_executor import (
    ExecutionConfig,
    ExecutionResult,
    ExecutionSummary,
    PlanExecutor,
)
from app.services.plans.plan_models import PlanNode, PlanTree
from app.services.plans.plan_rubric_evaluator import evaluate_plan_rubric


class _AssemblerRepoStub:
    def get_task_info(self, task_id: int):
        return {
            "id": task_id,
            "name": "Composite Step",
            "task_type": "composite",
            "workflow_id": 1,
        }

    def get_children(self, task_id: int):
        return [
            {
                "id": 2,
                "name": "Atomic Child",
                "task_type": "atomic",
            }
        ]

    def get_task_output_content(self, task_id: int) -> str:
        return "atomic output"


class _EmptyLLMServiceStub:
    def chat(self, prompt: str, force_real: bool = True) -> str:
        _ = (prompt, force_real)
        return "   "


def test_composite_assembly_fails_on_empty_llm_output() -> None:
    assembler = CompositeAssembler(
        repo=_AssemblerRepoStub(),
        llm_service=_EmptyLLMServiceStub(),
    )

    with pytest.raises(ValueError, match="empty output"):
        assembler.assemble(
            composite_task_id=1,
            strategy="llm",
            force_real=True,
        )


def test_llm_evaluator_returns_error_result_instead_of_fallback_scores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evaluator = LLMEvaluator(use_cache=False)
    monkeypatch.setattr(
        evaluator,
        "call_llm_with_json_parsing",
        lambda *_args, **_kwargs: None,
    )

    result = evaluator.evaluate_content_intelligent(
        content="This is a sufficiently long content block for evaluator testing.",
        task_context={"name": "no-fallback-eval", "task_type": "atomic"},
        iteration=0,
    )

    assert result.overall_score == 0.0
    assert "no valid structured score payload" in str(
        result.metadata.get("error", "")
    ).lower()


class _FailingPlanSummaryLLMStub:
    def generate(self, prompt: str, config: ExecutionConfig):
        _ = (prompt, config)
        raise RuntimeError("summary llm offline")


def _build_minimal_plan_tree() -> PlanTree:
    root = PlanNode(
        id=1,
        plan_id=1,
        name="Root",
        task_type="root",
        metadata={"is_root": True, "task_type": "root"},
        parent_id=None,
    )
    leaf = PlanNode(
        id=2,
        plan_id=1,
        name="Leaf Task",
        instruction="Do one concrete step.",
        parent_id=1,
    )
    tree = PlanTree(
        id=1,
        title="Test Plan",
        description="Plan for strict no-fallback policy tests.",
        nodes={1: root, 2: leaf},
    )
    tree.rebuild_adjacency()
    return tree


def test_plan_executor_summary_generation_fails_fast_without_text_fallback() -> None:
    executor = PlanExecutor(
        repo=object(),
        llm_service=_FailingPlanSummaryLLMStub(),
    )
    tree = _build_minimal_plan_tree()
    summary = ExecutionSummary(plan_id=1)
    summary.results = [
        ExecutionResult(
            plan_id=1,
            task_id=2,
            status="completed",
            content="Task completed with concrete output.",
        )
    ]

    with pytest.raises(RuntimeError, match="LLM plan summary generation failed"):
        executor._generate_plan_summary(
            plan_id=1,
            tree=tree,
            summary=summary,
            config=ExecutionConfig(),
        )


class _UnavailableRubricClient:
    provider = "qwen"
    model = "qwen-test"
    api_key = None
    url = None


def test_plan_rubric_returns_unavailable_result_without_rule_fallback() -> None:
    tree = _build_minimal_plan_tree()
    result = evaluate_plan_rubric(
        tree,
        evaluator_client=_UnavailableRubricClient(),
    )

    assert result.overall_score == 0.0
    assert result.feedback.get("status") == "evaluation_unavailable"
    assert result.dimension_scores
    assert all(score == 0.0 for score in result.dimension_scores.values())


class _SummaryFailingLLMStub:
    async def chat_async(self, **kwargs):  # type: ignore[override]
        _ = kwargs
        raise RuntimeError("summary backend unavailable")


async def _noop_tool_executor(_name: str, _params: dict):
    return {"ok": True}


def test_deep_think_summary_fails_fast_without_text_fallback() -> None:
    agent = DeepThinkAgent(
        llm_client=_SummaryFailingLLMStub(),
        available_tools=["web_search"],
        tool_executor=_noop_tool_executor,
        max_iterations=1,
    )
    steps = [
        ThinkingStep(
            iteration=1,
            thought="Need to summarize strict-mode behavior.",
            action='{"tool":"web_search","params":{"query":"example"}}',
            action_result="ok",
            self_correction=None,
        )
    ]

    with pytest.raises(DeepThinkProtocolError, match="summary generation failed"):
        asyncio.run(agent._generate_summary(steps, "why strict mode"))


class _RouterInvalidJSONLLMStub:
    async def chat_async(self, *args, **kwargs):  # type: ignore[override]
        _ = (args, kwargs)
        return "not-json"


def _build_router_agent(llm_stub: object) -> StructuredChatAgent:
    agent = StructuredChatAgent.__new__(StructuredChatAgent)
    agent.extra_context = {}
    agent.history = []
    agent.llm_service = llm_stub
    return agent


def test_deep_think_router_removed_no_method() -> None:
    """Verify that _should_use_deep_think no longer exists after unified architecture migration."""
    agent = _build_router_agent(_RouterInvalidJSONLLMStub())
    assert not hasattr(agent, "_should_use_deep_think")


def test_chat_message_fails_fast_without_fallback_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise_binding_error(*_args, **_kwargs):
        raise RuntimeError("binding failed")

    monkeypatch.setattr(chat_routes, "_resolve_plan_binding", _raise_binding_error)

    request = ChatRequest(message="hello")
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(chat_message(request, BackgroundTasks()))

    assert exc_info.value.status_code == 500
    assert "strict mode" in str(exc_info.value.detail)


def test_chat_stream_emits_strict_error_event_without_generic_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise_binding_error(*_args, **_kwargs):
        raise RuntimeError("binding failed")

    monkeypatch.setattr(chat_routes, "_resolve_plan_binding", _raise_binding_error)

    request = ChatRequest(message="hello")
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "root_path": "",
        "query_string": b"",
        "headers": [],
        "client": ("testclient", 50000),
        "server": ("test", 80),
        "state": {},
    }
    raw_request = Request(scope)
    stream_response = asyncio.run(chat_stream(request, BackgroundTasks(), raw_request))

    async def _consume_first_chunk() -> str:
        chunk = await stream_response.body_iterator.__anext__()
        if isinstance(chunk, bytes):
            return chunk.decode("utf-8")
        return str(chunk)

    first_chunk = asyncio.run(_consume_first_chunk()).strip()
    assert first_chunk.startswith("data: ")
    payload = json.loads(first_chunk[len("data: ") :])
    assert payload.get("type") == "error"
    assert payload.get("error_type") == "RuntimeError"
    assert payload.get("strict_mode") is True
    assert "Streaming request failed in strict mode" in payload.get("message", "")
    assert "Please try again" not in payload.get("message", "")


class _DeepThinkProbeBase:
    def pause(self) -> None:
        return

    def resume(self) -> None:
        return

    def skip_step(self) -> None:
        return


class _PlanSessionBindStub:
    def __init__(self, plan_id: int | None = None) -> None:
        self.plan_id = plan_id
        self.bound_ids: list[int] = []

    def bind(self, plan_id: int) -> None:
        self.plan_id = plan_id
        self.bound_ids.append(plan_id)


class _DeepThinkPlanCreateProbe(_DeepThinkProbeBase):
    def __init__(
        self,
        *,
        llm_client,
        available_tools,
        tool_executor,
        max_iterations,
        tool_timeout,
        on_thinking,
        on_thinking_delta,
        on_final_delta,
        on_tool_start=None,
        on_tool_result=None,
        on_artifact=None,
        **_kwargs,
    ) -> None:
        _ = (
            llm_client,
            available_tools,
            max_iterations,
            tool_timeout,
            on_thinking,
            on_thinking_delta,
            on_final_delta,
            on_tool_start,
            on_tool_result,
            on_artifact,
        )
        self._tool_executor = tool_executor

    async def think(self, user_query: str, context: dict | None = None, task_context=None) -> DeepThinkResult:
        _ = (user_query, context)
        await self._tool_executor(
            "plan_operation",
            {
                "operation": "create",
                "title": "Replacement plan",
                "description": "Create a separate plan.",
                "tasks": [
                    {
                        "name": "Task 1",
                        "instruction": "Do the replacement work.",
                    }
                ],
            },
        )
        return DeepThinkResult(
            final_answer="Created a new plan.",
            thinking_steps=[],
            total_iterations=1,
            tools_used=["plan_operation"],
            confidence=1.0,
            thinking_summary="ok",
        )


class _DeepThinkToolResultProtocolProbe(_DeepThinkProbeBase):
    def __init__(
        self,
        *,
        llm_client,
        available_tools,
        tool_executor,
        max_iterations,
        tool_timeout,
        on_thinking,
        on_thinking_delta,
        on_final_delta,
        on_tool_start=None,
        on_tool_result=None,
        on_artifact=None,
        **_kwargs,
    ) -> None:
        _ = (
            llm_client,
            available_tools,
            max_iterations,
            tool_timeout,
            on_thinking,
            on_thinking_delta,
            on_final_delta,
            on_tool_start,
            on_tool_result,
            on_artifact,
        )
        self._tool_executor = tool_executor

    async def think(self, user_query: str, context: dict | None = None, task_context=None) -> DeepThinkResult:
        _ = (user_query, context)
        tool_result = await self._tool_executor("web_search", {"query": "strict protocol"})
        return DeepThinkResult(
            final_answer=json.dumps({"tool_result": tool_result}, ensure_ascii=False),
            thinking_steps=[],
            total_iterations=1,
            tools_used=[],
            confidence=1.0,
            thinking_summary="ok",
        )


class _DeepThinkBioFailThenClaudeProbe(_DeepThinkProbeBase):
    def __init__(
        self,
        *,
        llm_client,
        available_tools,
        tool_executor,
        max_iterations,
        tool_timeout,
        on_thinking,
        on_thinking_delta,
        on_final_delta,
        on_tool_start=None,
        on_tool_result=None,
        on_artifact=None,
        **_kwargs,
    ) -> None:
        _ = (
            llm_client,
            available_tools,
            max_iterations,
            tool_timeout,
            on_thinking,
            on_thinking_delta,
            on_final_delta,
            on_tool_start,
            on_tool_result,
            on_artifact,
        )
        self._tool_executor = tool_executor

    async def think(self, user_query: str, context: dict | None = None, task_context=None) -> DeepThinkResult:
        _ = (user_query, context)
        bio_result = await self._tool_executor(
            "bio_tools",
            {"tool_name": "seqkit", "operation": "stats", "input_file": "/tmp/fake.fa"},
        )
        claude_result = await self._tool_executor(
            "code_executor",
            {"task": "fallback after bio failure"},
        )
        return DeepThinkResult(
            final_answer=json.dumps(
                {"bio_tools": bio_result, "code_executor": claude_result},
                ensure_ascii=False,
            ),
            thinking_steps=[],
            total_iterations=1,
            tools_used=["bio_tools", "code_executor"],
            confidence=1.0,
            thinking_summary="ok",
        )


class _DeepThinkBioRecoverThenClaudeProbe(_DeepThinkProbeBase):
    def __init__(
        self,
        *,
        llm_client,
        available_tools,
        tool_executor,
        max_iterations,
        tool_timeout,
        on_thinking,
        on_thinking_delta,
        on_final_delta,
        on_tool_start=None,
        on_tool_result=None,
        on_artifact=None,
        **_kwargs,
    ) -> None:
        _ = (
            llm_client,
            available_tools,
            max_iterations,
            tool_timeout,
            on_thinking,
            on_thinking_delta,
            on_final_delta,
            on_tool_start,
            on_tool_result,
            on_artifact,
        )
        self._tool_executor = tool_executor

    async def think(self, user_query: str, context: dict | None = None, task_context=None) -> DeepThinkResult:
        _ = (user_query, context)
        first_bio_result = await self._tool_executor(
            "bio_tools",
            {"tool_name": "seqkit", "operation": "stats", "input_file": "/tmp/fake.fa"},
        )
        help_result = await self._tool_executor(
            "bio_tools",
            {"tool_name": "seqkit", "operation": "help"},
        )
        retry_result = await self._tool_executor(
            "bio_tools",
            {"tool_name": "seqkit", "operation": "stats", "input_file": "/tmp/fake.fa"},
        )
        claude_result = await self._tool_executor(
            "code_executor",
            {"task": "fallback after help and retry"},
        )
        return DeepThinkResult(
            final_answer=json.dumps(
                {
                    "bio_first": first_bio_result,
                    "bio_help": help_result,
                    "bio_retry": retry_result,
                    "code_executor": claude_result,
                },
                ensure_ascii=False,
            ),
            thinking_steps=[],
            total_iterations=1,
            tools_used=["bio_tools", "code_executor"],
            confidence=1.0,
            thinking_summary="ok",
        )


class _DeepThinkBioSuccessThenClaudeProbe(_DeepThinkProbeBase):
    def __init__(
        self,
        *,
        llm_client,
        available_tools,
        tool_executor,
        max_iterations,
        tool_timeout,
        on_thinking,
        on_thinking_delta,
        on_final_delta,
        on_tool_start=None,
        on_tool_result=None,
        on_artifact=None,
        **_kwargs,
    ) -> None:
        _ = (
            llm_client,
            available_tools,
            max_iterations,
            tool_timeout,
            on_thinking,
            on_thinking_delta,
            on_final_delta,
            on_tool_start,
            on_tool_result,
            on_artifact,
        )
        self._tool_executor = tool_executor

    async def think(self, user_query: str, context: dict | None = None, task_context=None) -> DeepThinkResult:
        _ = (user_query, context)
        bio_result = await self._tool_executor(
            "bio_tools",
            {"tool_name": "seqkit", "operation": "stats", "input_file": "/tmp/fake.fa"},
        )
        claude_result = await self._tool_executor(
            "code_executor",
            {"task": "regular custom analysis"},
        )
        return DeepThinkResult(
            final_answer=json.dumps(
                {"bio_tools": bio_result, "code_executor": claude_result},
                ensure_ascii=False,
            ),
            thinking_steps=[],
            total_iterations=1,
            tools_used=["bio_tools", "code_executor"],
            confidence=1.0,
            thinking_summary="ok",
        )


class _DeepThinkSequenceFetchFailThenClaudeProbe(_DeepThinkProbeBase):
    def __init__(
        self,
        *,
        llm_client,
        available_tools,
        tool_executor,
        max_iterations,
        tool_timeout,
        on_thinking,
        on_thinking_delta,
        on_final_delta,
        on_tool_start=None,
        on_tool_result=None,
        on_artifact=None,
        **_kwargs,
    ) -> None:
        _ = (
            llm_client,
            available_tools,
            max_iterations,
            tool_timeout,
            on_thinking,
            on_thinking_delta,
            on_final_delta,
            on_tool_start,
            on_tool_result,
            on_artifact,
        )
        self._tool_executor = tool_executor

    async def think(self, user_query: str, context: dict | None = None, task_context=None) -> DeepThinkResult:
        _ = (user_query, context)
        fetch_result = await self._tool_executor(
            "sequence_fetch",
            {"accession": "bad$$id", "database": "nuccore", "format": "fasta"},
        )
        claude_result = await self._tool_executor(
            "code_executor",
            {"task": "fallback download via script"},
        )
        return DeepThinkResult(
            final_answer=json.dumps(
                {"sequence_fetch": fetch_result, "code_executor": claude_result},
                ensure_ascii=False,
            ),
            thinking_steps=[],
            total_iterations=1,
            tools_used=["sequence_fetch", "code_executor"],
            confidence=1.0,
            thinking_summary="ok",
        )


class _DeepThinkSequenceRecoverThenClaudeProbe(_DeepThinkProbeBase):
    def __init__(
        self,
        *,
        llm_client,
        available_tools,
        tool_executor,
        max_iterations,
        tool_timeout,
        on_thinking,
        on_thinking_delta,
        on_final_delta,
        on_tool_start=None,
        on_tool_result=None,
        on_artifact=None,
        **_kwargs,
    ) -> None:
        _ = (
            llm_client,
            available_tools,
            max_iterations,
            tool_timeout,
            on_thinking,
            on_thinking_delta,
            on_final_delta,
            on_tool_start,
            on_tool_result,
            on_artifact,
        )
        self._tool_executor = tool_executor

    async def think(self, user_query: str, context: dict | None = None, task_context=None) -> DeepThinkResult:
        _ = (user_query, context)
        first_fetch = await self._tool_executor(
            "sequence_fetch",
            {"accession": "bad$$id", "database": "nuccore", "format": "fasta"},
        )
        retry_fetch = await self._tool_executor(
            "sequence_fetch",
            {"accession": "NC_001416.1", "database": "nuccore", "format": "fasta"},
        )
        claude_result = await self._tool_executor(
            "code_executor",
            {"task": "analyze downloaded fasta"},
        )
        return DeepThinkResult(
            final_answer=json.dumps(
                {
                    "sequence_fetch_first": first_fetch,
                    "sequence_fetch_retry": retry_fetch,
                    "code_executor": claude_result,
                },
                ensure_ascii=False,
            ),
            thinking_steps=[],
            total_iterations=1,
            tools_used=["sequence_fetch", "code_executor"],
            confidence=1.0,
            thinking_summary="ok",
        )


def _build_deep_think_test_agent() -> StructuredChatAgent:
    agent = StructuredChatAgent.__new__(StructuredChatAgent)
    agent.plan_session = SimpleNamespace(plan_id=None)
    agent.extra_context = {}
    agent.history = []
    agent.session_id = None
    agent.llm_service = object()
    agent.max_history_messages = 20
    return agent


async def _collect_deep_think_events(
    agent: StructuredChatAgent, user_message: str
) -> list[dict]:
    events: list[dict] = []
    async for chunk in agent.process_deep_think_stream(user_message):
        text = chunk.decode("utf-8").strip() if isinstance(chunk, bytes) else str(chunk).strip()
        assert text.startswith("data: ")
        payload = json.loads(text[len("data: ") :])
        events.append(payload)
    return events


def _extract_final_message(events: list[dict]) -> str:
    final_events = [evt for evt in events if evt.get("type") == "final"]
    assert final_events, "Expected a final DeepThink SSE event."
    final_payload = final_events[-1].get("payload") or {}
    llm_reply = final_payload.get("llm_reply") or {}
    message = llm_reply.get("message")
    assert isinstance(message, str) and message.strip()
    return message


def test_deep_think_tool_wrapper_tolerates_non_dict_tool_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(chat_routes, "DeepThinkAgent", _DeepThinkToolResultProtocolProbe)

    agent = _build_deep_think_test_agent()

    async def _fake_handle_tool_action(action):  # type: ignore[no-untyped-def]
        return AgentStep(
            action=action,
            success=True,
            message="ok",
            details={"summary": "missing-result-payload"},
        )

    agent._handle_tool_action = _fake_handle_tool_action

    events = asyncio.run(_collect_deep_think_events(agent, "analyze deeply"))
    error_events = [evt for evt in events if evt.get("type") == "error"]
    assert not error_events, "DeepThink should continue when tool result payload is malformed."
    final_message = _extract_final_message(events)
    parsed = json.loads(final_message)
    tool_result = parsed["tool_result"]
    assert tool_result.get("success") is False
    assert tool_result.get("protocol_warning") is True
    assert tool_result.get("recovery_required") is None


def test_deep_think_create_new_rebinds_existing_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _DeepThinkJobStub:
        def create_job(self, **_kwargs):
            return None

        def mark_running(self, _job_id: str) -> None:
            return

        def register_subscriber(self, _job_id: str, _loop):
            return None

        def register_runtime_controller(self, _job_id: str, _controller) -> bool:
            return False

        def unregister_runtime_controller(self, _job_id: str) -> None:
            return

        def unregister_subscriber(self, _job_id: str, _queue) -> None:
            return

        def mark_success(self, _job_id: str, **_kwargs) -> None:
            return

        def mark_failure(self, _job_id: str, _error: str, **_kwargs) -> None:
            return

        def attach_plan(self, _job_id: str, _plan_id: int) -> None:
            return

    session_updates: list[tuple[str, int]] = []

    async def _fake_execute_tool(name: str, **params):
        assert name == "plan_operation"
        assert params["operation"] == "create"
        return {
            "success": True,
            "operation": "create",
            "plan_id": 99,
            "title": "Replacement plan",
        }

    monkeypatch.setattr(chat_routes, "DeepThinkAgent", _DeepThinkPlanCreateProbe)
    monkeypatch.setattr("app.routers.chat.agent.execute_tool", _fake_execute_tool)
    monkeypatch.setattr("app.routers.chat.agent.plan_decomposition_jobs", _DeepThinkJobStub())
    monkeypatch.setattr("app.routers.chat.agent._persist_runtime_context", lambda _agent: None)
    monkeypatch.setattr("app.routers.chat.agent._save_chat_message", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "app.routers.chat.agent._set_session_plan_id",
        lambda session_id, plan_id, **_kwargs: session_updates.append((session_id, plan_id)),
    )
    monkeypatch.setattr("app.routers.chat.agent.set_current_job", lambda _job_id: None)
    monkeypatch.setattr("app.routers.chat.agent.reset_current_job", lambda _token: None)

    agent = _build_deep_think_test_agent()
    agent.plan_session = _PlanSessionBindStub(plan_id=42)
    agent.session_id = "session-create-new"
    agent.max_history_messages = 20
    agent._dirty = False
    scheduled_reviews: list[int] = []
    agent._refresh_plan_tree = lambda force_reload=False: None
    agent._auto_decompose_plan = lambda *_args, **_kwargs: None
    agent._start_background_created_plan_auto_review = (
        lambda plan_id: scheduled_reviews.append(plan_id) or True
    )

    events = asyncio.run(
        _collect_deep_think_events(agent, "新建一个plan，和刚才那个分开")
    )

    final_events = [evt for evt in events if evt.get("type") == "final"]
    assert final_events, "Expected a final DeepThink SSE event."
    final_metadata = final_events[-1]["payload"]["metadata"]

    assert agent.plan_session.plan_id == 99
    assert agent.plan_session.bound_ids == [99]
    assert final_metadata["plan_id"] == 99
    assert session_updates == [("session-create-new", 99)]
    assert scheduled_reviews == [99]


def test_deep_think_schedules_auto_review_after_create_without_blocking_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _DeepThinkJobStub:
        def create_job(self, **_kwargs):
            return None

        def mark_running(self, _job_id: str) -> None:
            return

        def register_subscriber(self, _job_id: str, _loop):
            return None

        def register_runtime_controller(self, _job_id: str, _controller) -> bool:
            return False

        def unregister_runtime_controller(self, _job_id: str) -> None:
            return

        def unregister_subscriber(self, _job_id: str, _queue) -> None:
            return

        def mark_success(self, _job_id: str, **_kwargs) -> None:
            return

        def mark_failure(self, _job_id: str, _error: str, **_kwargs) -> None:
            return

        def attach_plan(self, _job_id: str, _plan_id: int) -> None:
            return

    tool_calls: list[tuple[str, dict]] = []

    async def _fake_execute_tool(name: str, **params):
        tool_calls.append((name, dict(params)))
        assert name == "plan_operation"
        if params["operation"] == "create":
            return {
                "success": True,
                "operation": "create",
                "plan_id": 67,
                "title": "Scored plan",
            }
        raise AssertionError(f"Unexpected tool call: {params}")

    monkeypatch.setattr(chat_routes, "DeepThinkAgent", _DeepThinkPlanCreateProbe)
    monkeypatch.setattr("app.routers.chat.agent.execute_tool", _fake_execute_tool)
    monkeypatch.setattr("app.routers.chat.agent.plan_decomposition_jobs", _DeepThinkJobStub())
    monkeypatch.setattr("app.routers.chat.agent._persist_runtime_context", lambda _agent: None)
    monkeypatch.setattr("app.routers.chat.agent._save_chat_message", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.routers.chat.agent._set_session_plan_id", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.routers.chat.agent.set_current_job", lambda _job_id: None)
    monkeypatch.setattr("app.routers.chat.agent.reset_current_job", lambda _token: None)

    agent = _build_deep_think_test_agent()
    agent.plan_session = _PlanSessionBindStub(plan_id=None)
    agent.session_id = "session-auto-review"
    agent.max_history_messages = 20
    agent._dirty = False
    scheduled_reviews: list[int] = []

    def _fake_refresh(force_reload=False):  # type: ignore[no-untyped-def]
        _ = force_reload
        agent.plan_tree = SimpleNamespace(title="Scored plan", metadata={})
        return agent.plan_tree

    agent._refresh_plan_tree = _fake_refresh
    agent._auto_decompose_plan = lambda *_args, **_kwargs: None
    agent._start_background_created_plan_auto_review = (
        lambda plan_id: scheduled_reviews.append(plan_id) or True
    )

    events = asyncio.run(_collect_deep_think_events(agent, "做一个plan"))

    final_events = [evt for evt in events if evt.get("type") == "final"]
    assert final_events, "Expected a final DeepThink SSE event."
    final_metadata = final_events[-1]["payload"]["metadata"]

    assert [params["operation"] for _, params in tool_calls] == ["create"]
    assert final_metadata["plan_id"] == 67
    assert "plan_evaluation" not in final_metadata
    assert scheduled_reviews == [67]


def test_deep_think_defers_auto_review_until_decomposition_job_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _DeepThinkJobStub:
        def create_job(self, **_kwargs):
            return None

        def mark_running(self, _job_id: str) -> None:
            return

        def register_subscriber(self, _job_id: str, _loop):
            return None

        def register_runtime_controller(self, _job_id: str, _controller) -> bool:
            return False

        def unregister_runtime_controller(self, _job_id: str) -> None:
            return

        def unregister_subscriber(self, _job_id: str, _queue) -> None:
            return

        def mark_success(self, _job_id: str, **_kwargs) -> None:
            return

        def mark_failure(self, _job_id: str, _error: str, **_kwargs) -> None:
            return

        def attach_plan(self, _job_id: str, _plan_id: int) -> None:
            return

    tool_calls: list[tuple[str, dict]] = []

    async def _fake_execute_tool(name: str, **params):
        tool_calls.append((name, dict(params)))
        assert name == "plan_operation"
        if params["operation"] == "create":
            return {
                "success": True,
                "operation": "create",
                "plan_id": 67,
                "title": "Deferred review plan",
            }
        raise AssertionError(f"Unexpected tool call: {params}")

    monkeypatch.setattr(chat_routes, "DeepThinkAgent", _DeepThinkPlanCreateProbe)
    monkeypatch.setattr("app.routers.chat.agent.execute_tool", _fake_execute_tool)
    monkeypatch.setattr("app.routers.chat.agent.plan_decomposition_jobs", _DeepThinkJobStub())
    monkeypatch.setattr("app.routers.chat.agent._persist_runtime_context", lambda _agent: None)
    monkeypatch.setattr("app.routers.chat.agent._save_chat_message", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.routers.chat.agent._set_session_plan_id", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.routers.chat.agent.set_current_job", lambda _job_id: None)
    monkeypatch.setattr("app.routers.chat.agent.reset_current_job", lambda _token: None)

    agent = _build_deep_think_test_agent()
    agent.plan_session = _PlanSessionBindStub(plan_id=None)
    agent.session_id = "session-auto-review-deferred"
    agent.max_history_messages = 20
    agent._dirty = False
    agent._refresh_plan_tree = (
        lambda force_reload=False: SimpleNamespace(
            title="Deferred review plan",
            metadata={},
        )
    )

    callback_holder: dict[str, object] = {}
    scheduled_reviews: list[int] = []
    deferred_reviews: list[int] = []

    def _fake_auto_decompose(plan_id, **kwargs):  # type: ignore[no-untyped-def]
        assert plan_id == 67
        callback_holder["after_success"] = kwargs.get("after_success")
        return {"job": SimpleNamespace(job_id="job-67")}

    agent._auto_decompose_plan = _fake_auto_decompose
    agent._start_background_created_plan_auto_review = (
        lambda plan_id: scheduled_reviews.append(plan_id) or True
    )
    agent._run_created_plan_auto_review_sync = (
        lambda plan_id: deferred_reviews.append(plan_id) or {"success": True}
    )

    events = asyncio.run(_collect_deep_think_events(agent, "做一个需要自动拆解的plan"))

    final_events = [evt for evt in events if evt.get("type") == "final"]
    assert final_events, "Expected a final DeepThink SSE event."
    final_metadata = final_events[-1]["payload"]["metadata"]

    assert [params["operation"] for _, params in tool_calls] == ["create"]
    assert final_metadata["plan_id"] == 67
    assert "plan_evaluation" not in final_metadata
    assert scheduled_reviews == []
    assert deferred_reviews == []
    after_success = callback_holder.get("after_success")
    assert callable(after_success)
    after_success()
    assert deferred_reviews == [67]


def test_deep_think_blocks_code_executor_until_bio_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(chat_routes, "DeepThinkAgent", _DeepThinkBioFailThenClaudeProbe)
    agent = _build_deep_think_test_agent()
    call_counts = {"code_executor": 0}

    async def _fake_handle_tool_action(action):  # type: ignore[no-untyped-def]
        if action.name == "bio_tools":
            return AgentStep(
                action=action,
                success=False,
                message="bio_tools stats failed",
                details={
                    "result": {
                        "success": False,
                        "error": "bio_tools stats failed",
                    }
                },
            )
        if action.name == "code_executor":
            call_counts["code_executor"] += 1
            return AgentStep(
                action=action,
                success=True,
                message="code_executor executed",
                details={"result": {"success": True}},
            )
        return AgentStep(
            action=action,
            success=True,
            message="ok",
            details={"result": {"success": True}},
        )

    agent._handle_tool_action = _fake_handle_tool_action

    events = asyncio.run(_collect_deep_think_events(agent, "analyze deeply"))
    assert not [evt for evt in events if evt.get("type") == "error"]
    final_message = _extract_final_message(events)
    parsed = json.loads(final_message)
    blocked = parsed["code_executor"]
    assert blocked.get("success") is False
    assert blocked.get("blocked_reason") == "bio_tools_recovery_not_completed"
    assert blocked.get("recovery_required") == "bio_tools help -> retry"
    assert call_counts["code_executor"] == 0


def test_deep_think_blocks_code_executor_after_bio_input_preparation_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(chat_routes, "DeepThinkAgent", _DeepThinkBioFailThenClaudeProbe)
    agent = _build_deep_think_test_agent()
    call_counts = {"code_executor": 0}

    async def _fake_handle_tool_action(action):  # type: ignore[no-untyped-def]
        if action.name == "bio_tools":
            return AgentStep(
                action=action,
                success=False,
                message="invalid sequence text",
                details={
                    "result": {
                        "success": False,
                        "tool": "bio_tools",
                        "operation": "stats",
                        "error": "sequence_text contains unsupported characters",
                        "error_code": "invalid_sequence_text",
                        "error_stage": "input_preparation",
                        "no_claude_fallback": True,
                    }
                },
            )
        if action.name == "code_executor":
            call_counts["code_executor"] += 1
            return AgentStep(
                action=action,
                success=True,
                message="code_executor executed",
                details={"result": {"success": True}},
            )
        return AgentStep(
            action=action,
            success=True,
            message="ok",
            details={"result": {"success": True}},
        )

    agent._handle_tool_action = _fake_handle_tool_action

    events = asyncio.run(_collect_deep_think_events(agent, "analyze deeply"))
    assert not [evt for evt in events if evt.get("type") == "error"]
    final_message = _extract_final_message(events)
    parsed = json.loads(final_message)
    blocked = parsed["code_executor"]
    assert blocked.get("success") is False
    assert blocked.get("blocked_reason") == "bio_tools_input_preparation_failed"
    assert call_counts["code_executor"] == 0


def test_deep_think_allows_code_executor_after_bio_help_and_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(chat_routes, "DeepThinkAgent", _DeepThinkBioRecoverThenClaudeProbe)
    agent = _build_deep_think_test_agent()
    call_counts = {"bio_stats": 0, "code_executor": 0}

    async def _fake_handle_tool_action(action):  # type: ignore[no-untyped-def]
        if action.name == "bio_tools":
            operation = str(action.parameters.get("operation") or "").strip().lower()
            if operation == "help":
                return AgentStep(
                    action=action,
                    success=True,
                    message="help ok",
                    details={"result": {"success": True, "operation": "help"}},
                )
            if operation == "stats":
                call_counts["bio_stats"] += 1
                if call_counts["bio_stats"] == 1:
                    return AgentStep(
                        action=action,
                        success=False,
                        message="initial stats failed",
                        details={"result": {"success": False, "error": "initial fail"}},
                    )
                return AgentStep(
                    action=action,
                    success=True,
                    message="retry stats ok",
                    details={"result": {"success": True, "operation": "stats"}},
                )
        if action.name == "code_executor":
            call_counts["code_executor"] += 1
            return AgentStep(
                action=action,
                success=True,
                message="code_executor executed",
                details={"result": {"success": True}},
            )
        return AgentStep(
            action=action,
            success=True,
            message="ok",
            details={"result": {"success": True}},
        )

    agent._handle_tool_action = _fake_handle_tool_action

    events = asyncio.run(_collect_deep_think_events(agent, "analyze deeply"))
    assert not [evt for evt in events if evt.get("type") == "error"]
    final_message = _extract_final_message(events)
    parsed = json.loads(final_message)
    claude_result = parsed["code_executor"]
    assert claude_result.get("success") is True
    assert "blocked_reason" not in claude_result
    assert call_counts["code_executor"] == 1


def test_deep_think_normal_bio_success_does_not_block_code_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(chat_routes, "DeepThinkAgent", _DeepThinkBioSuccessThenClaudeProbe)
    agent = _build_deep_think_test_agent()
    call_counts = {"code_executor": 0}

    async def _fake_handle_tool_action(action):  # type: ignore[no-untyped-def]
        if action.name == "bio_tools":
            return AgentStep(
                action=action,
                success=True,
                message="bio_tools stats ok",
                details={"result": {"success": True, "operation": "stats"}},
            )
        if action.name == "code_executor":
            call_counts["code_executor"] += 1
            return AgentStep(
                action=action,
                success=True,
                message="code_executor executed",
                details={"result": {"success": True}},
            )
        return AgentStep(
            action=action,
            success=True,
            message="ok",
            details={"result": {"success": True}},
        )

    agent._handle_tool_action = _fake_handle_tool_action

    events = asyncio.run(_collect_deep_think_events(agent, "analyze deeply"))
    assert not [evt for evt in events if evt.get("type") == "error"]
    final_message = _extract_final_message(events)
    parsed = json.loads(final_message)
    claude_result = parsed["code_executor"]
    assert claude_result.get("success") is True
    assert "blocked_reason" not in claude_result
    assert call_counts["code_executor"] == 1


def test_deep_think_allows_code_executor_after_help_and_failed_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(chat_routes, "DeepThinkAgent", _DeepThinkBioRecoverThenClaudeProbe)
    agent = _build_deep_think_test_agent()
    call_counts = {"bio_stats": 0, "code_executor": 0}

    async def _fake_handle_tool_action(action):  # type: ignore[no-untyped-def]
        if action.name == "bio_tools":
            operation = str(action.parameters.get("operation") or "").strip().lower()
            if operation == "help":
                return AgentStep(
                    action=action,
                    success=True,
                    message="help ok",
                    details={"result": {"success": True, "operation": "help"}},
                )
            if operation == "stats":
                call_counts["bio_stats"] += 1
                return AgentStep(
                    action=action,
                    success=False,
                    message=f"stats failed #{call_counts['bio_stats']}",
                    details={"result": {"success": False, "error": "still failing"}},
                )
        if action.name == "code_executor":
            call_counts["code_executor"] += 1
            return AgentStep(
                action=action,
                success=True,
                message="code_executor executed",
                details={"result": {"success": True}},
            )
        return AgentStep(
            action=action,
            success=True,
            message="ok",
            details={"result": {"success": True}},
        )

    agent._handle_tool_action = _fake_handle_tool_action

    events = asyncio.run(_collect_deep_think_events(agent, "analyze deeply"))
    assert not [evt for evt in events if evt.get("type") == "error"]
    final_message = _extract_final_message(events)
    parsed = json.loads(final_message)
    claude_result = parsed["code_executor"]
    assert claude_result.get("success") is True
    assert "blocked_reason" not in claude_result
    assert call_counts["code_executor"] == 1


def test_deep_think_blocks_code_executor_after_sequence_fetch_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(chat_routes, "DeepThinkAgent", _DeepThinkSequenceFetchFailThenClaudeProbe)
    agent = _build_deep_think_test_agent()
    call_counts = {"code_executor": 0}

    async def _fake_handle_tool_action(action):  # type: ignore[no-untyped-def]
        if action.name == "sequence_fetch":
            return AgentStep(
                action=action,
                success=False,
                message="invalid accession",
                details={
                    "result": {
                        "success": False,
                        "tool": "sequence_fetch",
                        "error": "invalid accession",
                        "error_code": "invalid_accession",
                        "error_stage": "input_validation",
                        "no_claude_fallback": True,
                    }
                },
            )
        if action.name == "code_executor":
            call_counts["code_executor"] += 1
            return AgentStep(
                action=action,
                success=True,
                message="code_executor executed",
                details={"result": {"success": True}},
            )
        return AgentStep(
            action=action,
            success=True,
            message="ok",
            details={"result": {"success": True}},
        )

    agent._handle_tool_action = _fake_handle_tool_action

    events = asyncio.run(_collect_deep_think_events(agent, "download accession fasta"))
    assert not [evt for evt in events if evt.get("type") == "error"]
    final_message = _extract_final_message(events)
    parsed = json.loads(final_message)
    blocked = parsed["code_executor"]
    assert blocked.get("success") is False
    assert blocked.get("blocked_reason") == "sequence_fetch_failed_no_fallback"
    assert call_counts["code_executor"] == 0


def test_deep_think_sequence_fetch_success_clears_claude_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(chat_routes, "DeepThinkAgent", _DeepThinkSequenceRecoverThenClaudeProbe)
    agent = _build_deep_think_test_agent()
    call_counts = {"sequence_fetch": 0, "code_executor": 0}

    async def _fake_handle_tool_action(action):  # type: ignore[no-untyped-def]
        if action.name == "sequence_fetch":
            call_counts["sequence_fetch"] += 1
            if call_counts["sequence_fetch"] == 1:
                return AgentStep(
                    action=action,
                    success=False,
                    message="invalid accession",
                    details={
                        "result": {
                            "success": False,
                            "tool": "sequence_fetch",
                            "error": "invalid accession",
                            "error_code": "invalid_accession",
                            "error_stage": "input_validation",
                            "no_claude_fallback": True,
                        }
                    },
                )
            return AgentStep(
                action=action,
                success=True,
                message="downloaded",
                details={
                    "result": {
                        "success": True,
                        "tool": "sequence_fetch",
                        "output_file": "/tmp/nc_001416.fasta",
                        "record_count": 1,
                    }
                },
            )
        if action.name == "code_executor":
            call_counts["code_executor"] += 1
            return AgentStep(
                action=action,
                success=True,
                message="code_executor executed",
                details={"result": {"success": True}},
            )
        return AgentStep(
            action=action,
            success=True,
            message="ok",
            details={"result": {"success": True}},
        )

    agent._handle_tool_action = _fake_handle_tool_action

    events = asyncio.run(_collect_deep_think_events(agent, "download and analyze accession fasta"))
    assert not [evt for evt in events if evt.get("type") == "error"]
    final_message = _extract_final_message(events)
    parsed = json.loads(final_message)
    claude_result = parsed["code_executor"]
    assert claude_result.get("success") is True
    assert "blocked_reason" not in claude_result
    assert call_counts["code_executor"] == 1


def test_meta_evaluator_fails_without_llm_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evaluator = MetaEvaluator()
    monkeypatch.setattr(
        evaluator,
        "call_llm_with_json_parsing",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(RuntimeError, match="meta-evaluation failed"):
        evaluator._llm_meta_evaluate(
            evaluation_history=[{"overall_score": 0.7, "suggestions": []}],
            content="content",
            task_context={"name": "task"},
        )


def test_phage_evaluator_fails_without_llm_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evaluator = PhageEvaluator()
    monkeypatch.setattr(
        evaluator,
        "call_llm_with_json_parsing",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(RuntimeError, match="phage expert evaluation failed"):
        evaluator._llm_phage_evaluate("content", {"name": "task"})


class _ExpertLLMFailureStub:
    def chat(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("llm unavailable")


def test_expert_evaluator_raises_instead_of_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evaluator = MultiExpertEvaluator(use_cache=False)
    evaluator.llm_client = _ExpertLLMFailureStub()
    expert_role = next(iter(evaluator.experts.values()))

    with pytest.raises(RuntimeError, match="Expert evaluation failed"):
        evaluator._evaluate_with_single_expert(
            expert_role=expert_role,
            content="content",
            task_context={"name": "task"},
        )


class _LLMAlwaysFails:
    def chat(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("llm unavailable")


class _LLMInvalidJSON:
    def chat(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        return {"content": "not-json"}


def test_adversarial_generator_improvement_fails_fast_without_fallback() -> None:
    generator = ContentGenerator(_LLMAlwaysFails(), use_cache=False)
    criticisms = [{"issue": "missing evidence", "suggestion": "add references"}]

    with pytest.raises(RuntimeError, match="strict mode"):
        generator.improve_content(
            original_content="draft",
            criticisms=criticisms,
            task_context={"name": "test task"},
        )


def test_adversarial_critic_fails_fast_on_invalid_json() -> None:
    critic = ContentCritic(_LLMInvalidJSON(), use_cache=False)

    with pytest.raises(RuntimeError, match="strict mode"):
        critic.critique_content(
            content="draft content",
            task_context={"name": "test task"},
            iteration=1,
        )


def test_adversarial_strategy_fails_fast_on_iteration_error() -> None:
    strategy = AdversarialExecutionStrategy.__new__(AdversarialExecutionStrategy)
    strategy.base_executor = SimpleNamespace(execute_llm_chat=lambda _prompt: "content")
    strategy.adversarial_evaluator = SimpleNamespace(
        adversarial_evaluate=lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    with pytest.raises(RuntimeError, match="iteration 1 failed"):
        strategy._execute_adversarial_iterative_loop(
            task_id=1,
            task_name="task",
            initial_prompt="prompt",
            task_context={"name": "task"},
            max_iterations=1,
            max_rounds=1,
            quality_threshold=0.8,
            improvement_threshold=0.1,
        )
