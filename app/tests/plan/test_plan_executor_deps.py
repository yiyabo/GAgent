"""Tests for _infer_missing_dependencies and _resolve_context_paths_from_deps."""
from __future__ import annotations

import json
import logging
from dataclasses import replace
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

from app.config.executor_config import ExecutorSettings, get_executor_settings
from app.services.plans.plan_executor import ExecutionConfig, ExecutionResponse, ExecutionResult, PlanExecutor
from app.services.plans.artifact_contracts import canonical_artifact_path, infer_artifact_namespace, load_artifact_manifest, resolve_manifest_aliases, save_artifact_manifest
from app.services.plans.dependency_validation import normalize_plan_dependencies
from app.services.plans.plan_models import PlanNode, PlanTree


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tree(plan_id: int, nodes: List[PlanNode]) -> PlanTree:
    tree = PlanTree(id=plan_id, title=f"Test Plan {plan_id}")
    for node in nodes:
        tree.nodes[node.id] = node
    tree.rebuild_adjacency()
    return tree


def _make_executor(repo=None, *, artifact_backfill_enabled: bool = False) -> PlanExecutor:
    """Create a PlanExecutor with a mock repo and stub LLM.

    Pass ``artifact_backfill_enabled=True`` to exercise the legacy compatibility
    path where runtime filesystem scans may publish inferred aliases.
    """
    if repo is None:
        repo = MagicMock()
    settings = replace(
        get_executor_settings(),
        artifact_backfill_enabled=artifact_backfill_enabled,
    )
    return PlanExecutor(
        repo=repo,
        llm_service=MagicMock(),
        settings=settings,
    )


# ---------------------------------------------------------------------------
# Part B: mock unit tests (pure logic)
# ---------------------------------------------------------------------------

class TestInferMissingDepsLogic:
    """Test _infer_missing_dependencies with mock repo (pure logic)."""

    def test_builds_producer_map(self):
        """Producer map should be built from acceptance_criteria file_exists/file_nonempty checks."""
        producer = PlanNode(
            id=21, plan_id=1, name="Producer",
            metadata={
                "acceptance_criteria": {
                    "checks": [
                        {"type": "file_exists", "path": "intro_outline.json"},
                        {"type": "file_nonempty", "path": "intro_outline.json"},
                    ]
                }
            },
        )
        consumer = PlanNode(
            id=22, plan_id=1, name="Consumer",
            metadata={"paper_context_paths": ["intro_outline.json"]},
            dependencies=[],
        )
        tree = _make_tree(1, [producer, consumer])

        # After update_task, return a tree with the new dependency
        updated_consumer = consumer.model_copy(update={"dependencies": [21]})
        updated_tree = _make_tree(1, [producer, updated_consumer])

        repo = MagicMock()
        repo.get_plan_tree.return_value = updated_tree
        executor = _make_executor(repo)

        result = executor._infer_missing_dependencies(tree)

        repo.update_task.assert_called_once()
        call_args = repo.update_task.call_args
        assert call_args[0] == (1, 22)  # plan_id, task_id
        assert 21 in call_args[1]["dependencies"]

    def test_adds_producer(self):
        """Consumer missing producer dependency should trigger update_task."""
        producer = PlanNode(
            id=10, plan_id=1, name="Producer",
            metadata={
                "acceptance_criteria": {
                    "checks": [{"type": "file_exists", "path": "output.json"}]
                }
            },
        )
        consumer = PlanNode(
            id=20, plan_id=1, name="Consumer",
            metadata={"paper_context_paths": ["output.json"]},
            dependencies=[5],  # has dep on 5, but not 10
        )
        tree = _make_tree(1, [producer, consumer])

        updated_consumer = consumer.model_copy(update={"dependencies": [5, 10]})
        updated_tree = _make_tree(1, [producer, updated_consumer])
        repo = MagicMock()
        repo.get_plan_tree.return_value = updated_tree
        executor = _make_executor(repo)

        result = executor._infer_missing_dependencies(tree)

        repo.update_task.assert_called_once()
        assert 10 in repo.update_task.call_args[1]["dependencies"]

    def test_no_duplicate(self):
        """If producer is already in dependencies, update_task should NOT be called."""
        producer = PlanNode(
            id=10, plan_id=1, name="Producer",
            metadata={
                "acceptance_criteria": {
                    "checks": [{"type": "file_exists", "path": "output.json"}]
                }
            },
        )
        consumer = PlanNode(
            id=20, plan_id=1, name="Consumer",
            metadata={"paper_context_paths": ["output.json"]},
            dependencies=[10],  # already depends on producer
        )
        tree = _make_tree(1, [producer, consumer])
        repo = MagicMock()
        executor = _make_executor(repo)

        result = executor._infer_missing_dependencies(tree)

        repo.update_task.assert_not_called()
        assert result is tree  # unchanged

    def test_skip_self(self):
        """Producer == consumer should not add self-dependency."""
        node = PlanNode(
            id=10, plan_id=1, name="Self-referencing",
            metadata={
                "acceptance_criteria": {
                    "checks": [{"type": "file_exists", "path": "output.json"}]
                },
                "paper_context_paths": ["output.json"],
            },
            dependencies=[],
        )
        tree = _make_tree(1, [node])
        repo = MagicMock()
        executor = _make_executor(repo)

        result = executor._infer_missing_dependencies(tree)

        repo.update_task.assert_not_called()
        assert result is tree

    def test_multiple_producers_warns_and_does_not_guess(self, caplog):
        """Multiple producers for same basename should be ambiguous, not guessed by task id."""
        producer_a = PlanNode(
            id=10, plan_id=1, name="Producer A",
            metadata={
                "acceptance_criteria": {
                    "checks": [{"type": "file_exists", "path": "evidence.md"}]
                }
            },
        )
        producer_b = PlanNode(
            id=20, plan_id=1, name="Producer B",
            metadata={
                "acceptance_criteria": {
                    "checks": [{"type": "file_nonempty", "path": "evidence.md"}]
                }
            },
        )
        consumer = PlanNode(
            id=30, plan_id=1, name="Consumer",
            metadata={"paper_context_paths": ["evidence.md"]},
            dependencies=[],
        )
        tree = _make_tree(1, [producer_a, producer_b, consumer])

        repo = MagicMock()
        executor = _make_executor(repo)

        with caplog.at_level(logging.WARNING):
            result = executor._infer_missing_dependencies(tree)

        assert any("Multiple producers" in msg for msg in caplog.messages)
        repo.update_task.assert_not_called()
        assert result is tree

    def test_failure_returns_original_tree(self):
        """Internal exception should return original tree unchanged."""
        tree = _make_tree(1, [
            PlanNode(
                id=10, plan_id=1, name="Node",
                metadata={"paper_context_paths": ["file.json"]},
            ),
        ])
        repo = MagicMock()
        repo.update_task.side_effect = RuntimeError("DB error")
        executor = _make_executor(repo)

        result = executor._infer_missing_dependencies(tree)

        assert result is tree  # unchanged


