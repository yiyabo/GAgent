"""Artifact manifest / backfill / publish cluster of ``PlanExecutor``.

Moved verbatim out of ``plan_executor.py`` per
``design/2026-09-24-backend-godfiles-refactor-plan.md`` §4.6 (cluster ④, the
~1.4k-line gravity centre of the file): execution persistence, stale-skip
recovery, workspace-artifact promotion, finalization materialization, DAG
execution order + dependency resolution, artifact-manifest read/write,
artifact backfill, required-artifact resolution, the two block branches,
finalized-payload enrichment and contract-deliverable publishing, path
classification/extraction, dependency artifact context and output directories.

The methods are composed into ``PlanExecutor`` as ``_ArtifactMethods`` (the
``deliverables.publisher`` mixin precedent), so every ``self.*`` call site —
inside this module and in the facade — is unchanged.

Patched-name handling (monkeypatch surface):

- ``publish_artifact`` and ``save_artifact_manifest`` are patched **on the
  facade module** by ``app/tests/plan/test_plan_executor_artifact_enrichment.py``
  (``monkeypatch.setattr(plan_executor_module, ...)``).  They are therefore
  reached through late-bound module-level proxies below, which forward to the
  facade's current binding at call time (the deep_think ``_dta()`` idiom).
  Call sites inside the moved bodies stay byte-verbatim.
- ``_log_job`` / ``_coerce_blocked_dependency_payload`` are not used here.
- ``ArtifactEvent``, ``get_registry_projector`` and ``get_runtime_session_dir``
  keep their original function-local imports.

Otherwise no body deviates from the pre-split code, and the module uses its own
``logging.getLogger(__name__)`` (split precedent).  One whitespace-only
deviation: a line inside ``_enrich_finalized_payload_with_artifacts`` that
carried 20 trailing spaces now carries none (behaviour-neutral; keeps
``git diff --check`` clean).
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .artifact_contracts import (
    aliases_for_file_name,
    aliases_for_path_text,
    artifact_manifest_path,
    canonical_artifact_path,
    canonicalize_artifact_alias,
    extend_contract_with_runtime_candidates,
    find_candidate_source_for_alias,
    find_runtime_candidates,
    infer_artifact_contract,
    infer_artifact_namespace,
    load_artifact_manifest,
    producer_candidates_for_alias,
    published_artifact_paths_for_task,
    resolve_artifact_contract_with_provenance,
    resolve_manifest_aliases,
)
from .executor_models import ExecutionResult
from .executor_text_utils import (
    _NON_DELIVERABLE_WORKSPACE_RE,
    _PATH_LIKE_RE,
    _is_non_canonical_runtime_path,
)
from .plan_models import PlanNode, PlanTree
from .task_verification import TaskVerificationService, VerificationFinalization

logger = logging.getLogger(__name__)


def _facade() -> Any:
    """Late-bound plan_executor facade module (monkeypatch-friendly lookups)."""
    from . import plan_executor

    return plan_executor


def publish_artifact(*args: Any, **kwargs: Any) -> Any:
    """Proxy: resolve ``publish_artifact`` on the facade at call time."""
    return _facade().publish_artifact(*args, **kwargs)


def save_artifact_manifest(*args: Any, **kwargs: Any) -> Any:
    """Proxy: resolve ``save_artifact_manifest`` on the facade at call time."""
    return _facade().save_artifact_manifest(*args, **kwargs)


class _ArtifactMethods:
    """Artifact/backfill/publish cluster of ``PlanExecutor`` (mixin)."""

    def _persist_execution(
        self,
        plan_id: int,
        task_id: int,
        payload: Dict[str, Any],
        *,
        status: Optional[str] = None,
    ) -> None:
        serialized = json.dumps(payload, ensure_ascii=False)
        try:
            self._repo.update_task(
                plan_id,
                task_id,
                execution_result=serialized,
                status=status,
            )
        except Exception:
            logger.exception(
                "Failed to persist execution result for plan %s task %s",
                plan_id,
                task_id,
            )
            raise

        # When a task completes successfully, clear stale "skipped" status on
        # downstream tasks that were blocked by this task's previous failure.
        # Without this, re-executing a failed task leaves its dependents stuck
        # in "skipped" even though the dependency is now satisfied.
        if status in ("completed", "done", "success"):
            self._clear_stale_skipped_dependents(plan_id, task_id)

    def _clear_stale_skipped_dependents(
        self,
        plan_id: int,
        completed_task_id: int,
    ) -> None:
        """Reset downstream tasks stuck in 'skipped' because this task previously failed.

        When a task is re-executed and succeeds, any direct dependents that were
        skipped due to ``blocked_by_dependencies`` should be reset to ``pending``
        so they can be re-executed.
        """
        try:
            tree = self._repo.get_plan_tree(plan_id)
        except Exception:
            return

        for node in tree.nodes.values():
            if str(getattr(node, "status", "") or "").strip().lower() != "skipped":
                continue
            deps = getattr(node, "dependencies", []) or []
            if completed_task_id not in [int(d) for d in deps if str(d).strip().isdigit()]:
                continue

            # Confirm it was blocked by dependencies (not skipped for other reasons)
            exec_result = getattr(node, "execution_result", None)
            if isinstance(exec_result, str):
                try:
                    exec_data = json.loads(exec_result)
                except (json.JSONDecodeError, TypeError):
                    exec_data = {}
            else:
                exec_data = exec_result if isinstance(exec_result, dict) else {}
            meta = exec_data.get("metadata", {}) if isinstance(exec_data, dict) else {}
            if not (isinstance(meta, dict) and meta.get("blocked_by_dependencies")):
                continue

            try:
                self._repo.update_task(plan_id, node.id, status="pending", execution_result="")
                logger.info(
                    "[TASK_RECOVERY] Reset skipped task %s to pending "
                    "(dependency task %s now completed, plan %s)",
                    node.id,
                    completed_task_id,
                    plan_id,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to reset skipped task %s for plan %s: %s",
                    node.id,
                    plan_id,
                    exc,
                )

    def _promote_workspace_artifacts_to_task_dir(
        self,
        *,
        node: PlanNode,
        payload: Dict[str, Any],
        session_context: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        if not isinstance(payload, dict) or not isinstance(session_context, dict):
            return payload

        session_id = str(session_context.get("session_id") or "").strip()
        if not session_id:
            return payload

        try:
            from app.services.session_paths import get_runtime_session_dir

            session_dir = get_runtime_session_dir(session_id, create=True).resolve()
        except Exception:
            return payload

        workspace_dir = (session_dir / "workspace").resolve()
        try:
            _ancestor_chain, task_dir_str = self._resolve_task_tool_workspace(
                node,
                session_id=session_id,
            )
            task_dir = Path(task_dir_str).resolve()
        except Exception:
            return payload

        candidate_paths = self._extract_path_like_values(payload)
        if not candidate_paths:
            return payload

        promoted_abs_paths: List[str] = []
        promoted_session_paths: List[str] = []

        for candidate in candidate_paths:
            try:
                source = Path(str(candidate)).expanduser().resolve(strict=False)
            except Exception:
                continue
            if not source.exists() or not source.is_file():
                continue

            try:
                source.relative_to(task_dir)
                promoted_abs_paths.append(str(source))
                try:
                    promoted_session_paths.append(
                        str(source.relative_to(session_dir)).replace("\\", "/")
                    )
                except Exception:
                    pass
                continue
            except ValueError:
                pass

            try:
                rel_workspace = source.relative_to(workspace_dir)
            except ValueError:
                continue

            target = (task_dir / rel_workspace).resolve()
            try:
                target.relative_to(task_dir)
            except ValueError:
                continue

            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(source, target)
            except Exception as exc:
                logger.warning(
                    "Failed to promote workspace artifact %s into %s: %s",
                    source,
                    target,
                    exc,
                )
                continue

            promoted_abs_paths.append(str(target))
            try:
                promoted_session_paths.append(
                    str(target.relative_to(session_dir)).replace("\\", "/")
                )
            except Exception:
                pass

        if not promoted_abs_paths and not promoted_session_paths:
            return payload

        normalized_payload = dict(payload)

        existing_artifact_paths = [
            str(item).strip()
            for item in list(normalized_payload.get("artifact_paths") or [])
            if str(item).strip()
        ]
        if promoted_abs_paths:
            normalized_payload["artifact_paths"] = list(
                dict.fromkeys([*existing_artifact_paths, *promoted_abs_paths])
            )[:40]
            normalized_payload["produced_files"] = list(normalized_payload["artifact_paths"])

        existing_session_paths = [
            str(item).strip()
            for item in list(normalized_payload.get("session_artifact_paths") or [])
            if str(item).strip()
        ]
        if promoted_session_paths:
            normalized_payload["session_artifact_paths"] = list(
                dict.fromkeys([*existing_session_paths, *promoted_session_paths])
            )[:40]

        metadata = normalized_payload.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
            normalized_payload["metadata"] = metadata
        if isinstance(normalized_payload.get("artifact_paths"), list):
            metadata["artifact_paths"] = list(normalized_payload["artifact_paths"])
        if isinstance(normalized_payload.get("session_artifact_paths"), list):
            metadata["session_artifact_paths"] = list(
                normalized_payload["session_artifact_paths"]
            )

        return normalized_payload

    def _materialize_finalization(
        self,
        plan_id: int,
        node: PlanNode,
        finalization: VerificationFinalization,
        *,
        session_context: Optional[Dict[str, Any]],
    ) -> Tuple[VerificationFinalization, str]:
        finalization.payload = self._promote_workspace_artifacts_to_task_dir(
            node=node,
            payload=finalization.payload,
            session_context=session_context,
        )
        finalization.payload = self._enrich_finalized_payload_with_artifacts(
            plan_id=plan_id,
            node=node,
            payload=finalization.payload,
            final_status=finalization.final_status,
            session_context=session_context,
        )
        finalization = self._task_verifier.apply_artifact_authority(
            plan_id,
            node,
            finalization,
            manifest=self._get_artifact_manifest(plan_id, session_context),
        )
        raw_response = json.dumps(finalization.payload, ensure_ascii=False)
        self._persist_execution(
            plan_id,
            node.id,
            finalization.payload,
            status=finalization.final_status,
        )
        return finalization, raw_response

    def _finalize_task_execution(
        self,
        plan_id: int,
        node: PlanNode,
        payload: Dict[str, Any],
        *,
        execution_status: Optional[str],
        trigger: str = "auto",
    ) -> Tuple[VerificationFinalization, str]:
        finalization = self._task_verifier.finalize_payload(
            node,
            payload,
            execution_status=execution_status,
            trigger=trigger,
        )
        serialized = json.dumps(finalization.payload, ensure_ascii=False)
        return finalization, serialized

    def _execution_order(self, tree: PlanTree) -> Iterable[PlanNode]:
        ordered: List[PlanNode] = []
        emitted: set[int] = set()
        visiting: set[int] = set()
        scheduled: set[int] = set()
        stack: List[Tuple[int, int]] = []

        def schedule(node_id: int, *, force: bool = False) -> None:
            if node_id not in tree.nodes:
                return
            if node_id in emitted:
                return
            if not force and node_id in scheduled:
                return
            stack.append((node_id, 0))
            scheduled.add(node_id)

        def process_stack() -> None:
            while stack:
                current_id, stage = stack.pop()

                if current_id not in tree.nodes:
                    continue

                if stage == 0:
                    if current_id in emitted:
                        continue
                    if current_id in visiting:
                        logger.warning(
                            "Detected circular dependency while ordering task %s, skipping",
                            current_id
                        )
                        continue
                    visiting.add(current_id)
                    stack.append((current_id, 1))
                    node = tree.nodes[current_id]
                    for dep_id in reversed(node.dependencies):
                        if dep_id not in tree.nodes or dep_id in emitted:
                            continue
                        if dep_id in visiting:
                            logger.warning(
                                "Detected circular dependency between tasks %s and %s, skipping dependency",
                                current_id, dep_id
                            )
                            continue
                        if dep_id in scheduled:
                            continue
                        stack.append((dep_id, 0))
                        scheduled.add(dep_id)
                    continue

                if stage == 1:
                    node = tree.nodes[current_id]
                    stack.append((current_id, 2))
                    child_ids = list(tree.children_ids(current_id))
                    for child_id in reversed(child_ids):
                        if child_id not in tree.nodes or child_id in emitted:
                            continue
                        if child_id in visiting:
                            logger.warning(
                                "Detected circular dependency between parent %s and child %s, skipping",
                                current_id, child_id
                            )
                            continue
                        if child_id in scheduled:
                            continue
                        stack.append((child_id, 0))
                        scheduled.add(child_id)
                    continue

                if stage == 2:
                    visiting.discard(current_id)
                    if current_id in emitted:
                        continue
                    emitted.add(current_id)
                    ordered.append(tree.nodes[current_id])

        for root_id in tree.root_node_ids():
            schedule(root_id)

        process_stack()

        for node_id in tree.nodes.keys():
            if node_id not in emitted:
                schedule(node_id, force=True)
                process_stack()

        return ordered

    def _resolve_dependencies(
        self,
        tree: PlanTree,
        node: PlanNode,
    ) -> List[PlanNode]:
        deps: List[PlanNode] = []
        for dep_id in node.dependencies:
            dep = tree.nodes.get(dep_id)
            if dep is not None:
                deps.append(dep)
        return deps

    def _get_artifact_manifest(
        self,
        plan_id: int,
        session_context: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        manifest = None
        if isinstance(session_context, dict):
            manifest = session_context.get("_artifact_manifest")
        if isinstance(manifest, dict) and int(manifest.get("plan_id") or plan_id) == plan_id:
            manifest.setdefault("artifacts", {})
            return manifest
        session_id = session_context.get("session_id") if isinstance(session_context, dict) else None
        manifest = load_artifact_manifest(plan_id, session_id)
        if isinstance(session_context, dict):
            session_context["_artifact_manifest"] = manifest
        return manifest

    def _save_artifact_manifest(
        self,
        plan_id: int,
        manifest: Dict[str, Any],
        session_context: Optional[Dict[str, Any]],
    ) -> None:
        session_id = session_context.get("session_id") if isinstance(session_context, dict) else None
        save_artifact_manifest(plan_id, manifest, session_id)
        if isinstance(session_context, dict):
            session_context["_artifact_manifest"] = manifest

    def _resolve_task_artifact_contract(self, node: PlanNode) -> Dict[str, List[str]]:
        metadata = node.metadata if isinstance(node.metadata, dict) else {}
        return infer_artifact_contract(
            task_name=node.display_name(),
            instruction=node.instruction or "",
            metadata=metadata,
        )

    def _task_can_publish_artifacts(
        self,
        plan_id: int,
        node: PlanNode,
        *,
        tree: Optional[PlanTree] = None,
        state_by_task: Optional[Dict[int, Dict[str, Any]]] = None,
        session_context: Optional[Dict[str, Any]] = None,
        allow_publish_contract_backfill: bool = False,
    ) -> bool:
        if state_by_task is None and tree is not None:
            state_by_task = self._status_resolver.resolve_plan_states(
                plan_id,
                tree,
                manifest=self._get_artifact_manifest(plan_id, session_context),
            )
        if isinstance(state_by_task, dict):
            state = state_by_task.get(node.id) or {}
            effective_status = str(
                state.get("effective_status") or ""
            ).strip().lower()
            if effective_status:
                if effective_status == "completed":
                    return True
                can_backfill_publish_gap = (
                    allow_publish_contract_backfill
                    and effective_status == "failed"
                    and str(state.get("reason_code") or "").strip() == "publish_contract_missing"
                )
                if not can_backfill_publish_gap:
                    return False

        raw_status = str(getattr(node, "status", "") or "").strip().lower()
        if raw_status in {"done", "success"}:
            raw_status = "completed"
        if raw_status != "completed":
            return False

        payload: Any = getattr(node, "execution_result", None)
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {"content": payload}

        payload_status = ""
        metadata: Dict[str, Any] = {}
        if isinstance(payload, dict):
            payload_status = str(payload.get("status", "") or "").strip().lower()
            if payload_status in {"done", "success"}:
                payload_status = "completed"
            raw_metadata = payload.get("metadata")
            metadata = raw_metadata if isinstance(raw_metadata, dict) else {}

        if payload_status and payload_status != "completed":
            return False
        if bool(metadata.get("blocked_by_dependencies")):
            return False
        return True

    def _extract_candidate_artifact_paths(self, node: PlanNode) -> List[str]:
        payload: Any = node.execution_result
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {"content": payload}
        candidates = self._extract_path_like_values(payload)
        if isinstance(payload, dict):
            metadata = payload.get("metadata")
            if isinstance(metadata, dict):
                published = metadata.get("published_artifacts")
                if isinstance(published, dict):
                    for entry in published.values():
                        if not isinstance(entry, dict):
                            continue
                        for key in ("path", "source_path"):
                            value = str(entry.get(key) or "").strip()
                            if value and value not in candidates:
                                candidates.append(value)
        return candidates[:80]

    def _backfill_task_artifacts(
        self,
        plan_id: int,
        node: PlanNode,
        manifest: Dict[str, Any],
        session_context: Optional[Dict[str, Any]],
    ) -> Dict[str, Dict[str, Any]]:
        if node.id <= 0:
            return {}
        metadata = node.metadata if isinstance(node.metadata, dict) else {}
        provenance = resolve_artifact_contract_with_provenance(
            task_name=node.display_name(),
            instruction=node.instruction or "",
            metadata=metadata,
        )
        candidate_paths = self._extract_candidate_artifact_paths(node)
        backfill_enabled = bool(
            getattr(self._settings, "artifact_backfill_enabled", False)
        )
        if backfill_enabled:
            # Compatibility path: extend the contract with runtime-discovered
            # aliases so legacy plans without explicit publishes still land
            # their artifacts in the canonical manifest.
            extended = extend_contract_with_runtime_candidates(
                provenance,
                task_name=node.display_name(),
                instruction=node.instruction or "",
                candidate_paths=candidate_paths,
            )
            publishes_to_backfill = extended.publishes()
            if not provenance.has_explicit and publishes_to_backfill:
                logger.info(
                    "[ARTIFACT_BACKFILL_COMPAT] plan=%s task=%s inferred_publishes=%s "
                    "runtime_publishes=%s — legacy compatibility path used.",
                    plan_id,
                    node.id,
                    extended.inferred_publishes,
                    extended.runtime_publishes,
                )
        else:
            # Default path: honor explicit publishes when present. For older
            # plans (or tasks that persisted an empty artifact_contract block),
            # fall back to acceptance/instruction-derived publish aliases so
            # canonical artifacts still land in the manifest.
            publishes_to_backfill = list(provenance.explicit_publishes)
            if not publishes_to_backfill:
                publishes_to_backfill = list(provenance.inferred_publishes)

        published: Dict[str, Dict[str, Any]] = {}
        manifest_changed = False
        artifact_registry = None
        if isinstance(session_context, dict):
            artifact_registry = session_context.get("_artifact_registry")

        session_id = session_context.get("session_id") if isinstance(session_context, dict) else None

        for alias in publishes_to_backfill:
            if provenance.explicit_publishes and alias not in provenance.explicit_publishes:
                continue
            existing_entry = manifest.get("artifacts", {}).get(alias) if isinstance(manifest.get("artifacts"), dict) else None
            source = find_candidate_source_for_alias(alias=alias, candidate_paths=candidate_paths)
            if source is None and backfill_enabled:
                runtime_candidates = find_runtime_candidates(plan_id, node.id, alias)
                for runtime_path in runtime_candidates:
                    if runtime_path not in candidate_paths:
                        candidate_paths.append(runtime_path)
                source = find_candidate_source_for_alias(alias=alias, candidate_paths=candidate_paths)
            if source is None and isinstance(existing_entry, dict):
                existing = resolve_manifest_aliases(manifest, [alias]).get(alias)
                if existing:
                    published[alias] = dict(existing_entry)
                    continue
            if source is None:
                continue
            entry = publish_artifact(
                plan_id=plan_id,
                alias=alias,
                source_path=source,
                producer_task_id=node.id,
                manifest=manifest,
                session_id=session_id,
            )
            if entry is None:
                continue
            manifest_changed = True
            published[alias] = entry
            if isinstance(artifact_registry, dict):
                existing_paths = artifact_registry.setdefault(node.id, [])
                if entry["path"] not in existing_paths:
                    existing_paths.append(entry["path"])

        if manifest_changed:
            self._save_artifact_manifest(plan_id, manifest, session_context)
        return published

    def _resolve_required_artifacts(
        self,
        plan_id: int,
        node: PlanNode,
        *,
        dependencies: List[PlanNode],
        tree: PlanTree,
        session_context: Optional[Dict[str, Any]],
    ) -> Tuple[Dict[str, List[str]], Dict[str, str], List[str], Dict[str, List[int]]]:
        contract = self._resolve_task_artifact_contract(node)
        required_aliases = list(contract.get("requires") or [])
        metadata = node.metadata if isinstance(node.metadata, dict) else {}
        raw_explicit_contract = metadata.get("artifact_contract")
        explicit_contract = raw_explicit_contract if isinstance(raw_explicit_contract, dict) else {}
        explicit_required_aliases = [
            str(alias).strip()
            for alias in list(explicit_contract.get("requires") or [])
            if str(alias).strip()
        ]
        if not required_aliases:
            return contract, {}, [], {}

        manifest = self._get_artifact_manifest(plan_id, session_context)
        session_id = session_context.get("session_id") if isinstance(session_context, dict) else None

        backfill_enabled = bool(
            getattr(self._settings, "artifact_backfill_enabled", False)
        )
        state_by_task: Dict[int, Dict[str, Any]] = {}

        # Only backfill dependency artifacts when the legacy compat flag is on.
        # In authority mode (default), dependencies must have already published
        # their artifacts during their own execution via _materialize_finalization.
        if backfill_enabled:
            state_by_task = self._status_resolver.resolve_plan_states(
                plan_id,
                tree,
                manifest=manifest,
                session_id=session_id,
            )
            for dep in dependencies:
                if self._task_can_publish_artifacts(
                    plan_id,
                    dep,
                    state_by_task=state_by_task,
                    allow_publish_contract_backfill=True,
                ):
                    self._backfill_task_artifacts(plan_id, dep, manifest, session_context)

        resolved = resolve_manifest_aliases(manifest, required_aliases)
        missing_for_resolution = [alias for alias in required_aliases if alias not in resolved]
        if not missing_for_resolution:
            return contract, resolved, [], {}

        authoritative_required_aliases = [
            canonicalize_artifact_alias(alias)
            for alias in explicit_required_aliases
            if str(alias or "").strip()
            and canonical_artifact_path(plan_id, alias, session_id) is not None
        ]
        missing = [
            alias
            for alias in missing_for_resolution
            if alias in authoritative_required_aliases
        ]
        if not missing:
            return contract, resolved, [], {}

        # Producer scan: only attempt backfill when compat flag is on
        producer_map: Dict[str, List[int]] = {}
        all_nodes = list(tree.nodes.values())
        for alias in missing:
            producer_map[alias] = producer_candidates_for_alias(alias, all_nodes)
            if backfill_enabled:
                for producer_id in producer_map[alias]:
                    producer = tree.nodes.get(producer_id)
                    if producer is None or not self._task_can_publish_artifacts(
                        plan_id,
                        producer,
                        state_by_task=state_by_task,
                        allow_publish_contract_backfill=True,
                    ):
                        continue
                    self._backfill_task_artifacts(plan_id, producer, manifest, session_context)
                refreshed = resolve_manifest_aliases(manifest, [alias]).get(alias)
                if refreshed:
                    resolved[alias] = refreshed

        final_missing = [alias for alias in authoritative_required_aliases if alias not in resolved]
        return contract, resolved, final_missing, producer_map

    def _block_for_missing_resources(
        self,
        *,
        plan_id: int,
        node: PlanNode,
        tree: PlanTree,
        missing_resources: List[str],
        resolved_resources: Dict[str, Dict[str, Any]],
    ) -> ExecutionResult:
        reason = (
            f"Blocked by dependencies: task #{node.id} is missing required external resources "
            f"{missing_resources}. Configure or mount the resource before execution."
        )
        notes = [
            "This task was not executed because required external data resources are unavailable.",
            f"Missing resources: {', '.join(missing_resources)}",
        ]
        metadata = {
            "blocked_by_dependencies": True,
            "missing_resources": list(missing_resources),
            "resolved_resources": dict(resolved_resources),
            "incomplete_dependencies": [],
            "incomplete_dependency_info": [],
        }
        payload = {
            "status": "skipped",
            "content": reason,
            "notes": notes,
            "metadata": metadata,
        }
        finalization = self._task_verifier.finalize_payload(
            node,
            payload,
            execution_status="skipped",
        )
        raw_response = json.dumps(finalization.payload, ensure_ascii=False)
        self._persist_execution(
            plan_id,
            node.id,
            finalization.payload,
            status=finalization.final_status,
        )
        node.status = finalization.final_status
        node.execution_result = raw_response
        tree.nodes[node.id] = node
        return ExecutionResult(
            plan_id=plan_id,
            task_id=node.id,
            status=finalization.final_status,
            content=reason,
            notes=notes,
            metadata=finalization.payload.get("metadata") or {},
            raw_response=raw_response,
        )

    def _block_for_missing_artifacts(
        self,
        *,
        plan_id: int,
        node: PlanNode,
        tree: PlanTree,
        missing_aliases: List[str],
        producer_candidates: Dict[str, List[int]],
        resolved_input_artifacts: Dict[str, str],
    ) -> ExecutionResult:
        blocking_ids: List[int] = []
        for alias in missing_aliases:
            blocking_ids.extend(producer_candidates.get(alias) or [])
        unique_blocking_ids = sorted({task_id for task_id in blocking_ids if task_id != node.id})
        dependency_info = []
        for task_id in unique_blocking_ids:
            dep = tree.nodes.get(task_id)
            if dep is None:
                continue
            dependency_info.append(
                {"id": dep.id, "name": dep.display_name(), "status": dep.status}
            )
        reason = (
            f"Blocked by dependencies: task #{node.id} is missing required published artifacts "
            f"{missing_aliases}. Resolve upstream producer task(s) first."
        )
        notes = [
            "This task was not executed because required input artifacts are missing.",
            f"Missing artifact aliases: {', '.join(missing_aliases)}",
        ]
        metadata = {
            "blocked_by_dependencies": True,
            "missing_artifact_aliases": list(missing_aliases),
            "producer_task_candidates": producer_candidates,
            "resolved_input_artifacts": dict(resolved_input_artifacts),
            "incomplete_dependencies": unique_blocking_ids,
            "incomplete_dependency_info": dependency_info,
        }
        payload = {
            "status": "skipped",
            "content": reason,
            "notes": notes,
            "metadata": metadata,
        }
        finalization = self._task_verifier.finalize_payload(
            node,
            payload,
            execution_status="skipped",
        )
        raw_response = json.dumps(finalization.payload, ensure_ascii=False)
        self._persist_execution(
            plan_id,
            node.id,
            finalization.payload,
            status=finalization.final_status,
        )
        node.status = finalization.final_status
        node.execution_result = raw_response
        tree.nodes[node.id] = node
        return ExecutionResult(
            plan_id=plan_id,
            task_id=node.id,
            status=finalization.final_status,
            content=reason,
            notes=notes,
            metadata=finalization.payload.get("metadata") or {},
            raw_response=raw_response,
        )

    def _enrich_finalized_payload_with_artifacts(
        self,
        *,
        plan_id: int,
        node: PlanNode,
        payload: Dict[str, Any],
        final_status: str,
        session_context: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        metadata = payload.setdefault("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
            payload["metadata"] = metadata

        session_id = session_context.get("session_id") if isinstance(session_context, dict) else None

        resolved_inputs = {}
        if isinstance(session_context, dict):
            maybe_resolved = session_context.get("resolved_input_artifacts")
            if isinstance(maybe_resolved, dict):
                resolved_inputs = {
                    str(alias): str(path)
                    for alias, path in maybe_resolved.items()
                    if str(alias).strip() and str(path).strip()
                }
        if resolved_inputs:
            metadata["resolved_input_artifacts"] = resolved_inputs

        contract = self._resolve_task_artifact_contract(node)
        if contract.get("requires") or contract.get("publishes"):
            metadata["artifact_contract"] = contract

        existing_artifact_paths = [
            str(item).strip()
            for item in list(payload.get("artifact_paths") or metadata.get("artifact_paths") or [])
            if str(item).strip()
        ]
        mentioned_artifact_paths = self._existing_artifact_metadata_paths(
            self._extract_path_like_values(payload)
        )
        merged_artifact_paths = list(dict.fromkeys([*existing_artifact_paths, *mentioned_artifact_paths]))[:80]
        if merged_artifact_paths:
            payload["artifact_paths"] = merged_artifact_paths
            metadata["artifact_paths"] = merged_artifact_paths

        if final_status != "completed":
            return payload

        manifest = self._get_artifact_manifest(plan_id, session_context)
        payload_for_scan = json.loads(json.dumps(payload, ensure_ascii=False))
        temp_node = node.model_copy(
            update={
                "execution_result": json.dumps(payload_for_scan, ensure_ascii=False),
            }
        )
        published = self._backfill_task_artifacts(plan_id, temp_node, manifest, session_context)

        contract_records = metadata.get("contract_artifacts")
        if not isinstance(contract_records, list):
            top_level_contract_records = payload.get("contract_artifacts") if isinstance(payload, dict) else None
            if isinstance(top_level_contract_records, list):
                contract_records = top_level_contract_records
                metadata["contract_artifacts"] = top_level_contract_records
        if isinstance(contract_records, list):
            contract_published: Dict[str, Dict[str, Any]] = {}
            missing_contract_artifacts: List[Dict[str, Any]] = []
            for index, record in enumerate(contract_records):
                if not isinstance(record, dict):
                    continue
                path = str(record.get("path") or "").strip()
                expected = str(record.get("expected") or record.get("relative_to_task") or "").strip()
                if not path:
                    missing_contract_artifacts.append({
                        "expected": expected,
                        "reason": "missing_path",
                    })
                    continue
                candidate = Path(path).expanduser()
                try:
                    exists = candidate.exists() and candidate.is_file()
                    size = candidate.stat().st_size if exists else record.get("size")
                    resolved_path = str(candidate.resolve()) if exists else path
                except OSError as exc:
                    exists = False
                    size = record.get("size")
                    resolved_path = path
                    missing_contract_artifacts.append({
                        "expected": expected,
                        "path": path,
                        "reason": f"stat_failed: {exc}",
                    })
                if not exists:
                    if not any(item.get("path") == path for item in missing_contract_artifacts):
                        missing_contract_artifacts.append({
                            "expected": expected,
                            "path": path,
                            "reason": "not_found",
                        })
                    continue
                try:
                    size_int = int(size) if size is not None else 0
                except (TypeError, ValueError):
                    size_int = 0
                promotion_skipped = size_int > 250 * 1024 * 1024
                expected = expected or candidate.name
                # Reconnect the contract bypass to canonical publishing: only
                # registered aliases are inferred, so a miss safely keeps the
                # legacy `contract:` entry instead of force-publishing.
                if not promotion_skipped:
                    try:
                        preferred_namespace = infer_artifact_namespace(
                            node.display_name(),
                            node.instruction or "",
                        )
                    except Exception:
                        preferred_namespace = "general"
                    inferred_aliases: List[str] = []
                    # Contract-declared publish aliases are authoritative:
                    # the decomposer wrote these semantic names into the task
                    # metadata, and status_resolver/preflight match tasks by
                    # exactly these names.  Register them first so the
                    # manifest carries both spellings and future plans stop
                    # tripping the contract-alias mismatch (which used to
                    # demote genuinely-finished tasks to "blocked").
                    try:
                        _node_meta = node.metadata
                        if isinstance(_node_meta, str):
                            import json as _json
                            _node_meta = _json.loads(_node_meta)
                        _node_contract: Dict[str, Any] = {}
                        if isinstance(_node_meta, dict):
                            _raw_contract = (
                                _node_meta.get("artifact_contract")
                                or _node_meta.get("contract")
                            )
                            if isinstance(_raw_contract, str):
                                import json as _json
                                _raw_contract = _json.loads(_raw_contract)
                            if isinstance(_raw_contract, dict):
                                _node_contract = _raw_contract
                        for _pub in _node_contract.get("publishes") or []:
                            _pub_text = str(_pub or "").strip()
                            if _pub_text and _pub_text not in inferred_aliases:
                                inferred_aliases.append(_pub_text)
                    except Exception:
                        pass
                    for inferred in aliases_for_file_name(
                        expected, preferred_namespace=preferred_namespace
                    ):
                        if inferred not in inferred_aliases:
                            inferred_aliases.append(inferred)
                    for inferred in aliases_for_path_text(
                        f"{expected}\n{resolved_path}",
                        preferred_namespace=preferred_namespace,
                    ):
                        if inferred not in inferred_aliases:
                            inferred_aliases.append(inferred)
                    for canonical_alias in inferred_aliases:
                        published_entry = publish_artifact(
                            plan_id=plan_id,
                            alias=canonical_alias,
                            source_path=resolved_path,
                            producer_task_id=node.id,
                            manifest=manifest,
                            session_id=session_id,
                        )
                        if isinstance(published_entry, dict):
                            contract_published[published_entry["alias"]] = published_entry

                    if not inferred_aliases:
                        fallback_alias = f"general.contract_{node.id}_{Path(expected).stem}"
                        fallback_entry = publish_artifact(
                            plan_id=plan_id,
                            alias=fallback_alias,
                            source_path=resolved_path,
                            producer_task_id=node.id,
                            manifest=manifest,
                            session_id=session_id,
                        )
                        if isinstance(fallback_entry, dict):
                            contract_published[fallback_entry["alias"]] = fallback_entry
                key = f"contract:{expected or index}"
                contract_published[key] = {
                    "alias": key,
                    "path": resolved_path,
                    "producer_task_id": node.id,
                    "source": "contract_artifacts",
                    "expected": expected,
                    "size": size,
                    "exists": True,
                    "promotion_skipped": promotion_skipped,
                    "promotion_skipped_reason": "large_file" if promotion_skipped else None,
                }
            if missing_contract_artifacts:
                metadata["missing_contract_artifacts"] = missing_contract_artifacts
                logger.warning(
                    "Task %s reported contract artifacts that were not publishable: %s",
                    node.id,
                    missing_contract_artifacts[:5],
                )
            if contract_published:
                manifest.setdefault("artifacts", {}).update(contract_published)
                published = {**published, **contract_published}

            declared_publishes = contract.get("publishes") or []
            if isinstance(declared_publishes, list) and declared_publishes:
                manifest_aliases_registered = {
                    canonicalize_artifact_alias(a)
                    for a in manifest.get("artifacts", {}).keys()
                    if isinstance(a, str)
                }
                for declared_alias in declared_publishes:
                    if not isinstance(declared_alias, str) or not declared_alias.strip():
                        continue
                    canonical_declared = canonicalize_artifact_alias(declared_alias)
                    if canonical_declared in manifest_aliases_registered:
                        continue
                    candidate_paths = [
                        entry.get("path")
                        for entry in contract_published.values()
                        if isinstance(entry, dict) and entry.get("path")
                    ]
                    chosen_path = None
                    slot_suffix = canonical_declared.split(".", 1)[1] if "." in canonical_declared else ""
                    desired_ext = {
                        "evidence_md": ".md",
                        "manuscript_md": ".md",
                        "references_bib": ".bib",
                        "library_jsonl": ".jsonl",
                    }.get(slot_suffix)
                    for cp in candidate_paths:
                        cp_ext = Path(str(cp)).suffix.lower()
                        if desired_ext and cp_ext == desired_ext:
                            chosen_path = cp
                            break
                    if chosen_path is None and candidate_paths:
                        chosen_path = candidate_paths[0]
                    if chosen_path:
                        forced_entry = publish_artifact(
                            plan_id=plan_id,
                            alias=canonical_declared,
                            source_path=chosen_path,
                            producer_task_id=node.id,
                            manifest=manifest,
                            session_id=session_id,
                        )
                        if isinstance(forced_entry, dict):
                            published[forced_entry["alias"]] = forced_entry
                            logger.info(
                                "Task %s contract.publishes backfill: %s -> %s",
                                node.id,
                                canonical_declared,
                                chosen_path,
                            )

        if published:
            metadata["published_artifacts"] = published
            if bool(manifest.get("artifacts")):
                metadata["artifact_manifest_path"] = str(artifact_manifest_path(plan_id, session_id))
            self._save_artifact_manifest(plan_id, manifest, session_context)
            if session_id:
                self._publish_contract_deliverables(
                    plan_id=plan_id,
                    node=node,
                    published=published,
                    session_context=session_context,
                    manifest=manifest,
                )
        return payload

    def _publish_contract_deliverables(
        self,
        *,
        plan_id: int,
        node: PlanNode,
        published: Dict[str, Dict[str, Any]],
        session_context: Optional[Dict[str, Any]],
        manifest: Optional[Dict[str, Any]] = None,
    ) -> None:
        session_id = session_context.get("session_id") if isinstance(session_context, dict) else None
        if not session_id:
            logger.debug(f"Plan {plan_id} task {node.id}: No session_id, skipping deliverable publish")
            return

        # Unified path: emit one artifact.produced event per output and let
        # the registry projector materialize them.  Publishing no longer
        # depends on the publisher's filename-keyword whitelists (which used
        # to silently drop real task outputs like task1_evidence_cards.md).
        from typing import List, Tuple

        pairs: List[Tuple[Optional[str], str]] = []
        seen_paths: set = set()

        def _collect(alias: Optional[str], entry: Any) -> None:
            if not isinstance(entry, dict):
                return
            path_text = str(entry.get("path") or "").strip()
            if not path_text or path_text in seen_paths:
                return
            if not Path(path_text).is_file():
                return
            seen_paths.add(path_text)
            pairs.append((alias, path_text))

        for alias, entry in (published or {}).items():
            if isinstance(alias, str):
                _collect(alias, entry)
        if isinstance(manifest, dict):
            manifest_artifacts = manifest.get("artifacts", {})
            if isinstance(manifest_artifacts, dict):
                for key, entry in manifest_artifacts.items():
                    if isinstance(key, str) and key.startswith("contract:"):
                        _collect(key, entry)

        if not pairs:
            logger.info("Plan %s task %s: no publishable artifacts to deliver", plan_id, node.id)
            return

        finale = self._task_completes_plan(node)
        from app.services.artifacts.events import ArtifactEvent

        events: List[ArtifactEvent] = []
        for alias, path_text in pairs:
            path = Path(path_text)
            try:
                size = path.stat().st_size
            except OSError:
                size = None
            is_contract_alias = bool(alias) and str(alias).startswith("contract:")
            semantic_alias = None if is_contract_alias else alias
            role = "final_report" if (finale or str(alias or "").startswith("report.")) else "normal"
            events.append(
                ArtifactEvent(
                    session_id=session_id,
                    file_path=str(path),
                    alias=semantic_alias,
                    path_aliases=[str(alias)] if alias else [],
                    file_size=size,
                    file_ext=path.suffix.lower(),
                    producer_kind="plan_task",
                    producer_plan_id=plan_id,
                    producer_task_id=node.id,
                    producer_task_name=node.display_name(),
                    contract_declared=bool(semantic_alias),
                    contract_alias_source="decomposer" if semantic_alias else "executor",
                    publish_requested=True,
                    publish_role=role,
                )
            )

        logger.info(
            "Plan %s task %s: emitting %d artifact event(s) to the registry projector",
            plan_id,
            node.id,
            len(events),
        )
        try:
            from app.services.artifacts.projector import get_registry_projector

            get_registry_projector().consume_plan_events(
                session_id=session_id,
                events=events,
                plan_id=plan_id,
                task_id=node.id,
                task_name=node.display_name(),
                task_instruction=node.instruction or "",
            )
            logger.info(f"Plan {plan_id} task {node.id}: Successfully published contract deliverables")
        except Exception as exc:
            logger.warning(
                "Failed to publish contract deliverables for plan %s task %s: %s",
                plan_id, node.id, exc,
            )

    def _task_completes_plan(self, node: PlanNode) -> bool:
        """True when this node is a leaf and every other leaf task is already
        terminal — i.e. this completion finishes the plan (final report)."""
        try:
            tree = self._repo.get_plan_tree(node.plan_id)
            nodes = getattr(tree, "nodes", {}) or {}
        except Exception:
            return False
        try:
            own_children = getattr(node, "child_ids", None) or getattr(node, "children_ids", None) or getattr(node, "children", None) or []
            if own_children:
                return False
            terminal = {"completed", "failed", "skipped", "cancelled", "canceled", "succeeded", "success"}
            own_id = getattr(node, "id", None)
            for other in nodes.values():
                if getattr(other, "id", None) == own_id:
                    continue
                children = getattr(other, "child_ids", None) or getattr(other, "children_ids", None) or getattr(other, "children", None) or []
                if children:
                    continue
                node_type = str(getattr(other, "node_type", "") or "").strip().lower()
                if node_type in {"root", "composite"}:
                    continue
                status = str(getattr(other, "status", "") or "").strip().lower()
                if status and status not in terminal:
                    return False
            return True
        except Exception:
            return False

    @classmethod
    def _existing_artifact_metadata_paths(cls, values: Sequence[Any]) -> List[str]:
        paths: List[str] = []
        seen: set[str] = set()
        for value in values:
            for path in cls._existing_artifact_metadata_path_candidates(value):
                if path not in seen:
                    seen.add(path)
                    paths.append(path)
        return paths

    @staticmethod
    def _existing_artifact_metadata_path_candidates(value: Any) -> List[str]:
        if not isinstance(value, str) or not value.strip():
            return []
        try:
            path = Path(value.strip()).expanduser()
            if path.exists() and (path.is_file() or path.is_dir()):
                return [str(path)]
            candidates: List[str] = []
            for match in _PATH_LIKE_RE.finditer(value):
                candidate = Path(match.group(1)).expanduser()
                if candidate.exists() and (candidate.is_file() or candidate.is_dir()):
                    candidates.append(str(candidate))
            return candidates
        except OSError:
            return []

    @staticmethod
    def _is_internal_artifact_path(value: str) -> bool:
        # Single source of truth: TaskVerificationService._is_internal_artifact_path (D5 convergence).
        return TaskVerificationService._is_internal_artifact_path(value)

    @staticmethod
    def _is_non_deliverable_workspace_path(value: str) -> bool:
        normalized = "/" + str(value or "").strip().replace("\\", "/").lstrip("/")
        if not normalized or normalized == "/":
            return False
        return bool(_NON_DELIVERABLE_WORKSPACE_RE.search(normalized))

    @classmethod
    def _extract_path_like_values(cls, payload: Any) -> List[str]:
        if payload is None:
            return []
        path_keys = {
            "path",
            "output_path",
            "analysis_path",
            "effective_output_path",
            "effective_analysis_path",
            "partial_output_path",
            "combined_path",
            "combined_partial",
            "sections_dir",
            "reviews_dir",
            "merge_queue",
            "citation_validation_path",
            "manifest_path",
            "result_path",
            "preview_path",
            "references_bib",
            "evidence_md",
            "library_jsonl",
            "pdf_dir",
            "artifact_paths",
            "produced_files",
            "session_artifact_paths",
        }
        found: List[str] = []
        seen: set[str] = set()

        def _add(value: Any) -> None:
            if not isinstance(value, str):
                return
            text = value.strip()
            if not text or text in seen:
                return
            if "\n" in text or "\r" in text:
                return
            if "/" not in text and "." not in text:
                return
            if (
                cls._is_internal_artifact_path(text)
                or cls._is_non_deliverable_workspace_path(text)
                or _is_non_canonical_runtime_path(text)
            ):
                return
            seen.add(text)
            found.append(text)

        def _visit(value: Any, key: Optional[str] = None) -> None:
            if value is None:
                return
            if isinstance(value, dict):
                for item_key, item_value in value.items():
                    lowered = str(item_key).strip().lower()
                    if lowered in {"artifact_paths", "produced_files", "session_artifact_paths"} and isinstance(
                        item_value, (list, tuple, set)
                    ):
                        for item in item_value:
                            _add(item)
                    elif lowered in path_keys or lowered.endswith("_path") or lowered.endswith("_file") or lowered.endswith("_dir"):
                        if isinstance(item_value, (list, tuple, set)):
                            for item in item_value:
                                _add(item)
                        else:
                            _add(item_value)
                    if isinstance(item_value, (dict, list, tuple, set)):
                        _visit(item_value, key=lowered)
                return
            if isinstance(value, (list, tuple, set)):
                for item in value:
                    _visit(item, key=key)
                return
            if isinstance(value, str) and key:
                if key in path_keys or key.endswith("_path") or key.endswith("_file") or key.endswith("_dir"):
                    _add(value)
            elif isinstance(value, str):
                for match in _PATH_LIKE_RE.finditer(value):
                    _add(match.group(1))

        _visit(payload)
        return found[:40]

    @classmethod
    def _extract_tool_result_context(cls, payload: Any) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        result = payload.get("result")
        if not isinstance(result, dict):
            return {}

        extracted: Dict[str, Any] = {}
        artifact_paths = cls._extract_path_like_values(result)
        if artifact_paths:
            extracted["artifact_paths"] = artifact_paths[:40]

        session_artifact_paths = result.get("session_artifact_paths")
        if isinstance(session_artifact_paths, list):
            cleaned_session_paths: List[str] = []
            for item in session_artifact_paths:
                text = str(item or "").strip()
                if (
                    not text
                    or cls._is_internal_artifact_path(text)
                    or cls._is_non_deliverable_workspace_path(text)
                    or _is_non_canonical_runtime_path(text)
                ):
                    continue
                if text not in cleaned_session_paths:
                    cleaned_session_paths.append(text)
            if cleaned_session_paths:
                extracted["session_artifact_paths"] = cleaned_session_paths[:40]

        for key in (
            "run_directory",
            "working_directory",
            "task_directory_full",
            "task_root_directory",
            "results_directory",
            "work_dir",
            "run_dir",
        ):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                extracted[key] = value.strip()

        metadata = result.get("metadata")
        if isinstance(metadata, dict):
            for key in (
                "run_directory",
                "working_directory",
                "task_directory_full",
                "task_root_directory",
                "results_directory",
                "work_dir",
                "run_dir",
            ):
                value = metadata.get(key)
                if isinstance(value, str) and value.strip() and key not in extracted:
                    extracted[key] = value.strip()

        return extracted

    def _dependency_artifact_context(
        self,
        dep: PlanNode,
        artifact_registry: Optional[Dict[int, List[str]]] = None,
        artifact_manifest: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        # --- Priority 1: artifact_registry (accumulated across tasks) ---
        registry_paths = (artifact_registry or {}).get(dep.id)
        manifest_paths = published_artifact_paths_for_task(artifact_manifest or {}, dep.id)
        if isinstance(registry_paths, list) and registry_paths:
            combined_paths: List[str] = []
            for candidate in list(registry_paths) + list(manifest_paths):
                if isinstance(candidate, str) and candidate and candidate not in combined_paths:
                    combined_paths.append(candidate)
            return {
                "artifact_paths": combined_paths[:40],
                "output_directories": self._artifact_output_directories(combined_paths),
                "deliverable_manifest": None,
                "published_modules": [],
            }
        if manifest_paths:
            return {
                "artifact_paths": manifest_paths[:40],
                "output_directories": self._artifact_output_directories(manifest_paths),
                "deliverable_manifest": None,
                "published_modules": [],
            }

        # --- Priority 2: parse from execution_result ---
        if not dep.execution_result:
            return {"artifact_paths": [], "deliverable_manifest": None}
        payload: Any = dep.execution_result
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {"content": payload}
        if not isinstance(payload, dict):
            return {"artifact_paths": [], "deliverable_manifest": None}

        deliverables = payload.get("metadata", {}).get("deliverables") if isinstance(payload.get("metadata"), dict) else None
        if not isinstance(deliverables, dict):
            deliverables = {}
        manifest_path = deliverables.get("manifest_path")
        if not isinstance(manifest_path, str):
            manifest_path = None

        artifact_paths = self._extract_path_like_values(payload)
        if manifest_path and manifest_path not in artifact_paths:
            artifact_paths.insert(0, manifest_path)
        return {
            "artifact_paths": artifact_paths[:40],
            "output_directories": self._artifact_output_directories(artifact_paths),
            "deliverable_manifest": manifest_path,
            "published_modules": deliverables.get("published_modules") if isinstance(deliverables.get("published_modules"), list) else [],
        }

    @staticmethod
    def _artifact_output_directories(artifact_paths: Sequence[str]) -> List[str]:
        directories: List[str] = []
        for raw_path in artifact_paths:
            text = str(raw_path or "").strip()
            if not text:
                continue
            path = Path(text).expanduser()
            candidate = path if path.exists() and path.is_dir() else path.parent
            directory = str(candidate)
            if directory and directory != "." and directory not in directories:
                directories.append(directory)
        return directories[:20]
