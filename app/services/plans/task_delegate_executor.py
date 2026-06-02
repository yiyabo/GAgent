from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import cast

from ..execution.tool_executor import ToolExecutionContext, UnifiedToolExecutor

logger = logging.getLogger(__name__)


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
            "Return the strict final response schema requested by the execution runtime.",
            "Do not claim that internal Phage-Agent tools were called; this delegate only has the external code-agent runtime tools.",
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
            lines.append(self._format_json_block(spec.acceptance_criteria))
        if spec.artifact_contract:
            lines.append("\n=== ARTIFACT CONTRACT ===")
            lines.append(self._format_json_block(spec.artifact_contract))
        return "\n".join(lines)

    @staticmethod
    def _format_json_block(value: Mapping[str, object]) -> str:
        return json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True)

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
        summary = CodeAgentTaskDelegateExecutor._summarize_result(result_payload, status)
        if status == "completed" and len(summary) < 50:
            llm_summary = CodeAgentTaskDelegateExecutor._generate_llm_summary(
                spec, result_payload, status
            )
            if llm_summary:
                summary = llm_summary
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
    def _summarize_result(result_payload: Mapping[str, object], status: str) -> str:
        for key in (
            "result",
            "error_summary",
            "error",
            "stdout",
            "execution_status",
            "verification_status",
        ):
            value = str(result_payload.get(key) or "").strip()
            if value and len(value) <= 500:
                return value
        if status == "completed":
            return "Task completed successfully."
        if status == "blocked":
            return "External task delegation blocked by missing dependency."
        return "External task delegation failed."

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

    @staticmethod
    def _generate_llm_summary(
        spec: TaskDelegationSpec,
        result_payload: Mapping[str, object],
        status: str,
    ) -> str | None:
        try:
            from app.llm import get_default_client
        except ImportError:
            return None

        produced_files: list[dict] = []
        raw_files = result_payload.get("produced_files") or []
        if isinstance(raw_files, list):
            for f in raw_files[:8]:
                if isinstance(f, dict):
                    produced_files.append(f)
                elif isinstance(f, str):
                    produced_files.append({"path": f})

        file_descriptions = []
        for f in produced_files:
            path = str(f.get("path") or "")
            name = path.rsplit("/", 1)[-1] if "/" in path else path
            desc = str(f.get("description") or "")
            if name:
                file_descriptions.append(f"- {name}" + (f"：{desc}" if desc else ""))

        contract_artifacts = result_payload.get("contract_artifacts") or []
        verified_files = []
        if isinstance(contract_artifacts, list):
            for a in contract_artifacts[:5]:
                if isinstance(a, dict) and a.get("exists"):
                    expected = str(a.get("expected") or a.get("path") or "")
                    name = expected.rsplit("/", 1)[-1] if "/" in expected else expected
                    size = a.get("size")
                    if name:
                        size_str = f" ({size:,} bytes)" if isinstance(size, (int, float)) else ""
                        verified_files.append(f"- {name}{size_str}")

        acceptance = result_payload.get("acceptance_check")
        verification_notes = ""
        if isinstance(acceptance, dict):
            verification_notes = str(acceptance.get("notes") or "")[:300]

        agent_summary = str(
            result_payload.get("result")
            or result_payload.get("summary")
            or ""
        ).strip()[:300]

        instruction = (spec.task_instruction or "")[:400]

        prompt = (
            "You are a senior biostatistician generating a structured execution report card "
            "with professional insights for the principal investigator.\n\n"
            f"Task: {spec.task_name}\n"
            f"Objective: {instruction}\n"
            f"Status: {status}\n"
        )
        if file_descriptions:
            prompt += f"Produced:\n" + "\n".join(file_descriptions) + "\n"
        if verified_files:
            prompt += f"Verified files:\n" + "\n".join(verified_files) + "\n"
        if verification_notes:
            prompt += f"Verification: {verification_notes}\n"
        if agent_summary:
            prompt += f"Key metrics: {agent_summary}\n"

        prompt += (
            "\nOutput format (use markdown):\n\n"
            "## ✅ {Task Name}\n\n"
            "**Data Source**: {describe input data briefly}\n\n"
            "**Analysis Performed**:\n"
            "- {method 1}: {brief result}\n"
            "- {method 2}: {brief result}\n\n"
            "**Key Results**:\n"
            "| Metric | Value |\n"
            "|--------|-------|\n"
            "| {metric1} | {value1} |\n"
            "| {metric2} | {value2} |\n"
            "| ... | ... |\n\n"
            "**Insights**:\n"
            "- {2-3 bullet points with professional interpretation: data quality assessment, "
            "statistical reliability, caveats, or domain-specific observations}\n\n"
            "**Deliverables**: {list produced files with brief descriptions}\n\n"
            "**Next Steps**: {1-2 sentences suggesting downstream analysis directions based on the results}\n\n"
            "Requirements:\n"
            "- Extract specific numbers/metrics from the data provided\n"
            "- Keep table to 3-5 rows of the most important metrics\n"
            "- Insights should add analytical value beyond raw numbers "
            "(e.g. data quality assessment, statistical reliability, sample size adequacy, caveats)\n"
            "- Total length: 200-300 words\n"
            "- Match the output language to the task name language\n"
            "- Do not mention code or file paths"
        )

        try:
            client = get_default_client()
            response = client.chat(prompt, max_tokens=500)
            summary = response.strip()
            if summary and len(summary) >= 30:
                return summary
        except Exception as exc:
            logger.warning("LLM summary generation failed for task %s: %s", spec.task_id, exc)
        return None
