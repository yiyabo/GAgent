"""Section rubrics, weighted scoring, and citation/release-consistency checks."""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Sequence

from .config import (
    _ALL_EVAL_DIMENSIONS,
    _DEFAULT_LOCAL_DRAFT_SECTIONS,
    _DEFAULT_SECTIONS,
    _DIMENSION_WEIGHTS,
    _NUMERIC_TOKEN_RE,
    _REVIEW_SECTION_EVAL_DIMS,
    _SECTION_EVAL_DIMS,
)


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
