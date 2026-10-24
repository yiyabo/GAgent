"""Shared NCBI E-utilities client: one pace for the process, retries, per-session fairness."""

from __future__ import annotations

import asyncio
import logging

import httpx
import pytest

from tool_box.tools_impl import ncbi_eutils


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    for name in (ncbi_eutils.ENV_API_KEY, ncbi_eutils.ENV_EMAIL, ncbi_eutils.ENV_TOOL, ncbi_eutils.ENV_MAX_RPS):
        monkeypatch.delenv(name, raising=False)
    ncbi_eutils._SCHEDULE.reset()
    monkeypatch.setattr(ncbi_eutils, "_retry_delay", lambda attempt, retry_after: 0.0)
    yield
    ncbi_eutils._SCHEDULE.reset()


@pytest.fixture()
def no_pacing(monkeypatch):
    calls = []

    async def _slot():
        calls.append(1)

    monkeypatch.setattr(ncbi_eutils, "wait_for_slot", _slot)
    return calls


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ------------------------------------------------------------------ pacing
def test_slots_are_handed_out_one_interval_apart():
    schedule = ncbi_eutils.SlotSchedule()
    delays = [schedule.reserve(0.5) for _ in range(3)]
    assert delays[0] == pytest.approx(0.0, abs=0.05)
    assert delays[1] == pytest.approx(0.5, abs=0.05)
    assert delays[2] == pytest.approx(1.0, abs=0.05)


def test_pace_stays_below_ncbi_limits(monkeypatch):
    assert ncbi_eutils.max_requests_per_second() == ncbi_eutils.DEFAULT_RPS_WITHOUT_KEY
    monkeypatch.setenv(ncbi_eutils.ENV_MAX_RPS, "50")
    assert ncbi_eutils.max_requests_per_second() == ncbi_eutils.NCBI_LIMIT_WITHOUT_KEY
    monkeypatch.setenv(ncbi_eutils.ENV_MAX_RPS, "1")
    assert ncbi_eutils.max_requests_per_second() == 1.0
    monkeypatch.setenv(ncbi_eutils.ENV_MAX_RPS, "fast")
    assert ncbi_eutils.max_requests_per_second() == ncbi_eutils.DEFAULT_RPS_WITHOUT_KEY
    monkeypatch.delenv(ncbi_eutils.ENV_MAX_RPS)
    monkeypatch.setenv(ncbi_eutils.ENV_API_KEY, "k")
    assert ncbi_eutils.max_requests_per_second() == ncbi_eutils.DEFAULT_RPS_WITH_KEY
    monkeypatch.setenv(ncbi_eutils.ENV_MAX_RPS, "50")
    assert ncbi_eutils.max_requests_per_second() == ncbi_eutils.NCBI_LIMIT_WITH_KEY


async def test_concurrent_callers_share_one_schedule(monkeypatch):
    monkeypatch.setattr(ncbi_eutils, "max_requests_per_second", lambda: 20.0)
    loop = asyncio.get_running_loop()
    starts = []

    async def _one():
        await ncbi_eutils.wait_for_slot()
        starts.append(loop.time())

    await asyncio.gather(*(_one() for _ in range(5)))
    starts.sort()
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert all(gap >= 0.04 for gap in gaps), gaps


def test_common_params_carry_tool_email_and_key(monkeypatch):
    assert ncbi_eutils.common_params() == {"tool": "gagent"}
    monkeypatch.setenv(ncbi_eutils.ENV_EMAIL, "lab@example.org")
    monkeypatch.setenv(ncbi_eutils.ENV_API_KEY, "secret-key")
    monkeypatch.setenv(ncbi_eutils.ENV_TOOL, "phage-lab")
    assert ncbi_eutils.common_params() == {"tool": "phage-lab", "email": "lab@example.org", "api_key": "secret-key"}


# ------------------------------------------------------------------ retries
async def test_429_is_retried_and_tool_param_is_sent(no_pacing):
    seen = []

    def handler(request):
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(429, headers={"Retry-After": "1"}, json={"error": "API rate limit exceeded"})
        return httpx.Response(200, json={"ok": True})

    async with _client(handler) as client:
        response = await ncbi_eutils.request("esearch.fcgi", {"db": "nuccore", "term": "x"}, client=client)
    assert response.json() == {"ok": True}
    assert len(seen) == 2 and len(no_pacing) == 2
    assert seen[0].url.params["tool"] == "gagent"
    assert seen[0].url.path == "/entrez/eutils/esearch.fcgi"


async def test_rate_limit_body_on_a_200_reply_is_retried(no_pacing):
    replies = [httpx.Response(200, text='{"error":"API rate limit exceeded","count":"11"}'), httpx.Response(200, text="ok")]

    async with _client(lambda request: replies.pop(0)) as client:
        response = await ncbi_eutils.request("efetch.fcgi", {"id": "1"}, client=client)
    assert response.text == "ok"


async def test_gives_up_after_the_last_attempt(no_pacing):
    async with _client(lambda request: httpx.Response(429)) as client:
        with pytest.raises(ncbi_eutils.NcbiRequestError) as err:
            await ncbi_eutils.request("efetch.fcgi", {"id": "1"}, client=client)
    assert err.value.rate_limited is True
    assert len(no_pacing) == ncbi_eutils.MAX_ATTEMPTS


