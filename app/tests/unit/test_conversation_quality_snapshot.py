from __future__ import annotations

from app.services.conversation_quality.snapshot import build_run_snapshot


def test_snapshot_uses_durable_facts_and_bounds_text(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.conversation_quality.snapshot.get_chat_run",
        lambda run_id: {
            "run_id": run_id,
            "status": "succeeded",
            "request_json": '{"message":"run a real analysis","context":{"request_tier":"execute"}}',
            "created_at": "2026-01-01",
            "started_at": "2026-01-01",
            "finished_at": "2026-01-01",
            "error": None,
        },
    )
    monkeypatch.setattr(
        "app.services.conversation_quality.snapshot.fetch_events_after",
        lambda run_id, _: [
            (1, {"type": "tool_output", "tool": "code_executor", "summary": "x" * 1000}),
            (2, {"type": "final", "payload": {
                "response": "y" * 10000,
                "metadata": {
                    "request_tier": "execute",
                    "intent_type": "execute_task",
                    "route_reason_codes": ["explicit_execute"],
                    "tools_used": ["code_executor"],
                    "tool_failures": ["timeout"],
                    "tool_results": [{"name": "code_executor", "result": {"success": False, "error": "timeout"}}],
                },
            }}),
        ],
    )

    snapshot = build_run_snapshot("run-1", max_chars=2000)
    assert snapshot is not None
    assert snapshot["routing"]["request_tier"] == "execute"
    assert snapshot["tools_used"] == ["code_executor"]
    assert snapshot["tool_failures"] == ["timeout"]
    assert len(snapshot["assistant_response"]) <= 2500
    assert snapshot["tool_results"][0]["success"] is False


def test_snapshot_has_a_strict_total_size_bound(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.conversation_quality.snapshot.get_chat_run",
        lambda run_id: {"status": "failed", "request_json": '{"message":"' + ('u' * 10000) + '"}'},
    )
    monkeypatch.setattr(
        "app.services.conversation_quality.snapshot.fetch_events_after",
        lambda run_id, _: [(1, {"type": "error", "message": "e" * 10000})],
    )

    snapshot = build_run_snapshot("run-2", max_chars=500)
    assert snapshot is not None

    import json
    assert len(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))) <= 500
