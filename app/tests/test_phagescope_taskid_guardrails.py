from __future__ import annotations

import asyncio

import pytest

from tool_box.tools_impl import phagescope as phagescope_module
from tool_box.tools_impl.phagescope import phagescope_handler


def test_phagescope_save_all_rejects_non_numeric_taskid_alias() -> None:
    result = asyncio.run(
        phagescope_handler(
            action="save_all",
            taskid="act_a1c0d8007a554d9a98d688d7394f5ecd",
        )
    )

    assert result["success"] is False
    assert result["status_code"] == 400
    assert result["action"] == "save_all"
    assert result["error_code"] == "invalid_taskid"


def test_phagescope_taskid_alias_resolves_via_action_run_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        phagescope_module,
        "_lookup_remote_taskid_by_tracking_job",
        lambda _job_id, session_id=None: None,
    )
    monkeypatch.setattr(
        phagescope_module,
        "_lookup_remote_taskid_by_action_run",
        lambda _run_id, session_id=None: "37468",
    )

    resolved = phagescope_module._resolve_phagescope_taskid(
        "act_a1c0d8007a554d9a98d688d7394f5ecd",
        session_id="session-x",
    )
    assert resolved == "37468"
