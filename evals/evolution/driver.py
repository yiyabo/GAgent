#!/usr/bin/env python3
"""Self-evolution harness: run one scenario through the production chat-run path.

This file is not run from the repository. controller.py replaces the
placeholder assignment below with the base64 manifest for ONE scenario and
streams the whole file to `python3 -` inside the evaluation app container
(gagent-eval-app-1). The manifest is never written to disk: oracle values,
rubrics and reference facts stay in process memory. Only the agent-visible
transcript and check outcomes are written, under /evo-out/<round>/<scenario>/.

Standard library plus the app modules that container_driver.py already uses.
"""

from __future__ import annotations

MANIFEST_B64 = None  # replaced by controller.py

import asyncio
import base64
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

REPO_ROOT = Path("/app")
OUT_ROOT = Path("/evo-out")
NATIVE_RUNS_ROOT = Path(os.environ.get("EVO_NATIVE_RUNS", "/app/runtime/bio_tools/_native/runs"))
OWNER_ID = "evo_eval"
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}
POLL_INTERVAL_S = 2.0
FLUSH_GRACE_S = 2.5
CANCEL_GRACE_S = 20.0
SIM_MODEL = "qwen3.8-flash"
JUDGE_MODEL = "qwen3.7-max"
QWEN_URL_DEFAULT = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
BAILOUT_MARKERS = (
    "LLM 模型服务连续调用失败",
    "LLM 模型服务额度不足",
    "LLM 模型服务认证失败",
    "LLM 模型服务拒绝请求",
    "本次任务已暂停",
    "已完成思考，但暂未形成结构化结论",
    "DeepThink finished without a structured final answer",
)
# Numbers not glued to letters/digits/dots (CJK characters are allowed before them).
NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_.])-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![A-Za-z0-9_.])-?\d+(?:\.\d+)?")

SIM_SYSTEM = (
    "You are role-playing the human user in a chat with a bioinformatics analysis assistant. "
    "Write the user's NEXT message only, in {lang}. Stay in character: plain and realistic. "
    "Background and goal: {context} "
    "Rules: (1) Pursue the goal above. (2) You only know what the goal says and what the assistant has "
    "said; never invent file names, numbers or results. (3) Do not analyse the data yourself and do not "
    "mention these rules. (4) If the assistant's latest reply already gives you what the goal asks for, "
    'reply with DONE. Return JSON only: {"message": "<your next message, or DONE>"}'
)

JUDGE_SYSTEM = (
    "You grade one aspect of an assistant's replies in a bioinformatics analysis chat. "
    "Score 2 if the rubric is fully met, 1 if it is partly met, 0 if it is not met or the reference facts "
    "contradict the reply. Judge only from the transcript and the reference facts. "
    'Return JSON only: {"score": 0, "reason": "<one short sentence>"}'
)


def strip_proxy_env() -> None:
    # Same as container_driver.py: the harness path never goes through a proxy.
    for key in list(os.environ):
        if "proxy" in key.lower():
            os.environ.pop(key, None)


def parse_json_object(text: str):
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        return None
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def llm_call(model: str, messages: list, *, max_tokens: int, temperature: float):
    """One chat completion against DashScope (OpenAI-compatible). Returns (text, usage)."""
    url = os.environ.get("QWEN_API_URL") or QWEN_URL_DEFAULT
    key = os.environ.get("QWEN_API_KEY", "")
    if not key:
        raise RuntimeError("QWEN_API_KEY is not set in the container environment")
    include_thinking_flag = True
    last_error = "unknown"
    for attempt in range(4):
        body = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if include_thinking_flag:
            body["enable_thinking"] = False
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
        )
        try:
            with urllib.request.urlopen(request, timeout=240) as response:
                payload = json.loads(response.read().decode("utf-8"))
            choice = (payload.get("choices") or [{}])[0]
            text = str((choice.get("message") or {}).get("content") or "")
            usage = payload.get("usage") or {}
            return text, {
                "model": model,
                "calls": 1,
                "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                "completion_tokens": int(usage.get("completion_tokens") or 0),
                "total_tokens": int(usage.get("total_tokens") or 0),
            }
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")
            except Exception:  # noqa: BLE001 - best effort for diagnostics
                pass
            last_error = f"HTTP {exc.code} {detail[:160]}"
            if exc.code == 400 and include_thinking_flag and "enable_thinking" in detail:
                include_thinking_flag = False  # model rejects the flag: retry without it
                continue
            if exc.code not in (408, 429, 500, 502, 503, 504):
                break
        except Exception as exc:  # noqa: BLE001 - network or parse failure: retry
            last_error = type(exc).__name__ + ": " + str(exc)[:160]
        time.sleep(4 * (attempt + 1))
    raise RuntimeError(f"llm call failed ({model}): {last_error}")


