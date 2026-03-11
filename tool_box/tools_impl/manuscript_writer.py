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
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.llm import LLMClient
from app.services.llm.llm_service import LLMService, get_llm_service

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
_RUNTIME_DIR = _PROJECT_ROOT / "runtime"
_DEFAULT_MAX_CONTEXT_BYTES = 200_000  # 200 KB per file
_DEFAULT_MAX_REVISIONS = 5
_DEFAULT_THRESHOLD = 0.8
_ALLOWED_TEXT_EXTENSIONS = {
    ".md",
    ".txt",
    ".csv",
    ".tsv",
    ".json",
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
    "method": ["structure", "scientific_rigor", "clarity", "citation_integrity", "cohesion"],
    "experiment": ["structure", "scientific_rigor", "clarity", "citation_integrity", "cohesion"],
    "result": ["results_analysis", "clarity", "cohesion", "citation_integrity", "structure"],
    "discussion": ["scientific_rigor", "results_analysis", "clarity", "cohesion", "citation_integrity"],
}

_DIMENSION_WEIGHTS: Dict[str, float] = {
    "scientific_rigor": 1.5,
    "citation_integrity": 1.3,
    "results_analysis": 1.2,
    "method_detail": 1.0,
    "experiment_detail": 1.0,
    "structure": 1.0,
    "clarity": 1.0,
    "cohesion": 0.8,
    "academic_style": 0.7,
}
_TRUTHY_VALUES = {"1", "true", "yes", "on", "y"}


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
        "review article",
        "literature review",
        "systematic review",
        "narrative review",
        "survey article",
        "state-of-the-art review",
        "write a review",
        "review on",
    )
    return any(marker in text for marker in review_markers)


def _resolve_model_name(model_name: Optional[str]) -> Optional[str]:
    if not model_name:
        return None
    env_value = os.getenv(model_name)
    if env_value:
        return env_value.strip() or None
    return model_name.strip()


def _build_llm_service(provider: Optional[str], model: Optional[str]) -> Tuple[LLMService, Optional[str]]:
    if provider:
        client = LLMClient(provider=provider, model=model)
        return LLMService(client), model
    return get_llm_service(), model


