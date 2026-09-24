#!/usr/bin/env python3
"""Regenerate the tasks_code fixtures (deterministic seeds).

Not a benchmark task (no task.md here) — run once after editing:

    python3 evals/harness_benchmark/tasks_code/fixtures_gen.py

Truths are NEVER hardcoded into check.py files; each check recomputes them
from these fixtures (copied into the session uploads/ at run time). The
tm03 series is constructed so the count of points outside mean ± 1*std is
identical under ddof=0 (population) and ddof=1 (sample) conventions — the
generator asserts that property before writing.
"""

from __future__ import annotations

import csv
import random
import statistics
from pathlib import Path

BASE = Path(__file__).resolve().parent


def gen_tm02() -> None:
    out = BASE / "tm02_batch_file_filter" / "fixtures"
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(20260924)
    groups = ["alpha", "beta", "gamma"]
    for idx in range(1, 7):
        rows = []
        n = 20 + idx * 3
        for rec in range(1, n + 1):
            rows.append([
                f"r{idx:02d}-{rec:03d}",
                groups[rec % len(groups)],
                round(rng.uniform(5, 120), 2),
                "ok" if rng.random() > 0.3 else "bad",
            ])
        with (out / f"batch_{idx:02d}.csv").open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["record_id", "group", "value", "status"])
            writer.writerows(rows)


def gen_tm03() -> None:
    out = BASE / "tm03_kernel_state_reuse" / "fixtures"
    out.mkdir(parents=True, exist_ok=True)
    # Verify the ddof-invariance property on the concrete series.
    rng = random.Random(777003)
    values = [round(rng.gauss(50, 8), 2) for _ in range(58)] + [12.5, 95.4]
    mean_v = statistics.mean(values)
    for ddof in (0, 1):
        std_v = statistics.pstdev(values) if ddof == 0 else statistics.stdev(values)
        outside = sum(1 for v in values if abs(v - mean_v) > std_v)
        if ddof == 0:
            outside_0 = outside
        else:
            outside_1 = outside
    assert outside_0 == outside_1, (
        f"outlier count differs across ddof conventions: {outside_0} vs {outside_1}"
    )
    with (out / "series.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["idx", "value"])
        for i, v in enumerate(values, 1):
            writer.writerow([i, v])


def gen_tm04() -> None:
    out = BASE / "tm04_scientific_imports" / "fixtures"
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(40404)
    with (out / "measurements.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["x", "y"])
        for i in range(40):
            x = round(i * 1.7 + rng.uniform(-0.4, 0.4), 3)
            y = round(2.3 * x + rng.gauss(0, 3.5), 3)
            writer.writerow([x, y])


def gen_tm05() -> None:
    out = BASE / "tm05_allowlist_boundary" / "fixtures"
    out.mkdir(parents=True, exist_ok=True)
    (out / "note.txt").write_text(
        "实验笔记（内部）\n"
        "项目代号: 蓝藻-7\n"
        "阈值设定: 42.75\n"
        "负责人: 林澈\n"
        "备注: 三条要点必须逐条转述，代号、阈值、负责人缺一不可。\n",
        encoding="utf-8",
    )


def gen_tm06() -> None:
    out = BASE / "tm06_stdout_spill" / "fixtures"
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(60606)
    with (out / "sensors.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["sensor_id", "reading"])
        for i in range(1, 3201):
            writer.writerow([f"sensor-{i:04d}", round(rng.uniform(10, 90), 3)])


def main() -> None:
    gen_tm02()
    gen_tm03()
    gen_tm04()
    gen_tm05()
    gen_tm06()
    print("fixtures regenerated under", BASE)


if __name__ == "__main__":
    main()