def sim_user_turn(scenario: dict, history: list, entry: dict):
    """Ask the simulated user for its next message. Returns (message or None when DONE, usage)."""
    lang = "Chinese" if scenario.get("lang") == "zh" else "English"
    context = (str(scenario.get("persona") or "").strip() + " " + str(entry.get("goal") or "").strip()).strip()
    system = SIM_SYSTEM.replace("{lang}", lang).replace("{context}", context)
    convo = []
    for item in history:
        label = "USER" if item["role"] == "user" else "ASSISTANT (visible reply)"
        convo.append(f"{label}:\n{item['text'][:2500]}")
    user_block = "\n\n".join(convo) or "(the conversation has not started; write the first user message)"
    text, usage = llm_call(
        SIM_MODEL,
        [{"role": "system", "content": system}, {"role": "user", "content": user_block}],
        max_tokens=300,
        temperature=0.4,
    )
    obj = parse_json_object(text)
    message = str(obj["message"]) if obj and "message" in obj else text
    message = message.strip()
    if not message or message.upper().startswith("DONE"):
        return None, usage
    return message, usage


def transcript_for_judge(history: list) -> str:
    parts = []
    for item in history:
        label = "USER" if item["role"] == "user" else "ASSISTANT (visible reply)"
        text = item["text"][:4000] if item["role"] == "assistant" else item["text"]
        parts.append(f"{label}:\n{text}")
    return "\n\n".join(parts)


def judge_item(item: dict, facts: str, transcript_text: str):
    user_block = (
        f"Rubric: {item['rubric']}\n\n"
        f"Reference facts (authoritative): {facts or '(none)'}\n\n"
        f"Transcript:\n{transcript_text}"
    )
    text, usage = llm_call(
        JUDGE_MODEL,
        [{"role": "system", "content": JUDGE_SYSTEM}, {"role": "user", "content": user_block}],
        max_tokens=300,
        temperature=0.0,
    )
    obj = parse_json_object(text) or {}
    try:
        score = int(obj.get("score"))
    except (TypeError, ValueError):
        score = None
    if score not in (0, 1, 2):
        score = None
    verdict = {
        "id": item["id"],
        "score": score,
        "reason": str(obj.get("reason") or text[:200]).strip()[:300],
    }
    return verdict, usage


def agent_usage(session_id: str) -> dict:
    from app.database import get_db

    with get_db() as con:
        row = con.execute(
            "SELECT COUNT(*), COALESCE(SUM(prompt_tokens),0), COALESCE(SUM(completion_tokens),0), "
            "COALESCE(SUM(total_tokens),0) FROM llm_usage_log WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    return {
        "calls": int(row[0]),
        "prompt_tokens": int(row[1]),
        "completion_tokens": int(row[2]),
        "total_tokens": int(row[3]),
    }


def extract_final(events: list) -> dict:
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


