from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.llm import LLMClient
from app.services.plans.plan_models import PlanNode, PlanTree

logger = logging.getLogger(__name__)

RUBRIC_VERSION = "plan_rubric_v1"


RubricScores = Dict[str, Dict[str, float]]
RubricEvidence = Dict[str, Dict[str, List[str]]]


@dataclass(frozen=True)
class PlanRubricResult:
    plan_id: int
    rubric_version: str
    evaluator_provider: str
    evaluator_model: str
    evaluated_at: str
    overall_score: float  # 0-100
    dimension_scores: Dict[str, float]  # 0-100
    subcriteria_scores: RubricScores  # 0-1 per subcriteria
    evidence: RubricEvidence
    feedback: Dict[str, Any]
    rule_evidence: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "rubric_version": self.rubric_version,
            "evaluator_provider": self.evaluator_provider,
            "evaluator_model": self.evaluator_model,
            "evaluated_at": self.evaluated_at,
            "overall_score": self.overall_score,
            "dimension_scores": dict(self.dimension_scores),
            "subcriteria_scores": self.subcriteria_scores,
            "evidence": self.evidence,
            "feedback": self.feedback,
            "rule_evidence": self.rule_evidence,
        }


def rubric_definition_en() -> Dict[str, Any]:
    """Return the plan-quality rubric definition in English."""
    return {
        "contextual_completeness": {
            "focus": "Only assess 'why/motivation' and reasoning; ignore tools/parameters/QC details.",
            "subcriteria": {
                "C1": "Most key steps explicitly state why they are needed.",
                "C2": "Rationales align with plan goals (goal–step linkage is explicit).",
                "C3": "Key assumptions/constraints are stated (scope/data availability).",
                "C4": "The ordering/workflow is justified (not arbitrary).",
                "C5": "Alternatives/trade-offs are discussed where appropriate.",
            },
        },
        "accuracy": {
            "focus": "Only assess correctness/feasibility of methods/tools/assumptions; ignore missing parameters/QC.",
            "subcriteria": {
                "A1": "Methods/tools are appropriate for the tasks.",
                "A2": "Assumptions are technically feasible.",
                "A3": "No obvious contradictions across steps.",
                "A4": "Tool capabilities match intended usage (no impossible asks).",
                "A5": "Conforms to domain conventions/best practices.",
            },
        },
        "task_granularity_atomicity": {
            "focus": "Only assess decomposition and executability; ignore rationale/parameters/QC.",
            "subcriteria": {
                "G1": "Most steps are atomic actions (single action, clear outcome).",
                "G2": "Steps are executable as written (no further decomposition needed).",
                "G3": "Few broad-goal or slogan-like steps.",
                "G4": "Dependencies are clear and minimized.",
                "G5": "Little redundancy/overlap across steps.",
            },
        },
        "reproducibility_parameterization": {
            "focus": "Only assess tools/parameters/I-O/data provenance; ignore validation rigor.",
            "subcriteria": {
                "R1": "Tools/methods are named.",
                "R2": "Inputs/outputs are specified (files/formats).",
                "R3": "Key parameters or decision rules are specified (ranges are acceptable).",
                "R4": "Data sources/versions are identified.",
                "R5": "Plan is reproducible at the plan level (someone else can follow it).",
            },
        },
        "scientific_rigor": {
            "focus": "Only assess QC/validation/metrics/baselines, not general writing quality.",
            "subcriteria": {
                "S1": "QC/validation steps are explicit.",
                "S2": "Evaluation metrics are defined (e.g., F1, N50).",
                "S3": "Baselines/controls are specified.",
                "S4": "Error analysis or robustness checks are included.",
                "S5": "Acceptance thresholds or pass criteria are stated.",
            },
        },
    }


def _extract_json_block(text: str) -> Optional[Dict[str, Any]]:
    if not isinstance(text, str):
        return None
    raw = text.strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(raw[start : end + 1])
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    if f != f:  # NaN
        return default
    return f


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _is_root(node: PlanNode) -> bool:
    if node.parent_id is not None:
        return False
    meta = node.metadata or {}
    if isinstance(meta, dict) and (meta.get("is_root") is True or meta.get("task_type") == "root"):
        return True
    # Fall back: any node with parent_id None is considered root-ish.
    return True


