from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.services.deep_think.acceptance import (
    acceptance_missing,
    prepare_acceptance_spec,
)
from app.services.deep_think.guards import _extract_guard_candidates, _verify_guard_path
from app.services.plans.output_spec import (
    capture_output_inputs,
    output_spec_from_metadata,
    parse_output_spec,
    seed_task_output_snapshot,
    validate_output_spec,
)
from app.services.plans.plan_models import PlanNode, PlanTree
from app.services.plans.status_resolver import PlanStatusResolver
from app.services.plans.task_verification import TaskVerificationService


def _agent():
    return SimpleNamespace(
        request_profile={},
        _request_tier=lambda: "execute",
        llm_client=SimpleNamespace(),
    )


def _check_both(spec, paths, root, snapshot=None):
    context = {
        "output_spec": spec.to_dict(),
        "output_spec_base_dir": str(root),
        "output_input_snapshot": snapshot or {},
    }
    agent = _agent()
    asyncio.run(
        prepare_acceptance_spec(agent, "Execute the supplied contract", context)
    )
    missing = acceptance_missing(agent, [], [str(path) for path in paths])
    node = PlanNode(id=1, plan_id=1, name="Produce outputs", metadata=context)
    result = TaskVerificationService().finalize_payload(
        node,
        {
            "status": "completed",
            "metadata": {"artifact_paths": [str(path) for path in paths]},
        },
    )
    assert bool(missing) is (result.final_status == "failed")
    assert (
        agent._output_verification["matched_counts"]
        == result.payload["metadata"]["output_verification"]["matched_counts"]
    )
    assert result.payload["metadata"]["output_spec"]["schema_version"] == 1
    return result, agent


@pytest.mark.parametrize(
    "actual_name, passed",
    [("report.md", False), ("other/report.pdf", False), ("report.pdf", True)],
)
def test_same_exact_format_and_path_contract_in_both_lanes(
    tmp_path, actual_name, passed
):
    actual = tmp_path / actual_name
    actual.parent.mkdir(parents=True, exist_ok=True)
    if actual.suffix == ".pdf":
        from pypdf import PdfWriter

        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.write(actual)
    else:
        actual.write_text("Fixture bytes", encoding="utf-8")
    spec = parse_output_spec(
        {
            "required_outputs": [
                {
                    "kind": "document",
                    "extensions": [".pdf"],
                    "target_path": "report.pdf",
                }
            ]
        }
    )
    result, _ = _check_both(spec, [actual], tmp_path)
    assert (result.final_status == "completed") is passed


def test_counts_require_distinct_real_files_across_overlapping_outputs(tmp_path):
    first, second = tmp_path / "a.png", tmp_path / "b.png"
    from PIL import Image

    Image.new("RGB", (2, 2), "red").save(first)
    Image.new("RGB", (2, 2), "blue").save(second)
    spec = parse_output_spec(
        {
            "required_outputs": [
                {"kind": "image", "extensions": [".png"]},
                {"kind": "image", "extensions": [".png"]},
            ]
        }
    )
    result, _ = _check_both(spec, [first, first], tmp_path)
    assert result.final_status == "failed"
    result, _ = _check_both(spec, [first, second], tmp_path)
    assert result.final_status == "completed"


@pytest.mark.parametrize("changed, passed", [(False, False), (True, True)])
def test_in_place_requires_content_change_from_input_snapshot(
    tmp_path, changed, passed
):
    target = tmp_path / "config.json"
    target.write_text('{"value": 1}', encoding="utf-8")
    spec = parse_output_spec(
        {
            "required_outputs": [
                {
                    "kind": "data",
                    "extensions": [".json"],
                    "target_path": "config.json",
                    "in_place": True,
                }
            ]
        }
    )
    snapshot = capture_output_inputs(spec, tmp_path)
    if changed:
        target.write_text('{"value": 2}', encoding="utf-8")
    result, _ = _check_both(spec, [target], tmp_path, snapshot)
    assert (result.final_status == "completed") is passed


