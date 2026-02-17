from __future__ import annotations

import asyncio

from app.routers import chat_routes


def test_analysis_only_response_uses_source_job_results(
    monkeypatch,
) -> None:
    source_job_id = "act_1234abcd5678ef90"
    fake_record = {
        "id": source_job_id,
        "status": "completed",
        "plan_id": 49,
        "created_at": "2026-02-17T17:23:59Z",
        "started_at": "2026-02-17T17:24:00Z",
        "finished_at": "2026-02-17T17:25:00Z",
        "errors": [],
        "result": {
            "tool_results": [
                {
                    "name": "claude_code",
                    "summary": "done",
                    "parameters": {"task": "fibonacci"},
                    "result": {"success": True, "output": "ok"},
                }
            ]
        },
    }

    async def _fake_generate_tool_analysis(**kwargs):
        assert kwargs["tool_results"][0]["name"] == "claude_code"
        return "job-scoped analysis"

    monkeypatch.setattr(chat_routes, "fetch_action_run", lambda _run_id: fake_record)
    monkeypatch.setattr(
        chat_routes,
        "_generate_tool_analysis",
        _fake_generate_tool_analysis,
    )

    response = asyncio.run(
        chat_routes._build_analysis_only_chat_response(
            user_message="Analyze this completed job.",
            context={"analysis_only": True, "source_job_id": source_job_id},
            session_id="session_test",
            llm_provider="qwen",
        )
    )

    assert response is not None
    assert response.response == "job-scoped analysis"
    assert response.actions == []
    assert response.metadata["analysis_only"] is True
    assert response.metadata["source_job_id"] == source_job_id
    assert "plan_outline" not in response.metadata


def test_analysis_only_response_requires_source_job_id() -> None:
    response = asyncio.run(
        chat_routes._build_analysis_only_chat_response(
            user_message="Please analyze the finished background task.",
            context={"analysis_only": True},
            session_id="session_test",
            llm_provider="qwen",
        )
    )

    assert response is not None
    assert "source_job_id" in response.response
    assert response.actions == []
    assert response.metadata["analysis_only"] is True
