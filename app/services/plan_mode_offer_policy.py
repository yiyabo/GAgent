"""Deterministic policy: when to interrupt a chat with a Plan-mode offer.

The Skill-based offer still exists, but model discretion alone proved
unreliable: a live multi-stage request executed everything without offering.
This policy only decides whether to surface the *offer card*; it never enables
Plan mode, creates a Plan, or runs work.
"""

from __future__ import annotations

import re

OFFER_MIN_CHARS = 20

# Explicit request for a persistent plan. Qualifiers are optional because
# "制定完整研究计划" and "制定完整的研究计划" must both match.
_EXPLICIT_PLAN = re.compile(
    r"(?:制定|构建|设计|生成|编写|写|给出|做)\s*(?:一份|一个|个)?\s*"
    r"(?:完整|详细|可执行|系统)?\s*(?:的)?\s*(?:研究|项目|实验|研发)?\s*"
    r"(?:计划|方案书|计划书)"
    r"|\b(?:create|build|make|write|draft|design)\s+(?:a\s+|an\s+)?"
    r"(?:complete\s+|detailed\s+|executable\s+)?(?:research\s+|project\s+)?plan\b",
    re.IGNORECASE,
)

_RESEARCH = re.compile(
    r"调研|检索|综述|分析|评估|梳理|文献|survey|research|review|analy", re.IGNORECASE
)
_EXECUTION = re.compile(
    r"执行|验证|测试|落地|实施|跑一遍|实验|execute|run|validate|test|experiment",
    re.IGNORECASE,
)

# A reply that just answers the offer card's "Continue directly" must never
# produce a second card.
_CONTINUATION = re.compile(
    r"^(?:直接继续|继续|不用计划|不要计划|不需要计划|无需计划|just continue|continue|no plan|skip plan)\s*[。.!！]?$",
    re.IGNORECASE,
)


def should_offer_plan_mode(
    message: str,
    *,
    plan_mode: bool,
    bound_plan_id: int | None,
    dismissed: bool,
) -> bool:
    """Return True only when the user explicitly asks for a plan.

    A bare research-plus-execution request stays model-discretionary (the
    ``plan-mode-guidance`` Skill); interrupting every such request would stop
    ordinary multi-step work that never asked for planning.
    """
    text = str(message or "").strip()
    if plan_mode or bound_plan_id is not None or dismissed:
        return False
    if len(text) < OFFER_MIN_CHARS or _CONTINUATION.match(text):
        return False
    return bool(_EXPLICIT_PLAN.search(text))


def offer_reason(message: str) -> str:
    text = str(message or "")
    if re.search(r"[\u4e00-\u9fff]", text):
        return "这个请求包含多阶段研究与执行，适合先生成可审查的计划。"
    return "This request spans research and execution stages, so a reviewable Plan comes first."


def offer_text(message: str) -> str:
    """User-visible prompt shown next to the offer card."""
    if re.search(r"[\u4e00-\u9fff]", str(message or "")):
        return (
            "这个请求包含多阶段研究与执行。建议先进入 Plan 模式，完成只读研究并创建结构化计划。"
            "请选择“进入 Plan 模式并创建计划”或“直接继续”。在你做出选择前，本轮不会创建或执行计划。"
        )
    return (
        "This request spans research and execution stages. Entering Plan mode would let me "
        "research and build a reviewable Plan first. Choose “Enter Plan mode and create a Plan” "
        "or “Continue directly”; nothing is created or executed until you choose."
    )
