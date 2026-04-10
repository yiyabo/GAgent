from __future__ import annotations

from types import SimpleNamespace

from app.routers.chat import plan_helpers


def _build_agent(*, owner_id: str = "alice", session_id: str = "session-1") -> SimpleNamespace:
    return SimpleNamespace(
        decomposer_settings=SimpleNamespace(
            auto_on_create=True,
            model="test-model",
            max_depth=2,
            total_node_budget=8,
        ),
        plan_decomposer=SimpleNamespace(),
        extra_context={"owner_id": owner_id},
        session_id=session_id,
        _decomposition_notes=[],
        _decomposition_errors=[],
        _last_decomposition=None,
        _dirty=False,
    )


def test_auto_decompose_plan_async_propagates_owner_and_session(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _start_job(_plan_decomposer, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(job_id="job-1")

    monkeypatch.setattr(plan_helpers, "start_decomposition_job_thread", _start_job)

    agent = _build_agent()
    result = plan_helpers.auto_decompose_plan(
        agent,
        42,
        wait_for_completion=False,
        session_context={"owner_id": "alice", "session_id": "session-1"},
    )

    assert result is not None
    assert result["job"].job_id == "job-1"
    assert captured["owner_id"] == "alice"
    assert captured["session_id"] == "session-1"


def test_auto_decompose_plan_sync_enriches_session_context_from_agent(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def _run_plan(plan_id: int, **kwargs):
        seen["plan_id"] = plan_id
        seen["session_context"] = kwargs.get("session_context")
        return SimpleNamespace(created_tasks=[], stats={})

    monkeypatch.setattr(plan_helpers, "refresh_plan_tree", lambda *_args, **_kwargs: None)

    agent = _build_agent(owner_id="bob", session_id="session-2")
    agent.plan_decomposer = SimpleNamespace(run_plan=_run_plan)
    result = plan_helpers.auto_decompose_plan(
        agent,
        84,
        wait_for_completion=True,
        session_context={"user_message": "make a plan"},
    )

    assert result is not None
    assert seen["plan_id"] == 84
    assert seen["session_context"] == {
        "user_message": "make a plan",
        "owner_id": "bob",
        "session_id": "session-2",
    }
