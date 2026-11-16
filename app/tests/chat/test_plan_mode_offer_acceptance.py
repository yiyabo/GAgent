"""Explicit Plan requests and typed offer acceptance outrank a dismissal."""

from __future__ import annotations

import json

from app.services.plan_mode_offer_acceptance import (
    plan_acceptance_metadata,
    resolve_plan_mode_acceptance,
)
from app.tests.chat.test_unified_stream_event_golden import (
    _build_agent,
    _patch_runtime,
    _run,
)

ORIGINAL_QUERY = "调研噬菌体裂解酶最新进展，并制定完整研究计划，之后执行小规模验证。"


def _pending_history() -> list[dict]:
    return [
        {"role": "user", "content": ORIGINAL_QUERY},
        {
            "role": "assistant",
            "content": "建议先进入 Plan 模式。",
            "metadata": json.dumps(
                {
                    "plan_mode_offer": {
                        "offer_id": "offer_abc123",
                        "status": "pending",
                        "query": ORIGINAL_QUERY,
                    }
                }
            ),
        },
    ]


def test_explicit_plan_request_clears_an_earlier_dismissal() -> None:
    decision = resolve_plan_mode_acceptance(
        message="你可以根据这一步的产出来计划下一步, 创建一个plan吧",
        plan_bound=False,
        settings={"plan_offer_dismissed": True},
        messages=[],
    )
    assert decision.plan_mode is True
    assert decision.clear_dismissed is True
    assert decision.source == "explicit_plan_request"
    assert decision.query is None


def test_typed_confirmation_reuses_the_original_offer_query() -> None:
    decision = resolve_plan_mode_acceptance(
        message="进入 Plan 模式并创建计划",
        plan_bound=False,
        settings={},
        messages=_pending_history(),
    )
    assert decision.plan_mode is True
    assert decision.source == "typed_offer_accept"
    assert decision.query == ORIGINAL_QUERY
    assert decision.offer_id == "offer_abc123"


def test_unrelated_message_does_not_resurrect_an_old_offer() -> None:
    history = _pending_history() + [{"role": "user", "content": "直接继续"}]
    decision = resolve_plan_mode_acceptance(
        message="这个任务的进展如何？",
        plan_bound=False,
        settings={"plan_offer_dismissed": True},
        messages=history,
    )
    assert decision.plan_mode is False
    assert decision.clear_dismissed is False


def test_completed_offer_is_not_accepted_again() -> None:
    history = _pending_history()
    history[1]["metadata"] = json.dumps(
        {"plan_mode_offer": {"offer_id": "offer_abc123", "status": "accepted", "query": ORIGINAL_QUERY}}
    )
    decision = resolve_plan_mode_acceptance(
        message="进入 Plan 模式并创建计划",
        plan_bound=False,
        settings={},
        messages=history,
    )
    # No pending offer: the explicit Plan request still applies, but the stale
    # offer query/id must not be replayed.
    assert decision.source == "explicit_plan_request"
    assert decision.offer_id is None


def test_bound_plan_and_existing_plan_mode_are_left_alone() -> None:
    for kwargs in (
        {"plan_bound": True, "settings": {}},
        {"plan_bound": False, "settings": {"plan_mode": True}},
    ):
        decision = resolve_plan_mode_acceptance(message="创建一个plan吧", messages=_pending_history(), **kwargs)
        assert decision.plan_mode is False


def test_acceptance_metadata_is_only_emitted_for_server_switches() -> None:
    assert plan_acceptance_metadata({}) == {}
    assert plan_acceptance_metadata({"plan_acceptance_source": "explicit_plan_request"}) == {
        "plan_mode": True,
        "plan_acceptance_source": "explicit_plan_request",
    }


def _patch_acceptance_runtime(monkeypatch, settings: dict) -> list[bool]:
    _patch_runtime(monkeypatch)
    monkeypatch.setattr("app.services.plan_mode_offers.session_offer_settings", lambda _session_id: settings)
    persisted: list[bool] = []

    def _persist(session_id, *, enabled):
        persisted.append(enabled)

    monkeypatch.setattr("app.services.plan_mode_offer_acceptance.persist_plan_mode_enabled", _persist)
    return persisted


