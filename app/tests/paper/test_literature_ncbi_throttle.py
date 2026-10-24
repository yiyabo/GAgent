"""literature_pipeline paces its NCBI calls through the shared E-utilities schedule."""
from __future__ import annotations

import time

import pytest

from tool_box.tools_impl import ncbi_eutils
from tool_box.tools_impl.literature_pipeline import _ncbi_throttle


@pytest.fixture(autouse=True)
def _fresh_schedule(monkeypatch):
    monkeypatch.delenv(ncbi_eutils.ENV_API_KEY, raising=False)
    monkeypatch.delenv(ncbi_eutils.ENV_MAX_RPS, raising=False)
    ncbi_eutils._SCHEDULE.reset()
    yield
    ncbi_eutils._SCHEDULE.reset()


@pytest.mark.asyncio
async def test_ncbi_throttle_enforces_interval():
    """Two consecutive _ncbi_throttle() calls are spaced by at least one interval."""
    interval = 1.0 / ncbi_eutils.max_requests_per_second()
    start = time.monotonic()
    await _ncbi_throttle()
    await _ncbi_throttle()
    assert time.monotonic() - start >= interval * 0.9


@pytest.mark.asyncio
async def test_ncbi_throttle_no_wait_after_interval():
    """With no recent NCBI call, _ncbi_throttle() returns almost immediately."""
    start = time.monotonic()
    await _ncbi_throttle()
    assert time.monotonic() - start < 0.1


@pytest.mark.asyncio
async def test_a_slot_booked_by_another_tool_delays_literature_calls():
    """sequence_fetch and literature_pipeline share one schedule, so together they stay under NCBI's limit."""
    ncbi_eutils._SCHEDULE.reserve(0.3)  # another tool just booked the current slot
    start = time.monotonic()
    await _ncbi_throttle()
    assert time.monotonic() - start >= 0.25
