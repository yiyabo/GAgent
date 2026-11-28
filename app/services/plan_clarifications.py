"""Structured clarifying questions for explicit Plan mode.

The model may ask 1-3 multiple-choice questions while drafting a Plan. Asking is
non-blocking: the chat turn ends with a question card, the session is marked as
waiting for the user, and the answer arrives as an ordinary next message. No run
stays open while the user thinks, so deploy gates and worker leases are never held.

This module owns validation, per-session state, prompt guidance and metadata
projection. It never creates, edits or executes a Plan.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any
from uuid import uuid4

MAX_QUESTIONS = 3
MIN_OPTIONS = 2
MAX_OPTIONS = 4
MAX_ROUNDS = 2
HEADER_MAX = 12
LABEL_MAX = 48
DESCRIPTION_MAX = 160
QUESTION_MAX = 300
CONTEXT_MAX = 500
ANSWER_TEXT_MAX = 500
ASSUMPTION_MAX = 300
MAX_ASSUMPTIONS = 12

PENDING_KEY = "pending_plan_clarification"
ROUNDS_KEY = "plan_clarification_rounds"
RESPONSE_MODES = frozenset({"answered", "recommended", "more_context"})

_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_RECOMMENDED_SUFFIX_RE = re.compile(
    r"\s*[\(（\[]\s*(recommended|推荐|建议)\s*[\)）\]]\s*$", re.IGNORECASE
)
_OTHER_LABELS = frozenset({"other", "others", "其他", "其它", "自定义"})

SessionUpdater = Callable[[dict[str, Any]], dict[str, Any]]


ASK_USER_GUIDANCE = (
    "Use ask_user only in explicit Plan mode, after read-only investigation, for 1-3 decisions that "
    "materially change the Plan and cannot be looked up; put the recommended option first, then stop. "
    "Never ask whether the Plan is ready or whether to proceed. "
    "On create, list unconfirmed defaults in assumptions."
)


def ask_user_questions_schema() -> dict[str, Any]:
    """JSON schema for plan_operation(operation='ask_user').questions."""
    return {
        "type": "array",
        "minItems": 1,
        "maxItems": MAX_QUESTIONS,
        "description": "ask_user only: 1-3 questions (prefer 1) that block the Plan.",
        "items": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Stable snake_case id."},
                "header": {"type": "string", "description": f"Short chip label (<= {HEADER_MAX} chars)."},
                "question": {"type": "string", "description": "One sentence in the user's language."},
                "multi_select": {"type": "boolean", "description": "Allow several options; default false."},
                "options": {
                    "type": "array",
                    "minItems": MIN_OPTIONS,
                    "maxItems": MAX_OPTIONS,
                    "description": "2-4 distinct choices, recommended first; the client adds 'Other'.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string", "description": "1-5 words."},
                            "description": {"type": "string", "description": "One sentence on the impact on the Plan."},
                        },
                        "required": ["label"],
                    },
                },
            },
            "required": ["header", "question", "options"],
        },
    }


def assumptions_schema() -> dict[str, Any]:
    return {
        "type": "array",
        "items": {"type": "string"},
        "maxItems": MAX_ASSUMPTIONS,
        "description": "create only: unconfirmed choices the Plan relies on (for example accepted recommended defaults).",
    }


class ClarificationError(ValueError):
    """Invalid model-supplied clarification request."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _clip(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _scope(bound_plan_id: int | None) -> str:
    return f"plan:{bound_plan_id}" if bound_plan_id is not None else "unbound"


def _normalize_options(raw: Any, index: int) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise ClarificationError(
            "options_required", f"Question {index} needs {MIN_OPTIONS}-{MAX_OPTIONS} options."
        )
    options: list[dict[str, Any]] = []
    seen: set[str] = set()
    recommended = 0
    for item in raw:
        if isinstance(item, str):
            item = {"label": item}
        if not isinstance(item, Mapping):
            continue
        raw_label = str(item.get("label") or "").strip()
        marked = bool(_RECOMMENDED_SUFFIX_RE.search(raw_label)) or item.get("recommended") is True
        label = _clip(_RECOMMENDED_SUFFIX_RE.sub("", raw_label), LABEL_MAX)
        key = label.casefold()
        # The client always adds a free-form "Other" choice.
        if not label or key in _OTHER_LABELS or key in seen:
            continue
        seen.add(key)
        if marked and not any(option.get("recommended") for option in options):
            recommended = len(options)
        options.append(
            {"label": label, "description": _clip(item.get("description"), DESCRIPTION_MAX), "recommended": marked}
        )
    if len(options) < MIN_OPTIONS:
        raise ClarificationError(
            "options_required", f"Question {index} needs {MIN_OPTIONS}-{MAX_OPTIONS} distinct options."
        )
    options = options[:MAX_OPTIONS]
    recommended = recommended if recommended < len(options) else 0
    # Exactly one recommendation, listed first as in Codex/Claude question tools.
    ordered = [options[recommended]] + [o for i, o in enumerate(options) if i != recommended]
    for position, option in enumerate(ordered):
        option["recommended"] = position == 0
    return ordered