def test_dismissed_session_enters_plan_mode_on_explicit_request(monkeypatch) -> None:
    persisted = _patch_acceptance_runtime(monkeypatch, {"plan_offer_dismissed": True})
    agent = _build_agent(extra_context={"owner_id": "alice"})
    agent.plan_session.plan_id = None
    agent.plan_tree = None

    events = _run(agent, "你可以根据这一步的产出来计划下一步, 创建一个plan吧")

    assert persisted == [True]
    assert agent.extra_context["plan_mode"] is True
    assert agent.extra_context["plan_offer_dismissed"] is False
    metadata = events[-1]["payload"]["metadata"]
    assert "plan_mode_offer" not in metadata
    assert metadata["plan_contract_enforced"] is True
    assert metadata["plan_mode"] is True
    assert metadata["plan_acceptance_source"] == "explicit_plan_request"


def test_typed_acceptance_uses_the_offer_query_without_a_second_offer(monkeypatch) -> None:
    persisted = _patch_acceptance_runtime(monkeypatch, {})
    agent = _build_agent(extra_context={"owner_id": "alice"})
    agent.plan_session.plan_id = None
    agent.plan_tree = None
    agent.history = _pending_history()
    seen: list[str] = []
    original = agent._resolve_request_routing

    def _capture(message):
        seen.append(message)
        return original(message)

    monkeypatch.setattr(agent, "_resolve_request_routing", _capture)

    events = _run(agent, "进入 Plan 模式并创建计划")

    assert seen == [ORIGINAL_QUERY]
    assert persisted == [True]
    assert agent.extra_context["plan_offer_accepted"] is True
    assert agent.extra_context["plan_offer_id"] == "offer_abc123"
    metadata = events[-1]["payload"]["metadata"]
    assert "plan_mode_offer" not in metadata
    assert metadata["plan_acceptance_source"] == "typed_offer_accept"


def _live_history() -> list[dict]:
    """The shape the web client actually posts: current turn + empty reply slot."""
    return _pending_history() + [
        {"role": "user", "content": "进入 Plan 模式并创建计划"},
        {"role": "assistant", "content": ""},
    ]


def test_typed_confirmation_ignores_the_current_turn_in_history() -> None:
    decision = resolve_plan_mode_acceptance(
        message="进入 Plan 模式并创建计划",
        plan_bound=False,
        settings={"plan_offer_dismissed": True},
        messages=_live_history(),
    )
    assert decision.plan_mode is True
    assert decision.source == "typed_offer_accept"
    assert decision.query == ORIGINAL_QUERY
    assert decision.offer_id == "offer_abc123"
    assert decision.clear_dismissed is True


def test_typed_confirmation_tolerates_an_empty_assistant_placeholder_only() -> None:
    history = _pending_history() + [{"role": "assistant", "content": ""}]
    decision = resolve_plan_mode_acceptance(
        message="进入 Plan 模式并创建计划",
        plan_bound=False,
        settings={},
        messages=history,
    )
    assert decision.source == "typed_offer_accept"
    assert decision.query == ORIGINAL_QUERY


def test_offer_message_without_body_is_still_found() -> None:
    """The offer card may live on a near-empty assistant message."""
    history = [
        {"role": "user", "content": ORIGINAL_QUERY},
        {
            "role": "assistant",
            "content": "",
            "metadata": json.dumps(
                {"plan_mode_offer": {"offer_id": "offer_x", "status": "pending", "query": ORIGINAL_QUERY}}
            ),
        },
        {"role": "user", "content": "按推荐继续"},
        {"role": "assistant", "content": ""},
    ]
    decision = resolve_plan_mode_acceptance(
        message="按推荐继续",
        plan_bound=False,
        settings={},
        messages=history,
    )
    assert decision.source == "typed_offer_accept"
    assert decision.offer_id == "offer_x"


def test_current_turn_trim_does_not_skip_an_older_user_turn() -> None:
    history = _pending_history() + [
        {"role": "user", "content": "帮我看看这个"},
        {"role": "assistant", "content": "好的，这是结果。"},
        {"role": "user", "content": "进入 Plan 模式并创建计划"},
        {"role": "assistant", "content": ""},
    ]
    decision = resolve_plan_mode_acceptance(
        message="进入 Plan 模式并创建计划",
        plan_bound=False,
        settings={},
        messages=history,
    )
    # A newer exchange sits between the offer and this message: not a card accept.
    assert decision.source == "explicit_plan_request"
    assert decision.query is None
# --- server-side fallback -------------------------------------------------
# Real clients upload history with ``metadata: null`` on every message, so the
# offer card is only visible where it is stored: the session's last assistant
# reply.

import pytest  # noqa: E402

from app.database import get_db, init_db  # noqa: E402


