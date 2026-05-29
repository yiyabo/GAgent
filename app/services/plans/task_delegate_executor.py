from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import cast

from ..execution.tool_executor import ToolExecutionContext, UnifiedToolExecutor


@dataclass(frozen=True)
class TaskDelegationSpec:
    plan_id: int
    task_id: int
    task_name: str
    task_instruction: str
    task_prompt: str
    executor_backend: str
    session_id: str | None = None
    ancestor_chain: list[int] | None = None
    owner_id: str | None = None
    current_job_id: str | None = None
    work_dir: str | None = None
    artifact_contract: dict[str, object] = field(default_factory=dict)
    acceptance_criteria: dict[str, object] = field(default_factory=dict)
    resolved_input_artifacts: dict[str, str] = field(default_factory=dict)
    readable_dirs: list[str] = field(default_factory=list)
    resolved_resources: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class TaskDelegationResult:
    status: str
    summary: str
    artifact_paths: list[str] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    executor: str = "external_agent"
    executor_session_id: str | None = None
    raw_result: dict[str, object] = field(default_factory=dict)
    metadata: dict[str, object] = field(default_factory=dict)


class CodeAgentTaskDelegateExecutor:
    """Delegate a plan task to the existing code_executor CLI backend."""

    def __init__(self, *, tool_executor: UnifiedToolExecutor | None = None) -> None:
        self._tool_executor: UnifiedToolExecutor
        self._tool_executor = tool_executor or UnifiedToolExecutor()

    def execute(self, spec: TaskDelegationSpec) -> TaskDelegationResult:
        params: dict[str, object] = {
            "task": self._build_delegate_prompt(spec),
            "execution_backend": spec.executor_backend,
            "skip_permissions": True,
            "output_format": "json",
            "auto_fix": True,
            "resolved_resources": dict(spec.resolved_resources or {}),
        }
        if spec.readable_dirs:
            params["add_dirs"] = list(spec.readable_dirs)

        payload = cast(
            dict[str, object],
            self._tool_executor.execute_sync(
            "code_executor",
            params,
            context=ToolExecutionContext(
                plan_id=spec.plan_id,
                task_id=spec.task_id,
                task_name=spec.task_name,
                task_instruction=spec.task_instruction,
                session_id=spec.session_id,
                ancestor_chain=spec.ancestor_chain,
                owner_id=spec.owner_id,
                current_job_id=spec.current_job_id,
                work_dir=spec.work_dir,
                channel="plan_executor",
                mode="delegated_task_execution",
                resolved_resources=spec.resolved_resources,
            ),
            ),
        )
        return self._to_delegation_result(spec, payload)

    def _build_delegate_prompt(self, spec: TaskDelegationSpec) -> str:
        lines = [
            "You are executing one atomic plan task delegated by the orchestration system.",
            "Complete only this task; do not create or modify the plan.",
            "The orchestration system, not you, decides final task completion after deterministic verification.",
            "If inputs are missing, report BLOCKED_DEPENDENCY with a concise DETAIL.",
            "Return actual produced file paths in your final response.",
            "",
            "=== PLAN TASK ===",
            f"Plan ID: {spec.plan_id}",
            f"Task ID: {spec.task_id}",
            f"Task Name: {spec.task_name}",
            "",
            spec.task_prompt,
        ]
        if spec.resolved_input_artifacts:
            lines.append("\n=== RESOLVED INPUT ARTIFACTS ===")
            for alias, path in spec.resolved_input_artifacts.items():
                lines.append(f"- {alias}: {path}")
        if spec.acceptance_criteria:
            lines.append("\n=== ACCEPTANCE CRITERIA ===")
            lines.append(str(spec.acceptance_criteria))
        if spec.artifact_contract:
            lines.append("\n=== ARTIFACT CONTRACT ===")
            lines.append(str(spec.artifact_contract))
        return "\n".join(lines)

    @staticmethod
    def _to_delegation_result(spec: TaskDelegationSpec, payload: Mapping[str, object]) -> TaskDelegationResult:
        result = payload.get("result")
        result_payload = CodeAgentTaskDelegateExecutor._as_string_key_dict(result)
        tool_success = bool(payload.get("success", False))
        execution_success = bool(result_payload.get("success", tool_success))
        error_category = str(result_payload.get("error_category") or "").strip().lower()
        blocked_reason = str(result_payload.get("blocked_reason") or "").strip()
        if error_category == "blocked_dependency" or blocked_reason:
            status = "blocked"
        elif tool_success and execution_success:
            status = "completed"
        else:
            status = "failed"

        artifact_paths = CodeAgentTaskDelegateExecutor._collect_artifact_paths(result_payload)
        summary = (
            str(payload.get("summary") or "").strip()
            or str(result_payload.get("result") or "").strip()
            or str(result_payload.get("error") or "").strip()
            or "External task delegation finished."
        )
        metadata: dict[str, object] = {
            "delegated_task_execution": True,
            "executor": spec.executor_backend,
            "tool_success": tool_success,
            "execution_success": execution_success,
            "execution_backend": result_payload.get("execution_backend") or spec.executor_backend,
            "run_directory": result_payload.get("run_directory"),
            "working_directory": result_payload.get("working_directory"),
            "task_directory_full": result_payload.get("task_directory_full"),
            "execution_status": result_payload.get("execution_status"),
            "verification_status": result_payload.get("verification_status"),
            "failure_kind": result_payload.get("failure_kind"),
            "error_category": result_payload.get("error_category"),
            "error_summary": result_payload.get("error_summary"),
            "contract_diff": result_payload.get("contract_diff"),
            "contract_artifacts": result_payload.get("contract_artifacts"),
        }
        metadata = {key: value for key, value in metadata.items() if value is not None}
        return TaskDelegationResult(
            status=status,
            summary=summary,
            artifact_paths=artifact_paths,
            stdout=str(result_payload.get("stdout") or ""),
            stderr=str(result_payload.get("stderr") or ""),
            executor=spec.executor_backend,
            executor_session_id=str(result_payload.get("run_id") or "") or None,
            raw_result=result_payload,
            metadata=metadata,
        )

    @staticmethod
    def _as_string_key_dict(value: object) -> dict[str, object]:
        if not isinstance(value, dict):
            return {}
        mapping = cast(Mapping[object, object], value)
        return {str(key): item for key, item in mapping.items()}

    @staticmethod
    def _collect_artifact_paths(result_payload: Mapping[str, object]) -> list[str]:
        paths: list[str] = []
        for key in ("artifact_paths", "produced_files", "session_artifact_paths"):
            values = result_payload.get(key)
            if not isinstance(values, list):
                continue
            typed_values: list[object] = values
            for value in typed_values:
                if not isinstance(value, str):
                    continue
                text = value.strip()
                if text and text not in paths:
                    paths.append(text)
        return paths[:80]
