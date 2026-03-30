"""
Opt-in live checks against https://phageapi.deepomics.org (see data/experiment_phagescope/phageapi.md).

The upstream API doc does not define an api_key: it uses `userid` on submit/list, etc.
Our handler optionally sends Bearer token when `token` is provided.

Run manually:
  PHAGESCOPE_LIVE_TEST=1 pytest app/tests/test_phagescope_live.py -v

CI: skipped unless PHAGESCOPE_LIVE_TEST=1 (network + external availability).
"""

from __future__ import annotations

import asyncio
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("PHAGESCOPE_LIVE_TEST", "").strip() not in {"1", "true", "yes"},
    reason="Set PHAGESCOPE_LIVE_TEST=1 to run live PhageScope HTTP checks",
)


def test_phagescope_ping_live() -> None:
    from tool_box.tools_impl.phagescope import phagescope_handler

    r = asyncio.run(phagescope_handler(action="ping"))
    assert r.get("success") is True
    assert r.get("status_code") == 200
    assert r.get("action") == "ping"


def test_phagescope_task_list_live() -> None:
    from tool_box.tools_impl.phagescope import phagescope_handler

    r = asyncio.run(
        phagescope_handler(action="task_list", userid="agent_default_user", timeout=120.0)
    )
    assert r.get("success") is True
    assert r.get("status_code") == 200
    data = r.get("data")
    assert isinstance(data, dict)
    assert isinstance(data.get("results"), list)


def test_phagescope_input_check_live() -> None:
    import json

    from tool_box.tools_impl.phagescope import phagescope_handler

    r = asyncio.run(
        phagescope_handler(
            action="input_check",
            phageid=json.dumps(["test_placeholder_id"]),
            inputtype="enter",
            timeout=60.0,
        )
    )
    assert r.get("status_code") == 200
    assert r.get("success") is True


def test_phagescope_task_detail_live_soft() -> None:
    """If task_detail returns HTTP 500, xfail (server-side)."""
    from tool_box.tools_impl.phagescope import phagescope_handler

    listed = asyncio.run(
        phagescope_handler(action="task_list", userid="agent_default_user", timeout=120.0)
    )
    assert listed.get("success") is True
    results = (listed.get("data") or {}).get("results")
    if not isinstance(results, list) or not results:
        pytest.skip("No tasks returned for agent_default_user")
    tid = results[0].get("id")
    if tid is None:
        pytest.skip("Task list entry missing id")

    detail = asyncio.run(phagescope_handler(action="task_detail", taskid=str(tid), timeout=120.0))
    if detail.get("status_code") == 500:
        pytest.xfail(
            f"PhageScope task_detail returned HTTP 500 for taskid={tid} (transient or server issue)"
        )
    assert detail.get("success") is True
    assert detail.get("status_code") == 200
