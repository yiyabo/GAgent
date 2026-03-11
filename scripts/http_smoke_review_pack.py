#!/usr/bin/env python3
"""Smoke test article generation through the chat HTTP API."""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, Optional, Sequence


DEFAULT_BASE_URL = "http://127.0.0.1:9000"
DEFAULT_MESSAGE = (
    "请直接调用 review_pack_writer 做一个最小 smoke test。"
    "topic='Pseudomonas phage'，query='pseudomonas phage'，"
    "max_results=2，download_pdfs=false，sections=['abstract']，"
    "max_revisions=1，evaluation_threshold=0.55。"
    "只需要产出一个英文综述初稿，并发布 deliverables。"
)
TERMINAL_ACTION_STATUSES = {"completed", "failed", "succeeded"}
SUCCESS_ACTION_STATUSES = {"completed", "succeeded"}
PLACEHOLDER_MARKERS = ("AUTO_PLACEHOLDER", "% TODO", "TODO")


class SmokeTestError(RuntimeError):
    """Raised when the smoke test cannot complete successfully."""


def _build_url(base_url: str, path: str, query: Optional[Dict[str, Any]] = None) -> str:
    normalized_base = base_url.rstrip("/")
    normalized_path = path if path.startswith("/") else f"/{path}"
    if not query:
        return f"{normalized_base}{normalized_path}"
    filtered = {
        key: value
        for key, value in query.items()
        if value is not None
    }
    return f"{normalized_base}{normalized_path}?{urllib.parse.urlencode(filtered, doseq=True)}"


def _request_json(
    *,
    base_url: str,
    method: str,
    path: str,
    payload: Optional[Dict[str, Any]] = None,
    query: Optional[Dict[str, Any]] = None,
    timeout: float = 30.0,
) -> Any:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        _build_url(base_url, path, query=query),
        data=data,
        headers=headers,
        method=method.upper(),
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise SmokeTestError(
            f"{method.upper()} {path} returned HTTP {exc.code}: {error_body}"
        ) from exc
    except urllib.error.URLError as exc:
        raise SmokeTestError(f"{method.upper()} {path} failed: {exc.reason}") from exc

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SmokeTestError(
            f"{method.upper()} {path} returned non-JSON payload: {raw[:400]}"
        ) from exc


def _log(message: str) -> None:
    print(message, flush=True)


def _terminal_status(status: Optional[str]) -> bool:
    return str(status or "").strip().lower() in TERMINAL_ACTION_STATUSES


def _success_status(status: Optional[str]) -> bool:
    return str(status or "").strip().lower() in SUCCESS_ACTION_STATUSES


def _contains_placeholder(text: str, markers: Iterable[str]) -> Optional[str]:
    for marker in markers:
        if marker in text:
            return marker
    return None


def _fetch_deliverables(
    *,
    base_url: str,
    session_id: str,
    timeout: float,
) -> Dict[str, Any]:
    deliverables = _request_json(
        base_url=base_url,
        method="GET",
        path=f"/artifacts/sessions/{session_id}/deliverables",
        timeout=timeout,
    )
    if not isinstance(deliverables, dict):
        raise SmokeTestError("Deliverables response is not a JSON object.")
    return deliverables


def _provision_session(base_url: str, session_name: str, timeout: float) -> str:
    try:
        payload = _request_json(
            base_url=base_url,
            method="POST",
            path="/sessions",
            payload={"name": session_name},
            timeout=timeout,
        )
        if isinstance(payload, dict) and isinstance(payload.get("id"), str):
            session_id = payload["id"]
            _log(f"[session] created via /sessions session_id={session_id}")
            return session_id
    except SmokeTestError as exc:
        if "HTTP 404" not in str(exc):
            raise
        _log("[session] /sessions is not exposed; falling back to chat auto-create")

    session_id = f"session_{int(time.time() * 1000)}_httpsmoke"
    _log(f"[session] using generated session_id={session_id}")
    return session_id


def _fetch_deliverable_text(
    *,
    base_url: str,
    session_id: str,
    path: str,
    timeout: float,
) -> str:
    payload = _request_json(
        base_url=base_url,
        method="GET",
        path=f"/artifacts/sessions/{session_id}/deliverables/text",
        query={"path": path},
        timeout=timeout,
    )
    if not isinstance(payload, dict):
        raise SmokeTestError(f"Deliverable text response for {path} is invalid.")
    content = payload.get("content")
    if not isinstance(content, str):
        raise SmokeTestError(f"Deliverable text response for {path} has no content.")
    return content


