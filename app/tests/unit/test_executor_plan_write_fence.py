"""Real two-database rollback and plan executor interruption boundaries."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import app.database as database
from app.services.cancellation import CancelToken, set_cancel_token, reset_cancel_token
from app.services.chat_run_state import chat_run_claim
from app.services.run_budget import RunDeadlineExceeded
from app.repository.run_steps import StaleRunClaim
from app.repository.plan_repository import PlanRepository
from app.services.deep_think.checkpointing import ControllerRestoreError


@pytest.fixture
def two_databases(tmp_path, monkeypatch):
    main = tmp_path / "main.sqlite"
    plan = tmp_path / "plan.sqlite"
    with sqlite3.connect(main) as conn:
        conn.executescript("""CREATE TABLE chat_runs(run_id TEXT PRIMARY KEY, worker_id TEXT, status TEXT, lease_expires_at TEXT);
          INSERT INTO chat_runs VALUES('run-a','claim-a','running',datetime('now','+300 seconds'));""")
    with database.plan_db_connection(plan) as conn:
        conn.executescript("CREATE TABLE tasks(id INTEGER PRIMARY KEY, status TEXT, execution_result TEXT);")
        conn.execute("INSERT INTO tasks VALUES(1,'running','')")

    @contextmanager
    def get_main():
        conn = sqlite3.connect(main, isolation_level=None, timeout=1)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    monkeypatch.setattr(database, "get_db", get_main)
    return SimpleNamespace(main=main, plan=plan)


@contextmanager
def run_scope():
    token = CancelToken()
    cancel_handle = set_cancel_token(token)
    claim_handle = chat_run_claim.set(("run-a", "claim-a"))
    try:
        yield token
    finally:
        chat_run_claim.reset(claim_handle)
        reset_cancel_token(cancel_handle)


def state(plan):
    with sqlite3.connect(plan) as conn:
        return conn.execute("SELECT status,execution_result FROM tasks WHERE id=1").fetchone()


@pytest.mark.parametrize("cause", ["closed", "user", "deadline"])
def test_plan_mutation_rolls_back_after_run_stop(two_databases, cause):
    expected = RunDeadlineExceeded if cause == "deadline" else asyncio.CancelledError
    with run_scope() as token:
        with pytest.raises(expected):
            with database.plan_db_connection(two_databases.plan) as conn:
                conn.execute("UPDATE tasks SET status='completed',execution_result='late success' WHERE id=1")
                if cause == "closed":
                    token.close()
                elif cause == "user":
                    token.set("chat_run_cancelled")
                else:
                    token.set_deadline(time.monotonic() - 1)
    assert state(two_databases.plan) == ("running", "")


@pytest.mark.parametrize("change", ["expired", "stolen", "terminal"])
def test_plan_mutation_rolls_back_when_claim_no_longer_owns_run(two_databases, change):
    with run_scope():
        with pytest.raises(StaleRunClaim):
            with database.plan_db_connection(two_databases.plan) as conn:
                conn.execute("UPDATE tasks SET status='completed' WHERE id=1")
                with sqlite3.connect(two_databases.main) as main:
                    sql = {
                        "expired": "UPDATE chat_runs SET lease_expires_at=datetime('now','-1 second')",
                        "stolen": "UPDATE chat_runs SET worker_id='claim-b'",
                        "terminal": "UPDATE chat_runs SET status='failed'",
                    }[change]
                    main.execute(sql)
    assert state(two_databases.plan) == ("running", "")


def test_plan_read_remains_available_after_closed_scope(two_databases):
    with run_scope() as token:
        token.close()
        with database.plan_db_connection(two_databases.plan) as conn:
            assert tuple(conn.execute("SELECT status,execution_result FROM tasks").fetchone()) == ("running", "")


def test_main_claim_lock_is_held_through_plan_commit(two_databases):
    takeover = []

    def take_over():
        conn = sqlite3.connect(two_databases.main, isolation_level=None, timeout=0.02)
        try:
            conn.execute("UPDATE chat_runs SET worker_id='claim-b' WHERE run_id='run-a'")
            takeover.append("won")
        except sqlite3.OperationalError as exc:
            takeover.append("locked" if "locked" in str(exc) else str(exc))
        finally:
            conn.close()

    with run_scope():
        with database.plan_db_connection(two_databases.plan) as conn:
            conn.execute("UPDATE tasks SET status='completed' WHERE id=1")

            def at_commit(sql):
                if sql == "COMMIT":
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        pool.submit(take_over).result()

            conn.set_trace_callback(at_commit)
    assert takeover == ["locked"]
    assert state(two_databases.plan) == ("completed", "")


@pytest.mark.parametrize("failure", [RunDeadlineExceeded, StaleRunClaim, ControllerRestoreError])
def test_actual_executor_does_not_retry_budget_or_stale_claim(two_databases, failure, monkeypatch, tmp_path):
    from app.tests.plan.test_plan_executor_deps import _make_executor, _make_tree
    from app.services.plans.plan_models import PlanNode
    from app.services.plans.plan_executor import ExecutionConfig

    node = PlanNode(id=1, plan_id=1, name="Work", instruction="perform work")
    tree = _make_tree(1, [node])
    repo = MagicMock(spec=PlanRepository)
    repo.get_plan_tree.return_value = tree
    executor = _make_executor(repo)
    monkeypatch.setattr(executor, "_should_use_deep_think", lambda config: False)
    monkeypatch.setattr(executor, "_resolve_task_tool_workspace", lambda *args, **kwargs: ([], str(tmp_path)))
    executor._prompt_builder = SimpleNamespace(build=lambda **kwargs: "work")
    generate = MagicMock(side_effect=failure("stop this run"))
    executor._llm = SimpleNamespace(generate=generate)
    finalizer = MagicMock(side_effect=AssertionError("interruption must not finalize/reuse files"))
    monkeypatch.setattr(executor, "_materialize_finalization", finalizer)
    with pytest.raises(failure):
        executor._run_task(1, node, tree, ExecutionConfig(max_retries=3, enable_skills=False))
    assert generate.call_count == 1
    assert finalizer.call_count == 0
    assert all(call.kwargs.get("status") != "completed" for call in repo.update_task.call_args_list)


def test_full_plan_deadline_never_enters_recovery_or_next_task(monkeypatch):
    from app.tests.plan.test_plan_executor_deps import _make_executor, _make_tree
    from app.services.plans.plan_models import PlanNode
    from app.services.plans.plan_executor import ExecutionConfig

    tree = _make_tree(1, [PlanNode(id=1, plan_id=1, name="first"), PlanNode(id=2, plan_id=1, name="second")])
    repo = MagicMock()
    repo.get_plan_tree.return_value = tree
    executor = _make_executor(repo)
    run = MagicMock(side_effect=RunDeadlineExceeded("stop before recovery"))
    monkeypatch.setattr(executor, "_run_task", run)
    monkeypatch.setattr(executor, "_normalize_plan_dependency_edges", lambda tree: tree)
    monkeypatch.setattr(executor, "_infer_missing_dependencies", lambda tree: tree)
    with pytest.raises(RunDeadlineExceeded):
        executor.execute_plan(1, config=ExecutionConfig(enable_skills=False, skip_preflight=True, force_rerun=True, auto_recovery=True))
    assert run.call_count == 1
    repo.update_plan_metadata.assert_not_called()


@pytest.mark.parametrize("failure", [RunDeadlineExceeded, StaleRunClaim, ControllerRestoreError])
def test_deepthink_interruption_never_materializes_failure_from_old_files(failure, monkeypatch, tmp_path):
    from app.tests.plan.test_plan_executor_deps import _make_executor, _make_tree
    from app.services.plans.plan_models import PlanNode
    from app.services.plans.plan_executor import ExecutionConfig
    import app.services.plans.plan_executor as facade

    node = PlanNode(id=1, plan_id=1, name="work", instruction="perform work")
    tree = _make_tree(1, [node])
    executor = _make_executor()
    monkeypatch.setattr(executor, "_resolve_task_tool_workspace", lambda *args, **kwargs: ([], str(tmp_path)))
    calls = []

    class InterruptedAgent:
        def __init__(self, **kwargs):
            pass

        async def think(self, *args, **kwargs):
            calls.append(True)
            raise failure("stop before materialization")

    monkeypatch.setattr(facade, "DeepThinkAgent", InterruptedAgent)
    materialize = MagicMock(side_effect=AssertionError("interruption must propagate"))
    monkeypatch.setattr(executor, "_materialize_finalization", materialize)
    with pytest.raises(failure):
        executor._run_task_with_deep_think(
            plan_id=1, node=node, parent=None, dependencies=[], plan_outline=None,
            tree=tree, config=ExecutionConfig(enable_skills=False, skill_trace_enabled=False),
        )
    assert calls == [True] and materialize.call_count == 0


@pytest.mark.parametrize("native", [False, True])
def test_entered_resume_validates_before_overwriting_task_marker(native, monkeypatch, tmp_path):
    from app.tests.plan.test_plan_executor_deps import _make_executor, _make_tree
    from app.services.plans.plan_models import PlanNode
    from app.services.plans.plan_executor import ExecutionConfig
    from app.services.deep_think import checkpointing
    import app.services.run_resume as resume

    node = PlanNode(id=1, plan_id=1, name="work", instruction="perform work", metadata={"controller_run_id": "source-run"})
    tree = _make_tree(1, [node])
    repo = MagicMock(spec=PlanRepository)
    executor = _make_executor(repo)
    monkeypatch.setattr(executor, "_should_use_deep_think", lambda config: native)
    monkeypatch.setattr(resume, "current_resume_source", lambda: "source-run")
    proof = MagicMock(side_effect=ControllerRestoreError("missing entered scope"))
    monkeypatch.setattr(checkpointing, "ensure_plan_resume_scope", proof)
    with run_scope():
        with pytest.raises(ControllerRestoreError):
            executor._run_task(1, node, tree, ExecutionConfig(enable_skills=False, max_retries=3))
    assert node.metadata["controller_run_id"] == "source-run"
    repo.update_task.assert_not_called()
    assert proof.call_count == (1 if native else 0)


def test_unentered_resume_records_marker_only_after_proof_and_localizes_context(monkeypatch, tmp_path):
    from app.tests.plan.test_plan_executor_deps import _make_executor, _make_tree
    from app.services.plans.plan_models import PlanNode
    from app.services.plans.plan_executor import ExecutionConfig, ExecutionResult
    from app.services.deep_think import checkpointing
    import app.services.run_resume as resume

    node = PlanNode(id=1, plan_id=1, name="work", instruction="perform work")
    tree = _make_tree(1, [node])
    repo = MagicMock(spec=PlanRepository)
    executor = _make_executor(repo)
    monkeypatch.setattr(executor, "_should_use_deep_think", lambda config: True)
    monkeypatch.setattr(resume, "current_resume_source", lambda: "source-run")
    monkeypatch.setattr(executor, "_resolve_task_tool_workspace", lambda *args, **kwargs: ([], str(tmp_path)))
    order = []

    def proof(plan_id, task_id, query, *, previously_entered):
        assert not previously_entered
        assert "controller_run_id" not in node.metadata
        repo.update_task.assert_not_called()
        order.append("proof")

    def run(**kwargs):
        assert kwargs["config"].session_context["resume_scope_entered"] is False
        assert node.metadata["controller_run_id"] == "run-a"
        order.append("run")
        return ExecutionResult(1, 1, "completed", "done")

    monkeypatch.setattr(checkpointing, "ensure_plan_resume_scope", proof)
    monkeypatch.setattr(executor, "_run_task_with_deep_think", run)
    config = ExecutionConfig(enable_skills=False, session_context={"session_id": "example"})
    with run_scope():
        executor._run_task(1, node, tree, config)
    assert order == ["proof", "run"]
    assert "resume_scope_entered" not in config.session_context
