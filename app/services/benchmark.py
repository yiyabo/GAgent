#!/usr/bin/env python3
"""
Benchmark service: generate reports under different configurations and
evaluate them using the built-in LLM evaluation, returning metrics and
markdown summary.
"""

from typing import Dict, List, Tuple, Any, Optional
import os
import csv

from ..repository.tasks import default_repo
from ..database import init_db
from ..execution.executors.enhanced import execute_task_with_llm_evaluation


def _parse_config_string(cfg: str) -> Tuple[str, Dict[str, str]]:
    parts = [p.strip() for p in str(cfg).split(',') if p and p.strip()]
    if not parts:
        return ("default", {})
    name = parts[0]
    kv: Dict[str, str] = {}
    for p in parts[1:]:
        if '=' in p:
            k, v = p.split('=', 1)
            kv[k.strip()] = v.strip()
    return name, kv


def _to_bool(val: Any, default: bool=False) -> bool:
    s = str(val).strip().lower()
    if s in {"true","1","yes","on"}: return True
    if s in {"false","0","no","off"}: return False
    return default


def _to_int(val: Any, default: int) -> int:
    try:
        return int(val)
    except Exception:
        return default


def _create_report_tasks(topic: str, sections: int) -> List[int]:
    titles = [
        f"[{topic}] 背景与现状",
        f"[{topic}] 数据与方法",
        f"[{topic}] 关键发现",
        f"[{topic}] 风险与局限",
        f"[{topic}] 建议与展望",
    ][:max(1, sections)]

    ids: List[int] = []
    for idx, title in enumerate(titles, 1):
        tid = default_repo.create_task(name=title, status="pending", priority=idx*10, task_type="atomic")
        prompt = (
            f"请撰写主题《{topic}》的分析报告章节：{title}.\n"
            "要求：客观、结构清晰、含数据/指标/案例，500-800字。"
        )
        default_repo.upsert_task_input(tid, prompt)
        ids.append(tid)
    return ids


def _accumulate_dimensions(dim_sums: Dict[str, float], dims: Any) -> None:
    for key in ["relevance","completeness","accuracy","clarity","coherence","scientific_rigor"]:
        val = getattr(dims, key, None)
        if isinstance(val, (int, float)):
            dim_sums[key] = dim_sums.get(key, 0.0) + float(val)


def _avg_dimensions(dim_sums: Dict[str, float], count: int) -> Dict[str, float]:
    if count <= 0:
        return {k: 0.0 for k in ["relevance","completeness","accuracy","clarity","coherence","scientific_rigor"]}
    return {k: round(v / count, 3) for k, v in dim_sums.items()}


