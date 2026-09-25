"""Review-evidence chain: review-pack discovery, coverage gate, diagnostics.

Self-contained cluster: it loads study_cards/coverage_report/evidence.md, scores
per-section evidence coverage, and folds the result into a section evaluation.
Path reads go through ``paths`` helpers, which in turn honour the
monkeypatched ``_PROJECT_ROOT`` on the package facade.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .config import (
    _DEFAULT_COVERAGE_THRESHOLDS,
    _EVIDENCE_MD_MAX_BYTES,
    _REVIEW_SECTION_COVERAGE_PASS_THRESHOLD,
    _REVIEW_SECTION_COVERAGE_TARGETS,
)
from .paths import _read_text_file, _resolve_project_path
from .rubrics import (
    _extract_markdown_citekeys,
    _extract_numeric_tokens,
    _normalize_section_key,
)

logger = logging.getLogger(__name__)


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


def _discover_sibling_context_path(
    context_paths: Iterable[str],
    target_name: str,
) -> Optional[str]:
    existing = _first_context_path(context_paths, target_name)
    if existing:
        return existing
    for raw in context_paths:
        value = str(raw or "").strip()
        if not value:
            continue
        try:
            candidate = Path(value).parent / target_name
            if candidate.is_file():
                return str(candidate)
        except Exception:
            continue
    return None


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


def _coverage_thresholds() -> Dict[str, int]:
    """Evidence-coverage gate thresholds, overridable via env for lighter editorial tasks."""
    def _int(name: str, default: int) -> int:
        try:
            return int(os.getenv(name, str(default)))
        except (TypeError, ValueError):
            return default

    return {
        "min_total_studies": max(0, _int("MANUSCRIPT_MIN_TOTAL_STUDIES", _DEFAULT_COVERAGE_THRESHOLDS["min_total_studies"])),
        "min_full_text_studies": max(0, _int("MANUSCRIPT_MIN_FULL_TEXT_STUDIES", _DEFAULT_COVERAGE_THRESHOLDS["min_full_text_studies"])),
        "min_quantitative_studies": max(0, _int("MANUSCRIPT_MIN_QUANTITATIVE_STUDIES", _DEFAULT_COVERAGE_THRESHOLDS["min_quantitative_studies"])),
        "min_support_per_core_section": max(0, _int("MANUSCRIPT_MIN_SUPPORT_PER_CORE_SECTION", _DEFAULT_COVERAGE_THRESHOLDS["min_support_per_core_section"])),
    }


def _evaluate_coverage(counts: Dict[str, Any], section_support_counts: Dict[str, Any], thresholds: Dict[str, int]) -> List[str]:
    failures: List[str] = []
    total = int(counts.get("total_studies") or 0)
    full_text = int(counts.get("full_text_studies") or 0)
    quantitative = int(counts.get("quantitative_studies") or 0)
    if total < thresholds["min_total_studies"]:
        failures.append(f"only {total} included studies; require at least {thresholds['min_total_studies']}")
    if full_text < thresholds["min_full_text_studies"]:
        failures.append(f"only {full_text} full-text studies; require at least {thresholds['min_full_text_studies']}")
    if quantitative < thresholds["min_quantitative_studies"]:
        failures.append(
            f"only {quantitative} studies with quantitative findings; require at least {thresholds['min_quantitative_studies']}"
        )
    for section, support_count in (section_support_counts or {}).items():
        if int(support_count or 0) < thresholds["min_support_per_core_section"]:
            failures.append(
                f"{section} is supported by only {support_count} studies; require at least {thresholds['min_support_per_core_section']}"
            )
    return failures


def _reevaluate_coverage_report(report: Dict[str, Any], thresholds: Dict[str, int]) -> Dict[str, Any]:
    """Re-check a stored coverage report against the currently configured thresholds.

    Review packs produced under older (stricter) settings carry ``pass: false``
    baked into their coverage_report.json; without this re-check a relaxed env
    configuration would never unblock them.
    """
    if not isinstance(report, dict) or report.get("pass"):
        return report
    counts = report.get("counts") if isinstance(report.get("counts"), dict) else {}
    if not counts:
        return report
    section_support_counts = report.get("section_support_counts") if isinstance(report.get("section_support_counts"), dict) else {}
    failures = _evaluate_coverage(counts, section_support_counts, thresholds)
    updated = dict(report)
    updated["thresholds"] = dict(thresholds)
    if failures:
        updated["pass"] = False
        updated["failures"] = failures
        updated["summary"] = "Evidence coverage blocked: " + "; ".join(failures)
    else:
        updated["pass"] = True
        updated["failures"] = []
        updated["env_relaxed"] = True
        updated["summary"] = (
            "Evidence coverage passed under relaxed thresholds: "
            f"{int(counts.get('total_studies') or 0)} studies included, "
            f"{int(counts.get('full_text_studies') or 0)} full-text studies."
        )
        logger.warning(
            "manuscript_writer: coverage report re-evaluated as PASS under env thresholds %s (was blocked under stricter settings)",
            thresholds,
        )
    return updated


def _discover_latest_review_pack_file(session_dir: Path, filename: str) -> Optional[str]:
    """Find *filename* inside the most recent literature_pipeline review pack of this session.

    Later turns of a session often re-run manuscript_writer without re-wiring
    the context paths of the original literature_pipeline run; this keeps the
    evidence pack attached instead of failing with a missing study_cards error.
    """
    try:
        pack_root = session_dir / "tool_outputs" / "literature_pipeline"
        candidates = [
            p for p in pack_root.glob(f"review_pack_*/{filename}")
            if p.is_file()
        ]
        if not candidates:
            return None
        latest = max(candidates, key=lambda p: p.stat().st_mtime)
        return str(latest)
    except Exception:
        return None


def _load_review_evidence(
    *,
    context_paths: List[str],
    merge_dir: Path,
    max_context_bytes: int,
    session_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    thresholds = _coverage_thresholds()
    study_cards_path = _first_context_path(context_paths, "study_cards.jsonl")
    if not study_cards_path:
        study_cards_path = _discover_sibling_context_path(context_paths, "study_cards.jsonl")
    review_pack_dir: Optional[Path] = None
    if study_cards_path:
        try:
            parent = _resolve_project_path(study_cards_path).parent
            if parent.name.startswith("review_pack_"):
                review_pack_dir = parent
        except Exception:
            review_pack_dir = None
    elif session_dir is not None:
        discovered = _discover_latest_review_pack_file(session_dir, "study_cards.jsonl")
        if discovered:
            study_cards_path = discovered
            review_pack_dir = Path(discovered).parent
            logger.warning(
                "manuscript_writer: context_paths carried no study_cards.jsonl; "
                "fell back to session review pack %s",
                discovered,
            )
    coverage_report_path = _first_context_path(context_paths, "coverage_report.json")
    if not coverage_report_path:
        coverage_report_path = _discover_sibling_context_path(context_paths, "coverage_report.json")
    if not coverage_report_path and review_pack_dir is not None:
        candidate = review_pack_dir / "coverage_report.json"
        if candidate.is_file():
            coverage_report_path = str(candidate)
    evidence_md_path = _first_context_path(context_paths, "evidence.md")
    if not evidence_md_path:
        evidence_md_path = _discover_sibling_context_path(context_paths, "evidence.md")
    if not evidence_md_path and review_pack_dir is not None:
        candidate = review_pack_dir / "evidence.md"
        if candidate.is_file():
            evidence_md_path = str(candidate)
    reference_library_path = _first_context_path(context_paths, ".bib")
    if not reference_library_path and review_pack_dir is not None:
        candidate = review_pack_dir / "references.bib"
        if candidate.is_file():
            reference_library_path = str(candidate)

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
            evidence_md_text = _read_text_file(
                _resolve_project_path(evidence_md_path), min(max_context_bytes, _EVIDENCE_MD_MAX_BYTES)
            )
        except Exception:
            evidence_md_text = ""

    if coverage_report is None and study_cards:
        counts = {
            "total_studies": len(study_cards),
            "full_text_studies": len([card for card in study_cards if card.get("evidence_tier") == "full_text"]),
            "quantitative_studies": len([card for card in study_cards if card.get("quantitative_findings")]),
        }
        section_support_counts = {
            section: len([card for card in study_cards if section in (card.get("section_support") or [])])
            for section in ("introduction", "method", "experiment", "result", "discussion", "conclusion")
        }
        failures = _evaluate_coverage(counts, section_support_counts, thresholds)
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
    elif coverage_report is not None and study_cards:
        # Re-evaluate only when the cards actually loaded; an unreadable
        # study_cards.jsonl must never pass the gate on stale counts.
        coverage_report = _reevaluate_coverage_report(coverage_report, thresholds)

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
            "thresholds": thresholds,
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
    # Bilingual slot detection: pipelines may produce English or Chinese
    # abstracts (e.g. 中文结构式摘要 with 【目的】【方法】【结果】【结论】).
    slots = {
        "background": any(token in normalized for token in ("antimicrobial", "infection", "pathogen", "therapy", "pseudomonas", "目的", "背景", "探讨")),
        "scope": any(token in normalized for token in ("this review", "we review", "we synthesize", "scope", "review synthesizes", "综述", "本文", "梳理", "回顾")),
        "evidence_base_method": any(token in normalized for token in ("literature", "studies", "evidence", "search", "reviewed", "检索", "文献", "纳入", "方法")),
        "major_findings": any(token in normalized for token in ("key finding", "collectively", "recent studies", "major", "findings", "结果", "发现", "显示", "报道")),
        "limitations": any(token in normalized for token in ("limitation", "heterogeneity", "not available", "limited", "however", "局限", "不足", "有限", "然而")),
        "conclusion": any(token in normalized for token in ("overall", "together", "support", "suggest", "conclude", "promise", "结论", "提示", "建议", "表明")),
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
    available_for_section = int(coverage.get("available_supported_studies") or 0)
    # Evidence counts are advisory: only demand linkage to studies that actually
    # exist for this section. A thin evidence base can never fail a section on
    # its own; ignoring the studies that DO exist still can.
    target_linked = min(min_linked_citations, available_for_section)
    passes_linkage = linked_count >= target_linked
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

    scores["evidence_linkage"] = 1.0 if passes_linkage else 0.0
    if available_for_section > 0:
        scores["evidence_coverage"] = float(coverage.get("score") or 0.0)
    else:
        # No section-relevant studies exist to cite: hold the section harmless
        # instead of sinking the weighted score for missing literature.
        scores["evidence_coverage"] = 1.0
    evaluation_data["review_evidence_coverage"] = coverage
    if not passes_linkage:
        if "insufficient_evidence_linkage" not in defects:
            defects.append("insufficient_evidence_linkage")
        linkage_instruction = (
            f"Support the {section} synthesis with at least {target_linked} cited included studies that are relevant to this section."
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
        if available_for_section > 0:
            evaluation_data["pass"] = False
    return evaluation_data
