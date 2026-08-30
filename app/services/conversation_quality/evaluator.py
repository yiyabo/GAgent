"""LLM-backed structured evaluator for conversation quality."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional, Tuple

from app.llm import LLMClient, clear_usage_context, set_usage_context
from app.services.foundation.settings import get_settings

from .models import ConversationQualityResult
from .prompt import PROMPT_VERSION, build_quality_evaluation_prompt


def _json_object(raw: str) -> Dict[str, Any]:
    candidate = str(raw or "").strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*|\s*```$", "", candidate, flags=re.IGNORECASE)
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ValueError("quality evaluator returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("quality evaluator must return a JSON object")
    return value


class ConversationQualityEvaluator:
    """Isolated evaluator client; it never participates in the main chat response."""

    def __init__(self, client: Optional[LLMClient] = None) -> None:
        settings = get_settings()
        self.provider = str(settings.quality_evaluator_provider or settings.llm_provider)
        configured_model = str(settings.quality_evaluator_model or "").strip() or None
        self.client = client or LLMClient(
            provider=self.provider,
            model=configured_model,
            timeout=min(120, max(10, int(settings.llm_request_timeout))),
        )
        self.provider = str(getattr(self.client, "provider", self.provider))
        self.model = str(getattr(self.client, "model", configured_model or "unknown"))

    async def evaluate(
        self,
        *,
        snapshot: Dict[str, Any],
        feedback_message: Optional[Dict[str, Any]],
        evaluation_basis: str,
        session_id: str,
    ) -> Tuple[ConversationQualityResult, str]:
        prompt = build_quality_evaluation_prompt(
            snapshot=snapshot,
            feedback_message=feedback_message,
            evaluation_basis=evaluation_basis,
        )
        token = set_usage_context(
            session_id=session_id,
            call_purpose="conversation_quality_evaluation",
            phase="audit",
        )
        try:
            raw = await self.client.chat_async(
                prompt,
                max_tokens=1800,
                retries=1,
            )
        finally:
            clear_usage_context(token)
        return ConversationQualityResult.model_validate(_json_object(raw)), PROMPT_VERSION