def _collect_children_map(tree: PlanTree) -> Dict[Optional[int], List[int]]:
    try:
        return dict(tree.adjacency or {})
    except (TypeError, ValueError):
        # Defensive: rebuild if needed
        tree.rebuild_adjacency()
        return dict(tree.adjacency or {})


def _leaf_ids(tree: PlanTree, children_map: Dict[Optional[int], List[int]]) -> List[int]:
    leaf: List[int] = []
    for node_id, node in tree.nodes.items():
        if _is_root(node):
            continue
        if not children_map.get(node_id):
            leaf.append(node_id)
    return leaf


def _build_plan_outline_for_eval(tree: PlanTree) -> str:
    # Prefer a slightly deeper outline for evaluation, but cap size.
    try:
        return tree.to_outline(max_depth=6, max_nodes=140, include_results=False)
    except (TypeError, ValueError, AttributeError):
        return f"Plan #{tree.id}: {tree.title}"


def _build_rule_evidence(tree: PlanTree) -> Dict[str, Any]:
    """
    Build deterministic evidence signals that help the LLM score consistently.
    This is NOT the final score; it's structured hints + examples.
    """
    children_map = _collect_children_map(tree)
    nodes = list(tree.nodes.values())
    root_ids = tree.root_node_ids()
    non_root_nodes = [n for n in nodes if not _is_root(n)]
    leaf_ids = _leaf_ids(tree, children_map)

    # Dependency graph signals
    all_ids = {n.id for n in nodes}
    orphan_deps: List[Tuple[int, int]] = []
    self_deps: List[int] = []
    dep_edges: List[Tuple[int, int]] = []
    for n in non_root_nodes:
        deps = list(n.dependencies or [])
        for d in deps:
            dep_edges.append((n.id, d))
            if d not in all_ids:
                orphan_deps.append((n.id, d))
        if n.id in deps:
            self_deps.append(n.id)

    # Very lightweight cycle check (Kahn-like heuristic): if we cannot topologically sort,
    # we surface "cycle_suspected". (We will still let downstream plan_tools keep the exact DFS.)
    indeg: Dict[int, int] = {n.id: 0 for n in non_root_nodes}
    adj: Dict[int, List[int]] = {n.id: [] for n in non_root_nodes}
    for src, dst in dep_edges:
        if src in indeg and dst in indeg:
            indeg[src] += 1
            adj.setdefault(dst, []).append(src)
    queue = [nid for nid, deg in indeg.items() if deg == 0]
    visited = 0
    while queue:
        cur = queue.pop()
        visited += 1
        for nxt in adj.get(cur, []):
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                queue.append(nxt)
    cycle_suspected = visited != len(indeg) and len(indeg) > 0

    # Textual signals for rubric subcriteria (multilingual heuristics)
    why_markers = [
        "because",
        "so that",
        "in order to",
        "rationale",
        "motivation",
        "because of",
        "为了",
        "以便",
        "因为",
        "目的",
        "动机",
        "原因",
        "理由",
    ]
    assumption_markers = [
        "assume",
        "assumption",
        "constraint",
        "limitations",
        "scope",
        "availability",
        "data availability",
        "假设",
        "约束",
        "限制",
        "范围",
        "数据可得",
        "可获得",
    ]
    ordering_markers = [
        "first",
        "then",
        "next",
        "finally",
        "step",
        "pipeline",
        "workflow",
        "首先",
        "然后",
        "接着",
        "最后",
        "流程",
        "步骤",
    ]
    alternative_markers = [
        "alternative",
        "trade-off",
        "tradeoff",
        "option",
        "ablation",
        "versus",
        "or",
        "或者",
        "备选",
        "权衡",
        "对比",
        "消融",
    ]
    tool_markers = [
        "blast",
        "bowtie",
        "bwa",
        "samtools",
        "seqkit",
        "hmmer",
        "prodigal",
        "checkv",
        "docker",
        "snakemake",
        "nextflow",
        "python",
        "pytorch",
        "tensorflow",
        "esm",
        "phagescope",
        "virsorter",
        "genomad",
    ]
    io_markers = [
        ".fasta",
        ".fa",
        ".fastq",
        ".fq",
        ".bam",
        ".sam",
        ".tsv",
        ".csv",
        ".json",
        ".yaml",
        ".yml",
        ".parquet",
        "input",
        "output",
        "输入",
        "输出",
        "文件",
        "格式",
    ]
    metric_markers = [
        "f1",
        "auc",
        "auroc",
        "accuracy",
        "precision",
        "recall",
        "n50",
        "perplexity",
        "metric",
        "baseline",
        "control",
        "threshold",
        "acceptance",
        "验收",
        "指标",
        "基线",
        "对照",
        "阈值",
        "通过标准",
        "qc",
        "validation",
        "benchmark",
        "robust",
        "ablation",
        "error analysis",
        "cross-validation",
        "交叉验证",
        "误差分析",
        "稳健",
    ]
    data_source_markers = [
        "ncbi",
        "genbank",
        "refseq",
        "ena",
        "uniprot",
        "pfam",
        "phagescope",
        "doi",
        "arxiv",
        "github",
        "版本",
        "v1",
        "v2",
        "release",
        "数据来源",
        "数据库",
    ]

    def _has_any(text: str, markers: List[str]) -> bool:
        t = text.lower()
        return any(m in t for m in markers)

    def _example_tasks(predicate, limit: int = 6) -> List[Dict[str, Any]]:
        examples: List[Dict[str, Any]] = []
        for n in non_root_nodes:
            instruction = (n.instruction or "").strip()
            name = (n.name or "").strip()
            blob = f"{name}\n{instruction}".strip()
            if not blob:
                continue
            if predicate(blob):
                snippet = blob[:160] + ("..." if len(blob) > 160 else "")
                examples.append({"task_id": n.id, "snippet": snippet})
            if len(examples) >= limit:
                break
        return examples

    # Counts
    why_count = sum(
        1
        for n in non_root_nodes
        if _has_any(f"{n.name}\n{n.instruction or ''}", why_markers)
    )
    assumption_count = sum(
        1
        for n in non_root_nodes
        if _has_any(f"{n.name}\n{n.instruction or ''}", assumption_markers)
    )
    ordering_count = sum(
        1
        for n in non_root_nodes
        if _has_any(f"{n.name}\n{n.instruction or ''}", ordering_markers)
    )
    alternative_count = sum(
        1
        for n in non_root_nodes
        if _has_any(f"{n.name}\n{n.instruction or ''}", alternative_markers)
    )
    tool_count = sum(
        1
        for n in non_root_nodes
        if _has_any(f"{n.name}\n{n.instruction or ''}", tool_markers)
    )
    io_count = sum(
        1
        for n in non_root_nodes
        if _has_any(f"{n.name}\n{n.instruction or ''}", io_markers)
    )
    metric_count = sum(
        1
        for n in non_root_nodes
        if _has_any(f"{n.name}\n{n.instruction or ''}", metric_markers)
    )
    data_source_count = sum(
        1
        for n in non_root_nodes
        if _has_any(f"{n.name}\n{n.instruction or ''}", data_source_markers)
    )

    short_instruction = [
        n.id
        for n in non_root_nodes
        if not (n.instruction or "").strip() or len((n.instruction or "").strip()) < 20
    ]

    # Broad/slogan-like steps heuristic
    broad_keywords = [
        "research",
        "investigate",
        "explore",
        "analyze",
        "optimize",
        "improve",
        "design",
        "build",
        "develop",
        "study",
        "研究",
        "探索",
        "分析",
        "优化",
        "设计",
        "开发",
        "搭建",
    ]
    broad_steps = [
        n.id
        for n in non_root_nodes
        if len((n.instruction or "").strip()) < 35
        and any(k in (n.name or "").lower() for k in broad_keywords)
    ]

    # Redundancy heuristic: duplicate normalized task names
    norm = lambda s: re.sub(r"\s+", " ", (s or "").strip().lower())
    name_counts: Dict[str, int] = {}
    for n in non_root_nodes:
        key = norm(n.name)
        if key:
            name_counts[key] = name_counts.get(key, 0) + 1
    duplicates = [k for k, c in name_counts.items() if c >= 2]

    # Dependency density
    dep_counts = [len(n.dependencies or []) for n in non_root_nodes]
    avg_deps = (sum(dep_counts) / len(dep_counts)) if dep_counts else 0.0

    # Depth stats
    depths = [n.depth for n in non_root_nodes if isinstance(n.depth, int)]
    max_depth = max(depths) if depths else 0

    return {
        "plan_id": tree.id,
        "title": tree.title,
        "description": tree.description or "",
        "node_counts": {
            "total_nodes": len(nodes),
            "root_nodes": len(root_ids),
            "non_root_nodes": len(non_root_nodes),
            "leaf_nodes": len(leaf_ids),
            "max_depth": max_depth,
        },
        "dependency_signals": {
            "edge_count": len(dep_edges),
            "avg_deps_per_task": round(avg_deps, 3),
            "cycle_suspected": bool(cycle_suspected),
            "orphan_dependencies": [{"task_id": t, "missing_dep": d} for t, d in orphan_deps[:20]],
            "self_dependencies": list(self_deps[:50]),
        },
        "text_signals": {
            "why_marker_tasks": why_count,
            "assumption_marker_tasks": assumption_count,
            "ordering_marker_tasks": ordering_count,
            "alternative_marker_tasks": alternative_count,
            "tool_named_tasks": tool_count,
            "io_specified_tasks": io_count,
            "metrics_qc_tasks": metric_count,
            "data_source_tasks": data_source_count,
            "short_or_missing_instruction_tasks": list(short_instruction[:80]),
            "broad_slogan_like_tasks": list(broad_steps[:80]),
            "duplicate_task_names": duplicates[:40],
        },
        "examples": {
            "why_examples": _example_tasks(lambda t: _has_any(t, why_markers)),
            "assumption_examples": _example_tasks(lambda t: _has_any(t, assumption_markers)),
            "tool_examples": _example_tasks(lambda t: _has_any(t, tool_markers)),
            "io_examples": _example_tasks(lambda t: _has_any(t, io_markers)),
            "metric_examples": _example_tasks(lambda t: _has_any(t, metric_markers)),
            "data_source_examples": _example_tasks(lambda t: _has_any(t, data_source_markers)),
            "broad_examples": _example_tasks(lambda t: any(k in t.lower() for k in broad_keywords)),
        },
    }


