"""A dependency-blocked task gets one bounded self-repair round instead of stopping the chain."""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.routers import plan_routes
from app.services.plans import blocker_recovery
from app.services.plans.executor_text_utils import _coerce_blocked_dependency_payload
from app.services.plans.plan_models import PlanNode, PlanTree


class _JobStore:
    def __init__(self) -> None:
        self.success: list[dict] = []
        self.failure: list[dict] = []

    def mark_running(self, _job_id: str) -> None:
        return None

    def update_stats(self, _job_id: str, _stats: dict) -> None:
        return None

    def mark_success(self, _job_id: str, **kwargs: Any) -> None:
        self.success.append(kwargs)

    def mark_failure(self, _job_id: str, error: str, **kwargs: Any) -> None:
        self.failure.append({"error": error, **kwargs})

    def is_execution_paused(self, _job_id: str) -> bool:
        return False

    def wait_while_paused(self, _job_id: str, poll_seconds: float = 1.0) -> bool:
        return True


def _tree() -> PlanTree:
    nodes = {
        2: PlanNode(id=2, plan_id=9, name="P1a Pfam scan", status="completed",
                    execution_result=json.dumps({"content": "Pfam download stopped at 30% after the cell timeout"})),
        3: PlanNode(id=3, plan_id=9, name="P1b filter", status="pending"),
        4: PlanNode(id=4, plan_id=9, name="P2 report", status="pending"),
    }
    tree = PlanTree(id=9, title="Plan 9", nodes=nodes)
    tree.rebuild_adjacency()
    return tree


def _blocked(task_id: int) -> SimpleNamespace:
    return SimpleNamespace(
        plan_id=9, task_id=task_id, status="skipped", duration_sec=0.1,
        content="BLOCKED_DEPENDENCY: P1a (Task 2) Pfam-A.hmm download truncated",
        metadata={"blocked_by_dependencies": True,
                  "blocked_dependency_detail": "P1a (Task 2) Pfam-A.hmm download truncated"},
    )


def _ok(task_id: int) -> SimpleNamespace:
    return SimpleNamespace(plan_id=9, task_id=task_id, status="completed", duration_sec=0.1, content="ok",
                           metadata={})


def _diagnosis_json(target: int) -> str:
    return json.dumps({"target_task_id": target, "brief": "Pfam download hit the 300 s cell limit",
                       "alternatives": ["Resume with curl -C - in several cells", "Use /data/pfam copy"]})


@pytest.fixture
def chain(monkeypatch):
    tree = _tree()
    store = _JobStore()
    calls: list[tuple[int, Any]] = []
    monkeypatch.setattr("app.services.plans.executor_reuse.completed_result", lambda *a: None)
    monkeypatch.setattr(plan_routes, "plan_decomposition_jobs", store)
    monkeypatch.setattr(plan_routes, "log_job_event", lambda *a, **kw: None)
    monkeypatch.setattr(plan_routes._plan_repo, "get_plan_tree", lambda *_a: tree)
    monkeypatch.setattr(plan_routes._plan_executor, "_repo", plan_routes._plan_repo, raising=False)
    monkeypatch.setattr(blocker_recovery, "diagnose",
                        lambda blocked, detail, candidates, chat=None, **_kw: blocker_recovery._parse(
                            _diagnosis_json(2), [blocked.id] + [c.id for c in candidates]))
    return SimpleNamespace(tree=tree, store=store, calls=calls)


def _install(monkeypatch, chain, responder) -> None:
    def execute(plan_id, task_id, config=None):
        chain.calls.append((task_id, config))
        return responder(task_id, config, len([c for c in chain.calls if c[0] == task_id]))

    monkeypatch.setattr(plan_routes._plan_executor, "execute_task", execute)


