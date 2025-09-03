"""
工具函数包

包含项目中使用的各种工具和辅助函数。
"""

# 从原utils.py导入核心函数
import re
import json
import asyncio
import threading
from typing import Any, Dict, List, Optional, Tuple, Union


def plan_prefix(title: str) -> str:
    """Return the standard plan title prefix for task names, e.g. "[Title] "."""
    return f"[{title}] "


def split_prefix(name: str):
    """Split a task name into (title, short_name) by extracting the leading [title] prefix.

    Returns (None, name) if no prefix exists.
    """
    m = re.match(r"^\[(.*?)\]\s+(.*)$", name)
    if m:
        return m.group(1), m.group(2)
    return None, name


def parse_json_obj(text: str):
    """Try to parse a JSON object or array from arbitrary LLM output."""
    # Extract a JSON-looking block first (support object or array)
    m = re.search(r"\{.*\}|\[.*\]", text, flags=re.S)
    cand = m.group(0) if m else text.strip()
    try:
        obj = json.loads(cand)
        if isinstance(obj, (dict, list)):
            return obj
    except Exception:
        pass
    # Try single->double quotes
    try:
        obj = json.loads(cand.replace("'", '"'))
        if isinstance(obj, (dict, list)):
            return obj
    except Exception:
        pass
    return None


def run_async(coro):
    """Safely run an async coroutine from sync code."""
    try:
        loop = asyncio.get_running_loop()
        if loop.is_running():
            result_box: Dict[str, Any] = {}
            error_box: Dict[str, BaseException] = {}

            def _runner():
                try:
                    result_box["value"] = asyncio.run(coro)
                except BaseException as e:  # noqa: BLE001
                    error_box["error"] = e

            t = threading.Thread(target=_runner, daemon=True)
            t.start()
            t.join()
            if "error" in error_box:
                raise error_box["error"]
            return result_box.get("value")
        else:
            try:
                return loop.run_until_complete(coro)
            except RuntimeError:
                return asyncio.run(coro)
    except RuntimeError:
        # No current event loop
        return asyncio.run(coro)


# 从route_helpers导入新的解析函数
from .route_helpers import (
    parse_bool,
    parse_int, 
    parse_opt_float,
    parse_opt_int,
    parse_strategy,
    parse_schedule,
    sanitize_manual_list,
    sanitize_context_options,
)

__all__ = [
    "plan_prefix",
    "split_prefix", 
    "parse_json_obj",
    "run_async",
    "parse_bool",
    "parse_int",
    "parse_opt_float", 
    "parse_opt_int",
    "parse_strategy",
    "parse_schedule",
    "sanitize_manual_list",
    "sanitize_context_options",
]