def _default_weights() -> Tuple[Dict[str, float], Dict[str, Dict[str, float]]]:
    # Equal weights by default (dimensions and subcriteria).
    dim_weights = {
        "contextual_completeness": 1.0,
        "accuracy": 1.0,
        "task_granularity_atomicity": 1.0,
        "reproducibility_parameterization": 1.0,
        "scientific_rigor": 1.0,
    }
    rubric = rubric_definition_en()
    sub_w: Dict[str, Dict[str, float]] = {}
    for dim, data in rubric.items():
        subs = data.get("subcriteria", {}) or {}
        sub_w[dim] = {k: 1.0 for k in subs.keys()}
    return dim_weights, sub_w


def _aggregate_scores(
    subcriteria_scores: RubricScores,
    *,
    dim_weights: Optional[Dict[str, float]] = None,
    sub_weights: Optional[Dict[str, Dict[str, float]]] = None,
) -> Tuple[Dict[str, float], float]:
    dim_w, sub_w = _default_weights()
    if isinstance(dim_weights, dict):
        dim_w.update({k: float(v) for k, v in dim_weights.items() if v is not None})
    if isinstance(sub_weights, dict):
        for dim, mapping in sub_weights.items():
            if isinstance(mapping, dict) and dim in sub_w:
                for k, v in mapping.items():
                    if v is not None:
                        sub_w[dim][k] = float(v)

    dim_scores_0_1: Dict[str, float] = {}
    for dim, subs in subcriteria_scores.items():
        if not isinstance(subs, dict) or dim not in sub_w:
            continue
        total = 0.0
        wsum = 0.0
        for sk, raw in subs.items():
            if sk not in sub_w[dim]:
                continue
            score = _clamp(_safe_float(raw, 0.0), 0.0, 1.0)
            w = float(sub_w[dim].get(sk, 1.0))
            total += score * w
            wsum += w
        dim_scores_0_1[dim] = (total / wsum) if wsum > 0 else 0.0

    # Convert to 0-100 and compute overall (weighted).
    dim_scores_0_100: Dict[str, float] = {
        dim: round(_clamp(v, 0.0, 1.0) * 100.0, 2) for dim, v in dim_scores_0_1.items()
    }
    overall_total = 0.0
    overall_wsum = 0.0
    for dim, score_0_100 in dim_scores_0_100.items():
        w = float(dim_w.get(dim, 0.0))
        if w <= 0:
            continue
        overall_total += (score_0_100 / 100.0) * w
        overall_wsum += w
    overall = (overall_total / overall_wsum) * 100.0 if overall_wsum > 0 else 0.0
    return dim_scores_0_100, round(_clamp(overall, 0.0, 100.0), 2)