def test_in_place_missing_snapshot_does_not_claim_overwrite(tmp_path):
    target = tmp_path / "config.json"
    target.write_text('{"value": 2}', encoding="utf-8")
    spec = parse_output_spec(
        {
            "required_outputs": [
                {"kind": "data", "target_path": "config.json", "in_place": True}
            ]
        }
    )
    result, _ = _check_both(spec, [target], tmp_path)
    assert result.final_status == "failed"
    assert "snapshot" in result.verification["failures"][0]["message"]


def test_input_snapshot_is_taken_before_execution_and_serializes(tmp_path):
    target = tmp_path / "config.json"
    target.write_text('{"value": 1}', encoding="utf-8")
    node = PlanNode(
        id=1,
        plan_id=1,
        name="Modify config",
        metadata={
            "v2_spec": {
                "required_outputs": [
                    {"kind": "data", "target_path": "config.json", "in_place": True}
                ]
            }
        },
    )
    seed_task_output_snapshot(node, tmp_path)
    before = node.metadata["output_input_snapshot"]
    restored = json.loads(json.dumps(node.metadata))
    target.write_text('{"value": 2}', encoding="utf-8")
    spec = output_spec_from_metadata(restored)
    assert (
        validate_output_spec(
            spec, [str(target)], base_dir=tmp_path, input_snapshot=before
        )["status"]
        == "passed"
    )


@pytest.mark.parametrize(
    "source, blocking", [("inferred_text", True), ("explicit", False)]
)
def test_inferred_and_opted_out_file_declarations_remain_advisory(
    tmp_path, source, blocking
):
    output = tmp_path / "report.md"
    output.write_text("notes", encoding="utf-8")
    spec = parse_output_spec(
        {
            "source": source,
            "blocking": blocking,
            "required_outputs": [{"kind": "document", "extensions": [".pdf"]}],
        }
    )
    result, _ = _check_both(spec, [output], tmp_path)
    assert result.final_status == "completed"
    assert result.verification["authoritative"] is False


def test_structured_legacy_value_checks_share_the_envelope_without_llm_grading(
    tmp_path,
):
    output = tmp_path / "stats.json"
    output.write_text('{"total": 5}', encoding="utf-8")
    criteria = {
        "blocking": True,
        "checks": [
            {
                "type": "json_field_equals",
                "path": str(output),
                "key_path": "total",
                "expected": 100,
            }
        ],
    }
    metadata = {"acceptance_criteria": criteria, "output_spec_base_dir": str(tmp_path)}
    agent = _agent()
    spec = asyncio.run(
        prepare_acceptance_spec(agent, "Execute the supplied contract", metadata)
    )
    assert spec.acceptance_criteria == criteria
    assert acceptance_missing(agent, [], [str(output)])
    node = PlanNode(
        id=1, plan_id=1, name="Verify stats", metadata={"output_spec": spec.to_dict()}
    )
    result = TaskVerificationService().finalize_payload(
        node, {"status": "completed", "metadata": {"artifact_paths": [str(output)]}}
    )
    assert result.final_status == "failed"
    assert result.verification["failures"][0]["actual"] == 5


def test_explicit_contract_inside_envelope_keeps_manifest_authority(tmp_path):
    output = tmp_path / "scratch.md"
    output.write_text("notes", encoding="utf-8")
    node = PlanNode(
        id=1,
        plan_id=1,
        name="Publish evidence",
        metadata={
            "output_spec": {
                "schema_version": 1,
                "artifact_contract": {"publishes": ["general.evidence_md"]},
            }
        },
    )
    verifier = TaskVerificationService()
    result = verifier.finalize_payload(
        node, {"status": "completed", "metadata": {"artifact_paths": [str(output)]}}
    )
    result = verifier.apply_artifact_authority(
        1, node, result, manifest={"artifacts": {}}
    )
    assert result.final_status == "failed"
    assert result.payload["metadata"]["artifact_authority"][
        "missing_publish_aliases"
    ] == ["general.evidence_md"]


