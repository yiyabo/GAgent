from __future__ import annotations

import json

from app.services.plans.dependency_enrichment import check_artifact_readiness
from app.services.plans.plan_models import PlanNode, PlanTree


def _tree(plan_id: int, *nodes: PlanNode) -> PlanTree:
    tree = PlanTree(id=plan_id, title=f"Plan {plan_id}")
    for node in nodes:
        tree.nodes[node.id] = node
    tree.rebuild_adjacency()
    return tree


def test_readiness_uses_effective_completed_state_for_soft_failed_producer() -> None:
    producer = PlanNode(
        id=1,
        plan_id=501,
        name="Producer",
        status="failed",
        metadata={"artifact_contract": {"publishes": ["demo.output_json"]}},
        execution_result=json.dumps(
            {
                "status": "failed",
                "content": "Completed with verification warning.",
                "metadata": {
                    "execution_status": "completed",
                    "verification_status": "warning",
                },
            }
        ),
    )
    consumer = PlanNode(
        id=2,
        plan_id=501,
        name="Consumer",
        status="pending",
        dependencies=[1],
        metadata={"artifact_contract": {"requires": ["demo.output_json"]}},
    )
    tree = _tree(501, producer, consumer)

    block = check_artifact_readiness(
        consumer,
        tree,
        state_by_task={1: {"effective_status": "completed"}},
    )

    assert block is None


def test_readiness_still_blocks_real_failed_producer() -> None:
    producer = PlanNode(
        id=1,
        plan_id=502,
        name="Producer",
        status="failed",
        metadata={"artifact_contract": {"publishes": ["demo.output_json"]}},
        execution_result=json.dumps(
            {
                "status": "failed",
                "content": "Execution failed before producing output.",
                "metadata": {"execution_status": "failed"},
            }
        ),
    )
    consumer = PlanNode(
        id=2,
        plan_id=502,
        name="Consumer",
        status="pending",
        dependencies=[1],
        metadata={"artifact_contract": {"requires": ["demo.output_json"]}},
    )
    tree = _tree(502, producer, consumer)

    block = check_artifact_readiness(
        consumer,
        tree,
        state_by_task={1: {"effective_status": "failed"}},
    )

    assert block is not None
    assert block.missing_artifacts[0].alias == "demo.output_json"
    assert block.missing_artifacts[0].reason == "producer_not_completed"


def test_readiness_accepts_completed_execution_status_without_resolver_state() -> None:
    producer = PlanNode(
        id=1,
        plan_id=503,
        name="Producer",
        status="failed",
        metadata={"artifact_contract": {"publishes": ["demo.output_json"]}},
        execution_result=json.dumps(
            {
                "status": "failed",
                "content": "Completed with verification warning.",
                "metadata": {"execution_status": "completed"},
            }
        ),
    )
    consumer = PlanNode(
        id=2,
        plan_id=503,
        name="Consumer",
        status="pending",
        dependencies=[1],
        metadata={"artifact_contract": {"requires": ["demo.output_json"]}},
    )
    tree = _tree(503, producer, consumer)

    assert check_artifact_readiness(consumer, tree) is None