_HEADER_BREAK_RE = re.compile(r"[？?：:，,。.（(；;]")


def _header(raw: Any, question: str, index: int) -> str:
    """Chip label: the model's header, else the question's leading phrase.

    An option label is never used: it reads as if that option were chosen.
    """
    text = " ".join(str(raw or "").split())
    if not text:
        text = _HEADER_BREAK_RE.split(question, maxsplit=1)[0].strip()
    return text[:HEADER_MAX].strip() or f"Q{index}"


def normalize_questions(raw: Any) -> list[dict[str, Any]]:
    """Validate model questions into the client contract or raise ClarificationError."""
    if not isinstance(raw, list) or not raw:
        raise ClarificationError(
            "questions_required", f"Provide 1-{MAX_QUESTIONS} questions with options."
        )
    if len(raw) > MAX_QUESTIONS:
        raise ClarificationError(
            "too_many_questions", f"Ask at most {MAX_QUESTIONS} questions; keep only decisions that block the Plan."
        )
    questions: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, Mapping):
            raise ClarificationError("invalid_question", f"Question {index} must be an object.")
        text = _clip(item.get("question"), QUESTION_MAX)
        if not text:
            raise ClarificationError("invalid_question", f"Question {index} needs question text.")
        options = _normalize_options(item.get("options"), index)
        qid = str(item.get("id") or "").strip().lower()
        if not _ID_RE.match(qid) or qid in used_ids:
            qid = f"q{index}"
            while qid in used_ids:
                qid += "_x"
        used_ids.add(qid)
        header = _header(item.get("header"), text, index)
        questions.append(
            {
                "id": qid,
                "header": header,
                "question": text,
                "multi_select": item.get("multi_select") is True,
                "options": options,
            }
        )
    return questions


def _default_read(session_id: str) -> dict[str, Any]:
    from app.database import get_db
    from app.routers.chat.session_helpers import _load_session_metadata_dict

    with get_db() as conn:
        return dict(_load_session_metadata_dict(conn, session_id) or {})


def _default_update(session_id: str, updater: SessionUpdater, owner_id: str | None) -> None:
    from app.routers.chat.session_helpers import _update_session_metadata

    _update_session_metadata(session_id, updater, owner_id=owner_id)


# Indirection keeps unit tests free of a real database.
read_session_metadata: Callable[[str], dict[str, Any]] = _default_read
update_session_metadata: Callable[[str, SessionUpdater, str | None], None] = _default_update


def rounds_used(metadata: Mapping[str, Any], bound_plan_id: int | None) -> int:
    record = metadata.get(ROUNDS_KEY)
    if not isinstance(record, Mapping) or record.get("scope") != _scope(bound_plan_id):
        return 0
    count = record.get("count")
    return count if isinstance(count, int) and count > 0 else 0


def unavailable_result() -> dict[str, Any]:
    return {
        "success": False,
        "executed": False,
        "operation": "ask_user",
        "error": "ask_user_unavailable",
        "summary": (
            "plan_operation ask_user is only available in explicit Plan mode in the main chat. "
            "Answer with your best judgment and state any assumptions."
        ),
    }


def issue_plan_clarification(
    *,
    session_id: str | None,
    owner_id: str | None,
    bound_plan_id: int | None,
    params: Mapping[str, Any],
    request_id: str | None = None,
) -> dict[str, Any]:
    """Validate, persist and return one pending clarification request."""
    if not session_id:
        return {**unavailable_result(), "error": "ask_user_session_required"}
    try:
        questions = normalize_questions(params.get("questions"))
    except ClarificationError as exc:
        return {
            "success": False,
            "executed": False,
            "operation": "ask_user",
            "error": exc.code,
            "summary": f"{exc} Fix the questions and call ask_user again, or continue without asking.",
        }
    scope = _scope(bound_plan_id)
    request = {
        "request_id": str(request_id or f"clarify_{uuid4().hex}"),
        "status": "pending",
        "context": _clip(params.get("context") or params.get("reason"), CONTEXT_MAX),
        "questions": questions,
        "max_rounds": MAX_ROUNDS,
    }
    outcome: dict[str, Any] = {}

    def updater(metadata: dict[str, Any]) -> dict[str, Any]:
        used = rounds_used(metadata, bound_plan_id)
        if used >= MAX_ROUNDS:
            outcome["limited"] = used
            return metadata
        request["round"] = used + 1
        metadata[ROUNDS_KEY] = {"scope": scope, "count": used + 1}
        metadata[PENDING_KEY] = {
            "request_id": request["request_id"],
            "round": used + 1,
            "scope": scope,
        }
        return metadata

    update_session_metadata(session_id, updater, owner_id)
    if "limited" in outcome:
        return {
            "success": False,
            "executed": False,
            "operation": "ask_user",
            "error": "clarification_limit_reached",
            "summary": (
                f"The user has already answered {outcome['limited']} rounds of questions for this Plan. "
                "Do not ask again. Continue with the recommended options and list them in the "
                "Plan's assumptions (plan_operation create assumptions=[...])."
            ),
        }
    return {
        "success": True,
        "executed": False,
        "operation": "ask_user",
        "clarification": request,
        "message": (
            "The questions are shown to the user. Stop now: do not create, edit or execute a Plan "
            "until the user's answer arrives in the next message."
        ),
    }


