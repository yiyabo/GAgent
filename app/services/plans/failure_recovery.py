"""Failure classification and recovery strategies for plan execution.

When a task fails during automatic plan execution, the FailureAnalyzer
classifies the failure into a category. If the category is recoverable,
the plan executor can attempt automatic recovery (e.g., re-running
upstream dependencies or retrying with error context).
"""

from __future__ import annotations

from enum import Enum
from typing import Sequence


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
    """Classify a task execution failure into a recoverable/unrecoverable category."""

    _UPSTREAM_SIGNALS: Sequence[str] = (
        "blocked_dependency",
        "dependency",
        "upstream",
        "missing filtered data",
        "fewer than",
        "requires the output from task",
        "ensure task",
        "上游产物",
        "依赖缺失",
    )

    _CODE_BUG_SIGNALS: Sequence[str] = (
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

    _TIMEOUT_SIGNALS: Sequence[str] = (
        "timeout",
        "timed out",
        "exceeded",
        "execution exceeded",
    )

    def classify(
        self,
        execution_result: str,
        metadata: dict,
    ) -> FailureCategory:
        """Return the failure category for a failed task.

        Args:
            execution_result: The raw execution_result text (may be JSON or free text).
            metadata: Parsed metadata dict (may contain ``error_category``).
        """
        # Fast path: structured error_category from the code executor.
        if metadata.get("error_category") == "blocked_dependency":
            return FailureCategory.UPSTREAM_INCOMPLETE

        lowered = (execution_result or "").lower()
        if not lowered:
            return FailureCategory.UNRECOVERABLE

        # Check upstream signals first (most specific).
        if any(s in lowered for s in self._UPSTREAM_SIGNALS):
            return FailureCategory.UPSTREAM_INCOMPLETE

        # Timeout before code-bug so that "timeout" in a traceback is
        # classified as timeout, not code_bug.
        if any(s in lowered for s in self._TIMEOUT_SIGNALS):
            return FailureCategory.TIMEOUT

        if any(s in lowered for s in self._CODE_BUG_SIGNALS):
            return FailureCategory.CODE_BUG

        if "filenotfounderror" in lowered or "no such file" in lowered:
            return FailureCategory.DATA_MISSING

        return FailureCategory.UNRECOVERABLE
