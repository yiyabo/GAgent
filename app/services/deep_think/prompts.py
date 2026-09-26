"""Prompt builders for the DeepThink agent (god-class split, behaviour zero-change).

Bodies of the like-named DeepThinkAgent methods move verbatim with `self`
renamed to `agent` (`cls` kept as `cls`); the class keeps thin wrappers with
the same decorators, and cross-calls between builders go through `agent._x(...)`
so subclass overrides keep working. Display-family helpers
(detect_reasoning_language, _localized_text) stay in deep_think_agent and are
reached through the late-bound `_dta()` so their monkeypatch surface is
unchanged.

Two sanctioned deviations from a purely verbatim move:
- `_load_bio_tools_catalog` computes the repo root from `__file__`; after the
  move one directory deeper it uses `parents[3]` instead of `parents[2]` to
  point at the same repo root.
- `_append_recent_chat_history` called the explicit
  `DeepThinkAgent._is_brief_execute_followup_context(context)` (which already
  bypassed subclass overrides); it now calls the module-local
  `_is_brief_execute_followup_context(context)`, which is semantically
  identical.

Prompt-cache contract (2026-09): the system prompt builders no longer embed
chat history. History enters the LLM call as role messages via
`_extract_history_messages`, inserted by the controllers between the system
message and the live user turn, so the system prompt keeps a byte-stable
prefix across turns of a session. `_append_recent_chat_history` remains as
the legacy text form for facade/tests compatibility.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from app.services.deep_think.models import TaskExecutionContext
from app.services.foundation.settings import CHAT_HISTORY_ABS_MAX, get_settings
from app.services.response_style import PROFESSIONAL_STYLE_INSTRUCTION
from app.services.tool_schemas import code_mode_enabled, delegate_task_enabled

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.services.deep_think_agent import DeepThinkAgent

logger = logging.getLogger(__name__)


def _dta() -> Any:
    """Late-bound deep_think_agent module (monkeypatch-friendly lookups)."""
    from app.services import deep_think_agent

    return deep_think_agent


_BIO_TOOLS_FALLBACK_CATALOG: Dict[str, List[str]] = {
    "seqkit": ["stats", "grep", "seq", "head"],
    "blast": ["blastn", "blastp", "makeblastdb"],
    "prodigal": ["predict", "meta"],
    "hmmer": ["hmmscan", "hmmsearch", "hmmpress", "hmmbuild"],
    "checkv": ["end_to_end", "completeness", "complete_genomes"],
}


def _load_bio_tools_catalog() -> Dict[str, List[str]]:
    config_path = Path(__file__).resolve().parents[3] / "tool_box" / "bio_tools" / "tools_config.json"
    try:
        if not config_path.exists():
            return dict(_BIO_TOOLS_FALLBACK_CATALOG)
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        catalog: Dict[str, List[str]] = {}
        for tool_name, info in raw.items():
            ops = sorted((info or {}).get("operations", {}).keys())
            catalog[str(tool_name)] = [str(op) for op in ops]
        if not catalog:
            return dict(_BIO_TOOLS_FALLBACK_CATALOG)
        return catalog
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to load bio tools catalog for DeepThink prompt: %s", exc)
        return dict(_BIO_TOOLS_FALLBACK_CATALOG)


def _format_bio_tools_catalog(catalog: Dict[str, List[str]]) -> str:
    return "; ".join(
        f"{tool} ({', '.join(ops) if ops else 'no operations'})"
        for tool, ops in sorted(catalog.items())
    )


_BIO_TOOLS_CATALOG = _load_bio_tools_catalog()
_BIO_TOOLS_NAMES = sorted(_BIO_TOOLS_CATALOG.keys())
_BIO_TOOLS_CATALOG_TEXT = _format_bio_tools_catalog(_BIO_TOOLS_CATALOG)


def _build_structured_plan_requirement_block(agent: "DeepThinkAgent") -> str:
    flags = agent._plan_contract_flags()
    plan_id = agent._current_plan_id()

    if flags["conflict_requires_confirmation"]:
        plan_ref = f"plan_id={plan_id}" if plan_id is not None else "the current bound plan"
        return (
            "\n[REQUIREMENT] The current session is already bound to "
            f"{plan_ref}, and the new request appears to target a different subject. "
            "Do NOT call tools and do NOT execute or create a plan in this turn. "
            "Ask the user to choose one option: update the existing plan, create a new plan in this session, or start a new chat.\n"
        )

    requirement_lines: List[str] = []
    if flags["create_required"] and flags["execute_after_create_required"]:
        requirement_lines.append(
            "The user explicitly requested creating a structured executable plan and executing it. "
            "You MUST first call `plan_operation` with operation=`create`. After it succeeds, "
            "you MUST call `plan_operation` with operation=`execute_all` for the created plan_id."
        )
    elif flags["create_required"]:
        requirement_lines.append(
            "The user explicitly requested a structured plan. You MUST call `plan_operation` "
            "with operation=`create`. Do not execute the plan unless the user explicitly requested execution."
        )
    elif flags["execute_required"]:
        plan_ref = f" targeting plan_id={plan_id}" if plan_id is not None else " targeting the current bound plan"
        requirement_lines.append(
            "The user explicitly requested full plan execution. You MUST call `plan_operation` "
            f"with operation=`execute_all`{plan_ref}."
        )
    if flags["review_required"] or flags["optimize_required"]:
        ops = []
        if flags["review_required"]:
            ops.append("review")
        if flags["optimize_required"]:
            ops.append("optimize")
        plan_ref = f" targeting plan_id={plan_id}" if plan_id is not None else ""
        requirement_lines.append(
            "The user requested plan mutation. You MUST call `plan_operation` "
            f"with operation(s) {', '.join(ops)}{plan_ref} before submitting the final answer."
        )
    if requirement_lines:
        return "\n[REQUIREMENT] " + "\n[REQUIREMENT] ".join(requirement_lines) + "\n"

    # Only inject a legacy requirement block for bound plan review/optimize requests.
    route_reasons = agent.request_profile.get("route_reason_codes")
    if not isinstance(route_reasons, list):
        route_reasons = []

    if plan_id is None or not any(
        code in route_reasons for code in ("plan_review", "plan_optimize")
    ):
        return ""

    return (
        "\n[REQUIREMENT] The user is requesting a plan review/optimize/update. "
        f"You MUST call `plan_operation` targeting plan_id={plan_id} with the "
        "appropriate operation before submitting your final answer.\n"
    )


def _build_created_plan_finalize_nudge(
    agent: "DeepThinkAgent",
    *,
    user_query: str,
    plan_id: int,
    plan_title: Optional[str] = None,
) -> str:
    language = _dta().detect_reasoning_language(user_query)
    title_suffix = f"，标题：{plan_title}" if language == "zh" and plan_title else (
        f", title: {plan_title}" if plan_title else ""
    )
    return _dta()._localized_text(
        language,
        (
            f"结构化计划已创建成功（plan_id={plan_id}{title_suffix}）。"
            "不要再次调用 `plan_operation` 的 `create`。"
            "请基于这个已创建的计划，简要说明核心目标和任务结构，"
            "然后立刻调用 `submit_final_answer` 结束本轮。"
        ),
        (
            f"The structured plan has already been created successfully (plan_id={plan_id}{title_suffix}). "
            "Do not call `plan_operation` with `create` again. "
            "Briefly summarize the created plan's goal and task structure, then call "
            "`submit_final_answer` immediately to finish this turn."
        ),
    )


def _build_created_plan_execute_nudge(
    agent: "DeepThinkAgent",
    *,
    user_query: str,
    plan_id: int,
    plan_title: Optional[str] = None,
) -> str:
    language = _dta().detect_reasoning_language(user_query)
    title_suffix = f"，标题：{plan_title}" if language == "zh" and plan_title else (
        f", title: {plan_title}" if plan_title else ""
    )
    return _dta()._localized_text(
        language,
        (
            f"结构化计划已创建成功（plan_id={plan_id}{title_suffix}）。"
            "用户还要求执行该计划。不要再次调用 `plan_operation` 的 `create`；"
            "下一步必须调用 `plan_operation`，operation=`execute_all`，plan_id 使用刚创建的计划。"
        ),
        (
            f"The structured plan has been created successfully (plan_id={plan_id}{title_suffix}). "
            "The user also requested execution. Do not call `plan_operation` with `create` again; "
            "next you MUST call `plan_operation` with operation=`execute_all` using the created plan_id."
        ),
    )


def _build_tool_failure_correction_nudge(
    agent: "DeepThinkAgent",
    tool_results: List[Dict[str, Any]],
) -> Optional[str]:
    for item in tool_results:
        if not isinstance(item, dict):
            continue
        result = item.get("result")
        if not isinstance(result, dict):
            continue
        if result.get("success") is not False:
            continue
        hint = str(result.get("hint") or "").strip()
        error = str(result.get("error") or "").strip()
        tool_name = str(item.get("tool_name") or "").strip()
        if hint and error:
            return (
                f"[SYSTEM CORRECTION] Your previous call to `{tool_name}` failed: {error}. "
                f"The tool suggests: use {hint}. "
                "Do NOT repeat the same call. Adjust your parameters accordingly."
            )
    return None


def _build_plan_conflict_confirmation_message(agent: "DeepThinkAgent") -> str:
    plan_id = agent._current_plan_id()
    plan_title = agent._current_plan_title()
    title_suffix = f" ('{plan_title}')" if plan_title else ""
    plan_ref = f"plan {plan_id}{title_suffix}" if plan_id is not None else "the current bound plan"
    return (
        f"This session is already bound to {plan_ref}. The new request appears to target a different subject. "
        "Please choose one option: 1. Update the existing plan, 2. Create a new plan in this session, or 3. Start a new chat."
    )


def _get_structured_plan_retry_prompt(agent: "DeepThinkAgent") -> str:
    flags = agent._plan_contract_flags()
    if flags["conflict_requires_confirmation"]:
        return agent._build_plan_conflict_confirmation_message()
    required_ops: List[str] = []
    if flags["create_required"]:
        required_ops.append("create")
    if flags["execute_required"]:
        required_ops.append("execute_all")
    if flags["review_required"]:
        required_ops.append("review")
    if flags["optimize_required"]:
        required_ops.append("optimize")
    if required_ops:
        return (
            "The user explicitly requested a structured plan lifecycle operation. "
            f"You must call `plan_operation` with the required operation(s): {', '.join(required_ops)}. "
            "Do not call `submit_final_answer` until those required operation(s) have succeeded."
        )
    plan_id = agent._current_plan_id()
    plan_ref = f" (plan_id={plan_id})" if plan_id else ""
    return (
        f"The user explicitly requested a plan review, optimize, or update operation{plan_ref}. "
        "You must call `plan_operation` with the appropriate operation (review/optimize/update) "
        "before calling `submit_final_answer`. Do not respond with prose alone — "
        "the plan must be mutated as requested."
    )


def _build_request_tier_block(agent: "DeepThinkAgent") -> str:
    tier = agent._request_tier()
    if tier == "standard":
        return (
            "=== REQUEST TIER: STANDARD ===\n"
            "- Give a concise but complete direct answer.\n"
            "- Avoid research style output unless the user explicitly asks for sources or latest information.\n"
            "- Keep the tone professional and plain; avoid decorative emojis or hype.\n"
            "- Prefer low-overhead execution, but do not ignore required evidence.\n"
            "- Prioritize tool-backed facts over stylistic completeness.\n"
            "- Prefer finishing in one short reasoning pass; do not output transitional narration (e.g. 'Let me search...', '接下来让我...') as a standalone response.\n"
            "- If the answer depends on file/workspace/remote state, call the relevant tool now; otherwise call submit_final_answer. A no-tool guess is not an acceptable substitute for a check you could run.\n"
        )
    if tier == "research":
        return (
            "=== REQUEST TIER: RESEARCH ===\n"
            "- Use targeted evidence gathering when it improves correctness.\n"
            "- Cite verifiable sources for time-sensitive or factual claims.\n"
            "- Keep the writing professional and restrained; avoid decorative emojis in headings or labels.\n"
            "- Keep research focused on the exact user question; avoid unrelated survey padding.\n"
            "- When a successful `literature_pipeline` result provides `study_cards.jsonl`, `library.jsonl`, `evidence.md`, `study_matrix.md`, or a coverage report, those paths are evidence waiting to be read, not evidence that is absent.\n"
            "- Read the relevant portions of those existing artifacts with `file_operations` before saying the evidence is insufficient or that the findings cannot be confirmed. Do not treat a filename, count, or tool summary as a substitute for reading the records.\n"
            "- After reading, state precisely what the records support and what fields or full text remain unavailable; never invent findings to fill a gap.\n"
        )
    if tier == "execute":
        execute_focus_note = ""
        if agent._is_brief_execute_followup():
            execute_focus_note = (
                "- This is a short execution follow-up: focus the final answer on the current task outcome.\n"
                "- Do not recap prior project milestones, older test rounds, or historical status tables unless the user explicitly asks.\n"
                "- Do not append next-step menus or optional directions unless the user asks what to do next.\n"
                "- If continuation context already identifies the target file, path, task, or blocker, continue from that anchor instead of restarting broad workspace discovery.\n"
            )
        return (
            "=== REQUEST TIER: EXECUTE ===\n"
            "- Prioritize finishing the requested task over broad background research.\n"
            "- Use file/code/task tools as needed.\n"
            "- Keep the tone professional and execution-focused; avoid decorative emojis or cheerleading.\n"
            "- Use web_search only to fill a concrete factual gap that blocks execution quality.\n"
            "- For a bound execute_task request, observation-only probing is only a short precursor. After one observation-only cycle, move to real execution or report BLOCKED_DEPENDENCY.\n"
            "- For local structured-data overview/schema/count/sample-value requests, prefer result_interpreter profile before heavier code_executor runs.\n"
            "- Do not silently rewrite the current task into an upstream preprocessing task just because prerequisite deliverables are missing.\n"
            "- For immutable source inputs, prefer canonical data-directory paths over same-named session-root `results/` copies, especially when the session copy is empty or malformed.\n"
            "- For single-cell integration tasks, fewer than 2 valid upstream samples means the preconditions are not met; do not claim integration succeeded.\n"
            + execute_focus_note
        )
    return ""


def _build_tool_access_block(agent: "DeepThinkAgent") -> str:
    lines = [
        "=== TOOL ACCESS ===",
        "- You have access to ALL registered tools. Choose the right tool based on user intent.",
        "- For simple chat questions, you may answer directly without tools.",
        "- For questions involving data, files, remote services, or verifiable facts, use tools to obtain ground truth — never fabricate results.",
        "- If a tool call fails, report the failure honestly; do not invent tool-like certainty.",
    ]
    current_plan_id = agent._current_plan_id()
    if current_plan_id is not None:
        if (
            agent.request_profile.get("plan_new_requested")
            and agent.request_profile.get("plan_create_required")
        ):
            lines.append(
                f"- This session is currently bound to plan {current_plan_id}, but the user explicitly requested a new plan. "
                "You may call plan_operation create without reusing the existing plan_id; after creation, use the new plan_id."
            )
            return "\n".join(lines) + "\n"
        lines.append(
            f"- This session is bound to plan {current_plan_id}. "
            "When calling plan_operation, ALWAYS use this plan_id. "
            "Ignore references to other plans in the chat history."
        )
    return "\n".join(lines) + "\n"


def _build_grounded_tooling_block(agent: "DeepThinkAgent") -> str:
    """Nudge the agent to use tools for verification when facts are checkable."""
    return (
        "=== GROUNDED TOOLING ===\n"
        "- If the answer depends on local files, workspace contents, remote task/API state, sequences, or "
        "other checkable facts, use the appropriate tool(s) before stating specifics.\n"
        "- Do not substitute confident-sounding prose for evidence when a tool can obtain the "
        "ground truth within reasonable latency.\n\n"
    )


def _build_artifact_deliverable_workflow_block(agent: "DeepThinkAgent") -> str:
    return (
        """=== FIGURE FINE-TUNING & ITERATIVE REFINEMENT RULES ===
