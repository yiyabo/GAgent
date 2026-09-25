"""PhageScope research-goal cluster of ``action_handlers``.

Moved verbatim out of ``action_handlers.py`` per
design/2026-09-24-backend-godfiles-refactor-plan.md §4.7 (handlers cluster ①).

Blueprint deviation (documented): the blueprint's ① is named
``action_analysis.py`` and described as "数学修复/验证分析/LLM 总结" — that content
lives in ``action_execution.py`` (W4d, out of scope for this phase).  The
substitute here is this file's own zero-patch-surface analysis/synthesis
surface: the PhageScope research-paper goal predicate, the seeded plan skeleton
builder (``_build_phagescope_research_seed_tasks``, imported directly by
``app/tests/chat/test_plan_phagescope_guardrail.py``) and the
``save_all`` result → structured analysis synthesizer
(``maybe_synthesize_phagescope_saveall_analysis``, re-exported by
``app/routers/chat/__init__.py`` and imported at module level by ``agent.py``).

Both names are re-exported by ``action_handlers``, so every import site is
unchanged.  Monkeypatch surface: none — no test patches these names.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from .models import AgentStep


def _looks_like_phagescope_research_paper_goal(goal: Any) -> bool:
    if not isinstance(goal, str) or not goal.strip():
        return False
    text = goal.lower()
    if "phagescope" not in text:
        return False
    research_tokens = ("research", "topic", "host", "genus", "prediction", "predict")
    paper_tokens = ("paper", "pdf", "report", "publish", "发表", "论文")
    return any(token in text for token in research_tokens) and any(token in text for token in paper_tokens)


def _build_phagescope_research_seed_tasks(goal: Any) -> List[Dict[str, Any]]:
    """Create a strict executable skeleton for PhageScope paper-generation plans.

    Generic decomposition tends to collapse this workflow into a few vague
    planning tasks.  For a publishable research paper, the system needs an
    audit→data→split→model→figure→report→verification chain with concrete
    artifact contracts from the start.
    """
    if not _looks_like_phagescope_research_paper_goal(goal):
        return []

    data_dir = os.getenv("PHAGESCOPE_DATA_DIR", "phagescope")
    output_md = "manuscript.md"
    output_pdf = "phagescope_research_topic1_production_report.pdf"
    specs: List[Dict[str, Any]] = [
        {
            "name": "Task 1: Audit PhageScope dataset",
            "instruction": (
                f"Run phagescope_research action=audit on {data_dir}. Save data_audit.json and require "
                "metadata_rows >= 1, metadata_files >= 1, unique_phage_ids >= 1, plus provenance for the "
                "resolved data directory."
            ),
            "criteria": [
                {"type": "file_nonempty", "path": "data_audit.json"},
                {"type": "json_field_at_least", "path": "data_audit.json", "key_path": "metadata_rows", "min_value": 1, "hard": True},
            ],
        },
        {
            "name": "Task 2: Prepare Host-derived genus metadata table",
            "instruction": (
                "Run phagescope_research action=prepare_metadata_table with label_level=genus, "
                "Host-derived labels only, leakage-aware split_group=cluster, and no Taxonomy-derived genus labels. "
                "Save curated_metadata.tsv, label_counts.tsv, and metadata_summary.json; require rows_written > 0 "
                "and labels_kept > 0."
            ),
            "criteria": [
                {"type": "file_nonempty", "path": "curated_metadata.tsv"},
                {"type": "json_field_at_least", "path": "curated_metadata.tsv", "key_path": "row_count", "min_value": 1, "hard": True},
                {"type": "file_nonempty", "path": "metadata_summary.json"},
                {"type": "json_field_at_least", "path": "metadata_summary.json", "key_path": "rows_written", "min_value": 1, "hard": True},
                {"type": "json_field_at_least", "path": "metadata_summary.json", "key_path": "labels_kept", "min_value": 1, "hard": True},
            ],
        },
        {
            "name": "Task 3: Validate split leakage and class balance",
            "instruction": (
                "Validate train/validation/test split integrity from curated_metadata.tsv. Report split counts, "
                "Host_label counts per split, subcluster/cluster leakage checks, smallest/largest class ratio, and "
                "limitations for rare genera. Cluster-level leakage must be zero for the final publishability gate. "
                "Save split_quality.json."
            ),
            "criteria": [
                {"type": "file_nonempty", "path": "split_quality.json"},
                {"type": "json_field_at_least", "path": "split_quality.json", "key_path": "total_rows", "min_value": 1, "hard": True},
            ],
        },
        {
            "name": "Task 4: Train RandomForest and ExtraTrees baselines",
            "instruction": (
                "Train metadata-only RandomForest and ExtraTrees baselines using the fixed Split column. Save "
                "model_metrics.json with numeric accuracy, macro_f1, weighted_f1, and top_k accuracy for each model; "
                "include baseline/majority-class comparison and random seed."
            ),
            "criteria": [
                {"type": "file_nonempty", "path": "model_metrics.json"},
                {"type": "model_metrics_valid", "path": "model_metrics.json", "hard": True},
            ],
        },
        {
            "name": "Task 5: Generate figures and result tables",
            "instruction": (
                "Generate publication-ready class distribution, model comparison, top-k performance, and confusion "
                "or error-analysis figures/tables. Save at least four figures or tables plus figure_manifest.json."
            ),
            "criteria": [
                {"type": "glob_count_at_least", "path": "figures/*.png", "count": 3},
                {"type": "file_nonempty", "path": "results/model_comparison.csv"},
                {"type": "file_nonempty", "path": "figure_manifest.json"},
            ],
        },
        {
            "name": "Task 6: Write reproducibility package",
            "instruction": (
                "Write reproducibility_manifest.json and methods_reproducibility.md covering data provenance, "
                "software versions, random seeds, exact commands, environment, and generated artifact paths."
            ),
            "criteria": [
                {"type": "file_nonempty", "path": "reproducibility_manifest.json"},
                {"type": "file_nonempty", "path": "methods_reproducibility.md"},
            ],
        },
        {
            "name": "Task 7: Draft publishable PhageScope manuscript Markdown",
            "instruction": (
                f"Write {output_md} as the primary publishable-paper-quality manuscript in Markdown before any PDF rendering. "
                "Use a MESM/BMC Biology-style structure: structured abstract, long Background, multi-subsection Results, "
                "Discussion, Conclusions, Methods, and Evidence boundary. The Results must integrate figure/table callouts "
                "with long-form analysis, include feature-leakage or ablation analysis, class-wise/error analysis, class "
                "imbalance interpretation, split leakage controls, metrics, limitations, and reproducibility. Avoid report-style "
                "bullet lists; write prose-first paragraphs. PDF is a later rendering target, not the source manuscript."
            ),
            "criteria": [
                {"type": "file_nonempty", "path": output_md},
                {
                    "type": "manuscript_markdown_quality",
                    "path": output_md,
                    "min_text_chars": 12000,
                    "min_sections": 6,
                    "min_long_paragraphs": 8,
                    "max_bullet_ratio": 0.12,
                    "min_figure_callouts": 4,
                    "min_table_callouts": 2,
                    "min_results_subsections": 3,
                    "required_terms": [
                        "Background",
                        "Results",
                        "Discussion",
                        "Methods",
                        "ablation",
                        "class-wise",
                        "leakage",
                        "Evidence boundary",
                    ],
                    "hard": True,
                },
            ],
        },
        {
            "name": "Task 8: Run final report quality audit",
            "instruction": (
                f"Audit {output_md} and all supporting artifacts against publishable-paper gates: dataset provenance, "
                "split leakage, class imbalance, baselines, metrics, figure/table integration, ablation or leakage-control "
                "analysis, class-wise/error analysis, limitations, and reproducibility. "
                "Save report_quality_audit.json with pass/fail flags and no unchecked mandatory gates."
            ),
            "criteria": [
                {"type": "file_nonempty", "path": "report_quality_audit.json"},
                {"type": "json_field_equals", "path": "report_quality_audit.json", "key_path": "mandatory_gates_passed", "expected": True, "hard": True},
            ],
        },
        {
            "name": "Task 9: Submit verified report deliverables",
            "instruction": (
                f"Submit {output_md} as the primary manuscript deliverable, plus model_metrics.json, report_quality_audit.json, "
                f"and reproducibility artifacts through deliverable_submit only after verification passes. If a stable Markdown-to-PDF "
                f"renderer produced {output_pdf}, include it as a secondary rendered artifact; do not treat PDF as the source of truth."
            ),
            "criteria": [
                {"type": "file_nonempty", "path": output_md},
                {
                    "type": "manuscript_markdown_quality",
                    "path": output_md,
                    "min_text_chars": 12000,
                    "min_sections": 6,
                    "min_long_paragraphs": 8,
                    "max_bullet_ratio": 0.12,
                    "min_figure_callouts": 4,
                    "min_table_callouts": 2,
                    "min_results_subsections": 3,
                    "required_terms": ["Results", "Discussion", "Methods", "ablation", "class-wise", "Evidence boundary"],
                    "hard": True,
                },
            ],
        },
    ]

    tasks: List[Dict[str, Any]] = []
    previous_name: Optional[str] = None
    for index, spec in enumerate(specs, start=1):
        task = {
            "name": spec["name"],
            "instruction": spec["instruction"],
            "metadata": {
                "task_type": "composite",
                "source": "phagescope_research_seed_plan",
                "explicit_task_number": index,
                "acceptance_criteria": {
                    "category": "file_data",
                    "blocking": True,
                    "checks": spec["criteria"],
                },
            },
            "dependencies": [previous_name] if previous_name else [],
        }
        tasks.append(task)
        previous_name = spec["name"]
    return tasks


# ---------------------------------------------------------------------------
# maybe_synthesize_phagescope_saveall_analysis
# ---------------------------------------------------------------------------

def maybe_synthesize_phagescope_saveall_analysis(agent: Any, steps: List[AgentStep]) -> Optional[str]:
    """If the action sequence matches save_all + local reads, return a structured analysis string."""
    if not steps:
        return None

    save_step: Optional[AgentStep] = None
    for step in steps:
        if step.action.kind == "tool_operation" and step.action.name == "phagescope":
            params = (
                step.details.get("parameters")
                if isinstance(step.details, dict)
                else None
            )
            if isinstance(params, dict) and params.get("action") == "save_all":
                save_step = step
                break
    if not save_step or not isinstance(save_step.details, dict):
        return None

    save_result = save_step.details.get("result")
    if not isinstance(save_result, dict):
        return None

    # Detect the injected chain by presence of file_operations reads with metadata labels.
    reads: Dict[str, str] = {}
    for step in steps:
        if step.action.kind != "tool_operation" or step.action.name != "file_operations":
            continue
        label = step.action.metadata.get("label") if isinstance(step.action.metadata, dict) else None
        if not isinstance(label, str) or not label:
            continue
        result = step.details.get("result") if isinstance(step.details, dict) else None
        if isinstance(result, dict) and isinstance(result.get("content"), str):
            reads[label] = result["content"]

    if not reads:
        return None

    output_dir = save_result.get("output_directory") or save_result.get("output_directory_rel")
    status_code = save_result.get("status_code")
    missing = save_result.get("missing_artifacts") or []
    missing_text = ""
    if isinstance(missing, list) and missing:
        missing_text = f" (partial missing: {', '.join(str(x) for x in missing)})"

    # Parse key jsons (best-effort)
    phage_info = None
    quality = None
    try:
        if "phage_info" in reads:
            phage_info = json.loads(reads["phage_info"]).get("results")
    except Exception:
        phage_info = None
    try:
        if "quality" in reads:
            quality = json.loads(reads["quality"]).get("results")
    except Exception:
        quality = None

    # Extract host/lifestyle/taxonomy
    host = lifestyle = taxonomy = gc_content = length = genes = None
    if isinstance(phage_info, list) and phage_info:
        row = phage_info[0] if isinstance(phage_info[0], dict) else None
        if isinstance(row, dict):
            host = row.get("host")
            lifestyle = row.get("lifestyle")
            taxonomy = row.get("taxonomy")
            gc_content = row.get("gc_content")
            length = row.get("length")
            genes = row.get("genes")

    # Extract quality summary
    qsum = None
    if isinstance(quality, dict):
        q = quality.get("quality_summary")
        if isinstance(q, list) and q and isinstance(q[0], dict):
            qsum = q[0]

    # Proteins: count + top5 annotations
    protein_count = None
    top5 = []
    proteins_tsv = reads.get("proteins_tsv")
    if isinstance(proteins_tsv, str) and proteins_tsv.strip():
        lines = [ln for ln in proteins_tsv.splitlines() if ln.strip()]
        if len(lines) >= 2:
            protein_count = max(0, len(lines) - 1)
            header = lines[0].split("\t")
            idx = None
            for i, col in enumerate(header):
                if col.strip() in {"Protein_function_classification", "function", "annotation"}:
                    idx = i
                    break
            for ln in lines[1:6]:
                cols = ln.split("\t")
                if idx is not None and idx < len(cols):
                    top5.append(cols[idx].strip())
    # Fallback: parse proteins.json when TSV missing/empty
    if (protein_count is None or not top5) and isinstance(reads.get("proteins_json"), str):
        try:
            payload = json.loads(reads["proteins_json"])
            results = payload.get("results") if isinstance(payload, dict) else None
            if isinstance(results, list):
                if protein_count is None:
                    protein_count = len(results)
                if not top5:
                    for item in results[:5]:
                        if not isinstance(item, dict):
                            continue
                        val = (
                            item.get("Protein_function_classification")
                            or item.get("function")
                            or item.get("annotation")
                        )
                        if val is not None:
                            top5.append(str(val).strip())
        except Exception:
            pass
    # Fallback: if no tsv, try summary/task_detail or phage_info genes
    if protein_count is None:
        try:
            if isinstance(genes, str) and genes.isdigit():
                protein_count = int(genes)
        except Exception:
            protein_count = None

    lines: List[str] = []
    lines.append(f"Downloaded to: {output_dir}{missing_text}")
    if status_code == 207:
        lines.append("Note: status 207 (partial success). Core results are available and interpretation continues with available artifacts.")

    lines.append("")
    lines.append("## Structured Interpretation")
    if isinstance(qsum, dict):
        lines.append("- **Quality Metrics**:")
        lines.append(
            "  - contig_id={cid}, length={clen}, gene_count={gc}, checkv_quality={cq}, miuvig_quality={mq}, completeness={comp}, contamination={cont}".format(
                cid=qsum.get("contig_id"),
                clen=qsum.get("contig_length"),
                gc=qsum.get("gene_count"),
                cq=qsum.get("checkv_quality"),
                mq=qsum.get("miuvig_quality"),
                comp=qsum.get("completeness"),
                cont=qsum.get("contamination"),
            )
        )
    else:
        lines.append("- **Quality Metrics**: Could not read `metadata/quality.json`; check file existence and security-policy restrictions.")

    lines.append("- **Host / Lifestyle**:")
    if host or lifestyle or taxonomy:
        lines.append(f"  - host={host}, lifestyle={lifestyle}, taxonomy={taxonomy}")
        lines.append(f"  - length={length}, genes={genes}, gc_content={gc_content}")
    else:
        lines.append("  - Could not read `metadata/phage_info.json`, or it is empty.")

    lines.append("- **Protein Annotation (derived from annotation outputs)**:")
    if protein_count is not None:
        lines.append(f"  - Protein count: {protein_count}")
    else:
        lines.append("  - Protein count: unavailable (you can later read proteins.json/tsv).")
    if top5:
        lines.append("  - Top 5 annotations:")
        for i, item in enumerate(top5, 1):
            lines.append(f"    {i}. {item}")
    else:
        lines.append("  - Top 5 annotations: failed to extract from `annotation/proteins.tsv` (missing file or read error).")

    return "\n".join(lines).strip()
