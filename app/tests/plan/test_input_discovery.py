import asyncio
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.llm import NativeStreamResult, NativeToolCall
from app.services.deep_think_agent import DeepThinkAgent
from app.services.plans import input_discovery as inputs
from app.services.plans.artifact_preflight import ArtifactPreflightService
from app.services.plans.plan_models import PlanNode, PlanTree
from app.services.plans.task_verification import TaskVerificationService, VerificationFinalization
from app.services.plans.status_resolver import PlanStatusResolver


@pytest.fixture
def case(tmp_path):
    path = tmp_path / "scope-v3.md"
    path.write_text("# Review outline\nScope, chapter themes, evidence needs and exclusion criteria.")
    producer = PlanNode(
        id=15,
        plan_id=187,
        name="Complete outline",
        status="completed",
        metadata={"artifact_contract": {"publishes": ["phage_depolymerase.review_outline_md"]}},
    )
    consumer = PlanNode(
        id=16,
        plan_id=187,
        name="Design search",
        instruction="Use the upstream review outline to design the search.",
        dependencies=[15],
        metadata={"artifact_contract": {"requires": ["phage_depoly.review_outline_md"]}},
    )
    tree = PlanTree(id=187, title="Review", nodes={15: producer, 16: consumer})
    tree.rebuild_adjacency()
    manifest = {
        "artifacts": {
            "phage_depolymerase.review_outline_md": {"path": str(path), "producer_task_id": 15, "validated": True}
        }
    }
    context = {}
    assert inputs.prepare(consumer, tree, manifest, ["phage_depoly.review_outline_md"], context)
    return consumer, tree, manifest, context, path


def select(case):
    node, tree, manifest, ctx, path = case
    inputs.note_read(ctx, "file_operations", {"operation": "read", "path": str(path)}, {"success": True})
    return inputs.bind(
        ctx,
        node,
        manifest,
        {
            "input_alias": "phage_depoly.review_outline_md",
            "candidate_id": "input-1",
            "reason": "The inspected document defines the review chapters and scope needed for search.",
        },
    )


def test_missing_registration_is_deferred_to_inspection_not_selected_by_name(case):
    node, tree, manifest, ctx, path = case
    result = ArtifactPreflightService().validate_plan(187, tree, manifest=manifest)
    assert result.ok and any(i.code == "missing_producer" for i in result.warnings)
    assert ctx["artifact_input_discovery"]["bindings"] == {}
    assert inputs.guard(ctx, "literature_pipeline", {})["executed"] is False
    choice = inputs.bind(
        ctx,
        node,
        manifest,
        {"input_alias": "phage_depoly.review_outline_md", "candidate_id": "input-1", "reason": "Same outline"},
    )
    assert not choice["success"] and inputs.pending(ctx)
    before = deepcopy(node.metadata)
    result = select(case)
    assert result["success"] and result["remaining_inputs"] == []
    assert node.metadata == before and inputs.guard(ctx, "literature_pipeline", {}) is None
    payload = {"status": "completed", "metadata": {}}
    inputs.attach(ctx, payload)
    final = VerificationFinalization(payload=payload, final_status="completed", execution_status="completed")
    final = TaskVerificationService().apply_artifact_authority(187, node, final, manifest=manifest)
    assert final.final_status == "completed"
    node.status = "completed"
    node.execution_result = json.dumps(final.payload)
    state = PlanStatusResolver().resolve_plan_states(187, tree, manifest=manifest)[16]
    assert state["missing_required_aliases"] == []
    assert state["effective_status"] == "completed"


@pytest.mark.parametrize("change", ["bytes", "goal", "scope", "manifest", "missing"])
def test_changed_or_wrong_input_bindings_never_satisfy_authority(case, change):
    node, tree, manifest, ctx, path = case
    record = select(case)["input_artifact_binding"]
    if change == "bytes":
        path.write_text("unrelated replacement")
    if change == "goal":
        node.instruction = "Different task objective"
    if change == "scope":
        record["task_id"] = 99
    if change == "manifest":
        manifest["artifacts"] = {}
    if change == "missing":
        path.unlink()
    assert (
        inputs.existing_bindings(
            node, manifest, {"input_artifact_bindings": {"phage_depoly.review_outline_md": record}}
        )
        == {}
    )


def test_replayed_binding_receipt_restores_only_valid_scoped_state(case):
    node, tree, manifest, ctx, path = case
    receipt = select(case)
    ctx["artifact_input_discovery"]["bindings"] = {}
    inputs.restore_receipt(ctx, node, manifest, receipt)
    assert inputs.pending(ctx) == []
    path.write_text("changed")
    ctx["artifact_input_discovery"]["bindings"] = {}
    inputs.restore_receipt(ctx, node, manifest, receipt)
    assert inputs.pending(ctx) == ["phage_depoly.review_outline_md"]