@pytest.mark.parametrize("extension", [".json", ".parquet"])
def test_supplied_spec_skips_extraction_and_discovers_declared_formats_outside_results(
    tmp_path, monkeypatch, extension
):
    monkeypatch.setenv("DEEP_THINK_ACCEPTANCE_V2_ENABLED", "1")
    target = tmp_path / ("result" + extension)
    target.write_bytes(b"{}" if extension == ".json" else b"result")
    context = {
        "output_spec_base_dir": str(tmp_path),
        "output_spec": {
            "required_outputs": [
                {"kind": "data", "extensions": [extension], "target_path": target.name}
            ]
        },
    }
    agent = _agent()
    asyncio.run(prepare_acceptance_spec(agent, "Create result" + extension, context))
    candidates = _extract_guard_candidates(
        agent, [{"tool_result": {"success": True, "artifact_paths": [str(target)]}}]
    )
    verified = [_verify_guard_path(agent, candidate) for candidate in candidates]
    assert acceptance_missing(agent, [], verified) == []


def test_simple_turn_does_not_trigger_extraction(monkeypatch):
    monkeypatch.setenv("DEEP_THINK_ACCEPTANCE_V2_ENABLED", "1")
    assert (
        asyncio.run(prepare_acceptance_spec(_agent(), "What does this word mean?"))
        is None
    )


@pytest.mark.parametrize("extension", [".json", ".parquet"])
def test_file_intent_beyond_v1_kinds_can_extract_spec(monkeypatch, extension):
    monkeypatch.setenv("DEEP_THINK_ACCEPTANCE_V2_ENABLED", "1")
    calls = []

    async def stream_chat_async(*args, **kwargs):
        calls.append(args)
        yield json.dumps(
            {"required_outputs": [{"kind": "data", "extensions": [extension]}]}
        )

    agent = _agent()
    agent.llm_client = SimpleNamespace(stream_chat_async=stream_chat_async)
    spec = asyncio.run(prepare_acceptance_spec(agent, "Create config" + extension))
    assert spec.required_outputs[0].extensions == [extension]
    assert len(calls) == 1


def test_deadline_during_extraction_is_not_swallowed(monkeypatch):
    from app.services.run_budget import RunDeadlineExceeded

    monkeypatch.setenv("DEEP_THINK_ACCEPTANCE_V2_ENABLED", "1")

    async def stream_chat_async(*args, **kwargs):
        raise RunDeadlineExceeded("deadline")
        yield "unreachable"

    agent = _agent()
    agent.llm_client = SimpleNamespace(stream_chat_async=stream_chat_async)
    with pytest.raises(RunDeadlineExceeded):
        asyncio.run(prepare_acceptance_spec(agent, "Create config.json"))


@pytest.mark.parametrize("extension", [".pdf", ".png", ".json", ".xlsx", ".docx"])
def test_precisely_declared_formats_reject_renamed_junk_in_both_lanes(
    tmp_path, extension
):
    target = tmp_path / ("result" + extension)
    target.write_text("Not a valid output format", encoding="utf-8")
    spec = parse_output_spec(
        {"required_outputs": [{"kind": "other", "extensions": [extension]}]}
    )
    result, _ = _check_both(spec, [target], tmp_path)
    assert result.final_status == "failed"
    assert result.verification["failures"][0]["failure_kind"] == "invalid_format"


def test_readable_empty_json_does_not_invent_row_requirements(tmp_path):
    target = tmp_path / "empty.json"
    target.write_text("{}", encoding="utf-8")
    spec = parse_output_spec(
        {"required_outputs": [{"kind": "data", "extensions": [".json"]}]}
    )
    result, _ = _check_both(spec, [target], tmp_path)
    assert result.final_status == "completed"


