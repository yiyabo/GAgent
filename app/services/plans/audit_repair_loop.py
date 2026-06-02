from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config.executor_config import get_executor_settings
from app.repository.plan_repository import PlanRepository

from .artifact_contracts import (
    artifact_path_matches_alias,
    canonical_plan_root,
    load_artifact_manifest,
    publish_artifact,
    save_artifact_manifest,
)
from .plan_executor import ExecutionConfig, PlanExecutor
from .plan_models import PlanNode
from .task_delegate_executor import CodeAgentTaskDelegateExecutor, TaskDelegationSpec
from .task_verification import TaskVerificationService, VerificationFinalization


_PASS_STATUSES = {"completed", "done", "success"}
_FINAL_FAILURE_STATUSES = {"failed", "error", "skipped", "blocked"}


@dataclass(frozen=True)
class AuditRepairLoopConfig:
    max_loops: int = 2
    max_task_repairs: int = 1
    enable_delegate_repair: bool = True
    enable_rerun: bool = True
    session_id: Optional[str] = None
    owner_id: Optional[str] = None


@dataclass(frozen=True)
class AuditRepairStep:
    iteration: int
    action: str
    classification: str
    status_before: str
    status_after: Optional[str] = None
    message: str = ""
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AuditRepairLoopResult:
    plan_id: int
    task_id: int
    success: bool
    final_status: str
    classification: str
    message: str
    steps: List[AuditRepairStep]
    final_payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "task_id": self.task_id,
            "success": self.success,
            "final_status": self.final_status,
            "classification": self.classification,
            "message": self.message,
            "steps": [
                {
                    "iteration": step.iteration,
                    "action": step.action,
                    "classification": step.classification,
                    "status_before": step.status_before,
                    "status_after": step.status_after,
                    "message": step.message,
                    "details": step.details,
                }
                for step in self.steps
            ],
            "final_payload": self.final_payload,
        }


