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
    mock_conn, mock_cursor = mock_db
    mock_cursor.fetchall.return_value = [
        {"name": "id"}, {"name": "provider"}, {"name": "model"},
        {"name": "prompt_tokens"}, {"name": "completion_tokens"},
        {"name": "total_tokens"}, {"name": "created_at"},
        {"name": "session_id"}, {"name": "plan_id"},
        {"name": "task_id"}, {"name": "call_purpose"},
        {"name": "input_cost"}, {"name": "output_cost"},
        {"name": "estimated_cost"}, {"name": "cost_currency"},
    ]
    init_llm_usage_table()
    calls = [call.args[0] for call in mock_conn.execute.call_args_list]
    assert any("CREATE TABLE IF NOT EXISTS llm_usage_log" in sql for sql in calls)
    assert any("CREATE INDEX IF NOT EXISTS idx_llm_usage_created_at" in sql for sql in calls)
    mock_conn.commit.assert_called_once()


def test_log_llm_usage_inserts_record(mock_db):
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
    assert params[6] is None  # session_id
    assert params[7] is None  # plan_id
    assert params[8] is None  # task_id
    assert params[9] is None  # call_purpose
    assert params[12] is not None  # estimated_cost
    assert params[13] == "CNY"
    mock_conn.commit.assert_called_once()


def test_log_llm_usage_inserts_record_with_session_context(mock_db):
    mock_conn, mock_cursor = mock_db
    log_llm_usage(
        provider="qwen",
        model="qwen-test",
        prompt_tokens=200,
        completion_tokens=100,
        total_tokens=300,
        session_id="session_abc123",
        plan_id=42,
        task_id=7,
        call_purpose="deep_think",
    )
    params = mock_conn.execute.call_args.args[1]
    assert params[6] == "session_abc123"
    assert params[7] == 42
    assert params[8] == 7
    assert params[9] == "deep_think"


def test_get_usage_summary_returns_aggregated_data(mock_db):
    """get_usage_summary should return aggregated usage data."""
    mock_conn, mock_cursor = mock_db
    mock_cursor.fetchone.return_value = {
        "call_count": 10,
        "total_prompt_tokens": 1000,
        "total_completion_tokens": 500,
        "total_tokens": 1500,
        "estimated_cost": 1.23,
    }
    mock_cursor.fetchall.return_value = [
        {
            "model": "qwen-test",
            "call_count": 5,
            "prompt_tokens": 500,
            "completion_tokens": 250,
            "total_tokens": 750,
            "estimated_cost": 0.5,
        },
        {
            "model": "glm-test",
            "call_count": 5,
            "prompt_tokens": 500,
            "completion_tokens": 250,
            "total_tokens": 750,
            "estimated_cost": 0.73,
        },
    ]
    result = get_usage_summary(hours=24)
    assert result["period_hours"] == 24
    assert result["call_count"] == 10
    assert result["total_prompt_tokens"] == 1000
    assert result["total_completion_tokens"] == 500
    assert result["total_tokens"] == 1500
    assert result["estimated_cost"] == 1.23
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
            session_id=None,
            plan_id=None,
            task_id=None,
            call_purpose=None,
        )


def test_log_usage_propagates_context_from_contextvar():
    from app.llm import _log_usage, set_usage_context, clear_usage_context
    token = set_usage_context(
        session_id="sess_xyz",
        plan_id=99,
        task_id=5,
        call_purpose="deep_think",
    )
    try:
        with patch("app.repository.llm_usage.log_llm_usage") as mock_log:
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
                session_id="sess_xyz",
                plan_id=99,
                task_id=5,
                call_purpose="deep_think",
            )
    finally:
        clear_usage_context(token)


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
    from app.llm import NativeStreamResult
    result = NativeStreamResult()
    assert hasattr(result, "usage")
    assert result.usage is None
    result.usage = {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
    assert result.usage["prompt_tokens"] == 100


def test_get_session_usage_summary_returns_aggregated_data(mock_db):
    from app.repository.llm_usage import get_session_usage_summary
    mock_conn, mock_cursor = mock_db
    mock_cursor.fetchone.return_value = {
        "call_count": 5,
        "total_prompt_tokens": 500,
        "total_completion_tokens": 250,
        "total_tokens": 750,
        "estimated_cost": 0.9,
    }
    mock_cursor.fetchall.side_effect = [
        [
            {
                "model": "qwen-test",
                "call_count": 5,
                "prompt_tokens": 500,
                "completion_tokens": 250,
                "total_tokens": 750,
                "estimated_cost": 0.9,
            },
        ],
        [
            {"purpose": "deep_think", "call_count": 3, "total_tokens": 450, "estimated_cost": 0.6},
            {"purpose": "routing", "call_count": 2, "total_tokens": 300, "estimated_cost": 0.3},
        ],
    ]
    result = get_session_usage_summary("session_abc")
    assert result["session_id"] == "session_abc"
    assert result["call_count"] == 5
    assert result["total_tokens"] == 750
    assert result["estimated_cost"] == 0.9
    assert len(result["by_model"]) == 1
    assert len(result["by_purpose"]) == 2
    assert result["by_purpose"][0]["purpose"] == "deep_think"


def test_log_llm_usage_accepts_explicit_cost(mock_db):
    mock_conn, _mock_cursor = mock_db
    log_llm_usage(
        provider="qwen_code_cli",
        model="qwen3.7-max",
        prompt_tokens=1000,
        completion_tokens=500,
        total_tokens=1500,
        session_id="session_1",
        plan_id=122,
        task_id=14,
        call_purpose="qwen_code_cli_execution",
        input_cost=0.02,
        output_cost=0.04,
        estimated_cost=0.06,
        cost_currency="CNY",
    )
    params = mock_conn.execute.call_args.args[1]
    assert params[6] == "session_1"
    assert params[7] == 122
    assert params[8] == 14
    assert params[9] == "qwen_code_cli_execution"
    assert params[10] == 0.02
    assert params[11] == 0.04
    assert params[12] == 0.06
    assert params[13] == "CNY"


def test_estimate_llm_cost_uses_default_qwen_code_rates():
    from app.repository.llm_usage import estimate_llm_cost
    cost = estimate_llm_cost(
        provider="qwen_code_cli",
        model="qwen3.7-max",
        prompt_tokens=1000,
        completion_tokens=1000,
    )
    assert cost["input_cost"] > 0
    assert cost["output_cost"] > 0
    assert cost["estimated_cost"] == cost["input_cost"] + cost["output_cost"]
    assert cost["cost_currency"] == "CNY"
