#!/usr/bin/env python3
"""Pipeline benchmark runner: execute the whole task suite via driver.py.

Run inside the phage-agent container with the repo at /app:

    python3 /app/evals/pipeline_benchmark/runner.py --out /app/data/evals/before
    python3 /app/evals/pipeline_benchmark/runner.py --out /app/data/evals/after --only t01,t07,t19

Per task the runner:
  1. spawns driver.py (literal argv, proxy-stripped env) on a fresh work root,
  2. links the runtime session dir into the result dir as `session/`,
  3. copies the task's check.py in ONLY AFTER the agent finished (the agent
     under test never sees the grading logic) and runs it
     (exit 0 = pass, 3 = netfail, anything else = fail),
  4. sums tokens for the task's unique session_id from llm_usage_log.

Token attribution: llm_usage_log lives in the main app database —
$DB_ROOT/main/plan_registry.db (default data/databases/main/plan_registry.db,
i.e. /app/data/databases/main/plan_registry.db in the container). The driver
calls app.database.init_db() so its rows land in that same file. If no
database with an llm_usage_log table is found, tokens are recorded as null.

Outputs in --out: results.jsonl (one line per task), report.md (per-category
tables + aggregate metrics), and one subdirectory per task holding
result.json, the check.py copy, the session/ link and the driver stdout tail.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
DRIVER = BASE / "driver.py"
DEFAULT_TASKS = BASE / "tasks"
CHECK_TIMEOUT = 60
NETFAIL_RC = 3


def build_env(timeout_scale: float) -> dict:
    env = {k: v for k, v in os.environ.items() if "proxy" not in k.lower()}
    env["BENCH_TIMEOUT_SCALE"] = str(timeout_scale)
    return env


def find_usage_db() -> Path | None:
    candidates = []
    explicit = os.environ.get("LLM_USAGE_DB")
    if explicit:
        candidates.append(Path(explicit))
    db_root = os.environ.get("DB_ROOT")
    if db_root:
        candidates.append(Path(db_root) / "main" / "plan_registry.db")
    candidates.append(Path("data/databases/main/plan_registry.db"))
    candidates.append(Path("/app/data/databases/main/plan_registry.db"))
    candidates.append(Path("/app/tasks.db"))
    candidates.append(Path("tasks.db"))
    for cand in candidates:
        try:
            if cand.is_file():
                return cand.resolve()
        except OSError:
            continue
    return None


def open_usage_db(db_path: Path | None) -> sqlite3.Connection | None:
    if db_path is None:
        return None
    try:
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='llm_usage_log'"
        ).fetchone()
        if not row:
            conn.close()
            return None
        return conn
    except sqlite3.Error:
        return None


def sum_tokens(conn: sqlite3.Connection | None, session_id: str) -> dict:
    empty = {"tokens": None, "prompt_tokens": None, "completion_tokens": None, "llm_calls": None}
    if not conn or not session_id:
        return empty
    try:
        row = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(total_tokens),0), COALESCE(SUM(prompt_tokens),0), "
            "COALESCE(SUM(completion_tokens),0) FROM llm_usage_log WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    except sqlite3.Error:
        return empty
    if not row or not row[0]:
        return empty
    return {"tokens": int(row[1]), "prompt_tokens": int(row[2]),
            "completion_tokens": int(row[3]), "llm_calls": int(row[0])}


def link_session_dir(work_root: Path, result_dir: Path, session_id: str) -> str:
    runtime = work_root / "runtime"
    target = None
    if session_id:
        exact = runtime / f"session_{session_id}"
        if exact.is_dir():
            target = exact
    if target is None and runtime.is_dir():
        candidates = sorted((p for p in runtime.glob("session_*") if p.is_dir()),
                            key=lambda p: p.stat().st_mtime)
        if candidates:
            target = candidates[-1]
    if target is None:
        return ""
    link = result_dir / "session"
    try:
        if link.is_symlink() or link.exists():
            if link.is_dir() and not link.is_symlink():
                shutil.rmtree(link)
            else:
                link.unlink()
        os.symlink(target, link)
        return "symlink"
    except OSError:
        try:
            shutil.copytree(target, link, dirs_exist_ok=True)
            return "copy"
        except OSError:
            return ""


def run_check(task_dir: Path, result_dir: Path) -> tuple[int, str]:
    shutil.copy(task_dir / "check.py", result_dir / "check.py")
    try:
        proc = subprocess.run(
            ["python3", "check.py", str(result_dir)],
            cwd=str(result_dir), capture_output=True, text=True,
            timeout=CHECK_TIMEOUT, shell=False,
        )
        out = ((proc.stderr or "") + "\n" + (proc.stdout or "")).strip()
        return proc.returncode, out[-300:]
    except subprocess.TimeoutExpired:
        return 1, "check timeout"


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    data = sorted(values)
    rank = (len(data) - 1) * pct
    low = int(rank)
    high = min(low + 1, len(data) - 1)
    frac = rank - low
    return round(data[low] + (data[high] - data[low]) * frac, 2)


def discover_tasks(tasks_dir: Path, only: set[str] | None) -> list[Path]:
    dirs = sorted(p for p in tasks_dir.iterdir()
                  if p.is_dir() and (p / "task.md").exists() and (p / "meta.json").exists())
    if only:
        dirs = [p for p in dirs if p.name.split("_")[0] in only]
    return dirs


def run_one(task_dir: Path, out_dir: Path, env: dict, timeout_scale: float,
            usage_conn: sqlite3.Connection | None) -> dict:
    name = task_dir.name
    meta = json.loads((task_dir / "meta.json").read_text(encoding="utf-8"))
    task_timeout = float(meta.get("timeout_seconds") or 600) * timeout_scale
    work_root = out_dir / "_work" / name
    result_dir = out_dir / name
    if work_root.exists():
        shutil.rmtree(work_root)
    if result_dir.exists():
        shutil.rmtree(result_dir)
    work_root.mkdir(parents=True)
    result_dir.mkdir(parents=True)
    result_json = result_dir / "result.json"

    t0 = time.monotonic()
    driver_note = ""
    try:
        proc = subprocess.run(
            ["python3", str(DRIVER), "--task", str(task_dir),
             "--work-root", str(work_root), "--out", str(result_json)],
            capture_output=True, text=True, timeout=task_timeout + 120, shell=False, env=env,
        )
        tail = ((proc.stdout or "") + "\n--- stderr ---\n" + (proc.stderr or "")).strip()
    except subprocess.TimeoutExpired:
        tail = ""
        driver_note = f"driver subprocess timeout after {task_timeout + 120:.0f}s"
    (result_dir / "driver_stdout.txt").write_text(tail[-4000:], encoding="utf-8")

    if result_json.is_file():
        try:
            record = json.loads(result_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            record = {"task": name, "session_id": None, "ok_run": False,
                      "error": f"corrupt result.json: {exc}"}
    else:
        record = {"task": name, "session_id": None, "ok_run": False,
                  "seconds": round(time.monotonic() - t0, 2),
                  "error": driver_note or "driver produced no result.json"}
    if driver_note and not record.get("error"):
        record["error"] = driver_note

    link_kind = link_session_dir(work_root, result_dir, str(record.get("session_id") or ""))
    check_rc, check_out = run_check(task_dir, result_dir)
    usage = sum_tokens(usage_conn, str(record.get("session_id") or ""))

    return {
        "task": name,
        "category": meta.get("category"),
        "ok": check_rc == 0,
        "netfail": check_rc == NETFAIL_RC,
        "ok_run": bool(record.get("ok_run")),
        "seconds": record.get("seconds"),
        **usage,
        "fallback_used": record.get("fallback_used"),
        "iterations": record.get("total_iterations"),
        "tools_used": record.get("tools_used") or [],
        "n_produced": len(record.get("produced_paths") or []),
        "acceptance_missing": record.get("acceptance_missing") or [],
        "session_link": link_kind,
        "check_out": check_out,
        "error": record.get("error"),
    }


def render_report(rows: list[dict], out_dir: Path, total_seconds: float,
                  usage_db: Path | None, timeout_scale: float) -> str:
    total = len(rows)
    passed = sum(1 for r in rows if r["ok"])
    netfails = sum(1 for r in rows if r["netfail"])
    effective = total - netfails
    ran = [r for r in rows if r["ok_run"]]
    secs = [float(r["seconds"]) for r in rows if isinstance(r["seconds"], (int, float))]
    fb = [r for r in ran if r["fallback_used"]]
    produced = [r for r in rows if r["n_produced"] > 0]
    tokens = [r["tokens"] for r in rows if isinstance(r["tokens"], int)]
    lines = [
        "# Pipeline benchmark report",
        "",
        f"- out_dir: `{out_dir}`",
        f"- generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- timeout_scale: {timeout_scale}",
        f"- usage db: `{usage_db}`" if usage_db else "- usage db: NOT FOUND (tokens = null)",
        f"- tasks: {total}, passed: {passed}, netfail: {netfails}",
        "",
        "## Aggregate metrics",
        "",
        "| metric | value |",
        "|---|---|",
        f"| success rate (excl. netfail) | {passed}/{effective} = {(passed / effective * 100 if effective else 0):.1f}% |",
        f"| ok_run rate | {len(ran)}/{total} = {(len(ran) / total * 100 if total else 0):.1f}% |",
        f"| mean seconds | {(sum(secs) / len(secs) if secs else 0):.1f} |",
        f"| p50 seconds | {percentile(secs, 0.50)} |",
        f"| p95 seconds | {percentile(secs, 0.95)} |",
        f"| fallback rate | {len(fb)}/{len(ran)} = {(len(fb) / len(ran) * 100 if ran else 0):.1f}% |",
        f"| deliverable rate (n_produced>0) | {len(produced)}/{total} = {(len(produced) / total * 100 if total else 0):.1f}% |",
        f"| total tokens | {sum(tokens) if tokens else 'n/a'} |",
        f"| wall seconds | {total_seconds:.0f} |",
        "",
        "## Results by category",
        "",
    ]
    categories = sorted({str(r["category"]) for r in rows})
    for cat in categories:
        cat_rows = [r for r in rows if str(r["category"]) == cat]
        cat_pass = sum(1 for r in cat_rows if r["ok"])
        cat_nf = sum(1 for r in cat_rows if r["netfail"])
        lines += [
            f"### {cat} — {cat_pass}/{len(cat_rows) - cat_nf} passed"
            + (f" ({cat_nf} netfail)" if cat_nf else ""),
            "",
            "| task | ok | ok_run | seconds | tokens | fallback | iters | n_prod | note |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for r in cat_rows:
            if r["ok"]:
                mark = "PASS"
            elif r["netfail"]:
                mark = "NETFAIL"
            else:
                mark = "FAIL"
            note = (r["error"] or r["check_out"] or "").replace("|", "/").replace("\n", " ")[:80]
            lines.append(
                f"| {r['task']} | {mark} | {r['ok_run']} | {r['seconds']} | "
                f"{r['tokens'] if r['tokens'] is not None else 'n/a'} | {r['fallback_used']} | "
                f"{r['iterations']} | {r['n_produced']} | {note} |"
            )
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the pipeline benchmark suite")
    ap.add_argument("--out", required=True, help="output directory for results + report")
    ap.add_argument("--tasks", default=str(DEFAULT_TASKS), help="tasks directory")
    ap.add_argument("--only", default="", help="comma-separated task prefixes, e.g. t01,t07")
    ap.add_argument("--timeout-scale", type=float, default=1.0,
                    help="multiplies every task timeout_seconds")
    args = ap.parse_args()

    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    only = {tok.strip() for tok in args.only.split(",") if tok.strip()} or None
    task_dirs = discover_tasks(Path(args.tasks).resolve(), only)
    if not task_dirs:
        sys.exit("no tasks found")
    env = build_env(args.timeout_scale)
    usage_db = find_usage_db()
    usage_conn = open_usage_db(usage_db)
    print(f"tasks={len(task_dirs)} usage_db={usage_db if usage_conn else 'unavailable'}", flush=True)

    rows = []
    suite_t0 = time.monotonic()
    for task_dir in task_dirs:
        print(f"[run] {task_dir.name} ...", flush=True)
        row = run_one(task_dir, out_dir, env, args.timeout_scale, usage_conn)
        rows.append(row)
        mark = "PASS" if row["ok"] else ("NETFAIL" if row["netfail"] else "FAIL")
        print(f"[{mark}] {row['task']} ok_run={row['ok_run']} {row['seconds']}s "
              f"tokens={row['tokens']} fallback={row['fallback_used']}", flush=True)
        with (out_dir / "results.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    total_seconds = time.monotonic() - suite_t0
    if usage_conn:
        usage_conn.close()

    report = render_report(rows, out_dir, total_seconds,
                           usage_db if usage_conn else None, args.timeout_scale)
    (out_dir / "report.md").write_text(report, encoding="utf-8")
    passed = sum(1 for r in rows if r["ok"])
    ran = [r for r in rows if r["ok_run"]]
    fb_rate = (sum(1 for r in ran if r["fallback_used"]) / len(ran)) if ran else 0.0
    print(f"DONE {passed}/{len(rows)} passed, {total_seconds:.0f}s, fallback_rate={fb_rate:.2f}",
          flush=True)


if __name__ == "__main__":
    main()
