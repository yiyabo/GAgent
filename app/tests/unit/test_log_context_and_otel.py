"""Log context propagation + optional OTel shim (no hard dependency)."""

from __future__ import annotations

import json
import logging

import pytest

from app.services.foundation.logging_config import JsonFormatter
from app.services.foundation.logging_context import (
    LogContextFilter,
    bind_log_context,
    clear_log_context,
    current_log_context,
    log_context_scope,
)
from app.services.foundation import otel


@pytest.fixture(autouse=True)
def _clean_context():
    clear_log_context()
    yield
    clear_log_context()


def test_bind_and_current_context() -> None:
    assert current_log_context() == {}
    bind_log_context(run_id="r1", session_id="s1")
    assert current_log_context() == {"run_id": "r1", "session_id": "s1"}
    bind_log_context(owner_id="u1")
    assert current_log_context()["owner_id"] == "u1"
    clear_log_context()
    assert current_log_context() == {}


def test_scope_restores_previous_values() -> None:
    bind_log_context(run_id="outer")
    with log_context_scope(run_id="inner", session_id="s2"):
        assert current_log_context()["run_id"] == "inner"
        assert current_log_context()["session_id"] == "s2"
    assert current_log_context() == {"run_id": "outer"}


def test_filter_injects_fields_and_preserves_explicit_extras() -> None:
    bind_log_context(run_id="r9", session_id="s9")
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "hello", (), None)
    assert LogContextFilter().filter(record)
    assert record.run_id == "r9"
    assert record.session_id == "s9"

    explicit = logging.LogRecord("t", logging.INFO, __file__, 1, "hi", (), None)
    explicit.run_id = "explicit"
    LogContextFilter().filter(explicit)
    assert explicit.run_id == "explicit"


def test_json_formatter_carries_context_fields() -> None:
    bind_log_context(run_id="r7")
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "json me", (), None)
    LogContextFilter().filter(record)
    payload = json.loads(JsonFormatter().format(record))
    assert payload["message"] == "json me"
    assert payload["run_id"] == "r7"
    assert payload["level"] == "INFO"


def test_otel_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OTEL_ENABLED", raising=False)
    assert not otel.otel_requested()
    assert otel.init_otel() is False
    assert not otel.otel_available()
    # span is a silent no-op when disabled
    with otel.otel_span("unit_test_span", run_id="r1"):
        pass


def test_otel_requested_but_sdk_missing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    pytest.importorskip("importlib.util", reason="stdlib")
    import importlib.util

    if importlib.util.find_spec("opentelemetry") is not None:
        pytest.skip("SDK installed in this env; missing-SDK path not exercisable")
    monkeypatch.setenv("OTEL_ENABLED", "1")
    assert otel.otel_requested()
    with caplog.at_level("INFO"):
        assert otel.init_otel() is False
    assert "not installed" in caplog.text
    assert not otel.otel_available()