def test_legacy_update_of_persisted_criteria_reconciles_canonical_envelope(tmp_path):
    output = tmp_path / "stats.json"
    output.write_text('{"total": 5}', encoding="utf-8")
    old = {
        "blocking": True,
        "checks": [
            {
                "type": "json_field_equals",
                "path": str(output),
                "key_path": "total",
                "expected": 5,
            }
        ],
    }
    new = {
        "blocking": True,
        "checks": [
            {
                "type": "json_field_equals",
                "path": str(output),
                "key_path": "total",
                "expected": 100,
            }
        ],
    }
    metadata = {"acceptance_criteria": old}
    metadata["output_spec"] = output_spec_from_metadata(metadata).to_dict()
    metadata["acceptance_criteria"] = new
    node = PlanNode(id=1, plan_id=1, name="Revised requirement", metadata=metadata)
    result = TaskVerificationService().finalize_payload(
        node, {"status": "completed", "metadata": {"artifact_paths": [str(output)]}}
    )
    assert result.final_status == "failed"
    assert result.payload["metadata"]["output_spec"]["acceptance_criteria"] == new
    assert result.verification["failures"][0]["actual"] == 5


def test_file_snapshot_preserves_deadline_and_cancellation(tmp_path):
    from app.services.cancellation import (
        CancelToken,
        set_cancel_token,
        reset_cancel_token,
    )
    from app.services.plans.output_spec import file_snapshot
    from app.services.run_budget import RunDeadlineExceeded

    target = tmp_path / "config.json"
    target.write_text("{}", encoding="utf-8")
    for reason, exception in [
        ("run_deadline_exceeded", RunDeadlineExceeded),
        ("user_cancel", asyncio.CancelledError),
    ]:
        token = CancelToken()
        token.set(reason)
        handle = set_cancel_token(token)
        try:
            with pytest.raises(exception):
                file_snapshot(target)
        finally:
            reset_cancel_token(handle)


@pytest.mark.parametrize("count", [0, -1, True, 2.5, "2", 10001])
def test_supplied_invalid_counts_never_fall_back_to_weaker_contract(count):
    from app.services.plans.output_spec import InvalidOutputSpec

    with pytest.raises(InvalidOutputSpec, match="min_count"):
        asyncio.run(
            prepare_acceptance_spec(
                _agent(),
                "Create files",
                {
                    "output_spec": {
                        "required_outputs": [{"kind": "data", "min_count": count}]
                    }
                },
            )
        )


def test_large_counts_and_exact_long_paths_are_preserved_without_truncation(tmp_path):
    target_path = "/".join(["nested"] * 40) + "/output.json"
    spec = parse_output_spec(
        {
            "required_outputs": [
                {
                    "kind": "data",
                    "min_count": 20,
                    "target_path": target_path,
                    "extensions": [".json"],
                }
            ]
        },
        strict=True,
    )
    assert spec.required_outputs[0].min_count == 20
    assert spec.required_outputs[0].target_path == target_path
    assert len(target_path) > 200
    report = validate_output_spec(spec, [], base_dir=tmp_path)
    assert report["matched_counts"] == [0]
    assert report["failures"][0]["required_count"] == 20


@pytest.mark.parametrize(
    "raw",
    [
        {"schema_version": 2, "required_outputs": []},
        {"required_outputs": [{"kind": "data", "target_path": "x" * 4097}]},
        {"required_outputs": [{"kind": "data"}] * 65},
    ],
)
def test_supplied_unsupported_specs_raise_clear_errors(raw):
    from app.services.plans.output_spec import InvalidOutputSpec

    with pytest.raises(InvalidOutputSpec, match="invalid_output_spec"):
        output_spec_from_metadata({"output_spec": raw})


