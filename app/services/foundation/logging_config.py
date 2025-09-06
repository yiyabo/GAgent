#!/usr/bin/env python3
"""
结构化日志初始化

提供 JSON/普通 文本两种格式，默认 JSON，支持 LOG_LEVEL 与 LOG_FORMAT 环境变量控制。
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
        # 附加 extra 字段（如有）
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
    # 清理已有 handler，避免重复初始化
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
