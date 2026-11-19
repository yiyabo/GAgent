"""Per-token request-rate and concurrency guards (process-local).

The daily byte quota lives in SQLite (``store``) so it holds across workers;
rate and concurrency are per process, which with one app worker is per token.
"""

from __future__ import annotations

import threading
import time
from collections import deque


class TokenLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._calls: dict[str, deque[float]] = {}
        self._active: dict[str, int] = {}

    def acquire(self, token_id: str, *, per_minute: int, max_concurrency: int) -> str | None:
        """Reserve a slot; return a refusal code or ``None`` when admitted."""
        now = time.monotonic()
        with self._lock:
            window = self._calls.setdefault(token_id, deque())
            while window and now - window[0] >= 60.0:
                window.popleft()
            if per_minute > 0 and len(window) >= per_minute:
                return "rate_limited"
            if max_concurrency > 0 and self._active.get(token_id, 0) >= max_concurrency:
                return "too_many_concurrent_requests"
            window.append(now)
            self._active[token_id] = self._active.get(token_id, 0) + 1
            return None

    def release(self, token_id: str) -> None:
        with self._lock:
            left = self._active.get(token_id, 0) - 1
            if left > 0:
                self._active[token_id] = left
            else:
                self._active.pop(token_id, None)

    def reset(self) -> None:
        with self._lock:
            self._calls.clear()
            self._active.clear()


limiter = TokenLimiter()
