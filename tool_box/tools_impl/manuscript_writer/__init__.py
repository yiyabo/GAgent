"""
Manuscript Writer Tool

Generate a research manuscript in staged sections with evaluation and revision.
Pipeline:
1) Build a global analysis memo from provided context.
2) Generate each section (abstract, introduction, method, experiment, result,
   discussion, conclusion, references).
3) Evaluate each section with a strict JSON rubric and revise up to N times.
4) Merge approved sections and perform a final global rewrite.
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


def _build_section_failure_row(
    *,
    section: str,
    idx: int,
    exc: Exception,
) -> Dict[str, Any]:
    """Failure row for a section whose LLM pipeline died, keeping the real
    section name (instead of the historical 'unknown') so the failure payload
    and quality gate can attribute it."""
    is_timeout = isinstance(exc, asyncio.TimeoutError)
    return {
        "section": section,
        "idx": idx,
        "text": "",
        "path": None,
        "attempts": 0,
        "passed": False,
        "score": 0.0,
        "evaluation_path": None,
        "defects": ["section_llm_timeout" if is_timeout else "section_llm_error"],
        "error": str(exc)[:500],
        "review_evidence_coverage": None,
    }


def _parse_json_payload(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    raw = text.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None


async def manuscript_writer_handler(
    task: str,
    output_path: str,
    context_paths: Optional[List[str]] = None,
    analysis_path: Optional[str] = None,
    sections: Optional[List[str]] = None,
    article_mode: Optional[str] = None,
    max_revisions: int = _DEFAULT_MAX_REVISIONS,
    evaluation_threshold: float = _DEFAULT_THRESHOLD,
    max_context_bytes: int = _DEFAULT_MAX_CONTEXT_BYTES,
    generation_model: Optional[str] = None,
    evaluation_model: Optional[str] = None,
    merge_model: Optional[str] = None,
    generation_provider: Optional[str] = None,
    evaluation_provider: Optional[str] = None,
    merge_provider: Optional[str] = None,
    session_id: Optional[str] = None,
    task_id: Optional[int] = None,
    ancestor_chain: Optional[List[int]] = None,
    keep_workspace: bool = False,
    draft_only: bool = False,
) -> Dict[str, Any]:
    """
    Generate a manuscript draft using staged generation, evaluation, and merge.
    """
    if not task or not task.strip():
        return {"tool": "manuscript_writer", "success": False, "error": "missing_task"}
    if not output_path or not str(output_path).strip():
        return {
            "tool": "manuscript_writer",
            "success": False,
            "error": "missing_output_path",
        }

    try:
        # Advisory by default (2026-08-29): near-miss citekeys are fuzzy-repaired
        # and residual mismatches become warnings instead of failing the section.
        # Set MANUSCRIPT_STRICT_GATE=true to restore hard-fail behavior.
        strict_gate = _env_enabled("MANUSCRIPT_STRICT_GATE", False)
        session_dir = _resolve_session_dir(session_id)
        output_file = _resolve_session_scoped_project_path(output_path, session_dir)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        if analysis_path and str(analysis_path).strip():
            analysis_file = _resolve_session_scoped_project_path(
                str(analysis_path).strip(),
                session_dir,
            )
        else:
            analysis_file = _default_analysis_path(output_file)
        analysis_file.parent.mkdir(parents=True, exist_ok=True)

        run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        # --- Unified output path: use PathRouter when task_id is available ---
        unified_output_dir: Optional[Path] = None
        if task_id is not None and session_id:
            from app.services.path_router import get_path_router
            path_router = get_path_router()
            unified_output_dir = path_router.get_task_output_dir(
                session_id, task_id, ancestor_chain, create=True
            )
            # Use unified output dir as work_dir base (sections/, reviews/, merge/ inside)
            work_dir = unified_output_dir
        else:
            # Legacy: use ToolOutputResolver for work_dir
            from app.services.tool_output_resolver import get_tool_output_resolver
            resolver = get_tool_output_resolver()
            work_base = session_dir or resolver.resolve(session_id=None, tool_name="manuscript_writer", create=True)
            work_base.mkdir(parents=True, exist_ok=True)
            work_dir = work_base / f".manuscript_writer_{run_id}"
        work_dir.mkdir(parents=True, exist_ok=True)
        sections_dir = work_dir / "sections"
        reviews_dir = work_dir / "reviews"
        merge_dir = work_dir / "merge"
        sections_dir.mkdir(parents=True, exist_ok=True)
        reviews_dir.mkdir(parents=True, exist_ok=True)
        merge_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = merge_dir / "merge_queue.json"
        manifest_path.write_text("[]", encoding="utf-8")

        # Normalize numeric params
        try:
            max_revisions = int(max_revisions)
        except (TypeError, ValueError):
            max_revisions = _DEFAULT_MAX_REVISIONS
        max_revisions = max(1, max_revisions)

        try:
            evaluation_threshold = float(evaluation_threshold)
        except (TypeError, ValueError):
            evaluation_threshold = _DEFAULT_THRESHOLD
        if evaluation_threshold <= 0 or evaluation_threshold > 1:
            evaluation_threshold = _DEFAULT_THRESHOLD

        try:
            max_context_bytes = int(max_context_bytes)
        except (TypeError, ValueError):
            max_context_bytes = _DEFAULT_MAX_CONTEXT_BYTES
        max_context_bytes = max(10_000, max_context_bytes)

        final_polish_enabled = _env_enabled("MANUSCRIPT_FINAL_POLISH_ENABLED", True)
        try:
            final_polish_max_revisions = int(
                os.getenv("MANUSCRIPT_FINAL_POLISH_MAX_REVISIONS", str(_DEFAULT_FINAL_POLISH_MAX_REVISIONS))
            )
        except (TypeError, ValueError):
            final_polish_max_revisions = _DEFAULT_FINAL_POLISH_MAX_REVISIONS
        final_polish_max_revisions = max(1, final_polish_max_revisions)

        try:
            final_polish_threshold = float(
                os.getenv("MANUSCRIPT_FINAL_POLISH_THRESHOLD", str(_DEFAULT_FINAL_POLISH_THRESHOLD))
            )
        except (TypeError, ValueError):
            final_polish_threshold = _DEFAULT_FINAL_POLISH_THRESHOLD
        if final_polish_threshold <= 0 or final_polish_threshold > 1:
            final_polish_threshold = _DEFAULT_FINAL_POLISH_THRESHOLD

        try:
            final_polish_step_timeout_sec = float(
                os.getenv(
                    "MANUSCRIPT_FINAL_POLISH_STEP_TIMEOUT_SEC",
                    str(_DEFAULT_FINAL_POLISH_STEP_TIMEOUT_SEC),
                )
            )
        except (TypeError, ValueError):
            final_polish_step_timeout_sec = _DEFAULT_FINAL_POLISH_STEP_TIMEOUT_SEC
        if final_polish_step_timeout_sec <= 0:
            final_polish_step_timeout_sec = None

        try:
            final_polish_llm_timeout_sec = float(
                os.getenv("MANUSCRIPT_FINAL_POLISH_LLM_TIMEOUT_SEC", "0")
            )
        except (TypeError, ValueError):
            final_polish_llm_timeout_sec = 0.0
        if final_polish_llm_timeout_sec <= 0:
            final_polish_llm_timeout_sec = 0.0

        context_paths = context_paths or []
        draft_only = bool(draft_only)
        article_mode_requested = _normalize_article_mode(article_mode)
        article_mode_resolved, review_mode = _resolve_article_mode(article_mode_requested, task)
        section_list = sections or _default_section_list(draft_only=draft_only, review_mode=review_mode)
        section_list = [_normalize_section_key(s) for s in section_list if s and str(s).strip()]
        if not section_list:
            section_list = _default_section_list(draft_only=draft_only, review_mode=review_mode)
        if review_mode and not any(
            "references" == s or "ref" in s.lower() or "参考" in s or "文献" in s
            for s in section_list
        ):
            # Agent-supplied section lists (often localized) may omit the
            # references section entirely; citation validation would then fail
            # against a section that can never exist. Always attach one.
            section_list.append("references")
        review_evidence = (
            _load_review_evidence(
                context_paths=context_paths,
                merge_dir=merge_dir,
                max_context_bytes=max_context_bytes,
                session_dir=session_dir,
            )
            if review_mode
            else {}
        )
        section_contexts = (
            review_evidence.get("section_contexts")
            if isinstance(review_evidence.get("section_contexts"), dict)
            else {}
        )
        context_text = (
            str(section_contexts.get("__global__") or "").strip()
            if review_mode
            else _build_context_blocks(context_paths, max_context_bytes)
        )
        reference_library_path = (
            str(review_evidence.get("reference_library_path") or "").strip()
            if isinstance(review_evidence, dict)
            else ""
        )
        bib_text = ""
        if reference_library_path:
            try:
                bib_text = _read_text_file(_resolve_project_path(reference_library_path), max_context_bytes)
            except Exception:
                bib_text = ""
        elif context_text:
            bib_text = context_text
        bib_keys = _extract_bibtex_keys(bib_text)

        # P3-8: Citation coverage precheck — warn early if no .bib keys found
        bib_precheck_warning: Optional[str] = None
        if not bib_keys and any(s != "references" for s in section_list):
            has_bib_file = any(
                str(p).strip().lower().endswith(".bib")
                for p in context_paths
                if p and str(p).strip()
            )
            if not has_bib_file:
                bib_precheck_warning = (
                    "No .bib file found in context_paths. "
                    "Citation integrity checks will likely fail. "
                    "Consider providing a references.bib file."
                )
                logger.warning("manuscript_writer: %s", bib_precheck_warning)

        gen_model = _resolve_model_name(generation_model)
        eval_model = _resolve_model_name(evaluation_model)
        merge_model_name = _resolve_model_name(merge_model)

        # P2-5: Use different evaluation model by default
        if eval_model is None:
            env_eval_model = os.getenv("MANUSCRIPT_EVAL_MODEL")
            if env_eval_model:
                eval_model = env_eval_model.strip() or None

        analysis_memo = ""

        section_results: List[Dict[str, Any]] = []
        section_scores: Dict[str, float] = {}
        failed_sections: List[str] = []
        passed_sections: List[Tuple[str, Path]] = []
        drafted_texts: List[str] = []
        section_text_map: Dict[str, str] = {}
        release_review: Optional[Dict[str, Any]] = None
        release_consistency_report: Optional[Dict[str, Any]] = None
        release_consistency_path: Optional[Path] = None

        def _to_rel(path: Optional[Path]) -> Optional[str]:
            if path is None:
                return None
            return str(path.relative_to(_PROJECT_ROOT))

        def _to_session_rel(path: Optional[Path]) -> Optional[str]:
            if path is None or session_dir is None:
                return None
            try:
                return str(path.resolve().relative_to(session_dir.resolve()))
            except Exception:
                return None

        def _hidden_prefixes(*paths: Optional[Path], extra: Optional[List[str]] = None) -> List[str]:
            prefixes: List[str] = []
            for candidate in paths:
                rel = _to_session_rel(candidate)
                if rel and rel not in prefixes:
                    prefixes.append(rel)
            for candidate in extra or []:
                value = str(candidate or "").strip().lstrip("/").replace("\\", "/")
                if value and value not in prefixes:
                    prefixes.append(value)
            return prefixes

        def _promote_explicit_outputs_to_task_dir(
            *entries: Tuple[str, Optional[Path]],
        ) -> Dict[str, str]:
            promoted: Dict[str, str] = {}
            if unified_output_dir is None or session_dir is None:
                return promoted

            target_root = unified_output_dir.resolve()
            for field_name, candidate in entries:
                if candidate is None or not candidate.exists() or not candidate.is_file():
                    continue

                source = candidate.resolve()
                try:
                    source.relative_to(target_root)
                    target = source
                except ValueError:
                    target = (target_root / source.name).resolve()
                    target.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        shutil.copy2(source, target)
                    except Exception as exc:
                        logger.warning(
                            "manuscript_writer failed to promote %s into task output dir: %s",
                            source,
                            exc,
                        )
                        continue

                rel = _to_session_rel(target)
                if rel:
                    promoted[field_name] = rel
            return promoted

        def _attach_output_location(
            result: Dict[str, Any],
            *,
            base_dir: Optional[Path] = None,
        ) -> Dict[str, Any]:
            if unified_output_dir is None:
                return result

            root_dir = (base_dir or unified_output_dir).resolve()
            artifact_paths = [str(path.resolve()) for path in sorted(root_dir.rglob("*")) if path.is_file()]
            session_artifact_paths: List[str] = []
            for path in sorted(root_dir.rglob("*")):
                if not path.is_file():
                    continue
                rel = _to_session_rel(path)
                session_artifact_paths.append((rel or str(path.resolve())).replace("\\", "/"))

            out = dict(result)
            out["output_location"] = {
                "type": "task",
                "session_id": session_id,
                "task_id": task_id,
                "ancestor_chain": ancestor_chain,
                "base_dir": str(root_dir),
                "files": session_artifact_paths,
            }
            out["artifact_paths"] = list(dict.fromkeys([
                *[str(item) for item in list(out.get("artifact_paths") or []) if str(item).strip()],
                *artifact_paths,
            ]))
            out["produced_files"] = list(out["artifact_paths"])
            out["session_artifact_paths"] = list(dict.fromkeys([
                *[str(item) for item in list(out.get("session_artifact_paths") or []) if str(item).strip()],
                *session_artifact_paths,
            ]))
            return out

        def _release_summary_for_error(
            error_code: str,
            *,
            evaluation: Optional[Dict[str, Any]] = None,
        ) -> str:
            normalized = str(error_code or "").strip().lower()
            if normalized == "section_evaluation_failed":
                section_list_text = ", ".join(sorted({str(item) for item in failed_sections if item}))
                if section_list_text:
                    return f"Publication blocked: section quality gate failed for {section_list_text}."
                return "Publication blocked: one or more manuscript sections did not pass the quality gate."
            if normalized == "citation_validation_failed":
                return (
                    "Publication blocked: citation validation failed because references were incomplete, "
                    "unsupported, or inconsistent."
                )
            if normalized == "low_evidence_coverage":
                if isinstance(evaluation, dict):
                    summary = str(evaluation.get("coverage_summary") or "").strip()
                    if summary:
                        return f"Publication blocked: {summary}"
                return "Publication blocked: evidence coverage was too weak for a PI-readable review manuscript."
            if normalized == "abstract_incomplete":
                return "Publication blocked: the abstract did not satisfy the required review-manuscript contract."
            if normalized == "unsupported_claims":
                return (
                    "Publication blocked: one or more review sections lacked sufficient evidence linkage "
                    "or section-level evidence coverage."
                )
            if normalized == "polish_quality_gate_failed":
                summary = ""
                if isinstance(evaluation, dict):
                    summary = str(evaluation.get("release_summary") or "").strip()
                if summary:
                    return f"Publication blocked: {summary}"
                return (
                    "Publication blocked: the final polish gate found remaining duplication, readability, "
                    "or formatting issues."
                )
            return "Publication blocked: the manuscript did not meet the final release gate."

        def _build_polish_failure_review(
            *,
            stage: str,
            attempt: int,
            exc: Exception,
        ) -> Dict[str, Any]:
            error_text = str(exc).strip() or exc.__class__.__name__
            is_timeout = isinstance(exc, asyncio.TimeoutError) or "timed out" in error_text.lower()
            defect = f"{stage}_timeout" if is_timeout else f"{stage}_execution_failed"
            summary = (
                "The final polish gate timed out before the manuscript could be verified for publication."
                if is_timeout
                else "The final polish gate failed before the manuscript could be verified for publication."
            )
            return {
                "scores": {},
                "defects": [defect],
                "revision_instructions": [
                    "Retry the final polish and release-review stage with a smaller prompt or a more reliable model.",
                    "Do not publish the manuscript until the release gate can complete successfully.",
                ],
                "release_summary": summary,
                "pass": False,
                "stage": stage,
                "attempt": attempt,
                "error": error_text,
            }

        def _build_stats(*, citation_report: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
            total = len(section_list) or 1
            passed_count = len([row for row in section_results if row.get("passed")])
            rewrite_attempts_total = 0
            failure_distribution: Dict[str, int] = {}
            for row in section_results:
                attempts = int(row.get("attempts") or 0)
                rewrite_attempts_total += max(0, attempts - 1)
                if not row.get("passed"):
                    section_name = str(row.get("section") or "unknown")
                    failure_distribution[section_name] = failure_distribution.get(section_name, 0) + 1
            citation_payload = citation_report or {}
            cited = citation_payload.get("body_citekeys") if isinstance(citation_payload, dict) else []
            missing_ref = (
                citation_payload.get("missing_reference_citekeys")
                if isinstance(citation_payload, dict)
                else []
            )
            cited_count = len(cited) if isinstance(cited, list) else 0
            missing_ref_count = len(missing_ref) if isinstance(missing_ref, list) else 0
            stats = {
                "section_pass_rate": round(passed_count / total, 4),
                "rewrite_attempts_total": rewrite_attempts_total,
                "failure_distribution": failure_distribution,
                "final_chars": 0,
                "citation_coverage_rate": (
                    round((cited_count - missing_ref_count) / cited_count, 4)
                    if cited_count > 0
                    else 1.0
                ),
                "cited_keys_count": cited_count,
                "missing_reference_keys_count": missing_ref_count,
            }
            if review_mode and isinstance(review_evidence, dict):
                coverage_report = review_evidence.get("coverage_report") or {}
                if isinstance(coverage_report, dict):
                    counts = coverage_report.get("counts") or {}
                    stats["evidence_coverage_passed"] = bool(coverage_report.get("pass"))
                    stats["evidence_total_studies"] = counts.get("total_studies", 0)
                    stats["evidence_full_text_studies"] = counts.get("full_text_studies", 0)
                    stats["evidence_quantitative_studies"] = counts.get("quantitative_studies", 0)
            return stats

        def _build_failure_payload(
            *,
            error_code: str,
            manifest_path: Path,
            combined_partial: str,
            citation_validation_path: Optional[Path] = None,
            citation_report: Optional[Dict[str, Any]] = None,
            release_summary: Optional[str] = None,
            polish_review_payload: Optional[Dict[str, Any]] = None,
        ) -> Dict[str, Any]:
            partial_path = merge_dir / "combined_partial.md"
            partial_path.write_text(combined_partial, encoding="utf-8")
            partial_output = output_file.with_suffix(output_file.suffix + ".partial.md")
            try:
                partial_output.write_text(combined_partial, encoding="utf-8")
            except Exception:
                pass
            stats = _build_stats(citation_report=citation_report)
            hidden_prefixes = _hidden_prefixes(
                work_dir,
                output_file,
                analysis_file,
                partial_output,
                partial_path,
            )
            promoted_paths = _promote_explicit_outputs_to_task_dir(
                ("effective_output_path", output_file),
                ("effective_analysis_path", analysis_file),
            )
            return _attach_output_location({
                "tool": "manuscript_writer",
                "success": False,
                "error": error_code,
                "error_code": error_code,
                "article_mode_requested": article_mode_requested,
                "article_mode_resolved": article_mode_resolved,
                "quality_gate_passed": False,
                "polish_gate_passed": False,
                "public_release_ready": False,
                "release_state": "blocked",
                "evidence_coverage_passed": bool(
                    (review_evidence.get("coverage_report") or {}).get("pass")
                )
                if review_mode and isinstance(review_evidence, dict)
                else None,
                "coverage_summary": str(
                    (review_evidence.get("coverage_report") or {}).get("summary") or ""
                ).strip()
                if review_mode and isinstance(review_evidence, dict)
                else None,
                "coverage_report_path": _to_rel(review_evidence.get("coverage_report_path"))
                if review_mode and isinstance(review_evidence.get("coverage_report_path"), Path)
                else None,
                "evidence_coverage_path": _to_rel(review_evidence.get("evidence_coverage_path"))
                if review_mode and isinstance(review_evidence.get("evidence_coverage_path"), Path)
                else None,
                "evidence_coverage_notice": evidence_coverage_notice,
                "study_matrix_path": _to_rel(review_evidence.get("study_matrix_path"))
                if review_mode and isinstance(review_evidence.get("study_matrix_path"), Path)
                else None,
                "reference_library_path": reference_library_path or None,
                "release_summary": release_summary or _release_summary_for_error(
                    error_code,
                    evaluation=polish_review_payload,
                ),
                "failed_sections": list(failed_sections),
                "section_scores": dict(section_scores),
                "output_path": _to_rel(output_file),
                "analysis_path": _to_rel(analysis_file),
                "effective_output_path": promoted_paths.get("effective_output_path") or _to_rel(output_file),
                "effective_analysis_path": promoted_paths.get("effective_analysis_path") or _to_rel(analysis_file),
                "pre_polish_output_path": None,
                "polished_output_path": None,
                "sections_dir": _to_rel(sections_dir),
                "reviews_dir": _to_rel(reviews_dir),
                "merge_queue": _to_rel(manifest_path),
                "combined_partial": _to_rel(partial_path),
                "partial_output_path": _to_rel(partial_output),
                "citation_validation_path": _to_rel(citation_validation_path),
                "citation_validation": citation_report,
                "release_consistency_path": _to_rel(release_consistency_path),
                "release_consistency_report": release_consistency_report,
                "temp_workspace": _to_rel(work_dir),
                "hidden_artifact_prefixes": hidden_prefixes,
                "release_review": polish_review_payload,
                "sections": section_results,
                "run_stats": stats,
                "bib_precheck_warning": bib_precheck_warning,
            }, base_dir=work_dir)

        # ---------------------------------------------------------------
        # Helper: generate + evaluate + revise a single section
        # ---------------------------------------------------------------
        async def _gen_eval_section(
            section: str,
            idx: int,
        ) -> Dict[str, Any]:
            """Bounded wrapper: a section finishes, fails as a named row, or is
            cancelled — it can no longer stall the whole run silently."""
            logger.info("manuscript_writer: section[%d] '%s' pipeline started", idx, section)
            try:
                result = await _await_with_deadline(
                    _gen_eval_section_core(section, idx),
                    timeout_sec=_section_timeout_sec(),
                    heartbeat_sec=_heartbeat_log_sec(),
                    label=f"section[{idx}] '{section}'",
                )
                logger.info(
                    "manuscript_writer: section[%d] '%s' finished (passed=%s, attempts=%s, score=%s)",
                    idx,
                    section,
                    result.get("passed"),
                    result.get("attempts"),
                    result.get("score"),
                )
                return result
            except Exception as exc:
                logger.warning(
                    "manuscript_writer: section[%d] '%s' failed: %s",
                    idx,
                    section,
                    exc,
                )
                return _build_section_failure_row(section=section, idx=idx, exc=exc)

        async def _gen_eval_section_core(
            section: str,
            idx: int,
        ) -> Dict[str, Any]:
            """Generate, evaluate, and revise one section. Returns a result dict."""
            section_filename = f"{idx:02d}_{section}.md"
            section_path = sections_dir / section_filename
            requirements = _section_requirements(section, review_mode=review_mode)
            section_context_text = (
                str(section_contexts.get(section) or "").strip()
                if review_mode and isinstance(section_contexts, dict)
                else context_text
            )

            text = await _chat(
                gen_llm,
                _build_section_prompt(
                    task,
                    section,
                    analysis_memo,
                    section_context_text,
                    requirements,
                    review_mode=review_mode,
                ),
                gen_model,
                max_tokens=_MAX_TOKENS_SECTION,
            )

            evaluation_data: Optional[Dict[str, Any]] = None
            passed = False
            attempts = 0
            avg_score = 0.0

            for attempt in range(1, max_revisions + 1):
                attempts = attempt
                eval_prompt = _build_evaluation_prompt(
                    section,
                    analysis_memo,
                    text,
                    requirements,
                    review_mode=review_mode,
                )
                eval_raw = await _chat(eval_llm, eval_prompt, eval_model, max_tokens=_MAX_TOKENS_EVAL, purpose="manuscript_writer:eval")
                evaluation_data = _parse_json_payload(eval_raw)

                if evaluation_data is None:
                    evaluation_data = {
                        "scores": {},
                        "defects": ["evaluation_json_parse_failed"],
                        "revision_instructions": [
                            "Reformat the evaluation output into valid JSON only.",
                            "Revise the section to satisfy all requirements.",
                        ],
                        "pass": False,
                    }
                if review_mode and isinstance(review_evidence, dict):
                    evaluation_data = _apply_review_evidence_diagnostics(
                        section=section,
                        text=text,
                        evaluation_data=evaluation_data,
                        study_cards=review_evidence.get("study_cards") or [],
                        coverage_report=review_evidence.get("coverage_report") or {},
                    )

                scores = evaluation_data.get("scores") or {}
                section_dims = _section_eval_dims(section, review_mode=review_mode)
                avg_score = _weighted_score(scores, section_dims) if isinstance(scores, dict) else 0.0
                pass_flag = evaluation_data.get("pass")
                if pass_flag is None:
                    pass_flag = avg_score >= evaluation_threshold
                passed = bool(pass_flag) and avg_score >= evaluation_threshold

                review_path = reviews_dir / f"{section}_eval_{attempt}.json"
                review_path.write_text(
                    json.dumps(evaluation_data, ensure_ascii=True, indent=2),
                    encoding="utf-8",
                )

                if passed or attempt >= max_revisions:
                    break

                revision_prompt = _build_revision_prompt(
                    section,
                    analysis_memo,
                    section_context_text,
                    text,
                    evaluation_data,
                    requirements,
                    review_mode=review_mode,
                )
                text = await _chat(gen_llm, revision_prompt, gen_model, max_tokens=_MAX_TOKENS_SECTION, purpose="manuscript_writer:section")

            section_path.write_text(text, encoding="utf-8")
            return {
                "section": section,
                "idx": idx,
                "text": text,
                "path": _to_rel(section_path),
                "attempts": attempts,
                "passed": passed,
                "score": round(avg_score, 4),
                "evaluation_path": _to_rel(reviews_dir / f"{section}_eval_{attempts}.json"),
                "defects": list(evaluation_data.get("defects") or []) if isinstance(evaluation_data, dict) else [],
                "review_evidence_coverage": (
                    evaluation_data.get("review_evidence_coverage")
                    if isinstance(evaluation_data, dict) and isinstance(evaluation_data.get("review_evidence_coverage"), dict)
                    else None
                ),
            }

        evidence_coverage_notice: Optional[str] = None
        if review_mode:
            coverage_report = review_evidence.get("coverage_report") if isinstance(review_evidence, dict) else {}
            coverage_payload = coverage_report if isinstance(coverage_report, dict) else {}
            if not coverage_payload.get("pass"):
                # Evidence counts are advisory, never blocking: proceed with the
                # draft and surface the shortfall alongside the result instead of
                # refusing to write (release gates below still guard quality).
                evidence_coverage_notice = str(coverage_payload.get("summary") or "").strip() or (
                    "Evidence coverage is below the recommended level for a review manuscript; "
                    "the draft is based on fewer studies than recommended."
                )
                logger.warning(
                    "manuscript_writer: evidence coverage below recommendation, proceeding without blocking: %s",
                    evidence_coverage_notice,
                )

        if draft_only:
            if output_file.suffix.lower() == ".pdf":
                return {
                    "tool": "manuscript_writer",
                    "success": False,
                    "error": "draft_only_pdf_output_not_supported",
                    "message": (
                        "draft_only mode writes Markdown/text drafts and must not create a file "
                        "with a .pdf suffix. Use a real PDF conversion step for PDF deliverables."
                    ),
                    "output_path": _to_rel(output_file),
                    "effective_output_path": _to_rel(output_file),
                    "analysis_path": _to_rel(analysis_file),
                }
            draft_text, analysis_memo, used_sources, section_counts, section_text_map = _assemble_local_draft_from_context(
                task=task,
                context_paths=context_paths,
                max_context_bytes=max_context_bytes,
                section_list=section_list,
            )
            if not used_sources:
                # Refuse to publish an empty placeholder draft: every section
                # would read "Not available in provided context.", which looks
                # like a real deliverable in the Artifacts UI but carries no
                # content.  Fail loudly instead of writing anything to disk.
                return {
                    "tool": "manuscript_writer",
                    "success": False,
                    "error": "no_usable_context_sources",
                    "error_code": "no_usable_context_sources",
                    "message": (
                        "Local draft assembly found no usable task output files in the "
                        "provided context; refusing to publish an empty placeholder draft. "
                        "Provide context_paths pointing at completed task outputs, or run "
                        "the full manuscript generation instead."
                    ),
                    "draft_only": True,
                    "release_state": "blocked",
                    "public_release_ready": False,
                    "output_path": _to_rel(output_file),
                    "effective_output_path": _to_rel(output_file),
                    "analysis_path": _to_rel(analysis_file),
                }
            analysis_file.write_text(analysis_memo, encoding="utf-8")
            output_file.write_text(draft_text, encoding="utf-8")
            from tool_box.watermark import apply_watermark_inplace
            apply_watermark_inplace(output_file)
            section_profile = _infer_section_profile(section_list)
            applicable_sections = [section for section in section_list if section != "references"]
            structured_section_dir = output_file.parent / f".{output_file.stem}_sections"
            shutil.rmtree(structured_section_dir, ignore_errors=True)
            structured_section_dir.mkdir(parents=True, exist_ok=True)
            structured_sections: List[Dict[str, Any]] = []
            completed_sections: List[str] = []
            for idx, section in enumerate(section_list, start=1):
                section_text = str(section_text_map.get(section) or "").strip()
                if _is_placeholder_section_content(section, section_text):
                    continue
                section_path = structured_section_dir / f"{idx:02d}_{section}.md"
                section_path.write_text(section_text + "\n", encoding="utf-8")
                structured_sections.append(
                    {
                        "section": section,
                        "path": _to_rel(section_path),
                        "status": "completed",
                        "substantive": True,
                    }
                )
                if section != "references":
                    completed_sections.append(section)
            missing_sections = [section for section in applicable_sections if section not in completed_sections]
            hidden_artifact_prefixes = _hidden_prefixes(
                work_dir,
                output_file,
                analysis_file,
                extra=[_to_session_rel(structured_section_dir) or ""],
            )
            cleanup_errors: List[str] = []
            if not keep_workspace:
                try:
                    shutil.rmtree(work_dir)
                except Exception as exc:
                    cleanup_errors.append(str(exc))
            promoted_paths = _promote_explicit_outputs_to_task_dir(
                ("effective_output_path", output_file),
                ("effective_analysis_path", analysis_file),
            )
            return _attach_output_location({
                "tool": "manuscript_writer",
                "success": True,
                "draft_only": True,
                "article_mode_requested": article_mode_requested,
                "article_mode_resolved": article_mode_resolved,
                "release_state": "draft",
                "public_release_ready": False,
                "release_summary": (
                    "Local manuscript draft assembled from completed task outputs without publication-quality gating."
                ),
                "reference_library_path": reference_library_path or None,
                "source_paths": used_sources,
                "analysis_path": _to_rel(analysis_file),
                "effective_analysis_path": promoted_paths.get("effective_analysis_path") or _to_rel(analysis_file),
                "output_path": _to_rel(output_file),
                "effective_output_path": promoted_paths.get("effective_output_path") or _to_rel(output_file),
                "pre_polish_output_path": None,
                "polished_output_path": None,
                "section_profile": section_profile,
                "applicable_sections": applicable_sections,
                "completed_sections": completed_sections,
                "missing_sections": missing_sections,
                "sections": structured_sections,
                "draft_chars": len(draft_text or ""),
                "intermediate_purged": not keep_workspace,
                "temp_workspace": _to_rel(work_dir),
                "hidden_artifact_prefixes": hidden_artifact_prefixes,
                "cleanup_errors": cleanup_errors,
                "run_stats": {
                    "draft_only": True,
                    "final_chars": len(draft_text or ""),
                    "final_polish_enabled": False,
                    "source_file_count": len(used_sources),
                    "method_sources": section_counts["method"],
                    "result_sources": section_counts["result"],
                    "supplementary_sources": section_counts["supplementary"],
                    "section_profile": section_profile,
                    "applicable_section_count": len(applicable_sections),
                    "completed_section_count": len(completed_sections),
                },
                "bib_precheck_warning": bib_precheck_warning,
            }, base_dir=work_dir)

        gen_llm, gen_model = _build_llm_service(generation_provider, gen_model)
        eval_llm, eval_model = _build_llm_service(evaluation_provider, eval_model)
        merge_llm, merge_model_name = _build_llm_service(merge_provider, merge_model_name)
        final_polish_eval_llm, _ = _build_llm_service(
            evaluation_provider,
            eval_model,
            timeout=final_polish_llm_timeout_sec,
        )
        final_polish_merge_llm, _ = _build_llm_service(
            merge_provider,
            merge_model_name,
            timeout=final_polish_llm_timeout_sec,
        )

        analysis_prompt = _build_analysis_prompt(task, context_text, section_list)
        logger.info(
            "manuscript_writer: drafting analysis memo (sections=%d, review_mode=%s)",
            len(section_list),
            review_mode,
        )
        memo_started_at = time.monotonic()
        analysis_memo = await _chat(gen_llm, analysis_prompt, gen_model, max_tokens=_MAX_TOKENS_MEMO, purpose="manuscript_writer:memo")
        analysis_file.write_text(analysis_memo, encoding="utf-8")
        logger.info(
            "manuscript_writer: analysis memo ready (%d chars, %.1fs)",
            len(analysis_memo or ""),
            time.monotonic() - memo_started_at,
        )

        # ---------------------------------------------------------------
        # Phase: Generate sections (parallel for non-reference sections)
        # ---------------------------------------------------------------
        non_ref_sections = [(idx, s) for idx, s in enumerate(section_list, 1) if s != "references"]
        ref_sections = [(idx, s) for idx, s in enumerate(section_list, 1) if s == "references"]

        # Generate non-reference sections in parallel
        if non_ref_sections:
            parallel_results = await asyncio.gather(
                *[_gen_eval_section(s, idx) for idx, s in non_ref_sections],
                return_exceptions=True,
            )
            for (idx, section_name), res in zip(non_ref_sections, parallel_results):
                if isinstance(res, BaseException):
                    logger.error("Section generation failed for '%s': %s", section_name, res)
                    failed_sections.append(section_name)
                    section_results.append(
                        _build_section_failure_row(section=section_name, idx=idx, exc=res)
                    )
                    continue
                section_results.append(res)
                section_scores[res["section"]] = res["score"]
                section_text_map[res["section"]] = res["text"]
                if res["passed"]:
                    passed_sections.append((res["section"], sections_dir / f"{res['idx']:02d}_{res['section']}.md"))
                    drafted_texts.append(res["text"])
                else:
                    failed_sections.append(res["section"])

        # Generate references deterministically (depends on all other sections' citekeys)
        for idx, section in ref_sections:
            section_filename = f"{idx:02d}_{section}.md"
            section_path = sections_dir / section_filename

            if bib_keys:
                cited = _extract_markdown_citekeys("\n\n".join(drafted_texts))
                allowed = set(bib_keys)
                used = [k for k in cited if k in allowed]
                missing = [k for k in cited if k not in allowed]

                # LLMs routinely mangle long generated citekeys by a character or
                # two; a strict string match would then fail the whole references
                # section (and with it the manuscript). Fuzzy-repair near-misses
                # back to the closest library key and rewrite the citations in
                # the drafted text so body and reference list stay consistent.
                repaired: Dict[str, str] = {}
                still_missing: List[str] = []
                for k in missing:
                    match = difflib.get_close_matches(k, allowed, n=1, cutoff=0.82)
                    if match:
                        repaired[k] = match[0]
                        if match[0] not in used:
                            used.append(match[0])
                    else:
                        still_missing.append(k)
                if repaired:
                    logger.warning(
                        "manuscript_writer: fuzzy-repaired %d near-miss citekeys: %s",
                        len(repaired),
                        repaired,
                    )
                    # Longest sources first so a source that is a prefix of
                    # another cannot corrupt it mid-replacement; the lookahead
                    # keeps us from clipping the tail off a longer valid key.
                    for src, dst in sorted(
                        repaired.items(), key=lambda kv: len(kv[0]), reverse=True
                    ):
                        pattern = re.compile("@" + re.escape(src) + r"(?![A-Za-z0-9_])")
                        for sec_key in list(section_text_map.keys()):
                            if section_text_map.get(sec_key):
                                section_text_map[sec_key] = pattern.sub(f"@{dst}", section_text_map[sec_key])
                        drafted_texts = [pattern.sub(f"@{dst}", t) for t in drafted_texts]
                        for res_row in section_results:
                            if isinstance(res_row, dict) and res_row.get("text"):
                                res_row["text"] = pattern.sub(f"@{dst}", res_row["text"])
                    missing = still_missing

                if not used:
                    used = bib_keys[: min(30, len(bib_keys))]
                section_text = _render_references_section(used)
                section_path.write_text(section_text, encoding="utf-8")
                passed = (not missing) if strict_gate else True
                score = 1.0 if passed else 0.0
                row: Dict[str, Any] = {
                    "section": section,
                    "path": _to_rel(section_path),
                    "attempts": 0,
                    "passed": passed,
                    "score": score,
                    "evaluation_path": None,
                    "reference_keys_used": len(used),
                    "reference_keys_missing": missing[:50] if missing else None,
                    "reference_keys_repaired": sorted(repaired) if repaired else None,
                }
            else:
                # No bib keys — generate via LLM like other sections
                res = await _gen_eval_section(section, idx)
                row = res
                section_text = res["text"]
                passed = res["passed"]
                score = res["score"]

            section_results.append(row)
            section_scores[section] = score
            section_text_map[section] = section_text
            if passed:
                passed_sections.append((section, section_path))
                drafted_texts.append(section_text)
            else:
                failed_sections.append(section)

        # Re-sort section_results by original order
        order_map = {s: i for i, s in enumerate(section_list)}
        section_results.sort(key=lambda r: order_map.get(r.get("section", ""), 999))

        manifest_path = merge_dir / "merge_queue.json"
        manifest_path.write_text(
            json.dumps(section_results, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )

        if len(passed_sections) != len(section_list):
            try:
                combined_partial = "\n\n".join(
                    (sections_dir / f"{i:02d}_{sec}.md").read_text(encoding="utf-8")
                    for i, sec in enumerate(section_list, start=1)
                )
            except Exception:
                combined_partial = "\n\n".join(
                    section_path.read_text(encoding="utf-8")
                    for _, section_path in passed_sections
                )
            has_review_evidence_failure = any(
                defect in {"insufficient_evidence_linkage", "insufficient_review_evidence_coverage"}
                for row in section_results
                if isinstance(row, dict)
                for defect in (row.get("defects") or [])
            )
            return _build_failure_payload(
                error_code="unsupported_claims" if has_review_evidence_failure else "section_evaluation_failed",
                manifest_path=manifest_path,
                combined_partial=combined_partial,
            )

        combined_text = "\n\n".join(
            section_text_map.get(section, "").strip() for section in section_list if section_text_map.get(section, "").strip()
        )
        combined_path = merge_dir / "combined_draft.md"
        combined_path.write_text(combined_text, encoding="utf-8")

        body_text = "\n\n".join(
            section_text_map.get(section, "").strip()
            for section in section_list
            if section != "references" and section_text_map.get(section, "").strip()
        )
        references_text = section_text_map.get("references", "")
        citation_report = _validate_citations(
            body_text=body_text,
            references_text=references_text,
            bib_keys=bib_keys,
        )
        citation_validation_path = merge_dir / "citation_validation.json"
        citation_validation_path.write_text(
            json.dumps(citation_report, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )
        if not citation_report.get("pass"):
            if strict_gate:
                if "references" not in failed_sections:
                    failed_sections.append("references")
                return _build_failure_payload(
                    error_code="citation_validation_failed",
                    manifest_path=manifest_path,
                    combined_partial=combined_text,
                    citation_validation_path=citation_validation_path,
                    citation_report=citation_report,
                )
            # Advisory mode (default): keep the draft, surface the mismatch.
            citation_report["advisory"] = True
            citation_validation_path.write_text(
                json.dumps(citation_report, ensure_ascii=True, indent=2),
                encoding="utf-8",
            )
            logger.warning(
                "manuscript_writer: citation validation mismatch kept as advisory: unknown=%s missing_in_refs=%s",
                citation_report.get("unknown_citekeys"),
                citation_report.get("missing_reference_citekeys"),
            )

        if review_mode and "abstract" in section_text_map:
            abstract_contract = _validate_review_abstract_contract(section_text_map.get("abstract", ""))
            abstract_contract_path = reviews_dir / "abstract_contract.json"
            abstract_contract_path.write_text(
                json.dumps(abstract_contract, ensure_ascii=True, indent=2),
                encoding="utf-8",
            )
            if not abstract_contract.get("pass"):
                if strict_gate:
                    if "abstract" not in failed_sections:
                        failed_sections.append("abstract")
                    return _build_failure_payload(
                        error_code="abstract_incomplete",
                        manifest_path=manifest_path,
                        combined_partial=combined_text,
                        citation_validation_path=citation_validation_path,
                        citation_report=citation_report,
                    )
                # Advisory (default): the slot check is keyword heuristics and has
                # already mis-judged Chinese structured abstracts once; the
                # abstract itself passed section evaluation, so record and continue.
                abstract_contract["advisory"] = True
                abstract_contract_path.write_text(
                    json.dumps(abstract_contract, ensure_ascii=True, indent=2),
                    encoding="utf-8",
                )
                logger.warning(
                    "manuscript_writer: abstract contract mismatch kept as advisory: %s",
                    abstract_contract.get("missing_slots"),
                )

        # ---------------------------------------------------------------
        # Phase: Segmented merge — transition smoothing + final pass
        # ---------------------------------------------------------------
        # Step 1: Smooth transitions between adjacent section pairs
        ordered_sections = [s for s in section_list if s != "references" and section_text_map.get(s, "").strip()]
        smoothed_texts: Dict[str, str] = dict(section_text_map)  # start with originals

        async def _smooth_transition(sec_a: str, sec_b: str) -> Optional[str]:
            """Ask LLM to smooth the transition between two adjacent sections."""
            text_a = section_text_map.get(sec_a, "").strip()
            text_b = section_text_map.get(sec_b, "").strip()
            if not text_a or not text_b:
                return None
            # Only use last ~800 chars of sec_a and first ~800 chars of sec_b
            tail_a = text_a[-800:] if len(text_a) > 800 else text_a
            head_b = text_b[:800] if len(text_b) > 800 else text_b
            prompt = (
                "You are a scientific writing editor. "
                "Improve the transition between these two consecutive sections. "
                "Return ONLY the revised ending paragraph of the first section "
                "and the revised opening paragraph of the second section, "
                "separated by '---SPLIT---'. "
                "Keep all facts and citations intact. Be concise.\n\n"
                f"End of '{_section_title(sec_a)}':\n{tail_a}\n\n"
                f"Start of '{_section_title(sec_b)}':\n{head_b}"
            )
            return await _chat(merge_llm, prompt, merge_model_name, max_tokens=_MAX_TOKENS_TRANSITION, purpose="manuscript_writer:merge")

        # Run transition smoothing in parallel for all adjacent pairs
        if len(ordered_sections) >= 2:
            pairs = list(zip(ordered_sections[:-1], ordered_sections[1:]))
            logger.info("manuscript_writer: smoothing %d section transitions", len(pairs))
            transition_results = await asyncio.gather(
                *[_smooth_transition(a, b) for a, b in pairs],
                return_exceptions=True,
            )
            for (sec_a, sec_b), result in zip(pairs, transition_results):
                if isinstance(result, Exception) or result is None:
                    continue
                # Best-effort: if the LLM returned a split, apply it
                if "---SPLIT---" in result:
                    parts = result.split("---SPLIT---", 1)
                    if len(parts) == 2:
                        # Replace last paragraph of sec_a
                        orig_a = smoothed_texts.get(sec_a, "")
                        last_para_start = orig_a.rfind("\n\n")
                        if last_para_start > 0:
                            smoothed_texts[sec_a] = orig_a[:last_para_start] + "\n\n" + parts[0].strip()
                        # Replace first paragraph of sec_b
                        orig_b = smoothed_texts.get(sec_b, "")
                        first_para_end = orig_b.find("\n\n")
                        if first_para_end > 0:
                            smoothed_texts[sec_b] = parts[1].strip() + "\n\n" + orig_b[first_para_end + 2:]

        # Step 2: Combine smoothed sections + references
        pre_polish_text = "\n\n".join(
            smoothed_texts.get(section, "").strip()
            for section in section_list
            if smoothed_texts.get(section, "").strip()
        )
        pre_polish_path = merge_dir / "pre_polish_draft.md"
        pre_polish_path.write_text(pre_polish_text, encoding="utf-8")

        quality_gate_passed = len(failed_sections) == 0 and (
            bool(citation_report.get("pass")) or bool(citation_report.get("advisory"))
        )
        polished_workspace_path = merge_dir / "polished_draft.md"
        polished_text = pre_polish_text
        polish_gate_passed = True
        public_release_ready = True
        release_state = "final"
        release_summary = "Manuscript passed section, citation, and final polish gates."

        if final_polish_enabled:
            polish_gate_passed = False
            current_candidate = pre_polish_text
            current_polish_stage = "polish_generation"
            current_polish_attempt = 0
            try:
                for attempt in range(1, final_polish_max_revisions + 1):
                    current_polish_attempt = attempt
                    logger.info(
                        "manuscript_writer: final polish attempt %d/%d started",
                        attempt,
                        final_polish_max_revisions,
                    )
                    if attempt == 1:
                        polish_prompt = _build_final_polish_prompt(
                            task,
                            analysis_memo,
                            current_candidate,
                            review_mode=review_mode,
                        )
                    else:
                        polish_prompt = _build_final_polish_revision_prompt(
                            task,
                            analysis_memo,
                            current_candidate,
                            release_review or {},
                            review_mode=review_mode,
                        )
                    current_polish_stage = "polish_generation"
                    polished_candidate = await _maybe_wait_with_timeout(
                        _chat(final_polish_merge_llm, polish_prompt, merge_model_name, max_tokens=_MAX_TOKENS_MERGE, purpose="manuscript_writer:polish"),
                        final_polish_step_timeout_sec,
                    )
                    attempt_path = merge_dir / f"polished_draft_attempt_{attempt}.md"
                    attempt_path.write_text(polished_candidate, encoding="utf-8")

                    review_prompt = _build_release_review_prompt(
                        task,
                        analysis_memo,
                        polished_candidate,
                        review_mode=review_mode,
                    )
                    current_polish_stage = "release_review"
                    review_raw = await _maybe_wait_with_timeout(
                        _chat(final_polish_eval_llm, review_prompt, eval_model, max_tokens=_MAX_TOKENS_EVAL, purpose="manuscript_writer:eval"),
                        final_polish_step_timeout_sec,
                    )
                    release_review = _parse_json_payload(review_raw)
                    if release_review is None:
                        release_review = {
                            "scores": {},
                            "defects": ["release_review_json_parse_failed"],
                            "revision_instructions": [
                                "Remove duplication and awkward transitions.",
                                "Fix citation or formatting issues without changing facts.",
                            ],
                            "release_summary": "The final release review could not verify that the manuscript is ready for publication.",
                            "pass": False,
                        }

                    release_consistency_report = _build_release_consistency_report(
                        baseline_text=pre_polish_text,
                        candidate_text=polished_candidate,
                    )
                    release_consistency_path = reviews_dir / f"final_release_consistency_{attempt}.json"
                    release_consistency_path.write_text(
                        json.dumps(release_consistency_report, ensure_ascii=True, indent=2),
                        encoding="utf-8",
                    )
                    release_review = _apply_release_consistency_report(
                        release_review,
                        release_consistency_report,
                    )

                    release_scores = release_review.get("scores") or {}
                    release_score = (
                        _weighted_score(release_scores, _FINAL_POLISH_EVAL_DIMS)
                        if isinstance(release_scores, dict)
                        else 0.0
                    )
                    pass_flag = release_review.get("pass")
                    if pass_flag is None:
                        pass_flag = release_score >= final_polish_threshold
                    polish_gate_passed = bool(pass_flag) and release_score >= final_polish_threshold

                    review_path = reviews_dir / f"final_release_eval_{attempt}.json"
                    review_path.write_text(
                        json.dumps(release_review, ensure_ascii=True, indent=2),
                        encoding="utf-8",
                    )

                    if polish_gate_passed:
                        polished_text = polished_candidate
                        polished_workspace_path.write_text(polished_text, encoding="utf-8")
                        release_summary = str(
                            release_review.get("release_summary")
                            or "Manuscript passed the final polish and release gate."
                        ).strip() or "Manuscript passed the final polish and release gate."
                        break

                    current_candidate = polished_candidate
            except Exception as exc:
                logger.warning(
                    "manuscript_writer final polish failed at stage=%s attempt=%s: %s",
                    current_polish_stage,
                    current_polish_attempt,
                    exc,
                )
                release_review = _build_polish_failure_review(
                    stage=current_polish_stage,
                    attempt=current_polish_attempt or 1,
                    exc=exc,
                )
                if strict_gate:
                    return _build_failure_payload(
                        error_code="polish_quality_gate_failed",
                        manifest_path=manifest_path,
                        combined_partial=pre_polish_text,
                        citation_validation_path=citation_validation_path,
                        citation_report=citation_report,
                        release_summary=_release_summary_for_error(
                            "polish_quality_gate_failed",
                            evaluation=release_review,
                        ),
                        polish_review_payload=release_review,
                    )
                # Advisory (default): deliver the evaluated draft unpolished.
                polished_text = pre_polish_text
                release_state = "draft"
                release_summary = (
                    "Final polish could not complete; the fully evaluated draft was delivered as-is."
                )

            if not polish_gate_passed:
                public_release_ready = False
                if strict_gate:
                    release_state = "blocked"
                    return _build_failure_payload(
                        error_code="polish_quality_gate_failed",
                        manifest_path=manifest_path,
                        combined_partial=pre_polish_text,
                        citation_validation_path=citation_validation_path,
                        citation_report=citation_report,
                        release_summary=_release_summary_for_error(
                            "polish_quality_gate_failed",
                            evaluation=release_review,
                        ),
                        polish_review_payload=release_review,
                    )
                release_state = "draft"
                release_summary = str(
                    (release_review or {}).get("release_summary") or ""
                ).strip() or "Final polish gate did not pass; the evaluated draft was delivered for review."
        else:
            polished_workspace_path.write_text(polished_text, encoding="utf-8")

        output_file.write_text(polished_text, encoding="utf-8")
        from tool_box.watermark import apply_watermark_inplace
        apply_watermark_inplace(output_file)

        hidden_artifact_prefixes = _hidden_prefixes(
            work_dir,
            output_file,
            analysis_file,
        )
        cleanup_errors: List[str] = []
        if not keep_workspace:
            try:
                shutil.rmtree(work_dir)
            except Exception as exc:
                cleanup_errors.append(str(exc))

        stats = _build_stats(citation_report=citation_report)
        stats["final_chars"] = len(polished_text or "")
        if release_review is not None:
            stats["final_polish_score"] = round(
                _weighted_score(release_review.get("scores") or {}, _FINAL_POLISH_EVAL_DIMS),
                4,
            ) if isinstance(release_review.get("scores"), dict) else 0.0
        stats["final_polish_enabled"] = final_polish_enabled
        stats["final_polish_passed"] = polish_gate_passed
        promoted_paths = _promote_explicit_outputs_to_task_dir(
            ("effective_output_path", output_file),
            ("effective_analysis_path", analysis_file),
        )
        return _attach_output_location({
            "tool": "manuscript_writer",
            "success": True,
            "article_mode_requested": article_mode_requested,
            "article_mode_resolved": article_mode_resolved,
            "quality_gate_passed": quality_gate_passed,
            "polish_gate_passed": polish_gate_passed,
            "public_release_ready": public_release_ready,
            "release_state": release_state,
            "evidence_coverage_passed": bool(
                (review_evidence.get("coverage_report") or {}).get("pass")
            )
            if review_mode and isinstance(review_evidence, dict)
            else None,
            "coverage_summary": str(
                (review_evidence.get("coverage_report") or {}).get("summary") or ""
            ).strip()
            if review_mode and isinstance(review_evidence, dict)
            else None,
            "evidence_coverage_notice": evidence_coverage_notice if review_mode else None,
            "coverage_report_path": _to_rel(review_evidence.get("coverage_report_path"))
            if review_mode and isinstance(review_evidence.get("coverage_report_path"), Path)
            else None,
            "evidence_coverage_path": _to_rel(review_evidence.get("evidence_coverage_path"))
            if review_mode and isinstance(review_evidence.get("evidence_coverage_path"), Path)
            else None,
            "study_matrix_path": _to_rel(review_evidence.get("study_matrix_path"))
            if review_mode and isinstance(review_evidence.get("study_matrix_path"), Path)
            else None,
            "reference_library_path": reference_library_path or None,
            "release_summary": release_summary,
            "failed_sections": list(failed_sections),
            "section_scores": dict(section_scores),
            "analysis_path": _to_rel(analysis_file),
            "effective_analysis_path": promoted_paths.get("effective_analysis_path") or _to_rel(analysis_file),
            "sections_dir": None if not keep_workspace else _to_rel(sections_dir),
            "reviews_dir": None if not keep_workspace else _to_rel(reviews_dir),
            "combined_path": None if not keep_workspace else _to_rel(combined_path),
            "merge_queue": None if not keep_workspace else _to_rel(manifest_path),
            "citation_validation_path": None if not keep_workspace else _to_rel(citation_validation_path),
            "citation_validation": citation_report if keep_workspace else None,
            "release_consistency_path": None if not keep_workspace else _to_rel(release_consistency_path),
            "release_consistency_report": release_consistency_report if keep_workspace else None,
            "output_path": _to_rel(output_file),
            "effective_output_path": promoted_paths.get("effective_output_path") or _to_rel(output_file),
            "pre_polish_output_path": _to_rel(pre_polish_path),
            "polished_output_path": _to_rel(polished_workspace_path),
            "release_review": release_review if keep_workspace else None,
            "sections": section_results,
            "draft_chars": len(polished_text or ""),
            "intermediate_purged": not keep_workspace,
            "temp_workspace": _to_rel(work_dir),
            "hidden_artifact_prefixes": hidden_artifact_prefixes,
            "cleanup_errors": cleanup_errors,
            "run_stats": stats,
            "bib_precheck_warning": bib_precheck_warning,
        }, base_dir=work_dir)
    except Exception as exc:
        logger.exception("Manuscript writer failed")
        return {"tool": "manuscript_writer", "success": False, "error": str(exc)}


manuscript_writer_tool = {
    "name": "manuscript_writer",
    "description": (
        "Generate a research manuscript with staged section drafting, evaluation, and merge. "
        "Uses the default LLM provider unless overridden."
    ),
    "category": "document_writing",
    "parameters_schema": {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "Manuscript goal or writing request.",
            },
            "output_path": {
                "type": "string",
                "description": "Output file path for the final manuscript (project-relative).",
            },
            "context_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of context file paths to ground the draft.",
            },
            "analysis_path": {
                "type": "string",
                "description": "Optional path for analysis memo (project-relative).",
            },
            "sections": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Section list (default: abstract/introduction/method/experiment/result/discussion/conclusion/references).",
            },
            "article_mode": {
                "type": "string",
                "enum": ["auto", "review", "research"],
                "description": "Optional article mode override. Use review to force review/synthesis behavior, research to force original-study behavior, or auto to infer from the task.",
                "default": "auto",
            },
            "max_revisions": {
                "type": "integer",
                "description": "Max revision attempts per section.",
                "default": _DEFAULT_MAX_REVISIONS,
            },
            "evaluation_threshold": {
                "type": "number",
                "description": "Pass threshold for average evaluation score (0-1).",
                "default": _DEFAULT_THRESHOLD,
            },
            "max_context_bytes": {
                "type": "integer",
                "description": "Per-file max bytes to read into context.",
                "default": _DEFAULT_MAX_CONTEXT_BYTES,
            },
            "generation_model": {
                "type": "string",
                "description": "Optional model name (or env var key) for generation.",
            },
            "evaluation_model": {
                "type": "string",
                "description": "Optional model name (or env var key) for evaluation.",
            },
            "merge_model": {
                "type": "string",
                "description": "Optional model name (or env var key) for final merge rewrite.",
            },
            "generation_provider": {
                "type": "string",
                "description": "Optional provider override for generation (e.g., qwen, glm).",
            },
            "evaluation_provider": {
                "type": "string",
                "description": "Optional provider override for evaluation (e.g., qwen, glm).",
            },
            "merge_provider": {
                "type": "string",
                "description": "Optional provider override for merge (e.g., qwen, glm).",
            },
            "keep_workspace": {
                "type": "boolean",
                "description": "Keep intermediate drafts/reviews workspace for audit/debugging.",
                "default": False,
            },
            "draft_only": {
                "type": "boolean",
                "description": "Assemble a lightweight local draft without the full staged evaluation and polish pipeline.",
                "default": False,
            },
        },
        "required": ["task", "output_path"],
    },
    "handler": manuscript_writer_handler,
    "tags": ["writing", "manuscript", "evaluation", "qwen"],
    "examples": [
        "Generate a staged manuscript using data/examples/outline.txt and save to runtime/session_x/shared/draft.md",
    ],
}
