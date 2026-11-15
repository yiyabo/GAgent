"""State and copy for the Plan-mode offer.

The decision to offer lives in ``plan_mode_offer_gate`` (model-reviewed); this
module only answers whether the session state permits an offer and supplies the
localized card copy.
"""

from __future__ import annotations

import re


def offer_state_allows(
    *,
    plan_mode: bool,
    bound_plan_id: int | None,
    dismissed: bool,
) -> bool:
    """Only an unplanned, unbound, not-yet-dismissed session can be offered."""
    return not plan_mode and bound_plan_id is None and not dismissed


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
