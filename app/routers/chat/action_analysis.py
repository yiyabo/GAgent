"""Math-repair / verification-analysis / summary cluster of ``action_execution``.

Moved out of ``action_execution.py`` per
design/2026-09-24-backend-godfiles-refactor-plan.md §4.7 (execution cluster ①):
the distribution-summary math repair, the artifact gallery/file adapters, the
deterministic contract-verification analysis builders (incl. the public
``build_phagescope_deep_profile_analysis``), the summary-text helpers
(``truncate_summary_text`` / ``build_actions_summary`` / ``append_summary_to_reply``)
and — appended by the second half of this cluster — the LLM tool/action
analysis generators.

Everything is re-exported by ``action_execution``, so ``chat/__init__.py``,
``agent.py`` and the tests keep importing the same names.

Patch surface: none of the moved names is patched, and the only facade bindings
they referenced (``truncate_summary_text``) moved with them — so this cluster
carries **zero body deviations**.  The LLM generators appended later do read
``_get_llm_service_for_provider`` through ``_ae()`` because that name *is*
patched on the action_execution namespace
(app/tests/chat/test_action_execution_summary_math.py:60).

No logger is used by the part-1 functions; the LLM generators appended in
part 2 use this module's own ``logging.getLogger(__name__)``, with byte-identical
messages.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.services.response_style import (
    PROFESSIONAL_STYLE_INSTRUCTION,
    sanitize_professional_response_text,
)

from .artifact_gallery import (
    extract_artifact_files_from_result,
    extract_artifact_gallery_from_result,
    merge_artifact_gallery,
)

logger = logging.getLogger(__name__)


def _ae() -> Any:
    """Late-bound action_execution facade module (monkeypatch-friendly lookups)."""
    from . import action_execution

    return action_execution


_DISTRIBUTION_TOTAL_PATTERNS = (
    re.compile(r"共\s*([\d,]+)\s*条(?:记录|数据|样本|序列)?"),
    re.compile(r"total\s*[:=]?\s*([\d,]+)\s*(?:records?|rows?|entries?)", re.IGNORECASE),
)
_INLINE_COUNT_PATTERN = re.compile(r"(?:合计|共)\s*([\d,]+)\s*条")
_PERCENT_PATTERN = re.compile(r"(\d+(?:\.\d+)?)%")


def _parse_int_token(value: Any) -> Optional[int]:
    text = str(value or "").strip().replace(",", "")
    if not text.isdigit():
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _format_percent(value: float) -> str:
    return f"{value:.1f}%"


def _repair_distribution_summary_math(text: Optional[str]) -> Optional[str]:
    raw = str(text or "")
    if not raw.strip() or "%" not in raw:
        return text

    lines = raw.splitlines()
    table_counts_total = 0

    for line in lines:
        stripped = line.strip()
        if not (stripped.startswith("|") and stripped.endswith("|")):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) < 3:
            continue
        if cells[0].startswith("---") or cells[1].startswith("---"):
            continue
        count = _parse_int_token(cells[1])
        if count is None:
            continue
        table_counts_total += count

    total: Optional[int] = None
    for pattern in _DISTRIBUTION_TOTAL_PATTERNS:
        match = pattern.search(raw)
        if not match:
            continue
        total = _parse_int_token(match.group(1))
        if total:
            break
    if total is None and table_counts_total > 0:
        total = table_counts_total
    if not total:
        return text

    repaired_lines: List[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if len(cells) >= 3 and not (cells[0].startswith("---") or cells[1].startswith("---")):
                count = _parse_int_token(cells[1])
                if count is not None:
                    expected_percent = _format_percent((count / total) * 100.0)
                    cells[2] = expected_percent
                    line = "| " + " | ".join(cells) + " |"

        count_matches = list(_INLINE_COUNT_PATTERN.finditer(line))
        pct_matches = list(_PERCENT_PATTERN.finditer(line))
        if len(count_matches) == 1 and len(pct_matches) == 1:
            count = _parse_int_token(count_matches[0].group(1))
            if count is not None:
                expected_percent = _format_percent((count / total) * 100.0)
                start, end = pct_matches[0].span(0)
                current_percent = pct_matches[0].group(0)
                if current_percent != expected_percent:
                    line = line[:start] + expected_percent + line[end:]

        repaired_lines.append(line)

    return "\n".join(repaired_lines)


def _build_artifact_gallery_from_tool_results(
    tool_results_payload: List[Dict[str, Any]],
    *,
    session_id: Optional[str],
    tracking_id: Optional[str],
) -> List[Dict[str, Any]]:
    gallery: List[Dict[str, Any]] = []
    for item in tool_results_payload or []:
        if not isinstance(item, dict):
            continue
        result_payload = item.get("result")
        if not isinstance(result_payload, dict):
            continue
        extracted = extract_artifact_gallery_from_result(
            result_payload,
            session_id=session_id,
            source_tool=item.get("name") or item.get("tool"),
            tracking_id=tracking_id,
        )
        if extracted:
            gallery = merge_artifact_gallery(gallery, extracted)
    return gallery


def _build_artifact_files_from_tool_results(
    tool_results_payload: List[Dict[str, Any]],
    *,
    session_id: Optional[str],
    tracking_id: Optional[str],
) -> List[Dict[str, Any]]:
    files: List[Dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in tool_results_payload or []:
        if not isinstance(item, dict):
            continue
        result_payload = item.get("result")
        if not isinstance(result_payload, dict):
            continue
        # A failed tool run's "output paths" describe files that were never written.
        if result_payload.get("success") is False:
            continue
        extracted = extract_artifact_files_from_result(
            result_payload,
            session_id=session_id,
            source_tool=item.get("name") or item.get("tool"),
            tracking_id=tracking_id,
        )
        for entry in extracted:
            key = (str(entry.get("origin") or ""), str(entry.get("path") or ""))
            if key in seen:
                continue
            seen.add(key)
            files.append(entry)
    return files[:8]


def truncate_summary_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    return str(value)


def _latest_verification_result_payload(
    tool_results_payload: List[Dict[str, Any]],
) -> Tuple[Optional[Dict[str, Any]], str]:
    latest_result_payload: Optional[Dict[str, Any]] = None
    latest_verification_status = ""

    for item in reversed(tool_results_payload or []):
        if not isinstance(item, dict):
            continue
        result_payload = item.get("result")
        if not isinstance(result_payload, dict):
            continue
        verification_status = str(result_payload.get("verification_status") or "").strip().lower()
        if not verification_status:
            artifact_verification = result_payload.get("artifact_verification")
            if isinstance(artifact_verification, dict):
                verification_status = str(artifact_verification.get("status") or "").strip().lower()
        if not verification_status:
            continue
        latest_result_payload = result_payload
        latest_verification_status = verification_status
        break

    return latest_result_payload, latest_verification_status


def _format_artifact_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


def _resolve_verified_output_rows(result_payload: Dict[str, Any]) -> List[Dict[str, str]]:
    artifact_verification = result_payload.get("artifact_verification")
    if not isinstance(artifact_verification, dict):
        return []

    raw_outputs = artifact_verification.get("verified_outputs")
    if not isinstance(raw_outputs, list) or not raw_outputs:
        raw_outputs = artifact_verification.get("actual_outputs")
    if not isinstance(raw_outputs, list) or not raw_outputs:
        raw_outputs = artifact_verification.get("expected_deliverables")
    if not isinstance(raw_outputs, list) or not raw_outputs:
        return []

    base_dir_value = (
        result_payload.get("task_directory_full")
        or result_payload.get("run_directory")
        or result_payload.get("working_directory")
    )
    base_dir = Path(str(base_dir_value).strip()).expanduser() if str(base_dir_value or "").strip() else None

    rows: List[Dict[str, str]] = []
    seen: set[str] = set()
    for raw_output in raw_outputs[:40]:
        label = str(raw_output or "").strip().replace("\\", "/")
        if not label:
            continue
        path = Path(label).expanduser()
        if not path.is_absolute():
            if base_dir is None:
                continue
            path = base_dir / path
        try:
            resolved = path.resolve(strict=False)
        except Exception:
            resolved = path
        absolute_path = str(resolved)
        if absolute_path in seen:
            continue
        seen.add(absolute_path)

        size_text = "unknown"
        try:
            if resolved.exists() and resolved.is_file():
                size_text = _format_artifact_size(int(resolved.stat().st_size))
        except OSError:
            pass

        rows.append(
            {
                "file": label,
                "absolute_path": absolute_path,
                "size": size_text,
            }
        )
    return rows


def _build_contract_verification_success_analysis(
    user_message: str,
    tool_results_payload: List[Dict[str, Any]],
) -> Optional[str]:
    language = "zh" if re.search(r"[\u4e00-\u9fff]", user_message or "") else "en"
    latest_result_payload, latest_verification_status = _latest_verification_result_payload(
        tool_results_payload
    )
    if latest_verification_status != "passed" or not isinstance(latest_result_payload, dict):
        return None

    rows = _resolve_verified_output_rows(latest_result_payload)
    if not rows:
        return None

    if language == "zh":
        lines = [
            "已通过确定性交付物校验。以下交付物已在本次运行目录中被物理验证为存在且非空：",
            "",
            "| # | File | Absolute Path | Size |",
            "|---|---|---|---|",
        ]
    else:
        lines = [
            "Deterministic artifact verification passed. The following deliverables were physically verified as present and non-empty in this run directory:",
            "",
            "| # | File | Absolute Path | Size |",
            "|---|---|---|---|",
        ]

    for index, row in enumerate(rows, start=1):
        lines.append(
            f"| {index} | `{row['file']}` | `{row['absolute_path']}` | {row['size']} |"
        )

    return "\n".join(lines)


def _build_contract_verification_analysis(
    user_message: str,
    tool_results_payload: List[Dict[str, Any]],
) -> Optional[str]:
    language = "zh" if re.search(r"[\u4e00-\u9fff]", user_message or "") else "en"
    latest_result_payload, latest_verification_status = _latest_verification_result_payload(
        tool_results_payload
    )

    if latest_verification_status != "failed" or not isinstance(latest_result_payload, dict):
        return None

    contract_diff = latest_result_payload.get("contract_diff")
    if not isinstance(contract_diff, dict):
        contract_diff = {}
    artifact_verification = latest_result_payload.get("artifact_verification")
    produced_files = latest_result_payload.get("produced_files") or []
    if not isinstance(produced_files, list):
        produced_files = []
    if not produced_files and isinstance(artifact_verification, dict):
        produced_files = list(artifact_verification.get("actual_outputs") or [])

    missing = [str(item) for item in contract_diff.get("missing_required_outputs") or [] if str(item).strip()]
    wrong_format = [str(item) for item in contract_diff.get("wrong_format_outputs") or [] if str(item).strip()]
    unexpected = [str(item) for item in contract_diff.get("unexpected_outputs") or [] if str(item).strip()]
    actual = []
    for raw in produced_files:
        text = str(raw or "").strip()
        if not text:
            continue
        actual.append(Path(text).name if "/" in text or "\\" in text else text)

    if language == "zh":
        lines = ["确定性产物校验未通过：本次生成结果与任务要求的交付物 contract 不一致。"]
        if missing:
            lines.append(f"缺失的必需输出：{', '.join(missing[:6])}")
        if wrong_format:
            lines.append(f"格式不匹配的输出：{', '.join(wrong_format[:6])}")
        if unexpected:
            lines.append(f"额外生成但不在 contract 内的输出：{', '.join(unexpected[:6])}")
        if actual:
            lines.append(f"本次实际观察到的输出：{', '.join(actual[:8])}")
        lines.append("因此当前任务不能判定为已完成；应先修复产物路径、文件名或格式，再重新执行。")
        return "\n".join(lines)

    lines = ["Deterministic artifact verification failed: the generated outputs do not satisfy the task contract."]
    if missing:
        lines.append(f"Missing required outputs: {', '.join(missing[:6])}")
    if wrong_format:
        lines.append(f"Wrong-format outputs: {', '.join(wrong_format[:6])}")
    if unexpected:
        lines.append(f"Unexpected outputs: {', '.join(unexpected[:6])}")
    if actual:
        lines.append(f"Actual observed outputs: {', '.join(actual[:8])}")
    lines.append("This task should not be reported as completed until the artifact contract is satisfied.")
    return "\n".join(lines)


def build_phagescope_deep_profile_analysis(
    user_message: str,
    tool_results_payload: List[Dict[str, Any]],
) -> Optional[str]:
    language = "zh" if re.search(r"[\u4e00-\u9fff]", user_message or "") else "en"
    profile: Optional[Dict[str, Any]] = None
    for item in tool_results_payload or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("name") or item.get("tool") or "").strip() != "phagescope_research":
            continue
        result = item.get("result")
        if not isinstance(result, dict):
            continue
        if str(result.get("action") or "").strip().lower() != "deep_profile":
            continue
        if result.get("success") is False:
            continue
        profile = result
        break
    if profile is None:
        return None

    rows = profile.get("metadata_rows")
    files = profile.get("metadata_files")
    meta_size = profile.get("metadata_size_human") or "unknown"
    total_size = profile.get("total_size_human") or "unknown"
    unique_ids = profile.get("unique_phage_ids")
    ml_table = profile.get("ml_metadata_table") if isinstance(profile.get("ml_metadata_table"), dict) else {}
    schema = profile.get("metadata_schema") if isinstance(profile.get("metadata_schema"), dict) else {}
    label_quality = profile.get("label_quality") if isinstance(profile.get("label_quality"), dict) else {}
    split_readiness = profile.get("split_readiness") if isinstance(profile.get("split_readiness"), dict) else {}
    ml_empty = bool(ml_table.get("empty_or_header_only"))
    headers_consistent = schema.get("headers_consistent")
    host_available = label_quality.get("host_available_rows")
    host_missing = label_quality.get("host_missing_rows")
    primary_split = split_readiness.get("recommended_primary_split") or "subcluster"
    robustness_split = split_readiness.get("robustness_split") or "cluster"

    if language == "zh":
        return "\n".join(
            [
                "我已经基于 `phagescope_research` 的 `deep_profile` 结果理解了本地 PhageScope 数据集；这不是建计划结果。",
                "",
                f"证据边界：`meta_data/` 有 {files} 个 metadata TSV、{rows} 行记录，metadata 体积是 {meta_size}；整个数据集目录体积是 {total_size}；不能把 metadata 体积误写成接近整个目录体积。唯一 phage ID 数为 {unique_ids}。",
                f"当前 ML-ready 表状态：`ml_metadata_table.empty_or_header_only={str(ml_empty).lower()}`；metadata header 一致性为 `{str(headers_consistent).lower()}`。Host 标签可用行约为 {host_available}，缺失 Host 行为 {host_missing}。",
                "",
                f"Data splitting：主划分应使用 `{primary_split}` 分组，稳健性/敏感性分析用 `{robustness_split}`；避免随机行级 split，因为相近 phage 会造成泄漏。",
                "Model selection：先做 metadata-only baseline（RandomForest、ExtraTrees 或线性/树模型），再逐步加入 k-mer 与 annotation-derived count features；不要先上复杂深度模型掩盖数据泄漏和标签噪声。",
                "Benchmarking：报告 majority baseline、macro-F1、weighted-F1、top-k accuracy、source-stratified 和 lifestyle-stratified 指标；所有指标必须绑定固定 split。",
                "Biological validation：重点检查 Host 标签来源、Taxonomy/Host 潜在泄漏、Cluster/Subcluster 泄漏、Lifestyle/Completeness 分层表现，并把稀有宿主类别作为限制说明。",
            ]
        )

    return "\n".join(
        [
            "I used `phagescope_research` `deep_profile` evidence for the local PhageScope dataset; this is not a plan-creation result.",
            "",
            f"Evidence boundary: `meta_data/` contains {files} metadata TSV files and {rows} rows, with metadata size {meta_size}; the whole dataset directory is {total_size}. Do not inflate metadata size toward whole-directory size. Unique phage IDs: {unique_ids}.",
            f"Current ML-ready table state: `ml_metadata_table.empty_or_header_only={str(ml_empty).lower()}`; metadata header consistency is `{str(headers_consistent).lower()}`. Host labels are available for about {host_available} rows, with {host_missing} missing Host rows.",
            "",
            f"Data splitting: use `{primary_split}` as the primary grouping key and `{robustness_split}` for robustness analysis; avoid random row-level splits because related phages can leak across splits.",
            "Model selection: start with metadata-only baselines such as RandomForest, ExtraTrees, or simple linear/tree models, then add k-mer and annotation-derived count features stepwise.",
            "Benchmarking: report majority baseline, macro-F1, weighted-F1, top-k accuracy, source-stratified metrics, and lifestyle-stratified metrics on the fixed split.",
            "Biological validation: audit Host label provenance, Taxonomy/Host leakage, Cluster/Subcluster leakage, Lifestyle/Completeness strata, and rare-host limitations.",
        ]
    )


def build_actions_summary(agent: Any, steps: List["AgentStep"]) -> List[Dict[str, Any]]:
    summary: List[Dict[str, Any]] = []
    for step in steps:
        action = step.action
        summary.append({
            "order": action.order,
            "kind": action.kind,
            "name": action.name,
            "success": step.success,
            "message": truncate_summary_text(step.message),
        })
    return summary


def append_summary_to_reply(
    agent: Any, reply: str, summary: List[Dict[str, Any]]
) -> str:
    # Do not append action summary at the end of replies:
    # the frontend already provides status tags and a "View process" panel.
    # Keep this method signature for backward compatibility.
    return reply


async def _generate_tool_analysis(
    user_message: str,
    tool_results: List[Dict[str, Any]],
    session_id: Optional[str] = None,
    llm_provider: Optional[str] = None,
) -> Optional[str]:
    """Use the LLM to generate a detailed analysis from tool results."""
    try:
        tools_description = []
        web_search_items: List[Dict[str, str]] = []
        for idx, tool_result in enumerate(tool_results, 1):
            tool_name = tool_result.get("name", "unknown")
            summary = tool_result.get("summary", "")
            result_data = tool_result.get("result", {})

            tool_desc = f"{idx}. Tool: {tool_name}"
            if summary:
                tool_desc += f"\n   Execution summary: {summary}"

            if isinstance(result_data, dict):
                useful_fields = [
                    "output",
                    "stdout",
                    "stderr",
                    "success",
                    "error",
                    "produced_files_count",
                    "verification_status",
                    "failure_kind",
                ]
                for field in useful_fields:
                    if field in result_data and result_data[field]:
                        value = result_data[field]
                        tool_desc += f"\n   {field}: {value}"
                if tool_name == "web_search":
                    results = result_data.get("results")
                    response_text = result_data.get("response") or result_data.get("answer")
                    if isinstance(response_text, str) and response_text.strip():
                        clip = response_text.strip()
                        if len(clip) > 2000:
                            clip = clip[:1997] + "..."
                        tool_desc += f"\n   response (may lack structured citations): {clip}"
                    if isinstance(results, list) and results:
                        tool_desc += "\n   results:"
                        for item in results:
                            if not isinstance(item, dict):
                                continue
                            title = str(item.get("title") or "").strip()
                            url = str(item.get("url") or "").strip()
                            snippet = str(item.get("snippet") or "").strip()
                            tool_desc += f"\n   - title: {title}"
                            tool_desc += f"\n     url: {url}"
                            if snippet:
                                tool_desc += f"\n     snippet: {snippet}"
                            web_search_items.append(
                                {
                                    "title": title,
                                    "url": url,
                                }
                            )
                    else:
                        tool_desc += (
                            "\n   results: (empty — no structured URL list from the search tool; "
                            "do not treat the summary as independently verifiable without links.)"
                        )
                else:
                    details_payload = {}
                    for field in (
                        "data",
                        "results",
                        "action",
                        "status_code",
                        "result_kind",
                        "produced_files",
                        "artifact_paths",
                        "contract_diff",
                        "artifact_verification",
                    ):
                        if field in result_data and result_data[field] is not None:
                            details_payload[field] = result_data[field]
                    if details_payload:
                        details_text = json.dumps(details_payload, ensure_ascii=True)
                        if len(details_text) > 1200:
                            details_text = details_text[:1197] + "..."
                        tool_desc += f"\n   details: {details_text}"

            tools_description.append(tool_desc)

        tools_text = "\n\n".join(tools_description)

        analysis_requirements = [
            "1. Provide a complete, in-depth analysis; do not repeat the user question.",
            "2. Clearly separate conclusions, evidence, and caveats/risks.",
            "3. If web_search is involved, list each result (title + url) and explain its value; "
            "if results are empty, state that no verifiable links were returned and avoid presenting claims as confirmed.",
            "4. If errors or uncertainty exist, explain why and propose next steps.",
            "5. Output at least 6 bullet points or 3 natural paragraphs.",
            "6. Use only fields present in the tool outputs; do not invent paths, modules, or metrics.",
            "7. If a field is missing, explicitly say it was not provided by the tool.",
            "8. Prefer factual summaries over speculation.",
        ]

        base_prompt = (
            "You are a senior analysis assistant. Below are the user question and tool execution results.\n"
            "Write a detailed analysis body that can be shown directly as the final answer.\n\n"
            f"User question: {user_message}\n\n"
            "Tool execution results:\n"
            f"{tools_text}\n\n"
            "Requirements:\n"
            + "\n".join(analysis_requirements)
            + f"\n9. {PROFESSIONAL_STYLE_INSTRUCTION}"
            + "\n\nOutput analysis:"
        )
        llm_service = _ae()._get_llm_service_for_provider(llm_provider)

        analysis = await llm_service.chat_async(base_prompt)
        if not analysis:
            return None
        cleaned = sanitize_professional_response_text(analysis.strip())
        return _repair_distribution_summary_math(cleaned)

    except Exception as exc:
        logger.error(
            "[CHAT][SUMMARY] Failed to generate analysis for session=%s: %s",
            session_id,
            exc,
        )
        return None


async def _generate_tool_summary(
    user_message: str,
    tool_results: List[Dict[str, Any]],
    session_id: Optional[str] = None,
    llm_provider: Optional[str] = None,
) -> Optional[str]:
    """Use the LLM to generate a short summary from tool results (for process panel)."""
    try:
        tools_description = []
        for idx, tool_result in enumerate(tool_results, 1):
            tool_name = tool_result.get("name", "unknown")
            summary = tool_result.get("summary", "")
            tool_desc = f"{idx}. {tool_name}"
            if summary:
                tool_desc += f" - {summary}"
            tools_description.append(tool_desc)

        tools_text = "\n".join(tools_description)
        prompt = (
            "You are a project assistant. Provide a brief summary (1-3 sentences) based on tool execution.\n"
            f"User question: {user_message}\n"
            f"Tool execution overview:\n{tools_text}\n"
            f"{PROFESSIONAL_STYLE_INSTRUCTION}\n"
            "Output summary:"
        )
        llm_service = _ae()._get_llm_service_for_provider(llm_provider)
        summary = await llm_service.chat_async(prompt)
        return sanitize_professional_response_text(summary.strip()) if summary else None
    except Exception as exc:
        logger.error(
            "[CHAT][SUMMARY] Failed to generate brief summary for session=%s: %s",
            session_id,
            exc,
        )
        return None


def _collect_created_tasks_from_steps(steps: List[Any]) -> List[Dict[str, Any]]:
    created: List[Dict[str, Any]] = []
    for step in steps:
        details = step.details or {}
        created_nodes = details.get("created")
        if isinstance(created_nodes, list):
            for node in created_nodes:
                if isinstance(node, dict):
                    created.append(node)
        task_node = details.get("task")
        if isinstance(task_node, dict):
            created.append(task_node)
    return created


async def _generate_action_analysis(
    user_message: str,
    steps: List[Any],
    session_id: Optional[str] = None,
    llm_provider: Optional[str] = None,
) -> Optional[str]:
    created_tasks = _collect_created_tasks_from_steps(steps)

    step_summaries: List[str] = []
    for step in steps:
        if not step.success:
            continue
        details = step.details or {}
        kind = step.action.kind or ""
        name = step.action.name or ""
        msg = step.message or ""
        if kind in ("plan_operation", "task_operation"):
            detail_text = msg
            if isinstance(details, dict):
                for key in ("plans", "outline", "task", "plan_id", "task_count"):
                    val = details.get(key)
                    if val is not None:
                        detail_text += (
                            f"\n{key}: "
                            f"{json.dumps(val, ensure_ascii=False, default=str)[:2000]}"
                        )
            step_summaries.append(f"[{kind}/{name}] {detail_text}")

    if created_tasks:
        lines: List[str] = []
        for idx, task in enumerate(created_tasks, 1):
            task_name = str(task.get("name") or task.get("title") or "").strip()
            instruction = str(task.get("instruction") or "").strip()
            if task_name:
                lines.append(f"{idx}. {task_name}")
            if instruction:
                lines.append(f"   - Instruction: {instruction}")
        tasks_text = "\n".join(lines)

        prompt = (
            "You are a project analysis assistant. The user requests a detailed analysis of task decomposition results. "
            "Based on the decomposition, analyze coverage sufficiency, relationships between tasks, "
            "possible omissions, and potential refinement directions (if any). Do not repeat summaries like "
            "'X subtasks were generated'; provide a professional analysis body directly.\n"
            "Requirements: at least 6 bullet points or 3 natural paragraphs; points must be clear, concrete, and actionable.\n"
            f"{PROFESSIONAL_STYLE_INSTRUCTION}\n\n"
            f"User question: {user_message}\n\n"
            "Decomposition results:\n"
            f"{tasks_text}\n\n"
            "Output analysis:"
        )
    elif step_summaries:
        steps_text = "\n\n".join(step_summaries)
        prompt = (
            "You are a project analysis assistant. The user requests analysis of background task execution results. "
            "Based on outputs from the following execution steps, provide a structured analysis: key findings, critical data, "
            "and next-step recommendations. Output the professional analysis body directly; avoid preambles like "
            "'I will analyze this now.'\n"
            "Requirements: specific, data-driven, and actionable.\n"
            f"{PROFESSIONAL_STYLE_INSTRUCTION}\n\n"
            f"User question: {user_message}\n\n"
            "Execution results:\n"
            f"{steps_text}\n\n"
            "Output analysis:"
        )
    else:
        return None
    try:
        llm_service = _ae()._get_llm_service_for_provider(llm_provider)
        analysis = await llm_service.chat_async(prompt)
        if not analysis:
            return None
        cleaned = sanitize_professional_response_text(analysis.strip())
        return _repair_distribution_summary_math(cleaned)
    except Exception as exc:
        logger.error(
            "[CHAT][SUMMARY] Failed to generate action analysis for session=%s: %s",
            session_id,
            exc,
        )
        return None


def _build_brief_action_summary(steps: List[Any]) -> Optional[str]:
    if not steps:
        return None
    if len(steps) == 1:
        step = steps[0]
        if step.message:
            return step.message
        if step.action.name:
            return f"Completed action: {step.action.name}"
        if step.action.kind:
            return f"Completed action: {step.action.kind}"
        return None

    names: List[str] = []
    for step in steps:
        if step.action.name:
            names.append(step.action.name)
        elif step.action.kind:
            names.append(step.action.kind)
    if not names:
        return f"Completed {len(steps)} actions."
    unique = []
    for name in names:
        if name not in unique:
            unique.append(name)
    preview = ", ".join(unique[:3])
    suffix = " and more" if len(unique) > 3 else ""
    return f"Completed {len(steps)} actions: {preview}{suffix}."


def _should_skip_post_action_analysis(steps: List[Any]) -> bool:
    """Skip extra LLM analysis when the action already returned a usable review body."""

    if not steps:
        return False

    meaningful_steps = [step for step in steps if getattr(step, "action", None) is not None]
    if not meaningful_steps:
        return False

    return all(
        getattr(step.action, "kind", None) == "plan_operation"
        and getattr(step.action, "name", None) == "review_plan"
        for step in meaningful_steps
    )
