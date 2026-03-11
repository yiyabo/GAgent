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
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


DEFAULT_BASE_URL = "http://127.0.0.1:9000"
ABSTRACT_SMOKE_MESSAGE = (
    "请帮我生成一篇关于 Pseudomonas phage 的英文综述初稿，带参考文献。"
    "先收集文献证据，再生成一个最小 smoke test 版本，只写 abstract 即可。"
    "请尽量减少外部依赖，控制范围在最小可验证结果，并发布 deliverables。"
)
STRICT_FULL_MESSAGE = (
    "请使用 review_pack_writer 为我生成一篇关于 Pseudomonas phage 的完整英文综述终稿，而不是 abstract-only smoke test。"
    "必须先收集充分的文献证据，再生成完整全文。章节必须至少包括"
    " abstract, introduction, method, experiment, result, discussion, conclusion 和 references。"
    "请使用高强度参数：高检索量、尽可能多的相关文献、较高 max_revisions，以及 evaluation_threshold 不低于 0.8。"
    "不要只输出 abstract，不要接受 partial/draft 作为完成标准；如果质量门未全部通过，也请保留完整 deliverables 供审查。"
)
TERMINAL_ACTION_STATUSES = {"completed", "failed", "succeeded"}
SUCCESS_ACTION_STATUSES = {"completed", "succeeded"}
PLACEHOLDER_MARKERS = ("AUTO_PLACEHOLDER", "% TODO", "TODO")
ABSTRACT_ONLY_PROFILE = "abstract_smoke"
STRICT_FULL_PROFILE = "strict_full_manuscript"
STRICT_FULL_SECTIONS = (
    "abstract",
    "introduction",
    "method",
    "experiment",
    "result",
    "discussion",
    "conclusion",
)
STRICT_FULL_SECTION_PATHS = {
    "abstract": "paper/sections/abstract.tex",
    "introduction": "paper/sections/introduction.tex",
    "method": "paper/sections/method.tex",
    "experiment": "paper/sections/experiment.tex",
    "result": "paper/sections/result.tex",
    "discussion": "paper/sections/discussion.tex",
    "conclusion": "paper/sections/conclusion.tex",
}


def _profile_defaults(profile: str) -> Dict[str, Any]:
    normalized = str(profile or ABSTRACT_ONLY_PROFILE).strip().lower()
    if normalized == STRICT_FULL_PROFILE:
        return {
            "message": STRICT_FULL_MESSAGE,
            "required_modules": ["paper", "refs"],
            "min_completed_sections": len(STRICT_FULL_SECTIONS),
            "action_timeout_sec": 3600.0,
            "deliverable_timeout_sec": 900.0,
        }
    return {
        "message": ABSTRACT_SMOKE_MESSAGE,
        "required_modules": ["paper", "refs"],
        "min_completed_sections": 1,
        "action_timeout_sec": 600.0,
        "deliverable_timeout_sec": 60.0,
    }


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


