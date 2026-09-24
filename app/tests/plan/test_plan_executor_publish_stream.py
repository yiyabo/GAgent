"""Regression tests for D6: PlanExecutor publish path convergence.

The legacy publisher-whitelist path (`_legacy_publish_contract_deliverables`)
was deleted after the unified artifact event stream had been the coded
default for over two weeks (flag introduced 2026-09-02, default True since
inception). These tests assert the remaining unified path still emits
artifact events to the registry projector and still materializes
deliverables end to end.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from types import SimpleNamespace
from typing import List, Optional, Tuple

import app.services.artifacts.projector as projector_module
from app.config.deliverable_config import DeliverableSettings
from app.services.artifacts.projector import RegistryProjector
from app.services.deliverables.publisher import DeliverablePublisher
from app.services.plans.plan_executor import PlanExecutor

SESSION_ID = "d6session"


def _make_projector(tmp_path: Path) -> Tuple[RegistryProjector, Path]:
    settings = dataclasses.replace(DeliverableSettings())
    project_root = tmp_path / "project"
    runtime_dir = project_root / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    publisher = DeliverablePublisher(settings=settings, project_root=project_root, runtime_dir=runtime_dir)
    projector = RegistryProjector(publisher=publisher)
    session_dir = publisher.get_session_dir(SESSION_ID, create=True)
    return projector, session_dir


def _node(task_id: int = 5) -> SimpleNamespace:
    return SimpleNamespace(
        id=task_id,
        plan_id=1,
        instruction="produce report",
        display_name=lambda: f"task-{task_id}",
    )


def _executor() -> PlanExecutor:
    # _publish_contract_deliverables only touches self._task_completes_plan,
    # whose repo access failure is contained (returns False).
    return PlanExecutor.__new__(PlanExecutor)


class _RecordingProjector:
    def __init__(self) -> None:
        self.calls: List[dict] = []

    def consume_plan_events(self, **kwargs):
        self.calls.append(kwargs)
        return None


def _wire_recorder(monkeypatch) -> _RecordingProjector:
    recorder = _RecordingProjector()
    monkeypatch.setattr(projector_module, "get_registry_projector", lambda: recorder)
    return recorder


def test_publish_contract_deliverables_emits_artifact_events(tmp_path, monkeypatch):
    recorder = _wire_recorder(monkeypatch)
    src = tmp_path / "task1_evidence_cards.md"
    src.write_text("# evidence\n", encoding="utf-8")
    contract_src = tmp_path / "contract_output.md"
    contract_src.write_text("# contract\n", encoding="utf-8")

    _executor()._publish_contract_deliverables(
        plan_id=1,
        node=_node(),
        published={"general.evidence_cards_md": {"path": str(src)}},
        session_context={"session_id": SESSION_ID},
        manifest={"artifacts": {"contract:report_md": {"path": str(contract_src)}}},
    )

    assert len(recorder.calls) == 1
    call = recorder.calls[0]
    assert call["session_id"] == SESSION_ID
    assert call["plan_id"] == 1
    assert call["task_id"] == 5
    events = call["events"]
    assert len(events) == 2

    by_path = {event.file_path: event for event in events}
    plain = by_path[str(src)]
    assert plain.session_id == SESSION_ID
    assert plain.alias == "general.evidence_cards_md"
    assert plain.path_aliases == ["general.evidence_cards_md"]
    assert plain.producer_kind == "plan_task"
    assert plain.producer_plan_id == 1
    assert plain.producer_task_id == 5
    assert plain.publish_requested is True
    assert plain.publish_role == "normal"
    assert plain.file_ext == ".md"
    assert plain.file_size == src.stat().st_size

    contract = by_path[str(contract_src)]
    assert contract.alias is None
    assert contract.path_aliases == ["contract:report_md"]
    assert contract.contract_declared is False
    assert contract.contract_alias_source == "executor"


def test_publish_contract_deliverables_materializes_via_real_projector(tmp_path, monkeypatch):
    projector, session_dir = _make_projector(tmp_path)
    monkeypatch.setattr(projector_module, "get_registry_projector", lambda: projector)
    src_dir = session_dir / "_scratch" / "plan1_task5" / "run_x" / "results"
    src_dir.mkdir(parents=True, exist_ok=True)
    src = src_dir / "task1_evidence_cards.md"
    src.write_text("# evidence\n", encoding="utf-8")

    _executor()._publish_contract_deliverables(
        plan_id=1,
        node=_node(),
        published={"general.evidence_cards_md": {"path": str(src)}},
        session_context={"session_id": SESSION_ID},
        manifest=None,
    )

    target = session_dir / "deliverables" / "latest" / "docs" / "task1_evidence_cards.md"
    assert target.is_file()
    manifest = json.loads((session_dir / "deliverables" / "manifest_latest.json").read_text(encoding="utf-8"))
    paths = {item["path"] for item in manifest["items"]}
    assert "docs/task1_evidence_cards.md" in paths


def test_publish_contract_deliverables_skips_missing_files(tmp_path, monkeypatch):
    recorder = _wire_recorder(monkeypatch)
    _executor()._publish_contract_deliverables(
        plan_id=1,
        node=_node(),
        published={"general.ghost_md": {"path": str(tmp_path / "does_not_exist.md")}},
        session_context={"session_id": SESSION_ID},
        manifest=None,
    )
    assert recorder.calls == []


def test_publish_contract_deliverables_requires_session_id(monkeypatch):
    recorder = _wire_recorder(monkeypatch)
    _executor()._publish_contract_deliverables(
        plan_id=1,
        node=_node(),
        published={"general.x_md": {"path": "/whatever.md"}},
        session_context={},
        manifest=None,
    )
    assert recorder.calls == []


def test_legacy_publish_path_removed():
    assert not hasattr(PlanExecutor, "_legacy_publish_contract_deliverables")
    source = Path(PlanExecutor.__module__.replace(".", "/") + ".py")
    assert "artifact_event_stream_enabled" not in source.read_text(encoding="utf-8")
