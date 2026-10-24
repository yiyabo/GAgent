"""Shared access to NCBI E-utilities: one rate limit for the whole process.

NCBI counts E-utility requests per IP address (3 per second) or per API key
(10 per second), whichever tool sends them. Tools that pace themselves
separately can still exceed the limit together, so every E-utility call goes
through :func:`request` here:

- one schedule of start times, shared by all callers (thread- and
  event-loop-agnostic, because tool handlers run on more than one loop);
- retries on 429 and transient 5xx/network errors, honouring ``Retry-After``;
- at most ``MAX_IN_FLIGHT_PER_OWNER`` requests in flight per chat session, so
  one session's batch cannot take every slot from the others.

Env: ``NCBI_API_KEY`` (optional; raises the limit to 10/s), ``NCBI_EMAIL``,
``NCBI_TOOL`` (default ``gagent``), ``NCBI_EUTILS_MAX_RPS`` (lowers the pace;
never above NCBI's own limit).
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import os
import random
import threading
import time
from collections import OrderedDict
from contextlib import asynccontextmanager, contextmanager
from typing import Any, AsyncIterator, Dict, Iterator, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
ENDPOINTS = frozenset(
    {"einfo.fcgi", "esearch.fcgi", "esummary.fcgi", "efetch.fcgi", "elink.fcgi", "epost.fcgi"}
)

ENV_API_KEY = "NCBI_API_KEY"
ENV_EMAIL = "NCBI_EMAIL"
ENV_TOOL = "NCBI_TOOL"
ENV_MAX_RPS = "NCBI_EUTILS_MAX_RPS"
DEFAULT_TOOL_NAME = "gagent"

NCBI_LIMIT_WITHOUT_KEY = 3.0
NCBI_LIMIT_WITH_KEY = 10.0
# Below NCBI's limits on purpose: the server shares its address with other programs.
DEFAULT_RPS_WITHOUT_KEY = 2.5
DEFAULT_RPS_WITH_KEY = 8.0

MAX_IN_FLIGHT_PER_OWNER = 2
MAX_ATTEMPTS = 4
MAX_BACKOFF_SECONDS = 30.0
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
_OWNER_POLL_SECONDS = 0.05


class NcbiRequestError(RuntimeError):
    """An E-utility request that failed after the retries it was allowed."""

    def __init__(self, message: str, *, status_code: Optional[int] = None, rate_limited: bool = False) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.rate_limited = rate_limited


def _api_key() -> str:
    return os.getenv(ENV_API_KEY, "").strip()


def max_requests_per_second() -> float:
    has_key = bool(_api_key())
    ceiling = NCBI_LIMIT_WITH_KEY if has_key else NCBI_LIMIT_WITHOUT_KEY
    default = DEFAULT_RPS_WITH_KEY if has_key else DEFAULT_RPS_WITHOUT_KEY
    raw = os.getenv(ENV_MAX_RPS, "").strip()
    if raw:
        try:
            value = float(raw)
        except ValueError:
            return default
        if value > 0:
            return min(value, ceiling)
    return default


def common_params() -> Dict[str, str]:
    """``tool``/``email``/``api_key`` that NCBI asks every E-utility request to carry."""
    params = {"tool": os.getenv(ENV_TOOL, "").strip() or DEFAULT_TOOL_NAME}
    email = os.getenv(ENV_EMAIL, "").strip()
    if email:
        params["email"] = email
    key = _api_key()
    if key:
        params["api_key"] = key
    return params


class SlotSchedule:
    """Hands out request start times at least ``interval`` apart, across threads and event loops."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next_start = 0.0

    def reserve(self, interval: float) -> float:
        """Book the next start time; returns how long the caller must wait for it."""
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next_start)
            self._next_start = start + interval
            return start - now

    def push_back(self, seconds: float) -> None:
        """Start no new request for ``seconds``: NCBI's limit is per address, so a 429 concerns every caller."""
        with self._lock:
            self._next_start = max(self._next_start, time.monotonic() + seconds)

    def reset(self) -> None:
        with self._lock:
            self._next_start = 0.0


_SCHEDULE = SlotSchedule()


async def wait_for_slot() -> None:
    """Wait until this caller may send its next E-utility request."""
    delay = _SCHEDULE.reserve(1.0 / max_requests_per_second())
    if delay > 0:
        await asyncio.sleep(delay)


# --------------------------------------------------------------------- per-session fairness
_current_owner: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("ncbi_owner", default=None)
_in_flight: Dict[str, int] = {}
_in_flight_lock = threading.Lock()


@contextmanager
def owner_scope(owner: Optional[str]) -> Iterator[None]:
    """Attribute the E-utility requests made inside this block to ``owner`` (a session id)."""
    value = str(owner).strip() if owner else ""
    token = _current_owner.set(value or None)
    try:
        yield
    finally:
        _current_owner.reset(token)


def _try_enter(owner: str) -> bool:
    with _in_flight_lock:
        count = _in_flight.get(owner, 0)
        if count >= MAX_IN_FLIGHT_PER_OWNER:
            return False
        _in_flight[owner] = count + 1
        return True


def _leave(owner: str) -> None:
    with _in_flight_lock:
        count = _in_flight.get(owner, 0) - 1
        if count > 0:
            _in_flight[owner] = count
        else:
            _in_flight.pop(owner, None)


