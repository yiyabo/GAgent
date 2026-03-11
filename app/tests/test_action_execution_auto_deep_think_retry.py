from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from app.routers.chat import action_execution
from app.routers.chat.models import AgentResult, AgentStep
from app.services.llm.structured_response import LLMAction, LLMReply, LLMStructuredResponse


class _PlanSessionStub:
    def __init__(self, repo: Any = None, plan_id: Optional[int] = None) -> None:
        self.repo = repo
        self.plan_id = plan_id

    def refresh(self) -> None:
        return

    def detach(self) -> None:
        return


class _JobStoreStub:
    def __init__(self) -> None:
        self._job: Optional[SimpleNamespace] = None
        self.mark_success_calls: List[Dict[str, Any]] = []
        self.mark_failure_calls: List[Dict[str, Any]] = []

    def create_job(self, **kwargs):  # type: ignore[no-untyped-def]
        job_id = kwargs.get("job_id") or "job_stub"
        job_type = kwargs.get("job_type") or "chat_action"
        self._job = SimpleNamespace(job_id=job_id, job_type=job_type)
        return self._job

    def get_job(self, _job_id: str):  # type: ignore[no-untyped-def]
        return self._job

    def attach_plan(self, _job_id: str, _plan_id: int) -> None:
        return

    def append_log(  # type: ignore[no-untyped-def]
        self, _job_id: str, _level: str, _message: str, _payload: Optional[Dict[str, Any]] = None
    ) -> None:
        return

    def mark_running(self, _job_id: str) -> None:
        return

    def mark_failure(self, _job_id: str, error: str, **kwargs) -> None:  # type: ignore[no-untyped-def]
        payload = dict(kwargs)
        payload["error"] = error
        self.mark_failure_calls.append(payload)

    def mark_success(self, _job_id: str, **kwargs) -> None:  # type: ignore[no-untyped-def]
        self.mark_success_calls.append(dict(kwargs))

    def get_job_payload(self, _job_id: str) -> Dict[str, Any]:
        return {"status": "running"}


def _build_failed_blocking_result() -> AgentResult:
    failed_step = AgentStep(
        action=LLMAction(
            kind="tool_operation",
            name="bio_tools",
            parameters={"tool_name": "seqkit", "operation": "stats"},
            blocking=True,
            order=1,
        ),
        success=False,
        message="bio tools failed",
        details={"result": {"success": False, "error": "bio tools failed"}},
    )
    return AgentResult(
        reply="initial reply",
        steps=[failed_step],
        suggestions=[],
        primary_intent=None,
        success=False,
        errors=["bio tools failed"],
    )


def _build_failed_review_pack_result() -> AgentResult:
    failed_step = AgentStep(
        action=LLMAction(
            kind="tool_operation",
            name="review_pack_writer",
            parameters={"topic": "Pseudomonas phage"},
            blocking=True,
            order=1,
        ),
        success=False,
        message="review_pack_writer finished execution.",
        details={
            "result": {
                "success": False,
                "error_code": "section_evaluation_failed",
                "partial": True,
                "partial_output_path": "data/review.partial.md",
                "draft": {
                    "quality_gate_passed": False,
                    "failed_sections": ["result"],
                },
            }
        },
    )
    return AgentResult(
        reply="initial reply",
        steps=[failed_step],
        suggestions=[],
        primary_intent=None,
        success=False,
        errors=["review failed"],
    )


def _build_failed_polish_gate_result() -> AgentResult:
    failed_step = AgentStep(
        action=LLMAction(
            kind="tool_operation",
            name="manuscript_writer",
            parameters={"task": "Write a review"},
            blocking=True,
            order=1,
        ),
        success=False,
        message="manuscript_writer finished execution.",
        details={
            "result": {
                "success": False,
                "error_code": "polish_quality_gate_failed",
                "public_release_ready": False,
                "release_state": "blocked",
            }
        },
    )
    return AgentResult(
        reply="initial reply",
        steps=[failed_step],
        suggestions=[],
        primary_intent=None,
        success=False,
        errors=["manuscript blocked"],
    )


def _build_record(run_id: str) -> Dict[str, Any]:
    structured = LLMStructuredResponse(
        llm_reply=LLMReply(message="run"),
        actions=[
            LLMAction(
                kind="tool_operation",
                name="bio_tools",
                parameters={"tool_name": "seqkit", "operation": "stats"},
                blocking=True,
                order=1,
            )
        ],
    )
    return {
        "id": run_id,
        "plan_id": None,
        "session_id": None,
        "mode": "assistant",
        "structured_json": structured.model_dump_json(),
        "context": {"auto_deep_think_retry_on_blocking_failure": True},
        "history": [],
        "user_message": "please run bio tools",
    }


