"""External code-agent delegation cluster of ``PlanExecutor``.

Moved verbatim out of ``plan_executor.py`` per
``design/2026-09-24-backend-godfiles-refactor-plan.md`` §4.6 (cluster ⑥):
the delegation gate, the external-delegate task runner, its readable-dir
resolution and the output-contract constraint lines.  Composed into
``PlanExecutor`` as the ``_DelegateMethods`` mixin, so call sites
(``_run_task``) and the ``self.*`` graph are unchanged.

No monkeypatch surface: nothing in ``app/tests`` patches these names, and the
``self._task_delegate_executor`` collaborator stays an instance attribute.
The module uses its own ``logging.getLogger(__name__)`` (split precedent); log
messages are byte-identical.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from .executor_models import ExecutionConfig, ExecutionResult
from .executor_text_utils import _coerce_blocked_dependency_payload
from .plan_models import PlanNode, PlanTree
from .task_delegate_executor import TaskDelegationSpec

logger = logging.getLogger(__name__)


class _DelegateMethods:
    """External code-agent delegation cluster of ``PlanExecutor`` (mixin)."""

    def _should_delegate_plan_task(self, config: ExecutionConfig) -> bool:
        _ = config
        return str(
            getattr(self._settings, "plan_task_execution_backend", "internal") or "internal"
        ).strip().lower() == "external_agent"

    def _run_task_with_external_delegate(
        self,
        *,
        plan_id: int,
        node: PlanNode,
        parent: Optional[PlanNode],
        dependencies: List[PlanNode],
        plan_outline: Optional[str],
        tree: PlanTree,
        config: ExecutionConfig,
        artifact_contract: Dict[str, List[str]],
        resolved_input_artifacts: Dict[str, str],
        resolved_resources: Dict[str, Dict[str, Any]],
    ) -> ExecutionResult:
        try:
            self._repo.update_task(plan_id, node.id, status="delegating", execution_result="")
            node.status = "delegating"
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "Failed to mark task %s as delegating for plan %s: %s",
                node.id,
                plan_id,
                exc,
            )

        session_context = config.session_context if isinstance(config.session_context, dict) else {}
        task_ancestor_chain, task_work_dir = self._resolve_task_tool_workspace(
            node,
            session_id=session_context.get("session_id"),
            tree=tree,
        )
        prompt = self._prompt_builder.build(
            node=node,
            parent=parent,
            dependencies=dependencies,
            plan_outline=plan_outline,
            include_context=config.use_context,
            session_context=session_context,
            include_tool_hints=False,
        )
        node_metadata = node.metadata if isinstance(node.metadata, dict) else {}
        acceptance_criteria = (
            node_metadata.get("acceptance_criteria")
            if isinstance(node_metadata.get("acceptance_criteria"), dict)
            else {}
        )
        readable_dirs = self._delegation_readable_dirs(
            resolved_input_artifacts=resolved_input_artifacts,
            dependencies=dependencies,
            session_context=session_context,
        )
        backend = str(
            getattr(self._settings, "plan_task_agent_backend", "qwen_code") or "qwen_code"
        ).strip().lower()
        delegation = self._task_delegate_executor.execute(
            TaskDelegationSpec(
                plan_id=plan_id,
                task_id=node.id,
                task_name=node.display_name(),
                task_instruction=node.instruction or "",
                task_prompt=prompt,
                executor_backend=backend,
                session_id=session_context.get("session_id"),
                ancestor_chain=task_ancestor_chain,
                owner_id=session_context.get("owner_id"),
                current_job_id=self._current_job_id(),
                work_dir=task_work_dir,
                artifact_contract=dict(artifact_contract or {}),
                acceptance_criteria=dict(acceptance_criteria or {}),
                resolved_input_artifacts=dict(resolved_input_artifacts or {}),
                readable_dirs=readable_dirs,
                resolved_resources=dict(resolved_resources or {}),
            )
        )
        metadata: Dict[str, Any] = {
            "delegated_task_execution": True,
            "executor": delegation.executor,
            "executor_session_id": delegation.executor_session_id,
            "delegation_status": delegation.status,
            **dict(delegation.metadata or {}),
        }
        if delegation.artifact_paths:
            metadata["artifact_paths"] = list(delegation.artifact_paths[:80])
        payload: Dict[str, Any] = {
            "status": "success" if delegation.status == "completed" else "skipped" if delegation.status == "blocked" else "failed",
            "content": delegation.summary,
            "notes": ["Task executed by external code agent delegate."],
            "metadata": metadata,
        }
        if delegation.artifact_paths:
            payload["artifact_paths"] = list(delegation.artifact_paths[:80])
        for key in (
            "run_directory",
            "working_directory",
            "task_directory_full",
            "task_root_directory",
            "results_directory",
            "work_dir",
            "run_dir",
        ):
            value = delegation.raw_result.get(key) if isinstance(delegation.raw_result, dict) else None
            if isinstance(value, str) and value.strip():
                payload[key] = value.strip()
        if delegation.status == "blocked":
            payload.setdefault("metadata", {})["blocked_by_dependencies"] = True
            payload, execution_status = _coerce_blocked_dependency_payload(payload)
            if execution_status == "completed":
                payload["status"] = "skipped"
                execution_status = "skipped"
        elif delegation.status == "failed":
            execution_status = "failed"
        else:
            execution_status = "completed"
        finalization, _ = self._finalize_task_execution(
            plan_id,
            node,
            payload,
            execution_status=execution_status,
        )
        finalization, raw_response = self._materialize_finalization(
            plan_id,
            node,
            finalization,
            session_context=config.session_context,
        )
        node.execution_result = raw_response
        node.status = finalization.final_status
        tree.nodes[node.id] = node
        if parent:
            tree.nodes[parent.id] = parent
        return ExecutionResult(
            plan_id=plan_id,
            task_id=node.id,
            status=finalization.final_status,
            content=str(finalization.payload.get("content") or delegation.summary),
            notes=list(finalization.payload.get("notes") or []),
            metadata=finalization.payload.get("metadata") or {},
            raw_response=raw_response,
            attempts=1,
        )

    def _delegation_readable_dirs(
        self,
        *,
        resolved_input_artifacts: Dict[str, str],
        dependencies: List[PlanNode],
        session_context: Dict[str, Any],
    ) -> List[str]:
        dirs: List[str] = []

        def _add(path_value: Any) -> None:
            text = str(path_value or "").strip()
            if not text:
                return
            path = Path(text).expanduser()
            candidate = path if path.exists() and path.is_dir() else path.parent
            candidate_text = str(candidate)
            if candidate_text and candidate_text != "." and candidate_text not in dirs:
                dirs.append(candidate_text)

        for path in resolved_input_artifacts.values():
            _add(path)
        artifact_manifest = self._get_artifact_manifest(
            dependencies[0].plan_id if dependencies else 0,
            session_context,
        ) if dependencies else {}
        for dep in dependencies:
            artifact_context = self._dependency_artifact_context(
                dep,
                session_context.get("_artifact_registry"),
                artifact_manifest=artifact_manifest,
            )
            for path in artifact_context.get("artifact_paths") or []:
                _add(path)
        return dirs[:20]

    @staticmethod
    def _build_output_contract_constraints(node_metadata: Dict[str, Any]) -> List[str]:
        metadata = node_metadata if isinstance(node_metadata, dict) else {}
        acceptance = metadata.get("acceptance_criteria") if isinstance(metadata.get("acceptance_criteria"), dict) else {}
        contract = metadata.get("artifact_contract") if isinstance(metadata.get("artifact_contract"), dict) else {}
        required_paths: List[str] = []
        checks = acceptance.get("checks") if isinstance(acceptance, dict) else None
        if isinstance(checks, list):
            for check in checks:
                if not isinstance(check, dict):
                    continue
                check_type = str(check.get("type") or "").strip().lower()
                if check_type not in {"file_exists", "file_nonempty"}:
                    continue
                path = str(check.get("path") or "").strip()
                if path and path not in required_paths:
                    required_paths.append(path)

        lines: List[str] = []
        if required_paths:
            lines.append(
                "OUTPUT CONTRACT: before claiming completion, create these exact required file(s): "
                + ", ".join(required_paths[:20])
                + ". Similar filenames do not satisfy the contract."
            )
        publishes = contract.get("publishes") if isinstance(contract, dict) else None
        if isinstance(publishes, list) and publishes:
            lines.append(
                "ARTIFACT CONTRACT: this task publishes logical artifact alias(es): "
                + ", ".join(str(item) for item in publishes[:20])
                + ". Ensure the required output files exist so the executor can validate and register them."
            )
        requires = contract.get("requires") if isinstance(contract, dict) else None
        if isinstance(requires, list) and requires:
            lines.append(
                "ARTIFACT INPUTS: use resolved_input_artifacts for required alias(es): "
                + ", ".join(str(item) for item in requires[:20])
                + "; do not resolve ambiguous basenames by guessing."
            )
        return lines
