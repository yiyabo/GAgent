#!/usr/bin/env python3
"""Harness-benchmark Mac-side runner: drive the .8 container over one SSH channel.

Runs the expanded harness benchmark (evals/pipeline_benchmark/tasks, 50 tasks
+ evals/harness_benchmark/tasks_code, code-mode tasks) against the REAL
chat-run chain inside the phage-agent container on 10.110.107.8:

    python3 evals/harness_benchmark/mac_runner.py --label baseline_pre
    python3 evals/harness_benchmark/mac_runner.py --label smoke_tm --category code_mode
    python3 evals/harness_benchmark/mac_runner.py --label t02 --only tm02_batch_file_filter
    python3 evals/harness_benchmark/mac_runner.py --dry-run --label baseline_pre

SSH discipline (docs/LOCAL_INFRA.md rate-limit lessons):
  * everything for a run travels in ONE ssh invocation whose remote command is
    `docker exec -i -e HB_MANIFEST_B64=<b64> phage-agent python3 -` with
    suite_driver.py piped on stdin (no docker cp, no second connection);
  * the channel rides the existing ControlMaster socket (~/.ssh/cm-gagent8).
    If the socket is missing or ssh fails, this runner STOPS and reports —
    it never rebuilds the master connection itself;
  * the report pull-back is a second ssh (slave connection, no re-auth) that
    streams `tar czf - report.md results.jsonl` out of the container.

Reports land in evals/reports/harness_benchmark/<label>/ (gitignored except
.gitkeep). The full run directory stays on .8 at
/data/phage-agent/data/evals_harness/<label>/ (= container /app/data/...).

A full 57-task run takes hours; launch it detached, e.g.
    nohup python3 evals/harness_benchmark/mac_runner.py --label baseline_pre \
        > /tmp/hb_baseline_pre.log 2>&1 &
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
REPO_ROOT = BASE.parents[1]
SUITE_DRIVER = BASE / "suite_driver.py"
TASK_ROOTS = (
    REPO_ROOT / "evals" / "pipeline_benchmark" / "tasks",
    BASE / "tasks_code",
)
REPORT_ROOT = REPO_ROOT / "evals" / "reports" / "harness_benchmark"

DEFAULT_HOST = "10.110.107.8"
DEFAULT_USER = "root"
DEFAULT_CONTROL_PATH = "~/.ssh/cm-gagent8"
DEFAULT_CONTAINER = "phage-agent"
CONTAINER_REPO = "/app"
LABEL_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def discover_tasks(only: set[str] | None, categories: set[str] | None) -> list[dict]:
    tasks = []
    for root in TASK_ROOTS:
        if not root.is_dir():
            continue
        for path in sorted(root.iterdir()):
            if not (path.is_dir() and (path / "task.md").exists() and (path / "meta.json").exists()):
                continue
            if only and path.name not in only and path.name.split("_")[0] not in only:
                continue
            try:
                meta = json.loads((path / "meta.json").read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                meta = {}
            category = str(meta.get("category") or "uncategorized")
            if categories and category not in categories:
                continue
            rel = path.relative_to(REPO_ROOT)
            tasks.append({
                "name": path.name,
                "dir": str(Path(CONTAINER_REPO) / rel),
                "category": category,
                "local_dir": str(path),
            })
    return tasks


def build_manifest(args: argparse.Namespace, tasks: list[dict]) -> dict:
    return {
        "label": args.label,
        "out_dir": f"{CONTAINER_REPO}/data/evals_harness/{args.label}",
        "timeout_scale": args.timeout_scale,
        "resume": args.resume,
        "tasks": [{"name": t["name"], "dir": t["dir"]} for t in tasks],
    }


def manifest_b64(manifest: dict) -> str:
    raw = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def ssh_argv(args: argparse.Namespace, remote_cmd: str) -> list:
    return [
        "ssh",
        "-o", f"ControlPath={args.control_path}",
        "-o", "BatchMode=yes",
        f"{args.user}@{args.host}",
        remote_cmd,
    ]


def control_path_expanded(args: argparse.Namespace) -> Path:
    return Path(os.path.expanduser(args.control_path))


def preflight_channel(args: argparse.Namespace) -> None:
    socket_path = control_path_expanded(args)
    if not socket_path.exists():
        sys.exit(
            f"ControlPath socket {socket_path} not found — the SSH master channel "
            "to .8 is down. Re-establish it manually (sshpass + ControlMaster, see "
            "docs/LOCAL_INFRA.md '.8 SSH 实操'); this runner must not rebuild it."
        )


def run_suite_ssh(args: argparse.Namespace, b64: str) -> int:
    remote_cmd = (
        f"docker exec -i -e HB_MANIFEST_B64={b64} "
        f"{args.container} python3 -"
    )
    argv = ssh_argv(args, remote_cmd)
    with SUITE_DRIVER.open("rb") as script:
        proc = subprocess.run(argv, stdin=script, shell=False)
    return proc.returncode


def pull_report(args: argparse.Namespace) -> Path | None:
    local_dir = REPORT_ROOT / args.label
    local_dir.mkdir(parents=True, exist_ok=True)
    members = ["report.md", "results.jsonl"]
    if args.pull_details:
        members += ["*/result.json", "*/events.jsonl", "*/driver_stdout.txt", "*/check.py"]
    inner = f"cd {CONTAINER_REPO}/data/evals_harness/{args.label} && tar czf - {' '.join(members)}"
    remote_cmd = f"docker exec {args.container} sh -c '{inner}'"
    proc = subprocess.run(ssh_argv(args, remote_cmd), capture_output=True, shell=False)
    if proc.returncode != 0:
        print(f"WARN: report pull-back failed rc={proc.returncode}: "
              f"{(proc.stderr or b'').decode('utf-8', 'replace')[-300:]}", flush=True)
        return None
    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
        tmp.write(proc.stdout)
        tmp_path = Path(tmp.name)
    try:
        with tarfile.open(tmp_path, "r:gz") as tar:
            for member in tar.getmembers():
                # defense-in-depth: never write outside local_dir
                target = (local_dir / member.name).resolve()
                if target == local_dir or local_dir in target.parents:
                    tar.extract(member, local_dir)
    finally:
        tmp_path.unlink(missing_ok=True)
    return local_dir


def dry_run(args: argparse.Namespace, tasks: list[dict], manifest: dict) -> None:
    b64 = manifest_b64(manifest)
    print(f"DRY RUN — no ssh, no container calls")
    print(f"label:        {args.label}")
    print(f"tasks:        {len(tasks)}")
    by_cat: dict[str, int] = {}
    for task in tasks:
        by_cat[task["category"]] = by_cat.get(task["category"], 0) + 1
    for cat in sorted(by_cat):
        print(f"  {cat}: {by_cat[cat]}")
    print(f"container:    {args.user}@{args.host} -> docker {args.container}")
    print(f"control path: {args.control_path}")
    print(f"out (remote): {manifest['out_dir']}")
    print(f"out (local):  {REPORT_ROOT / args.label}")
    print(f"manifest:     {len(b64)} base64 chars")
    print(f"ssh argv:     {ssh_argv(args, f'docker exec -i -e HB_MANIFEST_B64=<{len(b64)} chars> {args.container} python3 -')}")
    print("validating task definitions locally via suite_driver --print-plan ...")
    env = dict(os.environ)
    local_manifest = dict(manifest)
    local_manifest["tasks"] = [{"name": t["name"], "dir": t["local_dir"]} for t in tasks]
    env["HB_MANIFEST_B64"] = manifest_b64(local_manifest)
    proc = subprocess.run(
        ["python3", str(SUITE_DRIVER), "--print-plan"],
        capture_output=True, text=True, shell=False, env=env,
    )
    print(proc.stdout.strip())
    if proc.returncode != 0:
        sys.exit(f"dry-run validation FAILED (rc={proc.returncode})")


def main() -> None:
    ap = argparse.ArgumentParser(description="Mac-side harness-benchmark runner (SSH -> .8 container)")
    ap.add_argument("--label", required=True, help="run label; remote/local report dir name [A-Za-z0-9_-]")
    ap.add_argument("--only", default="", help="comma-separated task names or prefixes (e.g. t01,tm02)")
    ap.add_argument("--category", default="", help="comma-separated categories (e.g. code_mode,plotting)")
    ap.add_argument("--timeout-scale", type=float, default=1.0)
    ap.add_argument("--resume", action="store_true", help="skip tasks already present in remote results.jsonl")
    ap.add_argument("--pull-details", action="store_true",
                    help="also pull per-task result.json/events.jsonl/driver stdout (large)")
    ap.add_argument("--dry-run", action="store_true", help="validate + print plan without any ssh")
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--user", default=DEFAULT_USER)
    ap.add_argument("--container", default=DEFAULT_CONTAINER)
    ap.add_argument("--control-path", default=DEFAULT_CONTROL_PATH)
    args = ap.parse_args()

    if not LABEL_RE.match(args.label):
        sys.exit(f"--label must match {LABEL_RE.pattern!r}")
    only = {tok.strip() for tok in args.only.split(",") if tok.strip()} or None
    categories = {tok.strip() for tok in args.category.split(",") if tok.strip()} or None

    tasks = discover_tasks(only, categories)
    if not tasks:
        sys.exit("no tasks matched (check --only/--category)")
    manifest = build_manifest(args, tasks)

    if args.dry_run:
        dry_run(args, tasks, manifest)
        return

    preflight_channel(args)
    print(f"[hb] {len(tasks)} tasks -> {args.user}@{args.host} docker {args.container} "
          f"label={args.label}", flush=True)
    t0 = time.monotonic()
    rc = run_suite_ssh(args, manifest_b64(manifest))
    wall = time.monotonic() - t0
    if rc != 0:
        sys.exit(
            f"suite ssh/docker channel failed rc={rc} after {wall:.0f}s. If the "
            "ControlPath master dropped, re-establish it manually — this runner "
            "does not rebuild it. Remote partial results (if any) are under "
            f"{manifest['out_dir']} ; re-run with --resume to continue."
        )
    print(f"[hb] suite finished in {wall:.0f}s; pulling report ...", flush=True)
    local_dir = pull_report(args)
    if local_dir:
        print(f"[hb] report at {local_dir / 'report.md'}", flush=True)


if __name__ == "__main__":
    main()