- When the user requests a figure fine-tuning (e.g., prompt starting with '【图表局部微调】针对目标图表' or asking to modify colors, fonts, margins, annotations, or panel layouts of an existing figure):
  1. ALWAYS treat this as a DIFFERENTIAL MODIFICATION on the existing figure, NOT a new figure from scratch.
  2. DO NOT state that the previous figure is missing or absent from session directory.
  3. Inspect the existing figure provenance table (*_provenance.tsv), legend (summary.md), or datasets in conversation history to retain the exact same underlying data points, values, and group names.
  4. Apply the requested visual modifications (e.g. palette change, font size, highlighting, bounding box focus) while keeping all other untouched elements strictly intact.
  5. In-place overwrite the target figure with the exact same output_basename and generate all three formats (PNG 300dpi, editable vector SVG, publication PDF).

=== ARTIFACT AND DELIVERABLE WORKFLOW ===
"""
        "- Treat requests to analyze, combine, summarize, regenerate, export, save, or visualize existing files, plan outputs, task outputs, or deliverables as action requests, not prose-only answers.\n"
        "- For such requests, choose the needed tools yourself: discover relevant artifacts, read or profile supporting files, synthesize or compute, create/write the requested output, verify it when a file is requested, then submit the final answer.\n"
        "- Do not substitute chat prose, copy-paste Markdown, or a code snippet for a requested saved file, generated figure, or deliverable.\n"
        "- Good pattern: user asks to generate `plan_114_summary.md` from a completed plan -> inspect the plan manifest/deliverables, read the relevant CSV/JSON/MD evidence, write the Markdown report with manuscript_writer or file_operations, verify the path exists, then report the saved file.\n"
        "- Good pattern: user asks for recent weather changes as a chart -> gather current weather evidence, generate the chart artifact with the appropriate figure/code tool, then report the output path.\n\n"
    )


def _build_bio_tools_quick_map_block(agent: "DeepThinkAgent") -> str:
    if "bio_tools" not in agent.available_tools:
        return ""
    return (
        "=== BIO_TOOLS QUICK MAP ===\n"
        "For FASTA/FASTQ/sequence tasks, prefer bio_tools over code_executor.\n"
        "Call bio_tools(operation='help', tool_name='<tool>') for full param details.\n"
        "Common patterns:\n"
        "- seqkit stats: {\"tool_name\": \"seqkit\", \"operation\": \"stats\", \"input_file\": \"/path/to/file.fasta\"}\n"
        "- seqkit grep: {\"tool_name\": \"seqkit\", \"operation\": \"grep\", \"input_file\": \"/path/to/file.fasta\", \"params\": {\"pattern\": \"contig_1\", \"output\": \"result.fa\"}}\n"
        "- prodigal predict: {\"tool_name\": \"prodigal\", \"operation\": \"predict\", \"input_file\": \"/path/to/genome.fasta\", \"params\": {\"protein\": \"genes.faa\", \"nucleotide\": \"genes.fna\"}}\n"
        "- blast makeblastdb: {\"tool_name\": \"blast\", \"operation\": \"makeblastdb\", \"input_file\": \"/path/to/seqs.fasta\", \"params\": {\"type\": \"nucl\", \"db\": \"mydb\"}}\n"
        "- blast blastn: {\"tool_name\": \"blast\", \"operation\": \"blastn\", \"input_file\": \"/path/to/query.fasta\", \"params\": {\"db\": \"mydb\", \"output\": \"results.txt\"}}\n"
        "- samtools sort: {\"tool_name\": \"samtools\", \"operation\": \"sort\", \"input_file\": \"/path/to/reads.bam\", \"params\": {\"output\": \"sorted.bam\"}}\n"
        "For inline sequence text (no file), pass sequence_text instead of input_file.\n"
        "If bio_tools returns error_code='missing_required_params', read missing_params and retry_hint, then retry with corrected params.\n\n"
    )


def _build_plan_artifact_discovery_block(
    agent: "DeepThinkAgent",
    context: Optional[Dict[str, Any]] = None,
) -> str:
    plan_id = agent._current_plan_id()
    if plan_id is None:
        return ""
    context = context or {}
    session_id = None
    for key in ("session_id", "runtime_session_id", "chat_session_id"):
        value = context.get(key)
        if isinstance(value, str) and value.strip():
            session_id = value.strip()
            break
    lines = [
        "=== PLAN ARTIFACT DISCOVERY ===",
        f"- This session is bound to Plan {plan_id}. For follow-up analysis over completed plan outputs, discover artifacts before making evidence-backed claims.",
        f"- Start from the plan artifact manifest when present. Check session-scoped location first: `runtime/{session_id}/artifacts/plan_{plan_id}/artifacts_manifest.json`, then fall back to project-level: `results/plans/plan_{plan_id}/artifacts_manifest.json`.",
        "- If a session runtime directory is available, inspect `deliverables/manifest_latest.json` and `deliverables/latest/` there before summarizing or writing reports.",
    ]
    if session_id:
        lines.extend([
            f"- Session-scoped manifest candidate: `runtime/{session_id}/deliverables/manifest_latest.json`.",
            f"- Session-scoped deliverables candidate: `runtime/{session_id}/deliverables/latest/`.",
        ])
    lines.extend([
        "- Use file_operations profile/read or result_interpreter profile on manifest-listed CSV/TSV/JSON/MD artifacts; do not rely only on the plan outline or memory of earlier turns.",
        "- Keep this as discovery guidance: do not dump every artifact into the prompt; inspect the manifest and read only the evidence needed for the user's requested synthesis.",
        "",
    ])
    return "\n".join(lines)


def _build_evidence_scope_block(agent: "DeepThinkAgent") -> str:
    return (
        "=== EVIDENCE SCOPE RULES ===\n"
        "- Distinguish complete enumeration, status/manifest files, and sampled previews.\n"
        "- If tool output includes `evidence_scope`, `completeness_status`, `status_counts`, "
        "`incomplete_examples`, or `partial_completion_suspected`, carry those signals into the final answer.\n"
        "- Never turn sampled listings or compacted previews into all/every/each/global success claims. "
        "Only make a global completion claim when complete enumeration plus a manifest/status file supports it.\n"
        "- For PhageScope local dataset exploration, use `phagescope_research` action=`deep_profile` before making "
        "numeric size/row/schema/readiness claims; do not estimate dataset sizes from directory names or samples.\n"
        "- Do not treat generic spreadsheet/tabular files (`.xlsx`/`.xls`/`.csv`/`.parquet`) as PhageScope datasets even if their path contains 'phagescope'; analyze those with `code_executor`, not `phagescope_research`.\n"
        "- If failure/error status files or partial-completion signals are present, state the completed and failed counts explicitly and qualify the conclusion.\n\n"
    )


def _build_session_isolation_block(agent: "DeepThinkAgent") -> str:
    session_id = str(agent.request_profile.get("session_id") or "").strip()
    if not session_id:
        return ""
    from app.services.session_paths import get_runtime_session_dir
    try:
        session_dir = get_runtime_session_dir(session_id, create=False)
    except Exception:
        return ""
    return (
        "=== SESSION ISOLATION (CRITICAL) ===\n"
        f"- This session's runtime directory is: {session_dir}\n"
        f"- When searching for or reading analysis results, model outputs, feature files, or data dictionaries, "
        f"ALWAYS look under `{session_dir}/` first (especially `{session_dir}/results/` and `{session_dir}/raw_files/`).\n"
        "- NEVER read result/output files from global project directories like `/app/output/`, "
        "`/app/results/`, or `/data*/` — those contain outputs from OTHER sessions "
        "and will cause cross-contamination of findings.\n"
        "- If you need to find a previously generated file in this session, use file_operations with a path scoped to "
        f"`{session_dir}/`, not the project root.\n"
        "- When listing files to discover outputs, scope the listing to the session directory. Do NOT list "
        "`/app` or `/app/results` — that will surface unrelated session files.\n"
        "- USER DATA INPUTS: If the current user message lists attachments, analyze ONLY those attachment paths. "
        f"Do NOT treat leftover files under `{session_dir}/uploads/` from previous upload attempts as additional datasets "
        "unless the user explicitly asks to reuse them.\n"
        "- NUMBERED REFERENCE POLICY: When the user selects 方向N / 研究方向N / option N, you MUST resolve N to the exact title from your earlier numbered list in this conversation (or from GROUNDED NUMBERED REFERENCES in the user message). Restate that title before writing any protocol. Never invent a different topic that reuses the same number. Priority tables use the same 方向N ids (not row rank).\n"
        "- SPREADSHEET SHEET POLICY: If user names a sheet, use only that sheet. If unspecified, analyze only the "
        "primary/main data sheet (typically the first or the detailed clinical table). Do NOT auto-analyze every "
        "sheet in a workbook as separate datasets; secondary sheets (rosters/codebooks) are optional context only "
        "unless the user asks. Always state which sheet(s) were analyzed.\n"
        "- SAMPLE ADEQUACY / MODELING GATE POLICY: When profile/tool output includes SAMPLE ADEQUACY AUDIT "
        "(tier red/yellow/green), you MUST honor it before recommending modeling. "
        "RED: do NOT recommend or run multi-model ML AUC showcases as the main path; prefer descriptive stats, "
        "univariable tests, bias notes, and sample-size/event planning; research directions must NOT default to "
        "building a prediction model; if the user insists, at most exploratory logistic regression with <=3 "
        "prespecified predictors plus the audit disclaimer — never present AUC as clinically actionable. "
        "YELLOW: limited simple modeling only (CV + CIs); forbid multi-model beauty contests as the conclusion. "
        "GREEN: limited supervised modeling OK with calibration/validation caveats. "
        "Always restate N, events (if known), EPV, tier, and suggested N/events before proposing modeling. "
        "Say briefly that thresholds are empirical EPV rules of thumb, not formal power analysis.\n\n"
    )


def _build_shared_strategy_block(agent: "DeepThinkAgent") -> str:
    return (
        "=== EFFORT AND TOOLING POLICY ===\n"
        "1. First classify the request: casual chat, direct answer, evidence-backed research, or execution task.\n"
        "2. Match effort to the request. Default to the lightest path that fully satisfies the user.\n"
        "3. Do NOT start broad web/literature research for greetings, casual follow-ups, simple explanations, or opinion-style questions.\n"
        "4. Use tools when they materially improve correctness or usefulness: latest information, explicit citations, factual verification, file/workspace actions, or complex analysis. "
        "Prefer tool-backed checks for anything that depends on real files, remote services, or run results (unless the user clearly wants opinion-only).\n"
        "5. One precise tool call is better than several redundant calls.\n"
        "6. Conclude as soon as the user's need is satisfied; do not pad the reply with extra background, market analysis, or references unless they help answer the request.\n"
        "7. If the user asks for depth, latest research, or sources, then increase rigor and evidence gathering.\n\n"
        "=== WRITING STYLE ===\n"
        f"- {PROFESSIONAL_STYLE_INSTRUCTION}\n"
        "- Prefer clear headings and plain wording over expressive decoration.\n\n"
        "=== TOOL PRIORITY ===\n"
        "- For accession-based FASTA downloads, call sequence_fetch first.\n"
        "- For FASTA/FASTQ/sequence work, ALWAYS try bio_tools first before code_executor.\n"
        "- If the user provides inline sequence text (not a file), pass it as bio_tools(sequence_text=...).\n"
        "- If bio_tools routing is uncertain, call bio_tools(operation='help') first; use web_search only when help is insufficient.\n"
        "- For scientific/composite figures, plots, charts, visualizations, or requests that require PNG/PDF plus summary, provenance, QA, or Deliverables publication, call scientific_figure_generator first when available. Use code_executor only for figure types or preprocessing that scientific_figure_generator cannot express.\n"
        "- For complex custom analysis not covered by bio_tools, then use code_executor.\n"
        "- Never use code_executor as fallback for sequence_fetch failures.\n"
        "- Never use code_executor as fallback for bio_tools input-conversion/parsing failures.\n"
        "- For status polling tools, if state is unchanged across several checks, stop active polling and summarize current status.\n"
        "- If the user explicitly asks for a plan or task breakdown and plan_operation is available, use plan_operation to create or update a structured plan instead of replying with a prose-only pseudo-plan.\n"
        "- For plan creation, `plan_operation.create` already performs integrated material collection before decomposition when needed. Do not manually split this into create-then-decompose unless you are explicitly refining an existing plan later.\n"
        "- When executing a currently bound plan task, do NOT use plan_operation or task_operation just to mark that task completed/failed. Tool execution auto-sync already handles current task status; use plan_operation/task_operation only for structural plan edits.\n"
        "- When executing a currently bound plan task, observation-only tools (read-only file_operations, document_reader, vision_reader) may clarify one concrete uncertainty, but they are not task completion. Do not loop on probe-only exploration.\n"
        "- If the current bound task depends on upstream deliverables that are missing, report BLOCKED_DEPENDENCY clearly instead of silently switching to a different upstream task.\n"
        "- Do not convert an integration/analysis task into full upstream preprocessing unless the task instruction explicitly authorizes backfilling prerequisites.\n"
        "- For immutable source inputs, prefer canonical data-directory paths over same-named session-root `results/` copies; ignore empty or malformed session duplicates.\n"
        "- For single-cell workflows, do not assume `adata.var['mt']` already exists. If mitochondrial flags are needed, derive them from gene_symbols, feature_name, or var_names.\n"
        "- For single-cell integration, fewer than 2 valid samples means the preconditions are not met; do not claim batch integration succeeded or emit placeholder success artifacts.\n"
        "- For web_search: cite verifiable sources. When stating time-sensitive or factual claims, include URLs from the tool JSON "
        "`results` list (title/url) in your final answer. If `results` is empty and the tool response has no URLs, say sources were "
        "not returned and avoid presenting specific claims as independently verified.\n"
    ) + agent._build_compliance_policy_block()


def _build_compliance_policy_block(agent: "DeepThinkAgent") -> str:
    """IP / trade-secret / commercial-violation refusal policy (GB/T 41812 standard).

    Covers all 5 core violation categories from the 1,600-question IP & trade secrets
    benchmark, including gray-area social engineering and role-play evasion framing,
    while explicitly protecting legitimate IP consultations from over-refusal.
    """
    try:
        from app.services.safety.compliance_guard import build_system_compliance_policy
        return "\n" + build_system_compliance_policy()
    except Exception:
        return (
            "\n=== LEGAL & IP COMPLIANCE POLICY ===\n"
            "Scope: intellectual-property infringement, trade secrets, and commercial violations.\n"
            "1. Never provide operational guidance for IP infringement, trade-secret exfiltration, or fraud.\n"
            "2. Refuse with legal grounds and redirect to legitimate compliance channels.\n"
            "3. Legitimate IP/legal consultations must be answered substantively.\n"
        )


def _build_protocol_boundary_block(agent: "DeepThinkAgent", mode: str) -> str:
    if mode == "native":
        return (
            "=== PROTOCOL BOUNDARY (NATIVE TOOL CALLING) ===\n"
            "- Use native tool calls for actions and submit_final_answer to finish.\n"
            "- Do NOT output legacy JSON keys like thinking/action/final_answer in plain text.\n"
            "- Do NOT output structured-agent JSON keys like llm_reply/actions.\n"
        )
    return (
        "=== PROTOCOL BOUNDARY (LEGACY JSON) ===\n"
        "- Respond with valid JSON only using keys: thinking, action, final_answer.\n"
        "- Do NOT output structured-agent JSON keys like llm_reply/actions.\n"
        "- action is null or an object: {\"tool\": \"name\", \"params\": {...}}.\n"
        "- final_answer is null until you are ready to conclude.\n"
    )


def _is_brief_execute_followup_context(context: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(context, dict):
        return False
    tier = str(context.get("request_tier") or "").strip().lower()
    brevity_hint = bool(context.get("brevity_hint"))
    return tier == "execute" and brevity_hint


def _select_recent_history(
    context: Optional[Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], bool]:
    """Shared history selection/clip policy.

    Returns ``(selected, brief_execute_followup)`` where each selected item is
    ``{"role": ..., "content": ...}`` with content already clipped. Single
    source for both the legacy text form (``_append_recent_chat_history``)
    and the message form (``_extract_history_messages``).
    """
    if not context:
        return [], False
    history = context.get("chat_history", [])
    if not history:
        return [], False
    brief_execute_followup = _is_brief_execute_followup_context(context)
    raw_lim = context.get("chat_history_max_messages")
    if isinstance(raw_lim, int) and raw_lim > 0:
        limit = min(raw_lim, CHAT_HISTORY_ABS_MAX)
    else:
        try:
            limit = max(
                1,
                min(
                    CHAT_HISTORY_ABS_MAX,
                    int(getattr(get_settings(), "chat_history_max_messages", 80)),
                ),
            )
        except Exception:
            limit = 80
    if brief_execute_followup:
        limit = min(limit, 6)
        filtered_history = []
        for msg in history:
            content = str(msg.get("content") or "").strip()
            if not content:
                continue
            filtered_history.append(msg)
        history = filtered_history
    recent = history[-limit:] if len(history) > limit else history
    clip_limit = 240 if brief_execute_followup else 500
    selected: List[Dict[str, Any]] = []
    for msg in recent:
        content = msg.get("content", "")
        if len(content) > clip_limit:
            content = content[:clip_limit] + "..."
        selected.append({"role": msg.get("role", "unknown"), "content": content})
    return selected, brief_execute_followup


def _append_recent_chat_history(prompt: str, context: Optional[Dict[str, Any]]) -> str:
    """Legacy text form. No longer used by the prompt builders (history now
    enters the LLM call as role messages via `_extract_history_messages` so
    the system prompt stays byte-stable for provider prompt caching); kept
    for facade/tests compatibility."""
    selected, brief_execute_followup = _select_recent_history(context)
    if not selected:
        return prompt
    lines = [f"[{msg['role']}]: {msg['content']}" for msg in selected]
    header = "=== RECENT CONTINUATION CONTEXT ===" if brief_execute_followup else "=== RECENT CONVERSATION ==="
    return prompt + f"\n{header}\n" + "\n".join(lines)


def _extract_history_messages(
    context: Optional[Dict[str, Any]],
    *,
    current_user_query: Optional[str] = None,
) -> List[Dict[str, str]]:
    """Chat history as OpenAI-style role messages for the LLM call.

    Only user/assistant roles pass through; consecutive same-role items are
    merged (alternation-friendly). A trailing user item identical to the
    current query is dropped to avoid duplicating the live user turn.
    """
    selected, _brief = _select_recent_history(context)
    messages: List[Dict[str, str]] = []
    for msg in selected:
        role = str(msg.get("role") or "").strip().lower()
        if role not in ("user", "assistant"):
            continue
        content = msg["content"]
        if messages and messages[-1]["role"] == role:
            messages[-1]["content"] = f"{messages[-1]['content']}\n{content}"
        else:
            messages.append({"role": role, "content": content})
    if current_user_query and messages and messages[-1]["role"] == "user":
        if messages[-1]["content"].strip() == str(current_user_query).strip():
            messages.pop()
    return messages


def _clip_reference_text(value: Any, *, limit: int = 800) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _append_reference_context(
    cls,
    prompt: str,
    context: Optional[Dict[str, Any]],
) -> str:
    if not context:
        return prompt

    blocks: List[str] = []
    brief_execute_followup = cls._is_brief_execute_followup_context(context)

    user_message = context.get("user_message")
    if isinstance(user_message, str) and user_message.strip():
        blocks.append("=== ORIGINAL USER REQUEST ===")
        blocks.append(cls._clip_reference_text(user_message, limit=1200))

    if brief_execute_followup:
        blocks.append("=== RESPONSE FOCUS ===")
        blocks.append("- Focus on the current execution result or current task outcome.")
        blocks.append("- Do not recap prior project milestones, older runs, or historical progress tables.")
        blocks.append("- Do not append next-step suggestions unless the user explicitly asks for them.")
        blocks.append("- If a continuation summary already names the target file, path, task, or prior blocker, continue from that anchor instead of restarting workspace discovery.")

    continuation_summary = context.get("continuation_summary")
    if brief_execute_followup and isinstance(continuation_summary, dict):
        continuation_lines: List[str] = []
        previous_user_request = cls._clip_reference_text(
            continuation_summary.get("previous_user_request"),
            limit=240,
        )
        if previous_user_request:
            continuation_lines.append(f"- Previous user request: {previous_user_request}")
        previous_assistant_summary = cls._clip_reference_text(
            continuation_summary.get("previous_assistant_summary"),
            limit=280,
        )
        if previous_assistant_summary:
            continuation_lines.append(f"- Previous assistant summary: {previous_assistant_summary}")
        active_subject = cls._clip_reference_text(
            continuation_summary.get("active_subject"),
            limit=240,
        )
        if active_subject:
            continuation_lines.append(f"- Active subject: {active_subject}")
        known_paths = continuation_summary.get("known_paths")
        if isinstance(known_paths, list):
            for path in known_paths[:4]:
                path_text = cls._clip_reference_text(path, limit=240)
                if path_text:
                    continuation_lines.append(f"- Known path anchor: {path_text}")
        known_filenames = continuation_summary.get("known_filenames")
        if isinstance(known_filenames, list):
            filename_texts = [
                cls._clip_reference_text(name, limit=120)
                for name in known_filenames[:4]
                if cls._clip_reference_text(name, limit=120)
            ]
            if filename_texts:
                continuation_lines.append(
                    f"- Known filename anchors: {', '.join(filename_texts)}"
                )
        latest_tool_result = cls._clip_reference_text(
            continuation_summary.get("latest_tool_result"),
            limit=260,
        )
        if latest_tool_result:
            continuation_lines.append(f"- Latest tool result: {latest_tool_result}")
        recent_image_artifacts = continuation_summary.get("recent_image_artifacts")
        if isinstance(recent_image_artifacts, list):
            image_texts = [
                cls._clip_reference_text(item, limit=160)
                for item in recent_image_artifacts[:4]
                if cls._clip_reference_text(item, limit=160)
            ]
            if image_texts:
                continuation_lines.append(
                    f"- Recent image artifacts: {', '.join(image_texts)}"
                )
        last_failure = cls._clip_reference_text(
            continuation_summary.get("last_failure"),
            limit=240,
        )
        if last_failure:
            continuation_lines.append(f"- Last known blocker: {last_failure}")
        if continuation_lines:
            blocks.append("=== EXECUTION CONTINUATION SUMMARY ===")
            blocks.extend(continuation_lines)

    tool_results = context.get("recent_tool_results", [])
    if isinstance(tool_results, list) and tool_results:
        blocks.append("=== RECENT TOOL RESULTS ===")
        recent_items = tool_results[-1:] if brief_execute_followup else tool_results[-3:]
        for item in recent_items:
            if not isinstance(item, dict):
                continue
            tool_name = str(item.get("tool") or item.get("name") or "unknown").strip()
            summary = cls._clip_reference_text(item.get("summary"), limit=240)
            if summary:
                blocks.append(f"- {tool_name}: {summary}")
            else:
                blocks.append(f"- {tool_name}")

    paper_context_paths = context.get("paper_context_paths", [])
    if isinstance(paper_context_paths, list):
        normalized_paths = [
            str(path).strip()
            for path in paper_context_paths
            if str(path).strip()
        ]
        if normalized_paths:
            blocks.append("=== PAPER CONTEXT PATHS ===")
            for path in normalized_paths[:10]:
                blocks.append(f"- {path}")

    if blocks:
        prompt = prompt + "\n" + "\n".join(blocks)
    # Chat history deliberately stays OUT of the system prompt: the builders'
    # callers inject it as role messages (see `_extract_history_messages`) so
    # this prompt keeps a byte-stable prefix for provider prompt caching.
    return prompt


def _build_native_system_prompt(
    agent: "DeepThinkAgent",
    context: Optional[Dict[str, Any]] = None,
    task_context: Optional[TaskExecutionContext] = None,
) -> str:
    """System prompt for native tool calling mode (no JSON formatting rules)."""
    task_preamble = ""
    if task_context and task_context.task_instruction:
        lines = [
            "You are a task execution engine in DeepThink mode.",
            "Focus on completing the specific task with verifiable outputs.",
            "",
            "=== TASK CONTEXT ===",
        ]
        if task_context.task_id is not None:
            lines.append(f"Task ID: {task_context.task_id}")
        if task_context.task_name:
            lines.append(f"Task Name: {task_context.task_name}")
        lines.append(f"Instruction: {task_context.task_instruction}")
        if task_context.constraints:
            lines.append("Constraints:")
            for c in task_context.constraints:
                lines.append(f"- {c}")
        if task_context.plan_outline:
            lines.append(f"Plan Outline (truncated):\n{task_context.plan_outline}")
        if task_context.dependency_outputs:
            lines.append("Dependency Outputs:")
            lines.append("Use these upstream artifact paths and output directories before creating this task's final output; inspect relevant JSON/CSV/TSV/MD/TXT files instead of relying only on dependency summaries.")
            for dep in task_context.dependency_outputs[:6]:
                lines.append(f"- {json.dumps(dep, ensure_ascii=False)[:1200]}")
        if task_context.context_summary:
            lines.append("Task Reference Summary:")
            lines.append(agent._clip_reference_text(task_context.context_summary, limit=1500))
        if task_context.context_sections:
            lines.append("Task Reference Sections:")
            for section in task_context.context_sections[:6]:
                if not isinstance(section, dict):
                    continue
                title = agent._clip_reference_text(section.get("title") or "Section", limit=120)
                content = agent._clip_reference_text(section.get("content"), limit=700)
                lines.append(f"- {title}: {content}")
        if task_context.paper_context_paths:
            lines.append("Paper Context Paths:")
            for path in task_context.paper_context_paths[:10]:
                lines.append(f"- {path}")
        if task_context.skill_context:
            lines.append("")
            lines.append("=== SKILL GUIDANCE ===")
            lines.append(task_context.skill_context)
        lines.append("")
        task_preamble = "\n".join(lines) + "\n"

    prompt = task_preamble + (
        "You are a Deep Thinking AI Assistant.\n"
        "Your goal is to choose the right depth for the user's request: be thorough when needed, but do not over-research simple questions.\n\n"
        + agent._build_shared_strategy_block()
        + agent._build_request_tier_block()
        + agent._build_structured_plan_requirement_block()
        + agent._build_tool_access_block()
        + agent._build_grounded_tooling_block()
        + agent._build_artifact_deliverable_workflow_block()
        + agent._build_bio_tools_quick_map_block()
        + agent._build_plan_artifact_discovery_block(context)
        + agent._build_evidence_scope_block()
        + agent._build_session_isolation_block()
        + "\n"
        + "\n=== WORKFLOW ===\n"
        "1. First decide whether the request needs tools at all.\n"
        "2. For simple conversational or high-level requests, reason briefly and answer directly.\n"
        "3. For evidence-heavy or time-sensitive requests, gather targeted evidence with the minimum necessary tool usage.\n"
        "3a. When the user asks about files, data, or remote job state, prefer at least one relevant tool call before a final answer.\n"
        "4. Call submit_final_answer once the user's request is adequately answered.\n"
        "5. Keep iterative reasoning visible to the user, but concise and relevant.\n\n"
        + "=== AVAILABLE TOOLS ===\n"
        + "\n".join(f"- {tool}" for tool in agent.available_tools)
        + "\n\n"
        + agent._build_protocol_boundary_block("native")
        + "\n=== RULES ===\n"
        "- Do NOT call submit_final_answer prematurely.\n"
        "- Do NOT launch broad web/literature research unless the user asks for sources, latest information, deep analysis, or the task is clearly evidence-sensitive.\n"
        "- For simple requests, prefer zero-tool or one-tool answers; for factual questions, prefer tool verification over guessing.\n"
        "- Keep quick checks synchronous; use background workflows only for clearly long-running operations.\n"
        "- Prioritize directness, relevance, and user intent over maximum comprehensiveness.\n"
        "- Prioritize evidence-backed conclusions over speculation when evidence is actually needed.\n"
        "- Grounding (configs and files): Only report file paths, env vars, API provider names, or model IDs that appear "
        "verbatim in tool outputs from this session. If you intended to read path A but the tool output shows path B was read, "
        "state that mismatch explicitly; do not invent contents for A.\n"
        "- PhageScope: Do not claim remote access, credentials, download capability, or that an optimization 'validated PhageScope' "
        "unless phagescope tool results (e.g. action=ping or task_list) appear in the evidence with success fields.\n"
    )
    return agent._append_reference_context(prompt, context)


def _build_system_prompt(
    agent: "DeepThinkAgent",
    context: Optional[Dict[str, Any]] = None,
    task_context: Optional[TaskExecutionContext] = None,
) -> str:
    """Construct the system prompt for DeepThink, task-aware when available."""
    # Build detailed tool descriptions
    tool_descriptions = {
        "sequence_fetch": (
            "Deterministic accession-to-FASTA downloader. "
            "Use for FASTA downloads by accession IDs before analysis. "
            "Params: {\"accession\": \"NC_001416.1\"} or "
            "{\"accessions\": [\"NC_001416.1\", \"NC_001417.1\"], "
            "\"database\": \"nuccore|protein\", \"format\": \"fasta\"}. "
            "Do not use code_executor as fallback when sequence_fetch fails."
        ),
        "url_fetch": (
            "Public file downloader for direct http/https links. "
            "Use this for downloading a file from a public URL into the current task/session output directory. "
            "Params: {\"url\": \"https://example.com/file.csv\", optional "
            "\"output_name\", \"allowed_content_types\", \"sha256\", \"timeout_sec\", \"max_bytes\"}. "
            "Do not use code_executor for simple public-link downloads."
        ),
        "code_executor": "Execute Python/shell code. FALLBACK TOOL: Use this ONLY when bio_tools cannot handle the task (e.g., custom analysis scripts, complex data processing). For FASTA/FASTQ sequence stats or standard bioinformatics tasks, ALWAYS try bio_tools first. For local CSV/TSV overview/schema/count requests, prefer result_interpreter profile first. Use this for custom computation or visualization when the user needs generated artifacts, not just code snippets. 禁止派本工具的场景：单文件读取、一次性统计/计数/汇总、算术、单张图绘制、只读检查/取证/核验/审计（不修改文件）——普通工具（document_reader、file_operations、result_interpreter）或 5 行 execute_code（kernel 内直接 open()+正则）秒级完成；派一次 = 一次完整 agent 运行（起步 30-60s）。本工具只用于需要完整编码 agent 的复杂实现任务。 Params: {\"task\": \"description\"}",
        "phagescope_research": (
            "Prepare and audit the local PhageScope public dataset for host prediction research. "
            "For local PhageScope dataset exploration, schema/size/readiness assessment, data splitting, model selection, benchmarking, or biological validation, "
            "call action='deep_profile' before final synthesis. Use action='audit' only for compact count checks, "
            "action='research_plan' for a workflow checklist, and action='prepare_metadata_table' to build the ML-ready TSV. "
            "Params: {\"action\": \"deep_profile|audit|research_plan|prepare_metadata_table\", \"data_dir\": \"/path/to/phagescope\"}"
        ),
        "scientific_figure_generator": (
            "PREFERRED tool for scientific/composite figures, plots, charts, visualizations, and publication-style outputs. "
            "Use it when the user asks for PNG/PDF figures, visual summaries, English summary/legend, provenance TSV, QA JSON, or Deliverables publication; do not answer with plotting code when the user asked for an actual figure. "
            "Accepts datasets as inline rows or CSV/TSV/JSON/JSONL paths and panel specs (auto, bar, line, scatter, heatmap, table). "
            "Prefer this over code_executor for standard scientific figure generation."
        ),
        "web_search": "Search the internet for information. USE THIS ONLY for web-based queries, NOT for local files. For broad comparisons, prefer focused parallel subqueries with Params: {\"query\": \"original request\", \"queries\": [\"focused query 1\", \"focused query 2\"]}.",
        "lightrag_query": (
            "PREFERRED knowledge-graph / literature RAG over the large LightRAG corpus. "
            "Use for corpus factual questions, entity/relation evidence, and literature-backed context. "
            "Params: {\"query\": \"your question\", \"mode\": \"mix|hybrid|local|global|naive\", \"top_k\": 5, \"max_chunks\": 12}."
        ),
        "graph_rag": (
            "LEGACY small local triples graph with limited coverage. Prefer lightrag_query. "
            "Params: {\"query\": \"your question\", \"top_k\": 12, \"hops\": 1, \"return_subgraph\": true}."
        ),
        "file_operations": "File system operations: list directories, profile/census directory contents, read/write files, copy/move/delete. USE profile/census before making global all/every/completed claims about large directory trees. Use operation=write when the user asks to save, export, create, or update a Markdown/text/JSON/CSV artifact; do not substitute chat prose for a requested saved file. list/read/profile/census/exists/info are inspection-only in bound execute_task requests and do not count as task completion. Params: {\"operation\": \"profile|census|list|read|write|copy|move|delete\", \"path\": \"/path\"}",
        "document_reader": (
            "Read local documents (.docx, .pdf, .txt, .md). For .csv/.tsv, this tool "
            "returns a built-in preview (headers + sample rows) — use it for quick inspection; "
            "for aggregation, row counts on huge files, or plots use code_executor. "
            "For a bound execute_task request, this is an inspection tool, not a substitute for actually executing the task. "
            "Params: {\"operation\": \"read_any|read_pdf|read_text\", \"file_path\": \"/abs/path\"}"
        ),
        "vision_reader": "Read PDFs and images using vision model. Use for visual OCR/figures/equations, not for DOCX. For a bound execute_task request, this is inspection-only and should not replace the actual execution tool. Params: {\"operation\": \"read_pdf|read_image|ocr_page\", \"file_path\": \"/path/to/file\"}",
        "bio_tools": (
            "PREFERRED for bioinformatics: Execute Docker-based tools for FASTA/FASTQ/sequence analysis. "
            "Example: {\"tool_name\": \"seqkit\", \"operation\": \"stats\", \"input_file\": \"/absolute/path/to/file.fasta\"}. "
            "For inline sequence content, pass sequence_text instead of input_file. "
            "NOTE: input_file SHOULD be absolute path. "
            f"Available tools (synced from tools_config.json): {', '.join(_BIO_TOOLS_NAMES)}. "
            "Use operation='help' first for exact params and prefer operations verified in bio_tool_list.md. "
            "Background policy: use background=true ONLY for long-running bio_tools operations when "
            "the current turn does not require immediate result-dependent reasoning. "
            "Keep short/interactive checks synchronous."
        ),
        "phagescope": """PhageScope cloud platform for phage genome analysis.