async def run_turn(request, session_id: str, deadline: float) -> dict:
    """Start one chat run and poll it to a terminal status (as container_driver.py does)."""
    from app.repository.chat_runs import fetch_events_after, get_chat_run, insert_chat_run_signal
    from app.routers.chat.run_routes import start_background_chat_run
    from app.services import chat_run_hub as hub

    run_id = start_background_chat_run(request, session_id=session_id, owner_id=OWNER_ID)
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
    await asyncio.sleep(FLUSH_GRACE_S)
    events = fetch_events_after(run_id, -1)
    record["status"] = status or "unknown"
    record["seconds"] = round(time.monotonic() - t0, 2)
    record["events"] = events
    return record


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def list_session_files(session_dir: Path, fixture_hashes: dict) -> list:
    """Files the run produced (uploads and unchanged copies of the fixtures are excluded)."""
    rows = []
    for path in sorted(session_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(session_dir).as_posix()
        if rel.startswith("uploads/"):
            continue
        try:
            if sha256_file(path) in fixture_hashes:
                continue
            stat = path.stat()
        except OSError:
            continue
        rows.append({"rel": rel, "abs": str(path), "bytes": stat.st_size, "mtime": stat.st_mtime})
    return rows


def list_native_run_files(fixture_hashes: dict, since: float) -> list:
    """Outputs the native bio runner wrote for runs started during this scenario.

    Native runs write under <runs>/<run_id>/work/, outside the session directory.
    Files are attributed by modification time; scenarios run one at a time.
    """
    rows = []
    if not NATIVE_RUNS_ROOT.is_dir():
        return rows
    for run_dir in sorted(NATIVE_RUNS_ROOT.iterdir()):
        work = run_dir / "work"
        if not work.is_dir():
            continue
        for path in sorted(work.rglob("*")):
            if not path.is_file():
                continue
            try:
                stat = path.stat()
                if stat.st_mtime < since:
                    continue
                if stat.st_size <= 50_000_000 and sha256_file(path) in fixture_hashes:
                    continue
            except OSError:
                continue
            rows.append({
                "rel": f"native_runs/{run_dir.name}/work/{path.relative_to(work).as_posix()}",
                "abs": str(path),
                "bytes": stat.st_size,
                "mtime": stat.st_mtime,
            })
    return rows


def count_records(path: Path, kind: str):
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    if kind == "fasta":
        return sum(1 for line in lines if line.startswith(">"))
    if kind == "fastq":
        return sum(1 for line in lines if line.strip()) // 4
    if kind == "tsv_rows":
        return max(0, sum(1 for line in lines if line.strip()) - 1)
    if kind == "lines":
        return sum(1 for line in lines if line.strip())
    if kind == "cds_gff":
        count = 0
        for line in lines:
            cols = line.split("\t")
            if line and not line.startswith("#") and len(cols) > 2 and cols[2] == "CDS":
                count += 1
        return count
    return None


def fasta_lengths(path: Path) -> list:
    lengths = []
    current = None
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith(">"):
                if current is not None:
                    lengths.append(current)
                current = 0
            elif current is not None:
                current += len(line.strip())
    except OSError:
        return []
    if current is not None:
        lengths.append(current)
    return lengths


def n50_of(lengths: list) -> int:
    total = sum(lengths)
    running = 0
    for length in sorted(lengths, reverse=True):
        running += length
        if running * 2 >= total:
            return length
    return 0


def record_count(path: Path, check: dict):
    if check.get("min_len") and check.get("format") == "fasta":
        return sum(1 for length in fasta_lengths(path) if length >= int(check["min_len"]))
    return count_records(path, check["format"])


def numbers_in(text: str) -> list:
    values = []
    for token in NUMBER_RE.findall(text or ""):
        try:
            values.append(float(token.replace(",", "")))
        except ValueError:
            continue
    return values


def numbers_on_lines(text: str, terms=None) -> list:
    """Numbers on lines that mention one of the terms (all numbers when no terms are given)."""
    if not terms:
        return numbers_in(text)
    lowered_terms = [str(term).lower() for term in terms]
    values = []
    for line in (text or "").splitlines():
        lowered = line.lower()
        if any(term in lowered for term in lowered_terms):
            values.extend(numbers_in(line))
    return values


def matching_files(ctx: dict, check: dict) -> list:
    pattern = re.compile(check.get("pattern") or ".")
    min_bytes = int(check.get("min_bytes", 1))
    return [f for f in ctx["files"] if pattern.search(f["rel"]) and f["bytes"] >= min_bytes]


def run_check(check: dict, ctx: dict) -> dict:
    kind = check.get("kind")
    label = check.get("label") or str(kind)
    scope = check.get("scope", "all")
    texts = ctx["answers"] if scope == "all" else ctx["answers"][-1:]
    joined = "\n".join(texts)
    lowered = joined.lower()
    if kind == "run_ok":
        ok = bool(ctx["runs"]) and all(r["status"] == "succeeded" and not r["error"] for r in ctx["runs"])
        observed = "statuses=" + ",".join(str(r["status"]) for r in ctx["runs"])
    elif kind == "no_fallback":
        bad = any(r["fallback"] or r["bailout"] for r in ctx["runs"])
        ok = not bad
        observed = "fallback or bail-out seen" if bad else "clean"
    elif kind == "answer_nonempty":
        ok = bool(texts) and all(t.strip() for t in texts)
        observed = f"answer_chars={[len(t) for t in texts]}"
    elif kind == "number":
        target = float(check["value"])
        tol = float(check.get("tol", 0))
        rel = float(check.get("rel", 0))
        found = numbers_in(joined)
        ok = any(abs(v - target) <= max(tol, rel * abs(target)) for v in found)
        observed = "numbers=" + str(sorted(set(found))[:15])
    elif kind == "terms_any":
        hits = [t for t in check.get("terms", []) if t.lower() in lowered]
        ok = bool(hits)
        observed = f"matched={hits[:4]}"
    elif kind == "terms_none":
        hits = [t for t in check.get("terms", []) if t.lower() in lowered]
        ok = not hits
        observed = f"matched={hits[:4]}"
    elif kind == "tool_used":
        used = sorted({str(t).lower() for r in ctx["runs"] for t in r["tools_used"]})
        ok = str(check["name"]).lower() in used
        observed = f"tools={used[:10]}"
    elif kind == "turns_max":
        ok = len(ctx["runs"]) <= int(check["n"])
        observed = f"turns={len(ctx['runs'])}"
    elif kind == "file_exists":
        rows = matching_files(ctx, check)
        ok = bool(rows)
        observed = f"files={[r['rel'] for r in rows][:4]}"
    elif kind == "no_new_files":
        rows = matching_files(ctx, check)
        ok = not rows
        observed = f"files={[r['rel'] for r in rows][:4]}"
    elif kind == "file_records":
        rows = matching_files(ctx, check)
        counts = [count_records(Path(r["abs"]), check["format"]) for r in rows]
        expected = int(check["count"])
        tol = int(check.get("tol", 0))
        ok = any(c is not None and abs(c - expected) <= tol for c in counts)
        observed = f"counts={counts[:6]}"
    elif kind == "number_line":
        targets = [float(v) for v in (check.get("values") or [check["value"]])]
        tol = float(check.get("tol", 0))
        rel = float(check.get("rel", 0))
        found = numbers_on_lines(joined, check.get("terms"))
        ok = any(abs(v - t) <= max(tol, rel * abs(t)) for v in found for t in targets)
        observed = "label_line_numbers=" + str(sorted(set(found))[:15])
    elif kind == "no_number_on_terms":
        found = numbers_on_lines(joined, check.get("terms"))
        ok = not found
        observed = "numbers_on_term_lines=" + str(sorted(set(found))[:10])
    elif kind == "number_any":
        values = [float(v) for v in check["values"]]
        tol = float(check.get("tol", 0))
        found = numbers_in(joined)
        ok = any(abs(v - t) <= tol for v in found for t in values)
        observed = "numbers=" + str(sorted(set(found))[:15])
    elif kind == "consistent_count":
        rows = matching_files(ctx, check)
        if not rows:
            ok = False
            observed = "no matching output file"
        else:
            newest = max(rows, key=lambda r: r["mtime"])
            count = record_count(Path(newest["abs"]), check)
            ok = count is not None and any(
                abs(v - count) < 0.5 for v in numbers_on_lines(joined, check.get("terms"))
            )
            observed = f"file={newest['rel']} count={count}"
    elif kind == "consistent_n50":
        rows = matching_files(ctx, check)
        if not rows:
            ok = False
            observed = "no matching output file"
        else:
            newest = max(rows, key=lambda r: r["mtime"])
            lengths = fasta_lengths(Path(newest["abs"]))
            value = n50_of(lengths)
            ok = bool(lengths) and any(
                abs(v - value) < 0.5 for v in numbers_on_lines(joined, check.get("terms"))
            )
            observed = f"file={newest['rel']} n50={value}"
    else:
        raise ValueError(f"unknown check kind: {kind}")
    return {
        "id": check.get("id") or str(kind),
        "kind": kind,
        "label": label,
        "passed": bool(ok),
        "observed": observed[:400],
    }


def write_transcript(path: Path, header: str, runs: list, checks: list, judges: list, usage: dict, notes: list):
    lines = [header, ""]
    for run in runs:
        lines += [
            f"## Turn {run['turn']} — user",
            "",
            run["user"],
            "",
            (
                f"## Turn {run['turn']} — assistant visible reply (run {run['status']}, {run['seconds']} s, "
                f"tools {', '.join(run['tools_used']) or '-'}, fallback {run['fallback']})"
            ),
            "",
            run["answer"] or "(empty)",
            "",
        ]
    lines += ["## Deterministic checks", ""]
    lines += [f"- [{'PASS' if c['passed'] else 'FAIL'}] {c['label']}: {c['observed']}" for c in checks]
    lines += ["", "## Judge", ""]
    lines += [f"- {j['id']}: score {j['score']} — {j['reason']}" for j in judges] or ["- (none)"]
    lines += [
        "",
        "## Usage",
        "",
        (
            f"- agent {usage['agent']['total_tokens']} tokens over {usage['agent']['calls']} calls; "
            f"simulated user {usage['sim_user']['total_tokens']}; judge {usage['judge']['total_tokens']}"
        ),
    ]
    if notes:
        lines += ["", "## Notes", ""] + [f"- {note}" for note in notes]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def run_scenario(manifest: dict) -> dict:
    from app.database import init_db
    from app.repository.llm_usage import init_llm_usage_table
    from app.routers.chat.models import ChatRequest
    from app.services.session_paths import get_runtime_session_dir

    init_db()
    init_llm_usage_table()
    started = time.monotonic()
    started_wall = time.time()
    scenario = manifest["scenario"]
    round_id = manifest["round"]
    scenario_id = scenario["id"]
    cap = int(manifest.get("budget_tokens") or 150000)
    timeout_s = float(manifest.get("timeout_seconds") or 900)
    max_turns = int(manifest.get("max_turns") or len(scenario["user_turns"]))
    deadline = time.monotonic() + timeout_s
    out_dir = OUT_ROOT / round_id / scenario_id
    out_dir.mkdir(parents=True, exist_ok=True)

    sid = f"evo_{scenario_id}_{uuid.uuid4().hex[:8]}"
    session_dir = get_runtime_session_dir(sid, create=True)
    uploads = session_dir / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    fixture_hashes = {}
    fixture_names = []
    for name, encoded in (scenario.get("fixtures") or {}).items():
        data = base64.b64decode(encoded)
        (uploads / name).write_bytes(data)
        fixture_hashes[hashlib.sha256(data).hexdigest()] = name
        fixture_names.append(name)

    sim_usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    judge_usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    history: list = []
    runs: list = []
    all_events: list = []
    notes: list = []
    loop = asyncio.get_running_loop()

    for idx, entry in enumerate(scenario["user_turns"][:max_turns]):
        spent = agent_usage(sid)["total_tokens"] + sim_usage["total_tokens"] + judge_usage["total_tokens"]
        if spent >= cap:
            notes.append(f"budget cap reached before turn {idx + 1} ({spent} >= {cap})")
            break
        if time.monotonic() > deadline:
            notes.append("scenario deadline reached")
            break
        if "text" in entry:
            message = str(entry["text"])
        else:
            message, usage = await loop.run_in_executor(None, sim_user_turn, scenario, history, entry)
            for key in sim_usage:
                sim_usage[key] += int(usage.get(key) or 0) if key != "calls" else 1
            if message is None:
                notes.append(f"simulated user ended the conversation before turn {idx + 1}")
                break
        history.append({"role": "user", "text": message})
        context = {"memory_enabled": False}
        if idx == 0 and fixture_names:
            context["attachments"] = [
                {"type": "file", "name": name, "path": str(uploads / name)} for name in fixture_names
            ]
        request = ChatRequest(
            message=message,
            session_id=sid,
            context=context,
            client_message_id=f"evo-{scenario_id}-t{idx}-{uuid.uuid4().hex[:8]}",
        )
        turn = await run_turn(request, sid, deadline)
        events = turn.pop("events")
        for seq, payload in events:
            all_events.append({"turn": idx + 1, "run_id": turn["run_id"], "seq": seq, "payload": payload})
        final = extract_final(events)
        metadata = final.get("metadata") or {}
        answer = final.get("response") or ""
        runs.append({
            "turn": idx + 1,
            "user": message,
            "run_id": turn["run_id"],
            "status": turn["status"],
            "seconds": turn["seconds"],
            "error": turn["error"],
            "answer": answer,
            "tools_used": sorted({str(t) for t in (metadata.get("tools_used") or [])}),
            "fallback": bool(metadata.get("fallback_used")),
            "bailout": any(marker in answer for marker in BAILOUT_MARKERS),
            "iterations": int(metadata.get("iterations") or 0),
        })
        history.append({"role": "assistant", "text": answer})
        if turn["error"] or turn["status"] != "succeeded":
            break

    agent = agent_usage(sid)
    files = list_session_files(session_dir, fixture_hashes) + list_native_run_files(fixture_hashes, started_wall)
    answers = [run["answer"] for run in runs]
    ctx = {"answers": answers, "runs": runs, "files": files}
    checks = []
    for check in manifest.get("checks") or []:
        try:
            checks.append(run_check(check, ctx))
        except Exception as exc:  # noqa: BLE001 - a broken check must not hide the rest
            checks.append({
                "id": check.get("id") or str(check.get("kind")),
                "kind": check.get("kind"),
                "label": check.get("label") or str(check.get("kind")),
                "passed": False,
                "observed": f"check error: {type(exc).__name__}: {str(exc)[:160]}",
            })

    judges = []
    transcript_text = transcript_for_judge(history)
    for item in manifest.get("judge") or []:
        try:
            verdict, usage = await loop.run_in_executor(
                None, judge_item, item, str(manifest.get("facts") or ""), transcript_text
            )
        except Exception as exc:  # noqa: BLE001 - keep the scenario result even if the judge call fails
            verdict = {"id": item.get("id"), "score": None, "reason": f"judge call failed: {str(exc)[:200]}"}
            usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        for key in judge_usage:
            judge_usage[key] += int(usage.get(key) or 0)
        judges.append(verdict)

    runs_ok = bool(runs) and all(r["status"] == "succeeded" and not r["error"] for r in runs)
    checks_ok = all(c["passed"] for c in checks)
    judge_ok = all(j["score"] == 2 for j in judges)
    if not runs_ok:
        status = "error"
    elif checks_ok and judge_ok:
        status = "pass"
    else:
        status = "fail"
    total = agent["total_tokens"] + sim_usage["total_tokens"] + judge_usage["total_tokens"]
    usage = {
        "agent": agent,
        "sim_user": sim_usage,
        "judge": judge_usage,
        "total_tokens": total,
    }
    result = {
        "round": round_id,
        "scenario": scenario_id,
        "lang": scenario.get("lang"),
        "session_id": sid,
        "status": status,
        "error": "; ".join(r["error"] for r in runs if r.get("error")) or None,
        "notes": notes,
        "runs": runs,
        "checks": checks,
        "judge": judges,
        "files": [{"rel": f["rel"], "bytes": f["bytes"]} for f in files],
        "usage": usage,
        "seconds": round(time.monotonic() - started, 1),
    }
    (out_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "events.jsonl").write_text(
        "".join(json.dumps(e, ensure_ascii=False, default=str) + "\n" for e in all_events),
        encoding="utf-8",
    )
    write_transcript(
        out_dir / "transcript.md",
        f"# {scenario_id} · round {round_id} · status {status}\n\nsession: {sid}",
        runs,
        checks,
        judges,
        usage,
        notes,
    )
    return result


def write_error_result(manifest: dict, error: str) -> dict:
    round_id = manifest.get("round", "unknown")
    scenario_id = (manifest.get("scenario") or {}).get("id", "unknown")
    out_dir = OUT_ROOT / round_id / scenario_id
    out_dir.mkdir(parents=True, exist_ok=True)
    zero = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    result = {
        "round": round_id,
        "scenario": scenario_id,
        "status": "error",
        "error": error[:2000],
        "notes": ["driver failed before the scenario finished; usage may be incomplete"],
        "runs": [],
        "checks": [],
        "judge": [],
        "files": [],
        "usage": {"agent": zero, "sim_user": zero, "judge": zero, "total_tokens": 0, "incomplete": True},
    }
    (out_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def rejudge(manifest: dict) -> dict:
    """Judge items only, on a transcript recorded by an earlier round (no agent run)."""
    history = manifest.get("history") or []
    facts = str(manifest.get("facts") or "")
    transcript_text = transcript_for_judge(history)
    total = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    verdicts = []
    for item in manifest.get("judge") or []:
        try:
            verdict, usage = judge_item(item, facts, transcript_text)
        except Exception as exc:  # noqa: BLE001 - keep the other verdicts
            verdict = {"id": item.get("id"), "score": None, "reason": f"judge call failed: {str(exc)[:200]}"}
            usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        for key in total:
            total[key] += int(usage.get(key) or 0)
        verdicts.append(verdict)
    scenario_id = str((manifest.get("scenario") or {}).get("id"))
    out_dir = OUT_ROOT / str(manifest.get("round")) / scenario_id
    out_dir.mkdir(parents=True, exist_ok=True)
    result = {"tag": manifest.get("tag"), "scenario": scenario_id, "judge": verdicts, "usage": total}
    (out_dir / f"rejudge_{manifest.get('tag')}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return result


def main() -> None:
    manifest = json.loads(base64.b64decode(MANIFEST_B64).decode("utf-8"))
    os.chdir(str(REPO_ROOT))
    strip_proxy_env()
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    if manifest.get("mode") == "rejudge":
        verdict = rejudge(manifest)
        print(
            "REJUDGE {scenario} tag={tag} scores={scores} tokens={tokens}".format(
                scenario=(manifest.get("scenario") or {}).get("id"),
                tag=manifest.get("tag"),
                scores=[j.get("score") for j in verdict["judge"]],
                tokens=verdict["usage"]["total_tokens"],
            ),
            flush=True,
        )
        sys.exit(0)
    timeout_s = float(manifest.get("timeout_seconds") or 900)
    try:
        result = asyncio.run(asyncio.wait_for(run_scenario(manifest), timeout=timeout_s + CANCEL_GRACE_S + 300))
    except Exception as exc:  # noqa: BLE001 - always leave a result file behind
        import traceback

        detail = f"{type(exc).__name__}: {exc} | {traceback.format_exc(limit=6)}"
        result = write_error_result(manifest, detail)
    usage = result.get("usage") or {}
    print(
        "EVO {scenario} status={status} checks={passed}/{total} judge={scores} tokens={tokens} seconds={secs}".format(
            scenario=result.get("scenario"),
            status=result.get("status"),
            passed=sum(1 for c in result.get("checks") or [] if c.get("passed")),
            total=len(result.get("checks") or []),
            scores=[j.get("score") for j in result.get("judge") or []],
            tokens=usage.get("total_tokens"),
            secs=result.get("seconds"),
        ),
        flush=True,
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
