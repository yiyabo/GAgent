"""Native-loop state and iteration accounting; re-exported by controller."""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


def _consume_handoff_iteration_reserve(
    *,
    iteration: int,
    base_limit: int,
    limit: int,
    reserve_used: int,
    reserve_max: int = 4,
) -> tuple[int, int]:
    """Consume one handoff iteration reserve cycle, if eligible.

    Static budget semantics: the loop limit may grow beyond ``base_limit``
    only when a task handoff lands on the final base-budget iteration (the
    newly bound task would otherwise get zero cycles), and never by more
    than ``reserve_max`` cycles in total. Returns ``(new_limit, new_used)``;
    the worst-case limit is always ``base_limit + reserve_max``.
    """
    if reserve_used >= reserve_max or iteration < base_limit - 1:
        return limit, reserve_used
    return limit + 1, reserve_used + 1



@dataclass
class _NativeCycleState:
    """Mutable per-run counters shared by the native-loop stage helpers.

    Pure container — no behaviour. Field names mirror the loop-local
    variables of the pre-split ``_think_native`` one-to-one (incl. the
    static budget caps: the iteration limit starts at max_iterations and can
    only grow via ``_consume_handoff_iteration_reserve`` when a task handoff
    lands on the final base-budget iteration, so the worst case is
    max_iterations + handoff_reserve_max — never an open-ended mutation).
    """

    confidence: float = 0.0
    last_tool_cycle_signature: Optional[str] = None
    identical_tool_cycle_count: int = 0
    probe_only_execution_cycles: int = 0
    readonly_verification_cycles: int = 0
    forced_probe_followthrough_attempts: int = 0
    forced_handoff_followthrough_attempts: int = 0
    had_real_execution_tool: bool = False
    partial_completion_retry_count: int = 0
    max_partial_retries: int = 3
    handoff_reserve_max: int = 4
    base_iteration_limit: int = 0
    last_real_execution_tool_results: List[Dict[str, Any]] = field(default_factory=list)
    force_verified_execution_finalization: bool = False
    pending_handoff_task_id: Optional[int] = None
    pending_handoff_previous_task_id: Optional[int] = None
    structured_plan_finalize_nudge_plan_id: Optional[int] = None
    bound_task_before_cycle: Optional[int] = None
    runtime_iteration_limit: int = 0
    handoff_reserve_used: int = 0