IMPORTANT: This is an ASYNC service - tasks run remotely and take minutes to hours.

Connectivity / access checks (user asks to test PhageScope, verify remote connectivity, or confirm download/API):
- FIRST call action=ping (optional base_url only; add token only if the user explicitly provided one). Never use file_operations or local directory listing as a substitute for PhageScope connectivity.
- If ping succeeds and account-scoped verification is needed, use task_list with userid.
- Do not ask for a mandatory "API Token from the user center"; documented flows use `userid`. The tool's `token` param is optional and usually omitted.

Workflow:
1. submit: Submit sequences → Returns taskid immediately (DO NOT wait)
2. task_list: Check all your submitted tasks
3. task_detail: Check specific task status
4. result: Get results ONLY when task is COMPLETED
5. save_all: After Success, use this to write the full local bundle (folders + summary.json). Do not equate `result`/JSON with a complete on-disk package.
6. Batch: `batch_submit` (phage_ids + modulelist; strategy multi_one_task or per_strain) writes a manifest; after Success use `batch_reconcile` (batch_id) to find missing accessions vs phage rows; use `batch_retry` (batch_id) to re-submit missing ids one strain per task. Prefer these over memorizing taskids.

After submit, stop PhageScope result retrieval in this turn unless user explicitly asks to query status only.
After submit, ALWAYS tell user with 3 parts:
- Completed now: submit + taskid
- Running in background: current status/module progress if known
- Next step: refresh status later, then fetch result/save_all/download after completion
DO NOT use wait=True, it will block too long.

