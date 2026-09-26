"""Tool dispatch and tool-result shaping for the DeepThink agent.

God-class split (behaviour zero-change): the bodies of the like-named
DeepThinkAgent methods with `self` renamed to `agent` (`cls` kept); the
class keeps thin wrappers with the same decorators. The artifact-path
classification constants (_INTERNAL_ARTIFACT_FILENAMES,
_INTERNAL_TOOL_OUTPUT_RE, _ARTIFACT_PATH_EXTS) moved here together with
the only code that referenced them. Cross-calls between the moved
helpers go through agent._x(...)/cls._x(...) so subclass overrides keep
working.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from app.services.deep_think.models import ThinkingStep
from app.services.execution.tool_executor import UnifiedToolExecutor
from app.services.response_style import sanitize_professional_response_text

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.services.deep_think_agent import DeepThinkAgent

logger = logging.getLogger(__name__)

_INTERNAL_ARTIFACT_FILENAMES = {"result.json", "manifest.json", "preview.json"}
_INTERNAL_TOOL_OUTPUT_RE = re.compile(
    r"/job_[^/]+/step_\d+_[^/]+/(?:result|manifest|preview)\.json$",
    re.IGNORECASE,
)
_ARTIFACT_PATH_EXTS = {
    ".bib",
    ".csv",
    ".docx",
    ".fa",
    ".faa",
    ".fasta",
    ".gb",
    ".gbk",
    ".gif",
    ".h5ad",
    ".html",
    ".ipynb",
    ".jpeg",
    ".jpg",
    ".json",
    ".md",
    ".mmd",
    ".nwk",
    ".pdf",
    ".pkl",
    ".png",
    ".pptx",
    ".py",
    ".r",
    ".rds",
    ".svg",
    ".tex",
    ".tsv",
    ".txt",
    ".webp",
    ".xlsx",
    ".xls",
    ".xml",
    ".yaml",
    ".yml",
    ".zip",
}


def _normalize_tool_callback_outcome(result: Any) -> tuple[bool, Optional[str]]:
    if isinstance(result, dict):
        if "success" in result:
            success = bool(result.get("success"))
            error_val = result.get("error")
            error = str(error_val).strip() if error_val is not None else None
            return success, error
        nested = result.get("result")
        if isinstance(nested, dict) and nested.get("success") is False:
            nested_error = nested.get("error")
            if nested_error is not None:
                return False, str(nested_error)
            return False, None
    return True, None


def _extract_artifact_paths(agent: "DeepThinkAgent", tool_name: str, result: Any) -> List[str]:
    """Extract file paths from tool results that look like produced artifacts."""
    if tool_name == "terminal_session":
        if not isinstance(result, dict):
            return []
        verification_state = str(result.get("verification_state") or "").strip().lower()
        if verification_state != "verified_success":
            return []
        explicit_paths = result.get("artifact_paths")
        if isinstance(explicit_paths, list):
            cleaned: List[str] = []
            for item in explicit_paths:
                if isinstance(item, str) and item.strip() and not agent._is_internal_artifact_path(item):
                    cleaned.append(item.strip())
            return list(dict.fromkeys(cleaned))
        return []
    paths = agent._extract_explicit_artifact_paths(result)
    text = str(result) if result is not None else ""
    if text:
        for m in agent.ARTIFACT_PATH_RE.finditer(text):
            candidate = m.group(1)
            if not agent._is_internal_artifact_path(candidate) and candidate not in paths:
                paths.append(candidate)
        for m in agent.BARE_PATH_RE.finditer(text):
            candidate = m.group(1)
            if candidate not in paths and not agent._is_internal_artifact_path(candidate):
                paths.append(candidate)
    return list(dict.fromkeys(paths))


def _is_internal_artifact_path(path: str) -> bool:
    normalized = "/" + str(path or "").strip().replace("\\", "/").lstrip("/")
    if not normalized or normalized == "/":
        return False
    basename = normalized.rsplit("/", 1)[-1].lower()
    if basename in _INTERNAL_ARTIFACT_FILENAMES and "/tool_outputs/" in normalized.lower():
        return True
    if normalized.lower().endswith("/deliverables/manifest_latest.json"):
        return True
    return bool(_INTERNAL_TOOL_OUTPUT_RE.search(normalized))


def _looks_like_artifact_path(path: str) -> bool:
    text = str(path or "").strip()
    if not text or "\n" in text or "\r" in text:
        return False
    if text.startswith(("http://", "https://")):
        return False
    if text.startswith(("/", "./", "../", "~")):
        return True
    if "/" in text or "\\" in text:
        return True
    return Path(text).suffix.lower() in _ARTIFACT_PATH_EXTS


def _extract_explicit_artifact_paths(agent: "DeepThinkAgent", result: Any) -> List[str]:
    if not isinstance(result, dict):
        return []

    paths: List[str] = []

    def _append(value: Any) -> None:
        if not isinstance(value, str):
            return
        candidate = value.strip()
        if not agent._looks_like_artifact_path(candidate):
            return
        if agent._is_internal_artifact_path(candidate):
            return
        if candidate not in paths:
            paths.append(candidate)

    def _append_list(items: Any) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            _append(item)

    for key in ("artifact_paths", "session_artifact_paths", "produced_files"):
        _append_list(result.get(key))

    for key in (
        "image_path",
        "output_file",
        "output_file_rel",
        "saved_path",
        "saved_path_rel",
        "preview_path",
        "summary_file",
        "summary_file_rel",
    ):
        _append(result.get(key))

    items = result.get("items")
    if isinstance(items, list):
        for row in items:
            if not isinstance(row, dict):
                continue
            _append(row.get("path"))
            _append(row.get("relative_path"))

    outputs = result.get("outputs")
    if isinstance(outputs, dict):
        for value in outputs.values():
            _append(value)

    storage = result.get("storage")
    if isinstance(storage, dict):
        for container in (
            storage,
            storage.get("relative") if isinstance(storage.get("relative"), dict) else None,
        ):
            if not isinstance(container, dict):
                continue
            for key in (
                "preview_path",
                "result_path",
                "output_file",
                "output_file_rel",
                "saved_path",
                "saved_path_rel",
            ):
                _append(container.get(key))
            for key in ("artifact_paths", "paths"):
                _append_list(container.get(key))

    deliverables = result.get("deliverables")
    if isinstance(deliverables, dict):
        artifacts = deliverables.get("artifacts")
        if isinstance(artifacts, list):
            for row in artifacts:
                if not isinstance(row, dict):
                    continue
                _append(row.get("path"))
                _append(row.get("relative_path"))

    files_saved = result.get("files_saved")
    output_directory = result.get("output_directory")
    if isinstance(files_saved, dict):
        base_dir = None
        if isinstance(output_directory, str) and output_directory.strip():
            try:
                base_dir = Path(output_directory).expanduser().resolve()
            except Exception:
                base_dir = None
        for value in files_saved.values():
            if not isinstance(value, str):
                continue
            if base_dir is not None:
                try:
                    candidate = Path(value).expanduser()
                    if not candidate.is_absolute():
                        candidate = (base_dir / candidate).resolve()
                    else:
                        candidate = candidate.resolve()
                    _append(str(candidate))
                    continue
                except Exception:
                    pass
            _append(value)

    return paths


async def _emit_artifacts(agent: "DeepThinkAgent", tool_name: str, result: Any, iteration: int) -> None:
    if not agent.on_artifact:
        return
    paths = agent._extract_artifact_paths(tool_name, result)
    for path in paths:
        ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        await agent._safe_generic_callback(
            agent.on_artifact,
            {
                "path": path,
                "display_name": path.rsplit("/", 1)[-1] if "/" in path else path,
                "extension": ext,
                "source_tool": tool_name,
                "iteration": iteration,
            },
        )


async def _execute_native_tool_call(
    agent: "DeepThinkAgent",
    tc: Any,
    iteration: int,
    index: int,
) -> Dict[str, Any]:
    from tool_box.context import ToolContext

    tool_name = str(getattr(tc, "name", "") or "")
    tool_params = getattr(tc, "arguments", {}) or {}
    tool_call_id = str(getattr(tc, "id", "") or f"native_{iteration}_{index}")
    timeout = UnifiedToolExecutor.TOOL_TIMEOUTS.get(tool_name, agent.tool_timeout)

    async def _progress_bridge(data: Dict[str, Any]) -> None:
        if agent.on_tool_progress:
            await agent._safe_generic_callback(
                agent.on_tool_progress, tool_name, data,
            )

    tool_ctx = ToolContext(
        on_progress=_progress_bridge,
        # Recorded so a handler that offloads its work to a worker thread (the
        # delegated CLI lanes) can post progress back onto this loop.
        on_progress_loop=asyncio.get_running_loop(),
        plan_id=agent._current_plan_id(),
        session_id=str(agent.request_profile.get("session_id") or "").strip() or None,
        owner_id=str(agent.request_profile.get("owner_id") or "").strip() or None,
        extra={
            "chat_history": list(agent.messages[-20:]) if getattr(agent, "messages", None) else [],
            "paper_mode": bool(agent.request_profile.get("paper_mode", False)),
            "deep_think_enabled": True,
        },
    )

    if agent.on_tool_start and tool_name:
        await agent._safe_generic_callback(agent.on_tool_start, tool_name, tool_params)

    if tool_name not in agent.available_tools:
        error_payload = {
            "success": False,
            "error": f"tool_not_available:{tool_name}",
            "summary": f"Tool '{tool_name}' is not available.",
            "iteration": iteration,
        }
        if agent.on_tool_result and tool_name:
            await agent._safe_generic_callback(agent.on_tool_result, tool_name, error_payload)
        tool_result_text = json.dumps(error_payload, ensure_ascii=False)
        return {
            "index": index,
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "tool_params": tool_params,
            "tool_result": error_payload,
            "tool_result_text": tool_result_text,
            "evidence": [],
        }

    params_with_ctx = {**tool_params, "tool_context": tool_ctx}
    if tool_name == "code_executor":
        params_with_ctx["auto_fix"] = False
    attempt = 0
    while True:
        attempt += 1
        try:
            tool_result = await asyncio.wait_for(
                agent.tool_executor(tool_name, params_with_ctx),
                timeout=timeout,
            )
            
            if tool_name == "plan_operation" and isinstance(tool_result, dict):
                if tool_result.get("operation") == "bind" and tool_result.get("success"):
                    new_plan_id = tool_result.get("plan_id")
                    if new_plan_id is not None:
                        agent.request_profile["current_plan_id"] = new_plan_id
                        logger.info(
                            "[DEEP_THINK] Updated request_profile current_plan_id to %s after successful bind",
                            new_plan_id
                        )
            
            callback_success, callback_error = agent._normalize_tool_callback_outcome(tool_result)
            callback_payload = {
                "success": callback_success,
                "error": callback_error,
                "result": tool_result,
                "summary": agent._build_tool_callback_summary(tool_result),
                "iteration": iteration,
                "attempt": attempt,
            }
            if not callback_success:
                logger.warning(
                    "[DEEP_THINK_NATIVE] Tool returned success=false: tool=%s tool_call_id=%s summary=%s error=%s",
                    tool_name,
                    tool_call_id,
                    agent._clip_log_text(callback_payload.get("summary"), limit=360),
                    agent._clip_log_text(callback_error, limit=240),
                )
                if agent._should_retry_external_tool(tool_name, success=False) and attempt <= agent.MAX_EXTERNAL_TOOL_RETRIES:
                    if agent.on_tool_result:
                        await agent._safe_generic_callback(
                            agent.on_tool_result,
                            tool_name,
                            {
                                **callback_payload,
                                "retrying": True,
                                "retry_attempt": attempt,
                                "max_attempts": agent.MAX_EXTERNAL_TOOL_RETRIES + 1,
                            },
                        )
                    if agent.on_tool_start:
                        await agent._safe_generic_callback(agent.on_tool_start, tool_name, tool_params)
                    continue
            if agent.on_tool_result:
                await agent._safe_generic_callback(agent.on_tool_result, tool_name, callback_payload)
            await agent._emit_artifacts(tool_name, tool_result, iteration)
            tool_result_text = agent._build_tool_result_text_for_llm(
                tool_name=tool_name,
                result=tool_result,
                success=callback_success,
                error=callback_error,
            )
            evidence = agent._extract_evidence(tool_name, tool_params, tool_result)
            return {
                "index": index,
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "tool_params": tool_params,
                "tool_result": tool_result,
                "tool_result_text": tool_result_text,
                "evidence": evidence,
            }
        except asyncio.TimeoutError:
            timeout_payload = {
                "success": False,
                "tool": tool_name,
                "error": "timeout",
                "summary": f"Tool '{tool_name}' timed out after {timeout}s",
            }
            should_retry = agent._should_retry_external_tool(tool_name, success=False) and attempt <= agent.MAX_EXTERNAL_TOOL_RETRIES
            if agent.on_tool_result:
                await agent._safe_generic_callback(
                    agent.on_tool_result,
                    tool_name,
                    {
                        "success": False,
                        "error": "timeout",
                        "summary": timeout_payload["summary"],
                        "iteration": iteration,
                        "attempt": attempt,
                        "retrying": should_retry,
                        "retry_attempt": attempt if should_retry else None,
                        "max_attempts": agent.MAX_EXTERNAL_TOOL_RETRIES + 1 if should_retry else None,
                    },
                )
            if should_retry:
                if agent.on_tool_start:
                    await agent._safe_generic_callback(agent.on_tool_start, tool_name, tool_params)
                continue
            return {
                "index": index,
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "tool_params": tool_params,
                "tool_result": timeout_payload,
                "tool_result_text": json.dumps(timeout_payload, ensure_ascii=False),
                "evidence": [],
            }
        except Exception as exc:
            logger.exception(
                "Tool %s failed (tool_call_id=%s, params=%s)",
                tool_name,
                tool_call_id,
                agent._sanitize_tool_params_for_log(tool_params),
            )
            failure_payload = {
                "success": False,
                "tool": tool_name,
                "error": str(exc),
                "summary": f"Error executing tool: {exc}",
            }
            should_retry = agent._should_retry_external_tool(tool_name, success=False) and attempt <= agent.MAX_EXTERNAL_TOOL_RETRIES
            if agent.on_tool_result:
                await agent._safe_generic_callback(
                    agent.on_tool_result,
                    tool_name,
                    {
                        "success": False,
                        "error": str(exc),
                        "summary": failure_payload["summary"],
                        "iteration": iteration,
                        "attempt": attempt,
                        "retrying": should_retry,
                        "retry_attempt": attempt if should_retry else None,
                        "max_attempts": agent.MAX_EXTERNAL_TOOL_RETRIES + 1 if should_retry else None,
                    },
                )
            if should_retry:
                if agent.on_tool_start:
                    await agent._safe_generic_callback(agent.on_tool_start, tool_name, tool_params)
                continue
            return {
                "index": index,
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "tool_params": tool_params,
                "tool_result": failure_payload,
                "tool_result_text": json.dumps(failure_payload, ensure_ascii=False),
                "evidence": [],
            }


def _extract_evidence(
    agent: "DeepThinkAgent",
    tool_name: str,
    tool_params: Dict[str, Any],
    tool_result: Any,
) -> List[Dict[str, str]]:
    text = str(tool_result or "")
    evidence: List[Dict[str, str]] = []

    for path in agent._extract_artifact_paths(tool_name, tool_result):
        evidence.append(
            {
                "type": "file",
                "title": "Generated file",
                "ref": path,
                "snippet": f"{tool_name} produced {path}",
            }
        )
    for m in agent.URL_RE.finditer(text):
        url = m.group(0)
        evidence.append(
            {
                "type": "url",
                "title": "External source",
                "ref": url,
                "snippet": f"{tool_name} referenced {url}",
            }
        )
    task_id = None
    job_id = None
    if isinstance(tool_result, dict):
        for key in ("taskid", "task_id", "remote_taskid", "remote_task_id"):
            val = tool_result.get(key)
            if isinstance(val, (str, int)) and str(val).strip():
                task_id = str(val).strip()
                break
        job_val = tool_result.get("job_id")
        if isinstance(job_val, (str, int)) and str(job_val).strip():
            job_id = str(job_val).strip()
    if task_id:
        evidence.append(
            {
                "type": "task",
                "title": "Background task",
                "ref": task_id,
                "snippet": f"{tool_name} created task {task_id}",
            }
        )
    if job_id and job_id != task_id:
        evidence.append(
            {
                "type": "job",
                "title": "Background job",
                "ref": job_id,
                "snippet": f"{tool_name} created job {job_id}",
            }
        )
    if not evidence:
        snippet = (text or "").strip().replace("\n", " ")
        if len(snippet) > 240:
            snippet = snippet[:240] + "..."
        if snippet:
            evidence.append(
                {
                    "type": "output",
                    "title": "Tool output",
                    "ref": tool_name,
                    "snippet": snippet,
                }
            )
    return evidence[:8]


def _build_tool_callback_summary(result: Any) -> str:
    if isinstance(result, dict):
        parts: List[str] = []
        if "summary" in result:
            parts.append(str(result["summary"])[:500])
        elif "error" in result and result.get("error"):
            parts.append(str(result.get("error"))[:500])
        # Surface partial completion signals so LLM is aware
        if result.get("partial_completion_suspected"):
            ratio = result.get("partial_ratio", "unknown")
            parts.append(f"⚠️ PARTIAL COMPLETION SUSPECTED (ratio: {ratio}). Verify all expected outputs exist.")
        output_warnings = result.get("output_warnings")
        if isinstance(output_warnings, list) and output_warnings:
            parts.append(f"⚠️ {len(output_warnings)} warning(s) in output: {output_warnings[0][:150]}")
        if parts:
            return "; ".join(parts)[:600]
    return str(result)[:600]


def _build_tool_result_text_for_llm(
    cls: Any,
    *,
    tool_name: str,
    result: Any,
    success: bool,
    error: Any,
) -> str:
    payload = {
        "success": success,
        "tool": tool_name,
        "result": result,
        "error": error,
    }
    raw_text = json.dumps(payload, ensure_ascii=False, default=str)
    if (
        len(raw_text) <= cls.MAX_TOOL_RESULT_TEXT_CHARS
        and str(tool_name or "").strip().lower() not in _ALWAYS_COMPACT_TOOLS
    ):
        return raw_text

    compact_result = cls._compact_tool_result_for_llm(tool_name, result)
    if compact_result is None:
        return _clip_tool_result_text(raw_text)

    compact_payload = {
        "success": success,
        "tool": tool_name,
        "result": compact_result,
        "error": error,
    }
    compact_text = json.dumps(compact_payload, ensure_ascii=False, default=str)
    logger.info(
        "[DEEP_THINK_NATIVE] Compacted tool result for llm context: tool=%s raw_chars=%s compact_chars=%s",
        tool_name,
        len(raw_text),
        len(compact_text),
    )
    return compact_text


def _compact_tool_result_for_llm(
    cls: Any, tool_name: str, result: Any
) -> Optional[Dict[str, Any]]:
    name = str(tool_name or "").strip().lower()
    if name == "file_operations":
        return cls._compact_file_operations_result_for_llm(result)
    if name == "phagescope_research":
        return cls._compact_phagescope_research_result_for_llm(result)
    if name == "code_executor":
        return cls._compact_code_executor_result_for_llm(result)
    if name == "web_search":
        return cls._compact_web_search_result_for_llm(result)
    return None


_WEB_SEARCH_ANSWER_CHARS = 4_000
_WEB_SEARCH_SNIPPET_CHARS = 300
_CLIPPED_TOOL_RESULT_CHARS = 6_000
# A search result is ~9.6k chars, i.e. under MAX_TOOL_RESULT_TEXT_CHARS, so the
# size gate alone would never compact it even though it is re-sent on every
# later iteration. Compact these regardless of size.
_ALWAYS_COMPACT_TOOLS = frozenset({"web_search"})


def _compact_web_search_result_for_llm(
    cls: Any, result: Any
) -> Optional[Dict[str, Any]]:
    """Trim a search result before it enters the prompt.

    Measured 2026-09-27: one result is ~9.6k chars (~3.2k tokens) — under the
    12k cap, so with no compactor it entered the context whole and was re-sent
    on every later iteration. The answer is the useful part; the raw provider
    envelope is not.
    """
    if not isinstance(result, dict):
        return None
    compact: Dict[str, Any] = {
        "tool": "web_search",
        "query": result.get("query"),
        "provider": result.get("provider"),
        "success": bool(result.get("success", True)),
        "llm_compacted": True,
    }
    answer = result.get("answer") or result.get("response")
    if isinstance(answer, str) and answer.strip():
        compact["answer"] = answer[:_WEB_SEARCH_ANSWER_CHARS]
        if len(answer) > _WEB_SEARCH_ANSWER_CHARS:
            compact["answer_truncated"] = True
    items = result.get("results")
    if isinstance(items, list) and items:
        compact["results"] = [
            {
                "title": str(item.get("title") or "")[:200],
                "url": str(item.get("url") or ""),
                "snippet": str(item.get("snippet") or "")[:_WEB_SEARCH_SNIPPET_CHARS],
            }
            for item in items[:5]
            if isinstance(item, dict)
        ]
    for key in ("total_results", "fallback_from", "cache_hit", "error"):
        if result.get(key) is not None:
            compact[key] = result.get(key)
    return compact


def _clip_tool_result_text(raw_text: str) -> str:
    """Bound a tool result that has no dedicated compactor.

    ``_compact_tool_result_for_llm`` returning ``None`` used to hand the raw
    text through no matter how large it was, so any tool without a branch could
    put an unbounded blob in the prompt. Clip it and say so.
    """
    if len(raw_text) <= _CLIPPED_TOOL_RESULT_CHARS:
        return raw_text
    head = raw_text[: _CLIPPED_TOOL_RESULT_CHARS - 160]
    return (
        f"{head}…[clipped from {len(raw_text)} chars; "
        "narrow the request or read the file for the rest]"
    )


def _compact_code_executor_result_for_llm(
    cls: Any, result: Any
) -> Optional[Dict[str, Any]]:
    if not isinstance(result, dict):
        return None
    _MAX_STDOUT_CHARS = 3000
    stdout_raw = str(result.get("stdout") or "")
    stdout_text = (
        stdout_raw[:_MAX_STDOUT_CHARS] + "…[truncated]"
        if len(stdout_raw) > _MAX_STDOUT_CHARS
        else stdout_raw
    )
    stderr_raw = str(result.get("stderr") or "")
    stderr_text = (
        stderr_raw[:1000] + "…[truncated]"
        if len(stderr_raw) > 1000
        else stderr_raw
    )
    compact: Dict[str, Any] = {
        "tool": "code_executor",
        "success": bool(result.get("success", False)),
        "exit_code": result.get("exit_code", -1),
        "output_files": result.get("output_files", []),
        "output_location": result.get("output_location"),
        "stdout": stdout_text,
        "llm_compacted": True,
    }
    if stderr_text:
        compact["stderr"] = stderr_text
    for key in ("error", "error_category", "error_summary", "fix_guidance",
                 "execution_status", "verification_status", "failure_kind",
                 "result", "code_file", "produced_files_count"):
        val = result.get(key)
        if val is not None:
            compact[key] = val
    return compact


def _compact_phagescope_research_result_for_llm(
    cls: Any, result: Any
) -> Optional[Dict[str, Any]]:
    if not isinstance(result, dict):
        return None
    if str(result.get("action") or "").strip().lower() != "deep_profile":
        return None
    compact: Dict[str, Any] = {
        "tool": "phagescope_research",
        "action": "deep_profile",
        "success": bool(result.get("success", True)),
        "data_dir": result.get("data_dir"),
        "resolved_data_dir": result.get("resolved_data_dir"),
        "metadata_files": result.get("metadata_files"),
        "metadata_rows": result.get("metadata_rows"),
        "unique_phage_ids": result.get("unique_phage_ids"),
        "duplicate_phage_ids": result.get("duplicate_phage_ids"),
        "metadata_size_bytes": result.get("metadata_size_bytes"),
        "metadata_size_human": result.get("metadata_size_human"),
        "total_size_bytes": result.get("total_size_bytes"),
        "total_size_human": result.get("total_size_human"),
        "ml_metadata_table": result.get("ml_metadata_table"),
        "label_quality": result.get("label_quality"),
        "split_readiness": result.get("split_readiness"),
        "annotation_inventory": result.get("annotation_inventory"),
        "anomalies": result.get("anomalies"),
        "claim_guidance": result.get("claim_guidance"),
        "recommended_next_step": result.get("recommended_next_step"),
        "llm_compacted": True,
    }
    metadata_schema = result.get("metadata_schema")
    if isinstance(metadata_schema, dict):
        compact["metadata_schema"] = {
            "expected_columns": metadata_schema.get("expected_columns"),
            "headers_consistent": metadata_schema.get("headers_consistent"),
            "most_common_header": metadata_schema.get("most_common_header"),
            "missing_expected_by_file": metadata_schema.get("missing_expected_by_file"),
            "extra_columns_by_file": metadata_schema.get("extra_columns_by_file"),
        }
    rows_by_file = result.get("rows_by_metadata_file")
    if isinstance(rows_by_file, dict):
        compact["rows_by_metadata_file"] = rows_by_file
    source_top = result.get("source_top")
    if isinstance(source_top, list):
        compact["source_top"] = source_top[:10]
    taxonomy_top = result.get("taxonomy_top")
    if isinstance(taxonomy_top, list):
        compact["taxonomy_top"] = taxonomy_top[:10]
    subdir_summary = result.get("subdir_size_summary")
    if isinstance(subdir_summary, dict):
        compact["subdir_size_summary"] = {
            name: {
                key: value
                for key, value in summary.items()
                if key in {"files", "size_bytes", "size_human"}
            }
            for name, summary in subdir_summary.items()
            if isinstance(summary, dict)
        }
    return compact


def _compact_file_operations_result_for_llm(
    cls: Any, result: Any
) -> Optional[Dict[str, Any]]:
    if not isinstance(result, dict):
        return None
    operation = str(result.get("operation") or "").strip().lower()
    if operation in {"profile", "census"}:
        compact: Dict[str, Any] = {
            "operation": operation,
            "path": str(result.get("path") or "").strip(),
            "success": bool(result.get("success", True)),
            "summary": result.get("summary"),
            "completeness_status": result.get("completeness_status"),
            "llm_compacted": True,
        }
        for key in ("counts", "extension_counts", "evidence_scope"):
            value = result.get(key)
            if isinstance(value, dict):
                compact[key] = value
        for key in ("status_files", "status_count_sources", "incomplete_examples", "sample_items"):
            value = result.get(key)
            if isinstance(value, list) and value:
                compact[key] = value[:20]
        reconciliation = result.get("reconciliation")
        if isinstance(reconciliation, dict):
            compact["reconciliation"] = reconciliation
        if result.get("status_counts_confidence") is not None:
            compact["status_counts_confidence"] = result.get("status_counts_confidence")
        return compact
    if operation != "list":
        return None

    items = result.get("items")
    if not isinstance(items, list):
        return None

    count_raw = result.get("count")
    try:
        total_count = int(count_raw)
    except Exception:
        total_count = len(items)

    file_count = 0
    directory_count = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "").strip().lower()
        if item_type == "file":
            file_count += 1
        elif item_type == "directory":
            directory_count += 1

    path = str(result.get("path") or "").strip()
    preview_limit = min(len(items), cls.MAX_FILE_OPERATION_LIST_SAMPLE_ITEMS)
    evidence_scope = result.get("evidence_scope") if isinstance(result.get("evidence_scope"), dict) else None
    compact_result: Dict[str, Any] = {}

    while True:
        sample_items: List[Dict[str, Any]] = []
        for item in items[:preview_limit]:
            if not isinstance(item, dict):
                continue
            sample_item: Dict[str, Any] = {
                "name": str(item.get("name") or ""),
                "type": str(item.get("type") or ""),
            }
            size_value = item.get("size")
            if isinstance(size_value, (int, float)):
                sample_item["size"] = int(size_value)
            sample_items.append(sample_item)

        compact_result = {
            "operation": "list",
            "path": path,
            "success": bool(result.get("success", True)),
            "count": total_count,
            "files_count": file_count,
            "directories_count": directory_count,
            "sample_items": sample_items,
            "omitted_items": max(0, total_count - len(sample_items)),
            "llm_compacted": True,
            "summary": (
                f"Listed {total_count} items under {path or '.'} "
                f"({file_count} files, {directory_count} directories). "
                f"Showing the first {len(sample_items)} item(s) only because the full directory listing is too large for LLM context."
            ),
        }
        if evidence_scope:
            compact_result["evidence_scope"] = evidence_scope
            status_counts = evidence_scope.get("status_counts")
            if isinstance(status_counts, dict):
                compact_result["status_counts"] = status_counts
            completeness_status = evidence_scope.get("completeness_status")
            if isinstance(completeness_status, str) and completeness_status:
                compact_result["completeness_status"] = completeness_status
        compact_text = json.dumps(compact_result, ensure_ascii=False, default=str)
        if len(compact_text) <= cls.MAX_TOOL_RESULT_TEXT_CHARS or preview_limit == 0:
            return compact_result
        if preview_limit <= 5:
            preview_limit = 0
        else:
            preview_limit //= 2


def _append_tool_cycle_messages(
    *,
    messages: List[Dict[str, Any]],
    tool_results: List[Dict[str, Any]],
    assistant_content: str,
    current_step: "ThinkingStep",
) -> None:
    """Build assistant + tool messages from a tool execution cycle and update the step."""
    assistant_msg: Dict[str, Any] = {"role": "assistant", "content": assistant_content}
    assistant_msg["tool_calls"] = [
        {
            "id": item["tool_call_id"],
            "type": "function",
            "function": {
                "name": item["tool_name"],
                "arguments": json.dumps(item.get("tool_params") or {}, ensure_ascii=False),
            },
        }
        for item in tool_results
    ]
    messages.append(assistant_msg)
    for item in tool_results:
        messages.append(
            {
                "role": "tool",
                "tool_call_id": item["tool_call_id"],
                "content": item["tool_result_text"],
            }
        )
    per_tool_text = [
        f"[{item['tool_name']}] {item['tool_result_text']}"
        for item in tool_results
    ]
    current_step.action_result = "\n\n".join(per_tool_text)
    merged_evidence: List[Dict[str, str]] = []
    for item in tool_results:
        merged_evidence.extend(item.get("evidence") or [])
    current_step.evidence = merged_evidence
    current_step.finished_at = datetime.now()


def _contains_tool(tool_results: List[Dict[str, Any]], tool_name: str) -> bool:
    for item in tool_results:
        if str(item.get("tool_name") or "").strip().lower() == tool_name:
            return True
    return False


def _build_tool_cycle_signature(cls: Any, tool_results: List[Dict[str, Any]]) -> str:
    signature_parts: List[str] = []
    for item in tool_results:
        tool_name = str(item.get("tool_name") or "").strip().lower()
        tool_params = item.get("tool_params") or {}
        result_marker = cls._extract_tool_result_marker(tool_name, item.get("tool_result_text"))
        try:
            params_text = json.dumps(tool_params, ensure_ascii=False, sort_keys=True, default=str)
        except Exception:
            params_text = str(tool_params)
        signature_parts.append(f"{tool_name}|{params_text}|{result_marker}")
    raw = "\n".join(signature_parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _extract_tool_result_marker(cls: Any, tool_name: str, tool_result_text: Any) -> str:
    raw_text = str(tool_result_text or "")
    if tool_name == "phagescope":
        state = cls._extract_phagescope_state(raw_text)
        if state:
            return (
                f"task={state.get('task_id')};status={state.get('status')};"
                f"task_status={state.get('task_status')};progress={state.get('progress')};"
                f"waiting={state.get('waiting')};running={state.get('running')};failed={state.get('failed')}"
            )
    normalized = cls._normalize_marker_text(raw_text)
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


def _normalize_marker_text(raw_text: str) -> str:
    text = raw_text or ""
    # Remove timestamp-like values to make stability detection resilient.
    text = re.sub(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?", "<ts>", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > 600:
        text = text[:600]
    return text


def _extract_phagescope_state(cls: Any, tool_result_text: str) -> Optional[Dict[str, Any]]:
    try:
        payload = json.loads(tool_result_text)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    result = payload.get("result")
    if not isinstance(result, dict):
        # Prompt-based DeepThink may serialize tool payload directly instead of
        # wrapping under {"result": ...}. Accept that shape as well.
        if "data" in payload or "action" in payload or "status_code" in payload:
            result = payload
        else:
            return None

    data = result.get("data")
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, dict):
        return None

    task_id = results.get("id") or result.get("taskid") or result.get("task_id") or ""
    status = str(results.get("status") or "").strip()
    task_status = ""
    progress = ""
    waiting = 0
    running = 0
    failed = 0
    completed = 0
    total = 0

    detail_raw = results.get("task_detail")
    detail: Optional[Dict[str, Any]] = None
    if isinstance(detail_raw, dict):
        detail = detail_raw
    elif isinstance(detail_raw, str):
        stripped = detail_raw.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, dict):
                    detail = parsed
            except Exception:
                detail = None

    if isinstance(detail, dict):
        task_status = str(detail.get("task_status") or "").strip()
        queue = detail.get("task_que")
        if isinstance(queue, list):
            total = len(queue)
            for module_item in queue:
                if not isinstance(module_item, dict):
                    continue
                module_status = str(module_item.get("module_satus") or "").strip().lower()
                if module_status == "completed":
                    completed += 1
                elif module_status in {"waiting", "wait"}:
                    waiting += 1
                elif module_status in {"running", "create", "queued"}:
                    running += 1
                elif module_status in {"failed", "error"}:
                    failed += 1
        if total > 0:
            progress = f"{completed}/{total}"

    return {
        "task_id": str(task_id),
        "status": status,
        "task_status": task_status,
        "progress": progress,
        "waiting": waiting,
        "running": running,
        "failed": failed,
    }


def _build_repetition_stop_answer(
    cls: Any,
    tool_results: List[Dict[str, Any]],
    repeated_cycles: int,
) -> str:
    phagescope_state: Optional[Dict[str, Any]] = None
    for item in tool_results:
        if str(item.get("tool_name") or "").strip().lower() != "phagescope":
            continue
        phagescope_state = cls._extract_phagescope_state(str(item.get("tool_result_text") or ""))
        if phagescope_state:
            break

    if phagescope_state:
        task_id = phagescope_state.get("task_id") or "unknown"
        status = phagescope_state.get("status") or "unknown"
        task_status = phagescope_state.get("task_status") or "unknown"
        progress = phagescope_state.get("progress") or "unknown"
        return (
            f"PhageScope task {task_id} is still unchanged after {repeated_cycles} polling cycles "
            f"(status={status}, task_status={task_status}, module_progress={progress}).\n\n"
            "DeepThink stopped active polling to avoid an infinite loop. "
            "Please retry status check later, or continue once the remote task state changes."
        )

    return (
        "Tool outputs remained unchanged across repeated cycles, so DeepThink stopped active polling "
        f"after {repeated_cycles} repeats to avoid an infinite loop. "
        "Please retry later or provide new constraints."
    )


def _clip_log_text(value: Any, *, limit: int = 400) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _sanitize_tool_params_for_log(cls: Any, params: Any) -> str:
    redact_tokens = ("password", "passwd", "secret", "token", "api_key", "apikey", "authorization")

    def _sanitize(value: Any, depth: int = 0) -> Any:
        if depth >= 4:
            return "<truncated>"
        if isinstance(value, dict):
            sanitized: Dict[str, Any] = {}
            for key, item in value.items():
                key_text = str(key)
                key_lower = key_text.lower()
                if any(token in key_lower for token in redact_tokens):
                    sanitized[key_text] = "<redacted>"
                    continue
                sanitized[key_text] = _sanitize(item, depth + 1)
            return sanitized
        if isinstance(value, list):
            return [_sanitize(item, depth + 1) for item in value[:20]]
        if isinstance(value, tuple):
            return [_sanitize(item, depth + 1) for item in value[:20]]
        if isinstance(value, str):
            return cls._clip_log_text(value, limit=240)
        return value

    try:
        sanitized_params = _sanitize(params)
        raw = json.dumps(sanitized_params, ensure_ascii=False, default=str)
    except Exception:
        raw = str(params)
    return cls._clip_log_text(raw, limit=800)


def _chunk_final_answer(cls: Any, text: str) -> List[str]:
    text = text or ""
    if not text:
        return []

    chunks: List[str] = []
    buffer: List[str] = []
    max_chars = max(8, int(cls.FINAL_STREAM_CHUNK_CHARS))
    split_chars = {".", "!", "?", "\n", ",", ";", ":", "，", "。", "！", "？"}

    for ch in text:
        buffer.append(ch)
        if len(buffer) >= max_chars or (ch in split_chars and len(buffer) >= max_chars // 2):
            chunks.append("".join(buffer))
            buffer = []

    if buffer:
        chunks.append("".join(buffer))
    return chunks


async def _stream_final_answer(agent: "DeepThinkAgent", final_answer: str) -> None:
    if not agent.on_final_delta or not final_answer:
        return
    cleaned_answer = sanitize_professional_response_text(final_answer)
    for chunk in agent._chunk_final_answer(cleaned_answer):
        await agent._safe_final_delta_callback(chunk)
        if agent.FINAL_STREAM_DELAY_SEC > 0:
            await asyncio.sleep(agent.FINAL_STREAM_DELAY_SEC)
