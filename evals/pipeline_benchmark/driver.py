#!/usr/bin/env python3
"""Pipeline benchmark driver: run ONE task through the real DeepThinkAgent chain.

Usage (inside the phage-agent container, repo mounted at /app):

    python3 /app/evals/pipeline_benchmark/driver.py \
        --task /app/evals/pipeline_benchmark/tasks/t01_read_csv_rows \
        --work-root /app/data/evals/_work/t01_read_csv_rows \
        --out /app/data/evals/run_a/t01_read_csv_rows/result.json

The driver reproduces the production construction from
app/routers/chat/agent.py: DeepThinkAgent(llm_client=get_llm_service(),
available_tools=get_all_tools(), tool_executor=<async wrapper forwarding
tool_box.execute_tool with the loop-injected ToolContext>, max_iterations,
tool_timeout=120, request_profile={session_id, request_tier, intent_type}).

Fidelity gap and its fix (2026-09-26): production does NOT call
``tool_box.execute_tool`` here. The deep-think loop dispatches through
``UnifiedToolExecutor`` (app/services/deep_think/dispatch.py), which derives
``code_executor``'s ``require_task_context`` from the bound plan/task
(app/services/execution/tool_executor.py:321) — an unscoped chat call therefore
arrives with False. Calling ``tool_box.execute_tool`` directly leaves the
handler's own default (True, tool_box/tools_impl/code_executor.py:398), so every
bench ``code_executor`` call was refused with "Missing plan_id for strict atomic
execution", the guard booked that as a failed execution, and the run ended in a
hedged synthesis answer. That artifact — not the lane under test — produced the
t09/t26 failures and the 50% fallback rate in the split arms. The driver now
mirrors the derivation instead of inheriting the wrong default.

Usage attribution mirrors the app startup: init_db() points the connection
pool at the real main database ($DB_ROOT/main/plan_registry.db, default
data/databases/main/plan_registry.db) instead of the pool's auto-init
fallback "./tasks.db", and set_usage_context(session_id=...) tags every
llm_usage_log row so the runner can sum tokens per task.

The process ALWAYS exits 0 after writing result.json — a crashing task must
not kill the suite. stdout carries exactly one summary line.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
import traceback
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

TIER_INTENT = {"standard": "chat", "research": "execute_task", "execute": "execute_task"}
DEFAULT_MAX_ITERATIONS = 64  # production default (_DEEP_THINK_MAX_ITER_DEFAULT)


def _strip_proxy_env() -> None:
    for key in list(os.environ):
        if "proxy" in key.lower():
            os.environ.pop(key, None)


def _session_prefix(task_dir: Path, meta: dict) -> str:
    raw = str(meta.get("session_prefix") or "")
    if not raw:
        m = re.match(r"(t\d+)", task_dir.name)
        raw = f"bench{(m.group(1) if m else 'task').upper()}"
    token = re.sub(r"[^A-Za-z0-9_-]", "", raw) or "bench"
    return token


async def _run_agent(query: str, meta: dict, sid: str, session_dir: Path):
    # Imports stay inside the coroutine so APP_RUNTIME_ROOT / proxy stripping
    # are fully applied before any app module reads the environment.
    from app.database import init_db
    from app.llm import set_usage_context
    from app.repository.llm_usage import init_llm_usage_table
    from app.routers.chat.request_routing import get_all_tools
    from app.services.deep_think_agent import DeepThinkAgent
    from app.services.llm.llm_service import get_llm_service
    from tool_box import execute_tool
    from tool_box.context import ToolContext

    init_db()
    init_llm_usage_table()
    set_usage_context(session_id=sid, phase="chat", call_purpose="chat_main")

    async def _exec(name, params):
        # The deep-think loop already injects a ToolContext into params
        # (session_id comes from request_profile); forward it verbatim.
        # Injecting our own alongside it makes execute_tool raise
        # "got multiple values for keyword argument 'tool_context'".
        # Only fall back to our own context when the caller supplied none.
        params = dict(params or {})
        if "tool_context" not in params:
            params["tool_context"] = ToolContext(session_id=sid, work_dir=str(session_dir))
        if name == "code_executor":
            # This bench has no bound plan/task, which is exactly the shape of an
            # unscoped chat call — and the shape production answers with False
            # (see the module docstring). setdefault keeps an explicit caller
            # value authoritative.
            params.setdefault("require_task_context", False)
        return await execute_tool(name, **params)

    tier = str(meta.get("tier") or "standard")
    tools = meta.get("tools") or get_all_tools()
    agent = DeepThinkAgent(
        llm_client=get_llm_service(),
        available_tools=list(tools),
        tool_executor=_exec,
        max_iterations=int(meta.get("max_iterations") or DEFAULT_MAX_ITERATIONS),
        tool_timeout=120,
        request_profile={
            "session_id": sid,
            "request_tier": tier,
            "intent_type": TIER_INTENT.get(tier, "execute_task"),
        },
    )
    result = await agent.think(
        query,
        context={"session_id": sid, "chat_history": []},
        task_context=None,
    )
    return agent, result


def main() -> None:
    ap = argparse.ArgumentParser(description="Run one pipeline-benchmark task via DeepThinkAgent")
    ap.add_argument("--task", required=True, help="task directory containing task.md + meta.json")
    ap.add_argument("--work-root", required=True, help="scratch root; runtime/ is created below it")
    ap.add_argument("--out", required=True, help="path of the result.json to write")
    args = ap.parse_args()

    task_dir = Path(args.task).resolve()
    work_root = Path(args.work_root).resolve()
    out_path = Path(args.out)

    _strip_proxy_env()
    runtime_root = work_root / "runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)
    os.environ["APP_RUNTIME_ROOT"] = str(runtime_root)
    # The one-script delegation guard is a *chat routing* policy: it refuses an
    # unbound delegation that a short script already answers. This bench makes
    # unbound calls on purpose (every task here is a short script), so leaving
    # the guard on would measure the guard instead of the lane under test.
    os.environ["CODE_EXECUTOR_ONE_SCRIPT_GUARD"] = "0"
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    record = {
        "task": task_dir.name,
        "session_id": None,
        "ok_run": False,
        "seconds": 0.0,
        "final_answer": "",
        "answer_len": 0,
        "fallback_used": None,
        "total_iterations": None,
        "tools_used": [],
        "acceptance_missing": [],
        "produced_paths": [],
        "error": None,
    }

    t0 = time.monotonic()
    sid = ""
    try:
        meta = json.loads((task_dir / "meta.json").read_text(encoding="utf-8"))
        query = (task_dir / "task.md").read_text(encoding="utf-8").strip()
        timeout = float(meta.get("timeout_seconds") or 600)
        timeout *= float(os.getenv("BENCH_TIMEOUT_SCALE", "1") or 1)

        sid = f"{_session_prefix(task_dir, meta)}_{uuid.uuid4().hex[:6]}"
        record["session_id"] = sid

        from app.services.session_paths import get_runtime_session_dir

        session_dir = get_runtime_session_dir(sid, create=True)
        fixtures = task_dir / "fixtures"
        if fixtures.is_dir():
            uploads = session_dir / "uploads"
            uploads.mkdir(parents=True, exist_ok=True)
            for fixture in sorted(fixtures.iterdir()):
                if fixture.is_file():
                    (uploads / fixture.name).write_bytes(fixture.read_bytes())

        agent, result = asyncio.run(asyncio.wait_for(_run_agent(query, meta, sid, session_dir), timeout=timeout))
        answer = str(getattr(result, "final_answer", "") or "")
        record.update(
            ok_run=True,
            final_answer=answer,
            answer_len=len(answer),
            fallback_used=bool(getattr(result, "fallback_used", False)),
            total_iterations=int(getattr(result, "total_iterations", 0) or 0),
            tools_used=sorted({str(t) for t in (getattr(result, "tools_used", None) or [])}),
            acceptance_missing=[str(x) for x in (getattr(agent, "_acceptance_missing", None) or [])],
            produced_paths=[str(x) for x in (getattr(agent, "_produced_deliverable_paths", None) or [])],
        )
    except asyncio.TimeoutError:
        record["error"] = f"timeout after {timeout:.0f}s"
    except Exception as exc:  # noqa: BLE001 - the suite must survive any task crash
        record["error"] = f"{exc!r} | {traceback.format_exc(limit=8)}"
    record["seconds"] = round(time.monotonic() - t0, 2)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    print(
        "TASK {task} sid={sid} ok_run={ok} seconds={sec} answer_len={alen} "
        "fallback={fb} iters={it} error={err}".format(
            task=record["task"],
            sid=sid or "-",
            ok=record["ok_run"],
            sec=record["seconds"],
            alen=record["answer_len"],
            fb=record["fallback_used"],
            it=record["total_iterations"],
            err=(record["error"] or "")[:120],
        ),
        flush=True,
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