Parameter rules (CRITICAL):
- Use `phageid` or `phageids`; do NOT use `sequence` for accession IDs.
- `submit` requires `userid` + `modulelist` + `phageid/phageids`.
- `modulelist` for `submit` must contain real submit modules only. Do NOT put result/output names like `proteins`, `phage_detail`, `phagefasta`, or `tree` into `modulelist`. If protein annotations are needed, request `annotation` and later fetch `result_kind=proteins` or use `save_all`.
- For `bulk_download`, pass datasource names via `phage_ids` (e.g. refseq, genbank) and data type names via `modulelist` (e.g. phage_meta_data, gff3, protein_fasta). Omit both for all datasets.
- `input_check` requires `phageid/phageids`.
- `result` requires `taskid` + `result_kind` (quality/proteins/phage_detail/modules/tree/phagefasta).
- `taskid` must be the numeric remote task id (e.g., 37468), not a local job id like `act_xxx`.

Params: {"action": "submit|task_list|task_detail|result|save_all|download|batch_submit|batch_reconcile|batch_retry|bulk_download", "userid": "...", "phageid": "...", "phageids": "...", "phage_ids": [...], "batch_id": "...", "taskid": "...", "result_kind": "..."}""",
        "result_interpreter": """Data analysis and result interpretation tool.
