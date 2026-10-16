from __future__ import annotations

from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict

PROFESSIONAL_STYLE_INSTRUCTION = (
    "Use a professional, calm, and credible tone. "
    "Do not use celebratory, decorative, or playful emojis by default. "
    "Avoid emojis in headings, bullets, tables, labels, and summaries unless the user explicitly asks for a casual style."
)


def sanitize_professional_response_text(text: str | None) -> str:
    """Pass-through: emoji filtering is now handled at the prompt level only.

    Previously this function stripped decorative emoji prefixes and
    emoji-only lines.  That was overly aggressive — the LLM prompt
    already instructs a professional tone, so post-processing removal
    is no longer needed.
    """
    return str(text or "").strip()


ResponseStyle = Literal["general", "product_manager", "senior_developer", "research_advisor"]
ResponseLength = Literal["auto", "concise", "detailed"]
STYLE_VERSION = 1
SNAPSHOT_KEY = "chat_response_preferences"


class ResponsePreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    response_style: ResponseStyle = "general"
    response_length: ResponseLength = "auto"


_STYLE_GUIDANCE = {
    "general": "",
    "product_manager": "Organize explanations around user needs, priorities, tradeoffs and practical next steps. Use product terminology only when helpful.",
    "senior_developer": "Emphasize root causes, concrete implementation choices, verification and maintenance tradeoffs. Explain technical details at the user's level.",
    "research_advisor": "Distinguish evidence, inference and uncertainty. Explain methods and the limits of conclusions. Cite sources only when available and relevant.",
}
_LENGTH_GUIDANCE = {
    "auto": "",
    "concise": "Prefer a brief, direct answer with only the details needed to act; retain material caveats and requested content.",
    "detailed": "Explain the reasoning and relevant details with useful examples where helpful; avoid padding and repetition.",
}


def preferences_from_settings(settings: Mapping[str, Any] | None) -> ResponsePreferences:
    settings = settings or {}
    # Tolerate legacy/invalid stored metadata without weakening request validation.
    values = {}
    for key in ResponsePreferences.model_fields:
        value = settings.get(key)
        try:
            values[key] = ResponsePreferences(**{key: value}).model_dump()[key]
        except ValueError:
            pass
    return ResponsePreferences(**values)


def snapshot_response_preferences(request: Any, session_settings: Mapping[str, Any] | None) -> dict:
    """Freeze a turn once. Persisted requests retain this snapshot on resume."""
    context = dict(request.context or {})
    prior = context.get(SNAPSHOT_KEY)
    if isinstance(prior, dict) and prior.get("version") == STYLE_VERSION:
        preferences = preferences_from_settings(prior)
    else:
        values = preferences_from_settings(session_settings).model_dump()
        for key in ResponsePreferences.model_fields:
            value = getattr(request, key, None)
            if value is not None:
                values[key] = value
        preferences = ResponsePreferences(**values)
    snapshot = {**preferences.model_dump(), "version": STYLE_VERSION}
    context[SNAPSHOT_KEY] = snapshot
    request.context = context
    return snapshot


def response_style_instruction(context: Mapping[str, Any] | None = None) -> str:
    snapshot = (context or {}).get(SNAPSHOT_KEY)
    preferences = preferences_from_settings(snapshot if isinstance(snapshot, dict) else None)
    if preferences == ResponsePreferences():
        return PROFESSIONAL_STYLE_INSTRUCTION
    guidance = " ".join(filter(None, (
        _STYLE_GUIDANCE[preferences.response_style], _LENGTH_GUIDANCE[preferences.response_length],
    )))
    return (
        PROFESSIONAL_STYLE_INSTRUCTION + "\nChat reply preferences: " + guidance +
        " These are communication preferences, not a claim of professional credentials. "
        "The user's explicit instructions in the current turn about style, language, length or deliverable format take precedence. "
        "Apply these preferences to conversational replies only; preserve requested artifact formats and tool protocols. "
        "They do not authorize creating, editing, decomposing or executing a Plan, change task scope, "
        "or require extra tools, reviews or work. Keep deterministic tool receipts factual and brief."
    )


def attach_response_preferences(metadata: dict, context: Mapping[str, Any] | None) -> None:
    snapshot = (context or {}).get(SNAPSHOT_KEY)
    if isinstance(snapshot, dict):
        metadata[SNAPSHOT_KEY] = dict(snapshot)
