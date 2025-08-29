#!/usr/bin/env python3
"""
Benchmark LLM performance by generating reports under different configurations
and evaluating them using the built-in evaluation system.

Usage:
  conda run -n LLM python scripts/benchmark_reports.py --topic "抗菌素耐药年度报告" \
    --configs "base,use_context=False" "ctx_small,use_context=True,max_chars=3000,per_section_max=600" \
    --sections 5 --output benchmark_results.md

Notes:
- Requires environment: conda activate LLM (or use conda run as above)
- Respects LLM_MOCK=1 for offline testing
"""

import argparse
import os
import sys
from typing import Dict, List, Tuple

# Ensure app in path
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from app.repository.tasks import default_repo
from app.execution.executors.enhanced import execute_task_with_llm_evaluation
from app.database import init_db


def parse_config_string(cfg: str) -> Tuple[str, Dict[str, str]]:
    """Parse a config spec like: "ctx_small,use_context=True,max_chars=3000"
    Returns (name, kv_dict)
    """
    parts = [p.strip() for p in cfg.split(',') if p.strip()]
    if not parts:
        return ("default", {})
    name = parts[0]
    kv: Dict[str, str] = {}
    for p in parts[1:]:
        if '=' in p:
            k, v = p.split('=', 1)
            kv[k.strip()] = v.strip()
    return name, kv


def to_bool(val: str, default: bool=False) -> bool:
    v = str(val).strip().lower()
    if v in {"true","1","yes","on"}: return True
    if v in {"false","0","no","off"}: return False
    return default


def to_int(val: str, default: int) -> int:
    try:
        return int(val)
    except Exception:
        return default


def create_report_tasks(topic: str, sections: int) -> List[int]:
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


def run_config_on_tasks(cfg_name: str, kv: Dict[str,str], task_ids: List[int]) -> Dict[str, float]:
    """Run generation+LLM-eval with a config and return metrics.
    Returns dict: { 'avg_score': float, 'avg_iters': float, 'failures': int }
    """
    use_context = to_bool(kv.get('use_context', 'false'), default=False)
    max_iters = to_int(kv.get('max_iterations', '3'), 3)
    quality_threshold = float(kv.get('quality_threshold', '0.8'))

    # Context options (budget defaults already in executor for use_context=True)
    ctx_opts = None
    if use_context:
        ctx_opts = {}
        if 'max_chars' in kv:
            ctx_opts['max_chars'] = to_int(kv['max_chars'], 6000)
        if 'per_section_max' in kv:
            ctx_opts['per_section_max'] = to_int(kv['per_section_max'], 1200)
        if 'strategy' in kv:
            ctx_opts['strategy'] = kv['strategy']

    total_score = 0.0
    total_iters = 0
    count = 0
    failures = 0

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
            total_iters += int(result.iterations)
            count += 1
        except Exception:
            failures += 1

    avg_score = (total_score / count) if count else 0.0
    avg_iters = (total_iters / count) if count else 0.0
    return {"avg_score": round(avg_score, 3), "avg_iters": round(avg_iters, 2), "failures": failures, "count": count}


def render_summary(topic: str, configs: List[Tuple[str, Dict[str,str]]], metrics: Dict[str, Dict[str, float]]) -> str:
    lines: List[str] = []
    lines.append(f"# LLM 配置基准报告: {topic}")
    lines.append("")
    lines.append("| 配置 | 平均分 | 平均迭代 | 成功数 | 失败数 |")
    lines.append("|---|---:|---:|---:|---:|")
    for name, _ in configs:
        m = metrics.get(name, {"avg_score":0.0,"avg_iters":0.0,"failures":0,"count":0})
        lines.append(f"| {name} | {m['avg_score']:.3f} | {m['avg_iters']:.2f} | {m['count']} | {m['failures']} |")
    lines.append("")
    lines.append("> 注：评分维度来自系统内置 LLM 评估器（相关性/完整性/准确性/清晰度/连贯性/科学严谨性），overall_score 为加权结果。")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="基于不同配置生成报告并进行LLM评分的基准工具")
    parser.add_argument("--topic", required=True, help="报告主题")
    parser.add_argument("--configs", nargs='+', required=True, help="配置列表，如 'base,use_context=False' 'ctx_small,use_context=True,max_chars=3000' 等")
    parser.add_argument("--sections", type=int, default=5, help="章节/任务数")
    parser.add_argument("--output", type=str, default="benchmark_results.md", help="输出汇总Markdown")
    args = parser.parse_args()

    init_db()

    # Prepare tasks once per run
    task_ids = create_report_tasks(args.topic, args.sections)

    # Parse configs
    configs: List[Tuple[str, Dict[str,str]]] = [parse_config_string(c) for c in args.configs]

    # Run each config
    metrics: Dict[str, Dict[str, float]] = {}
    for name, kv in configs:
        print(f"\n=== 运行配置: {name} ===")
        print(f"参数: {kv}")
        m = run_config_on_tasks(name, kv, task_ids)
        print(f"结果: 平均分={m['avg_score']:.3f} 平均迭代={m['avg_iters']:.2f} 成功数={m['count']} 失败数={m['failures']}")
        metrics[name] = m

    # Render summary
    summary = render_summary(args.topic, configs, metrics)
    with open(args.output, 'w', encoding='utf-8') as f:
        f.write(summary)
    print(f"\n✅ 基准报告已生成: {args.output}")


if __name__ == "__main__":
    sys.exit(main())
