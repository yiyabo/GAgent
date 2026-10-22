"""Spreads Tavily searches over several API keys and skips a key that ran out.

Free Tavily keys have small monthly quotas and a rate limit. With several keys the
calls rotate, so the quotas are used evenly and the combined limit is the sum. A
key the API rejects for quota, rate or authentication reasons rests for a while
and the next key serves the call.

State is per process. Keys never appear in logs or errors: callers refer to a key
by its position.
"""

from __future__ import annotations

import time
from typing import Callable, Dict, List, Sequence, Tuple

# HTTP statuses that mean "this key cannot serve right now", and how long it rests.
# 432/433 are Tavily's plan and pay-as-you-go limits; 429 is the per-minute rate limit.
KEY_REST_SECONDS: Dict[int, float] = {
    401: 24 * 3600.0,
    403: 24 * 3600.0,
    429: 60.0,
    432: 3600.0,
    433: 3600.0,
}


class TavilyKeyPool:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._rest_until: Dict[str, float] = {}
        self._turn = 0

    def order(self, keys: Sequence[str]) -> List[Tuple[int, str]]:
        """Keys that can serve now as (position, key), starting at the next one in turn."""
        if not keys:
            return []
        start = self._turn % len(keys)
        self._turn += 1
        now = self._clock()
        rotated = [(i, keys[i]) for i in list(range(start, len(keys))) + list(range(start))]
        return [(i, key) for i, key in rotated if self._rest_until.get(key, 0.0) <= now]

    def rest(self, key: str, status: int) -> None:
        seconds = KEY_REST_SECONDS.get(status)
        if seconds is not None:
            self._rest_until[key] = self._clock() + seconds

    def seconds_until_available(self, keys: Sequence[str]) -> float:
        """Shortest wait before any of `keys` can serve again; 0 when one can already."""
        now = self._clock()
        waits = [max(0.0, self._rest_until.get(key, 0.0) - now) for key in keys]
        return min(waits) if waits else 0.0

    def reset(self) -> None:
        self._rest_until.clear()
        self._turn = 0


POOL = TavilyKeyPool()
