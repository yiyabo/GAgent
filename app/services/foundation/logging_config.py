#!/usr/bin/env python3
"""
log

JSON/ , default JSON, support LOG_LEVEL  LOG_FORMAT . 
"""
import json
import logging
import sys
from typing import Any, Dict

from app.services.foundation.settings import get_settings


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:  # type: ignore[override]
        payload: Dict[str, Any] = {
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