def _validate_deliverables(
    *,
    deliverables: Dict[str, Any],
    abstract_text: str,
    references_text: str,
    required_modules: Sequence[str],
    min_completed_sections: int,
) -> None:
    modules = deliverables.get("modules")
    if not isinstance(modules, dict):
        raise SmokeTestError("Deliverables payload has no modules map.")

    missing_modules = [module for module in required_modules if module not in modules]
    if missing_modules:
        raise SmokeTestError(
            f"Deliverables missing required modules: {', '.join(missing_modules)}"
        )

    paper_status = deliverables.get("paper_status")
    if not isinstance(paper_status, dict):
        raise SmokeTestError("Deliverables payload has no paper_status.")

    completed_count = int(paper_status.get("completed_count") or 0)
    if completed_count < min_completed_sections:
        raise SmokeTestError(
            f"paper_status.completed_count={completed_count} < {min_completed_sections}"
        )

    placeholder = _contains_placeholder(abstract_text, PLACEHOLDER_MARKERS)
    if placeholder:
        raise SmokeTestError(
            f"paper/sections/abstract.tex still contains placeholder marker {placeholder!r}"
        )

    if not references_text.strip():
        raise SmokeTestError("refs/references.bib is empty.")
    if references_text.strip() == "% references":
        raise SmokeTestError("refs/references.bib is still the placeholder file.")


def _poll_action_status(
    *,
    base_url: str,
    tracking_id: str,
    timeout_sec: float,
    poll_interval_sec: float,
    request_timeout: float,
) -> Dict[str, Any]:
    deadline = time.monotonic() + timeout_sec
    last_status: Optional[str] = None

    while time.monotonic() < deadline:
        payload = _request_json(
            base_url=base_url,
            method="GET",
            path=f"/chat/actions/{tracking_id}",
            timeout=request_timeout,
        )
        if not isinstance(payload, dict):
            raise SmokeTestError("Action status response is not a JSON object.")

        status = str(payload.get("status") or "").strip().lower()
        if status != last_status:
            _log(f"[poll] tracking_id={tracking_id} status={status or '<empty>'}")
            last_status = status

        if _terminal_status(status):
            return payload

        time.sleep(poll_interval_sec)

    raise SmokeTestError(
        f"Timed out after {timeout_sec:.0f}s while waiting for action {tracking_id}."
    )


def _wait_for_deliverables(
    *,
    base_url: str,
    session_id: str,
    wait_timeout_sec: float,
    poll_interval_sec: float,
    request_timeout: float,
    required_modules: Sequence[str],
    min_completed_sections: int,
) -> Dict[str, Any]:
    deadline = time.monotonic() + wait_timeout_sec
    last_error: Optional[str] = None

    while time.monotonic() < deadline:
        try:
            deliverables = _fetch_deliverables(
                base_url=base_url,
                session_id=session_id,
                timeout=request_timeout,
            )
            abstract_text = _fetch_deliverable_text(
                base_url=base_url,
                session_id=session_id,
                path="paper/sections/abstract.tex",
                timeout=request_timeout,
            )
            references_text = _fetch_deliverable_text(
                base_url=base_url,
                session_id=session_id,
                path="refs/references.bib",
                timeout=request_timeout,
            )
            _validate_deliverables(
                deliverables=deliverables,
                abstract_text=abstract_text,
                references_text=references_text,
                required_modules=required_modules,
                min_completed_sections=min_completed_sections,
            )
            deliverables["_smoke_abstract_text"] = abstract_text
            deliverables["_smoke_references_text"] = references_text
            return deliverables
        except SmokeTestError as exc:
            last_error = str(exc)
            time.sleep(poll_interval_sec)

    raise SmokeTestError(
        "Deliverables did not become ready in time."
        + (f" Last error: {last_error}" if last_error else "")
    )