@pytest.mark.parametrize("own_contract", [False, True])
def test_parent_with_explicit_file_contract_requires_own_acceptance(
    tmp_path, monkeypatch, own_contract
):
    monkeypatch.chdir(tmp_path)
    metadata = (
        {
            "output_spec": {
                "required_outputs": [
                    {
                        "kind": "document",
                        "extensions": [".pdf"],
                        "target_path": "report.pdf",
                    }
                ]
            }
        }
        if own_contract
        else {}
    )
    parent = PlanNode(
        id=1,
        plan_id=1,
        name="Synthesize report",
        status="completed",
        metadata=metadata,
        execution_result=json.dumps(
            {"status": "completed", "metadata": {"auto_completed_from_children": True}}
        ),
    )
    child = PlanNode(
        id=2,
        plan_id=1,
        name="Collect evidence",
        parent_id=1,
        status="completed",
        execution_result=json.dumps({"status": "completed"}),
    )
    tree = PlanTree(id=1, title="Parent contract", nodes={1: parent, 2: child})
    tree.rebuild_adjacency()
    states = PlanStatusResolver().resolve_plan_states(
        1, tree, manifest={"artifacts": {}}
    )
    assert states[1]["effective_status"] == ("pending" if own_contract else "completed")
    if own_contract:
        assert states[1]["reason_code"] == "parent_acceptance_pending"
        from pypdf import PdfWriter

        target = tmp_path / "report.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.write(target)
        parent.metadata["output_spec_base_dir"] = str(tmp_path)
        result = TaskVerificationService().finalize_payload(
            parent,
            {"status": "completed", "metadata": {"artifact_paths": [str(target)]}},
        )
        parent.execution_result = json.dumps(result.payload)
        assert (
            PlanStatusResolver().resolve_plan_states(
                1, tree, manifest={"artifacts": {}}
            )[1]["effective_status"]
            == "completed"
        )


