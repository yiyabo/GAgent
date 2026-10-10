"""Effective-state demotion must respect structured completed payloads (LOCAL_INFRA §117).

``_resolve_effective_task_states`` post-processes the resolver's states and
used to demote any *completed* task whose verification was not ``passed`` as
soon as its report prose contained a token such as "failed" or "error:".
``verification_status: skipped`` is the normal state for tasks without
acceptance criteria, so plan #183's finished tasks (#3/#4/#6, 2026-10-10)
were demoted for sentences like "RCSB PDB query failed (timeout)", every
downstream task was dependency-blocked, and the full-plan job ended failed.

Now a structured ``completed`` payload is authoritative unless verification
actually failed; legacy plain-text results keep the prose heuristic.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Dict

import pytest

from app.routers.plan_routes import effective_state as es
from app.services.plans.plan_models import PlanNode, PlanTree


def _tree(node: PlanNode) -> PlanTree:
    tree = PlanTree(id=183, title="Demotion plan")
    tree.nodes[node.id] = node
    tree.rebuild_adjacency()
    return tree


def _stub_facade(monkeypatch: pytest.MonkeyPatch) -> None:
    """The resolver says 'completed'; only the post-loop under test may change it."""

    def _resolve_plan_states(plan_id, tree, snapshot=None, session_id=None) -> Dict[int, Dict[str, Any]]:
        return {
            tid: {"effective_status": "completed", "status_reason": "Completed.", "reason_code": "completed"}
            for tid in tree.nodes
        }

    facade = SimpleNamespace(
        _build_plan_execution_snapshot=lambda plan_id, **_kwargs: {},
        _plan_status_resolver=SimpleNamespace(resolve_plan_states=_resolve_plan_states),
        _task_verifier=SimpleNamespace(is_manual_acceptance_active=lambda _metadata: False),
    )
    monkeypatch.setattr(es, "_facade", lambda: facade)
    monkeypatch.setattr(es, "_lookup_session_id_for_plan", lambda _plan_id: None)


def _completed_node(content: str, metadata: Dict[str, Any]) -> PlanNode:
    return PlanNode(
        id=3,
        plan_id=183,
        name="2. Literature search & dual screening",
        status="completed",
        execution_result=json.dumps({"status": "completed", "content": content, "metadata": metadata}),
    )


def test_completed_payload_with_skipped_verification_survives_failure_prose(monkeypatch) -> None:
    _stub_facade(monkeypatch)
    node = _completed_node(
        "## Evidence\n| External verification | RCSB PDB query **failed** (timeout) |",
        {"verification_status": "skipped"},
    )

    states = es._resolve_effective_task_states(183, _tree(node))

    assert states[3]["effective_status"] == "completed"
    assert states[3]["reason_code"] == "completed"


def test_completed_payload_without_verification_metadata_survives_failure_prose(monkeypatch) -> None:
    _stub_facade(monkeypatch)
    node = _completed_node("Step 44 failed: ValueError, corrected in step 45.", {})

    states = es._resolve_effective_task_states(183, _tree(node))

    assert states[3]["effective_status"] == "completed"


def test_completed_payload_with_failed_verification_is_still_demoted(monkeypatch) -> None:
    _stub_facade(monkeypatch)
    node = _completed_node(
        "error: deliverable did not pass the quality gate",
        {"verification_status": "failed"},
    )

    states = es._resolve_effective_task_states(183, _tree(node))

    assert states[3]["effective_status"] == "failed"
    assert states[3]["reason_code"] == "retry_or_blocked_failure"


def test_legacy_plain_text_retry_result_is_still_demoted(monkeypatch) -> None:
    _stub_facade(monkeypatch)
    node = PlanNode(
        id=3,
        plan_id=183,
        name="Legacy",
        status="completed",
        execution_result="Upstream timed out; please retry this task.",
    )

    states = es._resolve_effective_task_states(183, _tree(node))

    assert states[3]["effective_status"] == "failed"
    assert states[3]["reason_code"] == "retry_or_blocked_failure"
