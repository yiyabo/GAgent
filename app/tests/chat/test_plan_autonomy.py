from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.routers.chat.request_routing import _has_explicit_plan_optimize_request
from app.services import plan_autonomy


def _agent(metadata: dict[str, Any], *, plan_id: int = 11) -> SimpleNamespace:
    return SimpleNamespace(
        session_id="session-1",
        extra_context={"owner_id": "owner-1"},
        plan_session=SimpleNamespace(plan_id=plan_id),
        _metadata=metadata,
    )


def _wire_metadata(monkeypatch, metadata: dict[str, Any]) -> None:
    monkeypatch.setattr(plan_autonomy, "_read_metadata", lambda _sid, _oid: metadata)

    def update(_sid, updater, _oid):
        updated = updater(dict(metadata))
        metadata.clear()
        metadata.update(updated)

    monkeypatch.setattr(plan_autonomy, "_write_metadata", update)


def test_split_chinese_revision_authorization_is_explicit() -> None:
    assert plan_autonomy.explicit_revision_request("1. 授权修改，加入进去")
    assert plan_autonomy.explicit_revision_request("同意并入这些建议")
    assert not plan_autonomy.explicit_revision_request("同意")
    assert not plan_autonomy.explicit_revision_request("不要并入这些建议")
    assert _has_explicit_plan_optimize_request(
        "1. 授权修改，加入进去", plan_bound=True, task_bound=False
    )


def test_unapproved_optimize_stages_exact_bounded_proposal(monkeypatch) -> None:
    metadata: dict[str, Any] = {}
    _wire_metadata(monkeypatch, metadata)
    agent = _agent(metadata)
    changes = [{"action": "add_task", "description": "补充 NCBI 外部数据"}]
    result = plan_autonomy.revision_denial(
        agent=agent,
        operation="optimize",
        denied={"success": False, "error_code": "plan_edit_not_requested"},
        params={"plan_id": 11, "changes": changes, "reason": "加入外部证据"},
    )
    assert result["error_code"] == "plan_revision_confirmation_required"
    pending = metadata[plan_autonomy.PENDING_REVISION_KEY]
    assert pending["plan_id"] == 11
    assert pending["params"]["changes"] == changes
    assert pending["status"] == "pending"


def test_one_assent_unlocks_only_the_stored_plan_and_changes(monkeypatch) -> None:
    metadata: dict[str, Any] = {}
    _wire_metadata(monkeypatch, metadata)
    agent = _agent(metadata)
    plan_autonomy.stage_revision_proposal(
        session_id=agent.session_id,
        owner_id="owner-1",
        bound_plan_id=11,
        params={"plan_id": 11, "changes": [{"action": "add_task", "description": "x"}]},
    )
    decision = SimpleNamespace(
        effective_user_message="同意",
        plan_create_required=False,
        plan_optimize_required=False,
    )
    assert plan_autonomy.configure_revision_context(agent, decision) is True
    approved = agent.extra_context["approved_plan_revision_proposal"]
    assert approved["plan_id"] == 11
    assert approved["params"]["changes"][0]["description"] == "x"

    other_plan = _agent(metadata, plan_id=12)
    assert plan_autonomy.configure_revision_context(other_plan, decision) is False
    assert "approved_plan_revision_proposal" not in other_plan.extra_context


def test_bare_assent_without_proposal_does_not_unlock_edits(monkeypatch) -> None:
    metadata: dict[str, Any] = {}
    _wire_metadata(monkeypatch, metadata)
    agent = _agent(metadata)
    decision = SimpleNamespace(
        effective_user_message="同意",
        plan_create_required=False,
        plan_optimize_required=False,
    )
    assert plan_autonomy.configure_revision_context(agent, decision) is False


def test_successful_optimize_consumes_proposal_once(monkeypatch) -> None:
    metadata: dict[str, Any] = {}
    _wire_metadata(monkeypatch, metadata)
    agent = _agent(metadata)
    plan_autonomy.stage_revision_proposal(
        session_id=agent.session_id,
        owner_id="owner-1",
        bound_plan_id=11,
        params={"changes": [{"action": "add_task", "description": "x"}]},
    )
    plan_autonomy.consume_agent_revision(
        agent, {"success": True, "operation": "optimize", "plan_id": 11}, {}
    )
    assert plan_autonomy.PENDING_REVISION_KEY not in metadata
    plan_autonomy.consume_agent_revision(
        agent, {"success": True, "operation": "optimize", "plan_id": 11}, {}
    )


def test_failed_or_different_plan_result_keeps_proposal(monkeypatch) -> None:
    metadata: dict[str, Any] = {}
    _wire_metadata(monkeypatch, metadata)
    agent = _agent(metadata)
    plan_autonomy.stage_revision_proposal(
        session_id=agent.session_id,
        owner_id="owner-1",
        bound_plan_id=11,
        params={"changes": [{"action": "add_task", "description": "x"}]},
    )
    plan_autonomy.consume_agent_revision(
        agent, {"success": False, "operation": "optimize", "plan_id": 11}, {}
    )
    assert plan_autonomy.PENDING_REVISION_KEY in metadata
    plan_autonomy.consume_agent_revision(
        agent, {"success": True, "operation": "optimize", "plan_id": 12}, {}
    )
    assert plan_autonomy.PENDING_REVISION_KEY in metadata


def test_non_edit_denial_is_not_relabelled_as_a_proposal(monkeypatch) -> None:
    metadata: dict[str, Any] = {}
    _wire_metadata(monkeypatch, metadata)
    denied = {"success": False, "error_code": "plan_revision_not_allowed"}
    result = plan_autonomy.revision_denial(
        agent=_agent(metadata), operation="optimize", denied=denied, params={"changes": [{}]}
    )
    assert result == denied
    assert metadata == {}


def test_approved_proposal_cannot_be_silently_expanded(monkeypatch) -> None:
    metadata: dict[str, Any] = {}
    _wire_metadata(monkeypatch, metadata)
    agent = _agent(metadata)
    plan_autonomy.stage_revision_proposal(
        session_id=agent.session_id,
        owner_id="owner-1",
        bound_plan_id=11,
        params={"changes": [{"action": "add_task", "description": "原提案"}]},
    )
    decision = SimpleNamespace(
        effective_user_message="同意", plan_create_required=False, plan_optimize_required=False
    )
    assert plan_autonomy.configure_revision_context(agent, decision)
    exact = {"changes": [{"action": "add_task", "description": "原提案"}]}
    assert plan_autonomy.revision_scope_denial(agent=agent, operation="optimize", params=exact) is None
    expanded = {"changes": exact["changes"] + [{"action": "delete_task", "task_id": 3}]}
    denied = plan_autonomy.revision_scope_denial(agent=agent, operation="optimize", params=expanded)
    assert denied is not None
    assert denied["error_code"] == "plan_revision_proposal_mismatch"