@asynccontextmanager
async def _owner_turn(owner: Optional[str]) -> AsyncIterator[None]:
    if not owner:
        yield
        return
    # Wait for a turn before booking a slot, so a session at its cap leaves the slots to others.
    while not _try_enter(owner):
        await asyncio.sleep(_OWNER_POLL_SECONDS)
    try:
        yield
    finally:
        _leave(owner)


# --------------------------------------------------------------------------- request
def _retry_delay(attempt: int, retry_after: Optional[str]) -> float:
    if retry_after:
        try:
            return min(max(float(retry_after), 0.0), MAX_BACKOFF_SECONDS)
        except ValueError:
            pass
    return min(2.0 ** attempt, MAX_BACKOFF_SECONDS) + random.uniform(0.0, 0.5)


def _redact(text: str) -> str:
    # NCBI echoes the key back in its "API key invalid" error body.
    key = _api_key()
    return text.replace(key, "[redacted]") if key else text


def _looks_rate_limited(response: httpx.Response) -> bool:
    # NCBI's limit error is a small JSON body; read only the start of a 200 reply.
    head = response.text[:200] if response.status_code == 429 or len(response.content) < 512 else ""
    return "API rate limit exceeded" in head


async def request(
    endpoint: str,
    params: Dict[str, Any],
    *,
    client: Optional[httpx.AsyncClient] = None,
    timeout: float = 30.0,
    owner: Optional[str] = None,
    method: str = "GET",
) -> httpx.Response:
    """Send one E-utility request under the shared rate limit; returns the 200 response.

    Raises :class:`NcbiRequestError` on a 4xx reply or once the retries are spent.
    Error messages never contain the request URL, which carries the API key.
    """
    if endpoint not in ENDPOINTS:
        raise ValueError(f"unknown E-utility endpoint: {endpoint}")
    url = EUTILS_BASE + endpoint
    query = {key: value for key, value in params.items() if value is not None}
    query.update(common_params())
    who = owner if owner is not None else _current_owner.get()

    async with _owner_turn(who):
        http = client or httpx.AsyncClient(follow_redirects=True)
        try:
            last_error: Optional[NcbiRequestError] = None
            for attempt in range(MAX_ATTEMPTS):
                await wait_for_slot()
                retry_after: Optional[str] = None
                try:
                    if method == "POST":
                        response = await http.post(url, data=query, timeout=timeout)
                    else:
                        response = await http.get(url, params=query, timeout=timeout)
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    last_error = NcbiRequestError(f"NCBI {endpoint} could not be reached ({type(exc).__name__})")
                else:
                    status = response.status_code
                    rate_limited = status == 429 or (status == 200 and _looks_rate_limited(response))
                    if status == 200 and not rate_limited:
                        return response
                    if not rate_limited and status not in _RETRY_STATUS:
                        detail = _redact(response.text[:200].strip().replace("\n", " "))
                        raise NcbiRequestError(
                            f"NCBI {endpoint} returned HTTP {status}" + (f": {detail}" if detail else ""),
                            status_code=status,
                        )
                    retry_after = response.headers.get("Retry-After")
                    last_error = NcbiRequestError(
                        f"NCBI {endpoint} " + ("rate limit exceeded" if rate_limited else f"returned HTTP {status}"),
                        status_code=status,
                        rate_limited=rate_limited,
                    )
                if attempt + 1 < MAX_ATTEMPTS:
                    delay = _retry_delay(attempt, retry_after)
                    if last_error.rate_limited:
                        _SCHEDULE.push_back(delay)
                    logger.warning("[NCBI] %s; retrying in %.1fs (attempt %d/%d)", last_error, delay, attempt + 1, MAX_ATTEMPTS)
                    await asyncio.sleep(delay)
            assert last_error is not None
            raise last_error
        finally:
            if client is None:
                await http.aclose()


# --------------------------------------------------------------------------- cache
class TextCache:
    """Small in-memory LRU for immutable NCBI records (for example a versioned accession's FASTA)."""

    def __init__(self, *, max_bytes: int = 32 * 1024 * 1024, ttl_seconds: float = 24 * 3600) -> None:
        self._max_bytes = max_bytes
        self._ttl = ttl_seconds
        self._items: "OrderedDict[Tuple[Any, ...], Tuple[float, str]]" = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()

    def get(self, key: Tuple[Any, ...]) -> Optional[str]:
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            stored_at, value = item
            if time.monotonic() - stored_at > self._ttl:
                self._drop(key)
                return None
            self._items.move_to_end(key)
            return value

    def put(self, key: Tuple[Any, ...], value: str) -> None:
        size = len(value.encode("utf-8", errors="replace"))
        if size > self._max_bytes // 4:
            return  # one huge record would evict everything else
        with self._lock:
            if key in self._items:
                self._drop(key)
            self._items[key] = (time.monotonic(), value)
            self._bytes += size
            while self._bytes > self._max_bytes and self._items:
                self._drop(next(iter(self._items)))

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._bytes = 0

    def _drop(self, key: Tuple[Any, ...]) -> None:
        _, value = self._items.pop(key)
        self._bytes -= len(value.encode("utf-8", errors="replace"))
