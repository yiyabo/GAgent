from __future__ import annotations

import json
import logging

import pytest

from app.services.foundation.logging_config import setup_logging
from app.services.foundation.settings import get_settings


@pytest.fixture(autouse=True)
def _reset():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass
    setup_logging()


def test_file_handler_writes_json_line(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LOG_DIR", str(tmp_path))
    monkeypatch.setenv("LOG_FILE_ENABLED", "1")
    get_settings.cache_clear()

    setup_logging()
    logging.getLogger("app.test").info("persist-me-%s", "hello")

    log_file = tmp_path / "app.log"
    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")
    assert "persist-me-hello" in content
    assert '"logger": "app.test"' in content


def test_json_lines_include_timestamp(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LOG_DIR", str(tmp_path))
    monkeypatch.setenv("LOG_FILE_ENABLED", "1")
    get_settings.cache_clear()

    setup_logging()
    logging.getLogger("app.test").info("ts-check")

    content = (tmp_path / "app.log").read_text(encoding="utf-8")
    payload = json.loads(content.strip().splitlines()[-1])
    assert payload["message"] == "ts-check"
    assert payload["ts"].endswith("+00:00")


def test_file_logging_can_be_disabled(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("LOG_DIR", str(tmp_path))
    monkeypatch.setenv("LOG_FILE_ENABLED", "0")
    get_settings.cache_clear()

    setup_logging()
    logging.getLogger("app.test").info("should-not-persist")

    assert not (tmp_path / "app.log").exists()


def test_file_logging_survives_reinit_same_dir(tmp_path, monkeypatch) -> None:
    # Simulates process restart: a fresh setup_logging() against an existing
    # log dir must append, not wipe.
    monkeypatch.setenv("LOG_DIR", str(tmp_path))
    get_settings.cache_clear()

    setup_logging()
    logging.getLogger("app.test").info("before-restart")
    setup_logging()
    logging.getLogger("app.test").info("after-restart")

    content = (tmp_path / "app.log").read_text(encoding="utf-8")
    assert "before-restart" in content
    assert "after-restart" in content