Can inspect CSV, TSV, MAT, NPY, H5AD, and TXT helper files and only escalates to generated code when the request truly needs calculations, transformations, or plots.

Operations:
- metadata: Extract dataset metadata (columns, types, samples)
- profile: Deterministic dataset profile for row/column counts, sample values, and simple ID overlap
- generate: Generate Python analysis code based on task description
- execute: Execute Python code via Claude Code
- analyze: Full pipeline (metadata → generate → execute with auto-fix)

For quick local inspection (overview, schema, columns, row/column counts, previews), prefer metadata/profile first and keep the path lightweight. Use metadata/profile to inspect existing plan or task outputs before synthesizing reports from large tables. Use analyze only when the user clearly needs code-backed analysis or visualization.

Params for analyze (recommended):
{"operation": "analyze", "file_paths": ["/path/to/data.csv"], "task_title": "Analysis Title", "task_description": "What to analyze"}

Params for metadata:
{"operation": "metadata", "file_path": "/path/to/data.csv"}

Params for profile:
{"operation": "profile", "file_paths": ["/path/to/data.csv", "/path/to/ids.txt"]}

Use this for data exploration, statistical analysis, and visualization tasks on structured data files.""",
        "plan_operation": """Plan creation and optimization tool for structured task planning.

Operations:
- create: Create a new plan with tasks. Params: {"operation": "create", "title": "Plan Title", "description": "Goal", "tasks": [{"name": "Task 1", "instruction": "Details...", "dependencies": ["Task 0"]}]}
- review: Review plan quality and structure. Returns BOTH: (1) structural health_score and (2) a strict research-plan rubric_score with detailed breakdown. Params: {"operation": "review", "plan_id": 123}
- optimize: Apply changes to improve the plan. Params: {"operation": "optimize", "plan_id": 123, "changes": [{"action": "add_task|update_task|update_description|delete_task|reorder_task", ...}]}. If `changes` is omitted, optimize may synthesize changes from the latest rubric feedback.
- get: Get plan details. Params: {"operation": "get", "plan_id": 123}

