"""
Manuscript Writer Tool

Generate a research manuscript in staged sections with evaluation and revision.
Pipeline:
1) Build a global analysis memo from provided context.
2) Generate each section (abstract, introduction, method, experiment, result,
   discussion, conclusion, references).
3) Evaluate each section with a strict JSON rubric and revise up to N times.
4) Merge approved sections and perform a final global rewrite.

This module is the compatibility facade for the split-out implementation. The
package layout is:

- ``schema.py``   ``manuscript_writer_tool`` (registry schema dict)
- ``config.py``   eval tables and import-time ``MANUSCRIPT_*`` knobs
- ``paths.py``    project/session path guards and context-file reading
- ``rubrics.py``  section rubric, scoring, citation/release-consistency checks
- ``prompts.py``  prompt builders
- ``evidence.py`` review-evidence chain (study cards, coverage gate)
- ``local_draft.py`` draft_only local assembly
- ``llm_bridge.py`` LLM client construction, ``_chat`` streaming fallback
- ``pipeline.py`` ``manuscript_writer_handler`` orchestration

Every original module-level name is re-exported here, so
``from .manuscript_writer import manuscript_writer_tool`` /
``manuscript_writer_handler`` and every ``manuscript_writer._name`` test access
keep working unchanged. Do not import facade names at sibling module import
time: siblings that need a monkeypatched name read it through the facade at
call time (``from .. import manuscript_writer as facade``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import re
import time
import difflib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# Patched-name surface. Tests monkeypatch these two names on the package
# namespace (22 and 17 sites respectively); the definitions stay here and the
# sibling modules read them back through the facade at call time.
_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent.resolve()
_RUNTIME_DIR = _PROJECT_ROOT / "runtime"

from .config import (
    _ALL_EVAL_DIMENSIONS,
    _ALLOWED_TEXT_EXTENSIONS,
    _CONTEXT_FILE_MAX_BYTES,
    _DEFAULT_COVERAGE_THRESHOLDS,
    _DEFAULT_FINAL_POLISH_MAX_REVISIONS,
    _DEFAULT_FINAL_POLISH_STEP_TIMEOUT_SEC,
    _DEFAULT_FINAL_POLISH_THRESHOLD,
    _DEFAULT_HEARTBEAT_LOG_SEC,
    _DEFAULT_LLM_CALL_TIMEOUT_SEC,
    _DEFAULT_LOCAL_DRAFT_SECTIONS,
    _DEFAULT_MAX_CONTEXT_BYTES,
    _DEFAULT_MAX_REVISIONS,
    _DEFAULT_SECTIONS,
    _DEFAULT_SECTION_TIMEOUT_SEC,
    _DEFAULT_THRESHOLD,
    _DIMENSION_WEIGHTS,
    _EVIDENCE_MD_MAX_BYTES,
    _FINAL_POLISH_EVAL_DIMS,
    _MAX_TOKENS_EVAL,
    _MAX_TOKENS_MEMO,
    _MAX_TOKENS_MERGE,
    _MAX_TOKENS_SECTION,
    _MAX_TOKENS_TRANSITION,
    _NUMERIC_TOKEN_RE,
    _REVIEW_SECTION_COVERAGE_PASS_THRESHOLD,
    _REVIEW_SECTION_COVERAGE_TARGETS,
    _REVIEW_SECTION_EVAL_DIMS,
    _SECTION_EVAL_DIMS,
    _TRUTHY_VALUES,
    _VALID_ARTICLE_MODES,
)
from .evidence import (
    _apply_review_evidence_diagnostics,
    _build_review_context_bundle,
    _build_review_section_coverage_report,
    _coverage_thresholds,
    _discover_latest_review_pack_file,
    _discover_sibling_context_path,
    _evaluate_coverage,
    _first_context_path,
    _load_json_file,
    _load_jsonl_file,
    _load_review_evidence,
    _reevaluate_coverage_report,
    _render_coverage_markdown,
    _render_study_matrix,
    _review_study_card_excerpt,
    _take_unique,
    _validate_review_abstract_contract,
)
from .llm_bridge import (
    LLMClient,
    LLMService,
    _await_with_deadline,
    _build_llm_service,
    _chat,
    _chat_inner,
    _env_timeout_sec,
    _heartbeat_log_sec,
    _is_review_article_task,
    _llm_call_timeout_sec,
    _maybe_wait_with_timeout,
    _normalize_article_mode,
    _resolve_article_mode,
    _resolve_model_name,
    _section_timeout_sec,
    _silence_task,
    get_llm_service,
    update_usage_context,
)
from .local_draft import (
    _assemble_local_draft_from_context,
    _demote_markdown_headings,
    _local_draft_bucket,
    _local_draft_sort_key,
)
from .paths import (
    _build_context_blocks,
    _default_analysis_path,
    _env_enabled,
    _is_disallowed_project_source_write,
    _is_project_level_results_write,
    _is_relative_to,
    _read_text_file,
    _resolve_project_path,
    _resolve_session_dir,
    _resolve_session_scoped_project_path,
    _session_tmp_output_dir,
    _PROJECT_ARTIFACT_SUBDIRS,
)
from .pipeline import (
    _build_section_failure_row,
    _parse_json_payload,
    manuscript_writer_handler,
    manuscript_writer_tool,
)
from .prompts import (
    _build_analysis_prompt,
    _build_evaluation_prompt,
    _build_final_polish_prompt,
    _build_final_polish_revision_prompt,
    _build_merge_prompt,
    _build_release_review_prompt,
    _build_revision_prompt,
    _build_section_prompt,
)
from .rubrics import (
    _apply_release_consistency_report,
    _average_score,
    _build_release_consistency_report,
    _default_section_list,
    _exemplar_style_enabled,
    _exemplar_style_instructions,
    _extract_bibtex_keys,
    _extract_heading_sequence,
    _extract_markdown_citekeys,
    _extract_numeric_tokens,
    _infer_section_profile,
    _is_placeholder_section_content,
    _merge_and_polish_exemplar_hint,
    _normalize_section_key,
    _render_references_section,
    _section_eval_dims,
    _section_requirements,
    _section_title,
    _validate_citations,
    _weighted_score,
)