def test_chain_repairs_upstream_then_retries_blocked_task(monkeypatch, chain) -> None:
    def responder(task_id, config, nth):
        if task_id == 3 and nth == 1:
            return _blocked(3)
        return _ok(task_id)

    _install(monkeypatch, chain, responder)
    plan_routes._run_task_chain_job(job_id="j", plan_id=9, target_task_id=4, task_order=[2, 3, 4])

    assert [c[0] for c in chain.calls] == [2, 3, 2, 3, 4]
    upstream_cfg, retry_cfg = chain.calls[2][1], chain.calls[3][1]
    assert upstream_cfg.force_rerun and retry_cfg.force_rerun
    info = upstream_cfg.session_context[blocker_recovery.SELF_REPAIR_CONTEXT_KEY]
    assert info["role"] == "upstream_repair" and info["blocked_task_id"] == 3
    assert retry_cfg.session_context[blocker_recovery.SELF_REPAIR_CONTEXT_KEY]["role"] == "blocked_retry"
    # Regular steps do not carry a repair brief.
    assert blocker_recovery.SELF_REPAIR_CONTEXT_KEY not in chain.calls[1][1].session_context
    assert chain.store.failure == [] and len(chain.store.success) == 1
    steps = chain.store.success[0]["result"]["steps"]
    assert steps[1]["status"] == "completed" and steps[1]["self_repair"]["target_task_id"] == 2


def test_chain_stops_when_upstream_repair_does_not_complete(monkeypatch, chain) -> None:
    def responder(task_id, config, nth):
        if task_id == 2:
            return _ok(2) if nth == 1 else _blocked(2)
        return _blocked(task_id) if task_id == 3 else _ok(task_id)

    monkeypatch.setenv("PLAN_BLOCKER_RECOVERY_MAX_TASK_ROUNDS", "1")
    _install(monkeypatch, chain, responder)
    plan_routes._run_task_chain_job(job_id="j", plan_id=9, target_task_id=4, task_order=[2, 3, 4])

    # Upstream rerun stays blocked, so the blocked task is not retried pointlessly.
    assert [c[0] for c in chain.calls] == [2, 3, 2]
    assert chain.store.failure and "Task #3 was skipped" in chain.store.failure[0]["error"]


def test_self_repair_rounds_are_bounded_per_job(monkeypatch, chain) -> None:
    monkeypatch.setenv("PLAN_BLOCKER_RECOVERY_MAX_ROUNDS", "1")
    _install(monkeypatch, chain, lambda task_id, config, nth: _blocked(task_id) if task_id == 3 else _ok(task_id))
    plan_routes._run_task_chain_job(job_id="j", plan_id=9, target_task_id=4, task_order=[2, 3, 4])
    # One round: blocked run, upstream repair, single retry — then the chain stops.
    assert [c[0] for c in chain.calls] == [2, 3, 2, 3]
    assert chain.store.failure


def test_self_repair_can_be_disabled(monkeypatch, chain) -> None:
    monkeypatch.setenv("PLAN_BLOCKER_RECOVERY_ENABLED", "0")
    _install(monkeypatch, chain, lambda task_id, config, nth: _blocked(task_id))
    plan_routes._run_task_chain_job(job_id="j", plan_id=9, target_task_id=4, task_order=[3, 4])
    assert [c[0] for c in chain.calls] == [3]


def test_full_plan_job_repairs_blocked_task_in_place(monkeypatch, chain) -> None:
    monkeypatch.setattr(plan_routes, "_run_task_audit_repair_after_execution", lambda **_kw: {})
    monkeypatch.setattr(plan_routes, "_build_plan_execution_snapshot",
                        lambda *a, **kw: {"active_task_ids": set(), "active_jobs": []})
    monkeypatch.setattr(blocker_recovery, "diagnose",
                        lambda blocked, detail, candidates, chat=None, **_kw: blocker_recovery.RepairDiagnosis(
                            target_task_id=blocked.id, brief="cell timeout", alternatives=["resume"]))

    def responder(task_id, config, nth):
        return _blocked(3) if task_id == 3 and nth == 1 else _ok(task_id)

    _install(monkeypatch, chain, responder)
    plan_routes._run_full_plan_job(job_id="f", plan_id=9, task_order=[3, 4], stop_on_failure=True)
    # The blocker was inside task 3 itself: retry it directly, no upstream rerun.
    assert [c[0] for c in chain.calls] == [3, 3, 4]
    assert chain.store.failure == [] and chain.store.success


