"""Failure classification and recovery strategies for plan execution.

When a task fails during automatic plan execution, the FailureAnalyzer
classifies the failure into a category. If the category is recoverable,
the plan executor can attempt automatic recovery (e.g., re-running
upstream dependencies or retrying with error context).

Classification priority:
1. Structured metadata fields (``blocked_by_dependencies``,
   ``error_category``, ``status``) — deterministic, no guessing.
2. Text-based heuristics on ``execution_result`` — fallback only.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Optional, Sequence


class FailureCategory(Enum):
    """Classification of task failure causes."""

    UPSTREAM_INCOMPLETE = "upstream_incomplete"
    CODE_BUG = "code_bug"
    TIMEOUT = "timeout"
    DATA_MISSING = "data_missing"
    UNRECOVERABLE = "unrecoverable"


# Categories that the plan executor should attempt to recover from.
RECOVERABLE = frozenset({
    FailureCategory.UPSTREAM_INCOMPLETE,
    FailureCategory.CODE_BUG,
    FailureCategory.TIMEOUT,
})


class FailureAnalyzer:
    """Classify a task execution failure into a recoverable/unrecoverable category.

    The primary classification path uses **structured metadata** produced by
    the plan executor and tool backends.  Text-based keyword matching is
    retained only as a last-resort fallback.
    """

    # --- text fallback signals (kept for legacy / free-text results) ---

    _UPSTREAM_TEXT_SIGNALS: Sequence[str] = (
        "blocked_dependency",
        "upstream",
        "missing filtered data",
        "requires the output from task",
        "ensure task",
        "上游产物",
        "依赖缺失",
    )

    _CODE_BUG_TEXT_SIGNALS: Sequence[str] = (
        "traceback",
        "syntaxerror",
        "nameerror",
        "typeerror",
        "indexerror",
        "keyerror",
        "attributeerror",
        "valueerror",
        "zerodivisionerror",
        "importerror",
    )

    _TIMEOUT_TEXT_SIGNALS: Sequence[str] = (
        "timed out",
        "execution exceeded",
    )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def classify(
        self,
        execution_result: str,
        metadata: dict,
        *,
        result_status: Optional[str] = None,
        result_metadata: Optional[Dict[str, Any]] = None,
    ) -> FailureCategory:
        """Return the failure category for a failed or skipped task.

        Args:
            execution_result: Raw execution_result text (may be JSON or free text).
            metadata: Parsed metadata dict from the ``ExecutionResult``.
            result_status: The ``ExecutionResult.status`` string (e.g. "failed",
                "skipped").  Passing this enables structured classification for
                skipped-by-dependency tasks.
            result_metadata: The full ``ExecutionResult.metadata`` dict.  When
                provided, takes precedence over *metadata* for structured
                field lookups.  The *metadata* parameter is still consulted
                as a fallback so that callers that only have one dict still
                work.
        """
        # Merge metadata sources — prefer result_metadata when available.
        effective_meta: Dict[str, Any] = dict(metadata or {})
        if result_metadata:
            effective_meta.update(result_metadata)

        # ----- Layer 1: Fully-structured classification -----

        # 1a. blocked_by_dependencies (set by _run_task when deps incomplete)
        if effective_meta.get("blocked_by_dependencies"):
            return FailureCategory.UPSTREAM_INCOMPLETE

        # 1b. error_category explicitly set by tool backends (e.g. code_executor)
        error_cat = effective_meta.get("error_category")
        if isinstance(error_cat, str):
            cat_lower = error_cat.strip().lower()
            if cat_lower in ("blocked_dependency", "upstream_incomplete"):
                return FailureCategory.UPSTREAM_INCOMPLETE
            if cat_lower == "timeout":
                return FailureCategory.TIMEOUT
            if cat_lower == "data_missing":
                return FailureCategory.DATA_MISSING

        # 1c. incomplete_dependencies list present
        incomplete_deps = effective_meta.get("incomplete_dependencies")
        if isinstance(incomplete_deps, list) and incomplete_deps:
            return FailureCategory.UPSTREAM_INCOMPLETE

        # 1d. status-based shortcut — a "skipped" result with dependency info
        #     is always UPSTREAM_INCOMPLETE regardless of text content.
        if result_status == "skipped" and effective_meta.get("enforce_dependencies"):
            return FailureCategory.UPSTREAM_INCOMPLETE

        # 1e. runtime_failure flag from code_executor
        if effective_meta.get("runtime_failure"):
            return FailureCategory.CODE_BUG

        # ----- Layer 2: Text-based fallback -----

        lowered = (execution_result or "").lower()
        if not lowered:
            return FailureCategory.UNRECOVERABLE

        if any(s in lowered for s in self._UPSTREAM_TEXT_SIGNALS):
            return FailureCategory.UPSTREAM_INCOMPLETE

        if any(s in lowered for s in self._TIMEOUT_TEXT_SIGNALS):
            return FailureCategory.TIMEOUT

        if any(s in lowered for s in self._CODE_BUG_TEXT_SIGNALS):
            return FailureCategory.CODE_BUG

        if "filenotfounderror" in lowered or "no such file" in lowered:
            return FailureCategory.DATA_MISSING

        return FailureCategory.UNRECOVERABLE
