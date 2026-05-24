from __future__ import annotations

import json

from app.services.plans.dependency_enrichment import (
    check_artifact_readiness,
    enrich_plan_dependencies,
)
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


def test_fuzzy_match_resolves_namespace_mismatch() -> None:
    producer = PlanNode(
        id=11,
        plan_id=601,
        name="Prepare Annotated Working Dataset",
        status="pending",
        metadata={"artifact_contract": {"publishes": ["phage_genomics.working_dataset_json"]}},
    )
    consumer = PlanNode(
        id=12,
        plan_id=601,
        name="Extract Genomic Feature Vectors",
        status="pending",
        metadata={"artifact_contract": {"requires": ["phage_diversity.annotated_dataset_csv"]}},
    )
    tree = _tree(601, producer, consumer)

    result = enrich_plan_dependencies(tree)

    assert 11 in consumer.dependencies
    assert any(
        e.consumer_task_id == 12 and e.producer_task_id == 11
        for e in result.added_edges
    )
    assert "phage_diversity.annotated_dataset_csv" in producer.metadata["artifact_contract"]["publishes"]


def test_fuzzy_match_resolves_format_mismatch() -> None:
    producer = PlanNode(
        id=20,
        plan_id=602,
        name="Generate Report CSV",
        status="pending",
        metadata={"artifact_contract": {"publishes": ["results.report_csv"]}},
    )
    consumer = PlanNode(
        id=21,
        plan_id=602,
        name="Convert Report to TSV",
        status="pending",
        metadata={"artifact_contract": {"requires": ["results.report_tsv"]}},
    )
    tree = _tree(602, producer, consumer)

    result = enrich_plan_dependencies(tree)

    assert 20 in consumer.dependencies
    assert "results.report_tsv" in producer.metadata["artifact_contract"]["publishes"]


def test_fuzzy_match_does_not_match_unrelated_aliases() -> None:
    producer = PlanNode(
        id=30,
        plan_id=603,
        name="Generate Phylogenetic Tree",
        status="pending",
        metadata={"artifact_contract": {"publishes": ["phylo.tree_newick"]}},
    )
    consumer = PlanNode(
        id=31,
        plan_id=603,
        name="Analyze Diversity Metrics",
        status="pending",
        metadata={"artifact_contract": {"requires": ["diversity.metrics_csv"]}},
    )
    tree = _tree(603, producer, consumer)

    result = enrich_plan_dependencies(tree)

    assert 30 not in consumer.dependencies
    assert not result.added_edges


def test_fuzzy_match_prefers_higher_similarity() -> None:
    producer_a = PlanNode(
        id=40,
        plan_id=604,
        name="Prepare Metadata Table",
        status="pending",
        metadata={"artifact_contract": {"publishes": ["genomics.metadata_table"]}},
    )
    producer_b = PlanNode(
        id=41,
        plan_id=604,
        name="Prepare Annotated Dataset",
        status="pending",
        metadata={"artifact_contract": {"publishes": ["genomics.annotated_dataset"]}},
    )
    consumer = PlanNode(
        id=42,
        plan_id=604,
        name="Extract Features from Annotated Dataset",
        status="pending",
        instruction="From the annotated working dataset, extract features.",
        metadata={"artifact_contract": {"requires": ["genomics.annotated_dataset_csv"]}},
    )
    tree = _tree(604, producer_a, producer_b, consumer)

    result = enrich_plan_dependencies(tree)

    assert 41 in consumer.dependencies
    assert 40 not in consumer.dependencies