class AuditRepairLoopService:
    """Explicit audit -> classify -> repair/rerun/block -> re-audit loop.

    The service is intentionally opt-in. It never changes the default plan
    execution path and only persists task changes for the target task.
    """

    def __init__(
        self,
        *,
        repo: PlanRepository,
        verifier: Optional[TaskVerificationService] = None,
        plan_executor: Optional[PlanExecutor] = None,
        delegate_executor: Optional[CodeAgentTaskDelegateExecutor] = None,
    ) -> None:
        self._repo = repo
        self._verifier = verifier or TaskVerificationService()
        self._plan_executor = plan_executor or PlanExecutor(repo=repo)
        self._delegate_executor = delegate_executor or CodeAgentTaskDelegateExecutor()

    def run_task_loop(
        self,
        *,
        plan_id: int,
        task_id: int,
        config: Optional[AuditRepairLoopConfig] = None,
    ) -> AuditRepairLoopResult:
        cfg = config or AuditRepairLoopConfig()
        max_loops = max(1, int(cfg.max_loops))
        max_task_repairs = max(0, int(cfg.max_task_repairs))
        steps: List[AuditRepairStep] = []
        seen_signatures: set[str] = set()
        repair_count = 0
        finalization = self._audit(plan_id, task_id)
        classification = self._classify(finalization)

        for iteration in range(1, max_loops + 1):
            status_before = self._normalize_status(finalization.final_status)
            classification = self._classify(finalization)
            signature = self._failure_signature(classification, finalization)

            if status_before in _PASS_STATUSES:
                steps.append(
                    AuditRepairStep(
                        iteration=iteration,
                        action="audit_passed",
                        classification=classification,
                        status_before=status_before,
                        status_after=status_before,
                        message="Deterministic audit passed.",
                    )
                )
                return self._result(plan_id, task_id, True, finalization, classification, steps)

            repairable_with_budget = (
                classification in {"repairable_artifact_contract", "repairable_manifest_or_path"}
                and repair_count < max_task_repairs
            )
            if signature in seen_signatures and not repairable_with_budget:
                steps.append(
                    AuditRepairStep(
                        iteration=iteration,
                        action="blocked_duplicate_signature",
                        classification=classification,
                        status_before=status_before,
                        status_after=status_before,
                        message="The same failure signature appeared again; stopping repair loop.",
                        details={"failure_signature": signature},
                    )
                )
                self._record_blocked(plan_id, task_id, classification, steps[-1].message, steps)
                return self._result(plan_id, task_id, False, finalization, classification, steps)
            seen_signatures.add(signature)

            if classification in {"environment_blocked", "credential_blocked", "unrecoverable_scientific_failure", "dependency_blocked"}:
                message = self._blocked_message(classification)
                steps.append(
                    AuditRepairStep(
                        iteration=iteration,
                        action="blocked",
                        classification=classification,
                        status_before=status_before,
                        status_after=status_before,
                        message=message,
                    )
                )
                self._record_blocked(plan_id, task_id, classification, message, steps)
                return self._result(plan_id, task_id, False, finalization, classification, steps)

            if classification == "retryable_timeout" and cfg.enable_rerun:
                rerun = self._rerun_task(plan_id, task_id, cfg)
                finalization = self._audit(plan_id, task_id)
                steps.append(
                    AuditRepairStep(
                        iteration=iteration,
                        action="rerun_task",
                        classification=classification,
                        status_before=status_before,
                        status_after=self._normalize_status(finalization.final_status),
                        message=rerun.content or "Task rerun completed; audit re-run persisted.",
                        details={"rerun_status": rerun.status},
                    )
                )
                continue

            if classification in {"repairable_artifact_contract", "repairable_manifest_or_path"} and cfg.enable_delegate_repair:
                if repair_count >= max_task_repairs:
                    message = "Maximum repair attempts reached for this task."
                    steps.append(
                        AuditRepairStep(
                            iteration=iteration,
                            action="blocked_repair_limit",
                            classification=classification,
                            status_before=status_before,
                            status_after=status_before,
                            message=message,
                        )
                    )
                    self._record_blocked(plan_id, task_id, classification, message, steps)
                    return self._result(plan_id, task_id, False, finalization, classification, steps)
                repair_count += 1
                repair_payload = self._delegate_repair(
                    plan_id=plan_id,
                    task_id=task_id,
                    finalization=finalization,
                    classification=classification,
                    attempt=repair_count,
                    config=cfg,
                )
                self._publish_repair_artifacts(plan_id, task_id, repair_payload)
                self._persist_repair_payload(plan_id, task_id, repair_payload, repair_count, steps)
                finalization = self._audit(plan_id, task_id)
                steps.append(
                    AuditRepairStep(
                        iteration=iteration,
                        action="delegate_repair",
                        classification=classification,
                        status_before=status_before,
                        status_after=self._normalize_status(finalization.final_status),
                        message="Delegated repair completed; deterministic audit re-run persisted.",
                        details={"repair_attempt": repair_count},
                    )
                )
                continue

            message = f"No safe automatic action is configured for classification '{classification}'."
            steps.append(
                AuditRepairStep(
                    iteration=iteration,
                    action="blocked_no_strategy",
                    classification=classification,
                    status_before=status_before,
                    status_after=status_before,
                    message=message,
                )
            )
            self._record_blocked(plan_id, task_id, classification, message, steps)
            return self._result(plan_id, task_id, False, finalization, classification, steps)

        classification = self._classify(finalization)
        final_status = self._normalize_status(finalization.final_status)
        success = final_status in _PASS_STATUSES
        if not success:
            self._record_blocked(
                plan_id,
                task_id,
                classification,
                "Maximum audit-repair loop iterations reached.",
                steps,
            )
        return self._result(plan_id, task_id, success, finalization, classification, steps)

    def _audit(self, plan_id: int, task_id: int) -> VerificationFinalization:
        return self._verifier.verify_task(
            self._repo,
            plan_id=plan_id,
            task_id=task_id,
            trigger="audit_repair_loop",
        )

    def _rerun_task(self, plan_id: int, task_id: int, config: AuditRepairLoopConfig) -> Any:
        exec_config = ExecutionConfig(
            force_rerun=True,
            auto_recovery=False,
            session_context={
                "session_id": config.session_id,
                "owner_id": config.owner_id,
                "user_message": f"Audit-repair loop rerun for task #{task_id}.",
                "deep_think_enabled": True,
                "audit_repair_loop": True,
            },
        )
        return self._plan_executor.execute_task(plan_id, task_id, config=exec_config)

    def _delegate_repair(
        self,
        *,
        plan_id: int,
        task_id: int,
        finalization: VerificationFinalization,
        classification: str,
        attempt: int,
        config: AuditRepairLoopConfig,
    ) -> Dict[str, Any]:
        tree = self._repo.get_plan_tree(plan_id)
        node = tree.get_node(task_id)
        metadata = self._payload_metadata(finalization.payload)
        node_metadata = node.metadata if isinstance(node.metadata, dict) else {}
        settings = get_executor_settings()
        backend = str(getattr(settings, "plan_task_agent_backend", "qwen_code") or "qwen_code").strip().lower()
        task_prompt = self._build_repair_prompt(
            node=node,
            classification=classification,
            attempt=attempt,
            payload=finalization.payload,
            metadata=metadata,
        )
        result = self._delegate_executor.execute(
            TaskDelegationSpec(
                plan_id=plan_id,
                task_id=task_id,
                task_name=node.display_name(),
                task_instruction=node.instruction or "",
                task_prompt=task_prompt,
                executor_backend=backend,
                session_id=config.session_id,
                owner_id=config.owner_id,
                work_dir=self._repair_work_dir(plan_id, task_id),
                artifact_contract=dict(node_metadata.get("artifact_contract") or {}),
                acceptance_criteria=dict(node_metadata.get("acceptance_criteria") or {}),
            )
        )
        repair_metadata: Dict[str, Any] = {
            "audit_repair_loop": True,
            "repair_generated": True,
            "repair_attempts": attempt,
            "repair_classification": classification,
            "delegated_repair": True,
            "delegation_status": result.status,
            "executor": result.executor,
            "executor_session_id": result.executor_session_id,
            **dict(result.metadata or {}),
        }
        if result.artifact_paths:
            repair_metadata["artifact_paths"] = list(result.artifact_paths[:80])
        payload: Dict[str, Any] = {
            "status": "success" if result.status == "completed" else "skipped" if result.status == "blocked" else "failed",
            "content": result.summary,
            "notes": [
                "Task artifact/path/manifest repair was delegated by the audit-repair loop.",
                "Deterministic verification decides final completion after this repair.",
            ],
            "metadata": repair_metadata,
        }
        if result.artifact_paths:
            payload["artifact_paths"] = list(result.artifact_paths[:80])
        return payload

    def _persist_repair_payload(
        self,
        plan_id: int,
        task_id: int,
        payload: Dict[str, Any],
        repair_count: int,
        previous_steps: List[AuditRepairStep],
    ) -> None:
        tree = self._repo.get_plan_tree(plan_id)
        node = tree.get_node(task_id)
        task_metadata = dict(node.metadata or {})
        history = list(task_metadata.get("repair_history") or [])
        history.append(
            {
                "attempt": repair_count,
                "trigger": "audit_repair_loop",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "payload_status": payload.get("status"),
                "previous_steps": [step.action for step in previous_steps],
            }
        )
        task_metadata["repair_history"] = history[-20:]
        self._repo.update_task(
            plan_id,
            task_id,
            status="pending",
            execution_result=json.dumps(payload, ensure_ascii=False),
            metadata=task_metadata,
        )

    def _publish_repair_artifacts(
        self,
        plan_id: int,
        task_id: int,
        payload: Dict[str, Any],
    ) -> None:
        tree = self._repo.get_plan_tree(plan_id)
        node = tree.get_node(task_id)
        node_metadata = node.metadata if isinstance(node.metadata, dict) else {}
        contract = node_metadata.get("artifact_contract") if isinstance(node_metadata.get("artifact_contract"), dict) else {}
        raw_publishes = contract.get("publishes") if isinstance(contract, dict) else None
        publishes = [str(alias).strip() for alias in raw_publishes or [] if str(alias).strip()] if isinstance(raw_publishes, list) else []
        if not publishes:
            return

        candidates: List[str] = []
        for source in (payload.get("artifact_paths"), self._payload_metadata(payload).get("artifact_paths")):
            if not isinstance(source, list):
                continue
            for item in source:
                text = str(item or "").strip()
                if text and text not in candidates:
                    candidates.append(text)
        if not candidates:
            return

        manifest = load_artifact_manifest(plan_id)
        published: List[Dict[str, Any]] = []
        for alias in publishes:
            for candidate in candidates:
                if not artifact_path_matches_alias(candidate, alias):
                    continue
                safe_candidate = self._safe_repair_artifact_candidate(plan_id, task_id, candidate)
                if safe_candidate is None:
                    continue
                entry = publish_artifact(
                    plan_id=plan_id,
                    alias=alias,
                    source_path=safe_candidate,
                    producer_task_id=task_id,
                    manifest=manifest,
                )
                if entry is not None:
                    published.append(entry)
                    break
        if not published:
            return
        save_artifact_manifest(plan_id, manifest)
        metadata = self._payload_metadata(payload)
        metadata["repair_manifest_publish"] = [
            {"alias": entry.get("alias"), "path": entry.get("path"), "producer_task_id": entry.get("producer_task_id")}
            for entry in published
        ]
        payload["metadata"] = metadata

    def _safe_repair_artifact_candidate(
        self,
        plan_id: int,
        task_id: int,
        candidate_text: str,
    ) -> Optional[str]:
        try:
            candidate = Path(str(candidate_text or "").strip()).expanduser()
            resolved_candidate = candidate.resolve(strict=True)
        except (OSError, RuntimeError):
            return None
        if self._path_has_symlink_component(candidate):
            return None
        allowed_roots = [
            self._repair_work_dir_path(plan_id, task_id),
            canonical_plan_root(plan_id),
        ]
        for root in allowed_roots:
            try:
                resolved_root = root.resolve(strict=False)
                resolved_candidate.relative_to(resolved_root)
                return str(resolved_candidate)
            except ValueError:
                continue
        return None

    @staticmethod
    def _path_has_symlink_component(path: Path) -> bool:
        try:
            path.absolute()
        except OSError:
            return True
        for part in (path, *path.parents):
            try:
                if part.is_symlink():
                    return True
            except OSError:
                return True
        return False

    def _record_blocked(
        self,
        plan_id: int,
        task_id: int,
        classification: str,
        message: str,
        steps: List[AuditRepairStep],
    ) -> None:
        tree = self._repo.get_plan_tree(plan_id)
        node = tree.get_node(task_id)
        task_metadata = dict(node.metadata or {})
        task_metadata["audit_repair_loop"] = {
            "status": "blocked",
            "classification": classification,
            "message": message,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "steps": [step.action for step in steps],
        }
        self._repo.update_task(plan_id, task_id, metadata=task_metadata)

    def _classify(self, finalization: VerificationFinalization) -> str:
        status = self._normalize_status(finalization.final_status)
        if status in _PASS_STATUSES:
            return "passed"
        payload = finalization.payload if isinstance(finalization.payload, dict) else {}
        metadata = self._payload_metadata(payload)
        text = self._searchable_text(payload, metadata)
        failure_kind = str(metadata.get("failure_kind") or "").strip().lower()
        if metadata.get("blocked_by_dependencies") or metadata.get("incomplete_dependencies"):
            return "dependency_blocked"
        if self._has_credential_signal(text):
            return "credential_blocked"
        if self._has_environment_signal(text):
            return "environment_blocked"
        if self._has_timeout_signal(text, metadata):
            return "retryable_timeout"
        if failure_kind == "contract_mismatch" or isinstance(metadata.get("contract_diff"), dict):
            return "repairable_artifact_contract"
        artifact_authority = metadata.get("artifact_authority")
        if isinstance(artifact_authority, dict):
            missing_publish = artifact_authority.get("missing_publish_aliases") or artifact_authority.get("missing_required_aliases")
            if missing_publish:
                return "repairable_manifest_or_path"
        if metadata.get("missing_artifact_aliases"):
            return "repairable_manifest_or_path"
        if self._has_scientific_failure_signal(text):
            return "unrecoverable_scientific_failure"
        if status in _FINAL_FAILURE_STATUSES:
            return "unrecoverable_scientific_failure"
        return "unknown"

    @staticmethod
    def _build_repair_prompt(
        *,
        node: PlanNode,
        classification: str,
        attempt: int,
        payload: Dict[str, Any],
        metadata: Dict[str, Any],
    ) -> str:
        node_metadata = node.metadata if isinstance(node.metadata, dict) else {}
        relevant = {
            "classification": classification,
            "attempt": attempt,
            "failure_kind": metadata.get("failure_kind"),
            "contract_diff": metadata.get("contract_diff"),
            "artifact_authority": metadata.get("artifact_authority"),
            "missing_artifact_aliases": metadata.get("missing_artifact_aliases"),
            "artifact_paths": payload.get("artifact_paths") or metadata.get("artifact_paths"),
        }
        return "\n".join(
            [
                "You are repairing one failed Phage-Agent plan task after deterministic audit.",
                "Repair only artifact/path/manifest/light-format issues for this task.",
                "Do not fabricate scientific outputs, invent analysis results, install packages, download data, or modify upstream task outputs.",
                "If the required result cannot be recovered from existing task outputs, report BLOCKED_DEPENDENCY with a concise DETAIL.",
                "Write any repaired outputs only inside the current task workspace/output directory.",
                "Return paths to concrete artifacts so deterministic verification can re-audit them.",
                "",
                "=== TASK ===",
                f"Task ID: {node.id}",
                f"Task Name: {node.display_name()}",
                f"Instruction: {node.instruction or ''}",
                "",
                "=== ACCEPTANCE CRITERIA ===",
                json.dumps(node_metadata.get("acceptance_criteria") or {}, ensure_ascii=False, indent=2, sort_keys=True),
                "",
                "=== ARTIFACT CONTRACT ===",
                json.dumps(node_metadata.get("artifact_contract") or {}, ensure_ascii=False, indent=2, sort_keys=True),
                "",
                "=== AUDIT FAILURE CONTEXT ===",
                json.dumps(relevant, ensure_ascii=False, indent=2, sort_keys=True),
            ]
        )

    @staticmethod
    def _repair_work_dir(plan_id: int, task_id: int) -> str:
        path = AuditRepairLoopService._repair_work_dir_path(plan_id, task_id)
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    @staticmethod
    def _repair_work_dir_path(plan_id: int, task_id: int) -> Path:
        return canonical_plan_root(plan_id) / "repairs" / f"task_{task_id}"

    @staticmethod
    def _payload_metadata(payload: Dict[str, Any]) -> Dict[str, Any]:
        metadata = payload.get("metadata") if isinstance(payload, dict) else {}
        return metadata if isinstance(metadata, dict) else {}

    @staticmethod
    def _normalize_status(value: Any) -> str:
        text = str(value or "").strip().lower()
        if text in {"done", "success"}:
            return "completed"
        if text == "error":
            return "failed"
        return text or "pending"

    @staticmethod
    def _searchable_text(payload: Dict[str, Any], metadata: Dict[str, Any]) -> str:
        values = [payload.get("content"), payload.get("error"), metadata.get("error_summary"), metadata.get("blocked_dependency_detail")]
        notes = payload.get("notes")
        if isinstance(notes, list):
            values.extend(notes)
        return "\n".join(str(value) for value in values if value is not None).lower()

    @staticmethod
    def _has_timeout_signal(text: str, metadata: Dict[str, Any]) -> bool:
        error_category = str(metadata.get("error_category") or "").strip().lower()
        return error_category == "timeout" or "timed out" in text or "timeout" in text or "no_output_timeout" in text

    @staticmethod
    def _has_credential_signal(text: str) -> bool:
        tokens = ("api key", "apikey", "credential", "permission denied", "unauthorized", "forbidden", "401", "403")
        return any(token in text for token in tokens)

    @staticmethod
    def _has_environment_signal(text: str) -> bool:
        tokens = (
            "package not available",
            "packages not available",
            "module not found",
            "modulenotfounderror",
            "importerror",
            "no module named",
            "conda",
            "bioconductor",
            "deseq2",
            "tcgabiolinks",
            "docker image",
            "command not found",
        )
        return any(token in text for token in tokens)

    @staticmethod
    def _has_scientific_failure_signal(text: str) -> bool:
        tokens = ("insufficient data", "input data missing", "no valid records", "analysis failed", "cannot compute")
        return any(token in text for token in tokens)

    @staticmethod
    def _failure_signature(classification: str, finalization: VerificationFinalization) -> str:
        metadata = finalization.payload.get("metadata") if isinstance(finalization.payload, dict) else {}
        if not isinstance(metadata, dict):
            metadata = {}
        signature_payload = {
            "classification": classification,
            "status": finalization.final_status,
            "failure_kind": metadata.get("failure_kind"),
            "missing_artifact_aliases": metadata.get("missing_artifact_aliases"),
            "contract_diff": metadata.get("contract_diff"),
            "artifact_authority": metadata.get("artifact_authority"),
        }
        return json.dumps(signature_payload, ensure_ascii=False, sort_keys=True, default=str)

    @staticmethod
    def _blocked_message(classification: str) -> str:
        messages = {
            "dependency_blocked": "Task is blocked by incomplete upstream dependencies; repair must happen upstream first.",
            "environment_blocked": "Task is blocked by missing runtime environment or packages; user/environment action is required.",
            "credential_blocked": "Task is blocked by missing credentials or permissions; user action is required.",
            "unrecoverable_scientific_failure": "Task failure is not safely repairable from existing artifacts.",
        }
        return messages.get(classification, "Task is not safely repairable automatically.")

    def _result(
        self,
        plan_id: int,
        task_id: int,
        success: bool,
        finalization: VerificationFinalization,
        classification: str,
        steps: List[AuditRepairStep],
    ) -> AuditRepairLoopResult:
        status = self._normalize_status(finalization.final_status)
        if success:
            message = f"Audit-repair loop passed with status '{status}'."
        else:
            message = f"Audit-repair loop stopped with status '{status}' and classification '{classification}'."
        return AuditRepairLoopResult(
            plan_id=plan_id,
            task_id=task_id,
            success=success,
            final_status=status,
            classification=classification,
            message=message,
            steps=steps,
            final_payload=finalization.payload if isinstance(finalization.payload, dict) else {},
        )
