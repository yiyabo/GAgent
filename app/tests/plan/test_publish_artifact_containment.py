"""Publication sources must stay inside the plan's own storage roots.

``publish_artifact``/``stage`` copy a caller-supplied path into the plan
artifact store, which the artifact routes then serve back; without the
containment guard a tool argument such as ``/app/.env`` would be published
as an artifact (P5, 2026-10-11).
"""

from __future__ import annotations

import pytest

from app.services.plans import artifact_versions as av


@pytest.fixture
def containment_env(isolated_app_env, tmp_path, monkeypatch):
    import app.services.path_router as routing

    monkeypatch.setenv("APP_RUNTIME_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setattr(routing, "_default_router", None)
    from app.services.plans import artifact_contracts

    monkeypatch.setattr(artifact_contracts, "_repo_root", lambda: tmp_path / "plan-artifacts")
    (tmp_path / "runtime").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _manifest() -> dict:
    return {"schema_version": 2, "artifacts": {}, "versions": {}, "publications": {}, "bindings": {}}


def test_stage_rejects_source_outside_plan_roots(containment_env, tmp_path) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="version_source_outside_plan_roots"):
        av.stage(1, "general.evidence_md", str(outside), 1, _manifest(), None)


def test_stage_accepts_source_inside_runtime_root(containment_env, tmp_path) -> None:
    source = tmp_path / "runtime" / "session_x" / "out.json"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("{}", encoding="utf-8")

    record = av.stage(1, "general.evidence_md", str(source), 1, _manifest(), None)

    assert record["alias"] == "general.evidence_md"
    assert record["source_path"] == str(source.resolve())


def test_publish_artifact_v1_rejects_source_outside_plan_roots(containment_env, tmp_path) -> None:
    from app.services.plans.artifact_contracts import publish_artifact

    outside = tmp_path / "outside.md"
    outside.write_text("# secret\n", encoding="utf-8")

    with pytest.raises(ValueError, match="version_source_outside_plan_roots"):
        publish_artifact(
            plan_id=1,
            alias="general.evidence_md",
            source_path=str(outside),
            producer_task_id=1,
            manifest={"plan_id": 1, "artifacts": {}},
        )
