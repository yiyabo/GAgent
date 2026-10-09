"""Continuation-hint cluster of ``agent`` (W5a cluster ①).

Split out of ``agent.py`` per
design/2026-09-24-backend-godfiles-refactor-plan.md §4.8.  The
filename/absolute-path recognizers and the "where were we" continuation
summary they fed were gated on the ``execute`` request tier
(``request_tier == "execute" and brevity_hint``), which flat routing can no
longer produce (2026-10 tier removal), so the entire cluster was unreachable
and was deleted in the tier-label scaffolding sweep.  ``agent.py`` re-exports
the one surviving name, ``_current_user_turn_index_from_history``, and the
class call site is unchanged.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _current_user_turn_index_from_history(
    history: Optional[List[Dict[str, Any]]],
) -> int:
    if not history:
        return 1
    return 1 + sum(
        1
        for item in history
        if str(item.get("role") or "").strip().lower() == "user"
    )
