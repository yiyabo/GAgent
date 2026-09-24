#!/usr/bin/env python3
"""Harness-benchmark container driver: run ONE task through the real chat-run chain.

Invoked inside the phage-agent container by suite_driver.py as a subprocess
(repo bind-mounted at /app):

    python3 /app/evals/harness_benchmark/container_driver.py \
        --task /app/evals/harness_benchmark/tasks_code/tm02_batch_file_filter \
        --out-dir /app/data/evals_harness/<label>/tm02_batch_file_filter

Unlike evals/pipeline_benchmark/driver.py (which constructs a DeepThinkAgent
directly), this driver goes through the production chat-run path with no HTTP
auth: start_background_chat_run() (app/routers/chat/run_routes.py) creates the
chat_runs row, saves the user message and spawns the execute_chat_run worker
(app/services/chat_run_worker.py) on this process's event loop. The driver
then polls the run row until a terminal status, harvests every persisted
chat_run event (final payload, fallback_used metadata, artifact events) and
writes a pipeline-benchmark-compatible result.json plus events.jsonl.

Usage attribution needs no extra work: build_agent_for_chat_request
(stream_context.py) calls set_usage_context(session_id=...) inside the worker,
so llm_usage_log rows land tagged with this task's unique session id.

The process ALWAYS exits 0 after writing result.json — a crashing task must
not kill the suite. stdout carries exactly one summary line.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import sys
import time
import traceback
import uuid
from pathlib import Path

REPO_ROOT = Path("/app")
ENV_FILE = REPO_ROOT / ".env"

OWNER_ID = "bench_harness"
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}
POLL_INTERVAL_S = 2.0
FLUSH_GRACE_S = 2.5
CANCEL_GRACE_S = 20.0

# Bail-out phrases emitted by the real fallback paths (deep_think_agent.py
# _build_llm_unavailable_final_answer, deep_think/synthesis.py
# _fallback_answer_from_steps). A final answer containing one of these is a
# fallback-shaped response regardless of how the run is labelled.
BAILOUT_MARKERS = (
    "LLM 模型服务连续调用失败",
    "LLM 模型服务额度不足",
    "LLM 模型服务认证失败",
    "LLM 模型服务拒绝请求",
    "本次任务已暂停",
    "已完成思考，但暂未形成结构化结论",
    "DeepThink finished without a structured final answer",
)


def load_dotenv_no_override(path: Path) -> None:
    """Mirror the app's .env without clobbering the docker-exec environment."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def strip_proxy_env() -> None:
    # Stale 127.0.0.1:7890-style proxies in .env kill the LLM/pi clients
    # (docs/LOCAL_INFRA.md); the benchmark path never goes through a proxy.
    for key in list(os.environ):
        if "proxy" in key.lower():
            os.environ.pop(key, None)


def session_prefix(task_dir: Path, meta: dict) -> str:
    raw = str(meta.get("session_prefix") or "")
    if not raw:
        m = re.match(r"(tm?\d+)", task_dir.name)
        raw = f"hb{(m.group(1) if m else 'task').upper()}"
    return re.sub(r"[^A-Za-z0-9_-]", "", raw) or "hbTASK"


def extract_final(events: list) -> dict:
    """Last 'final' event payload -> {response, metadata}; empty dict if none."""
    for _seq, payload in reversed(events):
        if isinstance(payload, dict) and payload.get("type") == "final":
            body = payload.get("payload") or {}
            if not isinstance(body, dict):
                return {}
            response = body.get("response")
            if not response:
                response = (body.get("llm_reply") or {}).get("message") or ""
            metadata = body.get("metadata") or {}
            return {
                "response": str(response or ""),
                "metadata": metadata if isinstance(metadata, dict) else {},
            }
    return {}


def collect_produced_paths(events: list, session_dir: Path) -> list:
    produced = []
    for _seq, payload in events:
        if isinstance(payload, dict) and payload.get("type") == "artifact":
            path = str(payload.get("path") or "").strip()
            if path:
                produced.append(path)
    deliverables = session_dir / "deliverables"
    if deliverables.is_dir():
        for item in sorted(deliverables.rglob("*")):
            if item.is_file():
                produced.append(str(item.relative_to(session_dir)))
    return sorted(set(produced))