async def _chat(llm: LLMService, prompt: str, model: Optional[str]) -> str:
    if model:
        return await llm.chat_async(prompt, model=model)
    return await llm.chat_async(prompt)


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
    """Extract citekeys from Markdown citekey syntax: [@citekey]."""
    if not text:
        return []
    keys: List[str] = []
    for m in re.finditer(r"\[@([A-Za-z0-9_:\-]+)\]", text):
        k = m.group(1).strip()
        if k and k not in keys:
            keys.append(k)
    return keys


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
            ]
        return [
            "Interpret quantitative results and link to figures/tables.",
            "Discuss effect sizes, significance, and practical implications.",
            "Avoid vague statements; ground claims in data.",
        ]
    if section == "discussion":
        return [
            "Contextualize findings within existing literature.",
            "Compare with prior studies and highlight novelty.",
            "Discuss limitations, alternative interpretations, and future work.",
            "Ground claims in data from the results section.",
        ]
    if section == "conclusion":
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
    return (
        f"Write the section: {section_title}\n\n"
        "Requirements:\n"
        f"{requirements_text}\n\n"
        "Writing rules:\n"
        f"{mode_text}"
        "- Use cohesive expert academic prose (avoid short fragmented sentences).\n"
        "- Ground all claims in the provided analysis memo and context.\n"
        "- Do NOT fabricate citations or results.\n"
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
    return (
        f"Revise the section: {section_title}\n\n"
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
    return (
        "You are finalizing a scientific manuscript. "
        "Perform a global rewrite to ensure consistency, remove repetition, "
        "and improve transitions while preserving content.\n\n"
        "Rules:\n"
        "- Keep section headings and order intact.\n"
        "- Do NOT add new facts or citations not supported by the input.\n"
        "- Ensure Methods and Experiments remain detailed.\n"
        "- Ensure Results contain interpretation and link to figures/tables.\n\n"
        f"User request:\n{task}\n\n"
        f"Analysis memo:\n{analysis_memo}\n\n"
        f"Combined draft:\n{combined_text}\n\n"
        "Return ONLY the final manuscript in Markdown."
    )


async def manuscript_writer_handler(
    task: str,
    output_path: str,
    context_paths: Optional[List[str]] = None,
    analysis_path: Optional[str] = None,
    sections: Optional[List[str]] = None,
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
    keep_workspace: bool = False,
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
        output_file = _resolve_project_path(output_path)
        session_dir = _resolve_session_dir(session_id)
        if session_dir and not _is_relative_to(output_file, session_dir):
            output_file = session_dir / output_file.name
        output_file.parent.mkdir(parents=True, exist_ok=True)

        if analysis_path and str(analysis_path).strip():
            analysis_file = _resolve_project_path(str(analysis_path).strip())
        else:
            analysis_file = _default_analysis_path(output_file)
        analysis_file.parent.mkdir(parents=True, exist_ok=True)

        run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        work_dir = (session_dir or output_file.parent) / f".manuscript_writer_{run_id}"
        work_dir.mkdir(parents=True, exist_ok=True)

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

        context_paths = context_paths or []
        section_list = sections or list(_DEFAULT_SECTIONS)
        section_list = [_normalize_section_key(s) for s in section_list if s and str(s).strip()]
        if not section_list:
            section_list = list(_DEFAULT_SECTIONS)
        review_mode = _is_review_article_task(task)

        context_text = _build_context_blocks(context_paths, max_context_bytes)
        bib_keys = _extract_bibtex_keys(context_text)

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

        gen_llm, gen_model = _build_llm_service(generation_provider, gen_model)
        eval_llm, eval_model = _build_llm_service(evaluation_provider, eval_model)
        merge_llm, merge_model_name = _build_llm_service(merge_provider, merge_model_name)

        analysis_prompt = _build_analysis_prompt(task, context_text, section_list)
        analysis_memo = await _chat(gen_llm, analysis_prompt, gen_model)
        analysis_file.write_text(analysis_memo, encoding="utf-8")

        sections_dir = work_dir / "sections"
        reviews_dir = work_dir / "reviews"
        merge_dir = work_dir / "merge"
        sections_dir.mkdir(parents=True, exist_ok=True)
        reviews_dir.mkdir(parents=True, exist_ok=True)
        merge_dir.mkdir(parents=True, exist_ok=True)

        section_results: List[Dict[str, Any]] = []
        section_scores: Dict[str, float] = {}
        failed_sections: List[str] = []
        passed_sections: List[Tuple[str, Path]] = []
        drafted_texts: List[str] = []
        section_text_map: Dict[str, str] = {}

        def _to_rel(path: Optional[Path]) -> Optional[str]:
            if path is None:
                return None
            return str(path.relative_to(_PROJECT_ROOT))

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
            return {
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

        def _build_failure_payload(
            *,
            error_code: str,
            manifest_path: Path,
            combined_partial: str,
            citation_validation_path: Optional[Path] = None,
            citation_report: Optional[Dict[str, Any]] = None,
        ) -> Dict[str, Any]:
            partial_path = merge_dir / "combined_partial.md"
            partial_path.write_text(combined_partial, encoding="utf-8")
            partial_output = output_file.with_suffix(output_file.suffix + ".partial.md")
            try:
                partial_output.write_text(combined_partial, encoding="utf-8")
            except Exception:
                pass
            stats = _build_stats(citation_report=citation_report)
            return {
                "tool": "manuscript_writer",
                "success": False,
                "error": error_code,
                "error_code": error_code,
                "quality_gate_passed": False,
                "failed_sections": list(failed_sections),
                "section_scores": dict(section_scores),
                "output_path": _to_rel(output_file),
                "analysis_path": _to_rel(analysis_file),
                "effective_output_path": _to_rel(output_file),
                "effective_analysis_path": _to_rel(analysis_file),
                "sections_dir": _to_rel(sections_dir),
                "reviews_dir": _to_rel(reviews_dir),
                "merge_queue": _to_rel(manifest_path),
                "combined_partial": _to_rel(partial_path),
                "partial_output_path": _to_rel(partial_output),
                "citation_validation_path": _to_rel(citation_validation_path),
                "citation_validation": citation_report,
                "temp_workspace": _to_rel(work_dir),
                "sections": section_results,
                "run_stats": stats,
                "bib_precheck_warning": bib_precheck_warning,
            }

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

            text = await _chat(
                gen_llm,
                _build_section_prompt(
                    task,
                    section,
                    analysis_memo,
                    context_text,
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
                    context_text,
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
            }

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
            return _build_failure_payload(
                error_code="section_evaluation_failed",
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
            if strict_gate:
                return _build_failure_payload(
                    error_code="citation_validation_failed",
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
        final_text = "\n\n".join(
            smoothed_texts.get(section, "").strip()
            for section in section_list
            if smoothed_texts.get(section, "").strip()
        )
        output_file.write_text(final_text, encoding="utf-8")

        quality_gate_passed = len(failed_sections) == 0 and bool(citation_report.get("pass"))
        cleanup_errors: List[str] = []
        if not keep_workspace:
            try:
                shutil.rmtree(work_dir)
            except Exception as exc:
                cleanup_errors.append(str(exc))

        stats = _build_stats(citation_report=citation_report)
        stats["final_chars"] = len(final_text or "")
        return {
            "tool": "manuscript_writer",
            "success": True,
            "quality_gate_passed": quality_gate_passed,
            "failed_sections": list(failed_sections),
            "section_scores": dict(section_scores),
            "analysis_path": _to_rel(analysis_file),
            "effective_analysis_path": _to_rel(analysis_file),
            "sections_dir": None if not keep_workspace else _to_rel(sections_dir),
            "reviews_dir": None if not keep_workspace else _to_rel(reviews_dir),
            "combined_path": None if not keep_workspace else _to_rel(combined_path),
            "merge_queue": None if not keep_workspace else _to_rel(manifest_path),
            "citation_validation_path": None if not keep_workspace else _to_rel(citation_validation_path),
            "citation_validation": citation_report if keep_workspace else None,
            "output_path": _to_rel(output_file),
            "effective_output_path": _to_rel(output_file),
            "sections": section_results,
            "draft_chars": len(final_text or ""),
            "intermediate_purged": not keep_workspace,
            "temp_workspace": _to_rel(work_dir),
            "cleanup_errors": cleanup_errors,
            "run_stats": stats,
            "bib_precheck_warning": bib_precheck_warning,
        }
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
        },
        "required": ["task", "output_path"],
    },
    "handler": manuscript_writer_handler,
    "tags": ["writing", "manuscript", "evaluation", "qwen"],
    "examples": [
        "Generate a staged manuscript using data/examples/outline.txt and save to runtime/session_x/shared/draft.md",
    ],
}
