from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.repository.llm_usage import init_llm_usage_table, log_llm_usage, get_usage_summary


@pytest.fixture
def mock_db():
    """Mock database connection for testing."""
    with patch("app.repository.llm_usage.get_db") as mock_get_db:
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.execute.return_value = mock_cursor
        mock_get_db.return_value = mock_conn
        yield mock_conn, mock_cursor


def test_init_llm_usage_table_creates_table_and_index(mock_db):
    """init_llm_usage_table should create table and index."""
    mock_conn, mock_cursor = mock_db
    init_llm_usage_table()
    assert mock_conn.execute.call_count == 2
    calls = [call.args[0] for call in mock_conn.execute.call_args_list]
    assert any("CREATE TABLE IF NOT EXISTS llm_usage_log" in sql for sql in calls)
    assert any("CREATE INDEX IF NOT EXISTS idx_llm_usage_created_at" in sql for sql in calls)
    mock_conn.commit.assert_called_once()


def test_log_llm_usage_inserts_record(mock_db):
    """log_llm_usage should insert a record with correct fields."""
    mock_conn, mock_cursor = mock_db
    log_llm_usage(
        provider="qwen",
        model="qwen-test",
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
    )
    mock_conn.execute.assert_called_once()
    sql = mock_conn.execute.call_args.args[0]
    params = mock_conn.execute.call_args.args[1]
    assert "INSERT INTO llm_usage_log" in sql
    assert params[0] == "qwen"
    assert params[1] == "qwen-test"
    assert params[2] == 100
    assert params[3] == 50
    assert params[4] == 150
    assert isinstance(params[5], str)
    mock_conn.commit.assert_called_once()


def test_get_usage_summary_returns_aggregated_data(mock_db):
    """get_usage_summary should return aggregated usage data."""
    mock_conn, mock_cursor = mock_db
    mock_cursor.fetchone.return_value = {
        "call_count": 10,
        "total_prompt_tokens": 1000,
        "total_completion_tokens": 500,
        "total_tokens": 1500,
    }
    mock_cursor.fetchall.return_value = [
        {
            "model": "qwen-test",
            "call_count": 5,
            "prompt_tokens": 500,
            "completion_tokens": 250,
            "total_tokens": 750,
        },
        {
            "model": "glm-test",
            "call_count": 5,
            "prompt_tokens": 500,
            "completion_tokens": 250,
            "total_tokens": 750,
        },
    ]
    result = get_usage_summary(hours=24)
    assert result["period_hours"] == 24
    assert result["call_count"] == 10
    assert result["total_prompt_tokens"] == 1000
    assert result["total_completion_tokens"] == 500
    assert result["total_tokens"] == 1500
    assert len(result["by_model"]) == 2
    assert result["by_model"][0]["model"] == "qwen-test"
    assert result["by_model"][1]["model"] == "glm-test"


def test_get_usage_summary_handles_empty_data(mock_db):
    """get_usage_summary should handle empty database gracefully."""
    mock_conn, mock_cursor = mock_db
    mock_cursor.fetchone.return_value = None
    mock_cursor.fetchall.return_value = []
    result = get_usage_summary(hours=24)
    assert result["period_hours"] == 24
    assert result["call_count"] == 0
    assert result["total_prompt_tokens"] == 0
    assert result["total_completion_tokens"] == 0
    assert result["total_tokens"] == 0
    assert result["by_model"] == []


def test_log_usage_function_calls_repository():
    """_log_usage in llm.py should call repository function."""
    with patch("app.repository.llm_usage.log_llm_usage") as mock_log:
        from app.llm import _log_usage
        _log_usage(
            provider="qwen",
            model="qwen-test",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
        )
        mock_log.assert_called_once_with(
            provider="qwen",
            model="qwen-test",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
        )


def test_log_usage_handles_repository_error():
    """_log_usage should not raise exceptions if repository fails."""
    with patch("app.repository.llm_usage.log_llm_usage", side_effect=Exception("DB error")):
        from app.llm import _log_usage
        _log_usage(
            provider="qwen",
            model="qwen-test",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
        )


def test_chat_extracts_and_logs_usage(monkeypatch):
    """chat() should extract usage from response and log it."""
    import httpx
    from app.llm import LLMClient

    fake_response = httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": "ok"}}],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
            },
        },
        request=httpx.Request("POST", "https://example.com/v1/chat/completions"),
    )

    mock_client = MagicMock(spec=httpx.Client)
    mock_client.post.return_value = fake_response
    monkeypatch.setattr("app.llm._get_shared_sync_client", lambda: mock_client)

    with patch("app.llm._log_usage") as mock_log:
        client = LLMClient(
            provider="qwen",
            api_key="test-key",
            url="https://example.com/v1/chat/completions",
            model="qwen-test",
            timeout=0,
            retries=0,
        )
        result = client.chat("hello")
        assert result == "ok"
        mock_log.assert_called_once_with(
            provider="qwen",
            model="qwen-test",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
        )


def test_chat_async_extracts_and_logs_usage(monkeypatch):
    """chat_async() should extract usage from response and log it."""
    import httpx
    from app.llm import LLMClient

    fake_response = httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": "ok"}}],
            "usage": {
                "prompt_tokens": 200,
                "completion_tokens": 100,
                "total_tokens": 300,
            },
        },
        request=httpx.Request("POST", "https://example.com/v1/chat/completions"),
    )

    class _FakeAsyncClient:
        async def post(self, *args, **kwargs):
            return fake_response

        async def aclose(self):
            pass

    monkeypatch.setattr("app.llm._get_shared_async_client", lambda: _FakeAsyncClient())

    with patch("app.llm._log_usage") as mock_log:
        client = LLMClient(
            provider="qwen",
            api_key="test-key",
            url="https://example.com/v1/chat/completions",
            model="qwen-test",
            timeout=0,
            retries=0,
        )

        async def _call():
            return await client.chat_async("hello")

        result = asyncio.run(_call())
        assert result == "ok"
        mock_log.assert_called_once_with(
            provider="qwen",
            model="qwen-test",
            prompt_tokens=200,
            completion_tokens=100,
            total_tokens=300,
        )


def test_native_stream_result_includes_usage_field():
    """NativeStreamResult should have usage field."""
    from app.llm import NativeStreamResult
    result = NativeStreamResult()
    assert hasattr(result, "usage")
    assert result.usage is None
    result.usage = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
    assert result.usage["prompt_tokens"] == 100