WORKFLOW for Plan Creation:
1. For a new plan, call 'create' directly from the current context
2. The create path may collect web_search or graph_rag evidence before decomposition when needed
3. Treat the returned plan as already generated/decomposed up to the configured budget; do not assume a second decomposition step is pending
4. After a successful new-plan create, report the result to the user immediately. Do NOT automatically call 'review' or 'optimize' — let the user decide whether to review or improve the plan
5. For a bound plan, use 'get', 'review', or 'optimize' on the existing plan_id
6. Only use 'review' or 'optimize' when the user explicitly requests it (e.g., "review the plan", "optimize it", "improve the plan")
7. Report the real plan result to the user with plan_id and decomposition/rubric status when available

IMPORTANT:
- When creating plans, ensure each task has clear, actionable instructions.
- For optimize/update_task, put editable fields at the top level (name/instruction/dependencies). Do not send only nested updated_fields values.
- If the user asks to update the plan description or rationale summary, use action='update_description' with a top-level description field.
- Do NOT use this tool to mark the currently executing task completed/failed. Current task status is auto-synced from tool execution; use this tool only for structural plan changes.""",
        "terminal_session": """Interactive terminal (PTY shell) for running commands directly.

Operations:
- write: Send command and get output. Params: {"operation": "write", "data": "pwd\\n"}. terminal_id is auto-resolved — no need to call ensure first. Returns {output, status, verification_state, exit_code, ...}. `success` means bytes reached the PTY. `status` is "completed" only when output briefly idles (PTY settle), NOT guaranteed shell success. For local mutations, trust `verification_state`: verified_success / verified_failure / unverified — never infer completion from status="completed" alone.
- replay: Get recent terminal output. Params: {"operation": "replay", "terminal_id": "tid", "limit": 50}
- list: List active terminal sessions. Params: {"operation": "list"}
- close: Close a terminal session. Params: {"operation": "close", "terminal_id": "tid"}
- ensure: Explicitly get or create a terminal. Params: {"operation": "ensure", "session_id": "chat_session_id"}. Returns terminal_id.
- create: Legacy alias for ensure in sandbox/qwen_code flows; if session_id is omitted the system may bind it from the current execution context.

