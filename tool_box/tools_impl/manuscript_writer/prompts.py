"""Prompt builders for the staged manuscript pipeline.

Text lives here verbatim; changing any of it changes LLM-visible behaviour.
``_build_merge_prompt`` has no production call site (the merge/transition stage
uses ``_smooth_transition`` plus the final-polish builders) but is kept and
re-exported because tests call it directly.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from .config import _FINAL_POLISH_EVAL_DIMS
from .rubrics import (
    _exemplar_style_instructions,
    _merge_and_polish_exemplar_hint,
    _section_eval_dims,
    _section_title,
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
