"""Tool-result sanitisation, summarisation and compression utilities.

Extracted from StructuredChatAgent to keep the agent class focused on
orchestration logic.  Every function here is *stateless* – the caller
passes in whatever context is needed.

Public API
----------
sanitize_tool_result      – strip a raw tool dict down to the essentials
drop_callables            – recursively remove callable values
summarize_tool_result     – one-line human summary of a tool result
truncate_large_fields     – recursively cap string / list sizes
append_recent_tool_result – add a result to the agent's sliding window
normalize_dependencies    – coerce raw dependency lists to List[int]
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from .session_helpers import (
    _extract_phagescope_task_snapshot,
    _extract_taskid_from_result,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# normalize_dependencies
# ---------------------------------------------------------------------------

def normalize_dependencies(raw: Any) -> Optional[List[int]]:
    """Coerce a raw dependency list into ``List[int]`` or ``None``."""
    if raw is None:
        return None
    if not isinstance(raw, list):
        return None
    if len(raw) == 0:
        # Explicit empty list means "clear dependencies".
        return []
    deps: List[int] = []
    for item in raw:
        try:
            deps.append(int(item))
        except (TypeError, ValueError):
            continue
    # If user/LLM provided a non-empty list but all items were invalid,
    # treat as "no change" rather than clearing.
    if not deps:
        return None
    return deps


# ---------------------------------------------------------------------------
# sanitize_tool_result
# ---------------------------------------------------------------------------

def sanitize_tool_result(tool_name: str, raw_result: Any) -> Dict[str, Any]:
    """Strip a raw tool result dict down to essential fields.

    Each tool family has its own reducer; unknown tools go through a generic
    path that keeps commonly used keys.
    """
    if tool_name == "phagescope" and isinstance(raw_result, dict):
        sanitized: Dict[str, Any] = {
            "tool": tool_name,
            "action": raw_result.get("action"),
            "status_code": raw_result.get("status_code"),
            "success": raw_result.get("success", False),
        }
        if "error" in raw_result:
            sanitized["error"] = raw_result.get("error")
        for _k in ("artifact_scope", "local_bundle_hint", "taskid", "result_kind"):
            if _k in raw_result:
                sanitized[_k] = raw_result.get(_k)
        act_l = str(raw_result.get("action") or "").strip().lower()
        if act_l == "batch_submit":
            for key in (
                "batch_id",
                "manifest_path",
                "primary_taskid",
                "strategy",
                "requested_phage_ids",
                "warning",
            ):
                if key in raw_result:
                    sanitized[key] = raw_result.get(key)
            pst = raw_result.get("per_strain_tasks")
            if isinstance(pst, list):
                sanitized["per_strain_tasks"] = pst[:40]
            sr = raw_result.get("submit_result")
            if isinstance(sr, dict):
                sanitized["submit_result"] = {
                    "success": sr.get("success"),
                    "status_code": sr.get("status_code"),
                    "action": sr.get("action"),
                    "error": sr.get("error"),
                }
        elif act_l == "batch_reconcile":
            for key in (
                "batch_id",
                "manifest_path",
                "primary_taskid",
                "requested_phage_ids",
                "observed_phage_ids",
                "missing_phage_ids",
                "reconcile_note",
                "warning",
            ):
                if key in raw_result:
                    val = raw_result.get(key)
                    if isinstance(val, list) and key in {"requested_phage_ids", "observed_phage_ids", "missing_phage_ids"}:
                        sanitized[key] = val[:80]
                    else:
                        sanitized[key] = val
            pr = raw_result.get("result_phage")
            if isinstance(pr, dict):
                sanitized["result_phage"] = {
                    "success": pr.get("success"),
                    "status_code": pr.get("status_code"),
                    "action": pr.get("action"),
                    "not_ready": pr.get("not_ready"),
                }
        elif act_l == "batch_retry":
            for key in ("batch_id", "manifest_path", "retry_phage_ids", "warning"):
                if key in raw_result:
                    sanitized[key] = raw_result.get(key)
            rr = raw_result.get("retry_results")
            if isinstance(rr, list):
                sanitized["retry_results"] = [
                    {
                        "phage_id": item.get("phage_id"),
                        "taskid": item.get("taskid"),
                        "success": (item.get("submit") or {}).get("success")
                        if isinstance(item.get("submit"), dict)
                        else None,
                    }
                    for item in rr[:40]
                    if isinstance(item, dict)
                ]
        # Keep key local artifact paths for save_all so follow-up file reads can work.
        if str(raw_result.get("action") or "").strip().lower() == "save_all":
            for key in (
                "taskid",
                "artifact_scope",
                "output_directory",
                "output_directory_rel",
                "summary_file",
                "summary_file_rel",
                "files_saved",
                "errors",
                "missing_artifacts",
                "warnings",
                "partial",
            ):
                if key in raw_result:
                    sanitized[key] = raw_result.get(key)
        payload = raw_result.get("data")
        if isinstance(payload, dict):
            trimmed: Dict[str, Any] = {}
            for key in ("status", "message", "code", "results", "data", "error"):
                if key in payload:
                    trimmed[key] = payload[key]
            if "results" in trimmed and isinstance(trimmed["results"], list):
                trimmed["results"] = trimmed["results"][:3]
            sanitized["data"] = trimmed
        return sanitized

    if tool_name == "file_operations" and isinstance(raw_result, dict):
        # Some operations (e.g. exists) historically didn't include "success".
        inferred_success = raw_result.get("success")
        if inferred_success is None:
            inferred_success = False if raw_result.get("error") else True
        sanitized: Dict[str, Any] = {
            "tool": tool_name,
            "operation": raw_result.get("operation"),
            "path": raw_result.get("path"),
            "success": bool(inferred_success),
        }
        if "error" in raw_result:
            sanitized["error"] = raw_result.get("error")
        # Keep read content for downstream synthesis (already bounded by tool limits).
        content = raw_result.get("content")
        if isinstance(content, str):
            # Extra guardrail: cap to 80k chars to avoid bloating chat logs.
            sanitized["content"] = content[:80_000]
        for key in ("size", "file_size", "lines_read", "encoding", "truncated", "truncated_message", "count", "items", "exists", "type"):
            if key in raw_result:
                sanitized[key] = raw_result.get(key)
        return sanitized

    if tool_name == "claude_code" and isinstance(raw_result, dict):
        def _trim(text: str, limit: int = 800) -> str:
            text = text.strip()
            if len(text) > limit:
                return text[: limit - 3] + "..."
            return text

        sanitized: Dict[str, Any] = {
            "tool": tool_name,
            "code": raw_result.get("code"),
            "owner": raw_result.get("owner"),
            "language": raw_result.get("language", "python"),
            "uploaded_files": raw_result.get("uploaded_files") or [],
            "success": raw_result.get("success", False),
        }

        stdout_value = raw_result.get("stdout")
        if isinstance(stdout_value, str) and stdout_value.strip():
            sanitized["stdout"] = _trim(stdout_value)

        stderr_value = raw_result.get("stderr")
        if isinstance(stderr_value, str) and stderr_value.strip():
            sanitized["stderr"] = _trim(stderr_value, limit=400)

        output_value = raw_result.get("output")
        if isinstance(output_value, str) and output_value.strip():
            sanitized["output"] = _trim(output_value)

        if "error" in raw_result:
            sanitized["error"] = str(raw_result["error"])

        tool_calls = raw_result.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            sanitized["tool_calls"] = tool_calls

        artifact_paths = raw_result.get("artifact_paths")
        if isinstance(artifact_paths, list) and artifact_paths:
            trimmed_paths: List[str] = []
            for item in artifact_paths[:80]:
                if isinstance(item, str) and item.strip():
                    trimmed_paths.append(item.strip().replace("\\", "/"))
            if trimmed_paths:
                sanitized["artifact_paths"] = trimmed_paths

        return sanitized

    if tool_name == "terminal_session" and isinstance(raw_result, dict):
        sanitized: Dict[str, Any] = {
            "tool": tool_name,
            "success": raw_result.get("success", True),
        }
        if "error" in raw_result:
            sanitized["error"] = raw_result.get("error")
        for key in (
            "operation",
            "terminal_id",
            "session_id",
            "bytes_sent",
            "output",
            "status",
            "message",
            "command_state",
            "verification_state",
            "exit_code",
            "verification_summary",
            "verification_evidence",
            "replay",
            "items",
            "count",
        ):
            if key in raw_result:
                sanitized[key] = raw_result[key]
        if "success" not in sanitized:
            sanitized["success"] = False if sanitized.get("error") else True
        return sanitized

    if isinstance(raw_result, dict):
        sanitized: Dict[str, Any] = {"tool": tool_name}
        for key in (
            "query",
            "provider",
            "success",
            "response",
            "answer",
            "total_results",
            "fallback_from",
            "code",
            "cache_hit",
            "text",
            "page_count",
            "file_path",
            "file_type",
            "base64",
            "width",
            "height",
            "operation",
            "image_path",
            "page_number",
            "language",
            "experiment_id",
            "card",
            "error_code",
            "error_stage",
            "no_claude_fallback",
            "input_origin",
            "generated_input_file",
            "output_file",
            "output_file_rel",
            "record_count",
            "accessions",
            "bytes",
            "sha256",
            "database",
            "format",
            "predicted_label",
            "predicted_lifestyle",
            "positive_window_fraction",
            "thresholds",
            "sample_id",
            "execution_mode",
            "remote_profile",
            "pythonpath",
            "input_file_prepared",
            "run_directory",
            "background",
            "job_id",
            "status",
        ):
            if key in raw_result:
                sanitized[key] = raw_result[key]
        if "error" in raw_result:
            sanitized["error"] = raw_result["error"]
        results = raw_result.get("results")
        if isinstance(results, list):
            trimmed: List[Dict[str, Any]] = []
            for item in results[:3]:
                if isinstance(item, dict):
                    trimmed.append({
                        "title": item.get("title"),
                        "url": item.get("url"),
                        "snippet": item.get("snippet"),
                        "source": item.get("source"),
                    })
            if trimmed:
                sanitized["results"] = trimmed
        result_block = raw_result.get("result")
        if isinstance(result_block, dict):
            if "prompt" in result_block and isinstance(result_block["prompt"], str):
                sanitized["prompt"] = result_block["prompt"]
            triples = result_block.get("triples")
            if isinstance(triples, list):
                sanitized["triples"] = triples
            if "metadata" in result_block and isinstance(
                result_block["metadata"], dict
            ):
                sanitized["metadata"] = result_block["metadata"]
            if "subgraph" in result_block:
                sanitized["subgraph"] = result_block["subgraph"]
            if "query" in result_block and "query" not in sanitized:
                sanitized["query"] = result_block["query"]
        if "success" not in sanitized:
            if "error" in sanitized:
                sanitized["success"] = False
            else:
                sanitized["success"] = True
        if tool_name == "graph_rag":
            if not sanitized.get("success"):
                sanitized["empty_result"] = False
            else:
                triples = sanitized.get("triples")
                sanitized["empty_result"] = not bool(triples)
        return sanitized

    if raw_result is None:
        return {"tool": tool_name, "success": False, "error": "empty_result"}

    if isinstance(raw_result, (list, tuple)):
        items = list(raw_result)
        return {"tool": tool_name, "items": items, "success": True}

    text = str(raw_result)
    return {"tool": tool_name, "text": text, "success": True}


# ---------------------------------------------------------------------------
# drop_callables
# ---------------------------------------------------------------------------

def drop_callables(value: Any) -> Any:
    """Recursively remove callable values from nested data structures."""
    if callable(value):
        return None
    if isinstance(value, dict):
        cleaned: Dict[str, Any] = {}
        for key, item in value.items():
            if callable(item):
                continue
            cleaned[key] = drop_callables(item)
        return cleaned
    if isinstance(value, list):
        cleaned_list: List[Any] = []
        for item in value:
            if callable(item):
                continue
            cleaned_list.append(drop_callables(item))
        return cleaned_list
    if isinstance(value, tuple):
        cleaned_tuple: List[Any] = []
        for item in value:
            if callable(item):
                continue
            cleaned_tuple.append(drop_callables(item))
        return cleaned_tuple
    return value


# ---------------------------------------------------------------------------
# summarize_tool_result
# ---------------------------------------------------------------------------

def summarize_tool_result(tool_name: str, result: Dict[str, Any]) -> str:
    """Return a concise one-line summary for a sanitized tool result."""
    if tool_name == "phagescope":
        action = result.get("action") or "phagescope"
        # Special handling: save_all may return 207 (partial) but still be usable.
        if str(action).strip().lower() == "save_all":
            status_code = result.get("status_code")
            out_dir = result.get("output_directory") or result.get("output_directory_rel")
            missing = result.get("missing_artifacts") or []
            errors = result.get("errors") or []
            if result.get("success") is True:
                if status_code == 207:
                    miss_text = ""
                    if isinstance(missing, list) and missing:
                        miss_text = f"; missing: {', '.join(str(x) for x in missing[:6])}{'...' if len(missing) > 6 else ''}"
                    elif isinstance(errors, list) and errors:
                        miss_text = f"; partial errors: {', '.join(str(x) for x in errors[:2])}{'...' if len(errors) > 2 else ''}"
                    return f"PhageScope save_all completed (partial): saved to {out_dir}{miss_text}"
                return f"PhageScope save_all completed: saved to {out_dir}"
            error = result.get("error") or "Execution failed"
            # If partial output exists, surface it even on failure.
            if status_code == 207 and out_dir:
                return f"PhageScope save_all completed (partial): saved to {out_dir}; but marked failed: {error}"
            return f"PhageScope save_all failed: {error}"

        if result.get("success") is False:
            error = result.get("error") or "Execution failed"
            return f"PhageScope {action} failed: {error}"

        action_lower = str(action).strip().lower()
        if action_lower == "submit":
            taskid = _extract_taskid_from_result(result)
            if taskid:
                return f"PhageScope submit succeeded: taskid={taskid}; running in background."
            return "PhageScope submit succeeded; task is running in background."

        if action_lower == "batch_submit":
            bid = result.get("batch_id") or "?"
            pt = result.get("primary_taskid")
            mp = result.get("manifest_path")
            strat = result.get("strategy") or "multi_one_task"
            if result.get("success") is False:
                err = result.get("error") or "Execution failed"
                return f"PhageScope batch_submit failed: {err}"
            if strat == "per_strain":
                return f"PhageScope batch_submit (per_strain): batch_id={bid}; manifest={mp}."
            return f"PhageScope batch_submit: batch_id={bid}; primary_taskid={pt}; manifest={mp}."

        if action_lower == "batch_reconcile":
            if result.get("success") is False:
                err = result.get("error") or "Execution failed"
                return f"PhageScope batch_reconcile failed: {err}"
            bid = result.get("batch_id") or "?"
            miss = result.get("missing_phage_ids") or []
            nmiss = len(miss) if isinstance(miss, list) else 0
            return f"PhageScope batch_reconcile: batch_id={bid}; missing_phage_ids={nmiss}."

        if action_lower == "batch_retry":
            if result.get("success") is False:
                err = result.get("error") or "Execution failed"
                return f"PhageScope batch_retry failed: {err}"
            bid = result.get("batch_id") or "?"
            return f"PhageScope batch_retry: batch_id={bid}; see retry_results in tool output."

        if action_lower == "task_detail":
            snapshot = _extract_phagescope_task_snapshot(result)
            status = (
                str(snapshot.get("remote_status") or "").strip()
                or str(snapshot.get("task_status") or "").strip()
                or "unknown"
            )
            counts = snapshot.get("counts") if isinstance(snapshot.get("counts"), dict) else {}
            done = counts.get("done") if isinstance(counts.get("done"), int) else None
            total = counts.get("total") if isinstance(counts.get("total"), int) else None
            suffix = ""
            if result.get("local_bundle_hint"):
                suffix = " Full local bundle still needs action=save_all (API JSON alone is not the full folder tree)."
            if isinstance(done, int) and isinstance(total, int) and total > 0:
                return f"PhageScope task_detail succeeded: status={status}, progress={done}/{total}.{suffix}"
            return f"PhageScope task_detail succeeded: status={status}.{suffix}"

        payload = result.get("data")
        message = None
        if isinstance(payload, dict):
            message = payload.get("message") or payload.get("status")
        if message:
            base = f"PhageScope {action} succeeded: {message}"
        else:
            base = f"PhageScope {action} succeeded."
        if result.get("local_bundle_hint") and str(action).strip().lower() in {
            "result",
            "download",
            "task_log",
            "query",
        }:
            base += " Full local bundle still needs action=save_all (API JSON alone is not the full folder tree)."
        return base

    if tool_name == "terminal_session":
        op = str(result.get("operation") or "").strip().lower()
        if result.get("success") is False:
            err = result.get("error") or result.get("message") or "Execution failed"
            return f"terminal_session {op or 'operation'} failed: {err}"
        if op == "write":
            vs = str(result.get("verification_state") or "").strip().lower()
            summary = str(result.get("verification_summary") or "").strip()
            if vs == "verified_success":
                return summary or "terminal_session write: command verified successfully"
            if vs == "verified_failure":
                return summary or "terminal_session write: command failed verification"
            if summary:
                return summary
            st = str(result.get("status") or "")
            return (
                f"terminal_session write: command dispatched; PTY settle={st} "
                "(use verification_state for mutation outcomes)"
            )
        return f"terminal_session {op or 'operation'} succeeded."

    if tool_name == "web_search":
        query = result.get("query") or ""
        prefix = f"Web search\u201c{query}\u201d" if query else "Web search"
        provider = result.get("provider")
        if isinstance(provider, str) and provider:
            provider_map = {
                "builtin": "builtin",
                "perplexity": "Perplexity",
            }
            label = provider_map.get(provider, provider)
            prefix = f"{prefix}\uff08{label}\uff09"
        if result.get("success") is False:
            error = result.get("error") or "Execution failed"
            return f"{prefix} failed: {error}"
        results = result.get("results") or []
        if isinstance(results, list) and results:
            first = results[0]
            source = first.get("source") or first.get("url") or "Unknown source"
            title = first.get("title") or ""
            if title:
                return f'{prefix} finished; the first result came from {source}: "{title}".'
            return f"{prefix} finished; the first result came from {source}."
        response = result.get("response") or result.get("answer")
        if isinstance(response, str) and response.strip():
            snippet = response.strip()
            return f"{prefix} finished. Summary: {snippet}"
        total = result.get("total_results")
        if isinstance(total, int) and total > 0:
            return f"{prefix} finished with {total} results."
        return f"{prefix} finished."

    if tool_name == "graph_rag":
        query = result.get("query") or ""
        prefix = f"Knowledge-graph search\u201c{query}\u201d" if query else "Knowledge-graph search"
        if result.get("success") is False:
            error = result.get("error") or "Execution failed"
            return f"{prefix} failed: {error}"
        triples = result.get("triples") or []
        count = len(triples) if isinstance(triples, list) else 0
        if count:
            return f"{prefix} finished, returning {count} triples."
        if result.get("empty_result"):
            return f"{prefix} finished, but no relevant results were found."
        prompt = result.get("prompt")
        if isinstance(prompt, str) and prompt.strip():
            snippet = prompt.strip()
            return f"{prefix} finished. Prompt summary: {snippet}"
        return f"{prefix} finished."

    if tool_name == "claude_code":
        if result.get("success") is False:
            error = result.get("error") or "Code execution failed"
            return f"Claude Code execution failed: {error}"

        uploaded = result.get("uploaded_files") or []
        file_info = f" (with {len(uploaded)} file(s))" if uploaded else ""

        stdout_text = result.get("stdout") or result.get("output") or ""
        if stdout_text.strip():
            snippet = stdout_text.strip()
            return f"Claude Code execution{file_info} succeeded. Output: {snippet}"

        return f"Claude Code execution{file_info} succeeded."

    if tool_name == "deeppl":
        action = result.get("action") or "deeppl"
        if result.get("success") is False:
            error = result.get("error") or "Execution failed"
            return f"DeepPL {action} failed: {error}"
        action_text = str(action).strip().lower()
        if action_text == "predict":
            label = result.get("predicted_label") or "unknown"
            lifestyle = result.get("predicted_lifestyle") or "unknown"
            fraction = result.get("positive_window_fraction")
            if isinstance(fraction, (int, float)):
                return (
                    f"DeepPL predict succeeded: label={label}, "
                    f"lifestyle={lifestyle}, positive_window_fraction={fraction:.4f}."
                )
            return f"DeepPL predict succeeded: label={label}, lifestyle={lifestyle}."
        if action_text == "job_status":
            return f"DeepPL job_status: {result.get('status') or 'unknown'}."
        return f"DeepPL {action} succeeded."

    if tool_name == "manuscript_writer":
        if result.get("success") is False:
            error = result.get("error") or "Manuscript writing failed"
            return f"Manuscript writer failed: {error}"
        output_path = result.get("output_path") or ""
        analysis_path = result.get("analysis_path") or ""
        if output_path and analysis_path:
            return (
                "Manuscript writer succeeded. Draft: "
                f"{output_path}; analysis memo: {analysis_path}."
            )
        if output_path:
            return f"Manuscript writer succeeded. Draft: {output_path}."
        return "Manuscript writer succeeded."

    if tool_name == "paper_replication":
        if result.get("success") is False:
            error = result.get("error") or "Paper replication tool failed"
            return f"Paper replication tool failed: {error}"

        exp_id = result.get("experiment_id") or "unknown_experiment"
        card = result.get("card") or {}
        paper = {}
        if isinstance(card, dict):
            paper = card.get("paper") or {}
        title = ""
        if isinstance(paper, dict):
            title = paper.get("title") or ""
        if title:
            return f"Loaded replication spec for {exp_id} (paper: {title})."
        return f"Loaded replication spec for {exp_id}."

    if tool_name == "vision_reader":
        if result.get("success") is False:
            error = result.get("error") or "Vision reader execution failed"
            return f"Vision reader failed: {error}"

        op = result.get("operation") or "vision task"
        text = result.get("text") or ""
        if isinstance(text, str) and text.strip():
            snippet = text.strip()
            return f"Vision reader ({op}) succeeded. Content preview: {snippet}"

        return "Vision reader succeeded, but no textual content was extracted."

    if tool_name == "document_reader":
        if result.get("success") is False:
            error = result.get("error") or "Document reading failed"
            return f"Document reader failed: {error}"

        text = result.get("text") or ""
        page_count = result.get("page_count")

        if text.strip():
            page_info = f" ({page_count} pages)" if page_count else ""
            return f"Document reader{page_info} succeeded. Content preview: {text.strip()}"

        return "Document reader succeeded, but no text content was extracted."

    if tool_name == "sequence_fetch":
        if result.get("success") is False:
            error = result.get("error") or "Sequence fetch failed"
            return f"Sequence fetch failed: {error}"
        accessions = result.get("accessions")
        accession_count = len(accessions) if isinstance(accessions, list) else None
        record_count = result.get("record_count")
        output_file = result.get("output_file") or result.get("output_file_rel")
        provider = result.get("provider")
        parts: List[str] = ["Sequence fetch succeeded"]
        if isinstance(accession_count, int) and accession_count > 0:
            parts.append(f"accessions={accession_count}")
        if isinstance(record_count, int):
            parts.append(f"records={record_count}")
        if isinstance(provider, str) and provider:
            parts.append(f"provider={provider}")
        if isinstance(output_file, str) and output_file:
            parts.append(f"saved to {output_file}")
        return "; ".join(parts) + "."

    return f"{tool_name} finished execution."


# ---------------------------------------------------------------------------
# truncate_large_fields
# ---------------------------------------------------------------------------

def truncate_large_fields(
    data: Any, max_field_length: int = 1000, current_depth: int = 0
) -> Any:
    """Recursively truncate large text fields while preserving structure."""
    if current_depth > 5:  # Guard against excessive recursion depth.
        return "...[nested data truncated]"

    if isinstance(data, dict):
        result = {}
        for key, value in data.items():
            result[key] = truncate_large_fields(value, max_field_length, current_depth + 1)
        return result
    elif isinstance(data, list):
        if len(data) > 10:
            # List is too long: keep first 5 and last 2.
            truncated = data[:5] + [f"...[{len(data) - 7} items omitted]"] + data[-2:]
            return [truncate_large_fields(item, max_field_length, current_depth + 1) for item in truncated]
        return [truncate_large_fields(item, max_field_length, current_depth + 1) for item in data]
    elif isinstance(data, str):
        if len(data) > max_field_length:
            return data[:max_field_length] + f"...[truncated, {len(data)} chars total]"
        return data
    else:
        return data


# ---------------------------------------------------------------------------
# append_recent_tool_result
# ---------------------------------------------------------------------------

def append_recent_tool_result(
    extra_context: Dict[str, Any],
    tool_name: str,
    summary: str,
    sanitized: Dict[str, Any],
) -> None:
    """Append tool result to the agent's sliding-window context.

    Parameters
    ----------
    extra_context:
        The ``agent.extra_context`` dict – mutated in place.
    tool_name:
        Name of the tool that produced the result.
    summary:
        Human-friendly one-liner (from :func:`summarize_tool_result`).
    sanitized:
        Sanitized result dict (from :func:`sanitize_tool_result`).
    """
    history = extra_context.setdefault("recent_tool_results", [])
    if not isinstance(history, list):
        history = []
        extra_context["recent_tool_results"] = history

    # Tiered compression strategy.
    # Serialize result to estimate size.
    try:
        result_str = json.dumps(sanitized, ensure_ascii=False, default=str)
    except Exception:
        result_str = str(sanitized)

    result_size = len(result_str)

    # Size thresholds.
    SMALL_THRESHOLD = 2000    # <=2000 chars: keep full payload.
    MEDIUM_THRESHOLD = 8000   # <=8000 chars: keep structure with truncation.
    # >8000 chars: keep summary only.

    if result_size <= SMALL_THRESHOLD:
        # Small result: keep full payload.
        compressed_result = sanitized
        compression_level = "full"
    elif result_size <= MEDIUM_THRESHOLD:
        # Medium result: keep structure but truncate long text fields.
        compressed_result = truncate_large_fields(sanitized, max_field_length=1000)
        compression_level = "truncated"
    else:
        # Large result: keep summary and key metadata only.
        compressed_result = {
            "_compressed": True,
            "_original_size": result_size,
            "success": sanitized.get("success"),
            "summary": sanitized.get("summary") or summary,
            "error": sanitized.get("error"),
        }
        # Preserve some commonly used small fields.
        for key in ["file_path", "file_name", "total", "count", "status"]:
            if key in sanitized and sanitized[key] is not None:
                val = sanitized[key]
                if isinstance(val, str) and len(val) < 200:
                    compressed_result[key] = val
                elif isinstance(val, (int, float, bool)):
                    compressed_result[key] = val
        compression_level = "summary_only"

    entry = {
        "tool": tool_name,
        "summary": summary,
        "result": compressed_result,
        "_compression": compression_level,
        "_original_size": result_size,
    }
    history.append(entry)

    # Keep up to 10 recent entries.
    max_items = 10
    if len(history) > max_items:
        del history[:-max_items]
