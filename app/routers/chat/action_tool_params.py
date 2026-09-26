"""Per-tool parameter normalization for ``handle_tool_action`` (cluster ③).

Moved out of ``action_handlers.py`` per
design/2026-09-24-backend-godfiles-refactor-plan.md §4.7 (handlers cluster ③).

What moved
----------
The *bodies* of the 21-tool ``if/elif`` chain inside ``handle_tool_action`` were
lifted into ``_normalize_<tool>_params(agent, action, tool_name, params)``
functions: each returns either the ``AgentStep`` the branch used to return
(validation failure) or the normalized params dict.  The dispatcher chain itself
stays in the facade with its order, conditions, error texts and return shapes
untouched — each branch is now a four-line call pattern.  Also moved: the
parametric helper functions and constants those bodies need
(``_clean_existing_path_param``, ``_parse_json_list_param``,
``_scientific_figure_label_key``, ``_coerce_inline_number``,
``_extract_scientific_figure_inline_rows``, ``_READ_ONLY_FILE_OPERATIONS``,
``_BIO_TOOLS_NO_CLAUDE_FALLBACK_KEY``, ``_SEQUENCE_FETCH_NO_CLAUDE_FALLBACK_KEY``);
the facade re-exports them, so the facade's own call sites are unchanged.

Sanctioned deviations (all single-line, all documented below and in the commit):
- ``_normalize_code_executor_params`` additionally returns the ``original_task``
  that the branch used to assign in the enclosing scope (read later in
  ``handle_tool_action``'s post-processing); the dispatcher unpacks the tuple.
- ``_normalize_phagescope_params`` reaches ``_resolve_phagescope_taskid_alias``
  through ``_ah()`` because it is patched on the ``action_handlers`` namespace
  (``app/tests/tools/test_execution_semantics_regressions.py:820``).
- ``_normalize_manuscript_writer_params`` reaches
  ``_align_manuscript_writer_params_with_bound_task`` and
  ``_normalize_scientific_figure_generator_params`` reaches
  ``_build_chat_tool_context`` through ``_ah()``: both helpers stay in the facade
  (the latter consumes ``get_current_job``, which the ``agent.py`` compat bridge
  temporarily rebinds *in the action_handlers namespace*, so its call site must
  keep resolving there).

Deliberately NOT extracted: the ``terminal_session`` branch stays inline in
``handle_tool_action`` — its body calls the patched ``execute_tool``, whose
module-level binding *and call site* must remain in the ``action_handlers``
namespace (hard constraint; ``agent.py:5475-5520`` compat bridge + 5 test
patch sites).

Registry note: the blueprint also mentions a "注册表"; the explicit if/elif
chain is kept verbatim per the hard constraint, so no runtime registry is
introduced (it would be dead code).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from app.config import get_graph_rag_settings, get_search_settings
from app.services.llm.structured_response import LLMAction

from .guardrails import explicit_manuscript_request, local_manuscript_assembly_request
from .models import AgentStep
from .session_helpers import _lookup_phagescope_task_memory, _normalize_search_provider

_READ_ONLY_FILE_OPERATIONS = {"read", "list", "exists", "info", "profile", "census"}
_BIO_TOOLS_NO_CLAUDE_FALLBACK_KEY = "bio_tools_no_claude_fallback"
_SEQUENCE_FETCH_NO_CLAUDE_FALLBACK_KEY = "sequence_fetch_no_claude_fallback"


def _ah() -> Any:
    """Late-bound action_handlers facade module (monkeypatch-friendly lookups)."""
    from . import action_handlers

    return action_handlers


def _clean_existing_path_param(value: str) -> str:
    cleaned = value.strip()
    stripped = cleaned.rstrip(".。；;，,、")
    if stripped != cleaned:
        try:
            if not Path(cleaned).exists() and Path(stripped).exists():
                return stripped
        except Exception:
            pass
    return cleaned


def _parse_json_list_param(value: Any) -> Optional[List[Any]]:
    if isinstance(value, list):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = None
        if isinstance(parsed, list):
            return parsed
    return None


def _scientific_figure_label_key(params: Dict[str, Any]) -> str:
    panels_value = _parse_json_list_param(params.get("panels"))
    if panels_value:
        for panel in panels_value:
            if not isinstance(panel, dict):
                continue
            axis = panel.get("x") or panel.get("label") or panel.get("row")
            if isinstance(axis, str) and axis.strip():
                return axis.strip()
    return "label"


def _coerce_inline_number(value: str) -> Union[int, float]:
    number = float(value)
    return int(number) if number.is_integer() else number


def _extract_scientific_figure_inline_rows(
    user_message: Any,
    *,
    label_key: str,
) -> List[Dict[str, Any]]:
    text = str(user_message or "").strip()
    if not text:
        return []
    match = re.search(
        r"(?:rows?|records?|data)\s*[:：]\s*(?P<body>.+)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    body = match.group("body") if match else text
    body = re.split(
        r"\b(?:make|create|draw|plot|render|export|output_basename|publish)\b",
        body,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    rows: List[Dict[str, Any]] = []
    metric_re = re.compile(
        r"(?P<key>[A-Za-z_][A-Za-z0-9_./%-]*)\s*(?:=|:)?\s*(?P<value>-?\d+(?:\.\d+)?)"
    )
    for segment in re.split(r"[;；\n]+", body):
        cleaned = segment.strip().strip(".。 ,，")
        if not cleaned or not re.search(r"\d", cleaned):
            continue
        metric_matches = list(metric_re.finditer(cleaned))
        if not metric_matches:
            continue
        label = cleaned[: metric_matches[0].start()].strip(" :：,-–—")
        row: Dict[str, Any] = {label_key: label or f"row_{len(rows) + 1}"}
        for metric_match in metric_matches:
            row[metric_match.group("key")] = _coerce_inline_number(
                metric_match.group("value")
            )
        rows.append(row)
    return rows


def _normalize_web_search_params(
    agent: Any,
    action: LLMAction,
    tool_name: str,
    params: Dict[str, Any],
) -> Any:
    query = params.get("query")
    if not isinstance(query, str) or not query.strip():
        return AgentStep(
            action=action,
            success=False,
            message="web_search requires a non-empty query.",
            details={"error": "missing_query", "tool": tool_name},
        )

    provider_value = params.get("provider")
    normalized_provider = _normalize_search_provider(provider_value)
    if not normalized_provider:
        session_provider = _normalize_search_provider(
            agent.extra_context.get("default_search_provider")
        )
        if session_provider:
            normalized_provider = session_provider
        else:
            settings_provider = _normalize_search_provider(
                get_search_settings().default_provider
            )
            normalized_provider = settings_provider or "builtin"
    params["provider"] = normalized_provider
    return params


def _normalize_file_operations_params(
    agent: Any,
    action: LLMAction,
    tool_name: str,
    params: Dict[str, Any],
) -> Any:
    operation = params.get("operation")
    if not isinstance(operation, str) or not operation.strip():
        return AgentStep(
            action=action,
            success=False,
            message="file_operations requires a non-empty `operation` string.",
            details={"error": "invalid_operation", "tool": tool_name},
        )
    operation = operation.strip()
    # Minimal validation for common operations.
    if operation in _READ_ONLY_FILE_OPERATIONS or operation == "delete":
        path = params.get("path")
        if not isinstance(path, str) or not path.strip():
            return AgentStep(
                action=action,
                success=False,
                message=f"file_operations {operation} requires a non-empty `path` string.",
                details={"error": "missing_params", "tool": tool_name},
            )
        clean_params = {"operation": operation, "path": path}
        if operation in {"list", "profile", "census"}:
            pattern = params.get("pattern")
            if isinstance(pattern, str) and pattern.strip():
                clean_params["pattern"] = pattern
        params = clean_params
    elif operation in {"write"}:
        path = params.get("path")
        content = params.get("content")
        if not isinstance(path, str) or not path.strip():
            return AgentStep(
                action=action,
                success=False,
                message="file_operations write requires a non-empty `path` string.",
                details={"error": "missing_params", "tool": tool_name},
            )
        if content is None:
            content = ""
        if not isinstance(content, str):
            content = str(content)
        params = {"operation": operation, "path": path, "content": content}
    elif operation in {"copy", "move"}:
        path = params.get("path")
        dest = params.get("destination")
        if not isinstance(path, str) or not path.strip() or not isinstance(dest, str) or not dest.strip():
            return AgentStep(
                action=action,
                success=False,
                message=f"file_operations {operation} requires `path` and `destination`.",
                details={"error": "missing_params", "tool": tool_name},
            )
        params = {"operation": operation, "path": path, "destination": dest}
    else:
        return AgentStep(
            action=action,
            success=False,
            message=f"file_operations does not support operation={operation!r}.",
            details={"error": "invalid_operation", "tool": tool_name},
        )
    return params


def _normalize_lightrag_query_params(
    agent: Any,
    action: LLMAction,
    tool_name: str,
    params: Dict[str, Any],
) -> Any:
    query = params.get("query")
    if not isinstance(query, str) or not query.strip():
        return AgentStep(
            action=action,
            success=False,
            message="lightrag_query requires a non-empty query.",
            details={"error": "missing_query", "tool": tool_name},
        )

    def _safe_lightrag_int(value: Any, default: int, minimum: int, maximum: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        return max(minimum, min(parsed, maximum))

    mode_raw = params.get("mode")
    mode = str(mode_raw or "mix").strip() or "mix"
    include_references = params.get("include_references")
    if include_references is None:
        include_references = True
    params = {
        "query": query.strip(),
        "mode": mode,
        "top_k": _safe_lightrag_int(params.get("top_k"), 5, 1, 20),
        "max_chunks": _safe_lightrag_int(params.get("max_chunks"), 12, 1, 40),
        "max_references": _safe_lightrag_int(params.get("max_references"), 12, 1, 40),
        "include_references": bool(include_references),
    }
    return params


def _normalize_graph_rag_params(
    agent: Any,
    action: LLMAction,
    tool_name: str,
    params: Dict[str, Any],
) -> Any:
    query = params.get("query")
    if not isinstance(query, str) or not query.strip():
        return AgentStep(
            action=action,
            success=False,
            message="graph_rag requires a non-empty query.",
            details={"error": "missing_query", "tool": tool_name},
        )

    rag_settings = get_graph_rag_settings()

    def _safe_int(value: Any, default: int, minimum: int, maximum: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        return max(minimum, min(parsed, maximum))

    default_top_k = min(12, rag_settings.max_top_k)
    default_hops = min(1, rag_settings.max_hops)

    top_k = _safe_int(
        params.get("top_k"),
        default=default_top_k,
        minimum=1,
        maximum=rag_settings.max_top_k,
    )
    hops = _safe_int(
        params.get("hops"),
        default=default_hops,
        minimum=0,
        maximum=rag_settings.max_hops,
    )
    return_subgraph = params.get("return_subgraph")
    if return_subgraph is None:
        return_subgraph = True
    else:
        return_subgraph = bool(return_subgraph)

    focus_raw = params.get("focus_entities")
    focus_entities: List[str] = []
    if isinstance(focus_raw, list):
        for item in focus_raw:
            if isinstance(item, str) and item.strip():
                focus_entities.append(item.strip())

    params = {
        "query": query.strip(),
        "top_k": top_k,
        "hops": hops,
        "return_subgraph": return_subgraph,
        "focus_entities": focus_entities,
    }
    return params


def _normalize_literature_pipeline_params(
    agent: Any,
    action: LLMAction,
    tool_name: str,
    params: Dict[str, Any],
) -> Any:
    query = params.get("query")
    if not isinstance(query, str) or not query.strip():
        return AgentStep(
            action=action,
            success=False,
            message="literature_pipeline requires a non-empty `query` string.",
            details={"error": "missing_query", "tool": tool_name},
        )
    clean_params: Dict[str, Any] = {"query": query.strip()}
    max_results = params.get("max_results")
    if max_results is not None:
        try:
            clean_params["max_results"] = int(max_results)
        except (TypeError, ValueError):
            pass
    out_dir = params.get("out_dir")
    if isinstance(out_dir, str) and out_dir.strip():
        clean_params["out_dir"] = out_dir.strip()
    download_pdfs = params.get("download_pdfs")
    if isinstance(download_pdfs, bool):
        clean_params["download_pdfs"] = download_pdfs
    max_pdfs = params.get("max_pdfs")
    if max_pdfs is not None:
        try:
            clean_params["max_pdfs"] = int(max_pdfs)
        except (TypeError, ValueError):
            pass
    user_agent = params.get("user_agent")
    if isinstance(user_agent, str) and user_agent.strip():
        clean_params["user_agent"] = user_agent.strip()
    proxy = params.get("proxy")
    if isinstance(proxy, str) and proxy.strip():
        clean_params["proxy"] = proxy.strip()
    if isinstance(agent.session_id, str) and agent.session_id.strip():
        clean_params["session_id"] = agent.session_id.strip()
    params = clean_params
    return params


def _normalize_review_pack_writer_params(
    agent: Any,
    action: LLMAction,
    tool_name: str,
    params: Dict[str, Any],
) -> Any:
    topic = params.get("topic")
    if not isinstance(topic, str) or not topic.strip():
        return AgentStep(
            action=action,
            success=False,
            message="review_pack_writer requires a non-empty `topic` string.",
            details={"error": "missing_topic", "tool": tool_name},
        )
    clean_params: Dict[str, Any] = {"topic": topic.strip()}
    query = params.get("query")
    if isinstance(query, str) and query.strip():
        clean_params["query"] = query.strip()
    out_dir = params.get("out_dir")
    if isinstance(out_dir, str) and out_dir.strip():
        clean_params["out_dir"] = out_dir.strip()
    for int_key in ("max_results", "max_pdfs", "max_revisions"):
        if int_key in params and params[int_key] is not None:
            try:
                clean_params[int_key] = int(params[int_key])
            except (TypeError, ValueError):
                pass
    if "evaluation_threshold" in params and params["evaluation_threshold"] is not None:
        try:
            clean_params["evaluation_threshold"] = float(params["evaluation_threshold"])
        except (TypeError, ValueError):
            pass
    for bool_key in ("download_pdfs", "keep_workspace"):
        if isinstance(params.get(bool_key), bool):
            clean_params[bool_key] = params[bool_key]
    output_path = params.get("output_path")
    if isinstance(output_path, str) and output_path.strip():
        clean_params["output_path"] = output_path.strip()
    sections = params.get("sections")
    if isinstance(sections, list):
        clean_sections: List[str] = []
        for item in sections:
            if isinstance(item, str) and item.strip():
                clean_sections.append(item.strip())
        if clean_sections:
            clean_params["sections"] = clean_sections
    task_value = params.get("task")
    if isinstance(task_value, str) and task_value.strip():
        clean_params["task"] = task_value.strip()
    for key in (
        "generation_model",
        "evaluation_model",
        "merge_model",
        "generation_provider",
        "evaluation_provider",
        "merge_provider",
        "user_agent",
        "proxy",
    ):
        val = params.get(key)
        if isinstance(val, str) and val.strip():
            clean_params[key] = val.strip()
    if isinstance(agent.session_id, str) and agent.session_id.strip():
        clean_params["session_id"] = agent.session_id.strip()
    params = clean_params
    return params


def _normalize_sequence_fetch_params(
    agent: Any,
    action: LLMAction,
    tool_name: str,
    params: Dict[str, Any],
) -> Any:
    clean_params: Dict[str, Any] = {}
    accession_value = params.get("accession")
    accessions_value = params.get("accessions")

    if isinstance(accession_value, str) and accession_value.strip():
        clean_params["accession"] = accession_value.strip()

    if isinstance(accessions_value, list):
        accessions_clean = [
            str(item).strip()
            for item in accessions_value
            if str(item).strip()
        ]
        if accessions_clean:
            clean_params["accessions"] = accessions_clean

    if not clean_params.get("accession") and not clean_params.get("accessions"):
        return AgentStep(
            action=action,
            success=False,
            message="sequence_fetch requires `accession` or `accessions`.",
            details={"error": "missing_accession", "tool": tool_name},
        )

    database_value = params.get("database")
    if isinstance(database_value, str) and database_value.strip():
        clean_params["database"] = database_value.strip()

    format_value = params.get("format")
    if isinstance(format_value, str) and format_value.strip():
        clean_params["format"] = format_value.strip()

    output_name = params.get("output_name")
    if isinstance(output_name, str) and output_name.strip():
        clean_params["output_name"] = output_name.strip()

    timeout_sec = params.get("timeout_sec")
    if timeout_sec is not None:
        try:
            clean_params["timeout_sec"] = float(timeout_sec)
        except (TypeError, ValueError):
            pass

    max_bytes = params.get("max_bytes")
    if max_bytes is not None:
        try:
            clean_params["max_bytes"] = int(max_bytes)
        except (TypeError, ValueError):
            pass

    session_id_value = params.get("session_id")
    if isinstance(session_id_value, str) and session_id_value.strip():
        clean_params["session_id"] = session_id_value.strip()
    elif isinstance(agent.session_id, str) and agent.session_id.strip():
        clean_params["session_id"] = agent.session_id.strip()

    params = clean_params
    return params


def _normalize_url_fetch_params(
    agent: Any,
    action: LLMAction,
    tool_name: str,
    params: Dict[str, Any],
) -> Any:
    clean_params = {}
    url_value = params.get("url")
    if isinstance(url_value, str) and url_value.strip():
        clean_params["url"] = url_value.strip()
    else:
        return AgentStep(
            action=action,
            success=False,
            message="url_fetch requires `url`.",
            details={"error": "missing_url", "tool": tool_name},
        )

    output_name_value = params.get("output_name")
    if isinstance(output_name_value, str) and output_name_value.strip():
        clean_params["output_name"] = output_name_value.strip()

    timeout_sec = params.get("timeout_sec")
    if timeout_sec is not None:
        try:
            clean_params["timeout_sec"] = float(timeout_sec)
        except (TypeError, ValueError):
            pass

    max_bytes = params.get("max_bytes")
    if max_bytes is not None:
        try:
            clean_params["max_bytes"] = int(max_bytes)
        except (TypeError, ValueError):
            pass

    allowed_types = params.get("allowed_content_types")
    if isinstance(allowed_types, str):
        cleaned_allowed = [
            chunk.strip()
            for chunk in allowed_types.split(",")
            if chunk.strip()
        ]
        if cleaned_allowed:
            clean_params["allowed_content_types"] = cleaned_allowed
    elif isinstance(allowed_types, list):
        cleaned_allowed = [
            str(item).strip()
            for item in allowed_types
            if str(item).strip()
        ]
        if cleaned_allowed:
            clean_params["allowed_content_types"] = cleaned_allowed

    sha256_value = params.get("sha256")
    if isinstance(sha256_value, str) and sha256_value.strip():
        clean_params["sha256"] = sha256_value.strip()

    session_id_value = params.get("session_id")
    if isinstance(session_id_value, str) and session_id_value.strip():
        clean_params["session_id"] = session_id_value.strip()
    elif isinstance(agent.session_id, str) and agent.session_id.strip():
        clean_params["session_id"] = agent.session_id.strip()

    params = clean_params
    return params


async def _normalize_code_executor_params(
    agent: Any,
    action: LLMAction,
    tool_name: str,
    params: Dict[str, Any],
) -> Any:
    seq_block_payload = agent.extra_context.get(_SEQUENCE_FETCH_NO_CLAUDE_FALLBACK_KEY)
    if seq_block_payload:
        seq_root_reason = (
            str(seq_block_payload.get("summary") or "").strip()
            if isinstance(seq_block_payload, dict)
            else ""
        )
        seq_reason_text = (
            "code_executor fallback is blocked because sequence_fetch failed in input/download stage. "
            "Retry sequence_fetch with valid accession input."
        )
        if seq_root_reason:
            seq_reason_text = f"{seq_reason_text} Root cause: {seq_root_reason}"
        seq_details = {
            "error": seq_reason_text,
            "error_code": "sequence_fetch_failed_no_fallback",
            "blocked_reason": "sequence_fetch_failed_no_fallback",
            "tool": tool_name,
            "result": {
                "success": False,
                "tool": tool_name,
                "error": seq_reason_text,
                "error_code": "sequence_fetch_failed_no_fallback",
                "blocked_reason": "sequence_fetch_failed_no_fallback",
            },
        }
        if isinstance(seq_block_payload, dict):
            seq_details["sequence_fetch_block_context"] = seq_block_payload
        return AgentStep(
            action=action,
            success=False,
            message=seq_reason_text,
            details=seq_details,
        )

    block_payload = agent.extra_context.get(_BIO_TOOLS_NO_CLAUDE_FALLBACK_KEY)
    if block_payload:
        root_reason = (
            str(block_payload.get("summary") or "").strip()
            if isinstance(block_payload, dict)
            else ""
        )
        reason_text = (
            "code_executor fallback is blocked because bio_tools input preparation failed. "
            "Retry bio_tools with a valid FASTA/raw sequence input."
        )
        if root_reason:
            reason_text = f"{reason_text} Root cause: {root_reason}"
        details = {
            "error": reason_text,
            "error_code": "bio_tools_input_preparation_failed",
            "blocked_reason": "bio_tools_input_preparation_failed",
            "tool": tool_name,
            "result": {
                "success": False,
                "tool": tool_name,
                "error": reason_text,
                "error_code": "bio_tools_input_preparation_failed",
                "blocked_reason": "bio_tools_input_preparation_failed",
            },
        }
        if isinstance(block_payload, dict):
            details["bio_tools_block_context"] = block_payload
        return AgentStep(
            action=action,
            success=False,
            message=reason_text,
            details=details,
        )

    prepared = await agent._prepare_code_executor_params(
        action=action,
        tool_name=tool_name,
        params=params,
    )
    if isinstance(prepared, AgentStep):
        return prepared
    params, original_task = prepared
    # The delegation happens *inside* this call and can run for hours, so the
    # handler needs the turn's progress channel: its CLI lanes report through
    # ``ToolContext.on_progress`` and this lane never attached a context, which
    # left a delegated run invisible in the parent's stream until it finished.
    # Behaviour-neutral for the tool itself: ``code_executor`` reads only
    # ``on_progress`` (and ``model_provider``, which this context leaves unset)
    # off the context, never its work_dir/plan/task fields.
    if "tool_context" not in params:
        tool_context = _ah()._build_chat_tool_context(agent, tool_name)
        if tool_context is not None:
            params["tool_context"] = tool_context
    return params, original_task


def _normalize_document_reader_params(
    agent: Any,
    action: LLMAction,
    tool_name: str,
    params: Dict[str, Any],
) -> Any:
    operation = params.get("operation")
    file_path = params.get("file_path")

    if not operation or not file_path:
        return AgentStep(
            action=action,
            success=False,
            message="document_reader requires `operation` and `file_path`.",
            details={"error": "missing_params", "tool": tool_name},
        )

    # Validate action type.
    if operation not in [
        "read_pdf",
        "read_image",
        "read_text",
        "read_any",
        "read_file",
        "auto",
    ]:
        return AgentStep(
            action=action,
            success=False,
            message=f"Unsupported operation: {operation}",
            details={"error": "invalid_operation", "tool": tool_name},
        )

    params = {
        "operation": operation,
        "file_path": file_path,
        "use_ocr": params.get("use_ocr", False),
    }
    return params


def _normalize_vision_reader_params(
    agent: Any,
    action: LLMAction,
    tool_name: str,
    params: Dict[str, Any],
) -> Any:
    operation = params.get("operation")
    image_path = params.get("image_path") or params.get("file_path")

    if not operation or not image_path:
        return AgentStep(
            action=action,
            success=False,
            message="vision_reader requires `operation` and `image_path` or `file_path`.",
            details={"error": "missing_params", "tool": tool_name},
        )

    page_number = params.get("page_number")
    page_numbers = params.get("page_numbers")
    max_pages = params.get("max_pages")
    region = params.get("region")
    question = params.get("question")
    language = params.get("language")

    clean_params: Dict[str, Any] = {
        "operation": operation,
        "image_path": image_path,
    }
    if isinstance(page_number, int):
        clean_params["page_number"] = page_number
    # PDF parsing is billed per page, so a page selection must survive the lane
    # unchanged: it is what bounds the charge.
    if isinstance(page_numbers, list):
        selected: list[int] = []
        for value in page_numbers:
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                continue
            if value not in selected:
                selected.append(value)
        if selected:
            clean_params["page_numbers"] = selected
    if isinstance(max_pages, int) and not isinstance(max_pages, bool) and max_pages > 0:
        clean_params["max_pages"] = max_pages
    if isinstance(region, dict):
        clean_params["region"] = region
    if isinstance(question, str):
        clean_params["question"] = question
    if isinstance(language, str):
        clean_params["language"] = language

    params = clean_params
    return params


def _normalize_paper_replication_params(
    agent: Any,
    action: LLMAction,
    tool_name: str,
    params: Dict[str, Any],
) -> Any:
    # Paper replication ExperimentCard loader
    exp_id = params.get("experiment_id")
    if exp_id is None:
        exp_id = "experiment_1"
    elif not isinstance(exp_id, str):
        try:
            exp_id = str(exp_id)
        except Exception:
            exp_id = "experiment_1"

    params = {"experiment_id": exp_id}
    return params


def _normalize_generate_experiment_card_params(
    agent: Any,
    action: LLMAction,
    tool_name: str,
    params: Dict[str, Any],
) -> Any:
    exp_id = params.get("experiment_id")
    if exp_id is not None and not isinstance(exp_id, str):
        exp_id = str(exp_id)
    pdf_path = params.get("pdf_path")
    if pdf_path is not None and not isinstance(pdf_path, str):
        pdf_path = str(pdf_path)
    code_root = params.get("code_root")
    if code_root is not None and not isinstance(code_root, str):
        code_root = str(code_root)
    notes_val = params.get("notes")
    if notes_val is not None and not isinstance(notes_val, str):
        notes_val = str(notes_val)
    overwrite_val = params.get("overwrite")
    overwrite = False
    if isinstance(overwrite_val, bool):
        overwrite = overwrite_val
    elif isinstance(overwrite_val, str):
        overwrite = overwrite_val.strip().lower() in {"1", "true", "yes", "y"}

    params = {
        "experiment_id": exp_id,
        "pdf_path": pdf_path,
        "code_root": code_root,
        "notes": notes_val,
        "overwrite": overwrite,
    }
    return params


def _normalize_bio_tools_params(
    agent: Any,
    action: LLMAction,
    tool_name: str,
    params: Dict[str, Any],
) -> Any:
    raw_tool_name = params.get("tool_name")
    operation = params.get("operation", "help")
    if not isinstance(raw_tool_name, str) or not raw_tool_name.strip():
        return AgentStep(
            action=action,
            success=False,
            message="bio_tools requires a non-empty `tool_name` string.",
            details={"error": "missing_tool_name", "tool": tool_name},
        )
    if not isinstance(operation, str) or not operation.strip():
        return AgentStep(
            action=action,
            success=False,
            message="bio_tools requires a non-empty `operation` string.",
            details={"error": "missing_operation", "tool": tool_name},
        )

    clean_params: Dict[str, Any] = {
        "tool_name": raw_tool_name.strip(),
        "operation": operation.strip(),
    }

    for key in ("input_file", "output_file", "job_id"):
        value = params.get(key)
        if isinstance(value, str) and value.strip():
            if key in {"data_dir", "output_dir"}:
                clean_params[key] = _clean_existing_path_param(value)
            else:
                clean_params[key] = value.strip()

    sequence_text = params.get("sequence_text")
    if isinstance(sequence_text, str) and sequence_text.strip():
        clean_params["sequence_text"] = sequence_text.strip()

    tool_params = params.get("params")
    if isinstance(tool_params, dict):
        clean_params["params"] = tool_params

    timeout_value = params.get("timeout")
    if timeout_value is not None:
        try:
            clean_params["timeout"] = int(timeout_value)
        except (TypeError, ValueError):
            pass

    background_value = params.get("background")
    if isinstance(background_value, bool):
        clean_params["background"] = background_value
    elif isinstance(background_value, str):
        normalized = background_value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            clean_params["background"] = True
        elif normalized in {"0", "false", "no", "n", "off"}:
            clean_params["background"] = False

    if isinstance(agent.session_id, str) and agent.session_id.strip():
        clean_params["session_id"] = agent.session_id.strip()

    params = clean_params
    return params


def _normalize_phagescope_params(
    agent: Any,
    action: LLMAction,
    tool_name: str,
    params: Dict[str, Any],
) -> Any:
    if "result_kind" not in params:
        for alias in ("resultkind", "resultKind", "result_type", "resultType"):
            if alias in params and params[alias] is not None:
                params["result_kind"] = params[alias]
                break
    if "taskid" not in params:
        for alias in ("task_id", "taskId"):
            if alias in params and params[alias] is not None:
                params["taskid"] = params[alias]
                break
    if "phageid" not in params:
        for alias in ("phage_id", "phageId"):
            if alias in params and params[alias] is not None:
                params["phageid"] = params[alias]
                break
    if "phageids" not in params:
        for alias in ("phage_ids", "phageIds"):
            if alias in params and params[alias] is not None:
                params["phageids"] = params[alias]
                break

    # Compat aliases used by some prompts/tool wrappers.
    sequence_ids_value = None
    for alias in ("sequence_ids", "sequenceIds", "sequence_id", "sequenceId", "idlist"):
        if alias in params and params[alias] is not None:
            sequence_ids_value = params[alias]
            break
    if sequence_ids_value is not None and not params.get("phageid") and not params.get("phageids"):
        seq_items: List[str] = []
        if isinstance(sequence_ids_value, (list, tuple, set)):
            seq_items = [str(v).strip() for v in sequence_ids_value if str(v).strip()]
        elif isinstance(sequence_ids_value, str):
            raw = sequence_ids_value.strip()
            if raw:
                parsed = None
                if raw.startswith("["):
                    try:
                        parsed = json.loads(raw.replace("'", '"'))
                    except Exception:
                        parsed = None
                if isinstance(parsed, list):
                    seq_items = [str(v).strip() for v in parsed if str(v).strip()]
                else:
                    normalized = raw.replace(",", ";").replace("\n", ";")
                    seq_items = [chunk.strip() for chunk in normalized.split(";") if chunk.strip()]
        else:
            text = str(sequence_ids_value).strip()
            if text:
                seq_items = [text]
        if seq_items:
            params["phageid"] = seq_items[0] if len(seq_items) == 1 else json.dumps(seq_items, ensure_ascii=False)
            params["phageids"] = ";".join(seq_items)

    action_value = params.get("action")
    if not isinstance(action_value, str) or not action_value.strip():
        return AgentStep(
            action=action,
            success=False,
            message="phagescope requires a non-empty `action` string.",
            details={"error": "missing_action", "tool": tool_name},
        )

    clean_params: Dict[str, Any] = {
        "action": action_value.strip(),
    }
    for key in (
        "base_url",
        "token",
        "timeout",
        "phageid",
        "phageids",
        "sequence_ids",
        "inputtype",
        "analysistype",
        "userid",
        "modulelist",
        "rundemo",
        "taskid",
        "modulename",
        "result_kind",
        "module",
        "page",
        "pagesize",
        "seq_type",
        "download_path",
        "save_path",
        "preview_bytes",
        "wait",
        "poll_interval",
        "poll_timeout",
        "sequence",
        "file_path",
        "session_id",
    ):
        if key in params and params[key] is not None:
            clean_params[key] = params[key]

    if isinstance(agent.session_id, str) and agent.session_id.strip():
        clean_params["session_id"] = agent.session_id.strip()

    for int_key in ("page", "pagesize", "preview_bytes"):
        if int_key in clean_params:
            try:
                clean_params[int_key] = int(clean_params[int_key])
            except (TypeError, ValueError):
                clean_params.pop(int_key, None)

    if "timeout" in clean_params:
        try:
            clean_params["timeout"] = float(clean_params["timeout"])
        except (TypeError, ValueError):
            clean_params.pop("timeout", None)

    for float_key in ("poll_interval", "poll_timeout"):
        if float_key in clean_params:
            try:
                clean_params[float_key] = float(clean_params[float_key])
            except (TypeError, ValueError):
                clean_params.pop(float_key, None)

    if "wait" in clean_params and not isinstance(clean_params.get("wait"), bool):
        wait_value = str(clean_params.get("wait", "")).strip().lower()
        clean_params["wait"] = wait_value in {"1", "true", "yes", "y", "on"}

    if isinstance(clean_params.get("rundemo"), bool):
        clean_params["rundemo"] = "true" if clean_params["rundemo"] else "false"

    action_value = clean_params.get("action")
    raw_taskid_value = clean_params.get("taskid")
    if "taskid" in clean_params:
        resolved_taskid = _ah()._resolve_phagescope_taskid_alias(
            clean_params.get("taskid"),
            session_id=agent.session_id if isinstance(agent.session_id, str) else None,
        )
        if resolved_taskid:
            clean_params["taskid"] = resolved_taskid
        else:
            clean_params.pop("taskid", None)
    if (
        action_value in {"result", "quality", "task_detail", "save_all", "task_log"}
        and not clean_params.get("taskid")
        and agent.session_id
    ):
        cached_taskid = _lookup_phagescope_task_memory(
            agent.session_id,
            userid=clean_params.get("userid"),
            phageid=clean_params.get("phageid"),
            modulelist=clean_params.get("modulelist"),
        )
        if cached_taskid:
            clean_params["taskid"] = cached_taskid

    if (
        action_value in {"save_all", "task_log"}
        and raw_taskid_value is not None
        and not clean_params.get("taskid")
    ):
        return AgentStep(
            action=action,
            success=False,
            message=(
                "phagescope requires a numeric remote `taskid` (for example 37468). "
                "The provided value looks like a local job id alias and could not be mapped."
            ),
            details={
                "error": "invalid_taskid",
                "tool": tool_name,
                "provided_taskid": str(raw_taskid_value),
            },
        )

    if (
        action_value == "task_detail"
        and raw_taskid_value is not None
        and not clean_params.get("taskid")
        and not clean_params.get("phageid")
        and not clean_params.get("phageids")
    ):
        return AgentStep(
            action=action,
            success=False,
            message=(
                "phagescope task_detail requires a numeric remote `taskid` "
                "(for example 37468) when phageid is not provided."
            ),
            details={
                "error": "invalid_taskid",
                "tool": tool_name,
                "provided_taskid": str(raw_taskid_value),
            },
        )

    if action_value == "quality" or (
        action_value == "result"
        and str(clean_params.get("result_kind") or "").strip().lower()
        == "quality"
    ):
        clean_params.setdefault("wait", True)
        clean_params.setdefault("poll_interval", 2.0)
        clean_params.setdefault("poll_timeout", 120.0)

    params = clean_params
    return params


def _normalize_phagescope_research_params(
    agent: Any,
    action: LLMAction,
    tool_name: str,
    params: Dict[str, Any],
) -> Any:
    action_value = params.get("action", "audit")
    if not isinstance(action_value, str) or not action_value.strip():
        return AgentStep(
            action=action,
            success=False,
            message="phagescope_research requires a non-empty `action` string.",
            details={"error": "missing_action", "tool": tool_name},
        )

    clean_params: Dict[str, Any] = {"action": action_value.strip()}
    for key in (
        "data_dir",
        "output_dir",
        "session_id",
        "label_level",
        "split_group",
    ):
        value = params.get(key)
        if isinstance(value, str) and value.strip():
            if key in {"data_dir", "output_dir"}:
                clean_params[key] = _clean_existing_path_param(value)
            else:
                clean_params[key] = value.strip()

    for int_key in ("min_label_count", "max_rows", "top_n"):
        if int_key in params and params.get(int_key) is not None:
            try:
                clean_params[int_key] = int(params[int_key])
            except (TypeError, ValueError):
                pass

    completeness = params.get("completeness")
    if completeness is not None:
        clean_params["completeness"] = completeness

    if isinstance(agent.session_id, str) and agent.session_id.strip():
        clean_params.setdefault("session_id", agent.session_id.strip())

    params = clean_params
    return params


def _normalize_manuscript_writer_params(
    agent: Any,
    action: LLMAction,
    tool_name: str,
    params: Dict[str, Any],
) -> Any:
    raw_action_params = action.parameters if isinstance(action.parameters, dict) else {}
    params = _ah()._align_manuscript_writer_params_with_bound_task(agent, dict(params))
    user_message = str(getattr(agent, "_current_user_message", "") or "").strip()
    local_manuscript_request = local_manuscript_assembly_request(
        user_message,
        plan_bound=getattr(getattr(agent, "plan_session", None), "plan_id", None) is not None,
        task_bound=(getattr(agent, "extra_context", {}) or {}).get("current_task_id") is not None,
    )
    if explicit_manuscript_request(user_message):
        if not isinstance(params.get("task"), str) or not str(params.get("task") or "").strip():
            params["task"] = user_message
        if not isinstance(params.get("output_path"), str) or not str(params.get("output_path") or "").strip():
            params["output_path"] = "manuscript/manuscript_draft.md"
    aligned_params = dict(params)
    task_value = params.get("task")
    output_path = params.get("output_path")
    if not isinstance(task_value, str) or not task_value.strip():
        return AgentStep(
            action=action,
            success=False,
            message="manuscript_writer requires a non-empty `task` string.",
            details={"error": "invalid_task", "tool": tool_name},
        )
    if not isinstance(output_path, str) or not output_path.strip():
        return AgentStep(
            action=action,
            success=False,
            message="manuscript_writer requires a non-empty `output_path` string.",
            details={"error": "missing_output_path", "tool": tool_name},
        )

    context_paths = aligned_params.get("context_paths") or []
    if isinstance(context_paths, str):
        context_paths = [context_paths]
    if not isinstance(context_paths, list):
        context_paths = []

    analysis_path = aligned_params.get("analysis_path")
    if analysis_path is not None and not isinstance(analysis_path, str):
        analysis_path = str(analysis_path)

    max_context_bytes = aligned_params.get("max_context_bytes")
    if max_context_bytes is not None:
        try:
            max_context_bytes = int(max_context_bytes)
        except (TypeError, ValueError):
            max_context_bytes = None

    params = {
        "task": task_value,
        "output_path": output_path,
        "context_paths": context_paths,
    }
    if analysis_path:
        params["analysis_path"] = analysis_path
    if max_context_bytes:
        params["max_context_bytes"] = max_context_bytes
    if params.get("context_paths") is None:
        params["context_paths"] = []

    sections = aligned_params.get("sections")
    if sections is None:
        sections = raw_action_params.get("sections")
    if isinstance(sections, str):
        sections = [sections]
    if isinstance(sections, list):
        params["sections"] = sections

    article_mode = raw_action_params.get("article_mode")
    if article_mode is not None:
        params["article_mode"] = article_mode

    max_revisions = raw_action_params.get("max_revisions")
    if max_revisions is not None:
        params["max_revisions"] = max_revisions

    evaluation_threshold = raw_action_params.get("evaluation_threshold")
    if evaluation_threshold is not None:
        params["evaluation_threshold"] = evaluation_threshold

    generation_model = raw_action_params.get("generation_model")
    if generation_model is not None:
        params["generation_model"] = generation_model
    evaluation_model = raw_action_params.get("evaluation_model")
    if evaluation_model is not None:
        params["evaluation_model"] = evaluation_model
    merge_model = raw_action_params.get("merge_model")
    if merge_model is not None:
        params["merge_model"] = merge_model

    generation_provider = raw_action_params.get("generation_provider")
    if generation_provider is not None:
        params["generation_provider"] = generation_provider
    evaluation_provider = raw_action_params.get("evaluation_provider")
    if evaluation_provider is not None:
        params["evaluation_provider"] = evaluation_provider
    merge_provider = raw_action_params.get("merge_provider")
    if merge_provider is not None:
        params["merge_provider"] = merge_provider
    keep_workspace = raw_action_params.get("keep_workspace")
    if isinstance(keep_workspace, bool):
        params["keep_workspace"] = keep_workspace
    draft_only = raw_action_params.get("draft_only")
    if local_manuscript_request and not isinstance(draft_only, bool):
        draft_only = True
    if isinstance(draft_only, bool):
        params["draft_only"] = draft_only

    if agent.session_id:
        params["session_id"] = agent.session_id
    return params


def _normalize_result_interpreter_params(
    agent: Any,
    action: LLMAction,
    tool_name: str,
    params: Dict[str, Any],
) -> Any:
    operation = params.get("operation")
    if not isinstance(operation, str) or not operation.strip():
        return AgentStep(
            action=action,
            success=False,
            message="result_interpreter requires a non-empty `operation` string.",
            details={"error": "missing_operation", "tool": tool_name},
        )
    operation = operation.strip()
    valid_ops = {"metadata", "profile", "generate", "execute", "analyze", "plan_analyze"}
    if operation not in valid_ops:
        return AgentStep(
            action=action,
            success=False,
            message=f"Unsupported result_interpreter operation: {operation!r}.",
            details={"error": "invalid_operation", "tool": tool_name},
        )
    clean_params: Dict[str, Any] = {"operation": operation}
    fp = params.get("file_path")
    if isinstance(fp, str) and fp.strip():
        clean_params["file_path"] = fp.strip()
    for list_key in ("file_paths", "data_paths"):
        raw_list = params.get(list_key)
        if isinstance(raw_list, list):
            cleaned = [
                str(x).strip()
                for x in raw_list
                if isinstance(x, str) and x.strip()
            ]
            if cleaned:
                clean_params[list_key] = cleaned
    for key in ("task_title", "task_description", "code", "work_dir", "data_dir", "output_dir"):
        val = params.get(key)
        if isinstance(val, str) and val.strip():
            clean_params[key] = val.strip()
    for int_key in ("max_depth", "node_budget"):
        if params.get(int_key) is not None:
            try:
                clean_params[int_key] = int(params[int_key])
            except (TypeError, ValueError):
                pass
    params = clean_params
    return params


def _normalize_scientific_figure_generator_params(
    agent: Any,
    action: LLMAction,
    tool_name: str,
    params: Dict[str, Any],
) -> Any:
    datasets_value = _parse_json_list_param(params.get("datasets"))
    if datasets_value is None:
        rows = _extract_scientific_figure_inline_rows(
            getattr(agent, "_current_user_message", None),
            label_key=_scientific_figure_label_key(params),
        )
        dataset_name = params.get("datasets") or params.get("dataset") or "dataset_1"
        if rows:
            datasets_value = [
                {
                    "name": str(dataset_name).strip() or "dataset_1",
                    "rows": rows,
                }
            ]
    if datasets_value is None:
        return AgentStep(
            action=action,
            success=False,
            message="scientific_figure_generator requires `datasets` as a non-empty array.",
            details={"error": "missing_datasets", "tool": tool_name},
        )
    datasets = [dict(item) for item in datasets_value if isinstance(item, dict)]
    if not datasets:
        return AgentStep(
            action=action,
            success=False,
            message="scientific_figure_generator requires at least one dataset object.",
            details={"error": "missing_datasets", "tool": tool_name},
        )

    clean_params: Dict[str, Any] = {"datasets": datasets}
    title = params.get("title")
    if isinstance(title, str) and title.strip():
        clean_params["title"] = title.strip()

    panels_value = _parse_json_list_param(params.get("panels"))
    if panels_value is not None:
        panels = [dict(item) for item in panels_value if isinstance(item, dict)]
        if panels:
            clean_params["panels"] = panels

    for key in ("output_dir", "output_basename"):
        value = params.get(key)
        if isinstance(value, str) and value.strip():
            clean_params[key] = value.strip()

    formats_value = _parse_json_list_param(params.get("formats"))
    if formats_value is not None:
        formats = [str(item).strip() for item in formats_value if str(item).strip()]
        if formats:
            clean_params["formats"] = formats
    elif isinstance(params.get("formats"), str) and params.get("formats"):
        formats = [chunk.strip() for chunk in str(params["formats"]).split(",") if chunk.strip()]
        if formats:
            clean_params["formats"] = formats

    if params.get("dpi") is not None:
        try:
            clean_params["dpi"] = int(params["dpi"])
        except (TypeError, ValueError):
            pass

    publish_value = params.get("publish")
    if isinstance(publish_value, bool):
        clean_params["publish"] = publish_value
    elif isinstance(publish_value, str):
        clean_params["publish"] = publish_value.strip().lower() in {"1", "true", "yes", "on", "y"}

    tool_context = _ah()._build_chat_tool_context(agent, tool_name)
    if tool_context is not None:
        clean_params["tool_context"] = tool_context
    params = clean_params
    return params


def _normalize_deliverable_submit_params(
    agent: Any,
    action: LLMAction,
    tool_name: str,
    params: Dict[str, Any],
) -> Any:
    raw_artifacts = params.get("artifacts")
    if not isinstance(raw_artifacts, list):
        raw_artifacts = []
    publish_val = params.get("publish", True)
    if isinstance(publish_val, str):
        publish_val = publish_val.strip().lower() in {"1", "true", "yes", "on", "y"}
    params = {"publish": bool(publish_val), "artifacts": raw_artifacts}
    if isinstance(agent.session_id, str) and agent.session_id.strip():
        params["session_id"] = agent.session_id.strip()
    return params