def _dedupe_strings(values: Iterable[str]) -> List[str]:
    deduped: List[str] = []
    for value in values:
        item = str(value or "").strip()
        if item and item not in deduped:
            deduped.append(item)
    return deduped


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
    include_draft: bool = False,
) -> Dict[str, Any]:
    deliverables = _request_json(
        base_url=base_url,
        method="GET",
        path=f"/artifacts/sessions/{session_id}/deliverables",
        query={"include_draft": "true"} if include_draft else None,
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


def _fetch_artifact_text(
    *,
    base_url: str,
    session_id: str,
    path: str,
    timeout: float,
) -> str:
    payload = _request_json(
        base_url=base_url,
        method="GET",
        path=f"/artifacts/sessions/{session_id}/text",
        query={"path": path},
        timeout=timeout,
    )
    if not isinstance(payload, dict):
        raise SmokeTestError(f"Artifact text response for {path} is invalid.")
    content = payload.get("content")
    if not isinstance(content, str):
        raise SmokeTestError(f"Artifact text response for {path} has no content.")
    return content


def _fetch_deliverables_manifest(
    *,
    base_url: str,
    session_id: str,
    timeout: float,
) -> Dict[str, Any]:
    payload = _request_json(
        base_url=base_url,
        method="GET",
        path=f"/artifacts/sessions/{session_id}/deliverables/manifest",
        timeout=timeout,
    )
    if not isinstance(payload, dict):
        raise SmokeTestError("Deliverables manifest response is not a JSON object.")
    return payload


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


def _primary_action(action_result: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(action_result, dict):
        return None
    actions = action_result.get("actions")
    if not isinstance(actions, list) or not actions:
        return None
    first = actions[0]
    return first if isinstance(first, dict) else None


def _extract_storage_relative_paths(action_result: Optional[Dict[str, Any]]) -> List[str]:
    if not isinstance(action_result, dict):
        return []

    candidates: List[str] = []

    def _maybe_add(value: Any) -> None:
        if isinstance(value, str) and value.strip():
            candidates.append(value.strip())

    primary = _primary_action(action_result)
    if isinstance(primary, dict):
        details = primary.get("details")
        if isinstance(details, dict):
            storage = details.get("storage")
            if isinstance(storage, dict):
                _maybe_add(storage.get("result_path"))
            result_payload = details.get("result")
            if isinstance(result_payload, dict):
                result_storage = result_payload.get("storage")
                if isinstance(result_storage, dict):
                    relative = result_storage.get("relative")
                    if isinstance(relative, dict):
                        _maybe_add(relative.get("result_path"))
                    _maybe_add(result_storage.get("result_path"))

    result_payload = action_result.get("result")
    if isinstance(result_payload, dict):
        tool_results = result_payload.get("tool_results")
        if isinstance(tool_results, list):
            for row in tool_results:
                if not isinstance(row, dict):
                    continue
                tool_result = row.get("result")
                if not isinstance(tool_result, dict):
                    continue
                storage = tool_result.get("storage")
                if isinstance(storage, dict):
                    relative = storage.get("relative")
                    if isinstance(relative, dict):
                        _maybe_add(relative.get("result_path"))
                    _maybe_add(storage.get("result_path"))

    return _dedupe_strings(candidates)


def _parse_json_text(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _fetch_stored_tool_result(
    *,
    base_url: str,
    session_id: str,
    action_result: Optional[Dict[str, Any]],
    timeout: float,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    for relative_path in _extract_storage_relative_paths(action_result):
        try:
            content = _fetch_artifact_text(
                base_url=base_url,
                session_id=session_id,
                path=relative_path,
                timeout=timeout,
            )
        except SmokeTestError:
            continue
        parsed = _parse_json_text(content)
        if parsed is not None:
            return parsed, relative_path
    return None, None


def _strict_full_failure_reason(
    *,
    action_result: Optional[Dict[str, Any]],
    stored_result: Optional[Dict[str, Any]],
    deliverables: Optional[Dict[str, Any]],
) -> str:
    primary = _primary_action(action_result)
    action_name = str((primary or {}).get("name") or "").strip().lower()
    if action_name != "review_pack_writer":
        return "routing failure"

    result_payload = action_result.get("result") if isinstance(action_result, dict) else None
    if (
        isinstance(result_payload, dict)
        and isinstance(result_payload.get("deep_think_retry"), dict)
        and result_payload["deep_think_retry"].get("success")
        and isinstance(stored_result, dict)
        and stored_result.get("success") is False
    ):
        return "retry masking"

    if not isinstance(stored_result, dict):
        return "publish failure"

    pack = stored_result.get("pack")
    if isinstance(pack, dict) and pack.get("success") is not True:
        return "retrieval failure"

    draft = stored_result.get("draft")
    if isinstance(draft, dict):
        quality_gate = draft.get("quality_gate_passed")
        failed_sections = draft.get("failed_sections")
        if quality_gate is not True or (isinstance(failed_sections, list) and failed_sections):
            return "quality-gate failure"

    if isinstance(deliverables, dict):
        modules = deliverables.get("modules")
        paper_status = deliverables.get("paper_status")
        if not isinstance(modules, dict) or "paper" not in modules or "refs" not in modules:
            return "publish failure"
        if not isinstance(paper_status, dict):
            return "publish failure"
        completed_count = int(paper_status.get("completed_count") or 0)
        if completed_count < len(STRICT_FULL_SECTIONS):
            return "publish failure"

    return "publish failure"


def _build_failure_bundle(
    *,
    base_url: str,
    session_id: Optional[str],
    tracking_id: Optional[str],
    action_result: Optional[Dict[str, Any]],
    request_timeout: float,
) -> Dict[str, Any]:
    deliverables = None
    deliverables_manifest = None
    stored_result = None
    stored_result_path = None
    if session_id:
        try:
            deliverables = _fetch_deliverables(
                base_url=base_url,
                session_id=session_id,
                timeout=request_timeout,
                include_draft=True,
            )
        except SmokeTestError as exc:
            deliverables = {"fetch_error": str(exc)}
        try:
            deliverables_manifest = _fetch_deliverables_manifest(
                base_url=base_url,
                session_id=session_id,
                timeout=request_timeout,
            )
        except SmokeTestError as exc:
            deliverables_manifest = {"fetch_error": str(exc)}
        stored_result, stored_result_path = _fetch_stored_tool_result(
            base_url=base_url,
            session_id=session_id,
            action_result=action_result,
            timeout=request_timeout,
        )

    draft = stored_result.get("draft") if isinstance(stored_result, dict) else None
    failure_reason = _strict_full_failure_reason(
        action_result=action_result,
        stored_result=stored_result,
        deliverables=deliverables if isinstance(deliverables, dict) else None,
    )
    return {
        "session_id": session_id,
        "tracking_id": tracking_id,
        "failure_reason": failure_reason,
        "action_result": action_result,
        "stored_result_path": stored_result_path,
        "stored_result": stored_result,
        "section_scores": draft.get("section_scores") if isinstance(draft, dict) else None,
        "failed_sections": draft.get("failed_sections") if isinstance(draft, dict) else None,
        "analysis_text": (
            action_result.get("result", {}).get("analysis_text")
            if isinstance(action_result, dict) and isinstance(action_result.get("result"), dict)
            else None
        ),
        "deliverables": deliverables,
        "deliverables_manifest": deliverables_manifest,
    }


def _validate_strict_full_run(
    *,
    action_result: Dict[str, Any],
    stored_result: Dict[str, Any],
    deliverables: Dict[str, Any],
    section_texts: Dict[str, str],
    references_text: str,
) -> None:
    primary = _primary_action(action_result)
    if not isinstance(primary, dict):
        raise SmokeTestError("Strict validation requires an executed action payload.")
    if str(primary.get("name") or "").strip().lower() != "review_pack_writer":
        raise SmokeTestError("Strict validation failed: executed action is not review_pack_writer.")
    if primary.get("success") is not True:
        raise SmokeTestError("Strict validation failed: review_pack_writer step did not succeed.")

    if stored_result.get("tool") != "review_pack_writer":
        raise SmokeTestError("Strict validation failed: stored result is not review_pack_writer.")
    if stored_result.get("success") is not True:
        raise SmokeTestError(
            f"Strict validation failed: review_pack_writer success={stored_result.get('success')!r}."
        )
    if stored_result.get("partial"):
        raise SmokeTestError("Strict validation failed: review_pack_writer returned partial output.")

    draft = stored_result.get("draft")
    if not isinstance(draft, dict):
        raise SmokeTestError("Strict validation failed: nested manuscript draft payload is missing.")
    if draft.get("quality_gate_passed") is not True:
        raise SmokeTestError(
            f"Strict validation failed: quality_gate_passed={draft.get('quality_gate_passed')!r}."
        )
    failed_sections = draft.get("failed_sections")
    if isinstance(failed_sections, list) and failed_sections:
        raise SmokeTestError(
            "Strict validation failed: failed_sections="
            + json.dumps(failed_sections, ensure_ascii=False)
        )

    modules = deliverables.get("modules")
    if not isinstance(modules, dict):
        raise SmokeTestError("Strict validation failed: deliverables modules map is missing.")
    missing_modules = [module for module in ("paper", "refs") if module not in modules]
    if missing_modules:
        raise SmokeTestError(
            "Strict validation failed: missing deliverable modules "
            + ", ".join(missing_modules)
        )

    paper_status = deliverables.get("paper_status")
    if not isinstance(paper_status, dict):
        raise SmokeTestError("Strict validation failed: paper_status is missing.")
    completed_sections = paper_status.get("completed_sections")
    if not isinstance(completed_sections, list):
        raise SmokeTestError("Strict validation failed: completed_sections is missing.")
    normalized_completed = {str(item).strip().lower() for item in completed_sections if str(item).strip()}
    missing_sections = [name for name in STRICT_FULL_SECTIONS if name not in normalized_completed]
    completed_count = int(paper_status.get("completed_count") or 0)
    if completed_count != len(STRICT_FULL_SECTIONS) or missing_sections:
        raise SmokeTestError(
            "Strict validation failed: completed_count="
            f"{completed_count}, missing_sections={json.dumps(missing_sections, ensure_ascii=False)}"
        )

    items = deliverables.get("items")
    if not isinstance(items, list):
        raise SmokeTestError("Strict validation failed: deliverable items are missing.")
    draft_paths = [
        str(item.get("path") or "")
        for item in items
        if isinstance(item, dict)
        and str(item.get("status") or "").strip().lower() == "draft"
        and (
            str(item.get("module") or "").strip().lower() in {"paper", "refs"}
            or str(item.get("path") or "").startswith("paper/")
            or str(item.get("path") or "").startswith("refs/")
        )
    ]
    if draft_paths:
        raise SmokeTestError(
            "Strict validation failed: draft deliverables are still being exposed for paper/refs: "
            + json.dumps(draft_paths, ensure_ascii=False)
        )

    for section, text in section_texts.items():
        placeholder = _contains_placeholder(text, PLACEHOLDER_MARKERS)
        if placeholder:
            raise SmokeTestError(
                f"Strict validation failed: section {section} still contains placeholder {placeholder!r}."
            )
        if len(text.strip()) < 40:
            raise SmokeTestError(f"Strict validation failed: section {section} content is unexpectedly short.")

    if not references_text.strip():
        raise SmokeTestError("Strict validation failed: refs/references.bib is empty.")
    if references_text.strip() == "% references":
        raise SmokeTestError("Strict validation failed: refs/references.bib is still placeholder.")


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


def _collect_section_texts(
    *,
    base_url: str,
    session_id: str,
    timeout: float,
    profile: str,
) -> Dict[str, str]:
    if profile != STRICT_FULL_PROFILE:
        return {
            "abstract": _fetch_deliverable_text(
                base_url=base_url,
                session_id=session_id,
                path="paper/sections/abstract.tex",
                timeout=timeout,
            )
        }

    collected: Dict[str, str] = {}
    for section, path in STRICT_FULL_SECTION_PATHS.items():
        collected[section] = _fetch_deliverable_text(
            base_url=base_url,
            session_id=session_id,
            path=path,
            timeout=timeout,
        )
    return collected


def run_smoke_test(args: argparse.Namespace) -> int:
    profile_config = _profile_defaults(args.profile)
    request_timeout = float(args.request_timeout)
    action_timeout_sec = (
        float(args.action_timeout_sec)
        if args.action_timeout_sec is not None
        else float(profile_config["action_timeout_sec"])
    )
    deliverable_timeout_sec = (
        float(args.deliverable_timeout_sec)
        if args.deliverable_timeout_sec is not None
        else float(profile_config["deliverable_timeout_sec"])
    )
    message = args.message or str(profile_config["message"])
    required_modules = _dedupe_strings(
        list(profile_config["required_modules"]) + list(args.required_module or [])
    )
    min_completed_sections = (
        int(args.min_completed_sections)
        if args.min_completed_sections is not None
        else int(profile_config["min_completed_sections"])
    )
    session_name = args.session_name or (
        "http-strict-full-manuscript"
        if args.profile == STRICT_FULL_PROFILE
        else "http-smoke-review-pack"
    )
    session_id: Optional[str] = None
    tracking_id: Optional[str] = None
    action_result: Optional[Dict[str, Any]] = None

    try:
        _log(f"[preflight] base_url={args.base_url} profile={args.profile}")
        health = _request_json(
            base_url=args.base_url,
            method="GET",
            path="/health",
            timeout=request_timeout,
        )
        _log(f"[preflight] /health -> {json.dumps(health, ensure_ascii=False)}")

        chat_status = _request_json(
            base_url=args.base_url,
            method="GET",
            path="/chat/status",
            timeout=request_timeout,
        )
        _log(
            "[preflight] /chat/status -> "
            f"status={chat_status.get('status')} provider={((chat_status.get('llm') or {}).get('provider'))} "
            f"model={((chat_status.get('llm') or {}).get('model'))}"
        )

        session_id = _provision_session(
            base_url=args.base_url,
            session_name=session_name,
            timeout=request_timeout,
        )

        chat_payload = {
            "session_id": session_id,
            "mode": "assistant",
            "message": message,
        }
        response = _request_json(
            base_url=args.base_url,
            method="POST",
            path="/chat/message",
            payload=chat_payload,
            timeout=request_timeout,
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

        if isinstance(tracking_id, str) and tracking_id.strip():
            action_result = _poll_action_status(
                base_url=args.base_url,
                tracking_id=tracking_id.strip(),
                timeout_sec=action_timeout_sec,
                poll_interval_sec=args.poll_interval_sec,
                request_timeout=request_timeout,
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
            wait_timeout_sec=deliverable_timeout_sec,
            poll_interval_sec=args.poll_interval_sec,
            request_timeout=request_timeout,
            required_modules=required_modules,
            min_completed_sections=min_completed_sections,
        )

        section_texts = _collect_section_texts(
            base_url=args.base_url,
            session_id=session_id,
            timeout=request_timeout,
            profile=args.profile,
        )
        references_text = _fetch_deliverable_text(
            base_url=args.base_url,
            session_id=session_id,
            path="refs/references.bib",
            timeout=request_timeout,
        )

        stored_result = None
        stored_result_path = None
        if action_result is not None:
            stored_result, stored_result_path = _fetch_stored_tool_result(
                base_url=args.base_url,
                session_id=session_id,
                action_result=action_result,
                timeout=request_timeout,
            )

        if args.profile == STRICT_FULL_PROFILE:
            if stored_result is None:
                raise SmokeTestError("Strict validation failed: could not fetch stored review_pack_writer result.json.")
            _validate_strict_full_run(
                action_result=action_result or {},
                stored_result=stored_result,
                deliverables=deliverables,
                section_texts=section_texts,
                references_text=references_text,
            )

        paper_status = deliverables.get("paper_status") or {}
        summary = {
            "profile": args.profile,
            "session_id": session_id,
            "tracking_id": tracking_id,
            "executed_action": (action_result or {}).get("actions", [{}])[0].get("name")
            if isinstance((action_result or {}).get("actions"), list) and (action_result or {}).get("actions")
            else None,
            "quality_gate_passed": (
                stored_result.get("draft", {}).get("quality_gate_passed")
                if isinstance(stored_result, dict) and isinstance(stored_result.get("draft"), dict)
                else None
            ),
            "stored_result_path": stored_result_path,
            "completed_sections": paper_status.get("completed_sections"),
            "completed_count": paper_status.get("completed_count"),
            "modules": sorted((deliverables.get("modules") or {}).keys()),
            "deliverable_statuses": sorted(
                {
                    str(item.get("status") or "")
                    for item in (deliverables.get("items") or [])
                    if isinstance(item, dict)
                }
            ),
            "abstract_preview": (section_texts.get("abstract") or "")[:240],
        }
        if isinstance(stored_result, dict) and isinstance(stored_result.get("draft"), dict):
            summary["section_scores"] = stored_result["draft"].get("section_scores")
            summary["failed_sections"] = stored_result["draft"].get("failed_sections")
        _log("[result] smoke test passed")
        _log(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except SmokeTestError:
        failure_bundle = _build_failure_bundle(
            base_url=args.base_url,
            session_id=session_id,
            tracking_id=tracking_id if isinstance(tracking_id, str) else None,
            action_result=action_result,
            request_timeout=request_timeout,
        )
        _log("[failure_bundle]")
        _log(json.dumps(failure_bundle, ensure_ascii=False, indent=2))
        raise


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
        "--profile",
        choices=[ABSTRACT_ONLY_PROFILE, STRICT_FULL_PROFILE],
        default=ABSTRACT_ONLY_PROFILE,
        help="Validation profile to run.",
    )
    parser.add_argument(
        "--session-name",
        default=None,
        help="Name used when creating the chat session.",
    )
    parser.add_argument(
        "--message",
        default=None,
        help="Prompt sent to /chat/message. If omitted, uses the profile default prompt.",
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
        default=None,
        help="How long to wait for /chat/actions/{tracking_id}. Defaults by profile.",
    )
    parser.add_argument(
        "--deliverable-timeout-sec",
        type=float,
        default=None,
        help="How long to wait for deliverables to appear after completion. Defaults by profile.",
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
        default=[],
        help="Deliverable module that must exist. Can be passed multiple times.",
    )
    parser.add_argument(
        "--min-completed-sections",
        type=int,
        default=None,
        help="Minimum paper_status.completed_count. Defaults by profile.",
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
