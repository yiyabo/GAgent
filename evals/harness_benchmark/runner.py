#!/usr/bin/env python3
"""Harness benchmark: run identical delegation tasks on pi and qwen-code.

Runs inside gagent-qwen-code-runtime:pi-0.85 with the repo mounted at /app:

    python3 /app/evals/harness_benchmark/runner.py \
        --harness pi   --out /app/data/tools/bench_pi.json
    python3 /app/evals/harness_benchmark/runner.py \
        --harness qwen --out /app/data/tools/bench_qwen.json

Requires PLATFORM_LLM_API_URL / PLATFORM_LLM_API_KEY in the environment
(the container /app/.env has them). Proxy variables are stripped: the pi
client honours HTTPS_PROXY and dies on the stale 127.0.0.1:7890 default.

Both harnesses receive one fixed instruction ("read task_prompt.md and
follow it") so every subprocess argv is a literal; the actual task text
lives in the workspace copy of task_prompt.md. Workdir/env switching
happens via os.chdir/os.environ so the subprocess calls stay literal.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
TASKS_DIR = BASE / "tasks"
TIMEOUT_PER_TASK = int(os.getenv("BENCH_TIMEOUT", "600"))

STATIC_INSTRUCTION = ("Read the file task_prompt.md in the current directory and exactly "
                      "follow the instructions inside it.")


def build_env() -> dict:
    env = {k: v for k, v in os.environ.items() if "proxy" not in k.lower()}
    env.setdefault("PLATFORM_LLM_API_URL", "https://sub2api.medicalheart.cn/v1/chat/completions")
    if "PLATFORM_LLM_API_KEY" not in env:
        sys.exit("PLATFORM_LLM_API_KEY missing — source /app/.env first")
    env["OPENAI_BASE_URL"] = env["PLATFORM_LLM_API_URL"].rsplit("/chat/completions", 1)[0]
    env["OPENAI_API_KEY"] = env["PLATFORM_LLM_API_KEY"]
    env.setdefault("QWEN_CODE_MODEL", "qwen3.8-flash")
    return env


def build_pi_home(env: dict) -> Path:
    home = Path(tempfile.mkdtemp(prefix="pihome_"))
    agent = home / ".pi" / "agent"
    agent.mkdir(parents=True)
    (agent / "models.json").write_text(json.dumps({
        "providers": {"sub2api": {
            "baseUrl": env["OPENAI_BASE_URL"],
            "apiKey": env["PLATFORM_LLM_API_KEY"],
            "api": "openai-completions",
            "models": [{
                "id": "qwen3.8-flash", "name": "Qwen3.8 Flash", "reasoning": True,
                "contextWindow": 262144, "maxTokens": 16384,
            }],
        }}
    }))
    return home


def with_dir_and_env(path: str, env_updates: dict):
    """chdir + patch os.environ; returns a restore callable."""
    prev_cwd = os.getcwd()
    prev_env = dict(os.environ)
    os.chdir(path)
    for key, value in env_updates.items():
        os.environ[key] = value

    def restore() -> None:
        os.chdir(prev_cwd)
        os.environ.clear()
        os.environ.update(prev_env)

    return restore


def parse_tokens(stdout: str) -> int | None:
    hits = re.findall(r'"total_tokens":\s*(\d+)', stdout)
    hits = hits or re.findall(r'"totalTokens":\s*(\d+)', stdout)
    # usage fields repeat per event and are cumulative — the last one wins
    return int(hits[-1]) if hits else None


def run_task(harness: str, pi_home: Path | None, env: dict, task_dir: Path, ws: Path) -> dict:
    if harness == "pi":
        env_updates = {**env, "HOME": str(pi_home)}
    else:
        env_updates = dict(env)

    restore = with_dir_and_env(str(ws), env_updates)
    t0 = time.monotonic()
    try:
        if harness == "pi":
            proc = subprocess.run(
                ["pi", "--provider", "sub2api", "--model", "qwen3.8-flash",
                 "--no-session", "--mode", "json", "-p",
                 "Read the file task_prompt.md in the current directory and exactly follow the instructions inside it."],
                capture_output=True, text=True, timeout=TIMEOUT_PER_TASK, shell=False)
        else:
            proc = subprocess.run(
                ["qwen", "--output-format", "json", "-p",
                 "Read the file task_prompt.md in the current directory and exactly follow the instructions inside it."],
                capture_output=True, text=True, timeout=TIMEOUT_PER_TASK, shell=False)
        stdout, rc = proc.stdout, proc.returncode
        cli_err = (proc.stderr or "")[-300:]
    except subprocess.TimeoutExpired:
        restore()
        return {"ok": False, "seconds": round(time.monotonic() - t0, 1), "tokens": None,
                "error": f"timeout after {TIMEOUT_PER_TASK}s"}
    seconds = round(time.monotonic() - t0, 1)

    # make the checker available only after the harness finished, so the
    # agent under test never sees the grading logic
    shutil.copy(task_dir / "check.py", ws / "check.py")
    try:
        chk = subprocess.run(["python3", "check.py"], capture_output=True,
                             text=True, timeout=60, shell=False)
        check_out = (chk.stderr or chk.stdout or "")[-300:]
        check_rc = chk.returncode
    except subprocess.TimeoutExpired:
        check_out, check_rc = "check timeout", 1
    restore()

    return {
        "ok": check_rc == 0,
        "seconds": seconds,
        "tokens": parse_tokens(stdout),
        "rc": rc,
        "check_out": check_out,
        "cli_err": cli_err[-200:] if rc != 0 else "",
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--harness", choices=["pi", "qwen"], required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workdir", default="/app/data/tools/bench_ws")
    args = ap.parse_args()

    env = build_env()
    pi_home = build_pi_home(env) if args.harness == "pi" else None
    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    task_dirs = sorted(p for p in TASKS_DIR.iterdir() if p.is_dir() and (p / "task_prompt.md").exists())
    results = []
    for task_dir in task_dirs:
        ws = workdir / task_dir.name
        if ws.exists():
            shutil.rmtree(ws)
        ws.mkdir(parents=True)
        shutil.copy(task_dir / "task_prompt.md", ws / "task_prompt.md")
        fixtures = task_dir / "fixtures"
        if fixtures.exists():
            for fixture in sorted(fixtures.iterdir()):
                shutil.copy(fixture, ws / fixture.name)
        print(f"[{args.harness}] {task_dir.name} running...", flush=True)
        r = run_task(args.harness, pi_home, env, task_dir, ws)
        r["task"] = task_dir.name
        results.append(r)
        print(f"[{args.harness}] {task_dir.name} ok={r['ok']} {r['seconds']}s tokens={r['tokens']}", flush=True)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"harness": args.harness, "results": results}, ensure_ascii=False, indent=1))
    ok_n = sum(1 for r in results if r["ok"])
    print(f"DONE {args.harness}: {ok_n}/{len(results)} passed, total {sum(r['seconds'] for r in results):.0f}s", flush=True)


if __name__ == "__main__":
    main()
