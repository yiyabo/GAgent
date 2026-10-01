from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from app.services.artifacts.events import ArtifactEvent
from app.services.deep_think.acceptance import (
    acceptance_missing,
    prepare_acceptance_spec,
)
from app.services.plans.artifact_contracts import (
    publish_artifact,
    save_artifact_manifest,
)
from app.services.plans.output_spec import (
    output_origin_map,
    parse_output_spec,
    validate_output_spec,
)
from app.services.plans.plan_models import PlanNode
from app.services.plans.task_verification import TaskVerificationService
from app.tests.plan.test_plan_executor_publish_stream import SESSION_ID, _make_projector


def _check_both(spec, paths, project_root, manifest, session_id=None):
    context = {
        "output_spec": spec.to_dict(),
        "output_spec_base_dir": str(project_root),
        "_artifact_manifest": manifest,
    }
    if session_id:
        context["session_id"] = session_id
    agent = SimpleNamespace(
        request_profile={"task_id": 1},
        _request_tier=lambda: "execute",
        llm_client=SimpleNamespace(),
    )
    asyncio.run(prepare_acceptance_spec(agent, "Use supplied contract", context))
    missing = acceptance_missing(agent, [], [str(path) for path in paths])
    node = PlanNode(id=1, plan_id=1, name="Generate figures", metadata=context)
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
    return result, agent._output_verification


def test_real_plan_manifest_counts_original_and_canonical_mirror_once(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "source.md"
    source.write_text("# Source evidence\n", encoding="utf-8")
    manifest = {"plan_id": 1, "artifacts": {}}
    entry = publish_artifact(
        plan_id=1,
        alias="general.evidence_md",
        source_path=str(source),
        producer_task_id=1,
        manifest=manifest,
    )
    save_artifact_manifest(1, manifest)
    mirror = entry["path"]
    assert entry["source_path"] == str(source.resolve())
    assert entry["path"] != entry["source_path"]
    spec = parse_output_spec(
        {
            "required_outputs": [
                {"kind": "document", "extensions": [".md"], "min_count": 2}
            ]
        },
        strict=True,
    )
    result, report = _check_both(spec, [source, mirror], tmp_path, manifest)
    assert result.final_status == "failed"
    assert report["matched_counts"] == [1]
    for target in (source, mirror):
        exact = parse_output_spec(
            {
                "required_outputs": [
                    {
                        "kind": "document",
                        "extensions": [".md"],
                        "target_path": str(target),
                    }
                ]
            },
            strict=True,
        )
        assert (
            _check_both(exact, [source, mirror], tmp_path, manifest)[0].final_status
            == "completed"
        )
    both_exact = parse_output_spec(
        {
            "required_outputs": [
                {"extensions": [".md"], "target_path": str(target)}
                for target in (source, mirror)
            ]
        },
        strict=True,
    )
    assert (
        _check_both(both_exact, [source, mirror], tmp_path, manifest)[0].final_status
        == "failed"
    )


def test_real_deliverable_manifest_deduplicates_mirror_but_not_independent_identical_images(
    tmp_path, monkeypatch
):
    projector, session_dir = _make_projector(tmp_path)
    publisher = projector._publisher
    import app.services.deliverables.publisher as publisher_module

    monkeypatch.setattr(
        publisher_module, "get_deliverable_publisher", lambda: publisher
    )
    monkeypatch.chdir(publisher._project_root)
    sources = [publisher._project_root / "a.png", publisher._project_root / "b.png"]
    for source in sources:
        Image.new("RGB", (2, 2), "red").save(source)
    assert sources[0].read_bytes() == sources[1].read_bytes()
    projector.consume_plan_events(
        session_id=SESSION_ID,
        events=[
            ArtifactEvent(
                session_id=SESSION_ID,
                file_path=str(source),
                file_ext=".png",
                producer_kind="plan_task",
                producer_plan_id=1,
                producer_task_id=1,
                publish_requested=True,
            )
            for source in sources
        ],
        plan_id=1,
        task_id=1,
    )
    document = json.loads(
        (session_dir / "deliverables" / "manifest_latest.json").read_text()
    )
    image_rows = [
        row
        for row in document["items"]
        if row.get("source_path") and Path(row["path"]).suffix == ".png"
    ]
    mirrors = [
        session_dir / "deliverables" / "latest" / row["path"] for row in image_rows
    ]
    assert all(not row["path"].startswith("/") for row in document["items"])
    assert all(not row["source_path"].startswith("/") for row in image_rows)
    spec = parse_output_spec(
        {
            "required_outputs": [
                {"kind": "image", "extensions": [".png"], "min_count": 2}
            ]
        },
        strict=True,
    )
    first_mirror = next(path for path in mirrors if path.name == sources[0].name)
    assert (
        _check_both(
            spec,
            [sources[0], first_mirror],
            publisher._project_root,
            {"artifacts": {}},
            SESSION_ID,
        )[0].final_status
        == "failed"
    )
    result, report = _check_both(
        spec,
        [*sources, *mirrors],
        publisher._project_root,
        {"artifacts": {}},
        SESSION_ID,
    )
    assert result.final_status == "completed"
    assert report["matched_counts"] == [2]


def test_relative_roots_and_derived_formats_are_not_guessed_as_mirrors(tmp_path):
    first, second = tmp_path / "a.csv", tmp_path / "b.csv"
    first.write_text("same bytes")
    second.write_text("same bytes")
    spec = parse_output_spec(
        {
            "required_outputs": [
                {"kind": "data", "extensions": [".csv"], "min_count": 2}
            ]
        },
        strict=True,
    )
    relative = {
        "artifacts": {"output.a_csv": {"path": "b.csv", "source_path": "a.csv"}}
    }
    assert (
        validate_output_spec(
            spec,
            [str(first), str(second)],
            base_dir=tmp_path,
            artifact_manifest=relative,
        )["status"]
        == "passed"
    )
    derived = {
        "items": [{"source_path": str(first), "path": str(tmp_path / "rendered.pdf")}]
    }
    assert output_origin_map(deliverable_manifest=derived) == {}
    roots = {
        "items": [
            {"source_path": "source/chart.png", "path": "image_tabular/chart.png"}
        ]
    }
    assert output_origin_map(deliverable_manifest=roots) == {}
    actual = output_origin_map(
        deliverable_manifest=roots,
        project_root=tmp_path / "project",
        deliverables_root=tmp_path / "session" / "deliverables" / "latest",
    )
    assert actual == {
        str(tmp_path / "session/deliverables/latest/image_tabular/chart.png"): str(
            tmp_path / "project/source/chart.png"
        )
    }


def test_conflicting_alias_sources_do_not_merge_independent_outputs(tmp_path):
    first, second, mirror = [tmp_path / name for name in ("a.csv", "b.csv", "m.csv")]
    for path in (first, second, mirror):
        path.write_text("same bytes")
    manifest = {
        "artifacts": {
            "output.a_csv": {"path": str(mirror), "source_path": str(first)},
            "output.b_csv": {"path": str(mirror), "source_path": str(second)},
        }
    }
    assert output_origin_map(manifest) == {}
    spec = parse_output_spec(
        {"required_outputs": [{"extensions": [".csv"], "min_count": 2}]},
        strict=True,
    )
    assert validate_output_spec(
        spec, [str(first), str(second)], base_dir=tmp_path, artifact_manifest=manifest
    )["matched_counts"] == [2]
