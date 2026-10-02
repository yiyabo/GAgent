"""Real PlanExecutor delivery verification; only the external agent is scripted."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from app.config.executor_config import get_executor_settings
from app.database import get_db, init_db
from app.repository.plan_repository import PlanRepository
from app.services.foundation.settings import get_settings
from app.services.plans.plan_executor import ExecutionConfig, PlanExecutor
from app.services.plans.task_delegate_executor import TaskDelegationResult


@pytest.fixture
def delegate_case(isolated_app_env, monkeypatch):
    from app.services import path_router
    from app.services.deliverables import publisher
    from app.llm import LLMClient
    model_calls = []

    def forbidden_model(*args, **kwargs):
        model_calls.append(1)
        raise AssertionError("delegate delivery regression must not invoke a model")

    for method in ("chat", "chat_async", "stream_chat", "stream_chat_async", "stream_chat_with_tools_async"):
        monkeypatch.setattr(LLMClient, method, forbidden_model)
    monkeypatch.setattr(path_router, "_default_router", None)
    monkeypatch.setattr(publisher, "_publisher", publisher.DeliverablePublisher(
        project_root=isolated_app_env["runtime_root"].parent,
        runtime_dir=isolated_app_env["runtime_root"],
    ))
    init_db()
    with get_db() as conn:
        conn.execute("INSERT INTO chat_sessions(id,owner_id,name) VALUES('delegate-delivery','tester','delivery')")
        conn.commit()

    def prepare(*, runtime_v2, versioning, status="completed", pre_promoted=False, conflict=False):
        monkeypatch.setenv("AGENT_RUNTIME_V2_ENABLED", str(int(runtime_v2)))
        monkeypatch.setenv("ARTIFACT_VERSIONING_ENABLED", str(int(versioning)))
        get_settings.cache_clear()
        repo = PlanRepository()
        tree = repo.create_plan("Delegate output delivery", owner="tester")
        node = repo.create_task(tree.id, name="cleaned outputs", instruction="Write clean.csv and summary.json.")
        directory = path_router.get_path_router().get_task_output_dir_from_tree(
            "delegate-delivery", node.id, repo.get_plan_tree(tree.id), create=True,
        )
        outputs = {"clean.csv": "id,score\na,10\nb,20\n", "summary.json": '{"count":2,"mean":15}'}
        required = [{"kind": "data", "extensions": [Path(name).suffix], "target_path": str(directory / name)} for name in outputs]
        repo.update_task(tree.id, node.id, metadata={"output_spec": {"source": "explicit", "required_outputs": required}})
        with get_db() as conn:
            conn.execute("UPDATE chat_sessions SET plan_id=? WHERE id='delegate-delivery'", (tree.id,))
            conn.commit()
        source_dir = path_router.get_path_router().get_session_dir("delegate-delivery") / "_scratch" / f"plan{tree.id}_task{node.id}" / "run_test"
        calls = []

        class Delegate:
            def execute(self, spec):
                calls.append(spec)
                source_dir.mkdir(parents=True, exist_ok=True)
                paths = []
                for name, text in outputs.items():
                    source = source_dir / name
                    source.write_text(text)
                    paths.append(str(source))
                    if pre_promoted:
                        shutil.copy2(source, directory / name)
                if conflict:
                    (directory / "clean.csv").write_text("id,score\nprevious,999\n")
                return TaskDelegationResult(
                    status=status, summary="The external agent produced clean.csv and summary.json from the supplied rows.",
                    artifact_paths=paths, executor="local",
                    raw_result={"artifact_paths": paths, "run_directory": str(source_dir)},
                    metadata={"execution_status": status},
                )

        executor = PlanExecutor(repo=repo, settings=replace(get_executor_settings(),
            plan_task_execution_backend="external_agent", plan_task_agent_backend="local"),
            task_delegate_executor=Delegate())
        config = ExecutionConfig(session_context={"session_id": "delegate-delivery", "owner_id": "tester", "memory_enabled": False})
        return executor, repo, tree.id, node.id, config, directory, source_dir, outputs, calls

    yield prepare
    get_settings.cache_clear()
    assert model_calls == []


@pytest.mark.parametrize("runtime_v2", [False, True])
@pytest.mark.parametrize("versioning", [False, True])
@pytest.mark.parametrize("pre_promoted", [False, True])
def test_successful_delegate_has_formal_output_evidence_before_verification(delegate_case, runtime_v2, versioning, pre_promoted):
    executor, repo, plan, task, config, target, source, outputs, calls = delegate_case(
        runtime_v2=runtime_v2, versioning=versioning, pre_promoted=pre_promoted,
    )
    result = executor.execute_task(plan, task, config=config)
    assert result.status == "completed", result.to_dict()
    report = result.metadata["output_verification"]
    assert report["status"] == "passed" and report["matched_counts"] == [1, 1]
    assert {str(target / name) for name in outputs}.issubset(report["artifact_paths"])
    mirrors = result.metadata["derived_mirrors"]
    for name, text in outputs.items():
        assert (target / name).read_text() == text
        mirror = next(row for row in mirrors if row["path"] == str(target / name))
        assert mirror["source"] == str(source / name)
        assert mirror["sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert repo.get_plan_tree(plan).nodes[task].status == "completed"
    assert len(calls) == 1


@pytest.mark.parametrize("runtime_v2", [False, True])
@pytest.mark.parametrize("versioning", [False, True])
@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_failed_delegate_is_not_rescued_by_valid_existing_outputs(delegate_case, runtime_v2, versioning, status):
    executor, repo, plan, task, config, target, source, outputs, calls = delegate_case(
        runtime_v2=runtime_v2, versioning=versioning, status=status, pre_promoted=True,
    )
    result = executor.execute_task(plan, task, config=config)
    assert result.status == "failed", result.to_dict()
    assert result.metadata["delegation_status"] == status
    assert repo.get_plan_tree(plan).nodes[task].status == "failed"
    assert all((target / name).read_text() == value for name, value in outputs.items())
    assert len(calls) == 1


@pytest.mark.parametrize("runtime_v2", [False, True])
def test_delegate_does_not_replace_a_conflicting_formal_file(delegate_case, runtime_v2):
    from app.services.plans.artifact_versions import ArtifactRevisionConflict
    executor, repo, plan, task, config, target, source, outputs, calls = delegate_case(
        runtime_v2=runtime_v2, versioning=False, conflict=True,
    )
    with pytest.raises(ArtifactRevisionConflict, match="delegate_target_conflict"):
        executor.execute_task(plan, task, config=config)
    assert (target / "clean.csv").read_text() == "id,score\nprevious,999\n"
    assert repo.get_plan_tree(plan).nodes[task].status != "completed"


@pytest.mark.parametrize("runtime_v2", [False, True])
def test_changing_delegate_source_during_copy_cannot_be_accepted(delegate_case, runtime_v2, monkeypatch):
    from app.services.plans import artifact_versions as versions
    executor, repo, plan, task, config, target, source, outputs, calls = delegate_case(
        runtime_v2=runtime_v2, versioning=False,
    )
    copy = versions.shutil.copy2

    def changing_copy(src, dst, *args, **kwargs):
        result = copy(src, dst, *args, **kwargs)
        if Path(src) == source / "clean.csv":
            Path(src).write_text("id,score\nchanged,999\n")
        return result

    monkeypatch.setattr(versions.shutil, "copy2", changing_copy)
    with pytest.raises(versions.StaleArtifactInputs, match="delegate_source_changed"):
        executor.execute_task(plan, task, config=config)
    assert not (target / "clean.csv").exists()
    assert not list(target.glob(".delegate-output-*"))
    assert repo.get_plan_tree(plan).nodes[task].status != "completed"


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_failed_delegate_receipt_recovers_sql_without_publishing_success(delegate_case, monkeypatch, status):
    from app.services.plans import artifact_versions as versions
    from app.services.plans.artifact_contracts import artifact_manifest_path
    executor, repo, plan, task, config, target, source, outputs, calls = delegate_case(
        runtime_v2=False, versioning=True, status=status, pre_promoted=True,
    )
    atomic = versions._atomic

    def crash_after_manifest(path, data):
        atomic(path, data)
        raise OSError("crash after manifest before task SQL commit")

    monkeypatch.setattr(versions, "_atomic", crash_after_manifest)
    with pytest.raises(OSError, match="before task SQL commit"):
        executor.execute_task(plan, task, config=config)
    assert repo.get_plan_tree(plan).nodes[task].status == "delegating"
    monkeypatch.setattr(versions, "_atomic", atomic)
    versions.recover(repo, plan)
    node = repo.get_plan_tree(plan).nodes[task]
    assert node.status == "failed"
    assert json.loads(node.execution_result)["metadata"]["delegation_status"] == status
    manifest = versions._read(artifact_manifest_path(plan, "delegate-delivery"))
    assert manifest["artifacts"] == {} and manifest["bindings"] == {}
    assert len(manifest["publications"]) == 1
    assert next(iter(manifest["publications"].values()))["applied"] is True
    versions.recover(repo, plan)
    assert len(calls) == 1 and repo.get_plan_tree(plan).nodes[task].status == "failed"
