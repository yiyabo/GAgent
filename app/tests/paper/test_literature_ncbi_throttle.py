"""Tests for NCBI rate-limiting and retry logic in literature_pipeline."""
from __future__ import annotations

import asyncio
import time

import pytest

from tool_box.tools_impl.literature_pipeline import (
    _ncbi_throttle,
    _NCBI_RATE_LIMIT_INTERVAL,
)


@pytest.mark.asyncio
async def test_ncbi_throttle_enforces_interval():
    """Two consecutive _ncbi_throttle() calls should be spaced by at least the interval."""
    # Reset the global state
    import tool_box.tools_impl.literature_pipeline as mod
    mod._ncbi_last_request_time = 0.0

    start = time.monotonic()
    await _ncbi_throttle()
    await _ncbi_throttle()
    elapsed = time.monotonic() - start

    # Second call should have waited at least _NCBI_RATE_LIMIT_INTERVAL
    assert elapsed >= _NCBI_RATE_LIMIT_INTERVAL * 0.9  # small tolerance


@pytest.mark.asyncio
async def test_ncbi_throttle_no_wait_after_interval():
    """If enough time has passed, _ncbi_throttle() should not sleep."""
    import tool_box.tools_impl.literature_pipeline as mod
    # Pretend the last request was long ago
    mod._ncbi_last_request_time = time.monotonic() - 10.0

    start = time.monotonic()
    await _ncbi_throttle()
    elapsed = time.monotonic() - start

    assert elapsed < 0.1  # should return almost immediately
