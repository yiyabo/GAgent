"""Synthesis and fallback answer builders for the DeepThink agent.

God-class split (behaviour zero-change): the bodies of the like-named
DeepThinkAgent methods with `self`/`cls` renamed to `agent`; the class keeps
thin wrappers. Display-family helpers (detect_reasoning_language,
is_process_only_answer, ...) stay in deep_think_agent and are reached through
the late-bound `_dta()` so their monkeypatch surface is unchanged; env knobs
are resolved the same way for the same reason.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from app.llm import update_usage_context
from app.services.deep_think.models import (
    DeepThinkProtocolError,
    TaskExecutionContext,
    ThinkingStep,
)
from app.services.deep_think.text_utils import (
    _EXPECT_KIND_LABEL,
    _collect_deliverable_display_names,
    _default_fallback_timeout_seconds,
    _default_synthesis_max_tokens,
    _default_synthesis_timeout_seconds,
    _drop_process_echo_bullets,
    _ensure_inline_images,
    _strip_runtime_absolute_paths,
    _strip_cli_noise_from_multiline,
    _strip_cli_stream_noise,
)
from app.services.response_style import (
    PROFESSIONAL_STYLE_INSTRUCTION,
    sanitize_professional_response_text,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.services.deep_think_agent import DeepThinkAgent

logger = logging.getLogger(__name__)


def _dta() -> Any:
    """Late-bound deep_think_agent module (monkeypatch-friendly lookups)."""
    from app.services import deep_think_agent

    return deep_think_agent


_TOOL_RESULT_PREFIX_RE = re.compile(r"^\[[^\]]*]\s*")


def _storage_candidate_paths(storage: Any) -> List[Path]:
    if not isinstance(storage, dict):
        return []
    candidates: List[Path] = []

    def _append(value: Any) -> None:
        if not isinstance(value, str) or not value.strip():
            return
        raw = value.strip()
        try:
            candidates.append(Path(raw).expanduser())
        except Exception:
            return

    _append(storage.get("result_path"))
    relative = storage.get("relative")
    session_id = str(storage.get("session_id") or "").strip()
    if isinstance(relative, dict):
        rel_result = relative.get("result_path")
        _append(rel_result)
        if session_id and isinstance(rel_result, str) and rel_result.strip():
            try:
                from app.services.upload_storage import ensure_session_dir

                candidates.append(ensure_session_dir(session_id) / rel_result.strip())
            except Exception:
                pass
    return candidates


def _load_llm_safe_stored_tool_result(wrapper: Any, *, max_bytes: int = 200_000) -> Optional[Any]:
    """Load a stored tool ``result.json`` when the existing storage contract says it is safe.

    This reuses ``tool_output_storage`` outputs instead of inventing a second file-read path.
    It is intentionally narrow: only internal ``tool_outputs/**/result.json`` files are loaded,
    and sibling ``manifest.json`` must not mark them as too large for LLM use.
    """
    if not isinstance(wrapper, dict):
        return None

    storage_values: List[Any] = []
    for node in (wrapper, wrapper.get("result") if isinstance(wrapper.get("result"), dict) else None):
        if isinstance(node, dict):
            storage_values.append(node.get("storage"))

    for storage in storage_values:
        for candidate in _storage_candidate_paths(storage):
            try:
                path = candidate.resolve()
            except Exception:
                continue
            normalized = str(path).replace("\\", "/").lower()
            if not normalized.endswith("/result.json") or "/tool_outputs/" not in normalized:
                continue
            if not path.exists() or not path.is_file():
                continue
            try:
                if path.stat().st_size > max_bytes:
                    continue
                manifest_path = path.with_name("manifest.json")
                if manifest_path.exists():
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    result_meta = manifest.get("result") if isinstance(manifest, dict) else None
                    if isinstance(result_meta, dict) and result_meta.get("too_large_for_llm") is True:
                        continue
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
    return None


def _format_count_pairs(value: Any, *, limit: int = 5) -> str:
    if not isinstance(value, list):
        return ""
    parts: List[str] = []
    for item in value[:limit]:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            parts.append(f"{item[0]}={item[1]}")
    return ", ".join(parts)


def _collect_tool_usage_counts(cls: Any, steps: List[ThinkingStep]) -> Dict[str, int]:
    c: Counter[str] = Counter()
    for step in steps:
        if not step.action:
            continue
        try:
            payload = json.loads(step.action)
        except Exception:
            continue
        for name in cls._tool_names_from_payload(payload):
            if name and name != "submit_final_answer":
                c[name] += 1
    return dict(c)


def _format_tool_usage_counts(counts: Dict[str, int]) -> str:
    if not counts:
        return ""
    items = sorted(counts.items(), key=lambda x: (-x[1], x[0]))
    return ", ".join(f"{k}×{v}" for k, v in items)


def _select_steps_for_summary(steps: List[ThinkingStep]) -> List[ThinkingStep]:
    n = len(steps)
    if n <= 5:
        return list(steps)
    idx = sorted({0, 1, n - 3, n - 2, n - 1})
    idx = [i for i in idx if 0 <= i < n]
    return [steps[i] for i in idx]


def _slim_evidence_text_for_synthesis(text: str) -> str:
    """Drop internal tool-output paths and compress terminal_session JSON for synthesis.

    Important: tool results are often a *single* JSON line containing ``storage`` paths.
    Line-based removal previously deleted the entire line, leaving **empty evidence** for
    fallback synthesis — causing the LLM to hallucinate \"no tool output\" answers.
    """
    stripped = str(text or "").strip()
    if not stripped:
        return ""
    candidate = _TOOL_RESULT_PREFIX_RE.sub("", stripped).strip()

    def _drop_storage_keys(node: Any) -> Any:
        if isinstance(node, dict):
            return {
                k: _drop_storage_keys(v)
                for k, v in node.items()
                if k not in {"storage", "deliverables"}
            }
        if isinstance(node, list):
            return [_drop_storage_keys(x) for x in node]
        return node

    try:
        obj = json.loads(candidate)
    except Exception:
        # Non-JSON: avoid deleting one-line blobs; redact path substrings only.
        if "/tool_outputs/" in stripped.lower():
            redacted = re.sub(
                r"/[^\s\"']*tool_outputs[^\s\"']*",
                "[internal_tool_output_path]",
                stripped,
                flags=re.IGNORECASE,
            )
            return redacted[:12000]
        return stripped[:12000]

    if isinstance(obj, dict):
        obj = _drop_storage_keys(obj)
        if str(obj.get("tool") or "") == "terminal_session" or "terminal_id" in obj:
            slim: Dict[str, Any] = {
                "tool": "terminal_session",
                "operation": obj.get("operation"),
                "verification_state": obj.get("verification_state"),
                "command_state": obj.get("command_state"),
                "exit_code": obj.get("exit_code"),
                "verification_summary": obj.get("verification_summary"),
                "status": obj.get("status"),
                "output": obj.get("output"),
            }
            if isinstance(obj.get("verification_evidence"), dict):
                slim["verification_evidence"] = obj.get("verification_evidence")
            compact = {k: v for k, v in slim.items() if v is not None}
            out = json.dumps(compact, ensure_ascii=False)
            return out[:12000]
        return json.dumps(obj, ensure_ascii=False)[:12000]
    return str(obj)[:12000]


def _collect_evidence_snippets(
    agent: "DeepThinkAgent",
    steps: List[ThinkingStep],
    *,
    max_steps: int = 8,
    max_chars: int = 3000,
    per_snippet_max: int = 900,
) -> str:
    parts: List[str] = []
    total = 0
    count = 0
    for step in reversed(steps):
        if count >= max_steps:
            break
        ar = step.action_result
        if not isinstance(ar, str) or not ar.strip():
            continue
        text = agent._slim_evidence_text_for_synthesis(ar)
        text = " ".join(text.split()).strip()
        if len(text) > per_snippet_max:
            text = text[: per_snippet_max - 3] + "..."
        block = f"[Step {step.iteration}]\n{text}"
        if total + len(block) + 2 > max_chars:
            remaining = max(0, max_chars - total - 20)
            if remaining > 80:
                parts.append(f"[Step {step.iteration}]\n{text[:remaining]}...")
            break
        parts.append(block)
        total += len(block) + 2
        count += 1
    parts.reverse()
    return "\n\n".join(parts)


def _humanize_single_tool_result(tool_name: str, obj: dict) -> str:
    """Convert a single parsed tool-result dict into a concise, human-readable line."""
    success = obj.get("success")
    result = obj.get("result") or obj
    stored_result = _load_llm_safe_stored_tool_result(obj)
    if isinstance(stored_result, dict):
        result = stored_result
        success = stored_result.get("success", success)
    tool = str(result.get("tool", "") or tool_name).strip()

    # --- terminal_session: skip noise-only entries ---
    if tool == "terminal_session" or "terminal_id" in obj:
        output = _strip_cli_noise_from_multiline(
            str(result.get("output") or obj.get("output") or "")
        ).strip()
        vs = result.get("verification_summary") or obj.get("verification_summary")
        if vs:
            return f"终端会话：{vs}"
        if output and len(output) > 15:
            return f"终端输出：{output[:300]}"
        return ""  # skip noise

    # --- code_executor ---
    if tool == "code_executor":
        if success is True or result.get("success") is True:
            stdout = _strip_cli_noise_from_multiline(
                str(result.get("stdout") or "")
            ).strip()
            artifacts = result.get("artifact_paths") or []
            result_files = [
                p for p in artifacts
                if "/results/" in str(p) and not str(p).rstrip("/").endswith("/results")
            ]
            parts: List[str] = ["代码执行成功"]
            if stdout:
                # Take the most informative lines (skip blank / separator lines)
                lines = [ln.strip() for ln in stdout.splitlines() if ln.strip() and ln.strip("=- ")]
                if lines:
                    preview = "; ".join(lines[:5])
                    if len(preview) > 300:
                        preview = preview[:297] + "..."
                    parts.append(f"输出：{preview}")
            if result_files:
                names = [p.rsplit("/", 1)[-1] for p in result_files[:8]]
                parts.append(f"产出文件：{', '.join(names)}")
            return "。".join(parts)
        else:
            error = str(result.get("error") or result.get("stderr") or "unknown error").strip()
            if len(error) > 200:
                error = error[:197] + "..."
            return f"代码执行失败：{error}"

    # --- file_operations ---
    if tool == "file_operations":
        op = str(result.get("operation") or "").strip()
        if op == "list":
            items = result.get("items") or []
            file_names = [
                f"{it.get('name', '?')} ({it.get('size', '?')} B)"
                for it in items if isinstance(it, dict)
            ][:10]
            if file_names:
                return f"文件列表：{', '.join(file_names)}"
            return f"文件列表：空（{result.get('path', '')}）"
        if op == "read":
            summary = str(result.get("summary") or "").strip()
            path = str(result.get("path") or "").strip()
            fname = path.rsplit("/", 1)[-1] if path else "?"
            content = str(result.get("content") or "").strip()
            if content:
                preview = " ".join(content.split())[:300]
                return f"文件读取 ({fname})：{preview}"
            if summary:
                return f"文件读取 ({fname})：{summary[:200]}"
            return f"已读取文件：{fname}"
        if op == "write":
            path = str(result.get("path") or "").strip()
            fname = path.rsplit("/", 1)[-1] if path else "?"
            size = result.get("size", "?")
            return f"已写入文件：{fname} ({size} B)"
        return f"文件操作 ({op})：{result.get('summary', '完成')}"

    # --- phagescope_research ---
    if tool == "phagescope_research":
        if success is False or result.get("success") is False:
            error = str(result.get("error") or "unknown error").strip()
            return f"phagescope_research 失败：{error[:200]}"
        action = str(result.get("action") or "").strip() or "operation"
        if action == "audit":
            parts: List[str] = ["phagescope_research audit 成功"]
            if isinstance(result.get("metadata_rows"), int):
                parts.append(f"metadata_rows={result.get('metadata_rows')}")
            if isinstance(result.get("unique_phage_ids"), int):
                parts.append(f"unique_phage_ids={result.get('unique_phage_ids')}")
            missing = result.get("missing_counts")
            if isinstance(missing, dict) and missing:
                parts.append("missing=" + ", ".join(f"{k}={v}" for k, v in list(missing.items())[:4]))
            source_top = _format_count_pairs(result.get("source_top"), limit=4)
            if source_top:
                parts.append(f"top_sources={source_top}")
            completeness = _format_count_pairs(result.get("completeness_counts"), limit=4)
            if completeness:
                parts.append(f"completeness={completeness}")
            lifestyle = _format_count_pairs(result.get("lifestyle_counts"), limit=3)
            if lifestyle:
                parts.append(f"lifestyle={lifestyle}")
            return "；".join(parts)
        if action == "deep_profile":
            parts = ["phagescope_research deep_profile 成功"]
            if isinstance(result.get("metadata_rows"), int):
                parts.append(f"metadata_rows={result.get('metadata_rows')}")
            if isinstance(result.get("metadata_files"), int):
                parts.append(f"metadata_files={result.get('metadata_files')}")
            if result.get("metadata_size_human"):
                parts.append(f"meta_data_size={result.get('metadata_size_human')}")
            if result.get("total_size_human"):
                parts.append(f"total_size={result.get('total_size_human')}")
            ml_table = result.get("ml_metadata_table")
            if isinstance(ml_table, dict):
                parts.append(f"ml_table_rows={ml_table.get('rows', '?')}")
            label_quality = result.get("label_quality")
            if isinstance(label_quality, dict):
                parts.append(
                    f"host_missing={label_quality.get('host_missing_rows', '?')}"
                )
            return "；".join(parts)
        if action == "prepare_metadata_table":
            rows = result.get("rows_written") or result.get("output_table_rows")
            labels = result.get("labels_kept")
            splits = result.get("split_totals")
            parts = ["phagescope_research prepare_metadata_table 成功"]
            if rows is not None:
                parts.append(f"rows_written={rows}")
            if labels is not None:
                parts.append(f"labels_kept={labels}")
            if isinstance(splits, dict):
                parts.append("splits=" + ", ".join(f"{k}={v}" for k, v in splits.items()))
            return "；".join(parts)
        return f"phagescope_research {action} 执行完成"

    # --- generic tool with summary ---
    summary = str(result.get("summary") or "").strip()
    if summary and len(summary) > 10:
        if tool:
            return f"{tool}：{summary[:300]}"
        return summary[:300]
    if success is True:
        return f"{tool}：执行完成" if tool else "工具执行完成"
    if success is False:
        error = str(result.get("error") or "").strip()
        if tool:
            return f"{tool}：失败{f' — {error[:150]}' if error else ''}"
        return f"执行失败{f'：{error[:150]}' if error else ''}"
    return ""


def _collect_user_facing_evidence_snippets(
    agent: "DeepThinkAgent",
    steps: List[ThinkingStep],
    *,
    max_steps: int = 8,
    max_chars: int = 3000,
    per_snippet_max: int = 900,
) -> str:
    """Build human-readable bullet list from raw step action_results.

    Unlike _collect_evidence_snippets (which preserves JSON for LLM synthesis),
    this method humanizes tool results into natural-language summaries for direct
    display to the user.
    """
    bullets: List[str] = []
    total = 0
    count = 0
    for step in reversed(steps):
        if count >= max_steps:
            break
        ar = step.action_result
        if not isinstance(ar, str) or not ar.strip():
            continue
        count += 1

        # action_result may contain multiple tool results:
        # "[tool_a] {json}\n\n[tool_b] {json}"
        segments = re.split(r"\n\n(?=\[)", ar)
        for seg in segments:
            seg = seg.strip()
            if not seg:
                continue
            # Extract tool name prefix if present: [tool_name] rest
            prefix_match = re.match(r"^\[([^\]]+)]\s*", seg)
            tool_name = prefix_match.group(1) if prefix_match else ""
            body = seg[prefix_match.end():] if prefix_match else seg

            # Try to parse as JSON and humanize
            humanized = None  # None = not parsed; "" = parsed but skip
            try:
                obj = json.loads(body)
                if isinstance(obj, dict):
                    humanized = agent._humanize_single_tool_result(tool_name, obj)
            except Exception:
                pass

            # humanized == "" means the humanizer explicitly says "skip this"
            if humanized == "":
                continue
            if humanized is None:
                # Non-JSON or unrecognized: use a trimmed version
                cleaned = " ".join(body.split()).strip()
                if len(cleaned) > per_snippet_max:
                    cleaned = cleaned[: per_snippet_max - 3] + "..."
                if cleaned and len(cleaned) > 15:
                    humanized = cleaned
            if not humanized:
                continue
            if len(humanized) > per_snippet_max:
                humanized = humanized[: per_snippet_max - 3] + "..."
            bullet = f"- {humanized}"
            if total + len(bullet) + 1 > max_chars:
                break
            bullets.append(bullet)
            total += len(bullet) + 1

    bullets.reverse()
    return "\n".join(bullets)


def _build_bound_execute_task_fallback(
    agent: "DeepThinkAgent",
    steps: List[ThinkingStep],
    *,
    user_query: str,
    task_context: Optional[TaskExecutionContext],
) -> str:
    if not (
        agent._is_execute_task_request()
        and agent._has_bound_task_context(task_context)
        and agent._explicit_task_override_active(task_context)
    ):
        return ""
    language = _dta().detect_reasoning_language(user_query)
    task_id = agent._current_bound_task_id(task_context)
    task_name = str(getattr(task_context, "task_name", "") or "").strip()
    task_label = f"Task {task_id}" if task_id is not None else "the current bound task"
    if task_name:
        task_label = f"{task_label} ({task_name})"
    counts = agent._collect_tool_usage_counts(steps)
    stats = agent._format_tool_usage_counts(counts)
    evidence = agent._collect_evidence_snippets(
        steps,
        max_steps=8,
        max_chars=1800,
        per_snippet_max=500,
    ).strip()
    if language == "zh":
        header = f"本轮已经进入已绑定任务执行链{f'（{stats}）' if stats else ''}，但系统在结束前没有成功提交正式最终答案。"
        lines = [
            header,
            f"当前绑定任务：{task_label}。",
            "这不表示缺少任务定义；当前任务上下文已经存在，后续应从当前绑定任务继续执行，而不是再要求用户补任务描述。",
        ]
        if evidence:
            lines.extend(["", "已观察到的关键信息：", evidence])
        return "\n".join(lines).strip()
    header = (
        f"This run stayed inside a bound execute-task chain{f' ({stats})' if stats else ''}, "
        "but it ended before a formal final answer was submitted."
    )
    lines = [
        header,
        f"Current bound task: {task_label}.",
        "This does not mean the task definition is missing; the task context is already bound and execution should continue from the current task instead of asking the user to provide task details again.",
    ]
    if evidence:
        lines.extend(["", "Observed evidence:", evidence])
    return "\n".join(lines).strip()


def _build_structured_fallback(agent: "DeepThinkAgent", steps: List[ThinkingStep], user_query: str = "") -> str:
    """Last-resort fallback: include evidence excerpts rather than a generic 'no answer' message."""
    language = _dta().detect_reasoning_language(user_query)
    structured_plan_outcome = agent._summarize_structured_plan_outcome(
        steps,
        user_query=user_query,
    )
    if structured_plan_outcome.get("required") and not structured_plan_outcome.get("satisfied"):
        return agent._build_structured_plan_contract_failure_answer(
            outcome=structured_plan_outcome,
            user_query=user_query,
        )

    # Collect any meaningful evidence to include in the fallback
    raw_evidence = _strip_cli_stream_noise(
        agent._collect_user_facing_evidence_snippets(
            steps,
            max_steps=12,
            max_chars=4000,
            per_snippet_max=1200,
        )
    )
    output_files = _collect_deliverable_display_names(raw_evidence)
    deliverable_files = output_files
    evidence = _drop_process_echo_bullets(raw_evidence)
    # Also collect useful thoughts
    useful_thoughts: List[str] = []
    for s in reversed(steps):
        if isinstance(s.thought, str) and s.thought.strip() and not _dta().is_process_only_answer(s.thought, user_query=user_query):
            cleaned = s.thought.strip()
            if len(cleaned) > 50:
                useful_thoughts.append(cleaned[:800])
                if len(useful_thoughts) >= 3:
                    break
    useful_thoughts.reverse()

    # If we have substantial evidence or thoughts, build a content-rich fallback
    if evidence.strip() and len(evidence.strip()) > 20:
        # Check if we have successful tool execution evidence
        has_success = "代码执行成功" in evidence or "已写入文件" in evidence or "产出文件" in evidence
        artifact_hint = ""
        if output_files:
            if language == "zh":
                artifact_hint = (
                    "\n\n本轮已生成以下交付文件（完整内容见右侧 Artifacts 面板）：\n"
                    + "\n".join(f"- {name}" for name in output_files)
                )
            else:
                artifact_hint = (
                    "\n\nThe following output files were generated (see the Artifacts panel for full content):\n"
                    + "\n".join(f"- {name}" for name in output_files)
                )
        if language == "zh":
            if has_success:
                header = "以下是本轮工具执行的结果摘要：\n\n"
                footer = artifact_hint
            else:
                header = "以下是本轮执行中观察到的信息：\n\n"
                footer = "\n\n如需更详细的分析，请指出具体要查看的内容。"
        else:
            if has_success:
                header = "Here is the summary of tool execution results:\n\n"
                footer = artifact_hint
            else:
                header = "Here is what was observed during execution:\n\n"
                footer = "\n\nFor a more detailed analysis, please specify what you'd like to examine."
        return _strip_runtime_absolute_paths(
            _ensure_inline_images(
                header + evidence.strip() + footer,
                agent._collect_inline_image_relpaths(),
            )
        )

    if deliverable_files:
        # Evidence reduced to process echoes, but the run did produce files —
        # report the deliverables cleanly instead of dumping debris.
        names = "\n".join(f"- {name}" for name in deliverable_files)
        if language == "zh":
            text = (
                "本轮执行已生成以下交付文件（完整内容见右侧 Artifacts 面板）：\n"
                f"{names}\n\n"
                "如需调整内容或导出其他格式，请告诉我。"
            )
        else:
            text = (
                "This run produced the following deliverable files (see the Artifacts panel for full content):\n"
                f"{names}\n\n"
                "Let me know if you want changes or a different export format."
            )
        return _strip_runtime_absolute_paths(
            _ensure_inline_images(text, agent._collect_inline_image_relpaths())
        )

    if useful_thoughts:
        combined = "\n\n".join(useful_thoughts)
        if language == "zh":
            return f"我先给出目前已经收敛出的关键判断：\n\n{combined}"
        return f"Here are the key conclusions that could still be supported:\n\n{combined}"

    # Truly nothing useful — keep minimal message
    if language == "zh":
        return (
            "这一轮检查还不足以支撑可靠结论。"
            "建议进一步缩小范围，或指定要继续核查的对象后再收敛。"
        )
    return (
        "The completed checks were not enough to support a reliable final answer. "
        "This request likely needs a narrower scope or a more specific fact to verify next."
    )


async def _chat_text_streaming(agent: "DeepThinkAgent", prompt: str, *, max_tokens: int) -> str:
    """Collect a complete synthesis response via streaming.

    Non-streaming calls against thinking models sit silent for minutes and
    get cut by upstream gateways with 504s (observed 2026-09-19: forced
    synthesis died with HTTPStatusError:504 after 4 attempts); streaming
    keeps bytes flowing and survives multi-minute generations.
    """
    stream_fn = getattr(agent.llm_client, "stream_chat_async", None)
    if callable(stream_fn):
        chunks: List[str] = []
        async for chunk in stream_fn(prompt=prompt, max_tokens=max_tokens):
            chunks.append(str(chunk))
        return "".join(chunks)
    return await agent.llm_client.chat_async(prompt=prompt, max_tokens=max_tokens)


async def _generate_fallback_from_evidence(
    agent: "DeepThinkAgent",
    user_query: str,
    evidence_snippets: str,
    steps: List[ThinkingStep],
    task_context: Optional[TaskExecutionContext] = None,
    *,
    max_retries: int = 3,
    timeout: Optional[float] = None,
    max_tokens: int = 2000,
) -> str:
    if not hasattr(agent.llm_client, "chat_async"):
        raise DeepThinkProtocolError("LLM client does not support chat_async")
    if timeout is None:
        timeout = float(_default_fallback_timeout_seconds())
    n = len(steps)
    uq = (user_query or "").strip()

    # Collect useful thoughts as additional context
    thought_context = ""
    useful_thoughts = [
        s.thought.strip()
        for s in steps
        if isinstance(s.thought, str) and len(s.thought.strip()) > 30
    ]
    if useful_thoughts:
        thought_context = (
            "\n\nAssistant's reasoning during the process:\n"
            + "\n".join(f"- {t[:300]}" for t in useful_thoughts[-5:])
            + "\n"
        )

    focus_instruction = ""
    if agent._is_brief_execute_followup():
        focus_instruction = (
            "7) This is a short execution follow-up. Focus on the current task outcome and latest successful tool result.\n"
            "8) Do NOT recap prior project milestones, historical progress tables, or next-step menus unless the user explicitly asked for them.\n"
        )
    bound_task_instruction = ""
    if agent._is_execute_task_request() and agent._has_bound_task_context(task_context):
        task_id = agent._current_bound_task_id(task_context)
        task_name = str(getattr(task_context, "task_name", "") or "").strip()
        task_label = f"Task {task_id}" if task_id is not None else "the current bound task"
        if task_name:
            task_label = f"{task_label} ({task_name})"
        bound_task_instruction = (
            f"9) This request is already bound to {task_label}. "
            "Do NOT ask the user to provide task definitions, task descriptions, or to confirm whether a parent plan68_task directory exists.\n"
            "10) If execution stopped early, summarize the real bound-task state and the observed outputs instead of inventing a missing-task-definition blocker.\n"
        )

    prompt = (
        f"User question:\n{uq[:2000]}\n\n"
        f"Below are excerpts from tool outputs collected during {n} reasoning step(s). "
        f"The system did not produce a final answer automatically, so you must synthesize one now.\n\n"
        f"{evidence_snippets}"
        f"{thought_context}\n\n"
        "INSTRUCTIONS:\n"
        "Respond in the same language as the user question. Provide a COMPLETE, USEFUL answer:\n"
        "1) Directly answer the user's question based on the evidence available.\n"
        "2) Summarize the key facts, findings, and data points from the excerpts.\n"
        "3) If the evidence is insufficient for a complete answer, clearly state what was found and what remains unknown.\n"
        "4) Use clear structure (headings, bullet points) for readability.\n"
        "5) Do NOT say 'I could not find an answer' if there is ANY useful information — present what was found.\n"
        "6) Do not invent dates or claims absent from the excerpts.\n"
        "7) Respect evidence scope: if excerpts show sampled/compacted listings, partial completion, failure status files, "
        "or status counts, state those limits and do not convert them into all/every/global success claims.\n"
        f"{focus_instruction}"
        f"{bound_task_instruction}"
        f"{PROFESSIONAL_STYLE_INSTRUCTION}"
    )

    last_exc: Optional[Exception] = None
    for attempt in range(max_retries):
        try:
            raw = await asyncio.wait_for(
                agent._chat_text_streaming(prompt, max_tokens=max_tokens),
                timeout=timeout,
            )
            cleaned = sanitize_professional_response_text(str(raw or "").strip())
            if len(cleaned) < 20:
                raise ValueError(f"fallback synthesis too short ({len(cleaned)} chars)")
            cleaned = _strip_runtime_absolute_paths(
                _ensure_inline_images(cleaned, agent._collect_inline_image_relpaths())
            )
            return cleaned
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "DeepThink fallback synthesis attempt %d/%d failed: %r",
                attempt + 1,
                max_retries,
                exc,
            )
            if attempt < max_retries - 1:
                await asyncio.sleep(1.0 * (attempt + 1))

    raise last_exc or ValueError("fallback synthesis failed after retries")


async def _forced_synthesis_from_steps(
    agent: "DeepThinkAgent",
    steps: List[ThinkingStep],
    user_query: str,
    messages: List[Dict[str, Any]],
    task_context: Optional[TaskExecutionContext] = None,
) -> str:
    update_usage_context(call_purpose="deep_think_forced_synthesis", phase="deep_think")
    """Make one final LLM call with all context asking it to synthesize a complete answer.

    This is more powerful than _generate_fallback_from_evidence because it
    includes both evidence snippets and the assistant's reasoning thoughts,
    giving the LLM rich context to produce a quality answer.
    """
    if not hasattr(agent.llm_client, "chat_async"):
        return ""

    try:
        evidence = _strip_cli_stream_noise(
            agent._collect_evidence_snippets(
                steps, max_steps=12, max_chars=6000, per_snippet_max=1500
            )
        )
        uq = (user_query or "").strip()
        if not uq:
            return ""

        # Also collect assistant thoughts for additional context
        thought_lines: List[str] = []
        for s in steps:
            if isinstance(s.thought, str) and len(s.thought.strip()) > 30:
                thought_lines.append(f"[Step {s.iteration} thought]: {s.thought.strip()[:500]}")
        thoughts_text = "\n".join(thought_lines[-8:]) if thought_lines else ""

        language = _dta().detect_reasoning_language(user_query)
        structured_plan_outcome = agent._summarize_structured_plan_outcome(
            steps,
            user_query=user_query,
        )
        if language == "zh":
            instruction = (
                "你是一个深度思考AI助手。用户提出了一个问题，系统已经执行了多个工具调用来收集信息。"
                "现在请基于下面收集到的所有证据和推理过程，直接回答用户的问题。\n"
                "要求：\n"
                "- 提供完整、有用的回答\n"
                "- 使用清晰的结构（标题、要点列表）\n"
                "- 即使信息不完整，也请尽力基于已有证据给出最佳回答\n"
                "- 不要说'无法回答'，请展示已找到的信息\n"
                "- 不要编造证据中没有的信息\n"
                "- 严禁：若「证据」里没有某文件的完整读取内容，却声称已读取该文件；严禁根据常识列举 "
                "`.env` 里「通常会有」的 Qwen/OpenAI/GLM 等名称，除非这些字符串出现在证据原文中。\n"
                "- 若推理里打算读 A 文件，但证据实际只有 B 文件，必须在答案中说明「未读到 A」或「仅见 B」，不得假装已检查 A。\n"
                "- 若用户要验证 PhageScope 远程访问，而证据中没有任何 phagescope 工具的成功返回，"
                "必须明确写出「尚未执行 phagescope 远程请求」，不得声称已确认 PhageScope 权限或「优化已验证 PhageScope」。\n"
                "- 介绍 PhageScope 接口时不要臆造「必须去官网用户中心申请 API Token」等；官方文档以 `userid` 为主，"
                "工具里 `token` 为可选；无证据时不要断言具体凭证流程。\n"
                "- 不要引导用户去查看、粘贴 `.env` 来「验证 PhageScope」；除非用户明确问本地部署密钥。"
                "连通性应以 `phagescope` 工具（如 ping）为准；勿臆造 `PHAGESCOPE_API_TOKEN` 等变量名。\n"
                "- 尊重 evidence_scope / completeness_status / status_counts / partial_completion_suspected："
                "若证据只是采样、压缩列表或存在失败/部分完成信号，必须明确范围限制，不得写成全部/每个样本都成功。"
            )
            if structured_plan_outcome.get("required") and not structured_plan_outcome.get("satisfied"):
                instruction += (
                    "\n- 这次请求要求产出真实的结构化计划。当前证据里没有成功的 `plan_operation` 创建/更新结果。"
                    "\n- 你必须明确写出：本轮未成功创建或更新结构化计划；不要把普通 markdown 文本说成已经创建好的计划。"
                )
            if agent._is_brief_execute_followup():
                instruction += (
                    "\n- 这是一次简短的执行跟进。优先汇报当前任务结果或最新工具结果。"
                    "\n- 不要回顾先前项目里程碑、旧测试轮次、阶段性总结或“下一步建议”，除非用户明确要求。"
                )
        else:
            instruction = (
                "You are a Deep Thinking AI assistant. The user asked a question and the system "
                "has executed multiple tool calls to gather information. Now synthesize ALL the "
                "evidence and reasoning below into a complete, useful answer.\n"
                "Requirements:\n"
                "- Provide a complete, useful answer\n"
                "- Use clear structure (headings, bullet points)\n"
                "- Even if information is incomplete, provide the best answer from available evidence\n"
                "- Do NOT say you cannot answer — present what was found\n"
                "- Do not invent claims absent from the evidence\n"
                "- Do not claim a file was read unless its contents (or an explicit excerpt) appear in the evidence; "
                "if reasoning mentions file A but evidence only shows file B, say so.\n"
                "- Do not list typical `.env` API provider names unless they appear verbatim in the evidence.\n"
                "- For PhageScope access checks: if no phagescope tool success appears in the evidence, "
                "state clearly that remote PhageScope was not exercised; do not claim credentials or that ping succeeded.\n"
                "- When describing PhageScope API requirements, do not invent a mandatory API token from a "
                "\"user center\" unless the evidence says so; documented flows center on `userid`; tool `token` is optional.\n"
                "- Do not tell the user to inspect or paste `.env` for PhageScope verification unless they explicitly "
                "ask about local secrets; use phagescope tool (e.g. ping). This codebase only documents optional env "
                "`PHAGESCOPE_BASE_URL` and `PHAGESCOPE_SSL_VERIFY`, not a `PHAGESCOPE_API_TOKEN` variable.\n"
                "- Respect evidence_scope / completeness_status / status_counts / partial_completion_suspected: if evidence is "
                "sampled, compacted, or shows failures/partial completion, state the scope limits and do not claim all/every sample succeeded."
            )
            if structured_plan_outcome.get("required") and not structured_plan_outcome.get("satisfied"):
                instruction += (
                    "\n- This request required a real structured plan result."
                    "\n- No successful plan_operation create/update is present in the evidence, so you must explicitly say that a structured plan was not created or updated in this run."
                    "\n- Do not present ordinary markdown text as if the system already created the plan."
                )
            if agent._is_brief_execute_followup():
                instruction += (
                    "\n- This is a short execution follow-up. Prioritize the current task outcome or latest tool result."
                    "\n- Do not recap prior project milestones, older test rounds, progress summaries, or next-step menus unless the user explicitly asked."
                )

        produced_images = agent._collect_inline_image_relpaths()
        if language == "zh":
            instruction += (
                "\n- 答案中引用任何产出文件（清单、正文、图片路径）一律使用会话相对路径"
                "（如 raw_files/...、deliverables/...），禁止输出以 /app/runtime/ 开头的绝对路径。"
            )
        else:
            instruction += (
                "\n- Reference every produced file (manifest lists, prose, image paths) with its "
                "session-relative path (e.g. raw_files/..., deliverables/...); never emit "
                "absolute paths starting with /app/runtime/."
            )
        if produced_images:
            listing = "\n".join(f"- {p}" for p in produced_images)
            if language == "zh":
                instruction += (
                    "\n- 本轮已产出的图片文件（相对路径）：\n" + listing +
                    "\n  在答案相应位置用 `![描述](相对路径)` 把图片内联展示，不要只在文字里提文件名。"
                )
            else:
                instruction += (
                    "\n- Image files produced in this run (relative paths):\n" + listing +
                    "\n  Embed each image inline at the relevant position with `![caption](relative-path)`; do not merely name the files."
                )
        acceptance_missing = [m for m in (getattr(agent, "_acceptance_missing", None) or []) if m]
        if acceptance_missing:
            if language == "zh":
                labels_zh = "、".join(
                    _EXPECT_KIND_LABEL.get(m, {}).get("zh", m) for m in acceptance_missing
                )
                instruction += (
                    f"\n- 本轮未能产出的交付物类型：{labels_zh}。"
                    "必须在答案开头明确说明哪些没有完成以及原因，不得假装全部完成。"
                )
            else:
                labels_en = ", ".join(
                    _EXPECT_KIND_LABEL.get(m, {}).get("en", m) for m in acceptance_missing
                )
                instruction += (
                    f"\n- Deliverable type(s) NOT produced this run: {labels_en}. "
                    "State this explicitly at the top of the answer with the reason; do not pretend everything completed."
                )

        prompt = (
            f"{instruction}\n\n"
            f"User question: {uq[:2000]}\n\n"
        )
        if agent._is_execute_task_request() and agent._has_bound_task_context(task_context):
            task_id = agent._current_bound_task_id(task_context)
            task_name = str(getattr(task_context, "task_name", "") or "").strip()
            task_instruction = str(getattr(task_context, "task_instruction", "") or "").strip()
            task_label = f"Task {task_id}" if task_id is not None else "the current bound task"
            if task_name:
                task_label = f"{task_label} ({task_name})"
            prompt += (
                "=== BOUND TASK CONTEXT ===\n"
                f"Current bound task: {task_label}\n"
                f"Task instruction: {task_instruction[:600]}\n"
                "This task context is already authoritative. Do NOT ask the user to provide task definitions, task descriptions, or to confirm whether a parent plan68_task directory exists.\n\n"
            )
        prompt += f"=== EVIDENCE FROM TOOLS ===\n{evidence}\n\n"
        if thoughts_text:
            prompt += f"=== REASONING PROCESS ===\n{thoughts_text}\n\n"
        prompt += "Please provide your complete answer now:"

        synthesis_timeout = _default_synthesis_timeout_seconds()
        synthesis_max_tokens = _default_synthesis_max_tokens()
        logger.info(
            "[DEEP_THINK_NATIVE] Forced synthesis attempt (timeout=%ss max_tokens=%s)",
            synthesis_timeout,
            synthesis_max_tokens,
        )
        raw = await asyncio.wait_for(
            agent._chat_text_streaming(prompt, max_tokens=synthesis_max_tokens),
            timeout=synthesis_timeout,
        )
        cleaned = sanitize_professional_response_text(str(raw or "").strip())
        if len(cleaned) < 30 or _dta().is_process_only_answer(cleaned, user_query=user_query):
            logger.warning("[DEEP_THINK_NATIVE] Forced synthesis produced insufficient content (%d chars)", len(cleaned))
            return ""
        if structured_plan_outcome.get("required") and not structured_plan_outcome.get("satisfied"):
            return agent._build_structured_plan_contract_failure_answer(
                outcome=structured_plan_outcome,
                user_query=user_query,
            )
        cleaned = _strip_runtime_absolute_paths(
            _ensure_inline_images(cleaned, agent._collect_inline_image_relpaths())
        )
        logger.info("[DEEP_THINK_NATIVE] Forced synthesis succeeded (%d chars)", len(cleaned))
        return cleaned
    except Exception as exc:
        logger.warning("[DEEP_THINK_NATIVE] Forced synthesis failed: %r", exc, exc_info=True)
        return ""


async def _fallback_answer_from_steps(
    agent: "DeepThinkAgent",
    steps: List[ThinkingStep],
    user_query: str = "",
    task_context: Optional[TaskExecutionContext] = None,
) -> str:
    language = _dta().detect_reasoning_language(user_query)
    if not steps:
        return _dta()._localized_text(
            language,
            "已完成思考，但暂未形成结构化结论。",
            "DeepThink finished without a structured final answer.",
        )
    evidence = agent._collect_evidence_snippets(steps)
    uq = (user_query or "").strip()
    if agent._is_research_or_execute() and evidence.strip() and uq:
        try:
            fallback_kwargs: Dict[str, Any] = {}
            if task_context is not None:
                fallback_kwargs["task_context"] = task_context
            generated = await agent._generate_fallback_from_evidence(
                uq,
                evidence,
                steps,
                **fallback_kwargs,
            )
            if generated and not agent._should_reject_missing_task_definition_answer(
                generated,
                task_context=task_context,
            ):
                return generated
        except Exception:
            logger.warning(
                "DeepThink fallback synthesis from tool evidence failed; using structured fallback.",
                exc_info=True,
            )
    useful = [
        s.thought
        for s in steps
        if isinstance(s.thought, str)
        and s.thought.strip()
        and not _dta().is_process_only_answer(s.thought, user_query=user_query)
    ]
    if useful and not agent._is_research_or_execute():
        return sanitize_professional_response_text(
            _dta().sanitize_reasoning_text(
                useful[-1].strip(),
                language=language,
                max_chars=180,
            )
            or useful[-1].strip()
        )
    bound_execute_fallback = agent._build_bound_execute_task_fallback(
        steps,
        user_query=user_query,
        task_context=task_context,
    )
    if bound_execute_fallback:
        return bound_execute_fallback
    return agent._build_structured_fallback(steps, user_query)


async def _generate_summary(agent: "DeepThinkAgent", steps: List[ThinkingStep], user_query: str) -> str:
    """Build a concise DeepThink summary from visible steps (no LLM call).

    Previous implementation made an extra LLM API call here, which added
    1-10 seconds of latency *after* the final answer was already streamed
    to the user, delaying the ``final`` SSE event and keeping the UI in a
    "still thinking" state.  The frontend ``getProcessSummary`` already has
    a fallback that builds the summary from step display texts, so an LLM
    call is unnecessary.
    """
    language = _dta().detect_reasoning_language(user_query)
    selected = agent._select_steps_for_summary(steps)
    labels: List[str] = []
    for step in selected:
        visible = _dta().build_user_visible_step(step, language=language)
        display_text = str(visible.get("display_text") or "").strip()
        if display_text:
            labels.append(display_text)
    if len(labels) > 1:
        return " → ".join(labels[:3])
    if labels:
        return labels[0]
    return _dta()._default_deepthink_summary(user_query)