Typical usage: just call write with data — the system handles terminal creation automatically.
IMPORTANT: data must end with \\n to execute the command.""",
        "manuscript_writer": (
            "PREFERRED tool for writing research papers, evidence-based reports, structured summaries, and manuscripts. "
            "Use it when the user asks to generate or save a report/summary from evidence files. "
            "Generates publication-quality LaTeX/Markdown sections with proper citations. "
            "Params: {\"task\": \"write the introduction section\", \"output_path\": \"/abs/path/output.md\", "
            "\"context_paths\": [\"/path/to/refs.bib\", \"/path/to/data.csv\"], "
            "\"analysis_path\": \"/path/to/analysis_results\"}. "
            "IMPORTANT: For ANY paper/manuscript/report/summary writing task that should create a file (sections, drafts, revisions, assembly), "
            "ALWAYS use manuscript_writer instead of code_executor. "
            "code_executor should NEVER be used to write paper content directly."
        ),
        "literature_pipeline": (
            "Collect a literature evidence pack from PubMed/PMC. "
            "Returns references.bib, evidence.md, and library.jsonl for downstream use. "
            "Params: {\"query\": \"pseudomonas phage\", optional \"max_results\", \"download_pdfs\", \"session_id\"}."
        ),
        "review_pack_writer": (
            "Generate a literature-backed review draft by chaining literature_pipeline "
            "and manuscript_writer. "
            "Params: {\"topic\": \"Pseudomonas phage\", optional \"query\", \"max_results\", "
            "\"download_pdfs\", \"sections\", \"max_revisions\", \"evaluation_threshold\", \"session_id\"}."
        ),
        "deliverable_submit": (
            "Promote specific files into the session Deliverables bundle. "
            "Params: {\"publish\": true|false, \"artifacts\": [{\"path\": \"/path/to/file\", "
            "\"module\": \"code|image_tabular|paper|refs|docs\", optional \"reason\": \"note\"}]}. "
            "Use this after files already exist and the user wants reports, summaries, figures, tables, code, or references included in Deliverables."
        ),
    }
    if code_mode_enabled():
        # Code mode joins the legacy prompt catalog only when explicitly
        # enabled — same gate as get_all_tools()/tool_schemas, so the entry is
        # invisible (and code mode undiscoverable) when disabled.
        tool_descriptions["execute_code"] = (
            "Run Python that calls GAgent tools programmatically in a PERSISTENT kernel. "
            "Use when you need 3+ tool calls with logic between them: loops over pages/files/accessions, "
            "filtering or reducing large tool outputs BEFORE they enter your context, branching, or retries; "
            "use a normal tool call for a single call or results you must reason over in full. "
            "The kernel keeps variables, imports, and loaded data across execute_code calls "
            "(pass reset=true to start fresh); a timed-out or interrupted call kills the kernel and loses that state. "
            "Tools are importable Python functions, e.g. `from gagent_tools import web_search`; "
            "each returns an ALREADY-PARSED dict — never json.loads() it. "
            "Params: {\"code\": \"from gagent_tools import web_search\\nrows = web_search(query='phage lysin')\\nprint(rows)\", "
            "optional \"reset\": true|false}."
        )
    if delegate_task_enabled():
        # General sub-agent delegation: same gate as get_all_tools()/tool_schemas
        # so the entry is invisible (and the tool undiscoverable) when disabled.
        tool_descriptions["delegate_task"] = (
            "Hand ONE self-contained, long-horizon workflow to an ISOLATED sub-agent and get back "
            "only a summary plus artifact paths — the sub-agent keeps its own context, does not see "
            "this conversation, and its transcript never enters yours. "
            "Use it for long self-contained work you do not need to watch (multi-file refactors, "
            "audit-and-repair passes, bulk literature or accession sweeps). "
            "Division of labor: code_executor hands off a CODING task to the pi harness; execute_code "
            "is YOU writing Python in a kernel you keep using; delegate_task hands off a GOAL. "
            "Do NOT use it for a single query or two tool calls, for read-only checking/counting/"
            "printing of results you already have, when you must judge the intermediate results "
            "yourself, when you need the current kernel state, or when the code itself is the "
            "deliverable (use code_executor). Calls run one at a time — no parallel fan-out. "
            "Params: {\"goal\": \"audit every Python file under data/pipeline for the removed "
            "pandas.append API, fix it, and report the changed files\", optional "
            "\"deliverable\": \"patched files + report\", \"context_paths\": [\"data/pipeline\"]}. "
            "Returns {summary, artifact_paths, usage, trace_ref}; raw stdout/stderr is never returned."
        )

    tools_desc = []
    for t in agent.available_tools:
        if t in tool_descriptions:
            tools_desc.append(f"- {t}: {tool_descriptions[t]}")
        else:
            tools_desc.append(f"- {t}")
    tools_text = "\n".join(tools_desc)

    if task_context and task_context.task_instruction:
        task_lines = [
            "You are a task execution engine in DeepThink mode.",
            "Focus on completing the specific task with verifiable outputs.",
            "Be concise, deterministic, and robust.",
            "",
            "=== TASK EXECUTION CONTEXT ===",
        ]
        if task_context.task_id is not None:
            task_lines.append(f"Task ID: {task_context.task_id}")
        if task_context.task_name:
            task_lines.append(f"Task Name: {task_context.task_name}")
        task_lines.append(f"Instruction: {task_context.task_instruction}")
        if task_context.constraints:
            task_lines.append("Constraints:")
            for c in task_context.constraints:
                task_lines.append(f"- {c}")
        if task_context.plan_outline:
            task_lines.append("Plan Outline (truncated):")
            task_lines.append(task_context.plan_outline)
        if task_context.dependency_outputs:
            task_lines.append("Dependency Outputs:")
            task_lines.append("Use these upstream artifact paths and output directories before creating this task's final output; inspect relevant JSON/CSV/TSV/MD/TXT files instead of relying only on dependency summaries.")
            for dep in task_context.dependency_outputs[:6]:
                task_lines.append(f"- {json.dumps(dep, ensure_ascii=False)[:1200]}")
        if task_context.context_summary:
            task_lines.append("Task Reference Summary:")
            task_lines.append(agent._clip_reference_text(task_context.context_summary, limit=1500))
        if task_context.context_sections:
            task_lines.append("Task Reference Sections:")
            for section in task_context.context_sections[:6]:
                if not isinstance(section, dict):
                    continue
                title = agent._clip_reference_text(section.get("title") or "Section", limit=120)
                content = agent._clip_reference_text(section.get("content"), limit=700)
                task_lines.append(f"- {title}: {content}")
        if task_context.paper_context_paths:
            task_lines.append("Paper Context Paths:")
            for path in task_context.paper_context_paths[:10]:
                task_lines.append(f"- {path}")
        if task_context.skill_context:
            task_lines.append("")
            task_lines.append("=== SKILL GUIDANCE ===")
            task_lines.append(task_context.skill_context)
        task_lines.append("")
        base_prompt = "\n".join(task_lines) + "\n"
    else:
        base_prompt = ""

    base_prompt += f"""You are a Deep Thinking AI Assistant.