@pytest.fixture
def offer_session(isolated_app_env):
    init_db()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO chat_sessions(id,owner_id,project_id,name) VALUES(?,?,?,?)",
            ("s_offer", "u", None, "Offer"),
        )
        conn.commit()
    return isolated_app_env


def _store_message(role: str, content: str, metadata: dict | None = None) -> None:
    with get_db() as conn:
        conn.execute(
            "INSERT INTO chat_messages(session_id,role,content,metadata) VALUES(?,?,?,?)",
            ("s_offer", role, content, json.dumps(metadata) if metadata else None),
        )
        conn.commit()


def _offer_metadata(status: str = "pending") -> dict:
    return {
        "plan_mode_offer": {
            "offer_id": "offer_stored",
            "status": status,
            "query": ORIGINAL_QUERY,
        }
    }


def _metadata_free_history() -> list[dict]:
    """Exactly what the browser sends: no per-message metadata."""
    return [
        {"role": "user", "content": ORIGINAL_QUERY, "metadata": None},
        {"role": "assistant", "content": "建议先进入 Plan 模式。", "metadata": None},
        {"role": "user", "content": "进入 Plan 模式并创建计划", "metadata": None},
        {"role": "assistant", "content": "", "metadata": None},
    ]


def test_typed_confirmation_accepts_the_stored_offer(offer_session) -> None:
    _store_message("user", ORIGINAL_QUERY)
    _store_message("assistant", "建议先进入 Plan 模式。", _offer_metadata())
    decision = resolve_plan_mode_acceptance(
        message="进入 Plan 模式并创建计划",
        plan_bound=False,
        settings={},
        messages=_metadata_free_history(),
        session_id="s_offer",
    )
    assert decision.source == "typed_offer_accept"
    assert decision.query == ORIGINAL_QUERY
    assert decision.offer_id == "offer_stored"


def test_stored_offer_survives_an_empty_placeholder(offer_session) -> None:
    _store_message("user", ORIGINAL_QUERY)
    _store_message("assistant", "建议先进入 Plan 模式。", _offer_metadata())
    _store_message("assistant", "")
    decision = resolve_plan_mode_acceptance(
        message="进入 Plan 模式并创建计划",
        plan_bound=False,
        settings={},
        session_id="s_offer",
    )
    assert decision.source == "typed_offer_accept"


def test_stored_offer_is_superseded_by_a_newer_reply(offer_session) -> None:
    _store_message("user", ORIGINAL_QUERY)
    _store_message("assistant", "建议先进入 Plan 模式。", _offer_metadata())
    _store_message("user", "先不管计划，查一下这个基因")
    _store_message("assistant", "这是检索结果。")
    decision = resolve_plan_mode_acceptance(
        message="进入 Plan 模式并创建计划",
        plan_bound=False,
        settings={},
        session_id="s_offer",
    )
    assert decision.source == "explicit_plan_request"
    assert decision.query is None


def test_stored_offer_must_still_be_pending(offer_session) -> None:
    _store_message("user", ORIGINAL_QUERY)
    _store_message("assistant", "建议先进入 Plan 模式。", _offer_metadata("declined"))
    decision = resolve_plan_mode_acceptance(
        message="进入 Plan 模式并创建计划",
        plan_bound=False,
        settings={},
        session_id="s_offer",
    )
    assert decision.source == "explicit_plan_request"


def test_agent_level_typed_acceptance_reads_the_stored_offer(monkeypatch, offer_session) -> None:
    """The wiring passes the session so a metadata-free client still works."""
    persisted = _patch_acceptance_runtime(monkeypatch, {})
    _store_message("user", ORIGINAL_QUERY)
    _store_message("assistant", "建议先进入 Plan 模式。", _offer_metadata())
    agent = _build_agent(extra_context={"owner_id": "alice"})
    agent.plan_session.plan_id = None
    agent.plan_tree = None
    agent.session_id = "s_offer"
    agent.history = _metadata_free_history()
    seen: list[str] = []
    original = agent._resolve_request_routing

    def _capture(message):
        seen.append(message)
        return original(message)

    monkeypatch.setattr(agent, "_resolve_request_routing", _capture)

    events = _run(agent, "进入 Plan 模式并创建计划")

    assert seen == [ORIGINAL_QUERY]
    assert persisted == [True]
    assert agent.extra_context["plan_offer_accepted"] is True
    metadata = events[-1]["payload"]["metadata"]
    assert metadata["plan_acceptance_source"] == "typed_offer_accept"