# ---------------------------------------------------------------------------
# Part B: integration tests (real SQLite + PlanRepository)
# ---------------------------------------------------------------------------


class TestDependencyValidation:
    def test_child_parent_dependency_is_removed(self):
        root = PlanNode(id=1, plan_id=1, name="Root")
        child = PlanNode(id=2, plan_id=1, name="Child", parent_id=1, dependencies=[1])
        tree = _make_tree(1, [root, child])

        normalization = normalize_plan_dependencies(tree)

        assert normalization.dependencies_by_task[2] == []
        assert any(issue.code == "ancestor_dependency" for issue in normalization.issues)

    def test_composite_dependency_expands_to_leaf_children(self):
        root = PlanNode(id=1, plan_id=1, name="Root")
        composite = PlanNode(id=2, plan_id=1, name="Composite", parent_id=1)
        leaf_a = PlanNode(id=3, plan_id=1, name="Leaf A", parent_id=2)
        leaf_b = PlanNode(id=4, plan_id=1, name="Leaf B", parent_id=2)
        consumer = PlanNode(id=5, plan_id=1, name="Consumer", parent_id=1, dependencies=[2])
        tree = _make_tree(1, [root, composite, leaf_a, leaf_b, consumer])

        normalization = normalize_plan_dependencies(tree)

        assert normalization.dependencies_by_task[5] == [3, 4]
        assert any(issue.code == "composite_dependency_expanded" for issue in normalization.issues)

    def test_cycle_edge_is_removed(self):
        a = PlanNode(id=1, plan_id=1, name="A", dependencies=[2])
        b = PlanNode(id=2, plan_id=1, name="B", dependencies=[1])
        tree = _make_tree(1, [a, b])

        normalization = normalize_plan_dependencies(tree)

        assert normalization.dependencies_by_task
        assert any(issue.code == "dependency_cycle" for issue in normalization.issues)

