"""Regression tests for the `_persist_runtime_context` dedup (refactor registry D1).

`app.routers.chat.agent` historically carried a byte-identical copy of
`_persist_runtime_context` (and `_RUNTIME_CONTEXT_KEYS`) that lives in
`app.routers.chat.action_handlers`. The agent module now re-exports the single
source of truth. These tests pin both the shared identity and the identical
persistence effect of the two entry points.
"""

from types import SimpleNamespace
from typing import Any, Dict, List, Tuple

import app.routers.chat.action_handlers as action_handlers
import app.routers.chat.agent as agent_module


def _fake_agent() -> SimpleNamespace:
    return SimpleNamespace(
        session_id="sess-d1",
        extra_context={
            "active_subject": {"path": "data/a.tsv", "kind": "file"},
            "last_failure_state": {"tool": "code_executor", "count": 2},
            "last_evidence_state": "not-a-dict",
            "last_subject_action_class": None,
            "recent_image_artifacts": [
                {"path": "runtime/sess-d1/plot.png"},
                "not-a-dict-entry",
                {"path": "runtime/sess-d1/chart.svg"},
            ],
        },
    )


def _capture_metadata(monkeypatch, seed: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], SimpleNamespace]:
    captured: List[Dict[str, Any]] = []

    def _fake_update(session_id: str, updater) -> None:
        metadata = dict(seed)
        captured.append(updater(metadata))

    monkeypatch.setattr(action_handlers, "_update_session_metadata", _fake_update)
    return captured, _fake_agent()


def test_agent_module_reexports_action_handlers_source():
    assert agent_module._persist_runtime_context is action_handlers._persist_runtime_context


def test_both_entry_points_persist_identical_metadata(monkeypatch):
    seed = {"last_evidence_state": "stale-value", "unrelated": "keep-me"}
    via_agent, agent_obj = _capture_metadata(monkeypatch, seed)
    agent_module._persist_runtime_context(agent_obj)

    via_handlers, handler_obj = _capture_metadata(monkeypatch, seed)
    action_handlers._persist_runtime_context(handler_obj)

    assert via_agent == via_handlers == [
        {
            "unrelated": "keep-me",
            "active_subject": {"path": "data/a.tsv", "kind": "file"},
            "last_failure_state": {"tool": "code_executor", "count": 2},
            "recent_image_artifacts": [
                {"path": "runtime/sess-d1/plot.png"},
                {"path": "runtime/sess-d1/chart.svg"},
            ],
        }
    ]
    # Persisted copies must be independent of the live extra_context dicts.
    assert via_agent[0]["active_subject"] is not agent_obj.extra_context["active_subject"]


def test_missing_session_id_skips_persistence(monkeypatch):
    calls: List[Any] = []
    monkeypatch.setattr(
        action_handlers,
        "_update_session_metadata",
        lambda session_id, updater: calls.append(session_id),
    )
    agent_module._persist_runtime_context(SimpleNamespace(session_id=None, extra_context={}))
    action_handlers._persist_runtime_context(SimpleNamespace(session_id="", extra_context={}))
    assert calls == []
