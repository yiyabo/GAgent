"""Lock the shared response guard pipeline behind the two agent entry points.

``StructuredChatAgent.handle`` and
``StructuredChatAgent.get_structured_response`` used to carry a verbatim copy of
the same guard chain (registered as D3 in
design/2026-09-24-backend-godfiles-refactor-plan.md §6).  W5b extracts that chain
into one private pipeline method; these tests characterise the chain so the
extraction cannot change any of it:

* the exact step order and count of both entry points, including the two
  PhageScope rewrite calls and the conditional ``_invoke_llm``;
* the guardrail-rejection payloads (PhageScope ``create_plan`` rewrite, both
  completion-claim rejections);
* the tail semantics — ``handle`` hands the guarded response to
  ``execute_structured``, ``get_structured_response`` returns it unchanged.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

from app.routers.chat import agent as agent_module
from app.routers.chat.request_routing import RequestRoutingDecision
from app.routers.chat_routes import StructuredChatAgent
from app.services.llm.structured_response import LLMAction, LLMReply, LLMStructuredResponse
from app.services.plans.plan_models import PlanNode, PlanTree

_REWRITE = "_rewrite_phagescope_dataset_understanding_plan_to_deep_profile"

#: The guard chain both entry points run, in call order.  ``_invoke_llm`` is
#: reachable only between ``_build_deterministic_execute_task_structured`` and the
#: first rewrite (i.e. when the deterministic execute shortcut does not fire), and
#: ``execute_structured`` is appended by ``handle`` alone.
GUARD_PIPELINE_STEPS: List[str] = [
    "_resolve_request_routing",
    "_update_routing_context",
    "_build_deterministic_execute_task_structured",
    _REWRITE,
    "_apply_experiment_fallback",
    "_apply_plan_first_guardrail",
    _REWRITE,
    "_apply_phagescope_fallback",
    "_apply_task_execution_followthrough_guardrail",
    "_apply_completion_claim_guardrail",
]

_LLM_PIPELINE_STEPS: List[str] = [
    "_resolve_request_routing",
    "_update_routing_context",
    "_build_deterministic_execute_task_structured",
    "_invoke_llm",
    *GUARD_PIPELINE_STEPS[3:],
]

_RECORDED_MEMBERS = (
    "_update_routing_context",
    "_build_deterministic_execute_task_structured",
    "_apply_experiment_fallback",
    "_apply_plan_first_guardrail",
    "_apply_phagescope_fallback",
    "_apply_task_execution_followthrough_guardrail",
    "_apply_completion_claim_guardrail",
)

_PLAN_CREATE_ACTIONS = [
    LLMAction(kind="plan_operation", name="create_plan", parameters={"goal": "g"}, order=1)
]


class _DummyRepo:
    def __init__(self, tree: PlanTree) -> None:
        self._tree = tree

    def get_plan_tree(self, plan_id: int) -> PlanTree:
        return self._tree


class _DummyPlanSession:
    def __init__(self, *, plan_id: int, tree: PlanTree) -> None:
        self.plan_id = plan_id
        self.repo = _DummyRepo(tree)


class _ExplodingLLM:
    async def chat_async(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("LLM must not be called on the deterministic shortcut path")


def _plan_tree(task_id: int) -> PlanTree:
    node = PlanNode(id=task_id, plan_id=34, name="", status="pending")
    return PlanTree(id=34, title="plan", nodes={task_id: node}, adjacency={None: [task_id]})


def _build_agent(*, task_id: Optional[int] = 66, session_id: Optional[str] = "sess_d3") -> StructuredChatAgent:
    tree = _plan_tree(task_id or 66)
    agent = StructuredChatAgent.__new__(StructuredChatAgent)
    agent.plan_session = _DummyPlanSession(plan_id=34, tree=tree)
    agent.plan_tree = tree
    agent.extra_context = {"current_task_id": task_id} if task_id is not None else {}
    agent.history = []
    agent.session_id = session_id
    agent.llm_service = _ExplodingLLM()
    return agent


def _chat_decision(message: str) -> RequestRoutingDecision:
    return RequestRoutingDecision(
        request_tier="standard",
        request_route_mode="auto_simple",
        route_reason_codes=[],
        manual_deep_think=False,
        thinking_visibility="hidden",
        effective_user_message=message,
        intent_type="chat",
        subject_resolution={},
        brevity_hint=False,
        explicit_task_ids=[],
        explicit_task_override=False,
        full_plan_execution=False,
    )


def _execute_decision(message: str, task_id: int) -> RequestRoutingDecision:
    return RequestRoutingDecision(
        request_tier="execute",
        request_route_mode="manual_deepthink",
        route_reason_codes=["explicit_task_override"],
        manual_deep_think=False,
        thinking_visibility="progress",
        effective_user_message=message,
        intent_type="execute_task",
        subject_resolution={},
        brevity_hint=False,
        explicit_task_ids=[task_id],
        explicit_task_override=True,
        full_plan_execution=False,
    )


def _recorder(name: str, original: Any, seq: List[str]) -> Any:
    if asyncio.iscoroutinefunction(original):

        async def _async_wrapper(*args: Any, **kwargs: Any) -> Any:
            seq.append(name)
            return await original(*args, **kwargs)

        return _async_wrapper

    def _sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        seq.append(name)
        return original(*args, **kwargs)

    return _sync_wrapper


def _run(
    entry: str,
    *,
    message: str,
    decision: RequestRoutingDecision,
    llm_message: str = "ok",
    llm_actions: Optional[List[LLMAction]] = None,
    task_id: Optional[int] = 66,
) -> SimpleNamespace:
    """Drive one entry point with every pipeline step recorded.

    Returns the recorded step sequence, the PhageScope rewrite calls (observed
    response and kwargs of each), the entry point's return value and, for
    ``handle``, the exact object handed to ``execute_structured``.
    """
    seq: List[str] = []
    rewrite_calls: List[Tuple[LLMStructuredResponse, Dict[str, Any]]] = []
    agent = _build_agent(task_id=task_id)

    def _resolve(_message: str) -> Tuple[RequestRoutingDecision, None]:
        seq.append("_resolve_request_routing")
        return decision, None

    agent._resolve_request_routing = _resolve

    async def _invoke_llm(_message: str) -> LLMStructuredResponse:
        seq.append("_invoke_llm")
        return LLMStructuredResponse(
            llm_reply=LLMReply(message=llm_message),
            actions=list(llm_actions or []),
        )

    agent._invoke_llm = _invoke_llm

    executed: Dict[str, Any] = {}
    if entry == "handle":

        async def _execute_structured(structured: LLMStructuredResponse) -> Any:
            seq.append("execute_structured")
            executed["structured"] = structured
            return "EXECUTED"

        agent.execute_structured = _execute_structured

    for name in _RECORDED_MEMBERS:
        setattr(agent, name, _recorder(name, getattr(agent, name), seq))

    original_rewrite = agent_module._rewrite_phagescope_dataset_understanding_plan_to_deep_profile

    def _spy(structured: LLMStructuredResponse, **kwargs: Any) -> LLMStructuredResponse:
        seq.append(_REWRITE)
        rewrite_calls.append((structured, dict(kwargs)))
        return original_rewrite(structured, **kwargs)

    agent_module._rewrite_phagescope_dataset_understanding_plan_to_deep_profile = _spy
    try:
        returned = asyncio.run(getattr(agent, entry)(message))
    finally:
        agent_module._rewrite_phagescope_dataset_understanding_plan_to_deep_profile = original_rewrite

    return SimpleNamespace(
        seq=seq,
        rewrite_calls=rewrite_calls,
        returned=returned,
        executed=executed.get("structured"),
    )


def _both(message: str, *, decision: Optional[RequestRoutingDecision] = None, **kwargs: Any) -> Tuple[
    SimpleNamespace, SimpleNamespace
]:
    decision = decision or _chat_decision(message)
    gsr = _run("get_structured_response", message=message, decision=decision, **kwargs)
    handle = _run("handle", message=message, decision=decision, **kwargs)
    return gsr, handle


# ---------------------------------------------------------------------------
# step order and count
# ---------------------------------------------------------------------------


def test_both_entry_points_run_the_same_guard_pipeline_in_the_same_order() -> None:
    message = "ping"
    gsr, handle = _both(message)

    assert gsr.seq == _LLM_PIPELINE_STEPS
    assert handle.seq == _LLM_PIPELINE_STEPS + ["execute_structured"]


def test_deterministic_execute_shortcut_skips_the_llm_in_both_entry_points() -> None:
    message = "继续执行 Task 66"
    gsr, handle = _both(message, decision=_execute_decision(message, 66))

    assert "_invoke_llm" not in gsr.seq
    assert "_invoke_llm" not in handle.seq
    assert gsr.seq == GUARD_PIPELINE_STEPS
    assert handle.seq == GUARD_PIPELINE_STEPS + ["execute_structured"]
    assert [action.name for action in gsr.returned.actions] == ["rerun_task"]
    assert gsr.returned.model_dump() == handle.executed.model_dump()


# ---------------------------------------------------------------------------
# the two PhageScope rewrite calls
# ---------------------------------------------------------------------------


def test_phagescope_rewrite_runs_twice_with_the_same_arguments_in_both_entry_points() -> None:
    message = "ping"
    gsr, handle = _both(message, llm_actions=_PLAN_CREATE_ACTIONS)

    for run in (gsr, handle):
        assert [index for index, step in enumerate(run.seq) if step == _REWRITE] == [4, 7]
        assert len(run.rewrite_calls) == 2
        first_kwargs = run.rewrite_calls[0][1]
        second_kwargs = run.rewrite_calls[1][1]
        assert first_kwargs == second_kwargs
        assert first_kwargs["user_message"] == message
        assert first_kwargs["session_id"] == "sess_d3"
        assert first_kwargs["extra_context"] is not None
        # Without a PhageScope dataset reference the rewrite is a pass-through, so
        # both calls observe an equivalent structured response.
        assert run.rewrite_calls[0][0].model_dump() == run.rewrite_calls[1][0].model_dump()


def test_dataset_understanding_rewrite_payload_matches_across_entry_points(tmp_path) -> None:
    data_dir = tmp_path / "phagescope_demo"
    (data_dir / "meta_data").mkdir(parents=True)
    (data_dir / "meta_data" / "phage_meta.tsv").write_text("id\tvalue\n1\t2\n", encoding="utf-8")
    message = f"分析本地 phagescope 数据集的 dataset metadata：{data_dir}"

    gsr, handle = _both(message, llm_actions=_PLAN_CREATE_ACTIONS)

    # The first call rewrites the raw create_plan action; the second call sees the
    # already-rewritten response.
    assert [action.kind for action in gsr.rewrite_calls[0][0].actions] == ["plan_operation"]
    assert [action.name for action in gsr.rewrite_calls[1][0].actions] == ["phagescope_research"]

    assert gsr.returned.model_dump() == handle.executed.model_dump()
    assert [action.name for action in gsr.returned.actions] == ["phagescope_research"]
    action = gsr.returned.actions[0]
    assert action.kind == "tool_operation"
    assert action.blocking is True
    assert action.parameters["action"] == "deep_profile"
    assert action.parameters["data_dir"] == str(data_dir)
    assert action.parameters["top_n"] == 30
    assert action.parameters["session_id"] == "sess_d3"
    assert action.metadata["origin"] == "phagescope_dataset_understanding_guardrail"


# ---------------------------------------------------------------------------
# guardrail rejection payloads
# ---------------------------------------------------------------------------


def test_completion_claim_status_rejection_matches_across_entry_points(tmp_path) -> None:
    missing = tmp_path / "missing" / "result.txt"
    message = "ping"
    claim = f"task completed, file: {missing}"

    gsr, handle = _both(message, llm_message=claim)

    rejected_reply = gsr.returned.llm_reply.message
    assert claim not in rejected_reply
    assert "claimed task completion" in rejected_reply
    assert "`pending`" in rejected_reply
    assert gsr.returned.model_dump() == handle.executed.model_dump()


def test_completion_claim_missing_file_rejection_matches_across_entry_points(tmp_path) -> None:
    missing = tmp_path / "missing" / "result.txt"
    message = "ping"
    claim = f"task completed, file: {missing}"

    gsr, handle = _both(message, task_id=None, llm_message=claim)

    rejected_reply = gsr.returned.llm_reply.message
    assert "cannot be confirmed" in rejected_reply
    assert str(missing) in rejected_reply
    assert gsr.returned.model_dump() == handle.executed.model_dump()


# ---------------------------------------------------------------------------
# tail semantics
# ---------------------------------------------------------------------------


def test_guarded_payload_identity_is_preserved_by_both_entry_points() -> None:
    message = "ping"
    sentinel = LLMStructuredResponse(llm_reply=LLMReply(message="guarded payload"), actions=[])

    for entry in ("get_structured_response", "handle"):
        agent = _build_agent()
        decision = _chat_decision(message)

        def _resolve(_message: str) -> Tuple[RequestRoutingDecision, None]:
            return decision, None

        async def _invoke_llm(_message: str) -> LLMStructuredResponse:
            return LLMStructuredResponse(llm_reply=LLMReply(message="raw"), actions=[])

        def _final_guardrail(_structured: LLMStructuredResponse) -> LLMStructuredResponse:
            return sentinel

        agent._resolve_request_routing = _resolve
        agent._invoke_llm = _invoke_llm
        agent._apply_completion_claim_guardrail = _final_guardrail

        executed: Dict[str, Any] = {}
        if entry == "handle":

            async def _execute_structured(structured: LLMStructuredResponse) -> Any:
                executed["structured"] = structured
                return "EXECUTED"

            agent.execute_structured = _execute_structured

        result = asyncio.run(getattr(agent, entry)(message))
        if entry == "handle":
            assert executed["structured"] is sentinel
            assert result == "EXECUTED"
        else:
            assert result is sentinel


def test_both_entry_points_return_the_same_payload_for_the_same_message() -> None:
    message = "ping"
    gsr, handle = _both(message, llm_actions=_PLAN_CREATE_ACTIONS)

    assert gsr.returned.model_dump() == handle.executed.model_dump()
    assert gsr.returned.llm_reply.message == handle.executed.llm_reply.message