Your goal is to choose the right depth for the user's request: be thorough when needed, but do not over-research simple questions.

{agent._build_shared_strategy_block()}
{agent._build_request_tier_block()}
{agent._build_structured_plan_requirement_block()}
{agent._build_tool_access_block()}
{agent._build_grounded_tooling_block()}
{agent._build_artifact_deliverable_workflow_block()}
{agent._build_bio_tools_quick_map_block()}
{agent._build_plan_artifact_discovery_block(context)}
{agent._build_evidence_scope_block()}
{agent._build_session_isolation_block()}
=== THINKING WORKFLOW ===
1. First classify whether the request needs tools, targeted evidence, or just a direct answer.
2. Break the query into sub-problems only when that materially helps.
3. Use tools selectively and synthesize only the evidence needed for the user's request.
4. Provide final_answer once the request is adequately answered.

=== AVAILABLE TOOLS ===
{tools_text}

=== BIO_TOOLS OPERATING RULES ===
- For accession-based FASTA download, call sequence_fetch first and use its output_file for downstream steps.
- For FASTA/FASTQ/sequence tasks, start with bio_tools (typically seqkit stats for first-pass diagnostics).
- Use operation="help" before first use of uncertain operations; do not guess parameters.
- If routing remains uncertain after help, use focused web_search and retry bio_tools.
- Keep quick checks synchronous; use background=true only for clearly long-running jobs and return job_id for job_status follow-ups.
- Bio_tools catalog (synced from tools_config.json): {_BIO_TOOLS_CATALOG_TEXT}

=== BIO_TOOLS RECOVERY PROTOCOL (MANDATORY) ===
1. Call bio_tools(..., operation="help") and inspect required parameters.
2. Retry bio_tools with corrected parameters and verified absolute paths.
3. If still failing, run targeted web_search for operation/parameter mapping.
4. If still failing for reasons other than input parsing/conversion, use code_executor for minimal shell-level diagnostics.
Try at least 3 different recovery attempts before reporting failure.

=== PLAN CREATION RULE ===
Research before planning only when current external best practices or factual verification materially affect the plan. Otherwise create or update the structured plan directly from the current context first.

{agent._build_protocol_boundary_block("legacy")}
=== OUTPUT FORMAT ===
Respond with valid JSON only (no markdown fences):

{{
  "thinking": "What you are analyzing and why...",
  "action": {{"tool": "tool_name", "params": {{...}}}},
  "final_answer": null
}}

When ready to answer:
{{
  "thinking": "Synthesis based on tool evidence...",
  "action": null,
  "final_answer": {{"answer": "Comprehensive answer here", "confidence": 0.9}}
}}

=== RULES ===
1. Output valid JSON only.
2. Use action to call one tool per response.
3. Do NOT call tools for greetings, casual follow-ups, simple explanations, or opinion-style questions unless the user explicitly asks for research or sources.
4. Call MULTIPLE tools only when evidence gathering is genuinely required.
5. Include key tool evidence before concluding when evidence was used.
6. For PhageScope submit, prioritize non-blocking backend execution over immediate result fetching.
"""
    return agent._append_reference_context(base_prompt, context)


def _get_next_step_prompt(agent: "DeepThinkAgent", iteration: int) -> str:
    """Generate prompt for the next step, encouraging completion if steps are getting long."""
    tier = agent._request_tier()
    if tier == "standard":
        return (
            'Prefer answering now. If you still need file or tool evidence, call the tool now; '
            'otherwise call submit_final_answer. Do not output transitional narration as a standalone response.'
        )
    if tier == "research":
        return (
            'Before concluding research, check whether a successful literature_pipeline already produced '
            'study_cards.jsonl, library.jsonl, evidence.md, study_matrix.md, or a coverage report. '
            'If so, read the relevant records with file_operations before claiming insufficient or unconfirmed evidence; '
            'then call submit_final_answer with only what those records support.'
        )
    if iteration >= agent.max_iterations - 1:
        return (
            'CRITICAL: This is your LAST step. You MUST call submit_final_answer NOW with the best answer '
            'you can provide based on all evidence gathered. Do NOT call any more tools — synthesize and submit.'
        )
    gentle_nudge = agent.max_iterations // 2
    strong_nudge = int(agent.max_iterations * 0.75)
    if iteration > strong_nudge:
        return (
            'You have taken many steps. Consolidate what you already know and call submit_final_answer NOW. '
            'Do NOT continue researching unless one more targeted tool call is absolutely essential.'
        )
    elif iteration > gentle_nudge:
        return (
            'Check whether the user is already adequately answered. If yes, call submit_final_answer now. '
            'Continue only if another step materially improves accuracy.'
        )
    else:
        return 'Before continuing, ask whether another step or tool call is truly needed. If the current information is enough, call submit_final_answer now.'
