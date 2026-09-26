"""Execute/evidence-scope truth barriers for the DeepThink agent (god-class
split, behaviour zero-change).

Each function here is the body of the like-named DeepThinkAgent method with
`self` renamed to `agent` (`cls` kept); the class keeps thin wrappers with
the same decorators. Display-family helpers (detect_reasoning_language,
_localized_text) stay in deep_think_agent and are reached through the
late-bound `_dta()` so their monkeypatch surface is unchanged. The three
claim-text predicates are imported from gating_finalize (they were already
shared across gating before the split).
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence

from app.services.deep_think.gating_finalize import (
    _answer_acknowledges_failed_status_counts,
    _looks_like_completion_claim_text,
    _looks_like_global_success_claim_text,
)
from app.services.deep_think.models import ThinkingStep
from app.services.deep_think.text_utils import _ensure_inline_images
from app.services.response_style import sanitize_professional_response_text

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.services.deep_think_agent import DeepThinkAgent

logger = logging.getLogger(__name__)


def _dta() -> Any:
    """Late-bound deep_think_agent module (monkeypatch-friendly lookups)."""
    from app.services import deep_think_agent

    return deep_think_agent


_UNVERIFIED_SAMPLE_DIRECTORY_CLAIM_RE = re.compile(
    r"(?:total\s+sample\s+director(?:y|ies)|sample\s+director(?:y|ies)\s*[:|]\s*[\d,]+|样本目录\s*[:：|]?\s*[\d,]+)",
    re.IGNORECASE,
)
_UNVERIFIED_EACH_FILE_STRUCTURE_RE = re.compile(
    r"(?:each|every|all)\s+(?:completed\s+)?(?:sample|sample\s+directory|sample\s+folder)[^\n.]{0,120}\b(?:contain|contains|has|have)\b|每个(?:已完成)?样本[^\n。]{0,80}(?:包含|含有|都有)",
    re.IGNORECASE,
)
_UNSUPPORTED_RETRY_RERUN_RE = re.compile(
    r"\b(?:retr(?:y|ied|ies)|rerun|re-run|multiple\s+pipeline\s+runs|logged\s+across\s+multiple)\b|重试|重新运行|多次运行|多轮运行",
    re.IGNORECASE,
)


def _collect_execute_truth_events(
    cls: Any,
    steps: Sequence[ThinkingStep],
) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    order = 0
    for step in steps:
        for entry in cls._extract_tool_payloads_from_step(step):
            tool_name = str(entry.get("tool") or "").strip().lower()
            payload = entry.get("payload")
            if not isinstance(payload, dict):
                continue
            inner = cls._unwrap_tool_result(payload)
            if not isinstance(inner, dict):
                continue

            raw_success = inner.get("success")
            if raw_success is None:
                raw_success = payload.get("success")
            success = bool(raw_success) if raw_success is not None else False

            blocked_reason = str(
                inner.get("blocked_reason") or payload.get("blocked_reason") or ""
            ).strip().lower()
            if (
                blocked_reason == "delegation_too_small"
                and tool_name in cls._CODE_EXECUTION_TOOLS
            ):
                # A one-script delegation refusal is policy, not an execution
                # attempt: counting it as a failed execution would make the
                # answer read "the main execution tool failed" for a nudge, and
                # would burn the failure-signature trap's budget.
                continue

            operation = str(
                inner.get("operation")
                or payload.get("operation")
                or ""
            ).strip().lower()
            task_type = str(inner.get("task_type") or "").strip().lower()
            execution_status = str(inner.get("execution_status") or "").strip().lower()
            verification_state = str(inner.get("verification_state") or "").strip().lower()

            kind: Optional[str] = None
            trusted = False
            if tool_name == "result_interpreter":
                if operation in {"profile", "metadata"} or (
                    operation == "analyze" and task_type == "text_only"
                ):
                    kind = "profile"
                    trusted = success
                elif operation in {"execute", "analyze"}:
                    kind = "execution"
                    trusted = success and (
                        execution_status == "success" or not execution_status
                    )
            elif tool_name == "terminal_session":
                if operation == "write":
                    kind = "execution"
                    trusted = success and verification_state == "verified_success"
            elif tool_name in cls._CODE_EXECUTION_TOOLS:
                kind = "execution"
                trusted = success

            if kind is None:
                continue

            summary_text = ""
            profile_payload = inner.get("profile")
            if isinstance(profile_payload, dict):
                profile_summary = profile_payload.get("summary")
                if isinstance(profile_summary, str) and profile_summary.strip():
                    summary_text = profile_summary.strip()
            if not summary_text:
                execution_output = inner.get("execution_output")
                if isinstance(execution_output, str) and execution_output.strip():
                    summary_text = execution_output.strip()
            if not summary_text:
                for key in ("summary", "error", "execution_error", "message"):
                    candidate = str(
                        inner.get(key) or payload.get(key) or ""
                    ).strip()
                    if candidate:
                        summary_text = candidate
                        break

            error_text = str(
                inner.get("error")
                or inner.get("execution_error")
                or payload.get("error")
                or ""
            ).strip()

            events.append(
                {
                    "order": order,
                    "iteration": step.iteration,
                    "tool": tool_name,
                    "operation": operation,
                    "kind": kind,
                    "success": success,
                    "trusted": trusted,
                    "summary_text": summary_text,
                    "error": error_text,
                }
            )
            order += 1
    return events


def _build_execute_failure_warning(
    agent: "DeepThinkAgent",
    *,
    user_query: str,
    failed_event: Dict[str, Any],
) -> str:
    """Soft warning prepended to the model's answer when execution failed
    but the model produced substantive content from read-only tools."""
    language = _dta().detect_reasoning_language(user_query or "")
    tool_name = str(failed_event.get("tool") or "execution tool").strip()
    failure_detail = str(
        failed_event.get("error")
        or failed_event.get("summary_text")
        or "unknown failure"
    ).strip()
    return _dta()._localized_text(
        language,
        (
            f"> ⚠️ **注意**：本轮主执行工具未成功（{tool_name} 失败：{failure_detail}）。"
            "以下内容基于文件读取工具的输出，统计数值未经代码验证，仅供参考。"
        ),
        (
            f"> ⚠️ **Warning**: The main execution tool failed in this run "
            f"({tool_name}: {failure_detail}). "
            f"The content below is based on file-reading tools; "
            f"statistical figures are not code-verified and should be treated as approximate."
        ),
    )


def _build_execute_failure_truth_barrier(
    agent: "DeepThinkAgent",
    *,
    user_query: str,
    failed_event: Dict[str, Any],
    profile_text: Optional[str] = None,
) -> str:
    language = _dta().detect_reasoning_language(user_query or profile_text or "")
    tool_name = str(failed_event.get("tool") or "execution tool").strip()
    failure_detail = str(
        failed_event.get("error")
        or failed_event.get("summary_text")
        or "unknown failure"
    ).strip()

    if profile_text:
        return _dta()._localized_text(
            language,
            (
                f"说明：本轮真正的执行工具未成功完成（{tool_name} 失败：{failure_detail}）。"
                "下面只保留本轮已验证的确定性数据 profile 结果，不把它当作完整分析已完成：\n\n"
                f"{profile_text}"
            ),
            (
                f"Note: The main execution tool did not complete successfully in this run "
                f"({tool_name} failed: {failure_detail}). The content below is limited to "
                f"verified deterministic dataset profiling from this run and should not be "
                f"treated as a completed full analysis.\n\n{profile_text}"
            ),
        )

    return _dta()._localized_text(
        language,
        (
            f"本轮真正的执行工具未成功完成（{tool_name} 失败：{failure_detail}）。"
            "因此不能把后续分析性表述视为已验证结论。当前只能确认执行被该错误阻塞；"
            "如需继续，请先修复该失败原因后再重新运行。"
        ),
        (
            f"The main execution tool did not complete successfully in this run "
            f"({tool_name} failed: {failure_detail}). Any later analysis-style narrative "
            f"cannot be treated as verified. At this point the run is blocked by that error; "
            f"fix the failure first and rerun to obtain a trustworthy result."
        ),
    )


def _append_produced_image_section(
    agent: "DeepThinkAgent",
    text: str,
    *,
    user_query: str = "",
) -> str:
    """Keep already-produced figures reachable when a run ends on a failure.

    The barrier replaces the answer with an execution-failed message. Without
    this, figures that *do* exist on disk disappear from the answer entirely
    (production 2026-09-26: a four-panel CJK chart had been written to
    ``deliverables/`` before the code generator died on a gateway 504, and the
    final answer neither showed nor mentioned it). The section is explicitly
    marked unverified so the barrier's honesty contract stays intact.
    """
    relpaths = [str(path) for path in (agent._collect_inline_image_relpaths() or [])]
    if not relpaths:
        return text
    language = _dta().detect_reasoning_language(user_query or "")
    listing = "\n".join(f"- {rel}" for rel in relpaths)
    if language == "zh":
        note = (
            "本轮执行虽被上述错误中断，但已产出以下图像文件（未经核验，仅供参考）：\n"
            f"{listing}"
        )
    else:
        note = (
            "Execution was interrupted by the error above, but these image files were "
            f"produced (not verified, use with caution):\n{listing}"
        )
    return _ensure_inline_images(f"{text}\n\n{note}", relpaths)


def _apply_execute_failure_truth_barrier(
    agent: "DeepThinkAgent",
    answer: str,
    *,
    user_query: str,
    steps: Sequence[ThinkingStep],
) -> str:
    text = str(answer or "").strip()
    if not text or not agent._is_execute_task_request():
        return text

    def _barrier(payload: str) -> str:
        return _append_produced_image_section(agent, payload, user_query=user_query)

    events = agent._collect_execute_truth_events(steps)
    failed_execution_events = [
        event
        for event in events
        if event.get("kind") == "execution" and not event.get("success")
    ]
    if not failed_execution_events:
        return text

    last_failure = failed_execution_events[-1]
    last_failure_order = int(last_failure.get("order", -1))
    later_events = [
        event for event in events if int(event.get("order", -1)) > last_failure_order
    ]

    if any(
        event.get("kind") == "execution" and event.get("trusted")
        for event in later_events
    ):
        return text

    profile_recovery = next(
        (
            event
            for event in reversed(later_events)
            if event.get("kind") == "profile"
            and event.get("trusted")
            and str(event.get("summary_text") or "").strip()
        ),
        None,
    )
    if profile_recovery is not None:
        barrier = agent._build_execute_failure_truth_barrier(
            user_query=user_query,
            failed_event=last_failure,
            profile_text=str(profile_recovery.get("summary_text") or "").strip(),
        )
        return _barrier(barrier)

    # Check if the model's answer contains substantive content from
    # successful read tools (document_reader, file_operations, etc.).
    # If so, prepend a warning instead of replacing the entire answer,
    # so partial results are preserved for the user.
    has_substantive_answer = len(text) > 200
    if has_substantive_answer:
        if _looks_like_completion_claim_text(text):
            return _barrier(
                agent._build_execute_failure_truth_barrier(
                    user_query=user_query,
                    failed_event=last_failure,
                )
            )
        warning = agent._build_execute_failure_warning(
            user_query=user_query,
            failed_event=last_failure,
        )
        return f"{warning}\n\n---\n\n{text}"

    return _barrier(
        agent._build_execute_failure_truth_barrier(
            user_query=user_query,
            failed_event=last_failure,
        )
    )


def _collect_evidence_scope_signals(cls: Any, steps: Sequence[ThinkingStep]) -> List[Dict[str, Any]]:
    signals: List[Dict[str, Any]] = []
    for step in steps:
        for entry in cls._extract_tool_payloads_from_step(step):
            tool_name = str(entry.get("tool") or "").strip().lower()
            payload = entry.get("payload")
            if not isinstance(payload, dict):
                continue
            inner = cls._unwrap_tool_result(payload)
            if not isinstance(inner, dict):
                continue
            evidence_scope = inner.get("evidence_scope")
            if not isinstance(evidence_scope, dict):
                evidence_scope = payload.get("evidence_scope")
            status_counts = None
            if isinstance(evidence_scope, dict):
                status_counts = evidence_scope.get("status_counts")
            if status_counts is None and isinstance(inner.get("counts"), dict):
                counts = inner.get("counts")
                if any(key in counts for key in ("completed", "failed", "status_file_total")):
                    status_counts = {
                        key: counts.get(key)
                        for key in ("completed", "failed", "status_file_total")
                        if key in counts
                    }
            status_count_sources = None
            if isinstance(inner.get("status_count_sources"), list):
                status_count_sources = inner.get("status_count_sources")
            elif isinstance(evidence_scope, dict) and isinstance(evidence_scope.get("status_count_sources"), list):
                status_count_sources = evidence_scope.get("status_count_sources")
            status_counts_confidence = inner.get("status_counts_confidence")
            if status_counts_confidence is None and isinstance(evidence_scope, dict):
                status_counts_confidence = evidence_scope.get("status_counts_confidence")
            signal: Dict[str, Any] = {
                "tool": tool_name,
                "operation": str(inner.get("operation") or payload.get("operation") or "").strip().lower(),
                "path": inner.get("path") or payload.get("path"),
                "counts": inner.get("counts") if isinstance(inner.get("counts"), dict) else None,
                "summary": inner.get("summary") if isinstance(inner.get("summary"), str) else None,
                "sample_items": inner.get("sample_items") if isinstance(inner.get("sample_items"), list) else None,
                "evidence_scope": evidence_scope if isinstance(evidence_scope, dict) else None,
                "reconciliation": inner.get("reconciliation") if isinstance(inner.get("reconciliation"), dict) else (
                    evidence_scope.get("reconciliation") if isinstance(evidence_scope, dict) and isinstance(evidence_scope.get("reconciliation"), dict) else None
                ),
                "completeness_status": inner.get("completeness_status") or payload.get("completeness_status"),
                "status_counts": status_counts if isinstance(status_counts, dict) else None,
                "status_count_sources": status_count_sources,
                "status_counts_confidence": status_counts_confidence,
                "incomplete_examples": inner.get("incomplete_examples") if isinstance(inner.get("incomplete_examples"), list) else None,
                "partial_completion_suspected": bool(
                    inner.get("partial_completion_suspected")
                    or payload.get("partial_completion_suspected")
                ),
                "partial_ratio": inner.get("partial_ratio") or payload.get("partial_ratio"),
            }
            if signal["evidence_scope"] or signal["status_counts"] or signal["reconciliation"] or signal["incomplete_examples"] or signal["partial_completion_suspected"]:
                signals.append(signal)
    return signals


def _build_evidence_scope_notice(
    agent: "DeepThinkAgent",
    *,
    user_query: str,
    signals: Sequence[Dict[str, Any]],
    replace_claim: bool = False,
) -> str:
    completed: Optional[int] = None
    failed: Optional[int] = None
    failure_examples: List[str] = []
    partial_ratio = ""
    sampled_or_partial = False
    paths: List[str] = []
    profile_summaries: List[str] = []
    status_source_names: List[str] = []
    status_directory_names: List[str] = []
    reconciliation_notes: List[str] = []
    reconciliation_missing_examples: List[str] = []
    reconciliation_guidance: List[str] = []
    suffix_profile_text = ""
    success_structure_text = ""
    sampled_structure_notes: List[str] = []
    seen_sources: set[tuple[str, str, str]] = set()
    seen_profiles: set[str] = set()
    seen_status_names: set[str] = set()
    seen_reconciliation_notes: set[str] = set()
    seen_missing_examples: set[str] = set()
    seen_reconciliation_guidance: set[str] = set()
    seen_sampled_structure_notes: set[str] = set()

    for signal in signals:
        path = str(signal.get("path") or "").strip()
        if path and path not in paths:
            paths.append(path)
        counts = signal.get("counts") if isinstance(signal.get("counts"), dict) else {}
        if counts:
            profile_key = path or str(signal.get("operation") or "profile")
            if profile_key not in seen_profiles:
                seen_profiles.add(profile_key)
                metric_parts: List[str] = []
                for key, label in (
                    ("direct_children", "direct_children"),
                    ("directories", "directories"),
                    ("sample_candidate_directories", "sample_candidate_directories"),
                    ("status_directories", "status_directories"),
                    ("files", "files"),
                    ("other", "other"),
                ):
                    value = counts.get(key)
                    if isinstance(value, int):
                        metric_parts.append(f"{label}={value}")
                if metric_parts:
                    profile_summaries.append(", ".join(metric_parts))
        evidence_scope = signal.get("evidence_scope") if isinstance(signal.get("evidence_scope"), dict) else {}
        directory_classification = (
            evidence_scope.get("directory_classification")
            if isinstance(evidence_scope.get("directory_classification"), dict)
            else {}
        )
        if directory_classification:
            for name in directory_classification.get("status_directory_names") or []:
                name_text = str(name or "").strip()
                if name_text and name_text not in status_directory_names:
                    status_directory_names.append(name_text)
        completeness = str(
            signal.get("completeness_status")
            or evidence_scope.get("completeness_status")
            or ""
        ).strip().lower()
        enumeration = evidence_scope.get("enumeration") if isinstance(evidence_scope.get("enumeration"), dict) else {}
        omitted = enumeration.get("omitted_children")
        if completeness in {"partial", "unknown"} or (isinstance(omitted, int) and omitted > 0):
            sampled_or_partial = True
        status_counts = signal.get("status_counts") if isinstance(signal.get("status_counts"), dict) else {}
        status_total = status_counts.get("status_file_total")
        sample_candidates = directory_classification.get("sample_candidate_directories")
        if isinstance(status_total, int) and isinstance(sample_candidates, int) and status_total != sample_candidates:
            note = f"status_file_total={status_total} differs from sample_candidate_directories={sample_candidates}"
            if note not in seen_reconciliation_notes:
                seen_reconciliation_notes.add(note)
                reconciliation_notes.append(note)
        reconciliation = signal.get("reconciliation") if isinstance(signal.get("reconciliation"), dict) else {}
        if reconciliation:
            rec_counts = reconciliation.get("counts") if isinstance(reconciliation.get("counts"), dict) else {}
            rec_examples = reconciliation.get("examples") if isinstance(reconciliation.get("examples"), dict) else {}
            status_missing = rec_counts.get("status_entries_missing_directories")
            failure_missing = rec_counts.get("failure_missing_directories")
            success_missing = rec_counts.get("success_missing_directories")
            sample_without_status = rec_counts.get("sample_dirs_without_status")
            rec_note_parts: List[str] = []
            for key, label in (
                ("status_unique_total", "status_unique_total"),
                ("sample_candidate_directories", "sample_candidate_directories"),
                ("status_entries_missing_directories", "status_entries_missing_directories"),
                ("failure_missing_directories", "failure_missing_directories"),
                ("success_missing_directories", "success_missing_directories"),
                ("sample_dirs_without_status", "sample_dirs_without_status"),
                ("duplicate_success_entries", "duplicate_success_entries"),
                ("duplicate_failure_entries", "duplicate_failure_entries"),
                ("success_failure_overlap", "success_failure_overlap"),
            ):
                value = rec_counts.get(key)
                if isinstance(value, int):
                    rec_note_parts.append(f"{label}={value}")
            if rec_note_parts:
                note = ", ".join(rec_note_parts)
                if note not in seen_reconciliation_notes:
                    seen_reconciliation_notes.add(note)
                    reconciliation_notes.append(note)
            for key in ("failure_missing_directories", "success_missing_directories", "sample_dirs_without_status", "success_failure_overlap"):
                values = rec_examples.get(key)
                if not isinstance(values, list) or not values:
                    continue
                for value in values[:5]:
                    text_value = str(value or "").strip()
                    if text_value and text_value not in seen_missing_examples:
                        seen_missing_examples.add(text_value)
                        reconciliation_missing_examples.append(text_value)
            for item in reconciliation.get("claim_guidance") or []:
                guidance = str(item or "").strip()
                if guidance and guidance not in seen_reconciliation_guidance:
                    seen_reconciliation_guidance.add(guidance)
                    reconciliation_guidance.append(guidance)
            if isinstance(status_missing, int) and status_missing and not (rec_counts.get("duplicate_success_entries") or rec_counts.get("duplicate_failure_entries") or rec_counts.get("success_failure_overlap")):
                guidance = "Status/directory mismatch is explained by status IDs without matching directories; do not infer retries/reruns from this evidence."
                if guidance not in seen_reconciliation_guidance:
                    seen_reconciliation_guidance.add(guidance)
                    reconciliation_guidance.append(guidance)
            name_profile = reconciliation.get("sample_directory_name_profile") if isinstance(reconciliation.get("sample_directory_name_profile"), dict) else {}
            suffix_counts = name_profile.get("hyphen_suffix_counts") if isinstance(name_profile.get("hyphen_suffix_counts"), dict) else {}
            if suffix_counts and not suffix_profile_text:
                suffix_profile_text = ", ".join(f"{key}={value}" for key, value in list(suffix_counts.items())[:12])
            structure = reconciliation.get("success_directory_structure") if isinstance(reconciliation.get("success_directory_structure"), dict) else {}
            file_distribution = structure.get("file_count_distribution") if isinstance(structure.get("file_count_distribution"), dict) else {}
            if file_distribution and not success_structure_text:
                scanned = structure.get("directories_scanned")
                considered = structure.get("entries_considered")
                patterns = structure.get("common_file_patterns") if isinstance(structure.get("common_file_patterns"), list) else []
                pattern_text = ", ".join(
                    str(item.get("pattern"))
                    for item in patterns[:5]
                    if isinstance(item, dict) and item.get("pattern")
                )
                success_structure_text = (
                    f"success directories scanned={scanned}/{considered}, file_count_distribution={file_distribution}"
                    + (f", common_file_patterns={pattern_text}" if pattern_text else "")
                )
        count_sources = signal.get("status_count_sources") if isinstance(signal.get("status_count_sources"), list) else []
        if count_sources:
            for source in count_sources:
                if not isinstance(source, dict):
                    continue
                if source.get("count_confidence") != "high":
                    continue
                source_path = str(source.get("path") or source.get("name") or path).strip()
                kind = str(source.get("kind") or "").strip()
                key = (source_path, kind, str(source.get("count_source") or ""))
                if key in seen_sources:
                    continue
                seen_sources.add(key)
                if source_path and source_path not in seen_status_names:
                    seen_status_names.add(source_path)
                    status_source_names.append(str(source.get("name") or source_path).strip())
                entry_count = source.get("entry_count")
                if not isinstance(entry_count, int):
                    continue
                if kind == "success":
                    completed = (completed or 0) + entry_count
                elif kind == "failure":
                    failed = (failed or 0) + entry_count
        else:
            fallback_key = (path, str(signal.get("operation") or ""), "status_counts")
            if fallback_key not in seen_sources:
                seen_sources.add(fallback_key)
                if isinstance(status_counts.get("completed"), int):
                    completed = (completed or 0) + int(status_counts["completed"])
                if isinstance(status_counts.get("failed"), int):
                    failed = (failed or 0) + int(status_counts["failed"])
        examples = signal.get("incomplete_examples")
        if isinstance(examples, list):
            for item in examples[:5]:
                if isinstance(item, dict):
                    name = str(item.get("name") or "").strip()
                    reason = str(item.get("reason") or "").strip()
                    if name:
                        failure_examples.append(f"{name} ({reason})" if reason else name)
        if signal.get("partial_completion_suspected"):
            sampled_or_partial = True
            if signal.get("partial_ratio"):
                partial_ratio = str(signal.get("partial_ratio"))
        sample_items = signal.get("sample_items") if isinstance(signal.get("sample_items"), list) else []
        sampled_dirs = [
            item
            for item in sample_items
            if isinstance(item, dict)
            and str(item.get("type") or "").strip().lower() == "directory"
            and item.get("child_count") is not None
        ]
        if sampled_dirs:
            structure_counts = sorted(
                {
                    int(item.get("child_count"))
                    for item in sampled_dirs
                    if isinstance(item.get("child_count"), int)
                }
            )
            note = (
                f"per-sample file structure is based on {len(sampled_dirs)} sampled direct child directories"
                + (f" with observed child_count values {structure_counts[:5]}" if structure_counts else "")
            )
            if note not in seen_sampled_structure_notes:
                seen_sampled_structure_notes.add(note)
                sampled_structure_notes.append(note)

    path_text = ", ".join(paths[:3]) if paths else "the inspected path"
    completed_text = completed if completed is not None else "unknown"
    failed_text = failed if failed is not None else "unknown"
    examples_text = ", ".join(failure_examples[:5])
    profile_text = "; ".join(profile_summaries[:3])
    source_text = ", ".join(status_source_names[:5])
    status_dirs_text = ", ".join(status_directory_names[:5])
    reconciliation_text = "; ".join(reconciliation_notes[:3])
    reconciliation_examples_text = ", ".join(reconciliation_missing_examples[:8])
    reconciliation_guidance_text = " ".join(reconciliation_guidance[:3])
    sampled_structure_text = "; ".join(sampled_structure_notes[:3])
    label = "Corrected evidence-scoped conclusion" if replace_claim else "Evidence-scope note"
    if replace_claim:
        lines = [f"{label}:"]
        lines.append(f"- Evidence scope: tool output for {path_text}.")
        if profile_text:
            lines.append(f"- Directory profile: {profile_text}.")
        if completed is not None or failed is not None:
            lines.append(f"- Status counts: completed={completed_text}, failed={failed_text}.")
        if source_text:
            lines.append(f"- Count sources: {source_text}.")
        if status_dirs_text:
            lines.append(f"- Status/progress directories: {status_dirs_text}. Do not report root direct_children as verified sample-directory count.")
        if reconciliation_text:
            lines.append(f"- Reconciliation needed: {reconciliation_text}.")
        if reconciliation_examples_text:
            lines.append(f"- Reconciliation examples: {reconciliation_examples_text}.")
        if reconciliation_guidance_text:
            lines.append(f"- Reconciliation interpretation limit: {reconciliation_guidance_text}")
        if suffix_profile_text:
            lines.append(f"- Sample-name suffix distribution: {suffix_profile_text}.")
        if success_structure_text:
            lines.append(f"- Completed-directory file structure: {success_structure_text}.")
        if sampled_structure_text:
            lines.append(f"- Per-sample file structure evidence: {sampled_structure_text}; do not state that each/all samples share that structure.")
        if partial_ratio:
            lines.append(f"- Partial-completion signal: {partial_ratio}.")
        if examples_text:
            lines.append(f"- Failure/incomplete examples: {examples_text}.")
        if sampled_or_partial:
            lines.append("- Scope caution: Do not treat sampled or compacted listings as evidence that all samples succeeded.")
        lines.append("- Correction: the original all-success/global completion claim is not supported by the available evidence.")
        return "\n".join(lines)

    parts = [f"{label}: this run is limited to tool output for {path_text}. "]
    if profile_text:
        parts.append(f"Directory profile: {profile_text}. ")
    if completed is not None or failed is not None:
        parts.append(f"Observed status counts: completed={completed_text}, failed={failed_text}. ")
    if status_dirs_text:
        parts.append(f"Status/progress directories: {status_dirs_text}; root direct_children is not a verified sample-directory count. ")
    if reconciliation_text:
        parts.append(f"Reconciliation needed: {reconciliation_text}. ")
    if reconciliation_examples_text:
        parts.append(f"Reconciliation examples: {reconciliation_examples_text}. ")
    if reconciliation_guidance_text:
        parts.append(f"Reconciliation interpretation limit: {reconciliation_guidance_text} ")
    if suffix_profile_text:
        parts.append(f"Sample-name suffix distribution: {suffix_profile_text}. ")
    if success_structure_text:
        parts.append(f"Completed-directory file structure: {success_structure_text}. ")
    if sampled_structure_text:
        parts.append(f"Per-sample file structure evidence: {sampled_structure_text}; do not state that each/all samples share that structure. ")
    if partial_ratio:
        parts.append(f"A partial-completion signal was detected: {partial_ratio}. ")
    if examples_text:
        parts.append(f"Failure/incomplete examples: {examples_text}. ")
    if sampled_or_partial:
        parts.append("Do not treat sampled or compacted listings as evidence that all samples succeeded.")
    if replace_claim:
        parts.append(" The original global success claim is not supported by the available evidence.")
    return "".join(parts).strip()


def _apply_evidence_scope_truth_barrier(
    agent: "DeepThinkAgent",
    answer: str,
    *,
    user_query: str,
    steps: Sequence[ThinkingStep],
) -> str:
    text = str(answer or "").strip()
    if not text:
        return text
    signals = agent._collect_evidence_scope_signals(steps)
    if not signals:
        return text
    if _answer_acknowledges_failed_status_counts(text, signals):
        return text

    needs_notice = False
    replace_claim = False
    has_global_success_claim = _looks_like_global_success_claim_text(text)
    has_unverified_sample_directory_claim = bool(_UNVERIFIED_SAMPLE_DIRECTORY_CLAIM_RE.search(text))
    has_unverified_each_file_structure_claim = bool(_UNVERIFIED_EACH_FILE_STRUCTURE_RE.search(text))
    has_unsupported_retry_rerun_claim = bool(_UNSUPPORTED_RETRY_RERUN_RE.search(text))
    for signal in signals:
        evidence_scope = signal.get("evidence_scope") if isinstance(signal.get("evidence_scope"), dict) else {}
        enumeration = evidence_scope.get("enumeration") if isinstance(evidence_scope.get("enumeration"), dict) else {}
        omitted = enumeration.get("omitted_children")
        status_counts = signal.get("status_counts") if isinstance(signal.get("status_counts"), dict) else {}
        counts = signal.get("counts") if isinstance(signal.get("counts"), dict) else {}
        directory_classification = (
            evidence_scope.get("directory_classification")
            if isinstance(evidence_scope.get("directory_classification"), dict)
            else {}
        )
        if has_unverified_sample_directory_claim and (
            counts.get("status_directories")
            or directory_classification.get("status_directories")
            or directory_classification.get("sample_candidate_directories") != counts.get("direct_children")
        ):
            needs_notice = True
            replace_claim = True
        if has_unverified_each_file_structure_claim:
            sample_items = signal.get("sample_items") if isinstance(signal.get("sample_items"), list) else []
            reconciliation = signal.get("reconciliation") if isinstance(signal.get("reconciliation"), dict) else {}
            success_structure = reconciliation.get("success_directory_structure") if isinstance(reconciliation.get("success_directory_structure"), dict) else {}
            file_distribution = success_structure.get("file_count_distribution") if isinstance(success_structure.get("file_count_distribution"), dict) else {}
            complete_structure_scan = bool(success_structure.get("complete_scan"))
            if not (complete_structure_scan and len(file_distribution) == 1):
                if sample_items:
                    needs_notice = True
                    replace_claim = True
        if has_unsupported_retry_rerun_claim:
            reconciliation = signal.get("reconciliation") if isinstance(signal.get("reconciliation"), dict) else {}
            rec_counts = reconciliation.get("counts") if isinstance(reconciliation.get("counts"), dict) else {}
            has_retry_evidence = bool(
                rec_counts.get("duplicate_success_entries")
                or rec_counts.get("duplicate_failure_entries")
                or rec_counts.get("success_failure_overlap")
            )
            if reconciliation and not has_retry_evidence:
                needs_notice = True
                replace_claim = True
        if isinstance(status_counts.get("failed"), int) and status_counts.get("failed", 0) > 0:
            needs_notice = True
            if has_global_success_claim:
                replace_claim = True
        if signal.get("partial_completion_suspected"):
            needs_notice = True
            if has_global_success_claim:
                replace_claim = True
        if isinstance(omitted, int) and omitted > 0 and has_global_success_claim:
            needs_notice = True
            replace_claim = True
        completeness = str(
            signal.get("completeness_status")
            or evidence_scope.get("completeness_status")
            or ""
        ).strip().lower()
        if completeness in {"partial", "unknown"} and has_global_success_claim:
            needs_notice = True
            replace_claim = True
    if not needs_notice:
        return text

    notice = sanitize_professional_response_text(
        agent._build_evidence_scope_notice(
            user_query=user_query,
            signals=signals,
            replace_claim=replace_claim,
        )
    )
    if not notice or text.startswith(notice):
        return text
    if replace_claim:
        return notice
    return f"{notice}\n\n{text}"
