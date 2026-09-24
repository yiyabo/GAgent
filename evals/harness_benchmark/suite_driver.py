#!/usr/bin/env python3
"""Harness-benchmark suite driver: run the whole task set inside the container.

This file is SELF-CONTAINED (stdlib only) because it is piped into the
container by mac_runner.py over a single SSH invocation:

    ssh -o ControlPath=~/.ssh/cm-gagent8 root@10.110.107.8 \
        'docker exec -i -e HB_MANIFEST_B64=<base64-json> phage-agent python3 -' \
        < suite_driver.py

The manifest (env HB_MANIFEST_B64) carries
    {"label", "out_dir", "timeout_scale", "resume",
     "tasks": [{"name", "dir"} ...]}
where each dir is an in-container absolute path (/app/evals/...) holding
task.md + meta.json (+ fixtures/, check.py).

Per task the driver:
  1. spawns container_driver.py (literal argv, proxy stripping lives there)
     which drives the REAL chat-run chain (start_background_chat_run ->
     execute_chat_run worker) and writes result.json + events.jsonl,
  2. symlinks the runtime session dir into the result dir as `session/`
     (pipeline-benchmark check.py convention),
  3. copies the task's check.py in ONLY AFTER the run finished (the agent
     under test never sees the grading logic) and runs it
     (exit 0 = pass, 3 = netfail, anything else = fail),
  4. sums tokens for the task's unique session_id from llm_usage_log.

Outputs in out_dir: results.jsonl (one line per task), report.md
(per-category tables + aggregate metrics), and one subdirectory per task
holding result.json, events.jsonl, the check.py copy, the session/ link and
the driver stdout tail.

--print-plan mode (used by mac_runner --dry-run and local self-tests) validates
the manifest and every task directory WITHOUT running anything; it works with
local (non-/app) paths too.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

CONTAINER_DRIVER = "/app/evals/harness_benchmark/container_driver.py"
CHECK_TIMEOUT = 90
NETFAIL_RC = 3
DRIVER_GRACE_S = 240


# --------------------------------------------------------------------------
# manifest
# --------------------------------------------------------------------------

def load_manifest() -> dict:
    raw = os.environ.get("HB_MANIFEST_B64", "").strip()
    if not raw:
        sys.exit("HB_MANIFEST_B64 is not set — pipe via mac_runner.py")
    try:
        manifest = json.loads(base64.b64decode(raw).decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - manifest must be machine-readable
        sys.exit(f"corrupt HB_MANIFEST_B64: {exc!r}")
    if not isinstance(manifest.get("tasks"), list) or not manifest["tasks"]:
        sys.exit("manifest has no tasks")
    return manifest


def validate_manifest(manifest: dict) -> list:
    """Return a list of problems; empty means the suite can start."""
    problems = []
    for entry in manifest["tasks"]:
        name = str(entry.get("name") or "")
        tdir = Path(str(entry.get("dir") or ""))
        if not name:
            problems.append("task entry missing name")
            continue
        if not tdir.is_dir():
            problems.append(f"{name}: dir missing: {tdir}")
            continue
        if not (tdir / "task.md").is_file():
            problems.append(f"{name}: task.md missing")
        if not (tdir / "meta.json").is_file():
            problems.append(f"{name}: meta.json missing")
        else:
            try:
                meta = json.loads((tdir / "meta.json").read_text(encoding="utf-8"))
                if not meta.get("category"):
                    problems.append(f"{name}: meta.json missing category")
            except json.JSONDecodeError as exc:
                problems.append(f"{name}: meta.json corrupt: {exc}")
        if not (tdir / "check.py").is_file():
            problems.append(f"{name}: check.py missing")
    return problems


# --------------------------------------------------------------------------
# token attribution (same scheme as evals/pipeline_benchmark/runner.py)
# --------------------------------------------------------------------------

def find_usage_db() -> Path | None:
    candidates = []
    explicit = os.environ.get("HB_USAGE_DB") or os.environ.get("LLM_USAGE_DB")
    if explicit:
        candidates.append(Path(explicit))
    db_root = os.environ.get("DB_ROOT")
    if db_root:
        candidates.append(Path(db_root) / "main" / "plan_registry.db")
    candidates.append(Path("/app/data/databases/main/plan_registry.db"))
    candidates.append(Path("data/databases/main/plan_registry.db"))
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
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
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


# --------------------------------------------------------------------------
# per-task plumbing
# --------------------------------------------------------------------------

def link_session_dir(result_dir: Path, session_dir: str) -> str:
    if not session_dir:
        return ""
    target = Path(session_dir)
    if not target.is_dir():
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


def run_one(task: dict, out_dir: Path, timeout_scale: float,
            usage_conn: sqlite3.Connection | None) -> dict:
    name = task["name"]
    task_dir = Path(task["dir"])
    try:
        meta = json.loads((task_dir / "meta.json").read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"task": name, "category": None, "ok": False, "netfail": False,
                "ok_run": False, "seconds": None, "error": f"meta.json unreadable: {exc!r}"}
    category = str(meta.get("category") or "uncategorized")
    task_timeout = float(meta.get("timeout_seconds") or 600) * timeout_scale
    result_dir = out_dir / name
    if result_dir.exists():
        shutil.rmtree(result_dir)
    result_dir.mkdir(parents=True)
    result_json = result_dir / "result.json"

    env = dict(os.environ)
    env["HB_TIMEOUT_SCALE"] = str(timeout_scale)
    t0 = time.monotonic()
    driver_note = ""
    try:
        proc = subprocess.run(
            ["python3", CONTAINER_DRIVER, "--task", str(task_dir),
             "--out-dir", str(result_dir)],
            capture_output=True, text=True,
            timeout=task_timeout + DRIVER_GRACE_S, shell=False, env=env,
        )
        tail = ((proc.stdout or "") + "\n--- stderr ---\n" + (proc.stderr or "")).strip()
    except subprocess.TimeoutExpired:
        tail = ""
        driver_note = f"driver subprocess timeout after {task_timeout + DRIVER_GRACE_S:.0f}s"
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

    link_kind = link_session_dir(result_dir, str(record.get("session_dir") or ""))
    # Truth files (answer keys the agent must never see, e.g. external
    # snapshots) are staged into the result dir ONLY at check time; unlike
    # fixtures/ they are never copied into the session uploads.
    truth_src = task_dir / "truth"
    if truth_src.is_dir():
        shutil.copytree(truth_src, result_dir / "truth", dirs_exist_ok=True)
    check_rc, check_out = run_check(task_dir, result_dir)
    usage = sum_tokens(usage_conn, str(record.get("session_id") or ""))

    return {
        "task": name,
        "category": category,
        "ok": check_rc == 0,
        "netfail": check_rc == NETFAIL_RC,
        "ok_run": bool(record.get("ok_run")),
        "seconds": record.get("seconds"),
        **usage,
        "fallback_used": record.get("fallback_used"),
        "bailout_phrase": record.get("bailout_phrase"),
        "iterations": record.get("total_iterations"),
        "tools_used": record.get("tools_used") or [],
        "n_produced": len(record.get("produced_paths") or []),
        "session_id": record.get("session_id"),
        "session_link": link_kind,
        "check_out": check_out,
        "error": record.get("error"),
    }


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------

def render_report(rows: list[dict], out_dir: Path, total_seconds: float,
                  usage_db: Path | None, timeout_scale: float, label: str) -> str:
    total = len(rows)
    passed = sum(1 for r in rows if r["ok"])
    netfails = sum(1 for r in rows if r["netfail"])
    effective = total - netfails
    ran = [r for r in rows if r["ok_run"]]
    secs = [float(r["seconds"]) for r in rows if isinstance(r["seconds"], (int, float))]
    fb = [r for r in ran if r["fallback_used"]]
    bail = [r for r in ran if r["bailout_phrase"]]
    produced = [r for r in rows if (r.get("n_produced") or 0) > 0]
    tokens = [r["tokens"] for r in rows if isinstance(r.get("tokens"), int)]
    lines = [
        "# Harness benchmark report (chat-run chain)",
        "",
        f"- label: `{label}`",
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
        f"| fallback rate (final-event metadata) | {len(fb)}/{len(ran)} = {(len(fb) / len(ran) * 100 if ran else 0):.1f}% |",
        f"| bailout-phrase rate | {len(bail)}/{len(ran)} = {(len(bail) / len(ran) * 100 if ran else 0):.1f}% |",
        f"| deliverable rate (n_produced>0) | {len(produced)}/{total} = {(len(produced) / total * 100 if total else 0):.1f}% |",
        f"| total tokens | {sum(tokens) if tokens else 'n/a'} |",
        f"| wall seconds | {total_seconds:.0f} |",
        "",
        "## Results by category",
        "",
    ]
    categories = sorted({str(r.get("category")) for r in rows})
    for cat in categories:
        cat_rows = [r for r in rows if str(r.get("category")) == cat]
        cat_pass = sum(1 for r in cat_rows if r["ok"])
        cat_nf = sum(1 for r in cat_rows if r["netfail"])
        lines += [
            f"### {cat} — {cat_pass}/{len(cat_rows) - cat_nf} passed"
            + (f" ({cat_nf} netfail)" if cat_nf else ""),
            "",
            "| task | ok | ok_run | seconds | tokens | fallback | bailout | iters | n_prod | note |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
        for r in cat_rows:
            if r["ok"]:
                mark = "PASS"
            elif r["netfail"]:
                mark = "NETFAIL"
            else:
                mark = "FAIL"
            note = (r.get("error") or r.get("check_out") or "").replace("|", "/").replace("\n", " ")[:80]
            lines.append(
                f"| {r['task']} | {mark} | {r['ok_run']} | {r.get('seconds')} | "
                f"{r['tokens'] if r.get('tokens') is not None else 'n/a'} | "
                f"{r.get('fallback_used')} | {r.get('bailout_phrase')} | "
                f"{r.get('iterations')} | {r.get('n_produced')} | {note} |"
            )
        lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Run the harness-benchmark suite (in-container)")
    ap.add_argument("--print-plan", action="store_true",
                    help="validate manifest + task dirs, print the plan, run nothing")
    args = ap.parse_args()

    manifest = load_manifest()
    label = str(manifest.get("label") or "run")
    timeout_scale = float(manifest.get("timeout_scale") or 1.0)
    resume = bool(manifest.get("resume"))
    tasks = manifest["tasks"]

    if args.print_plan:
        problems = validate_manifest(manifest)
        print(f"label={label} tasks={len(tasks)} timeout_scale={timeout_scale} resume={resume}")
        for entry in tasks:
            print(f"  {entry['name']}\t{entry['dir']}")
        if problems:
            print("PROBLEMS:")
            for problem in problems:
                print(f"  ! {problem}")
            sys.exit(2)
        print("plan OK")
        return

    driver_path = Path(CONTAINER_DRIVER)
    if not driver_path.is_file():
        sys.exit(f"container driver missing on .8: {CONTAINER_DRIVER} — sync the branch first")
    problems = validate_manifest(manifest)
    if problems:
        for problem in problems:
            print(f"PREFLIGHT FAIL: {problem}", flush=True)
        sys.exit(2)

    out_dir = Path(str(manifest.get("out_dir") or "")).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / "results.jsonl"

    done: set[str] = set()
    if resume and results_path.is_file():
        for line in results_path.read_text(encoding="utf-8").splitlines():
            try:
                done.add(str(json.loads(line).get("task") or ""))
            except json.JSONDecodeError:
                continue

    usage_db = find_usage_db()
    usage_conn = open_usage_db(usage_db)
    print(f"label={label} tasks={len(tasks)} resume_skip={len(done)} "
          f"usage_db={usage_db if usage_conn else 'unavailable'}", flush=True)

    rows = []
    suite_t0 = time.monotonic()
    for entry in tasks:
        name = entry["name"]
        if name in done:
            print(f"[skip] {name} (resume)", flush=True)
            continue
        print(f"[run] {name} ...", flush=True)
        row = run_one(entry, out_dir, timeout_scale, usage_conn)
        rows.append(row)
        mark = "PASS" if row["ok"] else ("NETFAIL" if row["netfail"] else "FAIL")
        print(f"[{mark}] {row['task']} ok_run={row['ok_run']} {row.get('seconds')}s "
              f"tokens={row.get('tokens')} fallback={row.get('fallback_used')} "
              f"bailout={row.get('bailout_phrase')}", flush=True)
        with results_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    total_seconds = time.monotonic() - suite_t0
    if usage_conn:
        usage_conn.close()

    # Merge with resumed rows so the report covers the whole suite.
    if done:
        for line in results_path.read_text(encoding="utf-8").splitlines():
            try:
                prev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(prev.get("task") or "") in done and prev not in rows:
                rows.append(prev)
        order = {entry["name"]: idx for idx, entry in enumerate(tasks)}
        rows.sort(key=lambda r: order.get(r.get("task"), len(order)))

    report = render_report(rows, out_dir, total_seconds,
                           usage_db if usage_conn else None, timeout_scale, label)
    (out_dir / "report.md").write_text(report, encoding="utf-8")
    passed = sum(1 for r in rows if r["ok"])
    ran = [r for r in rows if r["ok_run"]]
    fb_rate = (sum(1 for r in ran if r["fallback_used"]) / len(ran)) if ran else 0.0
    print(f"DONE {passed}/{len(rows)} passed, {total_seconds:.0f}s, fallback_rate={fb_rate:.2f}",
          flush=True)


if __name__ == "__main__":
    main()
