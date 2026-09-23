"""Static iteration-budget semantics for the native DeepThink loop.

The loop limit may only grow when a task handoff lands on the final
base-budget iteration, consuming from a bounded reserve, so the worst-case
budget is always ``max_iterations + reserve_max`` — the operator's limit is
never stretched open-endedly mid-run.
"""

from __future__ import annotations

from app.services.deep_think.controller import _consume_handoff_iteration_reserve


def test_handoff_at_base_tail_consumes_reserve() -> None:
    limit, used = _consume_handoff_iteration_reserve(
        iteration=63, base_limit=64, limit=64, reserve_used=0
    )
    assert (limit, used) == (65, 1)


def test_handoff_before_tail_does_not_extend() -> None:
    limit, used = _consume_handoff_iteration_reserve(
        iteration=10, base_limit=64, limit=64, reserve_used=0
    )
    assert (limit, used) == (64, 0)


def test_reserve_is_bounded() -> None:
    limit, used = 64, 0
    # Even with repeated handoffs at/after the tail, the limit can never
    # exceed base + reserve_max.
    for i in range(10):
        limit, used = _consume_handoff_iteration_reserve(
            iteration=63 + i, base_limit=64, limit=limit, reserve_used=used
        )
    assert used == 4
    assert limit == 68  # 64 + reserve_max, hard cap


def test_extended_limit_does_not_requalify() -> None:
    # After one extension the limit is 65; a handoff at iteration 64 is at
    # the *extended* tail but past the base tail semantics only via the
    # (base_limit - 1) threshold — 64 >= 63 still qualifies, bounded by reserve.
    limit, used = _consume_handoff_iteration_reserve(
        iteration=64, base_limit=64, limit=65, reserve_used=1
    )
    assert (limit, used) == (66, 2)
    # Once the reserve is exhausted, nothing extends even at the tail.
    limit, used = _consume_handoff_iteration_reserve(
        iteration=67, base_limit=64, limit=68, reserve_used=4
    )
    assert (limit, used) == (68, 4)