def test_readonly_probe_inputs_do_not_satisfy_new_output_contract(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "config.json"
    target.write_text("{}", encoding="utf-8")
    context = {
        "output_spec_base_dir": str(tmp_path),
        "output_spec": {
            "required_outputs": [
                {"kind": "data", "extensions": [".json"], "target_path": "config.json"}
            ]
        },
    }
    agent = _agent()
    asyncio.run(prepare_acceptance_spec(agent, "Generate the declared file", context))
    probe = {
        "tool_name": "file_operations",
        "tool_params": {"operation": "read"},
        "tool_result": {
            "success": True,
            "file_path": str(target),
            "artifact_paths": [str(target)],
        },
    }
    assert _extract_guard_candidates(agent, [probe]) == []
    assert acceptance_missing(agent, [], [])
    node = PlanNode(id=1, plan_id=1, name="Produce file", metadata=context)
    result = TaskVerificationService().finalize_payload(
        node,
        {
            "status": "completed",
            "tool_call": {
                "name": "file_operations",
                "parameters": {"operation": "read"},
            },
            "metadata": {"artifact_paths": [str(target)]},
        },
    )
    assert result.final_status == "failed"


@pytest.mark.parametrize("extension", [".parquet", ".tar.gz", ".py"])
def test_explicit_producer_receipts_support_declared_extensions(tmp_path, extension):
    target = tmp_path / ("result" + extension)
    target.write_bytes(b"generated output")
    agent = _agent()
    asyncio.run(
        prepare_acceptance_spec(
            agent,
            "Generate declared output",
            {
                "output_spec_base_dir": str(tmp_path),
                "output_spec": {
                    "required_outputs": [
                        {
                            "kind": "other",
                            "extensions": [extension],
                            "target_path": target.name,
                        }
                    ]
                },
            },
        )
    )
    receipt = {
        "tool_name": "file_operations",
        "tool_params": {"operation": "write"},
        "tool_result": {"success": True, "produced_files": [str(target)]},
    }
    candidates = _extract_guard_candidates(agent, [receipt])
    assert (
        acceptance_missing(
            agent, [], [_verify_guard_path(agent, path) for path in candidates]
        )
        == []
    )


@pytest.mark.parametrize("preserve", [False, True])
def test_resume_preserves_original_input_snapshot_but_fresh_retry_reseeds(
    tmp_path, preserve
):
    target = tmp_path / "config.json"
    target.write_text('{"version": 1}', encoding="utf-8")
    node = PlanNode(
        id=1,
        plan_id=1,
        name="Update file",
        metadata={
            "output_spec": {
                "required_outputs": [
                    {
                        "kind": "data",
                        "extensions": [".json"],
                        "target_path": "config.json",
                        "in_place": True,
                    }
                ]
            }
        },
    )
    seed_task_output_snapshot(node, tmp_path)
    original = json.loads(json.dumps(node.metadata["output_input_snapshot"]))
    target.write_text('{"version": 2}', encoding="utf-8")
    seed_task_output_snapshot(node, tmp_path, preserve_existing=preserve)
    assert (node.metadata["output_input_snapshot"] == original) is preserve
    result = TaskVerificationService().finalize_payload(
        node, {"status": "completed", "metadata": {"artifact_paths": [str(target)]}}
    )
    assert result.final_status == ("completed" if preserve else "failed")


def test_runtime_reconciliation_issue_cannot_complete_from_existing_files(tmp_path):
    target = tmp_path / "partial.json"
    target.write_text("{}", encoding="utf-8")
    node = PlanNode(id=1, plan_id=1, name="Pending reconciliation")
    result = TaskVerificationService().finalize_payload(
        node,
        {
            "status": "completed",
            "metadata": {
                "artifact_paths": [str(target)],
                "execution_issues": [
                    {"code": "step_reconciliation_required", "step_id": "1"}
                ],
            },
        },
    )
    assert result.final_status == "failed"
    assert result.artifact_paths == [str(target)]
    assert result.payload["metadata"]["failure_kind"] == "step_reconciliation_required"


def test_parent_runtime_issue_is_not_hidden_by_completed_children(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    parent = PlanNode(
        id=1,
        plan_id=1,
        name="Parent",
        status="completed",
        execution_result=json.dumps(
            {
                "status": "completed",
                "metadata": {
                    "execution_issues": [{"code": "step_reconciliation_required"}]
                },
            }
        ),
    )
    child = PlanNode(
        id=2,
        plan_id=1,
        name="Child",
        parent_id=1,
        status="completed",
        execution_result=json.dumps({"status": "completed"}),
    )
    tree = PlanTree(id=1, title="Runtime issue", nodes={1: parent, 2: child})
    tree.rebuild_adjacency()
    state = PlanStatusResolver().resolve_plan_states(
        1, tree, manifest={"artifacts": {}}
    )[1]
    assert state["effective_status"] == "failed"
    assert state["reason_code"] == "step_reconciliation_required"


def test_large_declared_counts_do_not_recurse_per_file(tmp_path):
    files = []
    for index in range(1500):
        path = tmp_path / f"item_{index}.csv"
        path.write_text("value\n1\n", encoding="utf-8")
        files.append(str(path))
    spec = parse_output_spec(
        {
            "required_outputs": [
                {"kind": "data", "extensions": [".csv"], "min_count": 1500}
            ]
        },
        strict=True,
    )
    report = validate_output_spec(spec, files, base_dir=tmp_path)
    assert report["status"] == "passed"
    assert report["matched_counts"] == [1500]


def test_overlapping_groups_reassign_prior_owners_instead_of_greedy_failure(tmp_path):
    files = [tmp_path / "a.csv", tmp_path / "b.tsv", tmp_path / "c.txt"]
    for path in files:
        path.write_text("value", encoding="utf-8")
    spec = parse_output_spec(
        {
            "required_outputs": [
                {"kind": "other", "extensions": [".csv", ".tsv"]},
                {"kind": "other", "extensions": [".csv", ".txt"]},
                {"kind": "other", "extensions": [".csv", ".txt"]},
            ]
        },
        strict=True,
    )
    report = validate_output_spec(
        spec, [str(path) for path in files], base_dir=tmp_path
    )
    assert report["status"] == "passed"
    assert report["matched_counts"] == [1, 1, 1]
