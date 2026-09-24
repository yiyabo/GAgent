"""Pure text/regex helpers for the DeepThink agent.

Extracted from app.services.deep_think_agent (god-class split, behaviour
zero-change). Everything here is module-level and side-effect free: regex
constants, env-knob readers, and string utilities shared by the guard,
synthesis, and prompt families.

deep_think_agent re-exports every public name so existing imports (including
tests that monkeypatch the env knobs on the deep_think_agent namespace) keep
working.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional


def _default_max_consecutive_llm_failures() -> int:
    raw = os.getenv("DEEP_THINK_MAX_CONSECUTIVE_LLM_FAILURES", "5")
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 5


# Model context-window table (prefix-matched; only confirmed values, extend
# conservatively). Used to size the compaction budget relative to the model's
# real window instead of the historical fixed 32k.
_MODEL_CONTEXT_WINDOWS = (
    ("qwen3.7-max", 1_000_000),
    ("qwen-max", 262_144),
    ("qwen-plus", 131_072),
    ("gpt-4o", 128_000),
    ("kimi-k2", 262_144),
)
_DEFAULT_CONTEXT_WINDOW = 128_000
_CONTEXT_BUDGET_FLOOR = 32_000


def _resolve_context_budget_tokens(model: str) -> int:
    """Resolve the DeepThink compaction budget (native path) for ``model``.

    Priority: explicit ``DEEP_THINK_CONTEXT_BUDGET_TOKENS`` override (legacy
    semantics, including 0 = disabled) > model window x
    ``DEEP_THINK_CONTEXT_BUDGET_RATIO`` (default 0.5), clamped to
    [32k floor, ``DEEP_THINK_CONTEXT_BUDGET_MAX`` (default 131072)]. The floor
    preserves the historical 32k behaviour for small-window models.
    """
    raw_override = os.getenv("DEEP_THINK_CONTEXT_BUDGET_TOKENS", "").strip()
    if raw_override:
        try:
            return max(0, int(raw_override))
        except ValueError:
            pass  # fall through to model-aware resolution
    window = _DEFAULT_CONTEXT_WINDOW
    normalized = (model or "").strip().lower()
    for prefix, tokens in _MODEL_CONTEXT_WINDOWS:
        if normalized.startswith(prefix):
            window = tokens
            break
    try:
        ratio = float(os.getenv("DEEP_THINK_CONTEXT_BUDGET_RATIO", "0.5") or "0.5")
    except (TypeError, ValueError):
        ratio = 0.5
    ratio = min(max(ratio, 0.05), 0.9)
    try:
        cap = int(os.getenv("DEEP_THINK_CONTEXT_BUDGET_MAX", "131072") or "131072")
    except (TypeError, ValueError):
        cap = 131_072
    budget = int(window * ratio)
    return max(_CONTEXT_BUDGET_FLOOR, min(cap, budget))


def _default_synthesis_timeout_seconds() -> int:
    # Thinking models (e.g. qwen3.8-flash) routinely need >60s to synthesize a
    # long evidence prompt; a 60s cap cancelled forced synthesis on long runs.
    # Non-streaming calls buffer the whole generation, so allow ample headroom.
    raw = os.getenv("DEEP_THINK_SYNTHESIS_TIMEOUT_SECONDS", "300")
    try:
        return max(30, int(raw))
    except (TypeError, ValueError):
        return 300


def _default_fallback_timeout_seconds() -> int:
    # The evidence fallback also runs non-streaming against a thinking model;
    # the previous 45s default timed out on every attempt of real long runs.
    raw = os.getenv("DEEP_THINK_FALLBACK_TIMEOUT_SECONDS", "150")
    try:
        return max(30, int(raw))
    except (TypeError, ValueError):
        return 150


def _acceptance_v2_enabled() -> bool:
    # Declarative acceptance v2 (LLM spec extraction) is ON by default; set
    # DEEP_THINK_ACCEPTANCE_V2_ENABLED=0 to force every run back onto the v1
    # type heuristic. Any extraction failure silently falls back to v1, so the
    # default-on path is never a single point of failure.
    raw = os.getenv("DEEP_THINK_ACCEPTANCE_V2_ENABLED", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _acceptance_v2_timeout_seconds() -> int:
    # Hard-capped at 15s: spec extraction is a small auxiliary call and must
    # never stall the run; on timeout the run silently keeps the v1 heuristic.
    raw = os.getenv("DEEP_THINK_ACCEPTANCE_V2_TIMEOUT_SECONDS", "15")
    try:
        return max(5, min(15, int(raw)))
    except (TypeError, ValueError):
        return 15


def _acceptance_v2_max_tokens() -> int:
    raw = os.getenv("DEEP_THINK_ACCEPTANCE_V2_MAX_TOKENS", "800")
    try:
        return max(128, int(raw))
    except (TypeError, ValueError):
        return 800


def _progress_free_nudge_streak() -> int:
    # Execute-tier runs that stop producing new deliverables get a finalize
    # nudge after this many consecutive progress-free iterations.
    raw = os.getenv("DEEP_THINK_PROGRESS_FREE_NUDGE", "8")
    try:
        return max(3, int(raw))
    except (TypeError, ValueError):
        return 8


def _progress_free_break_streak() -> int:
    # Hard stop for progress-free execute-tier runs. Deliberately looser than
    # the nudge so slow-but-legitimate pipelines (downloads, renders) are not
    # killed while still working toward their first deliverable.
    raw = os.getenv("DEEP_THINK_PROGRESS_FREE_BREAK", "14")
    try:
        return max(4, int(raw))
    except (TypeError, ValueError):
        return 14


def _failure_signature_warn_count() -> int:
    raw = os.getenv("DEEP_THINK_FAILURE_SIG_WARN", "3")
    try:
        return max(2, int(raw))
    except (TypeError, ValueError):
        return 3


def _failure_signature_break_count() -> int:
    raw = os.getenv("DEEP_THINK_FAILURE_SIG_BREAK", "5")
    try:
        return max(3, int(raw))
    except (TypeError, ValueError):
        return 5


def _time_budget_nudge_seconds() -> int:
    # Iteration-based endgames cannot bound wall-clock time: one nested
    # qwen-code CLI call takes 6-8 minutes, so 14 "iterations" can exceed an
    # hour. Execute-tier runs get a hard wall-clock budget.
    raw = os.getenv("DEEP_THINK_TIME_BUDGET_NUDGE", "600")
    try:
        return max(60, int(raw))
    except (TypeError, ValueError):
        return 600


def _time_budget_break_seconds() -> int:
    raw = os.getenv("DEEP_THINK_TIME_BUDGET_BREAK", "900")
    try:
        return max(120, int(raw))
    except (TypeError, ValueError):
        return 900


def _default_synthesis_max_tokens() -> int:
    raw = os.getenv("DEEP_THINK_SYNTHESIS_MAX_TOKENS", "6000")
    try:
        return max(1000, int(raw))
    except (TypeError, ValueError):
        return 6000


_OUTPUT_FILE_RE = re.compile(r"[A-Za-z0-9_\-.]+\.(?:md|csv|xlsx|xls|json|png|html?|txt|fasta|pdf)")

_BARE_READ_MARKER_RE = re.compile(r"^-\s*已读取文件：[^：\n]*$")

_DELIVERABLE_FILE_RE = re.compile(r"[A-Za-z0-9_\-.]+\.(?:png|pdf|svg|md|csv|xlsx|xls|html?|fasta|fa)")


def _collect_deliverable_file_names(evidence: str, limit: int = 6) -> List[str]:
    """Extract likely deliverable file names from evidence text.

    Deliberately excludes .txt/.json so scratch probes and status dumps are
    not presented as deliverables.
    """
    names: List[str] = []
    for match in _DELIVERABLE_FILE_RE.finditer(evidence or ""):
        name = match.group(0).rsplit("/", 1)[-1]
        if len(name) > 8 and name not in names:
            names.append(name)
        if len(names) >= limit:
            break
    return names


def _collect_deliverable_display_names(evidence: str, limit: int = 6) -> List[str]:
    """Deliverable names shown to users in the fallback hint.

    Strict extension list, and a match whose containing path segment lives
    under uploads/ is the user's own input file — never a deliverable.
    """
    names: List[str] = []
    text = evidence or ""
    for match in _DELIVERABLE_FILE_RE.finditer(text):
        name = match.group(0).rsplit("/", 1)[-1]
        # walk back to the previous path/text separator; the segment between
        # it and the match is the containing directory chain
        seg_start = match.start()
        while seg_start > 0 and text[seg_start - 1] not in " \t\r\n,;，；：（(）)\"'`":
            seg_start -= 1
        segment = text[seg_start:match.start()]
        if "uploads/" in segment:
            continue
        if len(name) > 8 and name not in names:
            names.append(name)
        if len(names) >= limit:
            break
    return names


_GUARD_DELIVERABLE_EXT_RE = re.compile(
    r"\.(?:png|jpe?g|svg|pdf|md|markdown|csv|xlsx?|tsv|json|html?|txt|fasta|fa)$",
    re.IGNORECASE,
)
# Scratch/probe locations never count as deliverables. Real outputs live under
# deliverables/ or results/ (enforced again in _extract_guard_candidates).
# Absolute /tmp/ is excluded at the call site; a bare "tmp" path segment would
# also nuke legitimate paths like /data/tmp_results/... or pytest sandboxes.
_GUARD_SCRATCH_RE = re.compile(r"(?:^|/)(?:tool_outputs|_scratch|uploads|raw_files|workspaces)/", re.IGNORECASE)
_GUARD_PRODUCTIVE_DIR_RE = re.compile(r"(?:^|/)(?:deliverables|results)/", re.IGNORECASE)
_GUARD_PATH_NORMALIZE_RE = re.compile(r"/[^\s,;:'\")\]]+")
_GUARD_DIGIT_RE = re.compile(r"\d+")

_INLINE_IMAGE_EXT_RE = re.compile(r"\.(?:png|jpe?g|gif|webp|svg)$", re.IGNORECASE)
_PRODUCTIVE_SEGMENT_RE = re.compile(r"(?:deliverables|results)/", re.IGNORECASE)

# Image-only widened productive locations (pi/code_executor figures land in
# figures/, raw_files/tmp/<run>/, deliverables/latest/image_tabular/ as well).
# Used ONLY by the inline-image collection path; deliverable progress
# semantics (_GUARD_PRODUCTIVE_DIR_RE / _PRODUCTIVE_SEGMENT_RE) stay unchanged.
_INLINE_IMAGE_PRODUCTIVE_RE = re.compile(
    r"(?:^|/)(?:deliverables|results|figures|image_tabular|raw_files/tmp|raw_files/chat_tools)/",
    re.IGNORECASE,
)
# raw_files/tmp/ (delegated run workspaces) and raw_files/chat_tools/
# (chat-scoped tool outputs, e.g. scientific_figure_generator) are scratch for
# deliverable progress but productive for images.
_INLINE_IMAGE_RAW_TMP_RE = re.compile(r"(?:^|/)raw_files/(?:tmp|chat_tools)/", re.IGNORECASE)


def _ensure_inline_images(text: str, image_relpaths: List[str]) -> str:
    """Guarantee produced images render inline in the final answer.

    The frontend resolves a relative image path against the session artifact
    endpoint, so `![caption](deliverables/x.png)` renders the actual figure.
    For each produced image: keep an existing inline reference, upgrade a
    plain markdown link, convert a bare filename line, or — only when no
    anchor exists at all — append the image at the end.
    """
    out = text or ""
    for rel in image_relpaths or []:
        rel = str(rel or "").strip().lstrip("/")
        if not rel or ".." in rel or "\\" in rel:
            continue
        name = rel.rsplit("/", 1)[-1]
        inline_match = re.search(
            r"!\[([^\]\n]*)\]\(([^)\n]*" + re.escape(name) + r"[^)\n]*)\)", out
        )
        if inline_match:
            url = inline_match.group(2).strip()
            # An inline ref pointing at an absolute/container path (the model
            # often copies the tool result's /app/runtime/<sid>/... verbatim)
            # resolves to nothing in the frontend — rewrite it to the
            # session-relative rel instead of keeping it.
            if url.startswith("/") or ".." in url or "\\" in url:
                out = out[: inline_match.start(2)] + rel + out[inline_match.end(2) :]
            continue
        link_match = re.search(r"\[([^\]\n]*)\]\(([^)\n]*" + re.escape(name) + r"[^)\n]*)\)", out)
        if link_match:
            caption = link_match.group(1) or name
            out = out[: link_match.start()] + f"![{caption}]({rel})" + out[link_match.end() :]
            continue
        bare_match = re.search(
            r"(?m)^(?P<prefix>\s*(?:[-*]\s+)?)`?" + re.escape(name) + r"`?\s*$",
            out,
        )
        if bare_match:
            out = (
                out[: bare_match.start()]
                + f"{bare_match.group('prefix')}![{name}]({rel})"
                + out[bare_match.end() :]
            )
            continue
        # The filename is mentioned mid-line (backticked, inside a sentence or
        # a composite bullet): keep the text and place the image right after
        # the mentioning line so the figure appears where it is referenced.
        out_lines = out.split("\n")
        mention_idx = next((i for i, line in enumerate(out_lines) if name in line), None)
        if mention_idx is not None:
            out_lines[mention_idx + 1 : mention_idx + 1] = ["", f"![{name}]({rel})", ""]
            out = "\n".join(out_lines)
            continue
        out = out.rstrip() + f"\n\n![{name}]({rel})\n"
    return out


def _strip_runtime_absolute_paths(text: str) -> str:
    """Rewrite ``/app/runtime/<session>/x`` to session-relative ``x`` in final answers.

    Models often paste the tool result's container-absolute path into prose
    and manifest lists; those paths are meaningless to the frontend and the
    user. Strip the runtime root ($APP_RUNTIME_ROOT, default /app/runtime)
    plus the session segment wherever it prefixes a path.
    """
    if not text:
        return text or ""
    runtime_root = str(os.getenv("APP_RUNTIME_ROOT") or "/app/runtime").strip().rstrip("/")
    if not runtime_root:
        return text
    return re.sub(re.escape(runtime_root) + r"/[^/\s`'\)\]]+/", "", text)


# ---------------------------------------------------------------------------
# Declarative acceptance (v1): derive the deliverable types the user asked
# for and let the loop guard judge completion, not just act as a fuse.
# ---------------------------------------------------------------------------

_EXPECT_IMAGE_RE = re.compile(
    r"(饼图|柱状图|条形图|折线图|散点图|直方图|热力图|箱线图|流程图|示意图|曲线图|图表"
    r"|plot|chart|figure|histogram|scatter|heatmap|bar\s?chart|pie\s?chart|line\s?chart|可视化|绘制|画图|画一)",
    re.IGNORECASE,
)
_EXPECT_DATA_RE = re.compile(
    r"(csv|tsv|excel|xlsx|xls|spreadsheet|表格文件|数据表)",
    re.IGNORECASE,
)
_EXPECT_DOC_RE = re.compile(
    r"(报告|文档|docx|pdf|markdown|word文档|总结报告|report)",
    re.IGNORECASE,
)
_EXPECT_KIND_EXTS = {
    "image": (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"),
    "data": (".csv", ".tsv", ".xlsx", ".xls"),
    "document": (".md", ".markdown", ".pdf", ".docx", ".html", ".htm", ".txt"),
}
_EXPECT_KIND_LABEL = {
    "image": {"zh": "图片", "en": "image/figure"},
    "data": {"zh": "数据表（csv/excel）", "en": "data table (csv/excel)"},
    "document": {"zh": "文档（md/pdf 等）", "en": "document (md/pdf etc.)"},
}


def _derive_expected_outputs(query: str) -> List[str]:
    """Heuristically derive required deliverable types from the user request.

    Conservative: no clear file-type intent -> no expectations, and the loop
    guard keeps its current fuse-only behaviour.
    """
    q = str(query or "")
    expected: List[str] = []
    if _EXPECT_IMAGE_RE.search(q):
        expected.append("image")
    if _EXPECT_DATA_RE.search(q):
        expected.append("data")
    if _EXPECT_DOC_RE.search(q):
        expected.append("document")
    return expected


def _missing_expectations(expected: List[str], verified_paths: List[str]) -> List[str]:
    """Expected kinds with no verified on-disk deliverable of a matching type."""
    missing: List[str] = []
    for kind in expected or []:
        exts = _EXPECT_KIND_EXTS.get(kind)
        if not exts:
            continue
        if not any(str(p).lower().endswith(exts) for p in verified_paths or []):
            missing.append(kind)
    return missing


def _missing_expectations_detailed(
    expected: List[str],
    verified_paths: List[str],
    kind_requirements: Optional[Dict[str, int]] = None,
) -> List[str]:
    """Count-aware variant of _missing_expectations (acceptance v2).

    With no requirements this is byte-identical to the v1 check. Requirements
    map kind -> minimum count; a shortfall renders as ``<kind>x<n>`` so the
    guard/final answer can state how many deliverables are still missing.
    """
    if not kind_requirements:
        return _missing_expectations(expected, verified_paths)
    missing: List[str] = []
    for kind, needed in kind_requirements.items():
        exts = _EXPECT_KIND_EXTS.get(kind)
        if not exts or needed <= 0:
            continue
        have = sum(1 for p in verified_paths or [] if str(p).lower().endswith(exts))
        if have < needed:
            shortfall = needed - have
            missing.append(f"{kind}x{shortfall}" if needed > 1 else kind)
    for kind in _missing_expectations(expected, verified_paths):
        if kind not in kind_requirements:
            missing.append(kind)
    return missing


def _guard_json_payload(value: Any) -> Optional[Dict[str, Any]]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = json.loads(value)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _drop_process_echo_bullets(text: str) -> str:
    """Remove process-echo bullets from user-facing fallback evidence.

    Raw terminal dumps, bare "file was read" markers, and internal guard
    rejections (target_task_not_atomic) read as half-finished execution debris
    to users; outcome bullets (writes, products, listings, content previews)
    are kept.
    """
    if not text:
        return ""
    kept: List[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- 终端输出："):
            continue
        if _BARE_READ_MARKER_RE.match(stripped):
            continue
        if "target_task_not_atomic" in stripped:
            continue
        kept.append(line)
    return "\n".join(kept).strip()


_CLI_PROTOCOL_MARKERS = ('"subtype":"init"', '"type":"system"', '"type":"result"')


def _looks_like_cli_protocol_json(stripped: str) -> bool:
    return (
        len(stripped) > 120
        and stripped.startswith(('[{"type"', '{"type"'))
        and any(marker in stripped for marker in _CLI_PROTOCOL_MARKERS)
    )


def _strip_cli_noise_from_multiline(raw: str) -> str:
    """Drop CLI protocol JSON lines from a stdout/stderr block before previewing.

    The delegated CLI prints its init/result events as single huge lines; when
    such a block is previewed inside a humanized bullet the whole protocol
    dump rides along unless filtered here.
    """
    if not raw:
        return raw
    kept = [ln for ln in raw.splitlines() if not _looks_like_cli_protocol_json(ln.strip())]
    return "\n".join(kept)


def _strip_cli_stream_noise(text: str) -> str:
    """Drop qwen-code CLI protocol JSON blobs (system/init/result events) from evidence text.

    When a delegated CLI run's stdout is summarized as tool output, its init JSON
    can dominate the fallback summary while carrying no user-facing information.
    """
    if not text:
        return text
    kept: List[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if _looks_like_cli_protocol_json(stripped):
            continue
        kept.append(line)
    return "\n".join(kept)


def _collect_output_file_names(evidence: str, limit: int = 6) -> List[str]:
    """Best-effort extraction of generated output file names from evidence text."""
    names: List[str] = []
    for match in _OUTPUT_FILE_RE.finditer(evidence or ""):
        name = match.group(0).rsplit("/", 1)[-1]
        if len(name) > 8 and name not in names:
            names.append(name)
        if len(names) >= limit:
            break
    return names
