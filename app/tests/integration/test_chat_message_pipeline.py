"""Integration tests for the chat message pipeline.

Validates the full path: POST /chat/message → routing → agent → response → DB.
LLM calls are mocked; everything else (routing, middleware, DB, session mgmt) is real.
"""

from __future__ import annotations

import pytest

from app.database_pool import get_db


@pytest.mark.integration
def test_chat_message_returns_structured_response(
    app_client_factory,
    mock_llm_chat,
) -> None:
    mock_llm_chat("你好，有什么可以帮你的？")

    with app_client_factory() as client:
        client.patch(
            "/chat/sessions/pipeline-001",
            json={"name": "Pipeline Test"},
        )

        resp = client.post(
            "/chat/message",
            json={
                "message": "你好",
                "session_id": "pipeline-001",
            },
        )

        assert resp.status_code == 200
        payload = resp.json()
        assert "response" in payload
        assert payload["response"] == "你好，有什么可以帮你的？"


@pytest.mark.integration
def test_chat_message_persists_user_message_to_db(
    app_client_factory,
    mock_llm_chat,
) -> None:
    mock_llm_chat("Noted.")

    with app_client_factory() as client:
        client.patch(
            "/chat/sessions/persist-001",
            json={"name": "Persist Test"},
        )

        client.post(
            "/chat/message",
            json={
                "message": "请记住这个信息",
                "session_id": "persist-001",
            },
        )

        with get_db() as conn:
            rows = conn.execute(
                "SELECT role, content FROM chat_messages WHERE session_id = ? ORDER BY created_at",
                ("persist-001",),
            ).fetchall()

        roles = [row["role"] for row in rows]
        assert "user" in roles
        user_msgs = [row["content"] for row in rows if row["role"] == "user"]
        assert any("请记住这个信息" in msg for msg in user_msgs)


@pytest.mark.integration
def test_chat_message_persists_history_across_turns(
    app_client_factory,
    mock_llm_chat,
) -> None:
    messages = ["第一条消息", "第二条消息", "第三条消息"]

    with app_client_factory() as client:
        client.patch(
            "/chat/sessions/history-001",
            json={"name": "History Test"},
        )

        for i, msg in enumerate(messages):
            mock_llm_chat(f"回复{i + 1}")
            client.post(
                "/chat/message",
                json={"message": msg, "session_id": "history-001"},
            )

        history_resp = client.get("/chat/history/history-001")
        assert history_resp.status_code == 200
        history = history_resp.json()
        assert history["total"] >= 3  # At least 3 user messages


@pytest.mark.integration
def test_chat_message_session_auto_created_on_first_message(
    app_client_factory,
    mock_llm_chat,
) -> None:
    mock_llm_chat("Welcome!")

    with app_client_factory() as client:
        resp = client.post(
            "/chat/message",
            json={
                "message": "hello",
                "session_id": "auto-create-001",
            },
        )
        assert resp.status_code == 200

        head_resp = client.head("/chat/sessions/auto-create-001")
        assert head_resp.status_code == 200


@pytest.mark.integration
def test_chat_message_response_includes_metadata(
    app_client_factory,
    mock_llm_chat,
) -> None:
    mock_llm_chat("Analysis complete.")

    with app_client_factory() as client:
        client.patch(
            "/chat/sessions/meta-001",
            json={"name": "Metadata Test"},
        )

        resp = client.post(
            "/chat/message",
            json={"message": "帮我分析数据", "session_id": "meta-001"},
        )

        assert resp.status_code == 200
        payload = resp.json()
        assert "metadata" in payload
        meta = payload["metadata"]
        assert "status" in meta
        assert meta["status"] == "completed"
