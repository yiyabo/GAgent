from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services.conversation_quality.models import ConversationQualityResult
from app.services.conversation_quality.service import ConversationQualityService


class _RepositoryStub:
    def __init__(self) -> None:
        self.created = []
        self.attached = []
        self.claimed = []
        self.completed = []

    def create_pending_evaluation(self, **kwargs):
        self.created.append(kwargs)
        return True

    def attach_feedback_to_latest_pending(self, **kwargs):
        self.attached.append(kwargs)
        return 11

    def get_evaluation(self, evaluation_id):
        return {"id": evaluation_id, "feedback_message_id": 3}

    def claim_evaluation(self, evaluation_id, **kwargs):
        self.claimed.append((evaluation_id, kwargs))
        return {
            "id": evaluation_id,
            "session_id": "session-1",
            "snapshot": {"user_goal": "run analysis"},
            "evaluation_basis": kwargs["evaluation_basis"],
            "feedback_message_id": 3,
        }

    def get_feedback_message(self, row):
        return {"id": 3, "content": "this did not run"}

    def complete_evaluation(self, evaluation_id, **kwargs):
        self.completed.append((evaluation_id, kwargs))

    def record_evaluation_failure(self, evaluation_id, **kwargs):
        raise AssertionError(f"evaluation should not fail: {evaluation_id}, {kwargs}")


class _EvaluatorStub:
    provider = "qwen"
    model = "quality-test"

    async def evaluate(self, **kwargs):
        return (
            ConversationQualityResult.model_validate({
                "satisfaction_level": "negative",
                "confidence": 0.8,
                "feedback_relation": "unresolved_follow_up",
                "evidence": [{
                    "source": "user_follow_up",
                    "quote": "this did not run",
                    "explanation": "explicit correction",
                }],
                "failure_modes": ["tool_not_invoked"],
                "responsible_stages": ["tool_selection"],
            }),
            "conversation_quality_v1",
        )


def test_capture_completed_run_creates_pending_snapshot(monkeypatch) -> None:
    service = ConversationQualityService()
    settings = SimpleNamespace(
        quality_evaluation_enabled=True,
        quality_evaluation_max_snapshot_chars=12000,
        quality_evaluation_observation_hours=24,
    )
    captured = []
    monkeypatch.setattr("app.services.conversation_quality.service.get_settings", lambda: settings)
    monkeypatch.setattr(
        "app.services.conversation_quality.service.build_run_snapshot",
        lambda run_id, max_chars: {"run": {"run_id": run_id}},
    )
    monkeypatch.setattr(
        "app.repository.chat_runs.get_chat_run",
        lambda run_id: {"session_id": "session-1", "owner_id": "owner-1"},
    )
    monkeypatch.setattr(
        "app.services.conversation_quality.service.repository.create_pending_evaluation",
        lambda **kwargs: captured.append(kwargs) or True,
    )

    assert service.capture_completed_run("run-1") is True
    assert captured[0]["target_run_id"] == "run-1"
    assert captured[0]["owner_id"] == "owner-1"
    assert captured[0]["observed_until"] > datetime.now(timezone.utc)


def test_follow_up_evaluates_without_affecting_caller(monkeypatch) -> None:
    repository = _RepositoryStub()
    settings = SimpleNamespace(
        quality_evaluation_enabled=True,
        quality_evaluation_max_attempts=3,
        quality_evaluation_max_concurrency=2,
        quality_evaluation_poll_seconds=300,
    )
    service = ConversationQualityService(evaluator=_EvaluatorStub())
    monkeypatch.setattr("app.services.conversation_quality.service.get_settings", lambda: settings)
    monkeypatch.setattr("app.services.conversation_quality.service.repository", repository)

    asyncio.run(service.handle_follow_up(
        session_id="session-1",
        owner_id="owner-1",
        feedback_message_id=3,
    ))

    assert repository.attached[0]["feedback_message_id"] == 3
    assert repository.claimed[0][1]["evaluation_basis"] == "follow_up_message"
    assert repository.completed[0][1]["status"] == "final"
    assert repository.completed[0][1]["result"]["satisfaction_level"] == "negative"