def test_plain_failure_is_not_retried_by_self_repair(monkeypatch, chain) -> None:
    _install(monkeypatch, chain, lambda task_id, config, nth: SimpleNamespace(
        status="failed", duration_sec=0.1, content="Missing output after bounded repair"))
    plan_routes._run_task_chain_job(job_id="j", plan_id=9, target_task_id=4, task_order=[3, 4])
    assert [c[0] for c in chain.calls] == [3]


def test_blocked_upstream_marker_is_recognised() -> None:
    payload, status = _coerce_blocked_dependency_payload(
        {"status": "completed", "content": "执行判定：BLOCKED_UPSTREAM（R-05 门被守住，P2 未放行）"})
    assert status == "skipped"
    assert payload["metadata"]["blocked_by_dependencies"] is True


def test_diagnose_prefers_llm_target_and_falls_back_to_heuristic() -> None:
    tree = _tree()
    blocked, candidates = tree.nodes[3], [tree.nodes[2]]
    good = blocker_recovery.diagnose(blocked, "upstream truncated", candidates, chat=lambda _p: _diagnosis_json(2))
    assert good.source == "llm" and good.target_task_id == 2 and good.alternatives

    # Unknown task ids or malformed JSON never escape the allowed set.
    for raw in (_diagnosis_json(99), "not json", '{"target_task_id": true, "brief": "x"}'):
        fallback = blocker_recovery.diagnose(blocked, "Task 2 output truncated", candidates, chat=lambda _p, r=raw: r)
        assert fallback.source == "heuristic" and fallback.target_task_id == 2

    def boom(_prompt: str) -> str:
        raise RuntimeError("provider down")

    assert blocker_recovery.diagnose(blocked, "x", candidates, chat=boom).source == "heuristic"


def test_prompt_lines_only_present_for_repair_runs() -> None:
    assert blocker_recovery.prompt_lines({}) == []
    ctx = blocker_recovery.repair_context(
        {"session_id": "s"},
        diagnosis=blocker_recovery.RepairDiagnosis(target_task_id=2, brief="timeout", alternatives=["resume"]),
        blocked_task_id=3, role="upstream_repair")
    [line] = blocker_recovery.prompt_lines(ctx)
    assert "SELF-REPAIR RUN" in line and "task #3" in line and "resume" in line
    assert "Do not repeat the same approach" in line


def test_already_repaired_or_cancelled_results_are_not_repaired_again() -> None:
    result = _blocked(3)
    assert blocker_recovery.is_blocked_result(result)
    result.metadata[blocker_recovery.SELF_REPAIR_CONTEXT_KEY] = {"blocked_task_id": 3}
    assert not blocker_recovery.is_blocked_result(result)
    cancelled = _blocked(3)
    cancelled.metadata["termination_reason"] = "cancelled"
    assert not blocker_recovery.is_blocked_result(cancelled)
    assert not blocker_recovery.is_blocked_result(SimpleNamespace(status="failed", metadata=None))


def _gate_fail(task_id: int) -> SimpleNamespace:
    return SimpleNamespace(plan_id=9, task_id=task_id, status="completed", duration_sec=0.1,
                           content="召回 24/26 = 92.31%，未达到 95% 门槛，不放行 P2。", metadata={})


def _gate_review(node, content, candidates, chat=None, history=None):
    if "未达到" not in content:
        return None
    return blocker_recovery.RepairDiagnosis(target_task_id=node.id, brief="recall 92.31% < 95%",
                                            alternatives=["add GH25 family"], source="llm", kind="gate")


