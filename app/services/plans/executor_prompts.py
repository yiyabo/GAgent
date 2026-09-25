"""Executor prompt composition (god-class split, behaviour zero-change).

``ExecutorPromptBuilder`` and ``_strip_code_fences`` were moved verbatim out of
``plan_executor.py`` per
``design/2026-09-24-backend-godfiles-refactor-plan.md`` §4.6 (cluster ③).
``plan_executor.py`` re-exports both names.  Prompt text is byte-identical —
this split changes no character the execution LLM ever sees.

Only sanctioned deviation: the module uses its own
``logging.getLogger(__name__)`` (deep_think/deliverables split precedent);
the "Executor prompt too long" warning text and level are unchanged.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .plan_models import PlanNode

logger = logging.getLogger(__name__)


class ExecutorPromptBuilder:
    """Compose prompts for the execution LLM."""

    SYSTEM_HEADER = (
        "You are an execution agent that completes research or engineering tasks. "
        "When a task requires external tools (data retrieval, code execution, file operations), "
        "use the provided function-calling tools. "
        "When a task is text-only (design, planning, writing, analysis), respond with plain text content directly."
    )

    TOOL_HINTS = (
        "\n=== TOOL SELECTION HINTS ===\n"
        "- FASTA/accession download → sequence_fetch\n"
        "- Bioinformatics analysis (FASTA/FASTQ) → bio_tools. Call operation='help' first for required params. "
        "Pass input_file (absolute path) or sequence_text (inline FASTA). Extra params go in the 'params' dict. "
        "If error_code='missing_required_params', read retry_hint and retry with corrected params.\n"
        "- Literature/PubMed search → literature_pipeline\n"
        "- Data analysis/visualization/code → code_executor\n"
        "- Web information lookup → web_search\n"
        "- Read documents (PDF/TXT) → document_reader\n"
        "- Read images/scanned docs → vision_reader\n"
        "- PhageScope operations → phagescope\n"
        "- Design/planning/text writing → respond directly, no tool needed"
    )

    # Rough char-to-token ratio ~3.5 for mixed EN/ZH text.
    # Cap at ~28k tokens (~100k chars) to stay within typical model context windows.
    MAX_PROMPT_CHARS = 100_000

    def _summarize_long_result(self, result: str, max_length: int = 2000) -> str:
        """Summarize long execution results to avoid prompt bloat.

        Preserves key information like:
        - Numbers, metrics, statistics
        - File paths
        - Conclusions and key findings
        - Error messages

        Args:
            result: The original result text
            max_length: Maximum length to return

        Returns:
            Summarized result if over max_length, otherwise original
        """
        if not result or len(result) <= max_length:
            return result

        # Strategy: Keep beginning and end, add truncation notice
        # Beginning often has summary/conclusion
        # End often has final results or file paths
        keep_start = int(max_length * 0.6)
        keep_end = int(max_length * 0.3)

        # Extract key patterns to preserve
        import re

        # Find file paths
        file_paths = re.findall(r'[\w/\-\.]+\.(csv|json|txt|png|jpg|pdf|fasta|fa|fq|xlsx)', result)

        # Find numbers with context (e.g., "accuracy: 0.95", "rows: 1000")
        metrics = re.findall(r'\b\w+[:\s=]+\d+\.?\d*%?\b', result)[:5]

        # Build summary
        summary_parts = [
            result[:keep_start].strip(),
            "\n... [TRUNCATED - original was {} chars] ...\n".format(len(result)),
        ]

        if file_paths:
            summary_parts.append(f"[Key files: {', '.join(set(file_paths[:5]))}]")

        if metrics:
            summary_parts.append(f"[Key metrics: {'; '.join(metrics[:5])}]")

        summary_parts.append(result[-keep_end:].strip())

        return "\n".join(summary_parts)

    def build(
        self,
        *,
        node: PlanNode,
        parent: Optional[PlanNode],
        dependencies: List[PlanNode],
        plan_outline: Optional[str],
        include_context: bool,
        session_context: Optional[Dict[str, Any]] = None,
        include_tool_hints: bool = True,
    ) -> str:
        lines: List[str] = [self.SYSTEM_HEADER]

        if session_context:
            user_message = session_context.get("user_message")
            if user_message:
                lines.append("\n=== USER REQUEST ===")
                lines.append(f"{user_message}")

            chat_history = session_context.get("chat_history", [])
            if chat_history:
                lines.append("\n=== RECENT CONVERSATION ===")
                recent_history = chat_history[-6:] if len(chat_history) > 6 else chat_history
                for msg in recent_history:
                    role = msg.get("role", "unknown")
                    content = msg.get("content", "")
                    if len(content) > 500:
                        content = content[:500] + "..."
                    lines.append(f"[{role}]: {content}")

            tool_results = session_context.get("recent_tool_results", [])
            if tool_results:
                lines.append("\n=== RECENT TOOL RESULTS ===")
                for tr in tool_results[-3:]:  # 3result
                    tool_name = tr.get("tool", "unknown")
                    summary = tr.get("summary", "")
                    lines.append(f"- {tool_name}: {summary}")

        lines.append("\n=== TASK SUMMARY ===")
        lines.append(f"Task ID: {node.id}")
        lines.append(f"Task Name: {node.display_name()}")
        if node.instruction:
            lines.append(f"Instruction: {node.instruction.strip()}")
        lines.append(f"Path: {node.path}")
        node_metadata = node.metadata if isinstance(node.metadata, dict) else {}
        paper_mode = bool(
            node_metadata.get("paper_mode")
            or (session_context or {}).get("paper_mode")
        )
        paper_section = (
            str(node_metadata.get("paper_section")).strip()
            if node_metadata.get("paper_section") is not None
            else ""
        )
        paper_role = (
            str(node_metadata.get("paper_role")).strip()
            if node_metadata.get("paper_role") is not None
            else ""
        )
        raw_paper_context_paths = node_metadata.get("paper_context_paths")
        paper_context_paths = (
            [str(item).strip() for item in raw_paper_context_paths if str(item).strip()]
            if isinstance(raw_paper_context_paths, list)
            else []
        )
        artifact_contract = node_metadata.get("artifact_contract") if isinstance(node_metadata.get("artifact_contract"), dict) else {}
        acceptance_criteria = node_metadata.get("acceptance_criteria") if isinstance(node_metadata.get("acceptance_criteria"), dict) else {}
        resolved_input_artifacts = (
            (session_context or {}).get("resolved_input_artifacts")
            if isinstance((session_context or {}).get("resolved_input_artifacts"), dict)
            else {}
        )
        required_output_paths: List[str] = []
        criteria_checks = acceptance_criteria.get("checks") if isinstance(acceptance_criteria, dict) else None
        if isinstance(criteria_checks, list):
            for check in criteria_checks:
                if not isinstance(check, dict):
                    continue
                check_type = str(check.get("type") or "").strip()
                if check_type not in {"file_exists", "file_nonempty"}:
                    continue
                raw_path = str(check.get("path") or "").strip()
                if raw_path and raw_path not in required_output_paths:
                    required_output_paths.append(raw_path)
        if acceptance_criteria or artifact_contract:
            lines.append("\n=== OUTPUT CONTRACT ===")
            if required_output_paths:
                lines.append("Required output files that must exist before the task can complete:")
                for path in required_output_paths[:20]:
                    lines.append(f"- {path}")
            requires = artifact_contract.get("requires") if isinstance(artifact_contract, dict) else None
            publishes = artifact_contract.get("publishes") if isinstance(artifact_contract, dict) else None
            if isinstance(requires, list) and requires:
                lines.append(f"Required artifact aliases: {requires}")
            if isinstance(publishes, list) and publishes:
                lines.append(f"Published artifact aliases: {publishes}")
            lines.extend(
                [
                    "Contract rule: completion is based on these exact files/artifact aliases, not on similar filenames or prose claims.",
                    "If a required file is named, create that exact file path or explicitly report why it cannot be created.",
                ]
            )
        if paper_mode:
            lines.append("\n=== PAPER MODE ===")
            if paper_section:
                lines.append(f"- paper_section: {paper_section}")
            if paper_role:
                lines.append(f"- paper_role: {paper_role}")
            requires = artifact_contract.get("requires") if isinstance(artifact_contract, dict) else None
            publishes = artifact_contract.get("publishes") if isinstance(artifact_contract, dict) else None
            if isinstance(requires, list) and requires:
                lines.append(f"- artifact_requires: {requires}")
            if isinstance(publishes, list) and publishes:
                lines.append(f"- artifact_publishes: {publishes}")
            if resolved_input_artifacts:
                lines.append("- resolved_input_artifacts:")
                for alias, path in list(resolved_input_artifacts.items())[:10]:
                    lines.append(f"  - {alias}: {path}")
            if paper_context_paths:
                lines.append("- paper_context_paths:")
                for path in paper_context_paths[:10]:
                    lines.append(f"  - {path}")
            lines.extend(
                [
                    "- Execution template:",
                    "  1) evidence organization task -> produce artifact paths and references",
                    "  2) section tasks -> write focused section drafts",
                    "  3) assembly task -> call manuscript_writer main chain",
                    "  4) citation integrity check -> block success on citekey mismatch",
                ]
            )
        if parent:
            lines.append("\n=== PARENT TASK ===")
            lines.append(f"Parent ID: {parent.id}")
            lines.append(f"Parent Name: {parent.display_name()}")
            if parent.instruction:
                lines.append(f"Parent Instruction: {parent.instruction.strip()}")
            if parent.execution_result:
                parent_result = self._summarize_long_result(parent.execution_result, max_length=2000)
                lines.append(f"Parent Latest Result: {parent_result}")

        if dependencies:
            lines.append("\n=== DEPENDENCIES ===")
            for dep in dependencies:
                dep_status = (dep.status or "").strip().lower()
                status_indicator = (
                    "✓"
                    if dep_status in {"completed", "done"}
                    else "✗"
                    if dep_status == "failed"
                    else "○"
                )
                raw_result = dep.execution_result or "(not executed)"
                summary = self._summarize_long_result(raw_result, max_length=2000)
                lines.append(f"- [{status_indicator}] [{dep.id}] {dep.display_name()}: {summary}")

        if include_context:
            lines.append("\n=== CONTEXT ===")
            if node.context_combined:
                lines.append(f"Summary: {node.context_combined}")
            if node.context_sections:
                for section in node.context_sections:
                    title = section.get("title") or "Section"
                    content = section.get("content") or ""
                    lines.append(f"- {title}: {content}")
            if not node.context_combined and not node.context_sections:
                lines.append("(no additional context)")

        if plan_outline:
            lines.append("\n=== PLAN OUTLINE (TRUNCATED) ===")
            lines.append(plan_outline)

        if include_tool_hints:
            lines.append(self.TOOL_HINTS)
        prompt = "\n".join(lines)
        if len(prompt) > self.MAX_PROMPT_CHARS:
            logger.warning(
                "Executor prompt too long (%d chars), truncating plan outline and dependencies.",
                len(prompt),
            )
            # Rebuild without plan outline and with shorter dependency summaries.
            # Remove the plan outline section to reclaim space.
            marker = "\n=== PLAN OUTLINE (TRUNCATED) ==="
            idx = prompt.find(marker)
            if idx != -1:
                end_idx = prompt.find("\n===", idx + len(marker))
                if end_idx != -1:
                    prompt = prompt[:idx] + "\n[Plan outline omitted due to prompt size limit]\n" + prompt[end_idx:]
            # Hard-truncate if still too long.
            if len(prompt) > self.MAX_PROMPT_CHARS:
                prompt = prompt[: self.MAX_PROMPT_CHARS] + "\n... [TRUNCATED]"
        return prompt


def _strip_code_fences(raw: str) -> str:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        while lines and lines[-1].strip().startswith("```"):
            lines.pop()
        cleaned = "\n".join(lines).strip()
    return cleaned
