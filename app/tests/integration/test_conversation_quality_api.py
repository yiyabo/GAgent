from __future__ import annotations

from app.repository import conversation_quality as repository


def test_quality_api_restricts_access_to_internal_operators(
    app_client_factory,
) -> None:
    with app_client_factory() as client:
        unauthenticated = client.get("/quality/summary")
        regular_user = client.get(
            "/quality/summary",
            headers={"X-Forwarded-User": "regular-user"},
        )

    assert unauthenticated.status_code == 401
    assert regular_user.status_code == 403


def test_quality_api_returns_global_analytics_to_configured_operator(
    app_client_factory,
    monkeypatch,
) -> None:
    monkeypatch.setenv("QUALITY_ANALYTICS_ADMIN_IDS", "quality-admin")
    observed: dict[str, object] = {}

    def _summary(**kwargs):
        observed["summary"] = kwargs
        return {
            "total": 2,
            "pending": 0,
            "evaluated": 2,
            "average_confidence": 0.8,
            "by_satisfaction_level": [{"name": "negative", "count": 1}],
            "failure_modes": [],
            "responsible_stages": [],
            "request_tiers": [],
            "tools": [],
        }

    def _cases(**kwargs):
        observed["cases"] = kwargs
        return [{
            "id": 1,
            "target_run_id": "run-1",
            "session_id": "session-1",
            "status": "final",
            "evaluation_basis": "follow_up_message",
            "satisfaction_level": "negative",
            "confidence": 0.8,
            "evaluation": {
                "evidence": [{
                    "source": "user_follow_up",
                    "quote": "not correct",
                    "explanation": "explicit correction",
                }],
                "failure_modes": ["tool_not_invoked"],
                "responsible_stages": ["tool_selection"],
            },
            "snapshot": {"user_goal": "analyze data"},
        }]

    monkeypatch.setattr(repository, "get_quality_summary", _summary)
    monkeypatch.setattr(repository, "list_evaluations", _cases)
    monkeypatch.setattr(repository, "get_evaluation", lambda evaluation_id: _cases()[0])

    headers = {"X-Forwarded-User": "quality-admin"}
    with app_client_factory() as client:
        summary = client.get("/quality/summary", headers=headers)
        cases = client.get("/quality/cases", headers=headers)
        detail = client.get("/quality/cases/1", headers=headers)

    assert summary.status_code == 200
    assert summary.json()["total"] == 2
    assert cases.status_code == 200
    assert cases.json()[0]["failure_modes"] == ["tool_not_invoked"]
    assert detail.status_code == 200
    assert detail.json()["target_run_id"] == "run-1"
    assert observed["summary"] == {"since": observed["summary"]["since"]}
    assert "owner_id" not in observed["cases"]