async def run_turn(request_kwargs: dict, deadline: float) -> dict:
    """Spawn one chat run and poll it to a terminal status."""
    from app.repository.chat_runs import (
        fetch_events_after,
        get_chat_run,
        insert_chat_run_signal,
    )
    from app.routers.chat.run_routes import start_background_chat_run
    from app.services import chat_run_hub as hub

    run_id = start_background_chat_run(
        request_kwargs["request"],
        session_id=request_kwargs["session_id"],
        owner_id=OWNER_ID,
    )
    record = {"run_id": run_id, "status": None, "seconds": 0.0, "error": None}
    t0 = time.monotonic()
    status = None
    while True:
        await asyncio.sleep(POLL_INTERVAL_S)
        row = get_chat_run(run_id) or {}
        status = str(row.get("status") or "")
        if status in TERMINAL_STATUSES:
            break
        if time.monotonic() > deadline:
            # Durable cancel signal + in-process fast path, then a short grace.
            try:
                insert_chat_run_signal(run_id, "cancel")
                hub.request_cancel(run_id)
            except Exception:  # noqa: BLE001 - best-effort cancel
                pass
            grace_deadline = time.monotonic() + CANCEL_GRACE_S
            while time.monotonic() < grace_deadline:
                await asyncio.sleep(POLL_INTERVAL_S)
                row = get_chat_run(run_id) or {}
                status = str(row.get("status") or "")
                if status in TERMINAL_STATUSES:
                    break
            record["error"] = f"timeout; run status={status or 'unknown'}"
            break
    # Let the emitter's 80ms micro-batch flush land before harvesting events.
    await asyncio.sleep(FLUSH_GRACE_S)
    events = fetch_events_after(run_id, -1)
    record["status"] = status or "unknown"
    record["seconds"] = round(time.monotonic() - t0, 2)
    record["events"] = events
    return record


