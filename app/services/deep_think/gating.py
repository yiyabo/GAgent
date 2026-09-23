"""Execution gating facade for the DeepThink agent (god-class split,
behaviour zero-change).

Implementations moved to focused sibling modules in this package; every
name is re-exported here so ``_gating._xxx`` attribute access (the
DeepThinkAgent thin wrappers) and direct imports keep working unchanged:

- ``gating_probe.py``: probe/verification-only detection, partial-completion
  retry nudges, and execution follow-through (incl. task handoff).
- ``gating_finalize.py``: completion-claim predicates, verified-execution
  finalization, and post-execution answers.
- ``gating_payloads.py``: tool-result payload extraction/validation and
  final-answer predicates.
- ``gating_truth.py``: execute-failure and evidence-scope truth barriers.
- ``gating_plans.py``: plan operation events, structured plan contract, and
  dataset/PhageScope profile gates.

Display-family helpers (detect_reasoning_language, _localized_text,
is_process_only_answer) stay in deep_think_agent and are reached from the
sibling modules through their own late-bound ``_dta()`` so the monkeypatch
surface is unchanged. deep_think_agent continues to re-import the three
claim-text helpers from this facade, so its module namespace (and the test
import surface) is unchanged.

Sanctioned deviation from verbatim: _extract_recommended_tool_from_instruction
reads the class attribute _FOLLOWTHROUGH_TOOL_CANDIDATES through
_dta().DeepThinkAgent because the attribute stays on DeepThinkAgent and a
runtime import of the class there would be circular; the lookup still
happens at call time on the same class object, so behaviour is identical.
"""

from __future__ import annotations

from typing import Any

from app.services.deep_think.gating_finalize import (
    _answer_acknowledges_failed_status_counts,
    _build_blocked_dependency_answer,
    _build_post_execution_probe_stop_answer,
    _build_verified_execution_finalize_nudge,
    _extract_blocked_dependency_clue,
    _looks_like_completion_claim_text,
    _looks_like_global_success_claim_text,
    _should_force_verified_execution_finalization,
)
from app.services.deep_think.gating_payloads import (
    _apply_external_search_notice,
    _collect_output_refs_from_tool_results,
    _collect_task_scoped_output_refs_from_tool_results,
    _collect_tool_failures_from_steps,
    _collect_verified_output_refs_from_tool_results,
    _extract_outcomes_from_step,
    _extract_tool_payloads_from_step,
    _extract_tool_result_payload,
    _is_task_scoped_output_ref,
    _is_valid_final_answer,
    _iter_tool_payload_dicts,
    _looks_like_blocked_dependency_answer,
    _looks_like_missing_task_definition_answer,
    _payload_dict_indicates_verified_success,
    _search_verified_from_steps,
    _should_reject_missing_task_definition_answer,
    _should_retry_external_tool,
    _summarize_tool_payload_for_clue,
    _tool_counts_as_real_execution,
    _tool_results_indicate_verified_success,
    _try_parse_json_object,
    _unwrap_tool_result,
)
from app.services.deep_think.gating_plans import (
    _build_phagescope_deep_profile_failure_answer,
    _build_structured_plan_contract_failure_answer,
    _collect_plan_operation_events,
    _directory_dataset_analysis_requested,
    _directory_payload_is_generic_tabular_only,
    _directory_positively_lacks_phagescope_meta_data,
    _ensure_structured_plan_notice,
    _extract_directory_path_from_query,
    _extract_successful_created_plan_from_tool_results,
    _file_operation_profile_or_census_seen,
    _needs_directory_profile_before_final,
    _needs_phagescope_deep_profile_before_final,
    _path_is_generic_tabular_file,
    _phagescope_dataset_analysis_requested,
    _phagescope_deep_profile_failure,
    _phagescope_deep_profile_seen,
    _summarize_structured_plan_outcome,
)
from app.services.deep_think.gating_probe import (
    _build_forced_handoff_followthrough_task,
    _build_forced_probe_followthrough_task,
    _build_partial_completion_retry_nudge,
    _build_post_execution_summary_nudge,
    _build_probe_only_followthrough_nudge,
    _build_task_handoff_execution_nudge,
    _can_force_handoff_followthrough_execution,
    _can_force_probe_followthrough_execution,
    _detect_partial_completion_in_tool_results,
    _execute_forced_handoff_followthrough,
    _execute_forced_probe_followthrough,
    _extract_recommended_tool_from_instruction,
    _is_probe_only_execution_cycle,
    _is_verification_only_tool_result_cycle,
    _task_context_upstream_artifact_paths,
    _verification_only_cycle_replacement_task_id,
)
from app.services.deep_think.gating_truth import (
    _apply_evidence_scope_truth_barrier,
    _apply_execute_failure_truth_barrier,
    _build_evidence_scope_notice,
    _build_execute_failure_truth_barrier,
    _build_execute_failure_warning,
    _collect_evidence_scope_signals,
    _collect_execute_truth_events,
)


def _dta() -> Any:
    """Late-bound deep_think_agent module (monkeypatch-friendly lookups)."""
    from app.services import deep_think_agent

    return deep_think_agent
