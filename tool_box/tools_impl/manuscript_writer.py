"""
Manuscript Writer Tool

Generate a research manuscript in staged sections with evaluation and revision.
Pipeline:
1) Build a global analysis memo from provided context.
2) Generate each section (abstract, introduction, methods, experiments, results,
   conclusion, references).
3) Evaluate each section with a strict JSON rubric and revise up to N times.
4) Merge approved sections and perform a final global rewrite.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime
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
}
_DEFAULT_SECTIONS = [
    "abstract",
    "introduction",
    "methods",
    "experiments",
    "results",
    "conclusion",
    "references",
]


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
    session_dir = _RUNTIME_DIR / f"session_{session_id}"
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


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


def _average_score(scores: Dict[str, Any]) -> float:
    values: List[float] = []
    for value in scores.values():
        try:
            values.append(float(value))
        except (TypeError, ValueError):
            continue
    if not values:
        return 0.0
    return sum(values) / len(values)


def _section_title(section: str) -> str:
    mapping = {
        "methods": "Methods",
        "experiments": "Experiments",
        "results": "Results",
        "conclusion": "Conclusion",
        "references": "References",
        "abstract": "Abstract",
        "introduction": "Introduction",
    }
    return mapping.get(section.lower(), section.title())


def _section_requirements(section: str) -> List[str]:
    section = section.lower()
    if section == "abstract":
        return [
            "Provide background, objective, methods, key results, and conclusion.",
            "State key numeric findings with units when available.",
            "Keep concise but substantive.",
        ]
    if section == "introduction":
        return [
            "Explain the scientific motivation and gap.",
            "Summarize relevant prior work only from provided context.",
            "State study objectives and hypotheses.",
        ]
    if section == "methods":
        return [
            "Provide full data processing steps and parameter settings.",
            "Describe statistical tests and model configurations.",
            "Include QC thresholds, inclusion/exclusion criteria, and software versions if provided.",
        ]
    if section == "experiments":
        return [
            "Detail experimental setup, datasets, and analysis workflow.",
            "Specify sample groups, inclusion/exclusion criteria, and preprocessing steps.",
            "Explain evaluation protocol, metrics, and statistical tests used.",
            "Describe figure/table construction and what each figure demonstrates.",
            "List controls, baselines, ablations, and validation steps.",
            "Report key numeric results with units and uncertainty where available.",
        ]
    if section == "results":
        return [
            "Interpret quantitative results and link to figures/tables.",
            "Discuss effect sizes, significance, and practical implications.",
            "Avoid vague statements; ground claims in data.",
        ]
    if section == "conclusion":
        return [
            "Summarize key contributions and findings.",
            "Discuss limitations and future work.",
            "Avoid introducing new results.",
        ]
    if section == "references":
        return [
            "List only provided citations or data sources (file paths).",
            "Do not fabricate external references.",
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
) -> str:
    section_title = _section_title(section)
    requirements_text = "\n".join(f"- {item}" for item in requirements)
    return (
        f"Write the section: {section_title}\n\n"
        "Requirements:\n"
        f"{requirements_text}\n\n"
        "Writing rules:\n"
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
) -> str:
    section_title = _section_title(section)
    requirements_text = "\n".join(f"- {item}" for item in requirements)
    return (
        "You are a strict scientific writing reviewer. "
        "Evaluate the following section and return JSON ONLY.\n\n"
        f"Section: {section_title}\n\n"
        "Section requirements:\n"
        f"{requirements_text}\n\n"
        "Evaluation dimensions (score each 0.0 to 1.0):\n"
        "- structure\n"
        "- scientific_rigor\n"
        "- method_detail\n"
        "- experiment_detail\n"
        "- results_analysis\n"
        "- clarity\n"
        "- cohesion\n"
        "- academic_style\n"
        "- citation_integrity\n\n"
        "Return JSON with this schema:\n"
        "{\n"
        '  "scores": { "structure": 0.0, "scientific_rigor": 0.0, ... },\n'
        '  "defects": ["..."],\n'
        '  "revision_instructions": ["..."],\n'
        '  "pass": true\n'
        "}\n\n"
        "Rules:\n"
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
) -> str:
    section_title = _section_title(section)
    requirements_text = "\n".join(f"- {item}" for item in requirements)
    evaluation_json = json.dumps(evaluation, ensure_ascii=True, indent=2)
    return (
        f"Revise the section: {section_title}\n\n"
        "Section requirements:\n"
        f"{requirements_text}\n\n"
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
        output_file = _resolve_project_path(output_path)
        session_dir = _resolve_session_dir(session_id)
        if session_dir and not _is_relative_to(output_file, session_dir):
            output_file = session_dir / output_file.name
        output_file.parent.mkdir(parents=True, exist_ok=True)

        run_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        work_dir = (session_dir or output_file.parent) / f".manuscript_writer_{run_id}"
        work_dir.mkdir(parents=True, exist_ok=True)

        analysis_file = work_dir / f"{output_file.name}.analysis.md"

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
        section_list = [s.strip().lower() for s in section_list if s and str(s).strip()]
        if not section_list:
            section_list = list(_DEFAULT_SECTIONS)

        context_text = _build_context_blocks(context_paths, max_context_bytes)

        gen_model = _resolve_model_name(generation_model)
        eval_model = _resolve_model_name(evaluation_model)
        merge_model_name = _resolve_model_name(merge_model)

        gen_llm, gen_model = _build_llm_service(generation_provider, gen_model)
        eval_llm, eval_model = _build_llm_service(evaluation_provider, eval_model)
        merge_llm, merge_model_name = _build_llm_service(merge_provider, merge_model_name)

        analysis_prompt = _build_analysis_prompt(task, context_text, section_list)
        analysis_memo = await _chat(gen_llm, analysis_prompt, gen_model)
        analysis_file.write_text(analysis_memo, encoding="utf-8")

        base_dir = work_dir
        sections_dir = base_dir / "sections"
        reviews_dir = base_dir / "reviews"
        merge_dir = base_dir / "merge"
        sections_dir.mkdir(parents=True, exist_ok=True)
        reviews_dir.mkdir(parents=True, exist_ok=True)
        merge_dir.mkdir(parents=True, exist_ok=True)

        section_results: List[Dict[str, Any]] = []
        passed_sections: List[Tuple[str, Path]] = []

        for idx, section in enumerate(section_list, start=1):
            section_filename = f"{idx:02d}_{section}.md"
            section_path = sections_dir / section_filename
            requirements = _section_requirements(section)

            section_text = await _chat(
                gen_llm,
                _build_section_prompt(task, section, analysis_memo, context_text, requirements),
                gen_model,
            )

            evaluation_data: Optional[Dict[str, Any]] = None
            passed = False
            attempts = 0

            for attempt in range(1, max_revisions + 1):
                attempts = attempt
                eval_prompt = _build_evaluation_prompt(section, analysis_memo, section_text, requirements)
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
                avg_score = _average_score(scores) if isinstance(scores, dict) else 0.0
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
                    section_text,
                    evaluation_data,
                    requirements,
                )
                section_text = await _chat(gen_llm, revision_prompt, gen_model)

            section_path.write_text(section_text, encoding="utf-8")
            section_results.append(
                {
                    "section": section,
                    "path": str(section_path.relative_to(_PROJECT_ROOT)),
                    "attempts": attempts,
                    "passed": passed,
                    "evaluation_path": str(
                        (reviews_dir / f"{section}_eval_{attempts}.json").relative_to(_PROJECT_ROOT)
                    ),
                }
            )
            if passed:
                passed_sections.append((section, section_path))

        if len(passed_sections) != len(section_list):
            manifest_path = merge_dir / "merge_queue.json"
            manifest_path.write_text(
                json.dumps(section_results, ensure_ascii=True, indent=2),
                encoding="utf-8",
            )
            return {
                "tool": "manuscript_writer",
                "success": False,
                "error": "section_evaluation_failed",
                "analysis_path": str(analysis_file.relative_to(_PROJECT_ROOT)),
                "sections_dir": str(sections_dir.relative_to(_PROJECT_ROOT)),
                "reviews_dir": str(reviews_dir.relative_to(_PROJECT_ROOT)),
                "merge_queue": str(manifest_path.relative_to(_PROJECT_ROOT)),
                "temp_workspace": str(work_dir.relative_to(_PROJECT_ROOT)),
                "sections": section_results,
            }

        combined_text = "\n\n".join(
            section_path.read_text(encoding="utf-8") for _, section_path in passed_sections
        )
        combined_path = merge_dir / "combined_draft.md"
        combined_path.write_text(combined_text, encoding="utf-8")

        final_prompt = _build_merge_prompt(task, analysis_memo, combined_text)
        final_text = await _chat(merge_llm, final_prompt, merge_model_name)
        output_file.write_text(final_text, encoding="utf-8")

        manifest_path = merge_dir / "merge_queue.json"
        manifest_path.write_text(
            json.dumps(section_results, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )

        cleanup_errors: List[str] = []
        try:
            shutil.rmtree(work_dir)
        except Exception as exc:
            cleanup_errors.append(str(exc))

        return {
            "tool": "manuscript_writer",
            "success": True,
            "analysis_path": None,
            "sections_dir": None,
            "reviews_dir": None,
            "combined_path": None,
            "merge_queue": None,
            "output_path": str(output_file.relative_to(_PROJECT_ROOT)),
            "sections": section_results,
            "draft_chars": len(final_text or ""),
            "intermediate_purged": True,
            "temp_workspace": str(work_dir.relative_to(_PROJECT_ROOT)),
            "cleanup_errors": cleanup_errors,
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
                "description": "Section list (default: abstract/introduction/methods/experiments/results/conclusion/references).",
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
        },
        "required": ["task", "output_path"],
    },
    "handler": manuscript_writer_handler,
    "tags": ["writing", "manuscript", "evaluation", "qwen"],
    "examples": [
        "Generate a staged manuscript using data/examples/outline.txt and save to runtime/session_x/shared/draft.md",
    ],
}
