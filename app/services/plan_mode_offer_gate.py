"""LLM review for the Plan-mode offer decision.

Regex is used only for cheap recall: a message that never mentions plans or
planning is not worth a model call. The actual decision — should this turn
pause and ask the user to enter Plan mode? — is made by the model, so
background mentions, negations, hypotheticals and how-to questions do not
trigger a card.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from app.services.plans.edit_policy import plan_change_deferred

logger = logging.getLogger(__name__)

GATE_TIMEOUT_SECONDS = 12.0
GATE_REQUEST_TIMEOUT_SECONDS = 8.0
GATE_MAX_TOKENS = 256
GATE_MAX_MESSAGE_CHARS = 4000

# Recall only: planning vocabulary in either language. This deliberately
# over-matches ("项目计划", "planning docs", "规划") because the model decides.
_OFFER_CANDIDATE = re.compile(
    r"计划|方案书|规划|计划书|\bplan(?:ning|ned|s)?\b|\broadmap\b", re.IGNORECASE
)

_SYSTEM_PROMPT = """You are a routing gate for a research assistant's chat UI.
Decide ONE thing: should this turn pause now and show a "Plan mode" consent
card before any work runs?

Show the card ONLY when the user is asking the assistant to produce a plan
(or to work in a planning / read-only mode) as the goal of the current turn.

Do NOT show the card when "plan/计划/规划" is:
- background, narration, a quoted document, or a hypothetical
  ("our proposal says we plan to...", "if we were to write a plan...")
- negated ("do not create a plan", "请勿制定计划", "不用计划")
- a meta or how-to question ("how do we create a plan?", "如何制定计划")
- a reference to an existing plan or task
- an ordinary request to just analyze, answer or execute now
- a continuation ("continue", "直接继续", "no plan")

Return STRICT JSON only, no markdown and no reasoning text:
{"decision":"offer"|"proceed","reason":"short reason under 80 characters"}

Examples:
User: "调研噬菌体裂解酶最新进展，并制定完整研究计划，之后执行小规模验证。"
Assistant: {"decision":"offer","reason":"用户明确要求制定完整研究计划"}
User: "我们的申请书里写着：下一步计划制定完整的研究方案。请帮我润色这段背景。"
Assistant: {"decision":"proceed","reason":"计划只是引用背景，当前任务是润色"}
User: "请勿制定计划，直接分析这批候选蛋白。"
Assistant: {"decision":"proceed","reason":"用户明确否定创建计划"}
User: "如何制定一份完整的研究计划？"
Assistant: {"decision":"proceed","reason":"询问方法，不是要求创建"}
User: "帮我制定一份完整的研究计划。"
Assistant: {"decision":"offer","reason":"明确的计划创建请求"}
"""


@dataclass(frozen=True)
class PlanOfferGateDecision:
    """One gate verdict plus provenance for logs and response metadata."""

    offer: bool
    reason: str
    source: str  # "llm" | "fallback"


def is_offer_candidate(message: str) -> bool:
    """Cheap recall filter; never a final decision."""
    text = str(message or "")
    return not plan_change_deferred(text) and bool(_OFFER_CANDIDATE.search(text))


def _build_prompt(message: str) -> str:
    text = str(message or "").strip()
    if len(text) > GATE_MAX_MESSAGE_CHARS:
        text = text[:GATE_MAX_MESSAGE_CHARS] + "\n[truncated]"
    return f'{_SYSTEM_PROMPT}\nUser message:\n"""\n{text}\n"""\n\nJSON:'


def _parse_decision(raw: str) -> tuple[bool, str]:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("gate reply has no JSON object")
    try:
        payload = json.loads(text[start : end + 1])
    except ValueError as exc:
        raise ValueError("gate reply is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("gate reply is not an object")
    decision = str(payload.get("decision") or "").strip().lower()
    if decision not in {"offer", "proceed"}:
        raise ValueError(f"unsupported gate decision: {decision!r}")
    reason = str(payload.get("reason") or "").strip()[:160]
    return decision == "offer", reason


async def review_plan_offer(message: str, llm_service: Any) -> PlanOfferGateDecision:
    """Ask the model whether this turn should pause with a Plan offer.

    Any failure (timeout, provider error, unparseable reply) fails open: the
    turn proceeds without a card and the Skill can still suggest planning.
    Logging keeps these silent failures visible.
    """
    try:
        raw = await asyncio.wait_for(
            llm_service.chat_async(
                _build_prompt(message),
                max_tokens=GATE_MAX_TOKENS,
                timeout=GATE_REQUEST_TIMEOUT_SECONDS,
                retries=0,
            ),
            timeout=GATE_TIMEOUT_SECONDS,
        )
        offer, reason = _parse_decision(raw)
        return PlanOfferGateDecision(
            offer=offer, reason=reason or "llm_decided", source="llm"
        )
    except Exception as exc:  # pragma: no cover - provider/parse failures
        logger.warning(
            "[CHAT][PLAN_OFFER] gate unavailable, proceeding without offer: %s", exc
        )
        return PlanOfferGateDecision(
            offer=False, reason="gate_unavailable", source="fallback"
        )
