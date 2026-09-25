"""Manuscript writer configuration: eval tables and environment knobs.

Every value here is read at import time, exactly as it was when this lived in a
single module: the ``MANUSCRIPT_*`` overrides are consumed once, when the
package is imported, and are not re-read per call.
"""

from __future__ import annotations

import os
import re
from typing import Dict, List

_DEFAULT_MAX_CONTEXT_BYTES = 200_000  # 200 KB per file
# Evidence/context injection caps: re-sending full 200 KB files on every
# section/memo call was the dominant input-token driver.
_EVIDENCE_MD_MAX_BYTES = int(os.getenv("MANUSCRIPT_EVIDENCE_MD_MAX_BYTES", "32768") or "32768")
_CONTEXT_FILE_MAX_BYTES = int(os.getenv("MANUSCRIPT_CONTEXT_FILE_MAX_BYTES", "49152") or "49152")
_DEFAULT_MAX_REVISIONS = 5
_DEFAULT_THRESHOLD = 0.8
_DEFAULT_FINAL_POLISH_MAX_REVISIONS = 2
_DEFAULT_FINAL_POLISH_THRESHOLD = 0.85
_DEFAULT_FINAL_POLISH_STEP_TIMEOUT_SEC = 0.0
# Pipeline liveness guards. The per-byte httpx read timeout inside LLMClient
# cannot stop a stream whose upstream trickles keep-alive bytes forever, and
# the final-polish clients are intentionally built with timeout=0 (httpx
# timeout=None). These overall deadlines bound whole calls/stages instead.
_DEFAULT_LLM_CALL_TIMEOUT_SEC = 600.0
_DEFAULT_SECTION_TIMEOUT_SEC = 1800.0
_DEFAULT_HEARTBEAT_LOG_SEC = 60.0
_VALID_ARTICLE_MODES = {"auto", "review", "research"}
_ALLOWED_TEXT_EXTENSIONS = {
    ".md",
    ".txt",
    ".csv",
    ".tsv",
    ".json",
    ".jsonl",
    ".yaml",
    ".yml",
    ".bib",
}
_DEFAULT_SECTIONS = [
    "abstract",
    "introduction",
    "method",
    "experiment",
    "result",
    "discussion",
    "conclusion",
    "references",
]
_DEFAULT_LOCAL_DRAFT_SECTIONS = [
    "abstract",
    "introduction",
    "method",
    "result",
    "discussion",
    "conclusion",
    "references",
]

# ---------------------------------------------------------------------------
# Per-section evaluation dimensions & weights
# ---------------------------------------------------------------------------
_ALL_EVAL_DIMENSIONS = [
    "structure",
    "scientific_rigor",
    "method_detail",
    "experiment_detail",
    "results_analysis",
    "clarity",
    "cohesion",
    "academic_style",
    "citation_integrity",
    "evidence_linkage",
    "evidence_coverage",
]

_SECTION_EVAL_DIMS: Dict[str, List[str]] = {
    "abstract": ["structure", "clarity", "cohesion", "academic_style"],
    "introduction": ["structure", "scientific_rigor", "clarity", "citation_integrity", "cohesion"],
    "method": ["method_detail", "scientific_rigor", "clarity", "citation_integrity"],
    "experiment": ["experiment_detail", "method_detail", "results_analysis", "scientific_rigor"],
    "result": ["results_analysis", "scientific_rigor", "clarity", "citation_integrity"],
    "discussion": ["scientific_rigor", "results_analysis", "clarity", "cohesion", "citation_integrity"],
    "conclusion": ["structure", "clarity", "cohesion", "academic_style"],
}

_REVIEW_SECTION_EVAL_DIMS: Dict[str, List[str]] = {
    "introduction": ["structure", "scientific_rigor", "clarity", "citation_integrity", "cohesion", "evidence_linkage", "evidence_coverage"],
    "method": ["structure", "scientific_rigor", "clarity", "citation_integrity", "cohesion", "evidence_linkage", "evidence_coverage"],
    "experiment": ["structure", "scientific_rigor", "clarity", "citation_integrity", "cohesion", "evidence_linkage", "evidence_coverage"],
    "result": ["results_analysis", "clarity", "cohesion", "citation_integrity", "structure", "evidence_linkage", "evidence_coverage"],
    "discussion": ["scientific_rigor", "results_analysis", "clarity", "cohesion", "citation_integrity", "evidence_linkage", "evidence_coverage"],
    "conclusion": ["structure", "clarity", "cohesion", "academic_style", "evidence_linkage", "evidence_coverage"],
}

_DIMENSION_WEIGHTS: Dict[str, float] = {
    "scientific_rigor": 1.5,
    "citation_integrity": 1.3,
    "results_analysis": 1.2,
    "method_detail": 1.0,
    "experiment_detail": 1.0,
    "evidence_linkage": 1.2,
    "evidence_coverage": 1.15,
    "structure": 1.0,
    "clarity": 1.0,
    "cohesion": 0.8,
    "academic_style": 0.7,
}
_REVIEW_SECTION_COVERAGE_TARGETS: Dict[str, Dict[str, int]] = {
    "introduction": {"min_supported_citations": 2, "min_full_text_citations": 1},
    "method": {"min_supported_citations": 2, "min_full_text_citations": 1},
    "experiment": {"min_supported_citations": 3, "min_full_text_citations": 2},
    "result": {"min_supported_citations": 3, "min_full_text_citations": 2},
    "discussion": {"min_supported_citations": 3, "min_full_text_citations": 2},
    "conclusion": {"min_supported_citations": 2, "min_full_text_citations": 1},
}
_REVIEW_SECTION_COVERAGE_PASS_THRESHOLD = 0.85
_TRUTHY_VALUES = {"1", "true", "yes", "on", "y"}
_FINAL_POLISH_EVAL_DIMS = [
    "deduplication",
    "readability",
    "section_cohesion",
    "citation_integrity",
    "format_integrity",
    "factual_faithfulness",
]
_NUMERIC_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:\d[\d,]*(?:\.\d+)?%?)(?![A-Za-z0-9_])"
)

# Per-stage output caps (tokens). Output tokens dominate cost on the current
# relay (~86x input price), so long-form generation must not inherit the
# client-side 16k default.
_MAX_TOKENS_SECTION = int(os.getenv("MANUSCRIPT_MAX_TOKENS_SECTION", "4000") or "4000")
_MAX_TOKENS_EVAL = int(os.getenv("MANUSCRIPT_MAX_TOKENS_EVAL", "2000") or "2000")
_MAX_TOKENS_MEMO = int(os.getenv("MANUSCRIPT_MAX_TOKENS_MEMO", "3000") or "3000")
_MAX_TOKENS_TRANSITION = int(os.getenv("MANUSCRIPT_MAX_TOKENS_TRANSITION", "1000") or "1000")
_MAX_TOKENS_MERGE = int(os.getenv("MANUSCRIPT_MAX_TOKENS_MERGE", "8000") or "8000")

_DEFAULT_COVERAGE_THRESHOLDS = {
    "min_total_studies": 15,
    "min_full_text_studies": 6,
    "min_quantitative_studies": 4,
    "min_support_per_core_section": 2,
}
