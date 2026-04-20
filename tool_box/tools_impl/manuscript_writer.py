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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Dict, Iterable, List, Optional, Sequence, Tuple

from app.llm import LLMClient
from app.services.llm.llm_service import LLMService, get_llm_service

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
_RUNTIME_DIR = _PROJECT_ROOT / "runtime"
_DEFAULT_MAX_CONTEXT_BYTES = 200_000  # 200 KB per file
_DEFAULT_MAX_REVISIONS = 5
_DEFAULT_THRESHOLD = 0.8
_DEFAULT_FINAL_POLISH_MAX_REVISIONS = 2
_DEFAULT_FINAL_POLISH_THRESHOLD = 0.85
_DEFAULT_FINAL_POLISH_STEP_TIMEOUT_SEC = 0.0
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


def _env_enabled(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in _TRUTHY_VALUES


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        return path.is_relative_to(root)
    except AttributeError:
        return str(path).startswith(str(root))


def _resolve_project_path(path_str: str) -> Path:
    raw_path = Path(path_str)
    if not raw_path.is_absolute():
        raw_path = _PROJECT_ROOT / raw_path
    resolved = raw_path.resolve()
    if not _is_relative_to(resolved, _PROJECT_ROOT):
        raise ValueError(f"Path is outside project root: {path_str}")
    return resolved


def _resolve_session_scoped_project_path(path_str: str, session_dir: Optional[Path]) -> Path:
    raw_path = Path(path_str).expanduser()
    if raw_path.is_absolute():
        return _resolve_project_path(str(raw_path))
    if session_dir is not None:
        resolved = (session_dir / raw_path).resolve()
        if not _is_relative_to(resolved, session_dir):
            raise ValueError(f"Path escapes session workspace: {path_str}")
        return resolved
    return _resolve_project_path(path_str)


def _read_text_file(path: Path, max_bytes: int) -> str:
    if not path.exists():
        raise FileNotFoundError(f"Missing file: {path}")
    if path.is_dir():
        raise IsADirectoryError(f"Expected file, got directory: {path}")
    file_size = path.stat().st_size
    with path.open("rb") as handle:
        if file_size > max_bytes:
            content = handle.read(max_bytes)
            suffix = (
                f"\n\n[TRUNCATED] File size {file_size} bytes exceeds limit {max_bytes} bytes."
            )
            return content.decode("utf-8", errors="replace") + suffix
        return handle.read().decode("utf-8", errors="replace")


def _build_context_blocks(paths: Iterable[str], max_bytes: int) -> str:
    blocks: List[str] = []
    for raw in paths:
        if not raw:
            continue
        try:
            path = _resolve_project_path(raw)
            if path.suffix.lower() not in _ALLOWED_TEXT_EXTENSIONS:
                logger.warning("Skipping unsupported context file type: %s", path)
                continue
            content = _read_text_file(path, max_bytes)
            rel = path.relative_to(_PROJECT_ROOT)
            blocks.append(f"### File: {rel}\n{content}")
        except Exception as exc:
            logger.warning("Failed to read context file %s: %s", raw, exc)
    return "\n\n".join(blocks).strip()


def _default_analysis_path(output_path: Path) -> Path:
    return output_path.with_suffix(output_path.suffix + ".analysis.md")


def _resolve_session_dir(session_id: Optional[str]) -> Optional[Path]:
    if not session_id:
        return None
    normalized = str(session_id).strip()
    if not normalized:
        return None
    session_name = normalized if normalized.startswith("session_") else f"session_{normalized}"
    session_dir = _RUNTIME_DIR / session_name
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def _is_review_article_task(task: str) -> bool:
    text = str(task or "").strip().lower()
    if not text:
        return False
    review_markers = (
        "review",
        "review article",
        "literature review",
        "systematic review",
        "narrative review",
        "survey article",
        "state-of-the-art review",
        "write a review",
        "review on",
        "综述",
        "文献综述",
        "系统综述",
        "叙述性综述",
        "综述文章",
    )
    return any(marker in text for marker in review_markers)


def _normalize_article_mode(article_mode: Optional[str]) -> str:
    raw = str(article_mode or "").strip().lower()
    if not raw:
        return "auto"
    aliases = {
        "review_article": "review",
        "literature_review": "review",
        "review_synthesis": "review",
        "synthesis": "review",
        "original": "research",
        "original_research": "research",
        "research_article": "research",
        "study": "research",
    }
    normalized = aliases.get(raw, raw)
    if normalized not in _VALID_ARTICLE_MODES:
        return "auto"
    return normalized


def _resolve_article_mode(article_mode: Optional[str], task: str) -> Tuple[str, bool]:
    requested = _normalize_article_mode(article_mode)
    if requested == "review":
        return requested, True
    if requested == "research":
        return requested, False
    resolved_review_mode = _is_review_article_task(task)
    return ("review" if resolved_review_mode else "research"), resolved_review_mode


def _resolve_model_name(model_name: Optional[str]) -> Optional[str]:
    if not model_name:
        return None
    env_value = os.getenv(model_name)
    if env_value:
        return env_value.strip() or None
    return model_name.strip()


def _build_llm_service(
    provider: Optional[str],
    model: Optional[str],
    *,
    timeout: Optional[float] = None,
) -> Tuple[LLMService, Optional[str]]:
    if timeout is not None:
        client = LLMClient(provider=provider, model=model, timeout=timeout)
        return LLMService(client), model
    if provider:
        client = LLMClient(provider=provider, model=model)
        return LLMService(client), model
    return get_llm_service(), model


async def _chat(llm: LLMService, prompt: str, model: Optional[str]) -> str:
    if model:
        return await llm.chat_async(prompt, model=model)
    return await llm.chat_async(prompt)


async def _maybe_wait_with_timeout(
    operation: Awaitable[str],
    timeout_sec: Optional[float],
) -> str:
    if timeout_sec is None or timeout_sec <= 0:
        return await operation
    return await asyncio.wait_for(operation, timeout=timeout_sec)


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


def _load_jsonl_file(path: Path) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


def _load_json_file(path: Path) -> Optional[Dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _first_context_path(context_paths: Iterable[str], suffix: str) -> Optional[str]:
    for raw in context_paths:
        value = str(raw or "").strip()
        if value.lower().endswith(suffix.lower()):
            return value
    return None


def _take_unique(items: Iterable[str], *, limit: int) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for item in items:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
        if len(out) >= limit:
            break
    return out


def _review_study_card_excerpt(card: Dict[str, Any]) -> str:
    findings = "; ".join(card.get("quantitative_findings") or []) or "Not available"
    limitations = "; ".join(card.get("limitations") or []) or "Not available"
    snippets = "; ".join(card.get("supporting_snippets") or []) or "Not available"
    supported = ", ".join(card.get("section_support") or []) or "Not available"
    return (
        f"[@{card.get('citekey')}] {card.get('title')} ({card.get('year') or 'n.d.'}, {card.get('journal') or 'Unknown journal'})\n"
        f"- Evidence tier: {card.get('evidence_tier') or 'unknown'}\n"
        f"- Study type: {card.get('study_type') or 'unspecified'}\n"
        f"- Model system: {', '.join(card.get('model_system') or []) or 'Not available'}\n"
        f"- Intervention/delivery: {', '.join(card.get('intervention_delivery') or []) or 'Not available'}\n"
        f"- Receptor/mechanism terms: {', '.join(card.get('receptor_mechanism_terms') or []) or 'Not available'}\n"
        f"- Quantitative findings: {findings}\n"
        f"- Limitations: {limitations}\n"
        f"- Supporting sections: {supported}\n"
        f"- Supporting snippets: {snippets}"
    )


def _render_study_matrix(cards: List[Dict[str, Any]]) -> str:
    lines = [
        "# Study Matrix",
        "",
        "| Citekey | Evidence | Study type | Model system | Quantitative findings | Supported sections |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for card in cards:
        lines.append(
            "| {citekey} | {tier} | {study_type} | {model} | {quant} | {supported} |".format(
                citekey=f"[@{card.get('citekey')}]",
                tier=card.get("evidence_tier") or "unknown",
                study_type=card.get("study_type") or "unspecified",
                model=", ".join(card.get("model_system") or []) or "Not available",
                quant="Yes" if card.get("quantitative_findings") else "No",
                supported=", ".join(card.get("section_support") or []) or "Not available",
            )
        )
    lines.append("")
    return "\n".join(lines)


def _render_coverage_markdown(report: Dict[str, Any]) -> str:
    counts = report.get("counts") or {}
    thresholds = report.get("thresholds") or {}
    lines = [
        "# Evidence Coverage",
        "",
        f"Status: {'PASS' if report.get('pass') else 'BLOCKED'}",
        "",
        str(report.get("summary") or ""),
        "",
        f"- Total included studies: {counts.get('total_studies', 0)} / {thresholds.get('min_total_studies', 0)}",
        f"- Full-text studies: {counts.get('full_text_studies', 0)} / {thresholds.get('min_full_text_studies', 0)}",
        f"- Quantitative studies: {counts.get('quantitative_studies', 0)} / {thresholds.get('min_quantitative_studies', 0)}",
        "",
        "## Core-section support",
        "",
    ]
    for section, value in (report.get("section_support_counts") or {}).items():
        lines.append(
            f"- {section}: {value} / {thresholds.get('min_support_per_core_section', 0)}"
        )
    failures = report.get("failures") or []
    lines.append("")
    lines.append("## Failures")
    lines.append("")
    if failures:
        lines.extend(f"- {item}" for item in failures)
    else:
        lines.append("- None")
    lines.append("")
    return "\n".join(lines)


def _build_review_context_bundle(
    *,
    study_cards: List[Dict[str, Any]],
    coverage_report: Dict[str, Any],
    evidence_md_text: str,
) -> Dict[str, str]:
    section_contexts: Dict[str, str] = {}
    summary_block = "\n".join(
        [
            "## Evidence coverage gate",
            str(coverage_report.get("summary") or "Coverage summary unavailable."),
            "",
        ]
    ).strip()
    support_key = {
        "abstract": {"introduction", "result", "discussion", "conclusion"},
        "introduction": {"introduction"},
        "method": {"method"},
        "experiment": {"experiment"},
        "result": {"result"},
        "discussion": {"discussion"},
        "conclusion": {"conclusion", "discussion", "result"},
        "references": set(),
    }
    for section, supported_sections in support_key.items():
        if section == "references":
            section_contexts[section] = summary_block
            continue
        cards_for_section = [
            card
            for card in study_cards
            if supported_sections.intersection(set(card.get("section_support") or []))
        ]
        cards_for_section.sort(
            key=lambda card: (
                0 if card.get("evidence_tier") == "full_text" else 1,
                0 if card.get("quantitative_findings") else 1,
                str(card.get("year") or ""),
            )
        )
        excerpts = [_review_study_card_excerpt(card) for card in cards_for_section[:10]]
        extra_notes = evidence_md_text.strip() if section in {"method", "discussion"} else ""
        section_contexts[section] = "\n\n".join(
            part for part in (summary_block, "\n\n".join(excerpts), extra_notes) if part
        ).strip()
    global_context = "\n\n".join(
        [
            summary_block,
            "\n\n".join(_review_study_card_excerpt(card) for card in study_cards[:12]),
            evidence_md_text.strip(),
        ]
    ).strip()
    section_contexts["__global__"] = global_context
    return section_contexts


def _load_review_evidence(
    *,
    context_paths: List[str],
    merge_dir: Path,
    max_context_bytes: int,
) -> Dict[str, Any]:
    study_cards_path = _first_context_path(context_paths, "study_cards.jsonl")
    coverage_report_path = _first_context_path(context_paths, "coverage_report.json")
    evidence_md_path = _first_context_path(context_paths, "evidence.md")
    reference_library_path = _first_context_path(context_paths, ".bib")

    study_cards: List[Dict[str, Any]] = []
    coverage_report: Optional[Dict[str, Any]] = None
    evidence_md_text = ""
    if study_cards_path:
        try:
            study_cards = _load_jsonl_file(_resolve_project_path(study_cards_path))
        except Exception:
            study_cards = []
    if coverage_report_path:
        try:
            coverage_report = _load_json_file(_resolve_project_path(coverage_report_path))
        except Exception:
            coverage_report = None
    if evidence_md_path:
        try:
            evidence_md_text = _read_text_file(_resolve_project_path(evidence_md_path), max_context_bytes)
        except Exception:
            evidence_md_text = ""

    if coverage_report is None and study_cards:
        counts = {
            "total_studies": len(study_cards),
            "full_text_studies": len([card for card in study_cards if card.get("evidence_tier") == "full_text"]),
            "quantitative_studies": len([card for card in study_cards if card.get("quantitative_findings")]),
        }
        thresholds = {
            "min_total_studies": 15,
            "min_full_text_studies": 6,
            "min_quantitative_studies": 4,
            "min_support_per_core_section": 2,
        }
        section_support_counts = {
            section: len([card for card in study_cards if section in (card.get("section_support") or [])])
            for section in ("introduction", "method", "experiment", "result", "discussion", "conclusion")
        }
        failures: List[str] = []
        if counts["total_studies"] < thresholds["min_total_studies"]:
            failures.append(
                f"only {counts['total_studies']} included studies; require at least {thresholds['min_total_studies']}"
            )
        if counts["full_text_studies"] < thresholds["min_full_text_studies"]:
            failures.append(
                f"only {counts['full_text_studies']} full-text studies; require at least {thresholds['min_full_text_studies']}"
            )
        if counts["quantitative_studies"] < thresholds["min_quantitative_studies"]:
            failures.append(
                f"only {counts['quantitative_studies']} studies with quantitative findings; require at least {thresholds['min_quantitative_studies']}"
            )
        for section, support_count in section_support_counts.items():
            if support_count < thresholds["min_support_per_core_section"]:
                failures.append(
                    f"{section} is supported by only {support_count} studies; require at least {thresholds['min_support_per_core_section']}"
                )
        coverage_report = {
            "profile": "pi_ready_review",
            "pass": not failures,
            "summary": (
                "Evidence coverage passed."
                if not failures
                else "Evidence coverage blocked: " + "; ".join(failures)
            ),
            "thresholds": thresholds,
            "counts": counts,
            "section_support_counts": section_support_counts,
            "failures": failures,
        }

    evidence_coverage_path = merge_dir / "evidence_coverage.md"
    study_matrix_path = merge_dir / "study_matrix.md"
    coverage_report_output_path = merge_dir / "coverage_report.json"
    if coverage_report is not None:
        evidence_coverage_path.write_text(_render_coverage_markdown(coverage_report), encoding="utf-8")
        coverage_report_output_path.write_text(json.dumps(coverage_report, ensure_ascii=False, indent=2), encoding="utf-8")
    if study_cards:
        study_matrix_path.write_text(_render_study_matrix(study_cards), encoding="utf-8")

    if not study_cards:
        fallback_report = coverage_report or {
            "pass": False,
            "summary": "Evidence coverage blocked: structured study_cards.jsonl was not provided for a review manuscript.",
            "failures": ["structured study_cards.jsonl missing"],
            "counts": {"total_studies": 0, "full_text_studies": 0, "quantitative_studies": 0},
            "section_support_counts": {},
            "thresholds": {
                "min_total_studies": 15,
                "min_full_text_studies": 6,
                "min_quantitative_studies": 4,
                "min_support_per_core_section": 2,
            },
        }
        evidence_coverage_path.write_text(_render_coverage_markdown(fallback_report), encoding="utf-8")
        coverage_report_output_path.write_text(json.dumps(fallback_report, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "study_cards": [],
            "coverage_report": fallback_report,
            "coverage_report_path": coverage_report_output_path,
            "evidence_coverage_path": evidence_coverage_path,
            "study_matrix_path": study_matrix_path if study_matrix_path.exists() else None,
            "reference_library_path": reference_library_path,
            "section_contexts": {},
        }

    if not evidence_md_text:
        evidence_md_text = "\n\n".join(_review_study_card_excerpt(card) for card in study_cards[:12])

    section_contexts = _build_review_context_bundle(
        study_cards=study_cards,
        coverage_report=coverage_report or {},
        evidence_md_text=evidence_md_text,
    )
    return {
        "study_cards": study_cards,
        "coverage_report": coverage_report or {},
        "coverage_report_path": coverage_report_output_path if coverage_report_output_path.exists() else None,
        "evidence_coverage_path": evidence_coverage_path if evidence_coverage_path.exists() else None,
        "study_matrix_path": study_matrix_path if study_matrix_path.exists() else None,
        "reference_library_path": reference_library_path,
        "section_contexts": section_contexts,
    }


def _validate_review_abstract_contract(text: str) -> Dict[str, Any]:
    normalized = " ".join(str(text or "").strip().lower().split())
    slots = {
        "background": any(token in normalized for token in ("antimicrobial", "infection", "pathogen", "therapy", "pseudomonas")),
        "scope": any(token in normalized for token in ("this review", "we review", "we synthesize", "scope", "review synthesizes")),
        "evidence_base_method": any(token in normalized for token in ("literature", "studies", "evidence", "search", "reviewed")),
        "major_findings": any(token in normalized for token in ("key finding", "collectively", "recent studies", "major", "findings")),
        "limitations": any(token in normalized for token in ("limitation", "heterogeneity", "not available", "limited", "however")),
        "conclusion": any(token in normalized for token in ("overall", "together", "support", "suggest", "conclude", "promise")),
    }
    missing = [key for key, present in slots.items() if not present]
    return {"pass": not missing, "missing_slots": missing}


def _build_review_section_coverage_report(
    *,
    section: str,
    text: str,
    study_cards: List[Dict[str, Any]],
    coverage_report: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    normalized_section = _normalize_section_key(section)
    targets = _REVIEW_SECTION_COVERAGE_TARGETS.get(normalized_section)
    if not targets:
        return None

    thresholds = coverage_report.get("thresholds") if isinstance(coverage_report, dict) else {}
    try:
        min_support_per_core_section = int((thresholds or {}).get("min_support_per_core_section") or 2)
    except (TypeError, ValueError):
        min_support_per_core_section = 2

    eligible_cards = [
        card for card in study_cards if normalized_section in (card.get("section_support") or []) and card.get("citekey")
    ]
    cards_by_citekey = {
        str(card.get("citekey")): card
        for card in eligible_cards
        if card.get("citekey")
    }
    cited_section_keys = [
        key for key in _extract_markdown_citekeys(text) if key in cards_by_citekey
    ]
    cited_unique = list(dict.fromkeys(cited_section_keys))

    available_total = len(cards_by_citekey)
    available_full_text = len(
        [card for card in eligible_cards if card.get("evidence_tier") == "full_text"]
    )
    available_quantitative = len(
        [card for card in eligible_cards if card.get("quantitative_findings")]
    )
    cited_full_text = len(
        [key for key in cited_unique if (cards_by_citekey.get(key) or {}).get("evidence_tier") == "full_text"]
    )
    cited_quantitative = len(
        [key for key in cited_unique if (cards_by_citekey.get(key) or {}).get("quantitative_findings")]
    )

    target_supported = min(
        available_total,
        max(int(targets.get("min_supported_citations") or 0), min_support_per_core_section),
    )
    target_full_text = min(
        available_full_text,
        int(targets.get("min_full_text_citations") or 0),
    )
    has_numeric_claims = bool(_extract_numeric_tokens(text))
    target_quantitative = min(available_quantitative, 1) if has_numeric_claims else 0

    supported_ratio = (
        min(1.0, len(cited_unique) / target_supported)
        if target_supported > 0
        else 0.0
    )
    full_text_ratio = (
        min(1.0, cited_full_text / target_full_text)
        if target_full_text > 0
        else 1.0
    )
    quantitative_ratio = (
        min(1.0, cited_quantitative / target_quantitative)
        if target_quantitative > 0
        else 1.0
    )

    components: List[Tuple[str, float, float]] = [
        ("supported_studies", supported_ratio, 0.7),
        ("full_text_support", full_text_ratio, 0.3),
    ]
    if target_quantitative > 0:
        components = [
            ("supported_studies", supported_ratio, 0.55),
            ("full_text_support", full_text_ratio, 0.25),
            ("quantitative_support", quantitative_ratio, 0.2),
        ]
    total_weight = sum(weight for _, _, weight in components) or 1.0
    score = round(
        sum(ratio * weight for _, ratio, weight in components) / total_weight,
        4,
    )

    shortfalls: List[str] = []
    revision_instructions: List[str] = []
    if target_supported > 0 and len(cited_unique) < target_supported:
        shortfalls.append("supported_study_coverage")
        revision_instructions.append(
            f"Expand the {normalized_section} synthesis so it cites at least {target_supported} section-relevant included studies instead of only {len(cited_unique)}."
        )
    if target_full_text > 0 and cited_full_text < target_full_text:
        shortfalls.append("full_text_support")
        revision_instructions.append(
            f"Anchor the {normalized_section} claims in at least {target_full_text} cited full-text studies when that evidence is available."
        )
    if target_quantitative > 0 and cited_quantitative < target_quantitative:
        shortfalls.append("quantitative_support")
        revision_instructions.append(
            f"Support numeric claims in the {normalized_section} section with at least {target_quantitative} cited study reporting quantitative findings."
        )

    return {
        "section": normalized_section,
        "pass": available_total > 0 and score >= _REVIEW_SECTION_COVERAGE_PASS_THRESHOLD,
        "score": score,
        "available_supported_studies": available_total,
        "available_full_text_studies": available_full_text,
        "available_quantitative_studies": available_quantitative,
        "cited_supported_studies": len(cited_unique),
        "cited_full_text_studies": cited_full_text,
        "cited_quantitative_studies": cited_quantitative,
        "target_supported_citations": target_supported,
        "target_full_text_citations": target_full_text,
        "target_quantitative_citations": target_quantitative,
        "supported_coverage_ratio": round(supported_ratio, 4),
        "full_text_coverage_ratio": round(full_text_ratio, 4),
        "quantitative_coverage_ratio": round(quantitative_ratio, 4),
        "has_numeric_claims": has_numeric_claims,
        "shortfalls": shortfalls,
        "revision_instructions": revision_instructions,
        "cited_supported_citekeys": cited_unique,
    }


def _apply_review_evidence_diagnostics(
    *,
    section: str,
    text: str,
    evaluation_data: Dict[str, Any],
    study_cards: List[Dict[str, Any]],
    coverage_report: Dict[str, Any],
) -> Dict[str, Any]:
    coverage = _build_review_section_coverage_report(
        section=section,
        text=text,
        study_cards=study_cards,
        coverage_report=coverage_report,
    )
    if coverage is None:
        return evaluation_data

    try:
        min_linked_citations = int(((coverage_report or {}).get("thresholds") or {}).get("min_support_per_core_section") or 2)
    except (TypeError, ValueError):
        min_linked_citations = 2
    linked_count = int(coverage.get("cited_supported_studies") or 0)
    scores = evaluation_data.get("scores")
    if not isinstance(scores, dict):
        scores = {}
        evaluation_data["scores"] = scores
    defects = evaluation_data.get("defects")
    if not isinstance(defects, list):
        defects = []
        evaluation_data["defects"] = defects
    revision_instructions = evaluation_data.get("revision_instructions")
    if not isinstance(revision_instructions, list):
        revision_instructions = []
        evaluation_data["revision_instructions"] = revision_instructions

    passes_linkage = linked_count >= min_linked_citations
    scores["evidence_linkage"] = 1.0 if passes_linkage else 0.0
    scores["evidence_coverage"] = float(coverage.get("score") or 0.0)
    evaluation_data["review_evidence_coverage"] = coverage
    if not passes_linkage:
        if "insufficient_evidence_linkage" not in defects:
            defects.append("insufficient_evidence_linkage")
        linkage_instruction = (
            f"Support the {section} synthesis with at least {min_linked_citations} cited included studies that are relevant to this section."
        )
        if linkage_instruction not in revision_instructions:
            revision_instructions.append(linkage_instruction)
        evaluation_data["pass"] = False
    if not coverage.get("pass"):
        if "insufficient_review_evidence_coverage" not in defects:
            defects.append("insufficient_review_evidence_coverage")
        for instruction in coverage.get("revision_instructions") or []:
            if instruction not in revision_instructions:
                revision_instructions.append(str(instruction))
        evaluation_data["pass"] = False
    return evaluation_data


def _weighted_score(
    scores: Dict[str, Any],
    dimensions: Optional[List[str]] = None,
) -> float:
    """Compute weighted average over relevant dimensions for a section."""
    total_weight = 0.0
    weighted_sum = 0.0
    dims = dimensions or list(scores.keys())
    for dim in dims:
        value = scores.get(dim)
        if value is None:
            continue
        try:
            v = float(value)
        except (TypeError, ValueError):
            continue
        w = _DIMENSION_WEIGHTS.get(dim, 1.0)
        weighted_sum += v * w
        total_weight += w
    if total_weight == 0.0:
        return 0.0
    return weighted_sum / total_weight


def _average_score(scores: Dict[str, Any]) -> float:
    """Backward-compatible unweighted average."""
    return _weighted_score(scores)


def _extract_markdown_citekeys(text: str) -> List[str]:
    """Extract citekeys from Markdown citekey syntax, including grouped citations."""
    if not text:
        return []
    keys: List[str] = []
    for m in re.finditer(r"\[((?:\s*@[A-Za-z0-9_:\-]+\s*(?:;\s*@[A-Za-z0-9_:\-]+\s*)*))\]", text):
        raw_group = m.group(1)
        for part in raw_group.split(";"):
            normalized = part.strip()
            if normalized.startswith("@"):
                normalized = normalized[1:].strip()
            if normalized and normalized not in keys:
                keys.append(normalized)
    return keys


def _extract_heading_sequence(text: str) -> List[str]:
    headings: List[str] = []
    for match in re.finditer(r"^(#{1,6})\s+(.*\S)\s*$", str(text or ""), flags=re.MULTILINE):
        heading = re.sub(r"\s+", " ", match.group(2).strip()).lower()
        if heading:
            headings.append(heading)
    return headings


def _extract_numeric_tokens(text: str) -> List[str]:
    tokens: List[str] = []
    for match in _NUMERIC_TOKEN_RE.finditer(str(text or "")):
        token = match.group(0).replace(",", "").strip()
        if token and token not in tokens:
            tokens.append(token)
    return tokens


def _build_release_consistency_report(
    *,
    baseline_text: str,
    candidate_text: str,
) -> Dict[str, Any]:
    baseline_headings = _extract_heading_sequence(baseline_text)
    candidate_headings = _extract_heading_sequence(candidate_text)
    baseline_citekeys = _extract_markdown_citekeys(baseline_text)
    candidate_citekeys = _extract_markdown_citekeys(candidate_text)
    baseline_numbers = _extract_numeric_tokens(baseline_text)
    candidate_numbers = _extract_numeric_tokens(candidate_text)

    added_citekeys = [key for key in candidate_citekeys if key not in baseline_citekeys]
    removed_citekeys = [key for key in baseline_citekeys if key not in candidate_citekeys]
    added_numeric_tokens = [token for token in candidate_numbers if token not in baseline_numbers]
    removed_numeric_tokens = [token for token in baseline_numbers if token not in candidate_numbers]

    defects: List[str] = []
    revision_instructions: List[str] = []
    if candidate_headings != baseline_headings:
        defects.append("heading_structure_changed")
        revision_instructions.append(
            "Restore the original section heading sequence and keep section structure unchanged during final polish."
        )
    if added_citekeys or removed_citekeys:
        defects.append("citation_set_changed")
        revision_instructions.append(
            "Restore the original citation set; final polish must not add or remove citekeys."
        )
    if added_numeric_tokens or removed_numeric_tokens:
        defects.append("numeric_claims_changed")
        revision_instructions.append(
            "Restore the original numeric claims; final polish must not add, delete, or alter numeric values."
        )

    return {
        "pass": len(defects) == 0,
        "baseline_headings": baseline_headings,
        "candidate_headings": candidate_headings,
        "added_citekeys": added_citekeys,
        "removed_citekeys": removed_citekeys,
        "added_numeric_tokens": added_numeric_tokens,
        "removed_numeric_tokens": removed_numeric_tokens,
        "defects": defects,
        "revision_instructions": revision_instructions,
    }


def _apply_release_consistency_report(
    release_review: Optional[Dict[str, Any]],
    consistency_report: Dict[str, Any],
) -> Dict[str, Any]:
    payload = dict(release_review or {})
    defects = payload.get("defects")
    if not isinstance(defects, list):
        defects = []
    revision_instructions = payload.get("revision_instructions")
    if not isinstance(revision_instructions, list):
        revision_instructions = []

    for defect in consistency_report.get("defects") or []:
        if defect not in defects:
            defects.append(defect)
    for instruction in consistency_report.get("revision_instructions") or []:
        if instruction not in revision_instructions:
            revision_instructions.append(instruction)

    payload["defects"] = defects
    payload["revision_instructions"] = revision_instructions
    payload["consistency_report"] = consistency_report
    if not consistency_report.get("pass"):
        payload["pass"] = False
        summary = str(payload.get("release_summary") or "").strip()
        suffix = "Deterministic guardrails detected heading, citation, or numeric drift during final polish."
        payload["release_summary"] = f"{summary} {suffix}".strip() if summary else suffix
    return payload


def _extract_bibtex_keys(text: str) -> List[str]:
    """Extract BibTeX entry keys from raw .bib content blocks."""
    if not text:
        return []
    keys: List[str] = []
    # Match: @article{Key,
    for m in re.finditer(r"@\w+\s*\{\s*([^,\s]+)\s*,", text):
        k = m.group(1).strip()
        if k and k not in keys:
            keys.append(k)
    return keys


def _validate_citations(
    *,
    body_text: str,
    references_text: str,
    bib_keys: List[str],
) -> Dict[str, Any]:
    body_citekeys = _extract_markdown_citekeys(body_text)
    reference_citekeys = _extract_markdown_citekeys(references_text)
    allowed = set(bib_keys)
    unknown_citekeys = [key for key in body_citekeys if key not in allowed]
    missing_in_references = [key for key in body_citekeys if key not in set(reference_citekeys)]
    return {
        "body_citekeys": body_citekeys,
        "reference_citekeys": reference_citekeys,
        "allowed_bib_keys": list(bib_keys),
        "unknown_citekeys": unknown_citekeys,
        "missing_reference_citekeys": missing_in_references,
        "pass": len(unknown_citekeys) == 0 and len(missing_in_references) == 0,
    }


def _render_references_section(citekeys: List[str]) -> str:
    lines = ["## References", ""]
    for k in citekeys:
        lines.append(f"[@{k}]")
    if len(lines) == 2:
        lines.append("Not available")
    lines.append("")
    return "\n".join(lines)


def _normalize_section_key(section: str) -> str:
    """Normalize section name to canonical singular form."""
    mapping = {
        "methods": "method",
        "experiments": "experiment",
        "results": "result",
    }
    key = section.strip().lower()
    return mapping.get(key, key)


def _section_title(section: str) -> str:
    mapping = {
        "method": "Methods",
        "methods": "Methods",
        "experiment": "Experiments",
        "experiments": "Experiments",
        "result": "Results",
        "results": "Results",
        "discussion": "Discussion",
        "conclusion": "Conclusion",
        "references": "References",
        "abstract": "Abstract",
        "introduction": "Introduction",
    }
    return mapping.get(section.lower(), section.title())


def _default_section_list(*, draft_only: bool, review_mode: bool) -> List[str]:
    if draft_only and not review_mode:
        return list(_DEFAULT_LOCAL_DRAFT_SECTIONS)
    return list(_DEFAULT_SECTIONS)


def _infer_section_profile(section_list: Sequence[str]) -> str:
    normalized = [_normalize_section_key(str(section or "").strip()) for section in section_list]
    if "experiment" in normalized:
        return "research"
    return "bio_manuscript"


def _is_placeholder_section_content(section: str, text: str) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return True
    lowered = normalized.lower()
    placeholder_markers = {
        "not available in provided context.",
        "pending final synthesis from the completed result sections above.",
    }
    if lowered in placeholder_markers:
        return True
    if section == "references" and lowered == "% references":
        return True
    return False


def _section_eval_dims(section: str, *, review_mode: bool = False) -> List[str]:
    """Return evaluation dimensions relevant to *section*."""
    key = _normalize_section_key(section)
    if review_mode:
        review_dims = _REVIEW_SECTION_EVAL_DIMS.get(key)
        if review_dims:
            return review_dims
    return _SECTION_EVAL_DIMS.get(key, _ALL_EVAL_DIMENSIONS)


def _section_requirements(section: str, *, review_mode: bool = False) -> List[str]:
    section = _normalize_section_key(section)
    if section == "abstract":
        if review_mode:
            return [
                "Cover six elements in one coherent abstract: background, review scope/objective, evidence base or search approach, major findings/themes, limitations, and conclusion.",
                "State whether the evidence base includes full-text studies versus abstract-only studies when that distinction materially affects confidence.",
                "State representative quantitative findings with units when available; if the source literature is mainly qualitative, say so explicitly.",
                "Do not collapse the abstract into a generic conclusion-only paragraph.",
            ]
        return [
            "Provide background, objective, methods, key results, and conclusion.",
            "State representative numeric findings with units when available; if the source literature is qualitative, say so explicitly.",
            "Keep concise but substantive.",
        ]
    if section == "introduction":
        return [
            "Explain the scientific motivation and gap.",
            "Summarize relevant prior work only from provided context.",
            (
                "State review objectives and research questions."
                if review_mode
                else "State study objectives and hypotheses."
            ),
        ]
    if section == "method":
        if review_mode:
            return [
                "Describe the literature search and evidence synthesis workflow.",
                "State databases, search scope, study selection criteria, and evidence extraction approach when available.",
                "Be explicit about which methodological details were available versus missing in the source studies.",
                "Do not invent statistical procedures, thresholds, or software versions that were not reported by the source literature.",
                "Reference the evidence coverage constraints when they materially limit certainty.",
            ]
        return [
            "Provide full data processing steps and parameter settings.",
            "Describe statistical tests and model configurations.",
            "Include QC thresholds, inclusion/exclusion criteria, and software versions if provided.",
        ]
    if section == "experiment":
        if review_mode:
            return [
                "Present a comparative synthesis of representative experimental systems, cohorts, assays, or study designs from the cited literature.",
                "Summarize controls, endpoints, and measurements reported across studies when available.",
                "Report representative quantitative findings with units when available, but do not invent p-values, effect sizes, or uncertainty when the source studies do not provide them.",
                "Clearly distinguish extracted evidence from the review's synthesis or interpretation.",
                "If original figures/tables are unavailable, describe the proposed figure or table content instead of pretending direct figure assets exist.",
                "Use citations to anchor comparisons across at least two included studies.",
            ]
        return [
            "Detail experimental setup, datasets, and analysis workflow.",
            "Specify sample groups, inclusion/exclusion criteria, and preprocessing steps.",
            "Explain evaluation protocol, metrics, and statistical tests used.",
            "Describe figure/table construction and what each figure demonstrates.",
            "List controls, baselines, ablations, and validation steps.",
            "Report key numeric results with units and uncertainty where available.",
        ]
    if section == "result":
        if review_mode:
            return [
                "Synthesize the major findings across studies and explain points of agreement, disagreement, or heterogeneity.",
                "Report representative quantitative findings with units when available; if the literature is mainly qualitative, state that clearly.",
                "Ground claims in cited evidence and describe any proposed tables/figures when direct figure assets are unavailable.",
                "Do not present this section as newly generated original experimental data.",
                "Use citations to anchor comparative claims instead of making uncited narrative assertions.",
            ]
        return [
            "Interpret quantitative results and link to figures/tables.",
            "Discuss effect sizes, significance, and practical implications.",
            "Avoid vague statements; ground claims in data.",
        ]
    if section == "discussion":
        if review_mode:
            return [
                "Interpret the synthesis rather than repeating the results section.",
                "Compare with prior studies and highlight translational implications, uncertainties, and alternative interpretations.",
                "Discuss limitations, evidence gaps, and future work.",
                "Ground synthesis claims in cited evidence from the included studies.",
            ]
        return [
            "Contextualize findings within existing literature.",
            "Compare with prior studies and highlight novelty.",
            "Discuss limitations, alternative interpretations, and future work.",
            "Ground claims in data from the results section.",
        ]
    if section == "conclusion":
        if review_mode:
            return [
                "Summarize the review's main takeaways without introducing new evidence.",
                "State the main translational implication and the most important unresolved limitation.",
                "Ground the conclusion in the synthesized evidence base.",
            ]
        return [
            "Summarize key contributions and findings.",
            "Discuss limitations and future work.",
            "Avoid introducing new results.",
        ]
    if section == "references":
        return [
            "List references ONLY from the provided reference library/context (e.g. references.bib or evidence.md).",
            "Use the provided BibTeX citekeys (Markdown citekeys: [@citekey]) and do NOT invent new citekeys.",
            "Do not fabricate external references beyond the provided library.",
        ]
    return ["Follow scientific writing standards for this section."]


def _exemplar_style_enabled() -> bool:
    """When True, inject Nature-tier exemplar hints (see docs/writing_exemplars/nature_exemplars.md)."""
    raw = os.getenv("MANUSCRIPT_EXEMPLAR_STYLE_ENABLED", "1")
    return str(raw).strip().lower() not in {"0", "false", "no", "off"}


def _exemplar_style_instructions(section: str, *, review_mode: bool) -> str:
    """Short style guidance aligned with curated Nature exemplars; empty when disabled."""
    if not _exemplar_style_enabled():
        return ""
    s = _normalize_section_key(section)
    r1 = (
        "R1: Nature Reviews MCB (single-cell multi-omics landscape, 2023)—taxonomy of approaches, "
        "thematic subsections, roadmap-style organization, limitations/outlook, disciplined citation density."
    )
    r2 = (
        "R2: Nature AlphaFold (2021)—tight problem–method–headline result; benchmark-style evaluation narrative."
    )
    r3 = (
        "R3: Nature NK cell therapies (2023)—long-form review arc (biology → modalities → analysis); "
        "clinical translational framing."
    )
    lines = [
        "Style exemplars (structure and tone only; do NOT copy wording; ground claims only in provided context; "
        "see docs/writing_exemplars/nature_exemplars.md):",
    ]
    if s == "abstract":
        if review_mode:
            lines.append(f"- Primary: {r3}")
            lines.append(f"- Secondary: {r2} (keep the abstract compact and evidence-grounded).")
        else:
            lines.append(f"- Primary: {r2}")
            lines.append(f"- Secondary: {r3}.")
    elif s == "introduction":
        lines.append(f"- Primary: {r1}")
        lines.append(f"- Secondary: {r3}.")
    elif s == "method":
        # Review/synthesis: weight R1+R3 per docs/writing_exemplars/nature_exemplars.md (R2 mainly abstract).
        if review_mode:
            lines.append(f"- Primary: {r1} (review: search/inclusion/synthesis methodology; classify/compare routes).")
            lines.append(f"- Secondary: {r3} (translational or clinical framing where evidence supports).")
        else:
            lines.append(f"- Primary: {r2}")
            lines.append(f"- Secondary: {r1} (when describing methodology families and comparisons).")
    elif s == "experiment":
        if review_mode:
            lines.append(f"- Primary: {r3} (patterns across studies, modalities, and clinical contexts).")
            lines.append(f"- Secondary: {r1} (thematic synthesis of study designs; link to Methods).")
        else:
            lines.append(f"- Primary: {r2}")
            lines.append(
                "- Frame datasets, controls, ablations, and statistics with reproducible detail."
            )
    elif s == "result":
        if review_mode:
            lines.append(f"- Primary: {r1} (thematic subsections; evidence-driven synthesis, not adjectives).")
            lines.append(f"- Secondary: {r3} (cross-study comparison and translational reading).")
            lines.append(
                f"- Optional compactness: {r2} (headline quantitative claims only when cited evidence supports)."
            )
        else:
            lines.append(f"- Primary: {r2}")
            lines.append(f"- Secondary: {r1} (subsections driven by evidence themes).")
    elif s == "discussion":
        lines.append(f"- Primary: {r1}")
        lines.append(f"- Secondary: {r3}.")
    elif s == "conclusion":
        lines.append(f"- Primary: {r1}")
        lines.append(f"- Secondary: {r2} (close with open problems without adding new claims).")
    elif s == "references":
        lines.append(f"- Primary: {r1} (review-style citation discipline; pair claims with sources).")
    else:
        lines.append(f"- Primary: {r1}")
        lines.append(f"- Secondary: {r2}.")
    return "\n".join(lines) + "\n"


def _merge_and_polish_exemplar_hint() -> str:
    if not _exemplar_style_enabled():
        return ""
    return (
        "Optional style target: improve cohesion and transitions toward Nature-tier review/article clarity "
        "(see docs/writing_exemplars/nature_exemplars.md); do NOT add new facts or citations.\n\n"
    )


def _build_analysis_prompt(task: str, context_text: str, sections: List[str]) -> str:
    return (
        "You are preparing a scientific manuscript draft. "
        "First, produce an ANALYSIS MEMO in Markdown.\n\n"
        "Requirements for the memo:\n"
        "1) Evidence inventory: list datasets, sample groups, file paths, and key variables.\n"
        "2) Key numeric results: metrics, effect sizes, p-values, and units.\n"
        "3) Figure/Table mapping: for each figure path, state what it shows and the main takeaway.\n"
        "4) Method details checklist: data processing, stats tests, model settings, QC thresholds.\n"
        "5) Limitations and uncertainties grounded in the provided data.\n"
        f"6) Section outline: {', '.join(_section_title(s) for s in sections)}.\n\n"
        "Rules:\n"
        "- Use only provided context. If data is missing, say 'Not available'.\n"
        "- Do NOT fabricate citations or results.\n"
        "- Prefer cross-study synthesis over per-paper narration when the task is a review article.\n"
        "- Be concise but information-dense.\n\n"
        f"User request:\n{task}\n\n"
        f"Context:\n{context_text or '[No context provided]'}\n\n"
        "Return ONLY the analysis memo in Markdown."
    )


def _build_section_prompt(
    task: str,
    section: str,
    analysis_memo: str,
    context_text: str,
    requirements: List[str],
    *,
    review_mode: bool = False,
) -> str:
    section_title = _section_title(section)
    requirements_text = "\n".join(f"- {item}" for item in requirements)
    mode_text = (
        "- This manuscript is a review/synthesis article, not an original experimental report.\n"
        if review_mode
        else ""
    )
    exemplar_prefix = _exemplar_style_instructions(section, review_mode=review_mode)
    return (
        f"Write the section: {section_title}\n\n"
        f"{exemplar_prefix}"
        "Requirements:\n"
        f"{requirements_text}\n\n"
        "Writing rules:\n"
        f"{mode_text}"
        "- Use cohesive expert academic prose (avoid short fragmented sentences).\n"
        "- Ground all claims in the provided analysis memo and context.\n"
        "- Do NOT fabricate citations or results.\n"
        "- Prefer explicit evidence-linked synthesis over generic academic filler.\n"
        "- Include the section heading (Markdown '##').\n\n"
        f"User request:\n{task}\n\n"
        f"Analysis memo:\n{analysis_memo}\n\n"
        f"Context:\n{context_text or '[No context provided]'}\n\n"
        "Return ONLY the section in Markdown."
    )


def _build_evaluation_prompt(
    section: str,
    analysis_memo: str,
    section_text: str,
    requirements: List[str],
    *,
    review_mode: bool = False,
) -> str:
    section_title = _section_title(section)
    requirements_text = "\n".join(f"- {item}" for item in requirements)
    dims = _section_eval_dims(section, review_mode=review_mode)
    dims_text = "\n".join(f"- {d}" for d in dims)
    scores_example = ", ".join(f'"{d}": 0.0' for d in dims)
    review_rules = (
        "- This manuscript is a review/synthesis article, not an original experimental report.\n"
        "- Do not penalize the section merely because primary studies omit p-values, effect sizes, or uncertainty, as long as the section transparently states those limitations.\n"
        "- For review manuscripts, score `results_analysis` based on cross-study synthesis quality, evidence linkage, and explicit handling of missing quantitative data.\n"
        "- For review manuscripts, score `scientific_rigor` based on faithful reporting, transparency about evidence limitations, and sound synthesis rather than the presence of newly generated data.\n"
        "- For review manuscripts, score `evidence_linkage` based on whether comparative claims and synthesis statements are backed by cited included studies.\n"
        "- For review manuscripts, score `evidence_coverage` based on whether the section cites a broad enough slice of the available section-relevant evidence, prioritizes full-text studies when available, and uses quantitative studies when making numeric claims.\n"
        if review_mode
        else ""
    )
    return (
        "You are a strict scientific writing reviewer. "
        "Evaluate the following section and return JSON ONLY.\n\n"
        f"Section: {section_title}\n\n"
        "Section requirements:\n"
        f"{requirements_text}\n\n"
        "Evaluation dimensions (score each 0.0 to 1.0):\n"
        f"{dims_text}\n\n"
        "Return JSON with this schema:\n"
        "{\n"
        f'  "scores": {{ {scores_example} }},\n'
        '  "defects": ["..."],\n'
        '  "revision_instructions": ["..."],\n'
        '  "pass": true\n'
        "}\n\n"
        "Rules:\n"
        f"{review_rules}"
        "- If any requirement is missing or weak, include it in defects.\n"
        "- If citations are fabricated or unsupported, set pass=false.\n"
        "- Keep defects and revision instructions concise and specific.\n\n"
        f"Analysis memo:\n{analysis_memo}\n\n"
        f"Section text:\n{section_text}\n\n"
        "Return JSON ONLY."
    )


def _build_revision_prompt(
    section: str,
    analysis_memo: str,
    context_text: str,
    section_text: str,
    evaluation: Dict[str, Any],
    requirements: List[str],
    *,
    review_mode: bool = False,
) -> str:
    section_title = _section_title(section)
    requirements_text = "\n".join(f"- {item}" for item in requirements)
    evaluation_json = json.dumps(evaluation, ensure_ascii=True, indent=2)
    mode_text = (
        "This manuscript is a review/synthesis article, not an original experimental report. "
        "Preserve explicit statements about missing quantitative evidence when the source literature does not provide it.\n\n"
        if review_mode
        else ""
    )
    exemplar_prefix = _exemplar_style_instructions(section, review_mode=review_mode)
    return (
        f"Revise the section: {section_title}\n\n"
        f"{exemplar_prefix}"
        "Section requirements:\n"
        f"{requirements_text}\n\n"
        f"{mode_text}"
        "Use the evaluation feedback to improve the section. "
        "Address all defects and follow revision instructions. "
        "Do NOT fabricate citations or results.\n\n"
        f"Evaluation JSON:\n{evaluation_json}\n\n"
        f"Analysis memo:\n{analysis_memo}\n\n"
        f"Context:\n{context_text or '[No context provided]'}\n\n"
        f"Current section:\n{section_text}\n\n"
        "Return ONLY the revised section in Markdown (include the '##' heading)."
    )


def _build_merge_prompt(
    task: str,
    analysis_memo: str,
    combined_text: str,
) -> str:
    polish_hint = _merge_and_polish_exemplar_hint()
    return (
        "You are finalizing a scientific manuscript. "
        "Perform a global rewrite to ensure consistency, remove repetition, "
        "and improve transitions while preserving content.\n\n"
        "Rules:\n"
        f"{polish_hint}"
        "- Keep section headings and order intact.\n"
        "- Do NOT add new facts or citations not supported by the input.\n"
        "- Ensure Methods and Experiments remain detailed.\n"
        "- Ensure Results contain interpretation and link to figures/tables.\n\n"
        f"User request:\n{task}\n\n"
        f"Analysis memo:\n{analysis_memo}\n\n"
        f"Combined draft:\n{combined_text}\n\n"
        "Return ONLY the final manuscript in Markdown."
    )


def _demote_markdown_headings(text: str, *, levels: int = 1) -> str:
    if levels <= 0:
        return str(text or "").strip()

    def _replace(match: re.Match[str]) -> str:
        hashes = match.group(1)
        suffix = match.group(2)
        return f"{'#' * min(6, len(hashes) + levels)}{suffix}"

    return re.sub(r"^(#{1,6})(\s.*)$", _replace, str(text or "").strip(), flags=re.MULTILINE)


def _local_draft_bucket(path: str) -> Optional[str]:
    normalized = str(path or "").strip().replace("\\", "/").lower()
    if not normalized:
        return None
    if "/manuscript/results/" in normalized or normalized.startswith("manuscript/results/"):
        return "result"
    if (
        "/methods/" in normalized
        or normalized.startswith("methods/")
        or normalized.endswith("data_source_preprocessing.md")
    ):
        return "method"
    if normalized.endswith("_summary.md") or normalized.endswith("_summary.txt"):
        return "supplementary"
    return None


def _local_draft_sort_key(bucket: str, path: str) -> Tuple[int, str]:
    normalized = str(path or "").strip().replace("\\", "/").lower()
    if bucket == "method":
        for idx, token in enumerate(
            (
                "data_source_preprocessing",
                "clustering_annotation",
                "differential_enrichment",
                "cell_communication",
            )
        ):
            if token in normalized:
                return idx, normalized
        return 99, normalized
    if bucket == "result":
        match = re.search(r"5\.1\.3\.(\d+)", normalized)
        if match:
            return int(match.group(1)), normalized
        return 99, normalized
    return 99, normalized


def _assemble_local_draft_from_context(
    *,
    task: str,
    context_paths: List[str],
    max_context_bytes: int,
    section_list: List[str],
) -> Tuple[str, str, List[str], Dict[str, int], Dict[str, str]]:
    grouped: Dict[str, List[Tuple[Tuple[int, str], str, str]]] = {
        "method": [],
        "result": [],
        "supplementary": [],
    }
    used_sources: List[str] = []

    for raw in context_paths:
        value = str(raw or "").strip()
        if not value:
            continue
        try:
            path = _resolve_project_path(value)
        except Exception:
            continue
        if path.suffix.lower() not in {".md", ".txt"}:
            continue
        bucket = _local_draft_bucket(str(path.relative_to(_PROJECT_ROOT)))
        if bucket is None:
            continue
        try:
            content = _read_text_file(path, max_context_bytes).strip()
        except Exception:
            continue
        if not content:
            continue
        rel = str(path.relative_to(_PROJECT_ROOT))
        grouped[bucket].append((_local_draft_sort_key(bucket, rel), rel, content))
        used_sources.append(rel)

    for bucket in grouped:
        grouped[bucket].sort(key=lambda item: item[0])

    section_counts = {bucket: len(items) for bucket, items in grouped.items()}

    def _render_group(items: List[Tuple[Tuple[int, str], str, str]], *, fallback: str) -> str:
        if not items:
            return fallback
        rendered: List[str] = []
        for _sort_key, rel, content in items:
            block = _demote_markdown_headings(content, levels=1)
            if not block.startswith("#"):
                title = Path(rel).stem.replace("_", " ").strip() or rel
                block = f"### {title}\n\n{block}"
            rendered.append(block)
        return "\n\n".join(rendered).strip()

    parts: List[str] = [
        "# Manuscript Draft",
        "",
        "> Auto-assembled locally from completed task outputs without additional literature review or re-analysis.",
        "",
    ]
    ordered_sections = section_list or list(_DEFAULT_SECTIONS)
    section_text_map: Dict[str, str] = {}
    for section in ordered_sections:
        key = _normalize_section_key(section)
        title = _section_title(key)
        parts.extend([f"## {title}", ""])
        section_body = ""
        if key == "abstract":
            section_body = "Not available in provided context."
        elif key == "introduction":
            section_body = "Not available in provided context."
        elif key == "method":
            section_body = _render_group(
                grouped["method"],
                fallback="Not available in provided context.",
            )
        elif key == "experiment":
            section_body = "Not available in provided context."
        elif key == "result":
            section_body = _render_group(
                grouped["result"],
                fallback="Not available in provided context.",
            )
        elif key == "discussion":
            section_body = _render_group(
                grouped["supplementary"],
                fallback="Pending final synthesis from the completed result sections above.",
            )
        elif key == "conclusion":
            section_body = "Pending final synthesis from the completed result sections above."
        elif key == "references":
            section_body = "Not available in provided context."
        else:
            section_body = "Not available in provided context."
        section_text_map[key] = str(section_body).strip()
        parts.append(section_body)
        parts.extend(["", ""])

    analysis_lines = [
        "# Analysis Memo",
        "",
        "- mode: local_draft_assembly",
        f"- task: {task}",
        f"- source_files_used: {len(used_sources)}",
        f"- method_sources: {section_counts['method']}",
        f"- result_sources: {section_counts['result']}",
        f"- supplementary_sources: {section_counts['supplementary']}",
        "",
        "## Included source files",
        "",
    ]
    if used_sources:
        analysis_lines.extend(f"- {path}" for path in used_sources)
    else:
        analysis_lines.append("- None")
    analysis_lines.extend(["", "## Notes", "", "- This draft was assembled locally from existing Markdown outputs.", "- Missing sections remain explicitly marked as not available.", ""])

    return (
        "\n".join(parts).strip() + "\n",
        "\n".join(analysis_lines).strip() + "\n",
        used_sources,
        section_counts,
        section_text_map,
    )


def _build_final_polish_prompt(
    task: str,
    analysis_memo: str,
    manuscript_text: str,
    *,
    review_mode: bool = False,
) -> str:
    review_rule = (
        "- This is a review/synthesis article. Preserve explicit statements about missing quantitative evidence.\n"
        if review_mode
        else ""
    )
    polish_hint = _merge_and_polish_exemplar_hint()
    return (
        "You are the final manuscript editor for a publication-quality scientific paper. "
        "Perform a conservative final polish.\n\n"
        f"{polish_hint}"
        "Allowed edits:\n"
        "- Remove repeated or near-duplicate sentences/paragraphs.\n"
        "- Tighten wording and improve readability.\n"
        "- Smooth transitions between sections.\n"
        "- Standardize terminology and style.\n"
        "- Fix obvious Markdown/LaTeX-adjacent formatting issues without changing meaning.\n"
        "- Preserve citations and section order.\n\n"
        "Forbidden edits:\n"
        "- Do NOT add new facts, citations, numbers, or claims.\n"
        "- Do NOT change the scientific conclusion.\n"
        "- Do NOT remove explicit uncertainty statements unless they are duplicated.\n\n"
        f"{review_rule}"
        f"User request:\n{task}\n\n"
        f"Analysis memo:\n{analysis_memo}\n\n"
        f"Manuscript draft:\n{manuscript_text}\n\n"
        "Return ONLY the polished manuscript in Markdown."
    )


def _build_final_polish_revision_prompt(
    task: str,
    analysis_memo: str,
    manuscript_text: str,
    evaluation: Dict[str, Any],
    *,
    review_mode: bool = False,
) -> str:
    evaluation_json = json.dumps(evaluation, ensure_ascii=True, indent=2)
    review_rule = (
        "This is a review/synthesis article. Preserve explicit statements about missing quantitative evidence.\n\n"
        if review_mode
        else ""
    )
    return (
        "You are revising a manuscript after a final publication-readiness review.\n\n"
        "Goal:\n"
        "- Improve readability, cohesion, duplication control, citation hygiene, and formatting polish.\n"
        "- Keep all facts, citations, and conclusions unchanged.\n\n"
        "Rules:\n"
        f"{review_rule}"
        "- Do NOT add new facts, citations, or quantitative claims.\n"
        "- Do NOT remove section headings or reorder sections.\n"
        "- Use the review feedback precisely and conservatively.\n\n"
        f"User request:\n{task}\n\n"
        f"Analysis memo:\n{analysis_memo}\n\n"
        f"Release review JSON:\n{evaluation_json}\n\n"
        f"Current polished draft:\n{manuscript_text}\n\n"
        "Return ONLY the revised polished manuscript in Markdown."
    )


def _build_release_review_prompt(
    task: str,
    analysis_memo: str,
    manuscript_text: str,
    *,
    review_mode: bool = False,
) -> str:
    scores_example = ", ".join(f'"{name}": 0.0' for name in _FINAL_POLISH_EVAL_DIMS)
    review_rule = (
        "- This is a review/synthesis article. Do not penalize transparent statements that some primary studies did not report quantitative metrics.\n"
        if review_mode
        else ""
    )
    return (
        "You are the final release gate reviewer for a scientific manuscript. "
        "Evaluate whether this manuscript is safe to expose to an end user as a polished final draft. "
        "Return JSON ONLY.\n\n"
        "Evaluation dimensions (score each 0.0 to 1.0):\n"
        "- deduplication\n"
        "- readability\n"
        "- section_cohesion\n"
        "- citation_integrity\n"
        "- format_integrity\n"
        "- factual_faithfulness\n\n"
        "Return JSON with this schema:\n"
        "{\n"
        f'  "scores": {{ {scores_example} }},\n'
        '  "defects": ["..."],\n'
        '  "revision_instructions": ["..."],\n'
        '  "release_summary": "one short sentence suitable for users",\n'
        '  "pass": true\n'
        "}\n\n"
        "Rules:\n"
        f"{review_rule}"
        "- Fail the manuscript if repeated passages remain, if transitions are still rough, or if formatting/citation problems make the draft look unpublishable.\n"
        "- Fail the manuscript if the text appears to introduce unsupported facts or altered claims.\n"
        "- The release_summary must not quote large passages from the manuscript.\n\n"
        f"User request:\n{task}\n\n"
        f"Analysis memo:\n{analysis_memo}\n\n"
        f"Polished manuscript candidate:\n{manuscript_text}\n\n"
        "Return JSON ONLY."
    )


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
        strict_gate = _env_enabled("MANUSCRIPT_STRICT_GATE", True)
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
            # Legacy: use session_dir for work_dir
            work_base = session_dir or _RUNTIME_DIR / "_manuscript_scratch"
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
        review_evidence = (
            _load_review_evidence(
                context_paths=context_paths,
                merge_dir=merge_dir,
                max_context_bytes=max_context_bytes,
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
                eval_raw = await _chat(eval_llm, eval_prompt, eval_model)
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
                text = await _chat(gen_llm, revision_prompt, gen_model)

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

        if review_mode:
            coverage_report = review_evidence.get("coverage_report") if isinstance(review_evidence, dict) else {}
            coverage_payload = coverage_report if isinstance(coverage_report, dict) else {}
            if not coverage_payload.get("pass"):
                return _build_failure_payload(
                    error_code="low_evidence_coverage",
                    manifest_path=manifest_path,
                    combined_partial="",
                    release_summary=_release_summary_for_error(
                        "low_evidence_coverage",
                        evaluation={"coverage_summary": coverage_payload.get("summary")},
                    ),
                )

        if draft_only:
            draft_text, analysis_memo, used_sources, section_counts, section_text_map = _assemble_local_draft_from_context(
                task=task,
                context_paths=context_paths,
                max_context_bytes=max_context_bytes,
                section_list=section_list,
            )
            analysis_file.write_text(analysis_memo, encoding="utf-8")
            output_file.write_text(draft_text, encoding="utf-8")
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
        analysis_memo = await _chat(gen_llm, analysis_prompt, gen_model)
        analysis_file.write_text(analysis_memo, encoding="utf-8")

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
            for res in parallel_results:
                if isinstance(res, Exception):
                    logger.error("Section generation failed: %s", res)
                    failed_sections.append("unknown")
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
                missing = [k for k in cited if k not in allowed]
                used = [k for k in cited if k in allowed]
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
            if "references" not in failed_sections:
                failed_sections.append("references")
            return _build_failure_payload(
                error_code="citation_validation_failed",
                manifest_path=manifest_path,
                combined_partial=combined_text,
                citation_validation_path=citation_validation_path,
                citation_report=citation_report,
            )

        if review_mode and "abstract" in section_text_map:
            abstract_contract = _validate_review_abstract_contract(section_text_map.get("abstract", ""))
            abstract_contract_path = reviews_dir / "abstract_contract.json"
            abstract_contract_path.write_text(
                json.dumps(abstract_contract, ensure_ascii=True, indent=2),
                encoding="utf-8",
            )
            if not abstract_contract.get("pass"):
                if "abstract" not in failed_sections:
                    failed_sections.append("abstract")
                return _build_failure_payload(
                    error_code="abstract_incomplete",
                    manifest_path=manifest_path,
                    combined_partial=combined_text,
                    citation_validation_path=citation_validation_path,
                    citation_report=citation_report,
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
            return await _chat(merge_llm, prompt, merge_model_name)

        # Run transition smoothing in parallel for all adjacent pairs
        if len(ordered_sections) >= 2:
            pairs = list(zip(ordered_sections[:-1], ordered_sections[1:]))
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

        quality_gate_passed = len(failed_sections) == 0 and bool(citation_report.get("pass"))
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
                        _chat(final_polish_merge_llm, polish_prompt, merge_model_name),
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
                        _chat(final_polish_eval_llm, review_prompt, eval_model),
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

            if not polish_gate_passed:
                public_release_ready = False
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
        else:
            polished_workspace_path.write_text(polished_text, encoding="utf-8")

        output_file.write_text(polished_text, encoding="utf-8")

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