def test_failed_quality_gate_is_reworked_not_passed_on(monkeypatch, chain) -> None:
    monkeypatch.setattr(blocker_recovery, "review_gate", _gate_review)

    def responder(task_id, config, nth):
        return _gate_fail(3) if task_id == 3 and nth == 1 else _ok(task_id)

    _install(monkeypatch, chain, responder)
    plan_routes._run_task_chain_job(job_id="j", plan_id=9, target_task_id=4, task_order=[2, 3, 4])

    assert [c[0] for c in chain.calls] == [2, 3, 3, 4]
    retry_ctx = chain.calls[2][1].session_context[blocker_recovery.SELF_REPAIR_CONTEXT_KEY]
    assert retry_ctx["kind"] == "gate" and retry_ctx["role"] == "blocked_retry"
    [line] = blocker_recovery.prompt_lines(chain.calls[2][1].session_context)
    assert "never lower a threshold" in line
    assert chain.store.success and not chain.store.failure


def test_gate_still_unmet_after_rework_stops_before_later_tasks(monkeypatch, chain) -> None:
    monkeypatch.setattr(blocker_recovery, "review_gate", _gate_review)
    monkeypatch.setenv("PLAN_BLOCKER_RECOVERY_MAX_TASK_ROUNDS", "2")
    marked: list[tuple[int, Any]] = []
    monkeypatch.setattr(plan_routes._plan_repo, "update_task",
                        lambda plan_id, task_id, **kw: marked.append((task_id, kw.get("status"))))
    _install(monkeypatch, chain, lambda task_id, config, nth: _gate_fail(3) if task_id == 3 else _ok(task_id))
    plan_routes._run_task_chain_job(job_id="j", plan_id=9, target_task_id=4, task_order=[2, 3, 4])

    # Two rework rounds, then stop: task 4 never runs on substandard output.
    assert [c[0] for c in chain.calls] == [2, 3, 3, 3]
    assert marked == [(3, "failed")]
    assert chain.store.failure and "quality gate" in chain.store.failure[0]["error"]
    assert chain.store.failure[0]["result"]["steps"][-1]["self_repair"]["quality_gate_unmet"]


def test_met_gate_is_not_reworked(monkeypatch, chain) -> None:
    monkeypatch.setattr(blocker_recovery, "review_gate", lambda *a, **kw: None)
    _install(monkeypatch, chain, lambda task_id, config, nth: SimpleNamespace(
        plan_id=9, task_id=task_id, status="completed", duration_sec=0.1,
        content="召回 100%，达到 95% 门槛，放行。", metadata={}))
    plan_routes._run_task_chain_job(job_id="j", plan_id=9, target_task_id=4, task_order=[2, 3, 4])
    assert [c[0] for c in chain.calls] == [2, 3, 4] and chain.store.success


def test_gate_review_requires_explicit_llm_failure() -> None:
    tree = _tree()
    node, candidates = tree.nodes[3], [tree.nodes[2]]
    yes = json.dumps({"gate_failed": True, "target_task_id": 2, "brief": "missing family",
                      "alternatives": ["add family"]})
    found = blocker_recovery.review_gate(node, "未达标", candidates, chat=lambda _p: yes)
    assert found is not None and found.kind == "gate" and found.target_task_id == 2
    no = json.dumps({"gate_failed": False, "target_task_id": 3, "brief": "", "alternatives": []})
    assert blocker_recovery.review_gate(node, "门槛已满足", candidates, chat=lambda _p: no) is None
    assert blocker_recovery.review_gate(node, "未达标", candidates, chat=lambda _p: "garbage") is None


def test_a_completed_task_whose_main_execution_failed_is_a_gate_candidate() -> None:
    # p69: plan_9 task 17 ended "completed" with the truth-barrier text and no trees.
    unverified = SimpleNamespace(status="completed", metadata={}, content=(
        "本轮真正的执行工具未成功完成（code_executor 失败）。因此不能把后续分析性表述视为已验证结论。"))
    assert blocker_recovery.gate_review_candidate(unverified)
    plain = SimpleNamespace(status="completed", metadata={}, content="四棵域树已生成并通过拓扑检查。")
    assert not blocker_recovery.gate_review_candidate(plain)