def run_benchmark(
    topic: str,
    config_specs: List[str],
    sections: int = 5,
    outdir: Optional[str] = None,
    csv_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Run benchmark for given topic and config specs.

    Returns dict with metrics per config and markdown summary.
    """
    init_db()

    # Prepare tasks
    task_ids = _create_report_tasks(topic, sections)

    configs: List[Tuple[str, Dict[str, str]]] = [_parse_config_string(c) for c in config_specs]
    results: Dict[str, Dict[str, Any]] = {}
    files: Dict[str, str] = {}

    if outdir:
        try:
            os.makedirs(outdir, exist_ok=True)
        except Exception:
            pass

    for name, kv in configs:
        use_context = _to_bool(kv.get('use_context', 'false'), default=False)
        max_iters = _to_int(kv.get('max_iterations', '3'), 3)
        try:
            quality_threshold = float(kv.get('quality_threshold', '0.8'))
        except Exception:
            quality_threshold = 0.8

        ctx_opts = None
        if use_context:
            ctx_opts = {}
            if 'max_chars' in kv:
                ctx_opts['max_chars'] = _to_int(kv['max_chars'], 6000)
            if 'per_section_max' in kv:
                ctx_opts['per_section_max'] = _to_int(kv['per_section_max'], 1200)
            if 'strategy' in kv:
                ctx_opts['strategy'] = kv['strategy']
            # Extended context toggles
            if 'include_deps' in kv:
                ctx_opts['include_deps'] = _to_bool(kv['include_deps'], True)
            if 'include_plan' in kv:
                ctx_opts['include_plan'] = _to_bool(kv['include_plan'], True)
            if 'include_ancestors' in kv:
                ctx_opts['include_ancestors'] = _to_bool(kv['include_ancestors'], False)
            if 'include_siblings' in kv:
                ctx_opts['include_siblings'] = _to_bool(kv['include_siblings'], False)
            if 'semantic_k' in kv:
                try:
                    ctx_opts['semantic_k'] = int(kv['semantic_k'])
                except Exception:
                    ctx_opts['semantic_k'] = 5
            if 'min_similarity' in kv:
                try:
                    ctx_opts['min_similarity'] = float(kv['min_similarity'])
                except Exception:
                    ctx_opts['min_similarity'] = 0.1

        total_score = 0.0
        total_iters = 0
        total_time = 0.0
        count = 0
        failures = 0
        dim_sums: Dict[str, float] = {}

        # Collect sections for per-config markdown export
        collected_sections: List[Tuple[str, str]] = []

        for tid in task_ids:
            task = default_repo.get_task_info(tid)
            if not task:
                failures += 1
                continue
            try:
                result = execute_task_with_llm_evaluation(
                    task=task,
                    repo=default_repo,
                    max_iterations=max_iters,
                    quality_threshold=quality_threshold,
                    use_context=use_context,
                    context_options=ctx_opts
                )
                if result.evaluation:
                    total_score += float(result.evaluation.overall_score)
                    _accumulate_dimensions(dim_sums, result.evaluation.dimensions)
                if isinstance(result.iterations, int):
                    total_iters += result.iterations
                if isinstance(result.execution_time, (int, float)):
                    total_time += float(result.execution_time)
                count += 1
                # Capture content for export
                try:
                    sec_title = str(task.get('name', f'Section {tid}'))
                    sec_content = str(result.content or "")
                    collected_sections.append((sec_title, sec_content))
                except Exception:
                    pass
            except Exception:
                failures += 1

        avg_score = (total_score / count) if count else 0.0
        avg_iters = (total_iters / count) if count else 0.0
        avg_time = (total_time / count) if count else 0.0
        dim_avgs = _avg_dimensions(dim_sums, count)

        # Write per-config markdown file if requested
        if outdir and collected_sections:
            safe_name = "".join(c for c in name if c.isalnum() or c in ('-','_')).strip() or name
            md_path = os.path.join(outdir, f"{safe_name}.md")
            try:
                with open(md_path, 'w', encoding='utf-8') as f:
                    f.write(f"# {topic}（{name}）\n\n")
                    for sec_title, sec_content in collected_sections:
                        # 去掉主题前缀的展示更友好
                        display_title = sec_title.split('] ', 1)[-1] if sec_title.startswith('[') and '] ' in sec_title else sec_title
                        f.write(f"## {display_title}\n\n{sec_content}\n\n")
                files[name] = md_path
            except Exception:
                files[name] = md_path

        results[name] = {
            "params": kv,
            "avg_score": round(avg_score, 3),
            "avg_iters": round(avg_iters, 2),
            "avg_time": round(avg_time, 2),
            "failures": failures,
            "count": count,
            "dimensions_avg": dim_avgs,
            "file_path": files.get(name)
        }

    # Render summary markdown
    lines: List[str] = []
    lines.append(f"# LLM 配置基准报告: {topic}")
    lines.append("")
    lines.append("| 配置 | 平均分 | 平均迭代 | 平均耗时(s) | 成功数 | 失败数 | relevance | completeness | accuracy | clarity | coherence | scientific_rigor |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for name, _ in configs:
        m = results.get(name, {})
        dims = m.get("dimensions_avg", {})
        lines.append(
            f"| {name} | {m.get('avg_score',0):.3f} | {m.get('avg_iters',0):.2f} | {m.get('avg_time',0):.2f} | "
            f"{m.get('count',0)} | {m.get('failures',0)} | "
            f"{dims.get('relevance',0):.3f} | {dims.get('completeness',0):.3f} | {dims.get('accuracy',0):.3f} | "
            f"{dims.get('clarity',0):.3f} | {dims.get('coherence',0):.3f} | {dims.get('scientific_rigor',0):.3f} |"
        )
    lines.append("")
    lines.append("> 评分维度来自系统内置 LLM 评估器（相关性/完整性/准确性/清晰度/连贯性/科学严谨性），overall_score 为加权结果。")

    # Optional CSV export (per-config rows)
    if csv_path:
        try:
            parent = os.path.dirname(csv_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(csv_path, 'w', encoding='utf-8', newline='') as csvfile:
                fieldnames = [
                    'config_name','params','file_path','avg_score','avg_iters','avg_time','count','failures',
                    'relevance','completeness','accuracy','clarity','coherence','scientific_rigor'
                ]
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
                for name, m in results.items():
                    dims = m.get('dimensions_avg', {})
                    writer.writerow({
                        'config_name': name,
                        'params': str(m.get('params', {})),
                        'file_path': m.get('file_path') or '',
                        'avg_score': m.get('avg_score', 0.0),
                        'avg_iters': m.get('avg_iters', 0.0),
                        'avg_time': m.get('avg_time', 0.0),
                        'count': m.get('count', 0),
                        'failures': m.get('failures', 0),
                        'relevance': dims.get('relevance', 0.0),
                        'completeness': dims.get('completeness', 0.0),
                        'accuracy': dims.get('accuracy', 0.0),
                        'clarity': dims.get('clarity', 0.0),
                        'coherence': dims.get('coherence', 0.0),
                        'scientific_rigor': dims.get('scientific_rigor', 0.0),
                    })
        except Exception:
            pass

    return {
        "topic": topic,
        "configs": [{"name": n, "params": p} for n, p in configs],
        "metrics": results,
        "summary_md": "\n".join(lines),
        "files": files,
        "csv_path": csv_path,
    }