async def drive(task_dir: Path, out_dir: Path, record: dict, deadline: float) -> None:
    from app.database import init_db
    from app.repository.llm_usage import init_llm_usage_table
    from app.routers.chat.models import ChatRequest
    from app.services.session_paths import get_runtime_session_dir

    init_db()
    init_llm_usage_table()

    meta = json.loads((task_dir / "meta.json").read_text(encoding="utf-8"))
    task_text = (task_dir / "task.md").read_text(encoding="utf-8").strip()
    turns = meta.get("turns")
    if not isinstance(turns, list) or not all(isinstance(t, str) and t.strip() for t in turns or []):
        turns = [task_text]
    turns = [t.strip() for t in turns]

    sid = f"{session_prefix(task_dir, meta)}_{uuid.uuid4().hex[:6]}"
    record["session_id"] = sid
    session_dir = get_runtime_session_dir(sid, create=True)
    record["session_dir"] = str(session_dir)

    uploads = session_dir / "uploads"
    fixtures = task_dir / "fixtures"
    fixture_names = []
    if fixtures.is_dir():
        uploads.mkdir(parents=True, exist_ok=True)
        for fixture in sorted(fixtures.iterdir()):
            if fixture.is_file():
                shutil.copy2(fixture, uploads / fixture.name)
                fixture_names.append(fixture.name)

    all_events = []
    turn_records = []
    for idx, turn_text in enumerate(turns):
        context = {}
        if idx == 0 and fixture_names:
            context["attachments"] = [
                {"type": "file", "name": name, "path": str(uploads / name)}
                for name in fixture_names
            ]
        request = ChatRequest(
            message=turn_text,
            session_id=sid,
            context=context,
            client_message_id=f"hb-{task_dir.name}-t{idx}-{uuid.uuid4().hex[:8]}",
        )
        turn = await run_turn(
            {"request": request, "session_id": sid}, deadline=deadline
        )
        events = turn.pop("events")
        for seq, payload in events:
            all_events.append({"run_id": turn["run_id"], "turn": idx, "seq": seq, "payload": payload})
        final = extract_final(events)
        metadata = final.get("metadata") or {}
        turn_records.append({
            "run_id": turn["run_id"],
            "status": turn["status"],
            "seconds": turn["seconds"],
            "error": turn["error"],
            "final_answer": final.get("response") or "",
            "fallback_used": bool(metadata.get("fallback_used")),
            "iterations": int(metadata.get("iterations") or 0),
            "tools_used": sorted({str(t) for t in (metadata.get("tools_used") or [])}),
        })
        if turn["error"]:
            break  # timed-out turn: do not stack further turns on a dead session

    last = turn_records[-1] if turn_records else {}
    final_answer = str(last.get("final_answer") or "")
    record.update(
        run_ids=[t["run_id"] for t in turn_records],
        ok_run=bool(turn_records) and all(t["status"] == "succeeded" and not t["error"] for t in turn_records),
        final_answer=final_answer,
        answer_len=len(final_answer),
        fallback_used=any(t["fallback_used"] for t in turn_records),
        total_iterations=sum(t["iterations"] for t in turn_records),
        tools_used=sorted({tool for t in turn_records for tool in t["tools_used"]}),
        bailout_phrase=any(marker in final_answer for marker in BAILOUT_MARKERS),
        produced_paths=collect_produced_paths(
            [(e["seq"], e["payload"]) for e in all_events], session_dir
        ),
        turns=[{k: v for k, v in t.items() if k != "final_answer"} for t in turn_records],
    )
    if not record["ok_run"]:
        errors = [t["error"] or f"run status={t['status']}" for t in turn_records if t["status"] != "succeeded" or t["error"]]
        record["error"] = "; ".join(errors) or "no turns executed"
    (out_dir / "events.jsonl").write_text(
        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in all_events),
        encoding="utf-8",
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Run one harness-benchmark task via the chat-run chain")
    ap.add_argument("--task", required=True, help="task directory containing task.md + meta.json")
    ap.add_argument("--out-dir", required=True, help="result directory (result.json + events.jsonl)")
    args = ap.parse_args()

    task_dir = Path(args.task).resolve()
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    os.chdir(str(REPO_ROOT))
    load_dotenv_no_override(ENV_FILE)
    strip_proxy_env()
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    record = {
        "task": task_dir.name,
        "session_id": None,
        "session_dir": "",
        "run_ids": [],
        "ok_run": False,
        "seconds": 0.0,
        "final_answer": "",
        "answer_len": 0,
        "fallback_used": None,
        "bailout_phrase": None,
        "total_iterations": None,
        "tools_used": [],
        "acceptance_missing": [],
        "produced_paths": [],
        "turns": [],
        "error": None,
    }

    t0 = time.monotonic()
    timeout = 600.0
    try:
        meta = json.loads((task_dir / "meta.json").read_text(encoding="utf-8"))
        timeout = float(meta.get("timeout_seconds") or 600)
        timeout *= float(os.getenv("HB_TIMEOUT_SCALE", "1") or 1)
        deadline = time.monotonic() + timeout
        asyncio.run(asyncio.wait_for(drive(task_dir, out_dir, record, deadline), timeout=timeout + CANCEL_GRACE_S + 30))
    except asyncio.TimeoutError:
        record["error"] = f"driver timeout after {timeout:.0f}s"
    except Exception as exc:  # noqa: BLE001 - the suite must survive any task crash
        record["error"] = f"{exc!r} | {traceback.format_exc(limit=8)}"
    record["seconds"] = round(time.monotonic() - t0, 2)
    if record["error"] and not record.get("ok_run"):
        record["ok_run"] = False

    (out_dir / "result.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(
        "TASK {task} sid={sid} ok_run={ok} seconds={sec} answer_len={alen} "
        "fallback={fb} bailout={bail} runs={runs} error={err}".format(
            task=record["task"],
            sid=record["session_id"] or "-",
            ok=record["ok_run"],
            sec=record["seconds"],
            alen=record["answer_len"],
            fb=record["fallback_used"],
            bail=record["bailout_phrase"],
            runs=len(record["run_ids"]),
            err=(record["error"] or "")[:120],
        ),
        flush=True,
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