class TestInferMissingDepsIntegration:
    """Integration tests using real SQLite database and PlanRepository."""

    @pytest.fixture
    def plan_env(self, tmp_path, monkeypatch):
        """Set up a real plan database in a temp directory."""
        from app.config.database_config import reset_database_config
        from app.database_pool import initialize_connection_pool, close_connection_pool

        # Point DB_ROOT to temp directory so all databases are isolated
        db_root = str(tmp_path / "databases")
        monkeypatch.setenv("DB_ROOT", db_root)
        reset_database_config()

        from app.config.database_config import get_database_config
        config = get_database_config()

        # Reinitialize connection pool to point to temp main DB
        main_db_path = config.get_main_db_path()
        initialize_connection_pool(db_path=main_db_path)

        # Initialize main database tables
        from app.database import get_db
        with get_db() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS plans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    owner TEXT,
                    description TEXT,
                    metadata TEXT,
                    plan_db_path TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS plan_decomposition_job_index (
                    job_id TEXT PRIMARY KEY,
                    plan_id INTEGER,
                    job_type TEXT,
                    owner_id TEXT,
                    session_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

        from app.repository.plan_repository import PlanRepository
        repo = PlanRepository()
        yield repo

        # Cleanup
        close_connection_pool()
        reset_database_config()

    def test_persisted_and_verified(self, plan_env, caplog):
        """Inferred dependency should be persisted to task_dependencies table."""
        repo = plan_env

        from app.repository.plan_storage import initialize_plan_database, get_plan_db_path
        from app.database import get_db, plan_db_connection

        # Register plan in main DB
        with get_db() as conn:
            conn.execute(
                "INSERT INTO plans (id, title, plan_db_path) VALUES (?, ?, ?)",
                (99, "Integration Test", "plan_99.sqlite"),
            )

        initialize_plan_database(99, title="Integration Test")

        db_path = get_plan_db_path(99)
        with plan_db_connection(db_path) as conn:
            conn.execute(
                "INSERT INTO tasks (id, name, status, metadata) VALUES (?, ?, ?, ?)",
                (1, "Producer", "completed", json.dumps({
                    "acceptance_criteria": {
                        "checks": [{"type": "file_exists", "path": "outline.json"}]
                    }
                })),
            )
            conn.execute(
                "INSERT INTO tasks (id, name, status, metadata) VALUES (?, ?, ?, ?)",
                (2, "Consumer", "pending", json.dumps({
                    "paper_context_paths": ["outline.json"]
                })),
            )

        executor = _make_executor(repo)

        with caplog.at_level(logging.INFO):
            result = executor._infer_missing_dependencies(repo.get_plan_tree(99))

        assert 1 in result.nodes[2].dependencies
        assert any("Inferred dependency: task 2 -> task 1" in msg for msg in caplog.messages)

    def test_cycle_rejected_and_logged(self, plan_env, caplog):
        """Cyclic dependency should be rejected by repo and logged as warning."""
        repo = plan_env

        from app.repository.plan_storage import initialize_plan_database, get_plan_db_path
        from app.database import get_db, plan_db_connection

        with get_db() as conn:
            conn.execute(
                "INSERT INTO plans (id, title, plan_db_path) VALUES (?, ?, ?)",
                (100, "Cycle Test", "plan_100.sqlite"),
            )

        initialize_plan_database(100, title="Cycle Test")

        db_path = get_plan_db_path(100)
        with plan_db_connection(db_path) as conn:
            conn.execute(
                "INSERT INTO tasks (id, name, status, metadata) VALUES (?, ?, ?, ?)",
                (1, "Task A", "pending", json.dumps({
                    "acceptance_criteria": {
                        "checks": [{"type": "file_exists", "path": "a_output.json"}]
                    },
                    "paper_context_paths": ["b_output.json"],
                })),
            )
            conn.execute(
                "INSERT INTO tasks (id, name, status, metadata) VALUES (?, ?, ?, ?)",
                (2, "Task B", "pending", json.dumps({
                    "acceptance_criteria": {
                        "checks": [{"type": "file_exists", "path": "b_output.json"}]
                    },
                    "paper_context_paths": ["a_output.json"],
                })),
            )
            # A already depends on B
            conn.execute(
                "INSERT INTO task_dependencies (task_id, depends_on) VALUES (?, ?)",
                (1, 2),
            )

        executor = _make_executor(repo)
        tree = repo.get_plan_tree(100)

        with caplog.at_level(logging.WARNING):
            result = executor._infer_missing_dependencies(tree)

        # Task B -> Task A would create a cycle (A already depends on B)
        node_b = result.nodes[2]
        assert 1 not in node_b.dependencies, "Cyclic dependency should have been rejected"
        assert any("rejected" in msg.lower() for msg in caplog.messages)


# ---------------------------------------------------------------------------
# Part C: _resolve_context_paths_from_deps unit tests
# ---------------------------------------------------------------------------

class TestResolveContextPathsFromDeps:
    """Test _resolve_context_paths_from_deps static method."""

    def test_basename_match(self):
        """Relative path should be replaced by matching absolute artifact path."""
        result = PlanExecutor._resolve_context_paths_from_deps(
            context_paths=["outline.json"],
            dep_artifacts=[(10, ["/abs/path/to/outline.json"])],
        )
        assert result == ["/abs/path/to/outline.json"]

    def test_absolute_preserved(self):
        """Absolute paths should be kept as-is."""
        result = PlanExecutor._resolve_context_paths_from_deps(
            context_paths=["/abs/path/file.md"],
            dep_artifacts=[(10, ["/other/file.md"])],
        )
        assert result == ["/abs/path/file.md"]

    def test_no_match_preserved(self):
        """Unmatched relative paths should be preserved."""
        result = PlanExecutor._resolve_context_paths_from_deps(
            context_paths=["missing.json"],
            dep_artifacts=[(10, ["/abs/path/other.json"])],
        )
        assert result == ["missing.json"]

    def test_conflict_preserves_unresolved_basename(self, caplog):
        """When multiple deps have the same basename, do not guess a producer."""
        result = PlanExecutor._resolve_context_paths_from_deps(
            context_paths=["outline.json"],
            dep_artifacts=[
                (10, ["/dep10/results/outline.json"]),
                (20, ["/dep20/results/outline.json"]),
            ],
        )
        assert result == ["outline.json"]

    def test_same_dep_multiple_artifacts_preserves_ambiguous_basename(self):
        """Same dep with duplicate basenames is ambiguous without an exact path."""
        result = PlanExecutor._resolve_context_paths_from_deps(
            context_paths=["data.json"],
            dep_artifacts=[
                (10, [
                    "/run/results/data.json",
                    "/run/results/deliverable/data.json",
                ]),
            ],
        )
        assert result == ["data.json"]

    def test_same_dep_results_over_unknown_preserves_ambiguous_basename(self):
        """Duplicate basename inside one dependency is ambiguous."""
        result = PlanExecutor._resolve_context_paths_from_deps(
            context_paths=["data.json"],
            dep_artifacts=[
                (10, [
                    "/run/data.json",
                    "/run/results/data.json",
                ]),
            ],
        )
        assert result == ["data.json"]

    def test_mixed_paths(self):
        """Mix of absolute, matched relative, and unmatched relative paths."""
        result = PlanExecutor._resolve_context_paths_from_deps(
            context_paths=[
                "/absolute/file.md",
                "outline.json",
                "missing.bib",
            ],
            dep_artifacts=[
                (10, ["/dep10/outline.json"]),
            ],
        )
        assert result == ["/absolute/file.md", "/dep10/outline.json", "missing.bib"]

    def test_empty_context_paths(self):
        """Empty context_paths should return empty list."""
        result = PlanExecutor._resolve_context_paths_from_deps(
            context_paths=[],
            dep_artifacts=[(10, ["/abs/file.json"])],
        )
        assert result == []

    def test_empty_dep_artifacts(self):
        """Empty dep_artifacts should preserve all paths."""
        result = PlanExecutor._resolve_context_paths_from_deps(
            context_paths=["file.json"],
            dep_artifacts=[],
        )
        assert result == ["file.json"]

    def test_same_dep_same_priority_preserves_ambiguous_basename(self):
        """When same dep has duplicate basename artifacts, preserve unresolved path."""
        result = PlanExecutor._resolve_context_paths_from_deps(
            context_paths=["data.json"],
            dep_artifacts=[
                (10, [
                    "/run/results/old/data.json",
                    "/run/results/new/data.json",
                ]),
            ],
        )
        assert result == ["data.json"]

    def test_exact_absolute_path_preserved_for_duplicate_basename(self):
        result = PlanExecutor._resolve_context_paths_from_deps(
            context_paths=["/run/results/new/data.json"],
            dep_artifacts=[
                (10, [
                    "/run/results/old/data.json",
                    "/run/results/new/data.json",
                ]),
            ],
        )
        assert result == ["/run/results/new/data.json"]


class TestArtifactContracts:
    def test_infer_missing_dependencies_uses_artifact_aliases(self):
        producer = PlanNode(
            id=10,
            plan_id=1,
            name="Prepare AI evidence",
            metadata={
                "acceptance_criteria": {
                    "checks": [{"type": "file_exists", "path": "ai_evidence.md"}]
                }
            },
        )
        consumer = PlanNode(
            id=20,
            plan_id=1,
            name="Use AI evidence",
            metadata={"artifact_contract": {"requires": ["ai_dl.evidence_md"]}},
            dependencies=[],
        )
        tree = _make_tree(1, [producer, consumer])

        updated_consumer = consumer.model_copy(update={"dependencies": [10]})
        updated_tree = _make_tree(1, [producer, updated_consumer])
        repo = MagicMock()
        repo.get_plan_tree.return_value = updated_tree
        executor = _make_executor(repo)

        result = executor._infer_missing_dependencies(tree)

        repo.update_task.assert_called_once()
        assert 10 in repo.update_task.call_args[1]["dependencies"]

    def test_execute_task_blocks_when_required_artifact_alias_missing(self):
        producer = PlanNode(
            id=1,
            plan_id=7,
            name="Prepare evidence",
            status="completed",
            metadata={"artifact_contract": {"publishes": ["general.evidence_md"]}},
            execution_result=json.dumps({"status": "completed", "content": "ok"}),
        )
        consumer = PlanNode(
            id=2,
            plan_id=7,
            name="Consume evidence",
            status="pending",
            metadata={"artifact_contract": {"requires": ["general.evidence_md"]}},
            dependencies=[1],
        )
        tree = _make_tree(7, [producer, consumer])
        repo = MagicMock()
        repo.get_plan_tree.return_value = tree
        executor = _make_executor(repo)

        result = executor.execute_task(7, 2, config=ExecutionConfig(session_context={}))

        assert result.status == "skipped"
        assert result.metadata["blocked_by_dependencies"] is True
        assert result.metadata["missing_artifact_aliases"] == ["general.evidence_md"]
        assert result.metadata["producer_task_candidates"]["general.evidence_md"] == [1]

    def test_execute_task_backfills_runtime_artifact_into_canonical_manifest(
        self,
        monkeypatch,
        tmp_path,
    ):
        monkeypatch.chdir(tmp_path)
        runtime_file = (
            tmp_path
            / "runtime"
            / "session_adhoc"
            / "plan7_task1"
            / "run_20260418_000000_demo"
            / "results"
            / "structured_evidence_nmr_cryo_msm.json"
        )
        runtime_file.parent.mkdir(parents=True, exist_ok=True)
        runtime_file.write_text('{"entries": 3}', encoding="utf-8")

        producer = PlanNode(
            id=1,
            plan_id=7,
            name="提取NMR动态结构表征相关证据点",
            status="completed",
            metadata={
                "artifact_contract": {"publishes": ["nmr_cryo_msm.structured_evidence_json"]},
                "acceptance_criteria": {
                    "checks": [
                        {"type": "file_exists", "path": "structured_evidence_nmr_cryo_msm.json"}
                    ]
                }
            },
            execution_result=json.dumps({"status": "completed", "content": "ok"}),
        )
        consumer = PlanNode(
            id=2,
            plan_id=7,
            name="整合 NMR / Cryo-EM / MSM 章节",
            status="pending",
            metadata={"artifact_contract": {"requires": ["nmr_cryo_msm.structured_evidence_json"]}},
            dependencies=[],
        )
        tree = _make_tree(7, [producer, consumer])
        repo = MagicMock()
        repo.get_plan_tree.return_value = tree
        executor = _make_executor(repo, artifact_backfill_enabled=True)
        executor._should_use_deep_think = lambda _config: False
        executor._llm.generate.return_value = ExecutionResponse(
            status="success",
            content="assembled",
            notes=[],
            metadata={},
        )

        result = executor.execute_task(7, 2, config=ExecutionConfig(session_context={}))

        canonical = (
            tmp_path
            / "results"
            / "plans"
            / "plan_7"
            / "nmr_cryo_msm"
            / "structured_evidence.json"
        )
        manifest = (
            tmp_path
            / "results"
            / "plans"
            / "plan_7"
            / "artifacts_manifest.json"
        )

        assert result.status == "completed"
        assert canonical.exists() is True
        assert manifest.exists() is True
        assert (
            result.metadata["resolved_input_artifacts"]["nmr_cryo_msm.structured_evidence_json"]
            == str(canonical.resolve())
        )

    def test_execute_task_does_not_backfill_runtime_artifact_from_failed_producer(
        self,
        monkeypatch,
        tmp_path,
    ):
        monkeypatch.chdir(tmp_path)
        runtime_file = (
            tmp_path
            / "runtime"
            / "session_adhoc"
            / "plan7_task1"
            / "run_20260418_000000_demo"
            / "results"
            / "structured_evidence_nmr_cryo_msm.json"
        )
        runtime_file.parent.mkdir(parents=True, exist_ok=True)
        runtime_file.write_text('{"entries": 3}', encoding="utf-8")

        producer = PlanNode(
            id=1,
            plan_id=7,
            name="提取NMR动态结构表征相关证据点",
            status="failed",
            metadata={
                "acceptance_criteria": {
                    "checks": [
                        {"type": "file_exists", "path": "structured_evidence_nmr_cryo_msm.json"}
                    ]
                }
            },
            execution_result=json.dumps({"status": "failed", "content": "transport error"}),
        )
        consumer = PlanNode(
            id=2,
            plan_id=7,
            name="整合 NMR / Cryo-EM / MSM 章节",
            status="pending",
            metadata={"artifact_contract": {"requires": ["nmr_cryo_msm.structured_evidence_json"]}},
            dependencies=[],
        )
        tree = _make_tree(7, [producer, consumer])
        repo = MagicMock()
        repo.get_plan_tree.return_value = tree
        executor = _make_executor(repo, artifact_backfill_enabled=True)

        result = executor.execute_task(7, 2, config=ExecutionConfig(session_context={}))

        canonical = (
            tmp_path
            / "results"
            / "plans"
            / "plan_7"
            / "nmr_cryo_msm"
            / "structured_evidence.json"
        )

        assert result.status == "skipped"
        assert result.metadata["blocked_by_dependencies"] is True
        assert result.metadata["missing_artifact_aliases"] == ["nmr_cryo_msm.structured_evidence_json"]
        assert canonical.exists() is False

    def test_execute_plan_publishes_semantic_evidence_markdown_for_explicit_alias(
        self,
        monkeypatch,
        tmp_path,
    ):
        monkeypatch.chdir(tmp_path)
        runtime_file = (
            tmp_path
            / "runtime"
            / "session_task9"
            / "raw_files"
            / "task_1"
            / "task_2"
            / "task_9"
            / "ncAA_abstract_evidence_summary.md"
        )
        runtime_file.parent.mkdir(parents=True, exist_ok=True)
        runtime_file.write_text("# Key evidence\n", encoding="utf-8")

        node = PlanNode(
            id=1,
            plan_id=7,
            name="Gather key evidence for abstract",
            status="completed",
            metadata={"artifact_contract": {"publishes": ["general.evidence_md"]}},
        )
        repo = MagicMock()
        executor = _make_executor(repo)
        payload = {
            "status": "completed",
            "content": "ok",
            "metadata": {"artifact_paths": [str(runtime_file)]},
        }

        finalization = executor._task_verifier.finalize_payload(
            node,
            payload,
            execution_status="completed",
        )
        finalization, _ = executor._materialize_finalization(
            7,
            node,
            finalization,
            session_context={},
        )

        canonical = canonical_artifact_path(7, "general.evidence_md")
        assert canonical is not None
        manifest = tmp_path / "results" / "plans" / "plan_7" / "artifacts_manifest.json"

        assert finalization.final_status == "completed"
        assert canonical.exists() is True
        assert canonical.read_text(encoding="utf-8") == "# Key evidence\n"
        assert manifest.exists() is True
        assert finalization.payload["status"] == "completed"
        assert finalization.payload["metadata"]["artifact_authority"]["published_aliases"] == ["general.evidence_md"]

    def test_execute_plan_does_not_skip_false_completed_task_missing_canonical_publish(
        self,
        monkeypatch,
        tmp_path,
    ):
        monkeypatch.chdir(tmp_path)
        node = PlanNode(
            id=1,
            plan_id=7,
            name="Produce evidence",
            status="completed",
            metadata={"artifact_contract": {"publishes": ["general.evidence_md"]}},
            execution_result=json.dumps({"status": "completed", "content": "ok"}),
        )
        tree = _make_tree(7, [node])
        repo = MagicMock()
        repo.get_plan_tree.return_value = tree
        executor = _make_executor(repo)
        calls: list[int] = []

        monkeypatch.setattr(executor, "_infer_missing_dependencies", lambda current_tree: current_tree)
        monkeypatch.setattr(
            executor,
            "_run_task",
            lambda _plan_id, current_node, _tree, _cfg: calls.append(current_node.id)
            or ExecutionResult(
                plan_id=_plan_id,
                task_id=current_node.id,
                status="completed",
                content="ok",
            ),
        )

        summary = executor.execute_plan(7, config=ExecutionConfig(session_context={}))

        assert calls == [1]
        assert summary.executed_task_ids == [1]

    def test_run_task_blocks_dependency_with_failed_effective_status(
        self,
        monkeypatch,
        tmp_path,
    ):
        monkeypatch.chdir(tmp_path)
        dep = PlanNode(
            id=1,
            plan_id=7,
            name="Upstream",
            status="completed",
            execution_result=json.dumps({
                "status": "completed",
                "content": "bad output",
                "metadata": {"verification_status": "failed"},
            }),
        )
        node = PlanNode(
            id=2,
            plan_id=7,
            name="Downstream",
            status="pending",
            dependencies=[1],
        )
        tree = _make_tree(7, [dep, node])
        repo = MagicMock()
        executor = _make_executor(repo)

        result = executor._run_task(
            7,
            node,
            tree,
            ExecutionConfig(session_context={}, enforce_dependencies=True),
        )

        assert result.status == "skipped"
        assert result.metadata["incomplete_dependencies"] == [1]
        assert result.metadata["incomplete_dependency_info"] == [
            {"id": 1, "name": "Upstream", "status": "completed"}
        ]
        assert "#1(failed)" in result.content


def test_save_artifact_manifest_merges_existing_artifacts(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    save_artifact_manifest(
        123,
        {
            "plan_id": 123,
            "artifacts": {
                "general.evidence_md": {
                    "alias": "general.evidence_md",
                    "path": "/tmp/evidence.md",
                    "producer_task_id": 1,
                }
            },
        },
    )
    save_artifact_manifest(
        123,
        {
            "plan_id": 123,
            "artifacts": {
                "general.references_bib": {
                    "alias": "general.references_bib",
                    "path": "/tmp/references.bib",
                    "producer_task_id": 2,
                }
            },
        },
    )

    manifest = load_artifact_manifest(123)

    assert set(manifest["artifacts"]) == {
        "general.evidence_md",
        "general.references_bib",
    }


def test_contract_repair_query_prefers_runtime_artifacts_over_external_copy():
    node = PlanNode(
        id=13,
        plan_id=99,
        name="Identify cluster-specific marker genes",
    )

    query = PlanExecutor._build_contract_repair_query(
        node=node,
        attempt=1,
        contract_diff={
            "expected_deliverables": [
                "/home/zczhao/GAgent_backup_20260421_233939/data/ovarian_cancer_annotated.h5ad"
            ],
            "missing_required_outputs": [
                "/home/zczhao/GAgent_backup_20260421_233939/data/ovarian_cancer_annotated.h5ad"
            ],
            "actual_outputs": [
                "runtime/session_test/raw_files/task_1/task_3/task_13/ovarian_cancer_annotated.h5ad"
            ],
        },
    )

    assert "runtime/workspace artifact exists" in query
    assert "instead of copying it to an external absolute path" in query
    assert "do not copy large artifacts" in query
    assert "Create the exact missing required output files" not in query

    def test_materialize_finalization_publishes_inferred_alias_when_explicit_contract_empty(
        self,
        monkeypatch,
        tmp_path,
    ):
        monkeypatch.chdir(tmp_path)
        runtime_file = (
            tmp_path
            / "runtime"
            / "session_test"
            / "raw_files"
            / "task_1"
            / "evidence.md"
        )
        runtime_file.parent.mkdir(parents=True, exist_ok=True)
        runtime_file.write_text("# Recent review evidence\n", encoding="utf-8")

        node = PlanNode(
            id=1,
            plan_id=7,
            name="Collect recent review papers",
            status="completed",
            metadata={
                "artifact_contract": {"requires": [], "publishes": []},
                "acceptance_criteria": {
                    "checks": [
                        {"type": "file_nonempty", "path": "evidence.md"},
                    ]
                },
            },
        )
        repo = MagicMock()
        executor = _make_executor(repo, artifact_backfill_enabled=False)
        payload = {
            "status": "completed",
            "content": "ok",
            "metadata": {"artifact_paths": [str(runtime_file)]},
        }

        finalization = executor._task_verifier.finalize_payload(
            node,
            payload,
            execution_status="completed",
        )
        finalization, _ = executor._materialize_finalization(
            7,
            node,
            finalization,
            session_context={},
        )

        canonical = canonical_artifact_path(7, "general.evidence_md")
        assert canonical is not None
        assert canonical.exists() is True
        assert canonical.read_text(encoding="utf-8") == "# Recent review evidence\n"
        published = finalization.payload["metadata"]["published_artifacts"]
        assert published["general.evidence_md"]["producer_task_id"] == 1

    def test_infer_artifact_namespace_does_not_match_ai_inside_other_words(self):
        assert infer_artifact_namespace("Train and evaluate baseline", "") == "general"
        assert infer_artifact_namespace("AI-driven classifier", "") == "ai_dl"


    def test_resolve_required_artifacts_skips_backfill_when_flag_off(
        self,
        monkeypatch,
        tmp_path,
    ):
        """When artifact_backfill_enabled=False (default), _resolve_required_artifacts
        does NOT call _backfill_task_artifacts for dependency nodes."""
        monkeypatch.chdir(tmp_path)

        producer = PlanNode(
            id=1,
            plan_id=7,
            name="Produce evidence",
            status="completed",
            metadata={"artifact_contract": {"publishes": ["general.evidence_md"]}},
            execution_result=json.dumps({"status": "completed", "content": "ok"}),
        )
        consumer = PlanNode(
            id=2,
            plan_id=7,
            name="Consume evidence",
            status="pending",
            metadata={"artifact_contract": {"requires": ["general.evidence_md"]}},
            dependencies=[1],
        )
        tree = _make_tree(7, [producer, consumer])
        repo = MagicMock()
        repo.get_plan_tree.return_value = tree

        # Default: artifact_backfill_enabled=False
        executor = _make_executor(repo, artifact_backfill_enabled=False)

        backfill_calls: list[int] = []
        original_backfill = executor._backfill_task_artifacts

        def _tracking_backfill(plan_id, node, manifest, session_context):
            backfill_calls.append(node.id)
            return original_backfill(plan_id, node, manifest, session_context)

        monkeypatch.setattr(executor, "_backfill_task_artifacts", _tracking_backfill)

        _contract, _resolved, _missing, _producers = executor._resolve_required_artifacts(
            7,
            consumer,
            dependencies=[producer],
            tree=tree,
            session_context={},
        )

        # No backfill calls should have been made for the producer
        assert 1 not in backfill_calls

    def test_resolve_required_artifacts_backfills_when_flag_on(
        self,
        monkeypatch,
        tmp_path,
    ):
        """When artifact_backfill_enabled=True, _resolve_required_artifacts
        DOES call _backfill_task_artifacts for completed dependency nodes."""
        monkeypatch.chdir(tmp_path)

        # Create the canonical artifact so backfill can find it
        evidence_dir = tmp_path / "results" / "plans" / "plan_7" / "general"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        (evidence_dir / "evidence.md").write_text("# Evidence\n", encoding="utf-8")

        # Producer has NO explicit publish contract (so status resolver won't demote it)
        # but the consumer requires the alias, and the producer's execution_result
        # contains the artifact path so backfill can discover it.
        producer = PlanNode(
            id=1,
            plan_id=7,
            name="Produce evidence",
            status="completed",
            metadata={},
            execution_result=json.dumps({
                "status": "completed",
                "content": "ok",
                "metadata": {"artifact_paths": [str(evidence_dir / "evidence.md")]},
            }),
        )
        consumer = PlanNode(
            id=2,
            plan_id=7,
            name="Consume evidence",
            status="pending",
            metadata={"artifact_contract": {"requires": ["general.evidence_md"]}},
            dependencies=[1],
        )
        tree = _make_tree(7, [producer, consumer])
        repo = MagicMock()
        repo.get_plan_tree.return_value = tree

        executor = _make_executor(repo, artifact_backfill_enabled=True)

        backfill_calls: list[int] = []
        original_backfill = executor._backfill_task_artifacts

        def _tracking_backfill(plan_id, node, manifest, session_context):
            backfill_calls.append(node.id)
            return original_backfill(plan_id, node, manifest, session_context)

        monkeypatch.setattr(executor, "_backfill_task_artifacts", _tracking_backfill)

        _contract, _resolved, _missing, _producers = executor._resolve_required_artifacts(
            7,
            consumer,
            dependencies=[producer],
            tree=tree,
            session_context={},
        )

        # Backfill should have been called for the producer
        assert 1 in backfill_calls



def test_resolve_required_artifacts_ignores_invalid_manifest_entry(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    producer = PlanNode(
        id=1,
        plan_id=7,
        name="Produce kmer",
        status="completed",
        metadata={"artifact_contract": {"publishes": ["phage_ml.kmer_features_npz"]}},
    )
    consumer = PlanNode(
        id=2,
        plan_id=7,
        name="Consume kmer",
        status="pending",
        metadata={"artifact_contract": {"requires": ["phage_ml.kmer_features_npz"]}},
        dependencies=[1],
    )
    tree = _make_tree(7, [producer, consumer])
    repo = MagicMock()
    executor = _make_executor(repo)

    bad_artifact = tmp_path / "bad_kmer.npz"
    bad_artifact.write_bytes(b"not a sparse npz")
    save_artifact_manifest(
        7,
        {
            "plan_id": 7,
            "artifacts": {
                "phage_ml.kmer_features_npz": {
                    "alias": "phage_ml.kmer_features_npz",
                    "path": str(bad_artifact),
                    "producer_task_id": 1,
                    "validation": {"validated": False, "schema_valid": False},
                }
            },
        },
    )

    _contract, resolved, missing, _producers = executor._resolve_required_artifacts(
        7,
        consumer,
        dependencies=[producer],
        tree=tree,
        session_context={},
    )

    assert resolved == {}
    assert missing == ["phage_ml.kmer_features_npz"]


def test_resolve_manifest_aliases_accepts_dynamic_directory_artifact(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    alias = "analysis_ccc.evidence_dataframes"
    data_dir = tmp_path / "results" / "evidence_data"
    data_dir.mkdir(parents=True)
    (data_dir / "lr_pairs.tsv").write_text("ligand\treceptor\nMIF\tCD74\n", encoding="utf-8")
    manifest = {
        "plan_id": 7,
        "artifacts": {
            alias: {
                "alias": alias,
                "path": str(data_dir.resolve()),
                "producer_task_id": 3,
                "validation": {"validated": True, "kind": "directory"},
            }
        },
    }

    assert canonical_artifact_path(7, alias) == tmp_path / "results" / "plans" / "plan_7" / "analysis_ccc" / "evidence_dataframes"
    assert resolve_manifest_aliases(manifest, [alias]) == {alias: str(data_dir.resolve())}



def test_enrich_finalized_payload_exposes_contract_artifacts_as_published(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    artifact = tmp_path / "run" / "qc_results" / "filtered_adata.h5ad"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"large-artifact-record")
    node = PlanNode(id=11, plan_id=96, name="Load QC-filtered AnnData object", status="completed")
    executor = _make_executor(MagicMock())

    payload = {
        "status": "completed",
        "metadata": {
            "contract_artifacts": [
                {
                    "expected": "qc_results/filtered_adata.h5ad",
                    "path": str(artifact),
                    "size": artifact.stat().st_size,
                    "exists": True,
                }
            ]
        },
    }

    enriched = executor._enrich_finalized_payload_with_artifacts(
        plan_id=96,
        node=node,
        payload=payload,
        final_status="completed",
        session_context={},
    )

    published = enriched["metadata"].get("published_artifacts")
    assert "contract:qc_results/filtered_adata.h5ad" in published
    entry = published["contract:qc_results/filtered_adata.h5ad"]
    assert entry["path"] == str(artifact.resolve())
    assert entry["source"] == "contract_artifacts"
    manifest = load_artifact_manifest(96)
    manifest_entry = manifest["artifacts"]["contract:qc_results/filtered_adata.h5ad"]
    assert manifest_entry["path"] == str(artifact.resolve())
    assert manifest_entry["producer_task_id"] == 11



def test_enrich_finalized_payload_records_missing_contract_artifacts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    missing = tmp_path / "run" / "qc_results" / "filtered_adata.h5ad"
    node = PlanNode(id=11, plan_id=96, name="Load QC-filtered AnnData object", status="completed")
    executor = _make_executor(MagicMock())

    payload = {
        "status": "completed",
        "metadata": {
            "contract_artifacts": [
                {
                    "expected": "qc_results/filtered_adata.h5ad",
                    "path": str(missing),
                    "exists": True,
                }
            ]
        },
    }

    enriched = executor._enrich_finalized_payload_with_artifacts(
        plan_id=96,
        node=node,
        payload=payload,
        final_status="completed",
        session_context={},
    )

    assert enriched["metadata"].get("published_artifacts") is None
    assert enriched["metadata"]["missing_contract_artifacts"] == [
        {
            "expected": "qc_results/filtered_adata.h5ad",
            "path": str(missing),
            "reason": "not_found",
        }
    ]
