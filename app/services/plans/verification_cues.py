"""Verification cue tables and pure predicates (god-class split, behaviour zero-change).

Moved verbatim out of ``task_verification.py`` per
``design/2026-09-24-backend-godfiles-refactor-plan.md`` §4.6 (TV cluster ①):
the protocol word/literal tables (completion statuses, path keys, internal
artifact names, tabular row-count keys, semantic deliverable suffixes/keywords/
stopwords/topic aliases, output-discovery directory and scaffolding names,
source-discovery cue families) plus the one pure predicate that sits at a
cluster head, ``_has_nonempty_string``.

Every constant is re-exported by the ``task_verification`` facade, so the
facade's own globals and all import sites are unchanged.  No test references
any of these constants by name and none is monkeypatched (verified by grep),
so sibling modules import them directly.

The cue-driven *predicates* that read these tables (``_looks_like_source_discovery_task``,
``_failures_are_source_path_checks``, ``_looks_like_tabular_row_count_check``)
deliberately stay inside the clusters that consume them: pulling them here would
fragment the contiguous method runs of those clusters without cohesion gain.
"""

from __future__ import annotations

import re
from typing import Any

_COMPLETED_LIKE = {"completed", "done", "success"}
_FAILED_LIKE = {"failed", "failure", "error"}
_PATH_KEYS = {
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
    "run_directory",
    "working_directory",
    "task_directory_full",
    "task_root_directory",
    "results_directory",
    "work_dir",
    "run_dir",
    "references_bib",
    "evidence_md",
    "library_jsonl",
    "pdf_dir",
    "artifact_paths",
}
_PDB_LINE_RECORDS = {"HET", "HETNAM", "HETATM", "ATOM", "MODRES", "LINK"}
_INTERNAL_ARTIFACT_FILENAMES = {"result.json", "manifest.json", "preview.json"}
_INTERNAL_TOOL_OUTPUT_RE = re.compile(
    r"(?:^|/)tool_outputs/job_[^/]+/step_\d+_[^/]+(?:/.*)?$",
    re.IGNORECASE,
)
_TABULAR_ROW_COUNT_KEYS = {"row_count", "rows", "record_count"}
_SEMANTIC_DELIVERABLE_SUFFIXES = {".md"}
_SEMANTIC_DELIVERABLE_KEYWORDS = {"evidence"}
_SEMANTIC_FILENAME_STOPWORDS = {
    "a",
    "an",
    "and",
    "draft",
    "evidence",
    "file",
    "final",
    "for",
    "key",
    "md",
    "of",
    "output",
    "outputs",
    "report",
    "section",
    "sections",
    "summary",
    "summaries",
    "task",
    "the",
    "v2",
    "v3",
}
_SEMANTIC_SINGLETON_FALLBACK_GENERIC_TOKENS = {
    "memo",
    "memos",
    "misc",
    "miscellaneous",
    "note",
    "notes",
    "placeholder",
    "scratch",
    "temp",
    "tmp",
    "todo",
    "todos",
}
_SEMANTIC_TOPIC_ALIASES = {
    "conclusion": {
        "advance",
        "advances",
        "future",
        "outlook",
        "perspective",
        "perspectives",
        "prospect",
        "prospects",
    },
}

_OUTPUT_DISCOVERY_DIR_NAMES = {
    "artifact",
    "artifacts",
    "data",
    "docs",
    "figures",
    "output",
    "outputs",
    "plots",
    "result",
    "results",
    "tables",
}
_NON_DELIVERABLE_SUFFIXES = {".log", ".tmp", ".pyc"}
_SCAFFOLDING_DIR_NAMES = {"code", "_scratch", "logs", "__pycache__"}


_SOURCE_DISCOVERY_POSITIVE_CUES = {
    "locate",
    "located",
    "find",
    "found",
    "search",
    "inventory",
    "catalog",
    "catalogue",
    "list",
    "verify presence",
    "confirm presence",
}
_SOURCE_DISCOVERY_CONTEXT_CUES = {
    "existing",
    "pre-existing",
    "preexisting",
    "source",
    "input",
    "reuse",
    "reusable",
    "already generated",
    "already exists",
}
_SOURCE_DISCOVERY_LINE_FOUND_CUES = {
    "found",
    "exists",
    "present",
    "located",
    "available",
}
_SOURCE_DISCOVERY_LINE_MISSING_CUES = {
    "not found",
    "missing",
    "does not exist",
    "doesn't exist",
    "absent",
    "unavailable",
}
_SOURCE_DISCOVERY_PATH_CHECKS = {
    "file_exists",
    "file_nonempty",
    "pdf_valid",
}


class _CueMethods:
    """Cue-table-backed pure predicates of ``TaskVerificationService`` (mixin)."""

    @staticmethod
    def _has_nonempty_string(value: Any) -> bool:
        return isinstance(value, str) and bool(value.strip())