def _fallback_rule_only_scores(tree: PlanTree) -> Tuple[RubricScores, RubricEvidence, Dict[str, Any]]:
    """
    Rule-only fallback: deterministic, conservative scoring based on signals.
    Intended only when evaluator LLM is unavailable.
    """
    evidence = _build_rule_evidence(tree)
    s = evidence.get("text_signals", {}) or {}
    d = evidence.get("dependency_signals", {}) or {}
    counts = evidence.get("node_counts", {}) or {}
    non_root = max(int(counts.get("non_root_nodes", 0) or 0), 1)

    # Map signals to 0-1 subcriteria conservatively.
    ratio = lambda k: _clamp((_safe_float(s.get(k), 0.0) / non_root), 0.0, 1.0)

    cycle_penalty = 1.0 if not d.get("cycle_suspected") else 0.3
    orphan_penalty = 1.0 if not d.get("orphan_dependencies") else 0.3
    _self_dep_penalty = 1.0 if not d.get("self_dependencies") else 0.3

    broad_ratio = _clamp(_safe_float(len(s.get("broad_slogan_like_tasks", []) or []), 0.0) / non_root, 0.0, 1.0)
    short_ratio = _clamp(_safe_float(len(s.get("short_or_missing_instruction_tasks", []) or []), 0.0) / non_root, 0.0, 1.0)

    # Heuristics: these are intentionally strict.
    sub: RubricScores = {
        "contextual_completeness": {
            "C1": ratio("why_marker_tasks"),
            "C2": ratio("why_marker_tasks") * 0.9,
            "C3": ratio("assumption_marker_tasks"),
            "C4": ratio("ordering_marker_tasks"),
            "C5": ratio("alternative_marker_tasks") * 0.9,
        },
        "accuracy": {
            "A1": ratio("tool_named_tasks") * 0.75 + 0.2 * cycle_penalty,
            "A2": 0.6 * cycle_penalty,
            "A3": 0.7 * cycle_penalty,
            "A4": 0.7 * orphan_penalty,
            "A5": 0.55 + 0.25 * ratio("tool_named_tasks"),
        },
        "task_granularity_atomicity": {
            "G1": _clamp(1.0 - broad_ratio, 0.0, 1.0),
            "G2": _clamp(1.0 - short_ratio, 0.0, 1.0),
            "G3": _clamp(1.0 - broad_ratio, 0.0, 1.0),
            "G4": _clamp(1.0 - _clamp(_safe_float(d.get("avg_deps_per_task"), 0.0) / 3.0, 0.0, 1.0), 0.0, 1.0),
            "G5": _clamp(1.0 - _clamp(_safe_float(len(s.get("duplicate_task_names", []) or []), 0.0) / 8.0, 0.0, 1.0), 0.0, 1.0),
        },
        "reproducibility_parameterization": {
            "R1": ratio("tool_named_tasks"),
            "R2": ratio("io_specified_tasks"),
            "R3": _clamp(_safe_float(s.get("metrics_qc_tasks"), 0.0) / non_root, 0.0, 1.0) * 0.7,
            "R4": ratio("data_source_tasks"),
            "R5": _clamp(1.0 - short_ratio, 0.0, 1.0),
        },
        "scientific_rigor": {
            "S1": ratio("metrics_qc_tasks") * 0.8,
            "S2": ratio("metrics_qc_tasks") * 0.85,
            "S3": ratio("metrics_qc_tasks") * 0.75,
            "S4": ratio("metrics_qc_tasks") * 0.7,
            "S5": ratio("metrics_qc_tasks") * 0.6,
        },
    }

    # Clamp all
    for dim, subs in sub.items():
        for k, v in list(subs.items()):
            subs[k] = round(_clamp(_safe_float(v, 0.0), 0.0, 1.0), 3)

    evid: RubricEvidence = {}
    for dim, subs in rubric_definition_en().items():
        evid[dim] = {k: [] for k in subs.get("subcriteria", {}).keys()}
    return sub, evid, evidence


