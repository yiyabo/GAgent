import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException

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


def test_deep_think_router_fails_fast_on_invalid_json() -> None:
    agent = _build_router_agent(_RouterInvalidJSONLLMStub())

    with pytest.raises(DeepThinkProtocolError, match="valid top-level JSON object"):
        asyncio.run(agent._should_use_deep_think("Plan a complex multi-step study"))


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
    stream_response = asyncio.run(chat_stream(request, BackgroundTasks()))

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


class _DeepThinkToolResultProtocolProbe:
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
    ) -> None:
        _ = (
            llm_client,
            available_tools,
            max_iterations,
            tool_timeout,
            on_thinking,
            on_thinking_delta,
            on_final_delta,
        )
        self._tool_executor = tool_executor

    async def think(self, user_query: str, context: dict | None = None) -> DeepThinkResult:
        _ = (user_query, context)
        await self._tool_executor("web_search", {"query": "strict protocol"})
        return DeepThinkResult(
            final_answer="ok",
            thinking_steps=[],
            total_iterations=1,
            tools_used=[],
            confidence=1.0,
            thinking_summary="ok",
        )


def test_deep_think_tool_wrapper_fails_on_non_dict_tool_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(chat_routes, "DeepThinkAgent", _DeepThinkToolResultProtocolProbe)

    agent = StructuredChatAgent.__new__(StructuredChatAgent)
    agent.plan_session = SimpleNamespace(plan_id=None)
    agent.extra_context = {}
    agent.history = []
    agent.session_id = None
    agent.llm_service = object()

    async def _fake_handle_tool_action(action):  # type: ignore[no-untyped-def]
        return AgentStep(
            action=action,
            success=True,
            message="ok",
            details={"summary": "missing-result-payload"},
        )

    agent._handle_tool_action = _fake_handle_tool_action

    async def _collect_events() -> list[dict]:
        events: list[dict] = []
        async for chunk in agent.process_deep_think_stream("analyze deeply"):
            text = chunk.strip()
            assert text.startswith("data: ")
            payload = json.loads(text[len("data: ") :])
            events.append(payload)
        return events

    events = asyncio.run(_collect_events())
    error_events = [evt for evt in events if evt.get("type") == "error"]
    assert error_events, "Expected DeepThink stream to emit strict protocol error event."
    assert "expected a dict `result` payload" in error_events[0].get("message", "")


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