def run_smoke_test(args: argparse.Namespace) -> int:
    required_modules = [item.strip() for item in args.required_module if item.strip()]

    _log(f"[preflight] base_url={args.base_url}")
    health = _request_json(
        base_url=args.base_url,
        method="GET",
        path="/health",
        timeout=args.request_timeout,
    )
    _log(f"[preflight] /health -> {json.dumps(health, ensure_ascii=False)}")

    chat_status = _request_json(
        base_url=args.base_url,
        method="GET",
        path="/chat/status",
        timeout=args.request_timeout,
    )
    _log(
        "[preflight] /chat/status -> "
        f"status={chat_status.get('status')} provider={((chat_status.get('llm') or {}).get('provider'))} "
        f"model={((chat_status.get('llm') or {}).get('model'))}"
    )

    session_id = _provision_session(
        base_url=args.base_url,
        session_name=args.session_name,
        timeout=args.request_timeout,
    )

    chat_payload = {
        "session_id": session_id,
        "mode": "assistant",
        "message": args.message,
    }
    response = _request_json(
        base_url=args.base_url,
        method="POST",
        path="/chat/message",
        payload=chat_payload,
        timeout=args.request_timeout,
    )
    if not isinstance(response, dict):
        raise SmokeTestError("/chat/message returned an invalid payload.")

    metadata = response.get("metadata") if isinstance(response.get("metadata"), dict) else {}
    tracking_id = metadata.get("tracking_id")
    initial_status = metadata.get("status")
    _log(
        "[chat] submitted message"
        + (f" tracking_id={tracking_id}" if isinstance(tracking_id, str) and tracking_id else "")
        + (f" status={initial_status}" if initial_status else "")
    )

    action_result: Optional[Dict[str, Any]] = None
    if isinstance(tracking_id, str) and tracking_id.strip():
        action_result = _poll_action_status(
            base_url=args.base_url,
            tracking_id=tracking_id.strip(),
            timeout_sec=args.action_timeout_sec,
            poll_interval_sec=args.poll_interval_sec,
            request_timeout=args.request_timeout,
        )
        action_status = action_result.get("status")
        if not _success_status(action_status):
            raise SmokeTestError(
                f"Action {tracking_id} finished with non-success status {action_status!r}: "
                f"{json.dumps(action_result.get('errors') or [], ensure_ascii=False)}"
            )
    elif initial_status and not _success_status(initial_status):
        raise SmokeTestError(f"/chat/message returned non-success status {initial_status!r}")

    deliverables = _wait_for_deliverables(
        base_url=args.base_url,
        session_id=session_id,
        wait_timeout_sec=args.deliverable_timeout_sec,
        poll_interval_sec=args.poll_interval_sec,
        request_timeout=args.request_timeout,
        required_modules=required_modules,
        min_completed_sections=args.min_completed_sections,
    )

    paper_status = deliverables.get("paper_status") or {}
    summary = {
        "session_id": session_id,
        "tracking_id": tracking_id,
        "completed_sections": paper_status.get("completed_sections"),
        "completed_count": paper_status.get("completed_count"),
        "modules": sorted((deliverables.get("modules") or {}).keys()),
        "abstract_preview": (deliverables.get("_smoke_abstract_text") or "")[:240],
    }
    _log("[result] smoke test passed")
    _log(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run an HTTP smoke test for article generation through the chat API.",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"Backend base URL. Default: {DEFAULT_BASE_URL}",
    )
    parser.add_argument(
        "--session-name",
        default="http-smoke-review-pack",
        help="Name used when creating the chat session.",
    )
    parser.add_argument(
        "--message",
        default=DEFAULT_MESSAGE,
        help="Prompt sent to /chat/message.",
    )
    parser.add_argument(
        "--request-timeout",
        type=float,
        default=30.0,
        help="Per-request timeout in seconds.",
    )
    parser.add_argument(
        "--action-timeout-sec",
        type=float,
        default=600.0,
        help="How long to wait for /chat/actions/{tracking_id}.",
    )
    parser.add_argument(
        "--deliverable-timeout-sec",
        type=float,
        default=60.0,
        help="How long to wait for deliverables to appear after completion.",
    )
    parser.add_argument(
        "--poll-interval-sec",
        type=float,
        default=5.0,
        help="Polling interval in seconds.",
    )
    parser.add_argument(
        "--required-module",
        action="append",
        default=["paper", "refs"],
        help="Deliverable module that must exist. Can be passed multiple times.",
    )
    parser.add_argument(
        "--min-completed-sections",
        type=int,
        default=1,
        help="Minimum paper_status.completed_count.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return run_smoke_test(args)
    except KeyboardInterrupt:
        _log("[error] interrupted")
        return 130
    except SmokeTestError as exc:
        _log(f"[error] {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