def evaluate_plan_rubric(
    tree: PlanTree,
    *,
    evaluator_client: Optional[LLMClient] = None,
    evaluator_provider: str = "kimi",
    evaluator_model: Optional[str] = None,
    dim_weights: Optional[Dict[str, float]] = None,
    sub_weights: Optional[Dict[str, Dict[str, float]]] = None,
    strict_json: bool = True,
) -> PlanRubricResult:
    """
    Evaluate a plan using the 5-dimension rubric.

    - Primary path: use an independent evaluator LLM (Kimi) to score subcriteria and provide evidence.
    - Fallback path: deterministic, conservative rule-only scoring if evaluator is unavailable.

    Output is always English (textual feedback/evidence).
    """
    evaluated_at = datetime.utcnow().isoformat() + "Z"
    rule_evidence = _build_rule_evidence(tree)
    outline = _build_plan_outline_for_eval(tree)
    rubric = rubric_definition_en()

    client = evaluator_client
    if client is None:
        try:
            client = LLMClient(provider=evaluator_provider, model=evaluator_model)
        except (TypeError, ValueError) as exc:
            logger.warning("Rubric evaluator client init failed: %s", exc)
            client = None

    # If provider is kimi, enforce URL presence; otherwise LLMClient will error at call time.
    if client is None or not getattr(client, "api_key", None) or not getattr(client, "url", None):
        sub_scores, evidence, rule_only = _fallback_rule_only_scores(tree)
        dim_scores, overall = _aggregate_scores(
            sub_scores, dim_weights=dim_weights, sub_weights=sub_weights
        )
        return PlanRubricResult(
            plan_id=tree.id,
            rubric_version=RUBRIC_VERSION,
            evaluator_provider=evaluator_provider,
            evaluator_model=evaluator_model or "unavailable",
            evaluated_at=evaluated_at,
            overall_score=overall,
            dimension_scores=dim_scores,
            subcriteria_scores=sub_scores,
            evidence=evidence,
            feedback={
                "strengths": [],
                "weaknesses": [
                    "Evaluator model unavailable; produced a conservative rule-only estimate."
                ],
                "actionable_revisions": [
                    "Configure evaluator provider credentials (KIMI_API_KEY/KIMI_API_URL/KIMI_MODEL) to enable strict rubric scoring.",
                    "If your deployment hosts Kimi models behind a Qwen/DashScope OpenAI-compatible endpoint, ensure the runtime provides QWEN_API_KEY/QWEN_API_URL plus a Kimi model name (e.g., QWEN_KIMI_MODEL_NEW).",
                ],
            },
            rule_evidence=rule_only,
        )

    # Build strict JSON schema prompt (English only)
    rubric_json = json.dumps(rubric, ensure_ascii=False, indent=2)
    evidence_json = json.dumps(rule_evidence, ensure_ascii=False, indent=2)

    schema = {
        "rubric_version": RUBRIC_VERSION,
        "plan_id": tree.id,
        "subcriteria_scores": {
            dim: {k: 0.0 for k in data["subcriteria"].keys()} for dim, data in rubric.items()
        },
        "evidence": {
            dim: {k: ["evidence string"] for k in data["subcriteria"].keys()}
            for dim, data in rubric.items()
        },
        "feedback": {
            "strengths": ["..."],
            "weaknesses": ["..."],
            "actionable_revisions": ["..."],
        },
    }

    strict_rules = (
        "- Return ONLY valid JSON. No Markdown, no code fences, no trailing commas.\n"
        "- All numeric subcriteria scores MUST be in [0.0, 1.0]. Use increments of 0.05.\n"
        "- Be strict: do not give high scores without explicit evidence.\n"
        "- Evidence strings must reference concrete tasks using '[task_id]' whenever possible.\n"
        "- Output text fields in English.\n"
    )
    if not strict_json:
        strict_rules = "- Return JSON."

    prompt = f"""You are a strict research-plan quality evaluator.

You must evaluate the plan using the provided rubric with 5 dimensions and 5 subcriteria each.
Your job is to score each subcriterion and provide concrete evidence.

## Rubric (English)
{rubric_json}

## Plan Outline
{outline}

## Deterministic Signals (for calibration; do not blindly copy)
{evidence_json}

## Output JSON Schema (must match exactly)
{json.dumps(schema, ensure_ascii=False, indent=2)}

## Rules
{strict_rules}
"""

    raw = ""
    parsed: Optional[Dict[str, Any]] = None
    try:
        raw = client.chat(
            "",
            messages=[{"role": "user", "content": prompt}],
            model=evaluator_model,
        )
        parsed = _extract_json_block(raw) or None
    except Exception as exc:  # noqa: BLE001 - isolate evaluator failures
        logger.warning("Rubric evaluator call failed: %s", exc)
        parsed = None

    if not isinstance(parsed, dict):
        sub_scores, evidence, rule_only = _fallback_rule_only_scores(tree)
        dim_scores, overall = _aggregate_scores(
            sub_scores, dim_weights=dim_weights, sub_weights=sub_weights
        )
        return PlanRubricResult(
            plan_id=tree.id,
            rubric_version=RUBRIC_VERSION,
            evaluator_provider=str(getattr(client, "provider", evaluator_provider)),
            evaluator_model=str(getattr(client, "model", evaluator_model or "unknown")),
            evaluated_at=evaluated_at,
            overall_score=overall,
            dimension_scores=dim_scores,
            subcriteria_scores=sub_scores,
            evidence=evidence,
            feedback={
                "strengths": [],
                "weaknesses": [
                    "Evaluator returned an invalid response; produced a conservative rule-only estimate."
                ],
                "actionable_revisions": [
                    "Retry evaluation with a stable evaluator model outputting strict JSON."
                ],
            },
            rule_evidence=rule_only,
        )

    # Validate & normalize
    incoming_sub = parsed.get("subcriteria_scores") if isinstance(parsed, dict) else None
    incoming_evidence = parsed.get("evidence") if isinstance(parsed, dict) else None
    incoming_feedback = parsed.get("feedback") if isinstance(parsed, dict) else None

    sub_scores: RubricScores = {}
    evidence: RubricEvidence = {}
    for dim, dim_data in rubric.items():
        keys = list((dim_data.get("subcriteria") or {}).keys())
        dim_map: Dict[str, float] = {}
        ev_map: Dict[str, List[str]] = {}
        source_dim = incoming_sub.get(dim, {}) if isinstance(incoming_sub, dict) else {}
        source_ev = incoming_evidence.get(dim, {}) if isinstance(incoming_evidence, dict) else {}
        for k in keys:
            dim_map[k] = round(_clamp(_safe_float((source_dim or {}).get(k), 0.0), 0.0, 1.0), 3)
            ev_list = (source_ev or {}).get(k)
            if isinstance(ev_list, list):
                ev_map[k] = [str(x)[:240] for x in ev_list[:6] if str(x).strip()]
            else:
                ev_map[k] = []
        sub_scores[dim] = dim_map
        evidence[dim] = ev_map

    dim_scores, overall = _aggregate_scores(
        sub_scores, dim_weights=dim_weights, sub_weights=sub_weights
    )

    feedback: Dict[str, Any] = {"strengths": [], "weaknesses": [], "actionable_revisions": []}
    if isinstance(incoming_feedback, dict):
        for key in ("strengths", "weaknesses", "actionable_revisions"):
            items = incoming_feedback.get(key)
            if isinstance(items, list):
                feedback[key] = [str(x)[:240] for x in items[:8] if str(x).strip()]

    return PlanRubricResult(
        plan_id=tree.id,
        rubric_version=RUBRIC_VERSION,
        evaluator_provider=str(getattr(client, "provider", evaluator_provider)),
        evaluator_model=str(getattr(client, "model", evaluator_model or "unknown")),
        evaluated_at=evaluated_at,
        overall_score=overall,
        dimension_scores=dim_scores,
        subcriteria_scores=sub_scores,
        evidence=evidence,
        feedback=feedback,
        rule_evidence=rule_evidence,
    )

