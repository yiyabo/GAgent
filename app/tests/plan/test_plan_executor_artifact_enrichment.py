"""Golden-master tests for the plan_executor artifact enrichment cluster (W0 gap).

The artifact enrichment / publish cluster in `PlanExecutor`
(`_enrich_finalized_payload_with_artifacts`, `_promote_workspace_artifacts_to_task_dir`)
had few direct assertions ahead of the planned `executor_artifacts.py` split.
These tests pin full output structures for fixture inputs.  External
boundaries are faked deterministically: `publish_artifact` /
`save_artifact_manifest` (plan_executor module attributes), the deliverable
publish step, the runtime session dir, and the task workspace resolver.
Dynamic path roots are normalized to ``<TMP>`` / ``<MANIFEST>`` placeholders
so the goldens are machine-independent.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import pytest

import app.services.plans.plan_executor as plan_executor_module
from app.services.plans.plan_executor import PlanExecutor
from app.services.plans.plan_models import PlanNode


def _executor() -> PlanExecutor:
    executor = PlanExecutor.__new__(PlanExecutor)
    executor._settings = SimpleNamespace(artifact_backfill_enabled=False)
    return executor


def _normalize(value: Any, tmp_path: Path, manifest_path: Optional[str] = None) -> Any:
    if isinstance(value, str):
        if manifest_path is not None and value == manifest_path:
            return "<MANIFEST>"
        return value.replace(str(tmp_path), "<TMP>")
    if isinstance(value, dict):
        return {key: _normalize(item, tmp_path, manifest_path) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize(item, tmp_path, manifest_path) for item in value]
    return value


@pytest.fixture()
def fake_publish_boundary(monkeypatch: pytest.MonkeyPatch) -> Dict[str, List[Any]]:
    calls: Dict[str, List[Any]] = {"publish": [], "save": [], "deliver": []}

    def _fake_publish_artifact(*, plan_id, alias, source_path, producer_task_id, manifest, session_id=None):
        entry = {
            "alias": alias,
            "path": str(source_path),
            "producer_task_id": producer_task_id,
            "source": "fake_publish",
        }
        manifest.setdefault("artifacts", {})[alias] = entry
        calls["publish"].append(alias)
        return entry

    def _fake_save(plan_id, manifest, session_id=None):
        calls["save"].append((plan_id, sorted(manifest.get("artifacts", {}))))

    monkeypatch.setattr(plan_executor_module, "publish_artifact", _fake_publish_artifact)
    monkeypatch.setattr(plan_executor_module, "save_artifact_manifest", _fake_save)
    return calls


def test_enrich_failed_status_metadata_merge_golden(tmp_path) -> None:
    report = tmp_path / "summary.md"
    report.write_text("# summary\n", encoding="utf-8")
    data = tmp_path / "data.csv"
    data.write_text("a,b\n", encoding="utf-8")

    node = PlanNode(
        id=7,
        plan_id=1,
        name="Write summary",
        instruction="Write the summary.",
        metadata={"artifact_contract": {"requires": [], "publishes": ["general.summary_md"]}},
    )
    payload: Dict[str, Any] = {
        "status": "failed",
        # Free-text paths in content are intentionally NOT collected.
        "content": f"partial output at {report}",
        "artifact_paths": [str(data), str(data)],
        "metadata": {},
    }
    session_context = {
        "session_id": "sess-g1",
        "resolved_input_artifacts": {"general.data_csv": str(data)},
    }

    out = _executor()._enrich_finalized_payload_with_artifacts(
        plan_id=1,
        node=node,
        payload=payload,
        final_status="failed",
        session_context=session_context,
    )

    assert _normalize(out, tmp_path) == {
        "status": "failed",
        "content": "partial output at <TMP>/summary.md",
        "artifact_paths": ["<TMP>/data.csv"],
        "metadata": {
            "resolved_input_artifacts": {"general.data_csv": "<TMP>/data.csv"},
            "artifact_contract": {"requires": [], "publishes": ["general.summary_md"]},
            "artifact_paths": ["<TMP>/data.csv"],
        },
    }


def test_enrich_completed_status_contract_artifacts_golden(tmp_path, fake_publish_boundary) -> None:
    report = tmp_path / "report.md"
    report.write_text("# report\n", encoding="utf-8")
    report_size = report.stat().st_size

    node = PlanNode(
        id=5,
        plan_id=1,
        name="Write report",
        instruction="Write the final report.",
        metadata={"artifact_contract": {"requires": [], "publishes": ["general.report_md"]}},
    )
    payload: Dict[str, Any] = {
        "status": "completed",
        "content": "done",
        "contract_artifacts": [
            {"path": str(report), "expected": "report.md"},
            {"path": str(tmp_path / "missing.csv"), "expected": "missing.csv"},
            {"expected": "ghost.txt"},
        ],
        "metadata": {},
    }
    manifest: Dict[str, Any] = {"plan_id": 1, "artifacts": {}}
    session_context = {"session_id": "sess-g2", "_artifact_manifest": manifest}

    executor = _executor()
    delivered: List[Dict[str, Any]] = []
    executor._publish_contract_deliverables = lambda **kwargs: delivered.append(kwargs)

    out = executor._enrich_finalized_payload_with_artifacts(
        plan_id=1,
        node=node,
        payload=payload,
        final_status="completed",
        session_context=session_context,
    )

    manifest_path = out["metadata"]["artifact_manifest_path"]
    assert manifest_path.endswith("runtime/session_sess-g2/artifacts/plan_1/artifacts_manifest.json")

    assert _normalize(out, tmp_path, manifest_path) == {
        "status": "completed",
        "content": "done",
        "contract_artifacts": [
            {"path": "<TMP>/report.md", "expected": "report.md"},
            {"path": "<TMP>/missing.csv", "expected": "missing.csv"},
            {"expected": "ghost.txt"},
        ],
        "artifact_paths": ["<TMP>/report.md"],
        "metadata": {
            "artifact_contract": {"requires": [], "publishes": ["general.report_md"]},
            "artifact_paths": ["<TMP>/report.md"],
            "contract_artifacts": [
                {"path": "<TMP>/report.md", "expected": "report.md"},
                {"path": "<TMP>/missing.csv", "expected": "missing.csv"},
                {"expected": "ghost.txt"},
            ],
            "missing_contract_artifacts": [
                {"expected": "missing.csv", "path": "<TMP>/missing.csv", "reason": "not_found"},
                {"expected": "ghost.txt", "reason": "missing_path"},
            ],
            "published_artifacts": {
                "general.report_md": {
                    "alias": "general.report_md",
                    "path": "<TMP>/report.md",
                    "producer_task_id": 5,
                    "source": "fake_publish",
                },
                "contract:report.md": {
                    "alias": "contract:report.md",
                    "path": "<TMP>/report.md",
                    "producer_task_id": 5,
                    "source": "contract_artifacts",
                    "expected": "report.md",
                    "size": report_size,
                    "exists": True,
                    "promotion_skipped": False,
                    "promotion_skipped_reason": None,
                },
            },
            "artifact_manifest_path": "<MANIFEST>",
        },
    }

    # Boundary call contract: one publish (contract loop; backfill found no
    # alias-compatible candidate), one manifest save carrying both entries,
    # one deliverable publish pass with both entries.
    assert fake_publish_boundary["publish"] == ["general.report_md"]
    assert fake_publish_boundary["save"] == [(1, ["contract:report.md", "general.report_md"])]
    assert len(delivered) == 1
    assert sorted(delivered[0]["published"].keys()) == ["contract:report.md", "general.report_md"]
    assert sorted(manifest["artifacts"].keys()) == ["contract:report.md", "general.report_md"]


def test_promote_workspace_artifacts_golden(tmp_path, monkeypatch) -> None:
    session_dir = tmp_path / "runtime" / "sess-g3"
    task_dir = session_dir / "_scratch" / "plan1_task5" / "run_1"
    (session_dir / "workspace" / "sub").mkdir(parents=True)
    task_dir.mkdir(parents=True)

    in_task = task_dir / "already.md"
    in_task.write_text("kept\n", encoding="utf-8")
    in_workspace = session_dir / "workspace" / "sub" / "result.md"
    in_workspace.write_text("promoted\n", encoding="utf-8")
    outside = tmp_path / "elsewhere.md"
    outside.write_text("ignored\n", encoding="utf-8")
    ghost = session_dir / "workspace" / "ghost.md"

    import app.services.session_paths as session_paths

    monkeypatch.setattr(
        session_paths,
        "get_runtime_session_dir",
        lambda session_id, create=True: session_dir,
    )

    executor = _executor()
    executor._resolve_task_tool_workspace = lambda node, session_id=None: (None, str(task_dir))

    node = PlanNode(id=5, plan_id=1, name="Build", instruction="build it")
    payload: Dict[str, Any] = {
        "status": "completed",
        "output_path": str(in_workspace),
        "result_path": str(outside),
        "preview_path": str(ghost),
        "artifact_paths": [str(in_task)],
        "metadata": {},
    }

    out = executor._promote_workspace_artifacts_to_task_dir(
        node=node,
        payload=payload,
        session_context={"session_id": "sess-g3"},
    )

    promoted_target = task_dir / "sub" / "result.md"
    assert promoted_target.read_text(encoding="utf-8") == "promoted\n"
    assert not (task_dir / "elsewhere.md").exists()

    assert _normalize(out, tmp_path) == {
        "status": "completed",
        "output_path": "<TMP>/runtime/sess-g3/workspace/sub/result.md",
        "result_path": "<TMP>/elsewhere.md",
        "preview_path": "<TMP>/runtime/sess-g3/workspace/ghost.md",
        "artifact_paths": [
            "<TMP>/runtime/sess-g3/_scratch/plan1_task5/run_1/already.md",
            "<TMP>/runtime/sess-g3/_scratch/plan1_task5/run_1/sub/result.md",
        ],
        "produced_files": [
            "<TMP>/runtime/sess-g3/_scratch/plan1_task5/run_1/already.md",
            "<TMP>/runtime/sess-g3/_scratch/plan1_task5/run_1/sub/result.md",
        ],
        "session_artifact_paths": [
            "_scratch/plan1_task5/run_1/sub/result.md",
            "_scratch/plan1_task5/run_1/already.md",
        ],
        "metadata": {
            "artifact_paths": [
                "<TMP>/runtime/sess-g3/_scratch/plan1_task5/run_1/already.md",
                "<TMP>/runtime/sess-g3/_scratch/plan1_task5/run_1/sub/result.md",
            ],
            "session_artifact_paths": [
                "_scratch/plan1_task5/run_1/sub/result.md",
                "_scratch/plan1_task5/run_1/already.md",
            ],
        },
    }
