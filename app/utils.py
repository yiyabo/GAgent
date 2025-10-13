import asyncio
import json
import re
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
    """Try to parse a JSON object or array from arbitrary LLM output.

    Strategy:
    - Prefer extracting the first {...} or [...] block.
    - Attempt json.loads; if it fails, try replacing single quotes with double quotes and retry.
    - Return a dict/list on success, else None.
    """
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


# -------------------------------
# Async helpers (safe blocking run)
# -------------------------------


def run_async(coro):
    """Safely run an async coroutine from sync code.

    Behavior:
    - If there's no running loop in this thread: run directly (run_until_complete / asyncio.run)
    - If a loop is running (e.g., called from within an async context):
      use loop.run_in_executor to avoid nested event loop issues.
    """
    try:
        loop = asyncio.get_running_loop()
        if loop.is_running():
            # Use executor instead of creating new thread with new event loop
            # This avoids the dangerous nested event loop pattern
            import concurrent.futures
            import functools
            
            def _sync_runner():
                # Create new event loop in executor thread
                new_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(new_loop)
                try:
                    return new_loop.run_until_complete(coro)
                finally:
                    new_loop.close()
                    asyncio.set_event_loop(None)
            
            # Run in thread pool executor for better resource management
            executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            try:
                future = executor.submit(_sync_runner)
                return future.result(timeout=300)  # 5 minute timeout
            finally:
                executor.shutdown(wait=False)
        else:
            try:
                return loop.run_until_complete(coro)
            except RuntimeError:
                return asyncio.run(coro)
    except RuntimeError:
        # No current event loop
        return asyncio.run(coro)
