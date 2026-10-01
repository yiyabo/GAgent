"""The executor freezes in-place evidence before either execution backend starts."""
import copy
from types import SimpleNamespace

import pytest

from app.services.plans.plan_executor import ExecutionConfig, PlanExecutor
from app.services.plans.plan_models import PlanNode, PlanTree


@pytest.mark.parametrize("external", [False, True])
def test_task_input_snapshot_is_persisted_before_internal_or_delegate_effect(tmp_path, external):
    target = tmp_path / "config.json"
    target.write_text('{"value": 1}')
    node = PlanNode(id=1, plan_id=1, name="Update configuration", metadata={"output_spec": {
        "required_outputs": [{"kind": "data", "extensions": [".json"],
                              "target_path": "config.json", "in_place": True}],
    }})
    tree = PlanTree(id=1, title="Update", nodes={1: node}, adjacency={None: [1]})
    persisted = []
    executor = PlanExecutor.__new__(PlanExecutor)
    executor._repo = SimpleNamespace(update_task=lambda *args, **kwargs: persisted.append(copy.deepcopy(kwargs)))
    executor._status_resolver = SimpleNamespace(resolve_plan_states=lambda *args, **kwargs: {})
    executor._resolve_dependencies = lambda *args: []
    executor._get_artifact_manifest = lambda *args: {}
    executor._resolve_required_artifacts = lambda *args, **kwargs: ({}, {}, [], {})
    executor._resolve_task_tool_workspace = lambda *args, **kwargs: (None, str(tmp_path))
    executor._should_delegate_plan_task = lambda config: external
    executor._should_use_deep_think = lambda config: True

    def execute(**kwargs):
        snapshot = persisted[-1]["metadata"]["output_input_snapshot"][str(target)]
        from app.services.plans.output_spec import file_snapshot
        assert snapshot == file_snapshot(target)
        target.write_text('{"value": 2}')
        assert snapshot != file_snapshot(target)
        return "executed"

    executor._run_task_with_external_delegate = execute
    executor._run_task_with_deep_think = execute
    result = executor._run_task(1, node, tree, ExecutionConfig(session_context={"session_id": "spec-test"}))
    assert result == "executed"


def test_delegate_prompt_uses_the_canonical_precise_output_declaration():
    from app.services.plans.executor_delegate import _DelegateMethods

    lines = _DelegateMethods._build_output_contract_constraints({
        "output_spec_base_dir": "/workspace/task", "output_spec": {
            "required_outputs": [{"kind": "document", "min_count": 3,
                                  "extensions": [".pdf"], "target_path": "report.pdf",
                                  "in_place": True, "constraints": "include evidence"}],
        },
    })
    text = "\n".join(lines)
    assert "/workspace/task" in text and "3 document" in text
    assert "report.pdf" in text and ".pdf" in text
    assert "unchanged input" in text and "include evidence" in text


def test_resource_blocked_task_is_not_marked_as_entered(isolated_app_env, monkeypatch):
    from app.database import get_db, init_db
    from app.repository import chat_runs
    from app.services.chat_run_state import chat_run_claim
    import app.services.plans.plan_executor as module

    init_db()
    with get_db() as conn:
        conn.execute("INSERT INTO chat_sessions(id,owner_id,name) VALUES('gate','owner','gate')")
        conn.commit()
    chat_runs.create_chat_run("gate-source", "gate", "{}", owner_id="owner")
    assert chat_runs.claim_chat_run_lease("gate-source", "claim-gate")
    assert chat_runs.mark_chat_run_started("gate-source", worker_id="claim-gate")
    node = PlanNode(id=1, plan_id=1, name="Blocked task", metadata={})
    tree = PlanTree(id=1, title="Gate", nodes={1: node}, adjacency={None: [1]})
    writes = []
    executor = PlanExecutor.__new__(PlanExecutor)
    executor._repo = SimpleNamespace(update_task=lambda *args, **kwargs: writes.append(kwargs))
    executor._status_resolver = SimpleNamespace(resolve_plan_states=lambda *args, **kwargs: {})
    executor._resolve_dependencies = lambda *args: []
    executor._get_artifact_manifest = lambda *args: {}
    executor._resolve_required_artifacts = lambda *args, **kwargs: ({"resources": ["missing"]}, {}, [], {})
    executor._should_delegate_plan_task = lambda config: False
    executor._block_for_missing_resources = lambda **kwargs: "blocked"
    monkeypatch.setattr(module, "resolve_resources", lambda ids: ({}, ["missing"]))
    handle = chat_run_claim.set(("gate-source", "claim-gate"))
    try:
        assert executor._run_task(1, node, tree, ExecutionConfig()) == "blocked"
        assert "controller_run_id" not in node.metadata
        assert not any("controller_run_id" in entry.get("metadata", {}) for entry in writes)
    finally:
        chat_run_claim.reset(handle)