def consume_pending_clarification(
    session_id: str | None,
    response: Mapping[str, Any] | None,
    *,
    owner_id: str | None = None,
) -> None:
    """Clear the waiting state when any new user message arrives.

    Asking for more context does not consume a round: the agent may re-ask once
    it has explained the trade-offs.
    """
    if not session_id:
        return
    metadata = read_session_metadata(session_id)
    if not isinstance(metadata.get(PENDING_KEY), Mapping):
        return

    def updater(current: dict[str, Any]) -> dict[str, Any]:
        pending = current.pop(PENDING_KEY, None)
        if (
            isinstance(pending, Mapping)
            and isinstance(response, Mapping)
            and response.get("request_id") == pending.get("request_id")
            and response.get("mode") == "more_context"
        ):
            record = current.get(ROUNDS_KEY)
            if isinstance(record, Mapping) and isinstance(record.get("count"), int):
                current[ROUNDS_KEY] = {**record, "count": max(0, record["count"] - 1)}
        return current

    update_session_metadata(session_id, updater, owner_id)


def pending_settings(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Session settings exposed to the client for the card and sidebar."""
    pending = metadata.get(PENDING_KEY)
    if not isinstance(pending, Mapping):
        return {}
    request_id = str(pending.get("request_id") or "").strip()
    if not request_id:
        return {}
    return {"pending_plan_clarification_id": request_id, "waiting_user_input": True}


def normalize_clarification_response(raw: Any) -> dict[str, Any] | None:
    """Sanitize the client's structured answer; free text stays in the message itself."""
    if not isinstance(raw, Mapping):
        return None
    request_id = _clip(raw.get("request_id"), 80)
    mode = str(raw.get("mode") or "answered").strip()
    if not request_id or mode not in RESPONSE_MODES:
        return None
    answers: list[dict[str, Any]] = []
    raw_answers = raw.get("answers")
    for item in raw_answers if isinstance(raw_answers, list) else []:
        if not isinstance(item, Mapping):
            continue
        selected = item.get("selected")
        answers.append(
            {
                "id": _clip(item.get("id"), 40),
                "header": _clip(item.get("header"), HEADER_MAX * 2),
                "question": _clip(item.get("question"), QUESTION_MAX),
                "selected": [
                    _clip(value, LABEL_MAX)
                    for value in (selected if isinstance(selected, list) else [])[:MAX_OPTIONS]
                    if str(value or "").strip()
                ],
                "other": _clip(item.get("other"), ANSWER_TEXT_MAX) or None,
                "recommended_used": item.get("recommended_used") is True,
            }
        )
        if len(answers) >= MAX_QUESTIONS:
            break
    return {"request_id": request_id, "mode": mode, "answers": answers}


_TYPED_RECOMMENDED_RE = re.compile(
    r"^\s*(?:(?:就|请|那就)?(?:按|用|采用|接受)(?:你的|默认的?)?推荐(?:的?(?:选项|方案))?(?:继续|来|吧|执行)*"
    r"|(?:please\s+)?(?:continue|go|proceed)\s+with\s+(?:the|your)\s+recommend(?:ed|ations?)(?:\s+options?)?"
    r"|(?:use|accept)\s+(?:the|your)\s+recommend(?:ed|ations?)(?:\s+options?)?)\s*[。.!！]?\s*$",
    re.IGNORECASE,
)


def typed_recommendation_response(session_id: str | None, message: str | None) -> dict[str, Any] | None:
    """A typed "按推荐继续" while a card waits means the same as clicking that button."""
    if not session_id or not _TYPED_RECOMMENDED_RE.match(str(message or "")):
        return None
    pending = read_session_metadata(session_id).get(PENDING_KEY)
    request_id = str(pending.get("request_id") or "").strip() if isinstance(pending, Mapping) else ""
    return {"request_id": request_id, "mode": "recommended", "answers": []} if request_id else None


def clarification_prompt_lines(response: Mapping[str, Any] | None) -> list[str]:
    """Prompt guidance for the turn that carries the user's answer."""
    if not isinstance(response, Mapping):
        return []
    mode = response.get("mode")
    if mode == "more_context":
        return [
            "The user asked for more context before answering your clarification questions. Explain, "
            "briefly and concretely, what each option changes in the Plan and why it matters, then ask "
            "again with plan_operation ask_user using clearer options. Do not create the Plan yet."
        ]
    answers = [a for a in response.get("answers") or [] if isinstance(a, Mapping)]
    defaults = [str(a.get("header") or a.get("id")) for a in answers if a.get("recommended_used")]
    explicit = [str(a.get("header") or a.get("id")) for a in answers if not a.get("recommended_used")]
    lines = [
        "The user's message answers your clarification questions. Treat the answers as binding "
        "requirements and continue the Plan now; do not ask the same questions again."
    ]
    if explicit:
        lines.append(
            "The user chose these explicitly: "
            + ", ".join(explicit)
            + ". They are confirmed requirements: do not list them as assumptions or describe them as defaults."
        )
    if mode == "recommended" and not answers:
        lines.append(
            "The user accepted all of your recommended options without picking any explicitly. Record each "
            "recommended option in plan_operation create assumptions=[...], marked as recommended defaults."
        )
    if defaults:
        lines.append(
            "Only these were accepted as your recommended defaults rather than chosen explicitly: "
            + ", ".join(defaults)
            + ". Record exactly these in plan_operation create assumptions=[...], marked as recommended defaults; "
            "other assumptions are allowed only for decisions the user was never asked about."
        )
    return lines


def plan_mode_question_guidance(rounds_left: int) -> str:
    """Planning-time rule injected while explicit Plan mode is active."""
    if rounds_left <= 0:
        return (
            "You have already used the clarification rounds for this Plan. Do not call ask_user again; "
            "use the recommended options and record them as assumptions when you create the Plan."
        )
    return (
        "Before writing the Plan, investigate with read-only tools first. If a decision would materially "
        "change the Plan and cannot be discovered from tools, files or the conversation (for example target "
        "host range, data source, validation depth, deliverable form, or time/compute budget), call "
        "plan_operation with operation=ask_user and 1-3 questions, each with 2-4 options, your recommended "
        "option first, then stop. Never ask about facts you can look up, never ask whether the Plan is ready "
        f"or whether to proceed. Remaining clarification rounds: {rounds_left}. When creating the Plan, list "
        "unconfirmed choices in assumptions=[...]."
    )


def clarification_text(request: Mapping[str, Any], language: str) -> str:
    """Chat-history text for a clarification card; the card renders the options."""
    questions = [q for q in request.get("questions") or [] if isinstance(q, Mapping)]
    context = str(request.get("context") or "").strip()
    if language == "zh":
        lines = [context] if context else []
        lines.append(f"为了让计划更贴合你的需求，请先确认 {len(questions)} 个问题：")
        lines.extend(f"{i}. **{q.get('header')}**：{q.get('question')}" for i, q in enumerate(questions, 1))
        lines.append("\n请在下方卡片中选择；也可以直接回复，或选择“按推荐继续”。在你回答前，本轮不会创建或执行计划。")
    else:
        lines = [context] if context else []
        lines.append(f"Before drafting the Plan, please confirm {len(questions)} question(s):")
        lines.extend(f"{i}. **{q.get('header')}**: {q.get('question')}" for i, q in enumerate(questions, 1))
        lines.append(
            "\nChoose in the card below, reply directly, or continue with the recommendations. "
            "No Plan is created or executed until you answer."
        )
    return "\n".join(lines)


def plan_clarification_from_result(result: Any) -> dict[str, Any]:
    """Project a pending clarification carried by a completed DeepThinkResult."""
    request = getattr(result, "plan_clarification", None)
    if not isinstance(request, Mapping):
        return {}
    request_id = str(request.get("request_id") or "").strip()
    questions = request.get("questions")
    if not request_id or not isinstance(questions, list) or not questions:
        return {}
    return {
        "plan_clarification": {
            "request_id": request_id,
            "status": "pending",
            "round": request.get("round") or 1,
            "max_rounds": request.get("max_rounds") or MAX_ROUNDS,
            "context": str(request.get("context") or ""),
            "questions": questions,
        }
    }


def normalize_assumptions(raw: Any) -> list[str]:
    """Plan assumptions recorded at creation time (unconfirmed defaults)."""
    values = raw if isinstance(raw, list) else ([raw] if isinstance(raw, str) else [])
    result: list[str] = []
    for value in values:
        text = _clip(value, ASSUMPTION_MAX)
        if text and text not in result:
            result.append(text)
        if len(result) >= MAX_ASSUMPTIONS:
            break
    return result
