"""Action handler functions extracted from StructuredChatAgent.

Each handler corresponds to a specific action kind (tool, plan, task,
context_request, system, unknown) and operates on an ``agent`` instance
passed as the first argument.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import inspect
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from uuid import uuid4

from app.services.llm.structured_response import LLMAction, LLMStructuredResponse
from app.services.plans.plan_models import PlanNode, PlanTree
from app.services.plans.plan_session import PlanSession
from app.repository.plan_repository import PlanRepository
from app.repository.plan_storage import (
    append_action_log_entry,
    record_decomposition_job,
    update_decomposition_job_status,
)
from app.services.plans.decomposition_jobs import (
    get_current_job,
    log_job_event,
    plan_decomposition_jobs,
    reset_current_job,
    set_current_job,
    start_decomposition_job_thread,
    start_phagescope_track_job_thread,
)
from app.services.plans.plan_decomposer import DecompositionResult, PlanDecomposer
from app.services.plans.plan_executor import (
    PlanExecutor,
    PlanExecutorLLMService,
    ExecutionConfig,
)
from app.services.plans.task_verification import TaskVerificationService
from app.config import get_graph_rag_settings, get_search_settings
from app.config.tool_policy import get_tool_policy, is_tool_allowed
from app.config.decomposer_config import get_decomposer_settings
from app.config.executor_config import get_executor_settings
from app.services.foundation.settings import get_settings
from app.services.deliverables import get_deliverable_publisher
from app.services.upload_storage import delete_session_storage
from app.services.tool_output_storage import store_tool_output
from tool_box import execute_tool

from .models import AgentStep, AgentResult
from .session_helpers import (
    _lookup_phagescope_task_memory,
    _normalize_search_provider,
    _resolve_phagescope_taskid_alias,
    _record_phagescope_task_memory,
    _update_session_metadata,
)
from .tool_results import (
    sanitize_tool_result,
    summarize_tool_result,
    truncate_large_fields,
    drop_callables,
    normalize_dependencies,
    append_recent_tool_result,
)
from .background import (
    _classify_background_category,
    _BACKGROUND_TOOL_NAMES,
    _BACKGROUND_PLAN_OPS,
    _PHAGESCOPE_SYNC_ACTIONS,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Aliases matching the names used in the original StructuredChatAgent code
# ---------------------------------------------------------------------------
_sanitize_tool_result_fn = sanitize_tool_result
_summarize_tool_result_fn = summarize_tool_result
_drop_callables_fn = drop_callables
_normalize_dependencies_fn = normalize_dependencies
_append_recent_tool_result_fn = append_recent_tool_result
_BIO_TOOLS_NO_CLAUDE_FALLBACK_KEY = "bio_tools_no_claude_fallback"
_SEQUENCE_FETCH_NO_CLAUDE_FALLBACK_KEY = "sequence_fetch_no_claude_fallback"
_task_verifier = TaskVerificationService()


# ---------------------------------------------------------------------------
# maybe_synthesize_phagescope_saveall_analysis
# ---------------------------------------------------------------------------

def maybe_synthesize_phagescope_saveall_analysis(agent: Any, steps: List[AgentStep]) -> Optional[str]:
    """If the action sequence matches save_all + local reads, return a structured analysis string."""
    if not steps:
        return None

    save_step: Optional[AgentStep] = None
    for step in steps:
        if step.action.kind == "tool_operation" and step.action.name == "phagescope":
            params = (
                step.details.get("parameters")
                if isinstance(step.details, dict)
                else None
            )
            if isinstance(params, dict) and params.get("action") == "save_all":
                save_step = step
                break
    if not save_step or not isinstance(save_step.details, dict):
        return None

    save_result = save_step.details.get("result")
    if not isinstance(save_result, dict):
        return None

    # Detect the injected chain by presence of file_operations reads with metadata labels.
    reads: Dict[str, str] = {}
    for step in steps:
        if step.action.kind != "tool_operation" or step.action.name != "file_operations":
            continue
        label = step.action.metadata.get("label") if isinstance(step.action.metadata, dict) else None
        if not isinstance(label, str) or not label:
            continue
        result = step.details.get("result") if isinstance(step.details, dict) else None
        if isinstance(result, dict) and isinstance(result.get("content"), str):
            reads[label] = result["content"]

    if not reads:
        return None

    output_dir = save_result.get("output_directory") or save_result.get("output_directory_rel")
    status_code = save_result.get("status_code")
    missing = save_result.get("missing_artifacts") or []
    missing_text = ""
    if isinstance(missing, list) and missing:
        missing_text = f" (partial missing: {', '.join(str(x) for x in missing)})"

    # Parse key jsons (best-effort)
    phage_info = None
    quality = None
    try:
        if "phage_info" in reads:
            phage_info = json.loads(reads["phage_info"]).get("results")
    except Exception:
        phage_info = None
    try:
        if "quality" in reads:
            quality = json.loads(reads["quality"]).get("results")
    except Exception:
        quality = None

    # Extract host/lifestyle/taxonomy
    host = lifestyle = taxonomy = gc_content = length = genes = None
    if isinstance(phage_info, list) and phage_info:
        row = phage_info[0] if isinstance(phage_info[0], dict) else None
        if isinstance(row, dict):
            host = row.get("host")
            lifestyle = row.get("lifestyle")
            taxonomy = row.get("taxonomy")
            gc_content = row.get("gc_content")
            length = row.get("length")
            genes = row.get("genes")

    # Extract quality summary
    qsum = None
    if isinstance(quality, dict):
        q = quality.get("quality_summary")
        if isinstance(q, list) and q and isinstance(q[0], dict):
            qsum = q[0]

    # Proteins: count + top5 annotations
    protein_count = None
    top5 = []
    proteins_tsv = reads.get("proteins_tsv")
    if isinstance(proteins_tsv, str) and proteins_tsv.strip():
        lines = [ln for ln in proteins_tsv.splitlines() if ln.strip()]
        if len(lines) >= 2:
            protein_count = max(0, len(lines) - 1)
            header = lines[0].split("\t")
            idx = None
            for i, col in enumerate(header):
                if col.strip() in {"Protein_function_classification", "function", "annotation"}:
                    idx = i
                    break
            for ln in lines[1:6]:
                cols = ln.split("\t")
                if idx is not None and idx < len(cols):
                    top5.append(cols[idx].strip())
    # Fallback: parse proteins.json when TSV missing/empty
    if (protein_count is None or not top5) and isinstance(reads.get("proteins_json"), str):
        try:
            payload = json.loads(reads["proteins_json"])
            results = payload.get("results") if isinstance(payload, dict) else None
            if isinstance(results, list):
                if protein_count is None:
                    protein_count = len(results)
                if not top5:
                    for item in results[:5]:
                        if not isinstance(item, dict):
                            continue
                        val = (
                            item.get("Protein_function_classification")
                            or item.get("function")
                            or item.get("annotation")
                        )
                        if val is not None:
                            top5.append(str(val).strip())
        except Exception:
            pass
    # Fallback: if no tsv, try summary/task_detail or phage_info genes
    if protein_count is None:
        try:
            if isinstance(genes, str) and genes.isdigit():
                protein_count = int(genes)
        except Exception:
            protein_count = None

    lines: List[str] = []
    lines.append(f"Downloaded to: {output_dir}{missing_text}")
    if status_code == 207:
        lines.append("Note: status 207 (partial success). Core results are available and interpretation continues with available artifacts.")

    lines.append("")
    lines.append("## Structured Interpretation")
    if isinstance(qsum, dict):
        lines.append("- **Quality Metrics**:")
        lines.append(
            "  - contig_id={cid}, length={clen}, gene_count={gc}, checkv_quality={cq}, miuvig_quality={mq}, completeness={comp}, contamination={cont}".format(
                cid=qsum.get("contig_id"),
                clen=qsum.get("contig_length"),
                gc=qsum.get("gene_count"),
                cq=qsum.get("checkv_quality"),
                mq=qsum.get("miuvig_quality"),
                comp=qsum.get("completeness"),
                cont=qsum.get("contamination"),
            )
        )
    else:
        lines.append("- **Quality Metrics**: Could not read `metadata/quality.json`; check file existence and security-policy restrictions.")

    lines.append("- **Host / Lifestyle**:")
    if host or lifestyle or taxonomy:
        lines.append(f"  - host={host}, lifestyle={lifestyle}, taxonomy={taxonomy}")
        lines.append(f"  - length={length}, genes={genes}, gc_content={gc_content}")
    else:
        lines.append("  - Could not read `metadata/phage_info.json`, or it is empty.")

    lines.append("- **Protein Annotation (derived from annotation outputs)**:")
    if protein_count is not None:
        lines.append(f"  - Protein count: {protein_count}")
    else:
        lines.append("  - Protein count: unavailable (you can later read proteins.json/tsv).")
    if top5:
        lines.append("  - Top 5 annotations:")
        for i, item in enumerate(top5, 1):
            lines.append(f"    {i}. {item}")
    else:
        lines.append("  - Top 5 annotations: failed to extract from `annotation/proteins.tsv` (missing file or read error).")

    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# handle_tool_action
# ---------------------------------------------------------------------------

async def handle_tool_action(agent: Any, action: LLMAction) -> AgentStep:
    tool_name = (action.name or "").strip()
    if not tool_name:
        return AgentStep(
            action=action,
            success=False,
            message="Tool action is missing a name.",
            details={"error": "missing_tool_name"},
        )
    policy = get_tool_policy()
    if not is_tool_allowed(tool_name, policy):
        return AgentStep(
            action=action,
            success=False,
            message=f"Tool '{tool_name}' is not allowed by policy.",
            details={"error": "tool_not_allowed", "tool": tool_name},
        )

    params = dict(action.parameters or {})
    original_task: Optional[str] = None

    # 🔄 If LLM specified target_task_id, prioritize it for task-status updates.
    target_task_id = params.pop("target_task_id", None)
    if target_task_id is not None:
        try:
            agent.extra_context["current_task_id"] = int(target_task_id)
        except (TypeError, ValueError):
            pass

    if tool_name == "web_search":
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

    elif tool_name == "file_operations":
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
        if operation in {"read", "list", "delete", "exists", "info"}:
            path = params.get("path")
            if not isinstance(path, str) or not path.strip():
                return AgentStep(
                    action=action,
                    success=False,
                    message=f"file_operations {operation} requires a non-empty `path` string.",
                    details={"error": "missing_params", "tool": tool_name},
                )
            clean_params = {"operation": operation, "path": path}
            if operation == "list":
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

    elif tool_name == "graph_rag":
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

    elif tool_name == "literature_pipeline":
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

    elif tool_name == "review_pack_writer":
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

    elif tool_name == "sequence_fetch":
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

    elif tool_name == "claude_code":
        seq_block_payload = agent.extra_context.get(_SEQUENCE_FETCH_NO_CLAUDE_FALLBACK_KEY)
        if seq_block_payload:
            seq_root_reason = (
                str(seq_block_payload.get("summary") or "").strip()
                if isinstance(seq_block_payload, dict)
                else ""
            )
            seq_reason_text = (
                "claude_code fallback is blocked because sequence_fetch failed in input/download stage. "
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
                "claude_code fallback is blocked because bio_tools input preparation failed. "
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

        prepared = await agent._prepare_claude_code_params(
            action=action,
            tool_name=tool_name,
            params=params,
        )
        if isinstance(prepared, AgentStep):
            return prepared
        params, original_task = prepared

    elif tool_name == "document_reader":
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

    elif tool_name == "vision_reader":
        operation = params.get("operation")
        image_path = params.get("image_path")

        if not operation or not image_path:
            return AgentStep(
                action=action,
                success=False,
                message="vision_reader requires `operation` and `image_path`.",
                details={"error": "missing_params", "tool": tool_name},
            )

        page_number = params.get("page_number")
        region = params.get("region")
        question = params.get("question")
        language = params.get("language")

        clean_params: Dict[str, Any] = {
            "operation": operation,
            "image_path": image_path,
        }
        if isinstance(page_number, int):
            clean_params["page_number"] = page_number
        if isinstance(region, dict):
            clean_params["region"] = region
        if isinstance(question, str):
            clean_params["question"] = question
        if isinstance(language, str):
            clean_params["language"] = language

        params = clean_params

    elif tool_name == "paper_replication":
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

    elif tool_name == "generate_experiment_card":
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

    elif tool_name == "bio_tools":
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

    elif tool_name == "phagescope":
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
            resolved_taskid = _resolve_phagescope_taskid_alias(
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

    elif tool_name == "manuscript_writer":
        task_value = params.get("task")
        output_path = params.get("output_path")
        raw_action_params = action.parameters if isinstance(action.parameters, dict) else {}
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

        context_paths = params.get("context_paths") or []
        if isinstance(context_paths, str):
            context_paths = [context_paths]
        if not isinstance(context_paths, list):
            context_paths = []

        analysis_path = params.get("analysis_path")
        if analysis_path is not None and not isinstance(analysis_path, str):
            analysis_path = str(analysis_path)

        max_context_bytes = params.get("max_context_bytes")
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

        sections = params.get("sections")
        if sections is None:
            sections = raw_action_params.get("sections")
        if isinstance(sections, str):
            sections = [sections]
        if isinstance(sections, list):
            params["sections"] = sections

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

        if agent.session_id:
            params["session_id"] = agent.session_id

    elif tool_name == "deeppl":
        action_value = params.get("action", "help")
        if not isinstance(action_value, str) or not action_value.strip():
            return AgentStep(
                action=action,
                success=False,
                message="deeppl requires a non-empty `action` string.",
                details={"error": "missing_action", "tool": tool_name},
            )

        clean_params: Dict[str, Any] = {
            "action": action_value.strip(),
        }
        for key in (
            "input_file",
            "sequence_text",
            "sample_id",
            "execution_mode",
            "remote_profile",
            "model_path",
            "predict_script",
            "python_bin",
            "job_id",
            "session_id",
            "remote_host",
            "remote_user",
            "remote_runtime_dir",
            "remote_project_dir",
            "remote_predict_script",
            "remote_python_bin",
            "remote_password",
            "remote_ssh_key_path",
        ):
            value = params.get(key)
            if isinstance(value, str) and value.strip():
                clean_params[key] = value.strip()

        if "remote_port" in params and params.get("remote_port") is not None:
            try:
                clean_params["remote_port"] = int(params.get("remote_port"))
            except (TypeError, ValueError):
                pass

        if "timeout" in params and params.get("timeout") is not None:
            try:
                clean_params["timeout"] = int(params.get("timeout"))
            except (TypeError, ValueError):
                pass

        if "background" in params:
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
            clean_params.setdefault("session_id", agent.session_id.strip())

        params = clean_params

    elif tool_name == "terminal_session":
        operation_value = params.get("operation", "")
        if not isinstance(operation_value, str) or not operation_value.strip():
            return AgentStep(
                action=action,
                success=False,
                message="terminal_session requires a non-empty `operation` string.",
                details={"error": "missing_operation", "tool": tool_name},
            )
        clean_params: Dict[str, Any] = {"operation": operation_value.strip().lower()}
        for key in ("terminal_id", "data", "encoding", "mode", "approval_id"):
            value = params.get(key)
            if isinstance(value, str) and value.strip():
                clean_params[key] = value if key == "data" else value.strip()
        for int_key in ("cols", "rows", "limit"):
            if int_key in params and params.get(int_key) is not None:
                try:
                    clean_params[int_key] = int(params[int_key])
                except (TypeError, ValueError):
                    pass
        session_id_value = params.get("session_id")
        if isinstance(session_id_value, str) and session_id_value.strip():
            clean_params["session_id"] = session_id_value.strip()
        elif isinstance(agent.session_id, str) and agent.session_id.strip():
            clean_params["session_id"] = agent.session_id.strip()

        # Auto-ensure: if write needs terminal_id but none provided, call ensure first
        op = clean_params["operation"]
        needs_tid = op in ("write", "resize")
        has_tid = bool(clean_params.get("terminal_id"))
        if needs_tid and not has_tid:
            ensure_sid = clean_params.get("session_id") or (
                agent.session_id if isinstance(agent.session_id, str) and agent.session_id.strip() else None
            )
            if not ensure_sid:
                return AgentStep(
                    action=action,
                    success=False,
                    message="terminal_session write requires a session_id to auto-create a terminal.",
                    details={"error": "missing_session_id", "tool": tool_name},
                )
            try:
                ensure_result = await execute_tool(
                    "terminal_session", operation="ensure", session_id=ensure_sid
                )
                if isinstance(ensure_result, dict) and ensure_result.get("terminal_id"):
                    clean_params["terminal_id"] = ensure_result["terminal_id"]
            except Exception:
                pass  # let downstream report the missing terminal_id error

        params = clean_params

    else:
        return AgentStep(
            action=action,
            success=False,
            message=f"Tool {tool_name} is not supported yet.",
            details={"error": "unsupported_tool", "tool": tool_name},
        )

    try:
        # PhageScope: provide elegant progress during wait/poll (job_update -> stats.tool_progress)
        if tool_name == "phagescope":
            action_value = str(params.get("action") or "").strip().lower()
            wait_value = params.get("wait") is True
            taskid_value = params.get("taskid")
            if wait_value and action_value in {"result", "quality"} and taskid_value:
                import time as _time
                import json as _json

                def _extract_task_status(detail_result: Any) -> str:
                    if not isinstance(detail_result, dict):
                        return "unknown"
                    payload = detail_result.get("data")
                    if isinstance(payload, dict):
                        results = payload.get("results")
                        if isinstance(results, dict):
                            for k in ("status", "task_status", "state", "taskstatus"):
                                v = results.get(k)
                                if isinstance(v, str) and v.strip():
                                    return v.strip()
                    return "unknown"

                def _extract_task_detail_dict(detail_result: Any) -> Optional[Dict[str, Any]]:
                    if not isinstance(detail_result, dict):
                        return None
                    payload = detail_result.get("data")
                    if not isinstance(payload, dict):
                        return None
                    # phagescope_handler attaches parsed_task_detail when possible
                    parsed = payload.get("parsed_task_detail")
                    if isinstance(parsed, dict):
                        return parsed
                    # sometimes nested under results.task_detail
                    results = payload.get("results")
                    if isinstance(results, dict):
                        td = results.get("task_detail")
                        if isinstance(td, dict):
                            return td
                        if isinstance(td, str) and td.strip():
                            try:
                                parsed_td = _json.loads(td)
                                if isinstance(parsed_td, dict):
                                    return parsed_td
                            except Exception:
                                return None
                    return None

                def _module_status_upper(value: Any) -> Optional[str]:
                    if not isinstance(value, str):
                        return None
                    v = value.strip()
                    return v.upper() if v else None

                poll_timeout = float(params.get("poll_timeout") or 120.0)
                poll_interval = float(params.get("poll_interval") or 2.0)
                start = _time.monotonic()

                # Avoid the tool's internal polling; we do it here so we can stream progress
                attempt_params = dict(params)
                attempt_params["wait"] = False

                raw_result = None
                last_status = "queued"
                while True:
                    elapsed = _time.monotonic() - start
                    denom = poll_timeout if poll_timeout > 0 else 1.0
                    time_percent = int(max(0.0, min(1.0, elapsed / denom)) * 100)

                    # best-effort task status
                    modules_payload: Optional[List[Dict[str, Any]]] = None
                    counts_payload: Optional[Dict[str, int]] = None
                    try:
                        detail = await execute_tool(
                            "phagescope",
                            action="task_detail",
                            taskid=str(taskid_value),
                            base_url=params.get("base_url"),
                            token=params.get("token"),
                            timeout=min(float(params.get("timeout") or 60.0), 40.0),
                        )
                        last_status = _extract_task_status(detail)
                        task_detail = _extract_task_detail_dict(detail)
                        if isinstance(task_detail, dict):
                            queue = task_detail.get("task_que")
                            if isinstance(queue, list) and queue:
                                modules: List[Dict[str, Any]] = []
                                done = 0
                                total = 0
                                for item in queue:
                                    if not isinstance(item, dict):
                                        continue
                                    name = item.get("module")
                                    if not isinstance(name, str) or not name.strip():
                                        continue
                                    status_raw = (
                                        item.get("module_satus")
                                        or item.get("module_status")
                                        or item.get("status")
                                    )
                                    status_upper = _module_status_upper(status_raw) or "UNKNOWN"
                                    is_done: Optional[bool] = None
                                    if status_upper in {"COMPLETED", "SUCCESS", "SUCCEEDED", "DONE", "FINISHED"}:
                                        is_done = True
                                    elif status_upper in {"FAILED", "ERROR"}:
                                        is_done = False
                                    modules.append(
                                        {
                                            "name": name.strip(),
                                            "status": str(status_raw) if status_raw is not None else status_upper,
                                            "done": is_done,
                                        }
                                    )
                                    total += 1
                                    if is_done is True:
                                        done += 1
                                if total > 0:
                                    modules_payload = modules
                                    counts_payload = {"done": done, "total": total}
                    except Exception:
                        # keep last_status
                        pass

                    # Prefer module-based percent when available; otherwise fallback to time-based percent.
                    percent = time_percent
                    if counts_payload and counts_payload.get("total"):
                        percent = int(round((counts_payload["done"] / max(1, counts_payload["total"])) * 100))
                        percent = max(0, min(100, percent))

                    plan_decomposition_jobs.update_stats_from_context(
                        {
                            "tool_progress": {
                                "tool": "phagescope",
                                "taskid": str(taskid_value),
                                "percent": percent,
                                "status": last_status,
                                "phase": "poll",
                                **({"modules": modules_payload} if modules_payload is not None else {}),
                                **({"counts": counts_payload} if counts_payload is not None else {}),
                            }
                        }
                    )

                    # try fetch result
                    raw_result = await execute_tool(tool_name, **attempt_params)
                    if isinstance(raw_result, dict) and raw_result.get("success") is True:
                        plan_decomposition_jobs.update_stats_from_context(
                            {
                                "tool_progress": {
                                    "tool": "phagescope",
                                    "taskid": str(taskid_value),
                                    "percent": 100,
                                    "status": last_status or "Success",
                                    "phase": "done",
                                }
                            }
                        )
                        break

                    upper = str(last_status or "").strip().upper()
                    if upper in {"FAILED", "ERROR"}:
                        break
                    if elapsed >= poll_timeout:
                        raw_result = {
                            "success": False,
                            "status_code": 408,
                            "action": action_value,
                            "taskid": str(taskid_value),
                            "error": f"Result not ready within {poll_timeout:.0f}s. Retry later with taskid={taskid_value}.",
                            "polling": {
                                "waited": True,
                                "poll_timeout": poll_timeout,
                                "poll_interval": poll_interval,
                            },
                        }
                        break
                    await asyncio.sleep(max(0.2, poll_interval))
            else:
                raw_result = await execute_tool(tool_name, **params)
        else:
            raw_result = await execute_tool(tool_name, **params)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception(
            "Tool %s execution failed for session %s: %s",
            tool_name,
            agent.session_id,
            exc,
        )
        return AgentStep(
            action=action,
            success=False,
            message=f"{tool_name} failed: {exc}",
            details={"error": str(exc), "tool": tool_name},
        )

    sanitized = _sanitize_tool_result_fn(tool_name, raw_result)
    # For optional local file reads, keep failure semantics but annotate the
    # result so callers can decide whether to continue.
    try:
        is_optional = (
            isinstance(action.metadata, dict) and bool(action.metadata.get("optional"))
        )
        if (
            tool_name == "file_operations"
            and is_optional
            and isinstance(params, dict)
            and params.get("operation") == "read"
            and isinstance(sanitized, dict)
            and sanitized.get("success") is False
        ):
            patched = dict(sanitized)
            patched["optional"] = True
            patched["optional_error"] = patched.get("error") or "read_failed"
            sanitized = patched
    except Exception:
        pass

    if tool_name == "sequence_fetch":
        if (
            isinstance(raw_result, dict)
            and raw_result.get("success") is False
            and raw_result.get("no_claude_fallback") is True
        ):
            blocked_summary = str(
                raw_result.get("error")
                or "sequence_fetch failed and claude_code fallback is blocked."
            ).strip()
            agent.extra_context[_SEQUENCE_FETCH_NO_CLAUDE_FALLBACK_KEY] = {
                "summary": blocked_summary,
                "blocked_reason": "sequence_fetch_failed_no_fallback",
                "error_code": raw_result.get("error_code"),
                "error_stage": raw_result.get("error_stage"),
                "accessions": raw_result.get("accessions"),
                "provider": raw_result.get("provider"),
            }
        elif sanitized.get("success") is not False:
            agent.extra_context.pop(_SEQUENCE_FETCH_NO_CLAUDE_FALLBACK_KEY, None)

    if tool_name == "bio_tools":
        if (
            isinstance(raw_result, dict)
            and raw_result.get("success") is False
            and raw_result.get("no_claude_fallback") is True
        ):
            blocked_summary = str(
                raw_result.get("error")
                or "bio_tools input preparation failed; claude_code fallback is blocked."
            ).strip()
            agent.extra_context[_BIO_TOOLS_NO_CLAUDE_FALLBACK_KEY] = {
                "summary": blocked_summary,
                "blocked_reason": "bio_tools_input_preparation_failed",
                "error_code": raw_result.get("error_code"),
                "error_stage": raw_result.get("error_stage"),
                "tool_name": raw_result.get("tool"),
                "operation": raw_result.get("operation"),
            }
        elif sanitized.get("success") is not False:
            agent.extra_context.pop(_BIO_TOOLS_NO_CLAUDE_FALLBACK_KEY, None)

    summary = _summarize_tool_result_fn(tool_name, sanitized)
    _append_recent_tool_result_fn(agent.extra_context, tool_name, summary, sanitized)

    storage_info = None
    if agent.session_id:
        action_payload = {
            "kind": action.kind,
            "name": action.name,
            "order": action.order,
            "blocking": action.blocking,
            "parameters": _drop_callables_fn(params),
        }
        try:
            storage_info = store_tool_output(
                session_id=agent.session_id,
                job_id=get_current_job(),
                action=action_payload,
                tool_name=tool_name,
                raw_result=raw_result,
                summary=summary,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "Failed to store tool output for %s in session %s: %s",
                tool_name,
                agent.session_id,
                exc,
            )

    # Attach stored output paths back to agent/tool result (no manual copy needed)
    if storage_info is not None and agent.session_id:
        try:
            from app.services.upload_storage import ensure_session_dir
            from pathlib import Path

            session_root = ensure_session_dir(agent.session_id)

            def _abs(rel: Optional[str]) -> Optional[str]:
                if not rel:
                    return None
                try:
                    return str((session_root / Path(rel)).resolve())
                except Exception:
                    return str(session_root / Path(rel))

            storage_payload: Dict[str, Any] = {
                "session_id": agent.session_id,
                "job_id": get_current_job(),
                "tool": tool_name,
                "step_order": action.order,
                "output_dir": _abs(getattr(storage_info, "output_dir", None)),
                "result_path": _abs(getattr(storage_info, "result_path", None)),
                "manifest_path": _abs(getattr(storage_info, "manifest_path", None)),
                "preview_path": _abs(getattr(storage_info, "preview_path", None)),
            }
            # Also keep relative paths for portability (optional)
            storage_payload_rel: Dict[str, Any] = {
                "output_dir": getattr(storage_info, "output_dir", None),
                "result_path": getattr(storage_info, "result_path", None),
                "manifest_path": getattr(storage_info, "manifest_path", None),
                "preview_path": getattr(storage_info, "preview_path", None),
            }
            storage_payload["relative"] = storage_payload_rel

            if isinstance(raw_result, dict):
                raw_result.setdefault("storage", storage_payload)
            if isinstance(sanitized, dict):
                sanitized.setdefault("storage", storage_payload)

            # Persist latest output location for later retrieval
            def _updater(metadata: Dict[str, Any]) -> Dict[str, Any]:
                metadata["phagescope_last_output"] = storage_payload
                items = metadata.get("phagescope_recent_outputs")
                if not isinstance(items, list):
                    items = []
                # de-dup by result_path
                rp = storage_payload.get("result_path")
                items = [it for it in items if not (isinstance(it, dict) and it.get("result_path") == rp)]
                items.insert(0, storage_payload)
                metadata["phagescope_recent_outputs"] = items[:10]
                return metadata

            if tool_name == "phagescope":
                _update_session_metadata(agent.session_id, _updater)
                # Make it available to the current agent loop immediately
                agent.extra_context["phagescope_last_output"] = storage_payload
        except Exception as exc:  # pragma: no cover - best-effort
            logger.debug("Failed to attach phagescope storage paths: %s", exc)

    if tool_name == "phagescope" and agent.session_id:
        action_value = params.get("action")
        if action_value == "submit" and sanitized.get("success"):
            try:
                _record_phagescope_task_memory(agent.session_id, params, sanitized)
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug(
                    "Failed to record phagescope task memory for %s: %s",
                    agent.session_id,
                    exc,
                )

    success = sanitized.get("success", True)
    deliverable_report = None
    if agent.session_id:
        publish_task_id: Optional[int] = None
        publish_task_name: Optional[str] = None
        publish_task_instruction: Optional[str] = None
        try:
            current_task_id = agent.extra_context.get("current_task_id")
            if current_task_id is not None:
                publish_task_id = int(current_task_id)
        except (TypeError, ValueError):
            publish_task_id = None

        if publish_task_id is not None and agent.plan_session.plan_id is not None:
            try:
                tree = agent.plan_session.repo.get_plan_tree(agent.plan_session.plan_id)
                if tree.has_node(publish_task_id):
                    task_node = tree.get_node(publish_task_id)
                    publish_task_name = task_node.display_name()
                    publish_task_instruction = task_node.instruction
            except Exception as exc:  # pragma: no cover - best-effort
                logger.debug(
                    "Unable to resolve task context for deliverable publish in session %s: %s",
                    agent.session_id,
                    exc,
                )

        try:
            publish_payload = _drop_callables_fn(raw_result)
            deliverable_report = get_deliverable_publisher().publish_from_tool_result(
                session_id=agent.session_id,
                tool_name=tool_name,
                raw_result=publish_payload,
                summary=summary,
                source={
                    "channel": "chat",
                    "action_kind": action.kind,
                    "action_name": action.name,
                    "step_order": action.order,
                },
                job_id=get_current_job(),
                plan_id=agent.plan_session.plan_id,
                task_id=publish_task_id,
                task_name=publish_task_name,
                task_instruction=publish_task_instruction,
                publish_status="final" if success is not False else "draft",
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "Failed to publish deliverables for session %s tool %s: %s",
                agent.session_id,
                tool_name,
                exc,
            )

    if success is False:
        message = summary or f"{tool_name} failed to execute."
    else:
        message = summary or f"{tool_name} finished execution."

    # 💾 A-mem integration: save execution result asynchronously without blocking.
    if tool_name == "claude_code":
        try:
            from app.services.amem_client import get_amem_client
            amem_client = get_amem_client()

            if amem_client.enabled:
                # Save to A-mem asynchronously.
                asyncio.create_task(
                    amem_client.save_execution(
                        task=original_task,  # Use original task description.
                        result=sanitized,
                        session_id=agent.session_id,
                        plan_id=agent.plan_session.plan_id,
                        key_findings=summary  # Use summary as key findings.
                    )
                )
                logger.info("[AMEM] Scheduled execution result save")
        except Exception as amem_err:
            logger.warning(f"[AMEM] Failed to schedule save: {amem_err}")
            # Do not affect main flow.

    agent._sync_task_status_after_tool_execution(
        tool_name=tool_name,
        success=success,
        summary=summary,
        message=message,
        params=params,
        result=sanitized,
        extra_metadata={
            "storage": storage_info.__dict__ if storage_info else None,
            "deliverables": deliverable_report.to_dict() if deliverable_report else None,
        },
    )

    return AgentStep(
        action=action,
        success=bool(success),
        message=message,
        details={
            "tool": tool_name,
            "parameters": _drop_callables_fn(params),
            "result": sanitized,
            "summary": summary,
            "storage": storage_info.__dict__ if storage_info else None,
            "deliverables": deliverable_report.to_dict() if deliverable_report else None,
        },
    )


# ---------------------------------------------------------------------------
# handle_plan_action
# ---------------------------------------------------------------------------

async def handle_plan_action(agent: Any, action: LLMAction) -> AgentStep:
    params = action.parameters or {}
    if action.name == "create_plan":
        title = params.get("title")
        goal = params.get("goal")
        if not title:
            if isinstance(goal, str) and goal.strip():
                title = goal.strip()[:80]
            else:
                title = f"Plan-{agent.conversation_id or 'new'}"
        description = params.get("description")
        owner = params.get("owner")
        metadata = params.get("metadata")
        if metadata is None:
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        # Ensure plan origin is recorded for later comparison (standard vs deepthink).
        metadata.setdefault("plan_origin", "standard")
        metadata.setdefault("created_by", "structured_agent")
        # First create an empty plan record
        new_tree = agent.plan_session.repo.create_plan(
            title=title,
            owner=owner,
            description=description,
            metadata=metadata,
        )

        # Create a ROOT task first - enforce a single root node for the plan
        raw_tasks = params.get("tasks")
        root_node = agent.plan_session.repo.create_task(
            new_tree.id,
            name=title,
            status="pending",
            instruction=description or f"Root task for plan: {title}",
            parent_id=None,  # ROOT has no parent
            metadata={"is_root": True, "task_type": "root"},
        )
        root_task_id: Optional[int] = root_node.id
        logger.info("Created ROOT task %s for plan %s", root_task_id, new_tree.id)

        # If the caller provided an explicit task list, materialize it immediately
        created_seed_tasks: List[Any] = []
        if isinstance(raw_tasks, list) and raw_tasks:
            for idx, t in enumerate(raw_tasks):
                if not isinstance(t, dict):
                    continue
                instr_raw = t.get("instruction") or t.get("description") or ""
                try:
                    instruction = str(instr_raw).strip()
                except Exception:
                    instruction = ""
                name_raw = t.get("name") or t.get("title")
                name: str
                if isinstance(name_raw, str) and name_raw.strip():
                    name = name_raw.strip()
                else:
                    # Derive a short step name from the instruction when no explicit name is given
                    base = instruction.strip()
                    if "。" in base:
                        base = base.split("。", 1)[0]
                    elif "." in base:
                        base = base.split(".", 1)[0]
                    elif "\n" in base:
                        base = base.split("\n", 1)[0]
                    base = base[:40] if base else f"Step {idx + 1}"
                    name = f"Step {idx + 1}: {base}" if base else f"Step {idx + 1}"

                status_raw = t.get("status") or "pending"
                if not isinstance(status_raw, str):
                    status = str(status_raw).strip() or "pending"
                else:
                    status = status_raw.strip() or "pending"

                parent_id_raw = t.get("parent_id")
                # Default to ROOT task if no parent_id is specified
                parent_id = root_task_id
                if parent_id_raw is not None:
                    try:
                        parent_id = int(parent_id_raw)
                    except (TypeError, ValueError):
                        parent_id = root_task_id  # Fall back to ROOT if invalid
                # Enforce single root: if resolved parent_id is None, force to root_task_id
                if parent_id is None:
                    parent_id = root_task_id

                deps_raw = t.get("dependencies")
                deps: Optional[List[int]] = None
                if isinstance(deps_raw, list):
                    tmp: List[int] = []
                    for d in deps_raw:
                        try:
                            tmp.append(int(d))
                        except (TypeError, ValueError):
                            continue
                    deps = tmp or None

                meta_raw = t.get("metadata")
                meta = meta_raw if isinstance(meta_raw, dict) else None

                try:
                    node = agent.plan_session.repo.create_task(
                        new_tree.id,
                        name=name,
                        status=status,
                        instruction=instruction or None,
                        parent_id=parent_id,
                        metadata=meta,
                        dependencies=deps,
                    )
                    created_seed_tasks.append(node)
                except Exception as exc:  # pragma: no cover - defensive
                    logger.warning(
                        "Failed to create seed task for plan %s: %s", new_tree.id, exc
                    )

        # Bind session to the new plan and refresh the in-memory tree so that
        # any seed tasks are immediately visible to the caller and UI.
        agent.plan_session.bind(new_tree.id)
        agent._refresh_plan_tree(force_reload=True)
        effective_tree = agent.plan_tree or new_tree
        agent.plan_tree = effective_tree
        agent.extra_context["plan_id"] = effective_tree.id
        message = f'Created and bound new plan #{effective_tree.id} "{effective_tree.title}".'
        if created_seed_tasks:
            message += f" Seeded with {len(created_seed_tasks)} top-level task(s) from the proposed plan."
        details = {
            "plan_id": effective_tree.id,
            "title": effective_tree.title,
            "task_count": effective_tree.node_count(),
        }
        if created_seed_tasks:
            details["seed_tasks"] = [node.model_dump() for node in created_seed_tasks]
        agent._dirty = True

        decomposition_info = agent._auto_decompose_plan(new_tree.id)
        if decomposition_info:
            job = decomposition_info.get("job")
            summary = decomposition_info.get("result")
            if job is not None:
                job_payload = job.to_payload()
                details["decomposition_job"] = job_payload
                details["target_task_name"] = new_tree.title
                message += " Automatic decomposition has been submitted for background execution."
            elif summary is not None:
                created_count = len(summary.created_tasks)
                details["decomposition"] = {
                    "created": [
                        node.model_dump() for node in summary.created_tasks
                    ],
                    "failed_nodes": summary.failed_nodes,
                    "stopped_reason": summary.stopped_reason,
                    "stats": summary.stats,
                }
                if created_count:
                    message += f" Automatic decomposition produced {created_count} tasks."
                else:
                    message += " Automatic decomposition finished without creating new tasks."
        elif agent._decomposition_notes:
            details["decomposition_notes"] = list(agent._decomposition_notes)

        return AgentStep(
            action=action, success=True, message=message, details=details
        )

    if action.name == "list_plans":
        plans = agent.plan_session.list_plans()
        details = {"plans": [plan.model_dump() for plan in plans]}
        message = "Available plans have been listed." if plans else "No plans are currently available."
        return AgentStep(
            action=action, success=True, message=message, details=details
        )

    if action.name == "execute_plan":
        tree = agent._require_plan_bound()
        if agent.plan_executor is None:
            raise ValueError("Plan executor is not enabled in this environment.")
        paper_mode_raw = params.get("paper_mode")
        paper_mode = False
        if isinstance(paper_mode_raw, bool):
            paper_mode = paper_mode_raw
        elif paper_mode_raw is not None:
            paper_mode = str(paper_mode_raw).strip().lower() in {"1", "true", "yes", "on", "y"}
        # Build session context and pass to plan executor.
        session_ctx = {
            "session_id": agent.session_id,  # For tool calls.
            "user_message": agent._current_user_message if hasattr(agent, "_current_user_message") else None,
            "chat_history": agent.history,
            "recent_tool_results": agent.extra_context.get("recent_tool_results", []),
            "paper_mode": paper_mode,
        }
        exec_config = ExecutionConfig(session_context=session_ctx, paper_mode=paper_mode)
        summary = await asyncio.to_thread(agent.plan_executor.execute_plan, tree.id, config=exec_config)
        executed_count = len(summary.executed_task_ids)
        failed_count = len(summary.failed_task_ids)
        skipped_count = len(summary.skipped_task_ids)
        parts = [f"Plan #{tree.id} finished execution"]
        parts.append(f"Succeeded tasks: {executed_count}")
        if failed_count:
            parts.append(f"Failed tasks: {failed_count}")
        if skipped_count:
            parts.append(f"Skipped tasks: {skipped_count}")
        message = "，".join(parts) + "。"
        details = summary.to_dict()
        success = failed_count == 0 and skipped_count == 0
        agent._refresh_plan_tree(force_reload=True)
        return AgentStep(
            action=action, success=success, message=message, details=details
        )

    if action.name == "delete_plan":
        plan_id_param = params.get("plan_id") or agent.plan_session.plan_id
        plan_id = agent._coerce_int(plan_id_param, "plan_id")
        agent.plan_session.repo.delete_plan(plan_id)
        detached = False
        if agent.plan_session.plan_id == plan_id:
            agent.plan_session.detach()
            agent.plan_tree = None
            agent.extra_context.pop("plan_id", None)
            detached = True
        agent._dirty = False
        message = f"Plan #{plan_id} has been deleted."
        details = {"plan_id": plan_id, "detached": detached}
        return AgentStep(
            action=action, success=True, message=message, details=details
        )

    if action.name == "review_plan":
        tree = agent._require_plan_bound()
        plan_id = tree.id
        from app.services.plans.plan_rubric_evaluator import (
            evaluate_plan_rubric,
            is_rubric_evaluation_unavailable,
        )
        try:
            rubric_result = await asyncio.to_thread(
                evaluate_plan_rubric,
                tree,
                evaluator_provider="qwen",
                evaluator_model="qwen3.5-plus",
            )
        except Exception as exc:
            logger.warning("review_plan rubric evaluation failed: %s", exc)
            return AgentStep(
                action=action, success=False,
                message=f"Rubric evaluation failed: {exc}",
                details={"plan_id": plan_id},
            )
        # Persist evaluation into plan metadata
        merged_meta = dict(getattr(tree, "metadata", None) or {})
        merged_meta["plan_evaluation"] = rubric_result.to_dict()
        try:
            agent.plan_session.repo.update_plan_metadata(plan_id, merged_meta)
        except Exception as meta_exc:
            logger.warning("Failed to persist plan rubric evaluation: %s", meta_exc)
        agent._refresh_plan_tree(force_reload=True)
        rubric_unavailable = is_rubric_evaluation_unavailable(rubric_result)
        message = (
            f"Plan #{plan_id} review unavailable. Rubric evaluator could not complete."
            if rubric_unavailable
            else (
                f"Plan #{plan_id} review complete. "
                f"Rubric score: {rubric_result.overall_score}/100."
            )
        )
        details = {
            "plan_id": plan_id,
            "plan_title": tree.title,
            "status": "evaluation_unavailable" if rubric_unavailable else "completed",
            "rubric_score": rubric_result.overall_score,
            "rubric_dimension_scores": rubric_result.dimension_scores,
            "rubric_subcriteria_scores": rubric_result.subcriteria_scores,
            "rubric_feedback": rubric_result.feedback,
            "rubric_evaluator": {
                "provider": rubric_result.evaluator_provider,
                "model": rubric_result.evaluator_model,
                "rubric_version": rubric_result.rubric_version,
                "evaluated_at": rubric_result.evaluated_at,
            },
            "degraded": rubric_unavailable,
        }
        return AgentStep(
            action=action, success=not rubric_unavailable, message=message, details=details
        )

    if action.name == "optimize_plan":
        tree = agent._require_plan_bound()
        plan_id = tree.id
        changes = params.get("changes")
        if not changes or not isinstance(changes, list):
            return AgentStep(
                action=action, success=False,
                message="optimize_plan requires a non-empty `changes` list.",
                details={"plan_id": plan_id},
            )

        repo = agent.plan_session.repo
        try:
            applied = repo.apply_changes_atomically(plan_id, changes)
        except Exception as exc:
            agent._refresh_plan_tree(force_reload=True)
            return AgentStep(
                action=action,
                success=False,
                message=f"Plan #{plan_id} optimization failed: {exc}",
                details={
                    "plan_id": plan_id,
                    "applied_changes": 0,
                    "failed_changes": len(changes),
                    "changes_detail": {
                        "applied": [],
                        "failed": [{"error": str(exc)}],
                    },
                },
            )

        agent._refresh_plan_tree(force_reload=True)
        message = f"Plan #{plan_id} optimized: {len(applied)} changes applied."
        details = {
            "plan_id": plan_id,
            "applied_changes": len(applied),
            "failed_changes": 0,
            "changes_detail": {"applied": applied, "failed": []},
        }
        return AgentStep(
            action=action,
            success=True,
            message=message,
            details=details,
        )

    return handle_unknown_action(agent, action)


# ---------------------------------------------------------------------------
# handle_task_action
# ---------------------------------------------------------------------------

def handle_task_action(agent: Any, action: LLMAction) -> AgentStep:
    params = action.parameters or {}
    tree = agent._require_plan_bound()

    if action.name == "create_task":
        name = params.get("task_name") or params.get("name") or params.get("title")
        if not name:
            raise ValueError("create_task requires a task_name.")
        instruction = params.get("instruction")
        parent_id = params.get("parent_id")
        if parent_id is not None:
            parent_id = agent._coerce_int(parent_id, "parent_id")
        metadata = (
            params.get("metadata")
            if isinstance(params.get("metadata"), dict)
            else None
        )
        dependencies = _normalize_dependencies_fn(params.get("dependencies"))

        raw_anchor_task_id = params.get("anchor_task_id")
        anchor_task_id = None
        if raw_anchor_task_id is not None:
            anchor_task_id = agent._coerce_int(raw_anchor_task_id, "anchor_task_id")

        anchor_position = params.get("anchor_position")
        if anchor_position is not None and not isinstance(anchor_position, str):
            raise ValueError("anchor_position must be a string.")
        if isinstance(anchor_position, str):
            anchor_position = anchor_position.strip()
            anchor_position = anchor_position.lower() if anchor_position else None

        position_param = params.get("position")
        position: Optional[int] = None
        if position_param is not None:
            if isinstance(position_param, str):
                position_str = position_param.strip()
                if position_str:
                    parts = position_str.split(":", 1)
                    keyword = parts[0].strip().lower()
                    if keyword in {"before", "after"}:
                        if len(parts) < 2 or not parts[1].strip():
                            # Support shorthand "before"/"after":
                            # - If anchor_task_id is provided separately, treat as relative to it.
                            # - Otherwise, map to inserting as first/last child.
                            derived_position = keyword
                            if anchor_task_id is None:
                                derived_position = (
                                    "first_child" if keyword == "before" else "last_child"
                                )
                            if anchor_position is not None and anchor_position != derived_position:
                                raise ValueError(
                                    "anchor_position does not match the pattern specified in position."
                                )
                            anchor_position = derived_position
                        else:
                            candidate_id = agent._coerce_int(parts[1].strip(), f"position {keyword}")
                            if anchor_task_id is not None and anchor_task_id != candidate_id:
                                raise ValueError(
                                    "anchor_task_id does not match the task referenced in position."
                                )
                            if anchor_position is not None and anchor_position != keyword:
                                raise ValueError(
                                    "anchor_position does not match the pattern specified in position."
                                )
                            anchor_task_id = candidate_id
                            anchor_position = keyword
                    elif keyword in {"first_child", "last_child"}:
                        if anchor_position is not None and anchor_position != keyword:
                            raise ValueError(
                                "anchor_position does not match the pattern specified in position."
                            )
                        anchor_position = keyword
                    else:
                        position = agent._coerce_int(position_param, "position")
                else:
                    position = None
            else:
                position = agent._coerce_int(position_param, "position")

        if position is not None and position < 0:
            raise ValueError("position cannot be negative.")

        insert_before_val = params.get("insert_before")
        insert_after_val = params.get("insert_after")
        insert_before_id = (
            agent._coerce_int(insert_before_val, "insert_before")
            if insert_before_val is not None
            else None
        )
        insert_after_id = (
            agent._coerce_int(insert_after_val, "insert_after")
            if insert_after_val is not None
            else None
        )

        siblings_parent_key = parent_id if parent_id is not None else None
        siblings = tree.children_ids(siblings_parent_key)

        if insert_before_id is not None and insert_after_id is not None:
            if insert_before_id == insert_after_id:
                raise ValueError("insert_before and insert_after cannot point to the same task.")
            if insert_after_id not in siblings or insert_before_id not in siblings:
                raise ValueError("insert_before / The task referenced by insert_after does not belong to the target parent node.")
            after_idx = siblings.index(insert_after_id)
            before_idx = siblings.index(insert_before_id)
            if after_idx > before_idx:
                raise ValueError("insert_after must appear before insert_before.")
            if anchor_task_id is not None and anchor_task_id not in {
                insert_after_id,
                insert_before_id,
            }:
                raise ValueError("anchor_task_id is inconsistent with insert_before/insert_after.")
            anchor_task_id = insert_after_id
            anchor_position = "after"
        else:
            if insert_before_id is not None:
                if anchor_task_id is not None and anchor_task_id != insert_before_id:
                    raise ValueError("anchor_task_id points to a different task than insert_before.")
                if insert_before_id not in siblings:
                    raise ValueError("The task referenced by insert_before does not belong to the target parent node.")
                anchor_task_id = insert_before_id
                anchor_position = "before"
            if insert_after_id is not None:
                if anchor_task_id is not None and anchor_task_id != insert_after_id:
                    raise ValueError("anchor_task_id points to a different task than insert_after.")
                if insert_after_id not in siblings:
                    raise ValueError("The task referenced by insert_after does not belong to the target parent node.")
                anchor_task_id = insert_after_id
                anchor_position = "after"
        if anchor_position is not None:
            valid_anchor_positions = {
                "before",
                "after",
                "first_child",
                "last_child",
            }
            if anchor_position not in valid_anchor_positions:
                raise ValueError(
                    f"Invalid anchor_position; only {', '.join(sorted(valid_anchor_positions))} are supported."
                )
        node = agent.plan_session.repo.create_task(
            tree.id,
            name=name,
            instruction=instruction,
            parent_id=parent_id,
            metadata=metadata,
            dependencies=dependencies,
            position=position,
            anchor_task_id=anchor_task_id,
            anchor_position=anchor_position,
        )
        agent._refresh_plan_tree()
        message = f"Created task [{node.id}] {node.name}."
        details = {"task": node.model_dump()}
        agent._dirty = True
        return AgentStep(
            action=action, success=True, message=message, details=details
        )

    if action.name == "update_task":
        task_id = agent._coerce_int(params.get("task_id"), "task_id")
        name = params.get("name")
        instruction = params.get("instruction")
        metadata = (
            params.get("metadata")
            if isinstance(params.get("metadata"), dict)
            else None
        )
        dependencies = _normalize_dependencies_fn(params.get("dependencies"))
        node = agent.plan_session.repo.update_task(
            tree.id,
            task_id,
            name=name,
            instruction=instruction,
            metadata=metadata,
            dependencies=dependencies,
        )
        agent._refresh_plan_tree()
        message = f"Task [{node.id}] information has been updated."
        details = {"task": node.model_dump()}
        agent._dirty = True
        return AgentStep(
            action=action, success=True, message=message, details=details
        )

    if action.name == "update_task_instruction":
        task_id = agent._coerce_int(params.get("task_id"), "task_id")
        instruction = params.get("instruction")
        if not instruction:
            raise ValueError("update_task_instruction requires an instruction.")
        node = agent.plan_session.repo.update_task(
            tree.id,
            task_id,
            instruction=instruction,
        )
        agent._refresh_plan_tree()
        message = f"Task [{node.id}] instructions have been updated."
        details = {"task": node.model_dump()}
        agent._dirty = True
        return AgentStep(
            action=action, success=True, message=message, details=details
        )

    if action.name == "move_task":
        task_id = agent._coerce_int(params.get("task_id"), "task_id")
        new_parent_id = params.get("new_parent_id")
        if new_parent_id is not None:
            new_parent_id = agent._coerce_int(new_parent_id, "new_parent_id")
        new_position = params.get("new_position")
        if new_position is not None:
            new_position = agent._coerce_int(new_position, "new_position")
        node = agent.plan_session.repo.move_task(
            tree.id,
            task_id,
            new_parent_id=new_parent_id,
            new_position=new_position,
        )
        agent._refresh_plan_tree()
        message = f"Task [{node.id}] has been moved to a new position."
        details = {"task": node.model_dump()}
        agent._dirty = True
        return AgentStep(
            action=action, success=True, message=message, details=details
        )

    if action.name == "delete_task":
        task_id = agent._coerce_int(params.get("task_id"), "task_id")
        agent.plan_session.repo.delete_task(tree.id, task_id)
        agent._refresh_plan_tree()
        message = f"Task [{task_id}] and its subtasks have been deleted."
        details = {"task_id": task_id}
        agent._dirty = True
        return AgentStep(
            action=action, success=True, message=message, details=details
        )

    if action.name == "show_tasks":
        agent._refresh_plan_tree(force_reload=False)
        outline = agent.plan_session.outline(max_depth=6, max_nodes=120)
        message = f"Here is the task overview for plan #{tree.id}."
        details = {"plan_id": tree.id, "outline": outline}
        return AgentStep(
            action=action, success=True, message=message, details=details
        )

    if action.name == "query_status":
        agent._refresh_plan_tree(force_reload=False)
        node_count = agent.plan_tree.node_count() if agent.plan_tree else 0
        root_count = len(agent.plan_tree.root_node_ids()) if agent.plan_tree else 0
        message = f"Plan #{tree.id} currently has {node_count} task nodes ({root_count} roots)."
        details = {
            "plan_id": tree.id,
            "task_count": node_count,
            "root_tasks": root_count,
        }
        return AgentStep(
            action=action, success=True, message=message, details=details
        )

    if action.name == "rerun_task":
        task_id_raw = params.get("task_id")
        task_id = agent._coerce_int(task_id_raw, "task_id")
        if agent.plan_executor is None:
            raise ValueError("Plan executor is not enabled in this environment.")
        paper_mode_raw = params.get("paper_mode")
        paper_mode = False
        if isinstance(paper_mode_raw, bool):
            paper_mode = paper_mode_raw
        elif paper_mode_raw is not None:
            paper_mode = str(paper_mode_raw).strip().lower() in {"1", "true", "yes", "on", "y"}
        # Build session context and pass to task executor.
        session_ctx = {
            "session_id": agent.session_id,  # For tool calls.
            "user_message": agent._current_user_message if hasattr(agent, "_current_user_message") else None,
            "chat_history": agent.history,
            "recent_tool_results": agent.extra_context.get("recent_tool_results", []),
            "paper_mode": paper_mode,
        }
        exec_config = ExecutionConfig(session_context=session_ctx, paper_mode=paper_mode)
        result = agent.plan_executor.execute_task(tree.id, task_id, config=exec_config)
        status = (result.status or "").strip().lower()
        success = status in {"completed", "done", "success"}
        message = f"Task [{task_id}] execution status: {result.status}."
        if status == "skipped":
            message = f"Task [{task_id}] was skipped."
        elif status in {"failed", "error"}:
            message = f"Task [{task_id}] failed."
        details = result.to_dict()
        agent._refresh_plan_tree(force_reload=True)
        return AgentStep(
            action=action, success=success, message=message, details=details
        )

    if action.name == "verify_task":
        task_id = agent._coerce_int(params.get("task_id"), "task_id")
        if not tree.has_node(task_id):
            raise ValueError(f"Task {task_id} not found in plan {tree.id}")
        node = tree.get_node(task_id)
        if not node.execution_result:
            return AgentStep(
                action=action,
                success=False,
                message=f"Task [{task_id}] has no execution result to verify.",
                details={"task_id": task_id, "plan_id": tree.id},
            )

        # Collect override criteria from multiple sources the LLM might use:
        #   1. params.verification_criteria  – shorthand strings (preferred)
        #   2. params.acceptance_criteria     – full dict
        #   3. action.metadata.acceptance_criteria – LLM sometimes puts hints there
        #   4. action.metadata.verification_criteria – shorthand in metadata
        override_criteria = None
        raw_vc = params.get("verification_criteria")
        action_meta = action.metadata if isinstance(action.metadata, dict) else {}

        if not isinstance(raw_vc, list) or not raw_vc:
            raw_vc = action_meta.get("verification_criteria")
        if not isinstance(raw_vc, list) or not raw_vc:
            raw_vc = params.get("acceptance_criteria")
        if not isinstance(raw_vc, list) or not raw_vc:
            raw_vc = action_meta.get("acceptance_criteria")

        if isinstance(raw_vc, list) and raw_vc:
            # Check if items are shorthand strings or full dict checks
            if all(isinstance(item, str) for item in raw_vc):
                override_criteria = _task_verifier.parse_shorthand_criteria(raw_vc)
            elif all(isinstance(item, dict) for item in raw_vc):
                override_criteria = {
                    "category": "file_data",
                    "blocking": True,
                    "checks": list(raw_vc),
                }

        # Also accept a pre-formed dict in params.acceptance_criteria
        if not override_criteria:
            raw_ac = params.get("acceptance_criteria")
            if not isinstance(raw_ac, dict):
                raw_ac = action_meta.get("acceptance_criteria")
            if isinstance(raw_ac, dict) and raw_ac.get("checks"):
                override_criteria = raw_ac

        # Last resort: if the node has no acceptance_criteria and we have no
        # override, try to build basic checks from the task's execution_result
        # artifact paths so we don't always skip.
        if not override_criteria:
            existing_criteria = (
                node.metadata.get("acceptance_criteria")
                if isinstance(node.metadata, dict) else None
            )
            if not _task_verifier._has_checks(existing_criteria):
                try:
                    raw_payload = json.loads(node.execution_result) if isinstance(node.execution_result, str) else {}
                    artifact_paths = _task_verifier.collect_artifact_paths(raw_payload)
                    local_paths = [p for p in artifact_paths if _task_verifier._is_local_path(p)]
                    if local_paths:
                        override_criteria = _task_verifier._build_generated_criteria(local_paths)
                        logger.info(
                            "verify_task: auto-generated %d checks from artifact paths for task %s",
                            len(override_criteria.get("checks", [])),
                            task_id,
                        )
                except Exception:
                    pass

        if override_criteria and _task_verifier._has_checks(override_criteria):
            logger.info(
                "verify_task: using %d override checks for task %s",
                len(override_criteria.get("checks", [])),
                task_id,
            )

        try:
            finalization = _task_verifier.verify_task(
                agent.plan_session.repo,
                plan_id=tree.id,
                task_id=task_id,
                trigger="manual",
                override_criteria=override_criteria,
            )
        except Exception as verify_err:
            logger.warning("verify_task failed for task %s: %s", task_id, verify_err)
            return AgentStep(
                action=action,
                success=False,
                message=f"Task [{task_id}] verification error: {verify_err}",
                details={"task_id": task_id, "plan_id": tree.id},
            )
        verification = finalization.verification or {}
        verification_status = str(verification.get("status") or "skipped")
        checks_total = int(verification.get("checks_total", 0) or 0)
        checks_passed = int(verification.get("checks_passed", 0) or 0)
        needs_criteria = bool(verification.get("needs_criteria"))
        if verification_status == "passed":
            message = (
                f"Task [{task_id}] verification passed "
                f"({checks_passed}/{checks_total} checks)."
            )
        elif verification_status == "failed":
            message = (
                f"Task [{task_id}] verification failed "
                f"({checks_passed}/{checks_total} checks passed)."
            )
        elif needs_criteria:
            message = (
                f"Task [{task_id}] verification skipped: no acceptance_criteria or "
                f"verification_criteria provided. You MUST pass verification_criteria "
                f"with concrete check strings (e.g. 'file_exists:/path/to/output.csv') "
                f"for the verifier to run actual checks."
            )
        else:
            message = f"Task [{task_id}] verification skipped."
        verification_success = verification_status == "passed"
        if verification_status == "skipped" and not needs_criteria:
            verification_success = True
        agent._refresh_plan_tree(force_reload=True)
        return AgentStep(
            action=action,
            success=verification_success,
            message=message,
            details={
                "task_id": task_id,
                "plan_id": tree.id,
                "status": finalization.final_status,
                "verification": verification,
                "payload": finalization.payload,
            },
        )

    if action.name == "decompose_task":
        if agent.plan_decomposer is None:
            raise ValueError("Task decomposition service is not enabled in this environment.")
        if agent.decomposer_settings.model is None:
            raise ValueError("No decomposition model configured; cannot proceed.")

        expand_depth_raw = params.get("expand_depth")
        node_budget_raw = params.get("node_budget")
        allow_existing_raw = params.get("allow_existing_children")

        expand_depth = (
            agent._coerce_int(expand_depth_raw, "expand_depth")
            if expand_depth_raw is not None
            else None
        )
        node_budget = (
            agent._coerce_int(node_budget_raw, "node_budget")
            if node_budget_raw is not None
            else None
        )
        allow_existing_children = None
        if allow_existing_raw is not None:
            if isinstance(allow_existing_raw, bool):
                allow_existing_children = allow_existing_raw
            else:
                allow_existing_children = str(
                    allow_existing_raw
                ).strip().lower() in {
                    "1",
                    "true",
                    "yes",
                    "y",
                }

        task_id_raw = params.get("task_id")
        # Build session context and pass to plan decomposer.
        session_ctx = {
            "user_message": agent._current_user_message if hasattr(agent, "_current_user_message") else None,
            "chat_history": agent.history,
            "recent_tool_results": agent.extra_context.get("recent_tool_results", []),
        }
        if task_id_raw is None:
            result = agent.plan_decomposer.run_plan(
                tree.id,
                max_depth=expand_depth,
                node_budget=node_budget,
                session_context=session_ctx,
            )
        else:
            task_id = agent._coerce_int(task_id_raw, "task_id")
            result = agent.plan_decomposer.decompose_node(
                tree.id,
                task_id,
                expand_depth=expand_depth,
                node_budget=node_budget,
                allow_existing_children=allow_existing_children,
                session_context=session_ctx,
            )

        agent._last_decomposition = result
        if result.created_tasks:
            agent._dirty = True
        try:
            agent._refresh_plan_tree(force_reload=True)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "Failed to refresh plan tree after decomposition: %s", exc
            )
            agent._decomposition_errors.append(f"Failed to refresh plan after decomposition: {exc}")

        created_count = len(result.created_tasks)
        message = (
            f"Generated {created_count} subtasks."
            if created_count
            else "No new subtasks were generated."
        )
        if result.stopped_reason:
            message += f" Stop reason: {result.stopped_reason}."
        details = {
            "plan_id": tree.id,
            "mode": result.mode,
            "processed_nodes": result.processed_nodes,
            "created": [node.model_dump() for node in result.created_tasks],
            "failed_nodes": result.failed_nodes,
            "stopped_reason": result.stopped_reason,
            "stats": result.stats,
        }
        return AgentStep(
            action=action, success=True, message=message, details=details
        )

    return handle_unknown_action(agent, action)


# ---------------------------------------------------------------------------
# handle_context_request
# ---------------------------------------------------------------------------

def handle_context_request(agent: Any, action: LLMAction) -> AgentStep:
    if action.name != "request_subgraph":
        return handle_unknown_action(agent, action)
    params = action.parameters or {}
    tree = agent._require_plan_bound()
    node_id_value = params.get("logical_id") or params.get("task_id")
    node_id = agent._coerce_int(node_id_value, "task_id")
    max_depth_raw = params.get("max_depth")
    max_depth = (
        agent._coerce_int(max_depth_raw, "max_depth")
        if max_depth_raw is not None
        else 2
    )
    agent._refresh_plan_tree(force_reload=False)
    graph_tree = agent.plan_tree or agent.plan_session.ensure()
    try:
        nodes = graph_tree.subgraph_nodes(node_id, max_depth=max_depth)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    outline = graph_tree.subgraph_outline(node_id, max_depth=max_depth)
    details = {
        "plan_id": tree.id,
        "root_node": node_id,
        "max_depth": max_depth,
        "outline": outline,
        "nodes": [node.model_dump() for node in nodes],
    }
    message = f"Returned a subgraph preview for node {node_id}."
    return AgentStep(action=action, success=True, message=message, details=details)


# ---------------------------------------------------------------------------
# handle_system_action
# ---------------------------------------------------------------------------

def handle_system_action(agent: Any, action: LLMAction) -> AgentStep:
    if action.name == "help":
        message = (
            "System help: you can create/list/delete plans or perform CRUD and restructuring actions on the current plan. "
            "For subgraph queries and similar operations, bind a plan first by calling create_plan or list_plans."
        )
        return AgentStep(action=action, success=True, message=message, details={})
    return handle_unknown_action(agent, action)


# ---------------------------------------------------------------------------
# handle_unknown_action
# ---------------------------------------------------------------------------

def handle_unknown_action(agent: Any, action: LLMAction) -> AgentStep:
    message = f"Unrecognized action kind or name: {action.kind}/{action.name}."
    return AgentStep(action=action, success=False, message=message, details={})
