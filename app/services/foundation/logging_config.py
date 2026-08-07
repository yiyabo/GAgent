#!/usr/bin/env python3
"""
log

JSON/ , default JSON, support LOG_LEVEL  LOG_FORMAT . 
"""
import json
import logging
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict

from app.services.foundation.settings import get_settings

_LOG_FILE_MAX_BYTES = 50 * 1024 * 1024
_LOG_FILE_BACKUP_COUNT = 10


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:  # type: ignore[override]
        payload: Dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in getattr(record, "__dict__", {}).items():
            if key in {
                "args",
                "msg",
                "levelno",
                "levelname",
                "name",
                "pathname",
                "filename",
                "module",
                "exc_info",
                "exc_text",
                "stack_info",
                "lineno",
                "funcName",
                "created",
                "msecs",
                "relativeCreated",
                "thread",
                "threadName",
                "processName",
                "process",
            }:
                continue
            if key.startswith("_"):
                continue
            payload[key] = value
        return json.dumps(payload, ensure_ascii=False)


def setup_logging() -> None:
    settings = get_settings()
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)

    try:
        level_name = str(settings.log_level).upper()
    except Exception:
        level_name = "INFO"
    root.setLevel(getattr(logging, level_name, logging.INFO))
    handler = logging.StreamHandler(sys.stdout)

    try:
        fmt_name = str(settings.log_format).lower()
    except Exception:
        fmt_name = "json"
    if fmt_name == "json":
        handler.setFormatter(JsonFormatter())
    else:
        formatter = logging.Formatter(fmt="%(levelname)s %(name)s: %(message)s")
        handler.setFormatter(formatter)

    root.addHandler(handler)

    try:
        file_enabled = bool(getattr(settings, "log_file_enabled", True))
    except Exception:
        file_enabled = True
    if file_enabled:
        try:
            log_dir = Path(str(getattr(settings, "log_dir", "logs") or "logs"))
            log_dir.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                log_dir / "app.log",
                maxBytes=_LOG_FILE_MAX_BYTES,
                backupCount=_LOG_FILE_BACKUP_COUNT,
                encoding="utf-8",
            )
            file_handler.setFormatter(handler.formatter)
            root.addHandler(file_handler)
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "File logging disabled: cannot open log file (%s)", exc
            )
