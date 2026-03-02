from __future__ import annotations

import asyncio

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