def _patch_common(monkeypatch, *, run_id: str, result: AgentResult):  # type: ignore[no-untyped-def]
    record = _build_record(run_id)
    updates: List[Dict[str, Any]] = []
    job_store = _JobStoreStub()

    class _StructuredAgentStub:
        def __init__(self, **_kwargs) -> None:  # type: ignore[no-untyped-def]
            self.llm_service = object()

        async def execute_structured(self, _structured):  # type: ignore[no-untyped-def]
            return result

    async def _fake_generate_action_analysis(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return "fallback-analysis"

    monkeypatch.setattr(action_execution, "PlanSession", _PlanSessionStub)
    monkeypatch.setattr(action_execution, "get_structured_chat_agent_cls", lambda: _StructuredAgentStub)
    monkeypatch.setattr(action_execution, "fetch_action_run", lambda _id: record if _id == run_id else None)
    monkeypatch.setattr(action_execution, "update_action_run", lambda _id, **kwargs: updates.append(kwargs))
    monkeypatch.setattr(action_execution, "plan_decomposition_jobs", job_store)
    monkeypatch.setattr(action_execution, "set_current_job", lambda _job_id: None)
    monkeypatch.setattr(action_execution, "reset_current_job", lambda _token: None)
    monkeypatch.setattr(action_execution, "_update_message_content_by_tracking", lambda *_a, **_k: None)
    monkeypatch.setattr(action_execution, "_update_message_metadata_by_tracking", lambda *_a, **_k: None)
    monkeypatch.setattr(action_execution, "_set_session_plan_id", lambda *_a, **_k: None)
    monkeypatch.setattr(action_execution, "_generate_action_analysis", _fake_generate_action_analysis)

    return updates, job_store


def test_action_run_auto_deep_think_retry_marks_completed_on_recovery(monkeypatch) -> None:
    run_id = "run_auto_retry_success"
    result = _build_failed_blocking_result()
    updates, job_store = _patch_common(monkeypatch, run_id=run_id, result=result)

    async def _fake_retry(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return {
            "attempted": True,
            "success": True,
            "final_answer": "Recovered by DeepThink retry",
            "iterations": 3,
            "tools_used": ["bio_tools"],
            "confidence": 0.8,
        }

    monkeypatch.setattr(action_execution, "_run_blocking_failure_deep_think_retry_once", _fake_retry)

    asyncio.run(action_execution._execute_action_run(run_id))

    final_update = updates[-1]
    assert final_update["status"] == "completed"
    assert final_update["errors"] == []
    payload = final_update["result"]
    assert payload["success"] is True
    assert payload["initial_result_success"] is False
    assert payload["analysis_source"] == "deep_think_retry"
    assert payload["analysis_text"] == "Recovered by DeepThink retry"
    assert payload["deep_think_retry"]["success"] is True
    assert payload["errors"] == []
    assert job_store.mark_success_calls
    assert not job_store.mark_failure_calls


def test_action_run_auto_deep_think_retry_keeps_failed_status_when_recovery_fails(monkeypatch) -> None:
    run_id = "run_auto_retry_failure"
    result = _build_failed_blocking_result()
    updates, job_store = _patch_common(monkeypatch, run_id=run_id, result=result)

    async def _fake_retry(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return {
            "attempted": True,
            "success": False,
            "error": "still blocked",
        }

    monkeypatch.setattr(action_execution, "_run_blocking_failure_deep_think_retry_once", _fake_retry)

    asyncio.run(action_execution._execute_action_run(run_id))

    final_update = updates[-1]
    assert final_update["status"] == "failed"
    assert "DeepThink retry failed: still blocked" in final_update["errors"]
    payload = final_update["result"]
    assert payload["success"] is False
    assert payload["deep_think_retry"]["success"] is False
    assert payload["errors"] == final_update["errors"]
    assert not job_store.mark_success_calls
    assert job_store.mark_failure_calls


def test_action_run_skips_auto_deep_think_retry_for_review_pack_quality_gate_failure(monkeypatch) -> None:
    run_id = "run_review_pack_no_retry"
    result = _build_failed_review_pack_result()
    updates, job_store = _patch_common(monkeypatch, run_id=run_id, result=result)

    async def _unexpected_retry(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("deep_think_retry should not run for review pack quality gate failures")

    monkeypatch.setattr(action_execution, "_run_blocking_failure_deep_think_retry_once", _unexpected_retry)

    asyncio.run(action_execution._execute_action_run(run_id))

    final_update = updates[-1]
    assert final_update["status"] == "failed"
    payload = final_update["result"]
    assert payload["success"] is False
    assert "deep_think_retry" not in payload
    assert not job_store.mark_success_calls
    assert job_store.mark_failure_calls


def test_action_run_skips_auto_deep_think_retry_for_polish_quality_gate_failure(monkeypatch) -> None:
    run_id = "run_polish_gate_no_retry"
    result = _build_failed_polish_gate_result()
    updates, job_store = _patch_common(monkeypatch, run_id=run_id, result=result)

    async def _unexpected_retry(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("deep_think_retry should not run for polish release gate failures")

    monkeypatch.setattr(action_execution, "_run_blocking_failure_deep_think_retry_once", _unexpected_retry)

    asyncio.run(action_execution._execute_action_run(run_id))

    final_update = updates[-1]
    assert final_update["status"] == "failed"
    payload = final_update["result"]
    assert payload["success"] is False
    assert "deep_think_retry" not in payload
    assert not job_store.mark_success_calls
    assert job_store.mark_failure_calls
