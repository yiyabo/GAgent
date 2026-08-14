#!/usr/bin/env python
"""Run the live PhageScope MD-first flow through HTTP APIs.

Flow: chat create plan -> chat review -> chat optimize -> execute-full -> validate
tree, final verification, deliverables, and manuscript.md.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import hashlib
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


TERMINAL = {"completed", "succeeded", "success", "failed", "error", "done"}
SUCCESS = {"completed", "succeeded", "success", "done"}

CREATE_MESSAGE = (
    "Create a rigorous executable research plan for PhageScope Research Topic 1: "
    "predict host genus labels from /home/zczhao/Phage-Agent/phagescope and produce "
    "manuscript.md as the primary publishable Markdown manuscript. Build a plan-tree first; "
    "then we will review, optimize, and execute it. Requirements: metadata-only features, "
    "cluster-level train/test split, RandomForest and ExtraTrees baselines, publication-quality "
    "figures with source CSVs and a rich flexible figure manifest, an audit JSON, embedded "
    "Markdown figures, and final deliverables. PDF is optional and secondary."
)
REVIEW_MESSAGE = (
    "Review the current plan for publication-readiness, dependency correctness, MD-first manuscript "
    "delivery, flexible figure quality contract, and executable acceptance criteria. Use plan_operation "
    "review_plan for the bound plan and report the real result."
)
OPTIMIZE_MESSAGE = (
    "Optimize the current plan using the review feedback. Keep it flexible but engineered: do not "
    "hard-code specific chart templates, but require source data, figure_manifest_quality, embedded "
    "Markdown figures, manuscript.md, and final audit gates. Use plan_operation optimize_plan."
)


class E2EError(RuntimeError):
    pass


def log(message: str) -> None:
    print(message, flush=True)


def url(base: str, path: str, query: Optional[Dict[str, Any]] = None) -> str:
    out = base.rstrip("/") + (path if path.startswith("/") else f"/{path}")
    if query:
        filtered = {k: v for k, v in query.items() if v is not None}
        if filtered:
            out += "?" + urllib.parse.urlencode(filtered, doseq=True)
    return out


def req(
    base: str,
    method: str,
    path: str,
    *,
    owner: str,
    email: str,
    opener: Optional[urllib.request.OpenerDirector] = None,
    payload: Optional[Dict[str, Any]] = None,
    query: Optional[Dict[str, Any]] = None,
    timeout: float = 120.0,
) -> Any:
    data = None
    headers = {"Accept": "application/json", "X-Forwarded-User": owner, "X-Forwarded-Email": email}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url(base, path, query), data=data, headers=headers, method=method.upper())
    try:
        active_opener = opener or urllib.request.build_opener()
        with active_opener.open(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise E2EError(f"{method} {path} HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise E2EError(f"{method} {path} failed: {exc.reason}") from exc
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise E2EError(f"{method} {path} returned non-JSON: {raw[:500]}") from exc


def is_terminal(status: Any) -> bool:
    return str(status or "").strip().lower() in TERMINAL


def is_success(status: Any) -> bool:
    return str(status or "").strip().lower() in SUCCESS


def save_state(path: Path, state: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def collect_plan_ids(value: Any) -> List[int]:
    found: List[int] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "plan_id":
                try:
                    plan_id = int(str(child).strip())
                except (TypeError, ValueError):
                    plan_id = None
                if plan_id and plan_id not in found:
                    found.append(plan_id)
            for nested in collect_plan_ids(child):
                if nested not in found:
                    found.append(nested)
    elif isinstance(value, list):
        for child in value:
            for nested in collect_plan_ids(child):
                if nested not in found:
                    found.append(nested)
    return found


def tracking_id(response: Dict[str, Any]) -> Optional[str]:
    raw_meta = response.get("metadata")
    meta: Dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}
    value = meta.get("tracking_id") or meta.get("job_id")
    return value.strip() if isinstance(value, str) and value.startswith("act_") else None


def poll_action(
    base: str,
    owner: str,
    email: str,
    opener: urllib.request.OpenerDirector,
    action_id: str,
    timeout_s: float,
    interval_s: float,
) -> Dict[str, Any]:
    deadline = time.time() + timeout_s
    latest: Dict[str, Any] = {}
    while time.time() < deadline:
        latest = req(base, "GET", f"/chat/actions/{action_id}", owner=owner, email=email, opener=opener)
        status = str(latest.get("status") or "").lower()
        log(f"[action] {action_id} status={status or '<none>'} plan_id={latest.get('plan_id')}")
        if is_terminal(status):
            if not is_success(status):
                raise E2EError(f"action {action_id} failed: {json.dumps(latest, ensure_ascii=False)[:2000]}")
            return latest
        time.sleep(interval_s)
    raise E2EError(f"timed out waiting for action {action_id}: {latest}")


def chat_turn(
    base: str,
    owner: str,
    email: str,
    opener: urllib.request.OpenerDirector,
    session_id: str,
    message: str,
    context: Dict[str, Any],
    timeout_s: float,
    interval_s: float,
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    response = req(
        base,
        "POST",
        "/chat/message",
        owner=owner,
        email=email,
        opener=opener,
        payload={"session_id": session_id, "mode": "assistant", "message": message, "context": context},
    )
    if not isinstance(response, dict):
        raise E2EError("/chat/message returned invalid payload")
    raw_meta = response.get("metadata")
    meta: Dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}
    log(f"[chat] metadata_status={meta.get('status')} plan_id={meta.get('plan_id')} tracking={meta.get('tracking_id')}")
    action_id = tracking_id(response)
    if action_id:
        return response, poll_action(base, owner, email, opener, action_id, timeout_s, interval_s)
    if meta.get("status") and not is_success(meta.get("status")):
        raise E2EError(f"chat turn failed: {json.dumps(response, ensure_ascii=False)[:2000]}")
    return response, None


def nodes(tree: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw = tree.get("nodes")
    if isinstance(raw, dict):
        return [node for node in raw.values() if isinstance(node, dict)]
    if isinstance(raw, list):
        return [node for node in raw if isinstance(node, dict)]
    return []


def node_status(node: Dict[str, Any]) -> str:
    return str(node.get("effective_status") or node.get("status") or "").strip().lower()


def final_task_id(tree: Dict[str, Any]) -> int:
    candidates: List[int] = []
    fallback: List[int] = []
    for node in nodes(tree):
        try:
            task_id = int(str(node.get("id") or node.get("task_id")).strip())
        except (TypeError, ValueError):
            continue
        fallback.append(task_id)
        text = f"{node.get('name') or ''}\n{node.get('instruction') or ''}".lower()
        if "submit" in text or "deliverable" in text or "manuscript.md" in text:
            candidates.append(task_id)
    if candidates:
        return max(candidates)
    if fallback:
        return max(fallback)
    raise E2EError("could not determine final task id")


def assert_tree_completed(tree: Dict[str, Any]) -> Dict[str, int]:
    all_nodes = nodes(tree)
    incomplete = [
        {"id": n.get("id") or n.get("task_id"), "name": n.get("name"), "status": node_status(n)}
        for n in all_nodes
        if node_status(n) != "completed"
    ]
    if incomplete:
        raise E2EError(f"tree has non-completed tasks: {json.dumps(incomplete, ensure_ascii=False)[:2000]}")
    return {"total": len(all_nodes), "completed": len(all_nodes)}


def start_execution(base: str, owner: str, email: str, opener: urllib.request.OpenerDirector, plan_id: int, session_id: str) -> str:
    response = req(
        base,
        "POST",
        f"/plans/{plan_id}/execute-full",
        owner=owner,
        email=email,
        opener=opener,
        payload={
            "async_mode": True,
            "deep_think": True,
            "session_id": session_id,
            "paper_mode": True,
            "skip_completed": True,
            "stop_on_failure": False,
        },
    )
    job = response.get("job") if isinstance(response.get("job"), dict) else {}
    result = response.get("result") if isinstance(response.get("result"), dict) else {}
    job_id = job.get("job_id") or result.get("job_id")
    log(f"[execute] start success={response.get('success')} job_id={job_id} message={response.get('message')}")
    if not response.get("success") or not isinstance(job_id, str):
        raise E2EError(f"execute-full did not start: {json.dumps(response, ensure_ascii=False)[:2000]}")
    return job_id


def poll_job(
    base: str,
    owner: str,
    email: str,
    opener: urllib.request.OpenerDirector,
    job_id: str,
    timeout_s: float,
    interval_s: float,
) -> Dict[str, Any]:
    deadline = time.time() + timeout_s
    latest: Dict[str, Any] = {}
    while time.time() < deadline:
        latest = req(base, "GET", f"/tasks/decompose/jobs/{job_id}", owner=owner, email=email, opener=opener)
        status = str(latest.get("status") or "").lower()
        raw_stats = latest.get("stats")
        stats: Dict[str, Any] = raw_stats if isinstance(raw_stats, dict) else {}
        log(f"[execute] job={job_id} status={status or '<none>'} progress={stats.get('progress_percent')} task={stats.get('current_task_id')}")
        if is_terminal(status):
            if not is_success(status):
                raise E2EError(f"execution job failed: {json.dumps(latest, ensure_ascii=False)[:2000]}")
            return latest
        time.sleep(interval_s)
    raise E2EError(f"timed out waiting for execution job {job_id}: {latest}")


def deliverable_paths(deliverables: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for item in deliverables.get("items") or []:
        if isinstance(item, dict):
            value = item.get("path") or item.get("relative_path") or item.get("file_path")
            if isinstance(value, str) and value not in out:
                out.append(value)
    modules = deliverables.get("modules")
    if isinstance(modules, dict):
        for module_items in modules.values():
            for item in module_items if isinstance(module_items, list) else []:
                if isinstance(item, dict):
                    value = item.get("path") or item.get("relative_path") or item.get("file_path")
                    if isinstance(value, str) and value not in out:
                        out.append(value)
    return out


def choose_manuscript(paths: Sequence[str]) -> str:
    for candidate in ("paper/manuscript.md", "manuscript.md"):
        if candidate in paths:
            return candidate
    candidates = [p for p in paths if p.endswith("manuscript.md") or p.endswith(".md")]
    if not candidates:
        raise E2EError(f"no markdown manuscript in deliverables: {paths}")
    return sorted(candidates, key=lambda p: ("manuscript" not in p.lower(), len(p)))[0]


def validate_release(deliverables: Dict[str, Any], manifest_response: Dict[str, Any]) -> Dict[str, Any]:
    paths = deliverable_paths(deliverables)
    if not paths:
        raise E2EError("no deliverables returned")
    manifest_text = json.dumps(manifest_response.get("manifest") or {}, ensure_ascii=False)[:100000]
    png_count = sum(1 for path in paths if path.endswith(".png"))
    csv_count = sum(1 for path in paths if path.endswith(".csv"))
    figure_manifest = any(path.endswith("figure_manifest.json") for path in paths) or "figure_manifest" in manifest_text
    if png_count < 3:
        raise E2EError(f"expected >=3 PNG figures, found {png_count}: {paths}")
    if csv_count < 2:
        raise E2EError(f"expected >=2 source CSVs, found {csv_count}: {paths}")
    if not figure_manifest:
        raise E2EError("figure_manifest evidence missing")
    return {"paths": paths, "png_count": png_count, "csv_count": csv_count, "figure_manifest": figure_manifest}


def validate_manuscript(text: str) -> Dict[str, Any]:
    images = text.count("![")
    chars = len(text)
    missing = [section for section in ("Abstract", "Introduction", "Methods", "Results", "Discussion") if section.lower() not in text.lower()]
    if chars < 12000:
        raise E2EError(f"manuscript.md too short: {chars}")
    if images < 3:
        raise E2EError(f"manuscript.md embeds fewer than 3 figures: {images}")
    if missing:
        raise E2EError(f"manuscript.md missing sections: {missing}")
    return {"chars": chars, "embedded_images": images, "missing_sections": missing}


def authenticate(base: str, email: str, password: str) -> Tuple[urllib.request.OpenerDirector, Dict[str, Any]]:
    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
    payload = {"email": email, "password": password}
    try:
        auth = req(base, "POST", "/auth/register", owner=email, email=email, opener=opener, payload=payload)
        log(f"[auth] registered {email}")
    except E2EError as exc:
        if "HTTP 409" not in str(exc):
            raise
        auth = req(base, "POST", "/auth/login", owner=email, email=email, opener=opener, payload=payload)
        log(f"[auth] logged in existing {email}")
    if not isinstance(auth, dict) or not auth.get("authenticated"):
        raise E2EError(f"authentication failed: {auth}")
    return opener, auth


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:9000")
    parser.add_argument("--owner")
    parser.add_argument("--email")
    parser.add_argument("--session-id")
    parser.add_argument("--state-path", default="runtime/phagescope_live_e2e_state.json")
    parser.add_argument("--output-dir", default="runtime/phagescope_live_e2e_outputs")
    parser.add_argument("--chat-timeout-sec", type=float, default=1800)
    parser.add_argument("--execute-timeout-sec", type=float, default=14400)
    parser.add_argument("--poll-interval-sec", type=float, default=10)
    args = parser.parse_args(argv)

    stamp = int(time.time())
    owner = args.owner or f"phagescope.e2e.{stamp}"
    email = args.email or f"phagescope.e2e.{stamp}@example.com"
    password = f"PhageScopeE2E-{stamp}-Password"
    session_id = args.session_id or f"phagescope-live-e2e-{stamp}"
    state_path = Path(args.state_path)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    state: Dict[str, Any] = {"owner": owner, "email": email, "session_id": session_id, "base_url": args.base_url}
    save_state(state_path, state)

    opener, auth = authenticate(args.base_url, email, password)
    state["auth"] = auth
    save_state(state_path, state)

    status = req(args.base_url, "GET", "/chat/status", owner=owner, email=email, opener=opener)
    log(f"[preflight] chat_status={status.get('status') if isinstance(status, dict) else status}")
    state["chat_status"] = status
    save_state(state_path, state)

    context = {"default_llm_provider": "qwen", "default_base_model": "qwen3.6-plus"}
    create_response, create_action = chat_turn(args.base_url, owner, email, opener, session_id, CREATE_MESSAGE, context, args.chat_timeout_sec, args.poll_interval_sec)
    plan_ids = collect_plan_ids(create_response) + collect_plan_ids(create_action)
    if not plan_ids:
        raise E2EError("could not extract plan_id from create-plan turn")
    plan_id = max(plan_ids)
    state.update({"plan_id": plan_id, "create_response": create_response, "create_action": create_action})
    save_state(state_path, state)
    log(f"[plan] created plan_id={plan_id}")

    bound_context = {**context, "plan_id": plan_id}
    review_response, review_action = chat_turn(args.base_url, owner, email, opener, session_id, REVIEW_MESSAGE, bound_context, args.chat_timeout_sec, args.poll_interval_sec)
    state.update({"review_response": review_response, "review_action": review_action})
    save_state(state_path, state)
    optimize_response, optimize_action = chat_turn(args.base_url, owner, email, opener, session_id, OPTIMIZE_MESSAGE, bound_context, args.chat_timeout_sec, args.poll_interval_sec)
    state.update({"optimize_response": optimize_response, "optimize_action": optimize_action})
    save_state(state_path, state)

    tree = req(args.base_url, "GET", f"/plans/{plan_id}/tree", owner=owner, email=email, opener=opener)
    final_id = final_task_id(tree)
    state["optimized_tree_summary"] = {"nodes": len(nodes(tree)), "final_task_id": final_id}
    save_state(state_path, state)
    log(f"[plan] optimized nodes={len(nodes(tree))} final_task_id={final_id}")

    job_id = start_execution(args.base_url, owner, email, opener, plan_id, session_id)
    state["execute_job_id"] = job_id
    save_state(state_path, state)
    execution = poll_job(args.base_url, owner, email, opener, job_id, args.execute_timeout_sec, args.poll_interval_sec)
    state["execution_result"] = execution
    save_state(state_path, state)

    final_tree = req(args.base_url, "GET", f"/plans/{plan_id}/tree", owner=owner, email=email, opener=opener)
    tree_validation = assert_tree_completed(final_tree)
    verify = req(args.base_url, "POST", f"/tasks/{final_id}/verify", owner=owner, email=email, opener=opener, query={"plan_id": plan_id})
    if not isinstance(verify, dict) or not verify.get("success"):
        raise E2EError(f"final task verify failed: {json.dumps(verify, ensure_ascii=False)[:2000]}")
    state.update({"tree_validation": tree_validation, "final_task_verify": verify})
    save_state(state_path, state)

    deliverables = req(args.base_url, "GET", f"/artifacts/sessions/{session_id}/deliverables", owner=owner, email=email, opener=opener, query={"include_draft": "true"})
    manifest = req(args.base_url, "GET", f"/artifacts/sessions/{session_id}/deliverables/manifest", owner=owner, email=email, opener=opener)
    release = validate_release(deliverables, manifest)
    manuscript_path = choose_manuscript(release["paths"])
    manuscript_payload = req(
        args.base_url,
        "GET",
        f"/artifacts/sessions/{session_id}/deliverables/text",
        owner=owner,
        email=email,
        opener=opener,
        query={"path": manuscript_path, "max_bytes": 2000000},
    )
    manuscript_text = manuscript_payload.get("content") if isinstance(manuscript_payload, dict) else None
    if not isinstance(manuscript_text, str):
        raise E2EError(f"could not fetch manuscript text for {manuscript_path}")
    manuscript_validation = validate_manuscript(manuscript_text)
    digest = hashlib.sha256(manuscript_text.encode("utf-8")).hexdigest()
    out_path = output_dir / f"plan_{plan_id}_manuscript.md"
    out_path.write_text(manuscript_text, encoding="utf-8")
    state.update(
        {
            "deliverables": deliverables,
            "manifest": manifest,
            "release_validation": release,
            "manuscript_path": manuscript_path,
            "local_manuscript_path": str(out_path),
            "manuscript_sha256": digest,
            "manuscript_validation": manuscript_validation,
        }
    )
    save_state(state_path, state)
    log(f"[done] plan_id={plan_id} session_id={session_id} manuscript={out_path} chars={manuscript_validation['chars']} images={manuscript_validation['embedded_images']} sha256={digest}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except E2EError as exc:
        print(f"[error] {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1)