async def test_a_bad_request_is_not_retried(no_pacing, monkeypatch):
    monkeypatch.setenv(ncbi_eutils.ENV_API_KEY, "secret-key")
    async with _client(lambda request: httpx.Response(400, text="Cannot process ID list")) as client:
        with pytest.raises(ncbi_eutils.NcbiRequestError) as err:
            await ncbi_eutils.request("efetch.fcgi", {"id": "bogus"}, client=client)
    assert err.value.status_code == 400 and err.value.rate_limited is False
    assert "Cannot process ID list" in str(err.value)
    assert "secret-key" not in str(err.value)
    assert len(no_pacing) == 1


async def test_an_echoed_api_key_is_redacted_from_errors(no_pacing, monkeypatch):
    monkeypatch.setenv(ncbi_eutils.ENV_API_KEY, "secret-key")
    body = '{"error":"API key invalid","api-key":"secret-key","type":"invalid"}'
    async with _client(lambda request: httpx.Response(400, text=body)) as client:
        with pytest.raises(ncbi_eutils.NcbiRequestError) as err:
            await ncbi_eutils.request("einfo.fcgi", {}, client=client)
    assert "API key invalid" in str(err.value)
    assert "secret-key" not in str(err.value)


async def test_a_rate_limit_reply_pauses_every_caller(no_pacing, monkeypatch):
    monkeypatch.setattr(ncbi_eutils, "_retry_delay", lambda attempt, retry_after: 5.0)
    monkeypatch.setattr(ncbi_eutils.asyncio, "sleep", _no_sleep)
    replies = [httpx.Response(429), httpx.Response(200, text="ok")]
    async with _client(lambda request: replies.pop(0)) as client:
        await ncbi_eutils.request("esearch.fcgi", {}, client=client)
    assert ncbi_eutils._SCHEDULE.reserve(0.1) > 4.0


async def _no_sleep(_seconds):
    return None


async def test_network_errors_are_retried(no_pacing):
    attempts = []

    def handler(request):
        attempts.append(1)
        if len(attempts) == 1:
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, text="ok")

    async with _client(handler) as client:
        response = await ncbi_eutils.request("einfo.fcgi", {}, client=client)
    assert response.text == "ok" and len(attempts) == 2


async def test_retry_logs_never_show_the_api_key(no_pacing, monkeypatch, caplog):
    monkeypatch.setenv(ncbi_eutils.ENV_API_KEY, "secret-key")
    replies = [httpx.Response(503), httpx.Response(200, text="ok")]
    with caplog.at_level(logging.WARNING):
        async with _client(lambda request: replies.pop(0)) as client:
            await ncbi_eutils.request("esummary.fcgi", {"id": "1"}, client=client)
    assert "retrying" in caplog.text
    assert "secret-key" not in caplog.text


async def test_unknown_endpoints_are_refused():
    with pytest.raises(ValueError):
        await ncbi_eutils.request("../evil", {})


# ------------------------------------------------------------------ fairness
async def test_one_session_holds_at_most_two_requests_while_others_proceed(no_pacing):
    active = {"a": 0, "b": 0}
    peak = {"a": 0, "b": 0}

    async def handler(request):
        who = request.url.params["who"]
        active[who] += 1
        peak[who] = max(peak[who], active[who])
        await asyncio.sleep(0.03)
        active[who] -= 1
        return httpx.Response(200, text=who)

    async with _client(handler) as client:
        async def call(who):
            with ncbi_eutils.owner_scope(f"session-{who}"):
                return (await ncbi_eutils.request("esummary.fcgi", {"who": who}, client=client)).text

        results = await asyncio.gather(*[call("a") for _ in range(6)], call("b"))
    assert results.count("a") == 6 and results.count("b") == 1
    assert peak["a"] == ncbi_eutils.MAX_IN_FLIGHT_PER_OWNER
    assert peak["b"] == 1
    assert ncbi_eutils._in_flight == {}


# ------------------------------------------------------------------ cache
def test_text_cache_evicts_oldest_and_expires(monkeypatch):
    cache = ncbi_eutils.TextCache(max_bytes=40, ttl_seconds=10)
    cache.put(("a",), "x" * 10)
    cache.put(("b",), "y" * 10)
    assert cache.get(("a",)) == "x" * 10
    cache.put(("c",), "z" * 10)
    cache.put(("d",), "w" * 10)
    cache.put(("e",), "v" * 10)
    assert cache.get(("b",)) is None  # least recently used went first
    assert cache.get(("e",)) == "v" * 10
    cache.put(("huge",), "h" * 30)  # bigger than a quarter of the cache: never stored
    assert cache.get(("huge",)) is None

    now = [1000.0]
    monkeypatch.setattr(ncbi_eutils.time, "monotonic", lambda: now[0])
    fresh = ncbi_eutils.TextCache(ttl_seconds=10)
    fresh.put(("k",), "value")
    now[0] += 11
    assert fresh.get(("k",)) is None
