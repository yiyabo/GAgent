"""Tests for the artifact event stream (events, registry, projector)."""

from __future__ import annotations

import dataclasses
import json
import os
import threading
from pathlib import Path

from app.config.deliverable_config import DeliverableSettings
from app.services.artifacts.events import ArtifactEvent, append_events, iter_events
from app.services.artifacts.projector import RegistryProjector
from app.services.artifacts.registry import RegistryStore, apply_event_to_registry
from app.services.deliverables.publisher import DeliverablePublisher, PublishReport

SESSION_ID = "testsession"


def _make_settings(**overrides) -> DeliverableSettings:
    return dataclasses.replace(DeliverableSettings(), **overrides)


def _make_publisher(tmp_path: Path, **setting_overrides) -> DeliverablePublisher:
    settings = _make_settings(**setting_overrides)
    project_root = tmp_path / "project"
    runtime_dir = project_root / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    return DeliverablePublisher(settings=settings, project_root=project_root, runtime_dir=runtime_dir)


def _session_dir(publisher: DeliverablePublisher) -> Path:
    return publisher.get_session_dir(SESSION_ID, create=True)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _read_manifest(session_dir: Path) -> dict:
    return json.loads((session_dir / "deliverables" / "manifest_latest.json").read_text(encoding="utf-8"))


def _plan_event(src: Path, **overrides) -> ArtifactEvent:
    params = dict(
        session_id=SESSION_ID,
        file_path=str(src),
        publish_requested=True,
        producer_kind="plan_task",
        producer_plan_id=1,
        producer_task_id=5,
        producer_task_name="task-5",
    )
    params.update(overrides)
    return ArtifactEvent(**params)


# ---------------------------------------------------------------------- events


