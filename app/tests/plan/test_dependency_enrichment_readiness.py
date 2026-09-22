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


def test_fuzzy_match_does_not_collapse_plain_filenames() -> None:
    producer = PlanNode(
        id=25,
        plan_id=700,
        name="Render Final Report",
        status="pending",
        metadata={"artifact_contract": {"publishes": ["deliverables_manifest.md"]}},
    )
    consumer = PlanNode(
        id=21,
        plan_id=700,
        name="Run PSM Sensitivity",
        status="pending",
        metadata={"artifact_contract": {"requires": ["psm_skipped_note.md"]}},
    )
    tree = _tree(700, producer, consumer)

    result = enrich_plan_dependencies(tree)

    assert 25 not in consumer.dependencies
    assert "psm_skipped_note.md" not in producer.metadata["artifact_contract"]["publishes"]
    assert not result.added_edges


def test_artifact_edge_inverting_branch_order_is_rejected() -> None:
    root = PlanNode(id=1, plan_id=701, name="Root", status="pending")
    comp_data = PlanNode(id=2, plan_id=701, name="T2 Data", status="pending", parent_id=1, position=1, depth=1)
    comp_report = PlanNode(
        id=5, plan_id=701, name="T5 Report", status="pending",
        parent_id=1, position=2, depth=1, dependencies=[2],
    )
    early = PlanNode(
        id=7, plan_id=701, name="T2.1 Read CSV", status="pending", parent_id=2, position=0, depth=2,
        metadata={"artifact_contract": {"requires": ["deliverables_manifest.md"]}},
    )
    late = PlanNode(
        id=25, plan_id=701, name="T5.3 Render", status="pending", parent_id=5, position=2, depth=2,
        metadata={"artifact_contract": {"publishes": ["deliverables_manifest.md"]}},
    )
    tree = _tree(701, root, comp_data, comp_report, early, late)

    result = enrich_plan_dependencies(tree)

    assert 25 not in early.dependencies
    assert any(
        e.consumer_task_id == 7 and e.producer_task_id == 25
        for e in result.skipped_inversion_edges
    )


def test_artifact_edge_respecting_branch_order_is_injected() -> None:
    root = PlanNode(id=1, plan_id=702, name="Root", status="pending")
    comp_stats = PlanNode(id=3, plan_id=702, name="T3 Stats", status="pending", parent_id=1, position=1, depth=1)
    comp_report = PlanNode(
        id=5, plan_id=702, name="T5 Report", status="pending",
        parent_id=1, position=2, depth=1, dependencies=[3],
    )
    producer = PlanNode(
        id=14, plan_id=702, name="T3.4 Finalize Stats", status="pending", parent_id=3, position=3, depth=2,
        metadata={"artifact_contract": {"publishes": ["stats_summary.csv"]}},
    )
    consumer = PlanNode(
        id=24, plan_id=702, name="T5.2 Embed Numbers", status="pending", parent_id=5, position=1, depth=2,
        metadata={"artifact_contract": {"requires": ["stats_summary.csv"]}},
    )
    tree = _tree(702, root, comp_stats, comp_report, producer, consumer)

    result = enrich_plan_dependencies(tree)

    assert 14 in consumer.dependencies
    assert any(
        e.consumer_task_id == 24 and e.producer_task_id == 14
        for e in result.added_edges
    )


def test_same_composite_later_producer_rejected_earlier_allowed() -> None:
    parent = PlanNode(id=2, plan_id=703, name="T2 Composite", status="pending")
    first = PlanNode(
        id=7, plan_id=703, name="T2.1 Pair Data", status="pending", parent_id=2, position=0, depth=2,
        metadata={"artifact_contract": {"publishes": ["paired_dataset.csv"], "requires": ["qc_flags.json"]}},
    )
    second = PlanNode(
        id=8, plan_id=703, name="T2.2 QC Checks", status="pending", parent_id=2, position=1, depth=2,
        metadata={"artifact_contract": {"publishes": ["qc_flags.json"], "requires": ["paired_dataset.csv"]}},
    )
    tree = _tree(703, parent, first, second)

    result = enrich_plan_dependencies(tree)

    assert 8 not in first.dependencies
    assert 7 in second.dependencies
    assert any(
        e.consumer_task_id == 7 and e.producer_task_id == 8
        for e in result.skipped_inversion_edges
    )
