from __future__ import annotations

from app.services.session_title_service import SessionTitleService


def test_bulk_generate_empty_session_ids_does_not_fallback(monkeypatch) -> None:
    service = SessionTitleService()
    picked_limits: list[int | None] = []

    def _pick_candidate_sessions(*, limit=None):
        picked_limits.append(limit)
        return ["unexpected-session"]

    monkeypatch.setattr(service, "_pick_candidate_sessions", _pick_candidate_sessions)

    assert service.bulk_generate(session_ids=[], limit=5) == []
    assert picked_limits == []