def test_events_roundtrip_and_concurrent_append(tmp_path):
    session_dir = tmp_path / "session_x"
    events = [ArtifactEvent(session_id="session_x", file_path=f"/tmp/f{i}.md") for i in range(10)]
    append_events(session_dir, events[:5])
    append_events(session_dir, events[5:])
    loaded = list(iter_events(session_dir))
    assert [event.event_id for event in loaded] == [event.event_id for event in events]
    assert loaded[0].type == "artifact.produced"

    def worker(n: int) -> None:
        append_events(
            session_dir,
            [ArtifactEvent(session_id="session_x", file_path=f"/tmp/t{n}_{i}.md") for i in range(20)],
        )

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    lines = (session_dir / "artifacts" / "events.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 90
    for line in lines:
        json.loads(line)


def test_iter_events_tolerates_corrupt_lines(tmp_path):
    session_dir = tmp_path / "session_x"
    append_events(session_dir, [ArtifactEvent(session_id="session_x", file_path="/tmp/ok.md")])
    log_path = session_dir / "artifacts" / "events.jsonl"
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write("{not json\n")
    loaded = list(iter_events(session_dir))
    assert len(loaded) == 1


# ---------------------------------------------------------------------- registry


def test_registry_idempotent_and_rebuild(tmp_path):
    session_dir = tmp_path / "session_y"
    store = RegistryStore(session_dir)
    event = ArtifactEvent(
        session_id="session_y",
        file_path="/tmp/a.md",
        alias="general.a_md",
        publish_requested=True,
        module="docs",
    )
    append_events(session_dir, [event])
    with store.locked() as registry:
        assert apply_event_to_registry(registry, event) is not None
        assert apply_event_to_registry(registry, event) is None
        store.save(registry)
    loaded = store.load()
    assert event.event_id in loaded["event_ids"]
    assert "alias::general.a_md" in loaded["items"]
    rebuilt = store.rebuild()
    assert set(rebuilt["items"].keys()) == set(loaded["items"].keys())


# ---------------------------------------------------------------------- projector (plan path)


def test_projector_publishes_plan_task_outputs(tmp_path):
    """Regression anchor for the plan-160 empty-panel bug: a task output whose
    filename fails the docs keyword whitelist must still be published."""
    publisher = _make_publisher(tmp_path)
    projector = RegistryProjector(publisher=publisher)
    session_dir = _session_dir(publisher)
    src = _write(
        session_dir / "_scratch" / "plan1_task5" / "run_x" / "results" / "task1_evidence_cards.md",
        "# evidence\n",
    )
    event = _plan_event(src, alias="general.evidence_cards_md")
    projector.consume_plan_events(session_id=SESSION_ID, events=[event], plan_id=1, task_id=5, task_name="task-5")

    target = session_dir / "deliverables" / "latest" / "docs" / "task1_evidence_cards.md"
    assert target.is_file()
    manifest = _read_manifest(session_dir)
    row = next(item for item in manifest["items"] if item["path"] == "docs/task1_evidence_cards.md")
    assert row.get("trusted_publish") is True
    registry = RegistryStore(session_dir).load()
    item = registry["items"]["alias::general.evidence_cards_md"]
    assert item["published"]["state"] == "published"
    assert item["published"]["deliverable_path"] == "docs/task1_evidence_cards.md"
    assert item["published"]["storage"] == "copy"


def test_projector_manifest_stable_across_republishes(tmp_path):
    """Trusted rows must survive later publishes (the docs cleanup and the
    manifest scan used to drop/delete them)."""
    publisher = _make_publisher(tmp_path)
    projector = RegistryProjector(publisher=publisher)
    session_dir = _session_dir(publisher)
    scratch = session_dir / "_scratch" / "run" / "results"
    first = _write(scratch / "task1_evidence_cards.md", "# a\n")
    second = _write(scratch / "task2_cocktail_design.md", "# b\n")
    projector.consume_plan_events(session_id=SESSION_ID, events=[_plan_event(first)], plan_id=1, task_id=5)
    projector.consume_plan_events(session_id=SESSION_ID, events=[_plan_event(second)], plan_id=1, task_id=6)

    manifest = _read_manifest(session_dir)
    paths = {item["path"] for item in manifest["items"]}
    assert "docs/task1_evidence_cards.md" in paths
    assert "docs/task2_cocktail_design.md" in paths
    assert (session_dir / "deliverables" / "latest" / "docs" / "task1_evidence_cards.md").is_file()


def test_projector_consume_is_idempotent(tmp_path):
    publisher = _make_publisher(tmp_path)
    projector = RegistryProjector(publisher=publisher)
    session_dir = _session_dir(publisher)
    src = _write(session_dir / "_scratch" / "r.md", "# x\n")
    event = _plan_event(src)
    projector.consume_plan_events(session_id=SESSION_ID, events=[event])
    assert projector.consume_plan_events(session_id=SESSION_ID, events=[event]) is None
    log_lines = (session_dir / "artifacts" / "events.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(log_lines) == 1


def test_untrusted_submit_still_respects_doc_whitelist(tmp_path):
    """Chat behavior must be unchanged: the same filename without the trusted
    flag is still skipped by the publisher."""
    publisher = _make_publisher(tmp_path)
    session_dir = _session_dir(publisher)
    src = _write(session_dir / "_scratch" / "task1_evidence_cards.md", "# x\n")
    report = publisher.publish_from_tool_result(
        session_id=SESSION_ID,
        tool_name="deliverable_submit",
        raw_result={"deliverable_submit": {"artifacts": [{"path": str(src), "module": "docs"}], "publish": True}},
    )
    assert report is not None
    assert (report.submit_artifacts_published or 0) == 0
    assert not (session_dir / "deliverables" / "latest" / "docs" / "task1_evidence_cards.md").exists()


def test_final_report_role_published_to_docs(tmp_path):
    publisher = _make_publisher(tmp_path)
    projector = RegistryProjector(publisher=publisher)
    session_dir = _session_dir(publisher)
    src = _write(session_dir / "_scratch" / "run" / "results" / "shrimp_vibrio_phage_project_plan.md", "# plan\n")
    event = _plan_event(src, publish_role="final_report")
    projector.consume_plan_events(session_id=SESSION_ID, events=[event])
    assert (session_dir / "deliverables" / "latest" / "docs" / "shrimp_vibrio_phage_project_plan.md").is_file()
    registry = RegistryStore(session_dir).load()
    item = next(iter(registry["items"].values()))
    assert item.get("publish_role") == "final_report"
    assert item["published"]["state"] == "published"


# ---------------------------------------------------------------------- big files


def test_big_file_hardlink(tmp_path):
    publisher = _make_publisher(tmp_path, copy_max_bytes=8)
    projector = RegistryProjector(publisher=publisher)
    session_dir = _session_dir(publisher)
    src = _write(session_dir / "_scratch" / "big.fasta", ">a\nACGTACGTACGT\n")
    projector.consume_plan_events(session_id=SESSION_ID, events=[_plan_event(src)])

    target = session_dir / "deliverables" / "latest" / "image_tabular" / "big.fasta"
    assert target.is_file()
    assert os.path.samefile(str(target), str(src))
    manifest = _read_manifest(session_dir)
    row = next(item for item in manifest["items"] if item["path"] == "image_tabular/big.fasta")
    assert row["storage"] == "hardlink"
    registry = RegistryStore(session_dir).load()
    item = next(iter(registry["items"].values()))
    assert item["published"]["storage"] == "hardlink"


def test_big_file_reference(tmp_path):
    publisher = _make_publisher(tmp_path, copy_max_bytes=8, link_strategy="reference")
    projector = RegistryProjector(publisher=publisher)
    session_dir = _session_dir(publisher)
    src = _write(session_dir / "_scratch" / "big.fasta", ">a\nACGTACGTACGT\n")
    projector.consume_plan_events(session_id=SESSION_ID, events=[_plan_event(src)])

    assert not (session_dir / "deliverables" / "latest" / "image_tabular" / "big.fasta").exists()
    manifest = _read_manifest(session_dir)
    row = next(item for item in manifest["items"] if item["path"] == "image_tabular/big.fasta")
    assert row["storage"] == "reference"
    assert row["reference_source"] == str(src)
    registry = RegistryStore(session_dir).load()
    item = next(iter(registry["items"].values()))
    assert item["published"]["storage"] == "reference"


# ---------------------------------------------------------------------- module resolution


def test_resolve_module_fallbacks(tmp_path):
    projector = RegistryProjector(publisher=_make_publisher(tmp_path))
    assert projector.resolve_module(Path("/x/task1_evidence.md")) == "docs"
    assert projector.resolve_module(Path("/x/library.bib")) == "refs"
    assert projector.resolve_module(Path("/x/script.py")) == "code"
    assert projector.resolve_module(Path("/x/genomes.fasta")) == "image_tabular"
    assert projector.resolve_module(Path("/x/final.md"), alias="report.final_md") == "docs"
    assert projector.resolve_module(Path("/x/data.unknownext")) == "docs"


# ---------------------------------------------------------------------- chat envelope


def test_record_chat_publish(tmp_path):
    publisher = _make_publisher(tmp_path)
    projector = RegistryProjector(publisher=publisher)
    session_dir = _session_dir(publisher)
    _write(session_dir / "deliverables" / "latest" / "docs" / "report_final.md", "# r\n")
    manifest = {
        "version_id": "v1",
        "created_at": "2026-09-01T00:00:00+00:00",
        "template": "research",
        "single_version": True,
        "source": {},
        "modules": {},
        "paper_status": {},
        "release_state": "final",
        "public_release_ready": True,
        "release_summary": None,
        "hidden_artifact_prefixes": [],
        "published_files_count": 1,
        "published_modules": ["docs"],
        "items": [
            {
                "module": "docs",
                "path": "docs/report_final.md",
                "status": "final",
                "size": 5,
                "updated_at": "2026-09-01T00:00:00+00:00",
                "source_path": "runtime/session_testsession/_scratch/report_final.md",
            }
        ],
    }
    manifest_path = session_dir / "deliverables" / "manifest_latest.json"
    _write(manifest_path, json.dumps(manifest))
    report = PublishReport(
        version_id="v1",
        published_files_count=1,
        published_modules=["docs"],
        manifest_path=str(manifest_path),
        paper_status={},
    )
    projector.record_chat_publish(session_id=SESSION_ID, tool_name="deliverable_submit", report=report)

    registry = RegistryStore(session_dir).load()
    assert len(registry["items"]) == 1
    item = next(iter(registry["items"].values()))
    assert item["published"]["state"] == "published"
    assert item["published"]["storage"] == "copy"
    assert item["deliverable_path"] == "docs/report_final.md"
    # Re-recording the same publish adds events but never duplicate items.
    projector.record_chat_publish(session_id=SESSION_ID, tool_name="deliverable_submit", report=report)
    registry = RegistryStore(session_dir).load()
    assert len(registry["items"]) == 1


def test_rebuild_restores_publish_state(tmp_path):
    publisher = _make_publisher(tmp_path)
    projector = RegistryProjector(publisher=publisher)
    session_dir = _session_dir(publisher)
    src = _write(session_dir / "_scratch" / "run" / "results" / "task1_evidence_review.md", "# r\n")
    projector.consume_plan_events(session_id=SESSION_ID, events=[_plan_event(src, alias="general.evidence_md")])

    store = RegistryStore(session_dir)
    store.registry_path.unlink()
    manifest = _read_manifest(session_dir)
    rebuilt = store.rebuild(deliverables_manifest=manifest)
    item = rebuilt["items"]["alias::general.evidence_md"]
    assert item["published"]["state"] == "published"
    assert item["published"]["deliverable_path"] == "docs/task1_evidence_review.md"