@pytest.mark.parametrize("native", [True, False])
def test_same_agent_loop_reads_confirms_and_continues_original_task(case, native):
    node, tree, manifest, ctx, path = case
    output = path.parent / "search.md"
    calls = []
    actions = [
        ("file_operations", {"operation": "read", "path": str(path)}),
        (
            "plan_operation",
            {
                "operation": "resolve_input",
                "input_alias": "phage_depoly.review_outline_md",
                "candidate_id": "input-1",
                "reason": "Inspected chapter topics and scope match the task input.",
            },
        ),
        (
            "file_operations",
            {
                "operation": "write",
                "path": str(output),
                "content": "# Search strategy\nUse the confirmed review scope.",
            },
        ),
    ]

    class Strict:
        def next(self):
            index = len(calls)
            calls.append(index)
            return actions[index] if index < len(actions) else None

        async def stream_chat_async(self, **kw):
            item = self.next()
            yield json.dumps(
                {
                    "thinking": "Inspect input or complete the original task.",
                    "action": {"tool": item[0], "params": item[1]} if item else None,
                    "final_answer": None if item else {"answer": f"Created {output}", "confidence": 0.9},
                }
            )

        async def chat_async(self, **kw):
            return f"Created {output}"

    class Native(Strict):
        async def stream_chat_with_tools_async(self, **kw):
            item = self.next()
            return NativeStreamResult(
                content="Inspect input or complete the task.",
                tool_calls=[
                    (
                        NativeToolCall(str(len(calls)), item[0], item[1])
                        if item
                        else NativeToolCall(
                            "final", "submit_final_answer", {"answer": f"Created {output}", "confidence": 0.9}
                        )
                    )
                ],
                finish_reason="tool_calls",
            )

    async def execute(name, params):
        blocked = inputs.guard(ctx, name, params)
        if blocked:
            return blocked
        if name == "plan_operation":
            return inputs.bind(ctx, node, manifest, params)
        if params["operation"] == "read":
            result = {"success": True, "content": Path(params["path"]).read_text()}
            inputs.note_read(ctx, name, params, result)
            return result
        assert not inputs.pending(ctx)
        Path(params["path"]).write_text(params["content"])
        return {"success": True, "produced_files": [str(output)]}

    agent = DeepThinkAgent(
        llm_client=Native() if native else Strict(),
        available_tools=["file_operations", "plan_operation"],
        tool_executor=execute,
        max_iterations=6,
    )
    result = asyncio.run(agent.think(node.instruction + "\n" + inputs.prompt(ctx), context={}))
    assert output.exists() and not inputs.pending(ctx)
    assert len(calls) == 4 and str(output) in result.final_answer


def test_versioned_binding_freezes_input_and_detects_later_source_change(case, monkeypatch):
    from app.services.plans.artifact_versions import Binding, active_binding

    node, tree, manifest, ctx, path = case
    monkeypatch.setattr(
        "app.services.plans.artifact_contracts.canonical_plan_root", lambda *a: path.parent / "versions"
    )
    monkeypatch.setattr(
        "app.services.plans.artifact_contracts.artifact_manifest_path", lambda *a: path.parent / "manifest.json"
    )
    active = Binding(187, 16, None, inputs.definition(node), 0, {}, {})
    token = active_binding.set(active)
    try:
        result = select(case)
        record = result["input_artifact_binding"]
        assert result["success"] and record["path"] != str(path)
        frozen = Path(record["path"]).read_text()
        path.write_text("Changed source after binding")
        assert Path(record["path"]).read_text() == frozen
        assert active.source_inputs["phage_depoly.review_outline_md"]["source"] == str(path)
        assert (
            inputs.existing_bindings(
                node, manifest, {"input_artifact_bindings": {"phage_depoly.review_outline_md": record}}
            )
            == {}
        )
    finally:
        active_binding.reset(token)


def test_current_manifest_version_change_rejects_a_cached_candidate(case, monkeypatch):
    node, tree, manifest, ctx, path = case
    manifest["artifacts"]["phage_depolymerase.review_outline_md"]["artifact_version_id"] = "old"
    ctx["artifact_input_discovery"]["candidates"][0]["source_version_id"] = "old"
    fresh = deepcopy(manifest)
    fresh["schema_version"] = 2
    fresh["artifacts"]["phage_depolymerase.review_outline_md"]["artifact_version_id"] = "new"
    disk = path.parent / "manifest.json"
    disk.write_text(json.dumps(fresh))
    monkeypatch.setattr("app.services.plans.artifact_contracts.artifact_manifest_path", lambda *a: disk)
    ctx["artifact_input_discovery"]["manifest_on_disk"] = True
    assert select(case)["success"] is False


def test_unresolved_input_prevents_publication_even_if_an_output_file_exists(case):
    from unittest.mock import MagicMock
    from app.services.plans.executor_artifacts import _ArtifactMethods

    node, tree, manifest, ctx, path = case
    final = VerificationFinalization(
        payload={"status": "completed", "content": "Claimed done"},
        final_status="completed",
        execution_status="completed",
    )
    executor = MagicMock()
    executor._get_artifact_manifest.return_value = manifest
    executor._promote_workspace_artifacts_to_task_dir.side_effect = lambda **kw: kw["payload"]

    def enrich(**kw):
        assert kw["final_status"] == "skipped"
        return kw["payload"]

    executor._enrich_finalized_payload_with_artifacts.side_effect = enrich
    executor._task_verifier.apply_artifact_authority.side_effect = lambda plan_id, node, value, **kw: value
    value, _ = _ArtifactMethods._materialize_finalization(executor, 187, node, final, session_context=ctx)
    assert value.final_status == "skipped"
    assert value.payload["metadata"]["missing_artifact_aliases"] == ["phage_depoly.review_outline_md"]


def test_no_real_candidate_cannot_enable_discovery(case):
    node, tree, manifest, ctx, path = case
    path.unlink()
    assert not inputs.prepare(node, tree, manifest, ["phage_depoly.review_outline_md"], {})


def test_input_confirmation_is_not_output_evidence(case):
    from app.services.plans.plan_executor import PlanExecutor
    from app.services.plans.output_spec import is_readonly_output_probe

    node, tree, manifest, ctx, path = case
    receipt = select(case)
    payload = {
        "metadata": {"input_artifact_bindings": {"phage_depoly.review_outline_md": receipt["input_artifact_binding"]}}
    }
    assert PlanExecutor._extract_path_like_values(payload) == []
    assert TaskVerificationService()._extract_artifact_paths(payload) == []
    assert PlanExecutor._extract_tool_result_context({"result": receipt}) == {}
    assert is_readonly_output_probe("plan_operation", {"operation": "resolve_input"})
