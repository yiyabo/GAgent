from __future__ import annotations

import json

from app.routers import plan_routes
from app.services.plans.artifact_contracts import find_candidate_source_for_alias
from app.services.plans.acceptance_criteria import extract_explicit_deliverables_from_text
from app.services.plans.plan_executor import ExecutionConfig, ExecutionResponse, PlanExecutor
from app.services.plans.plan_models import PlanNode, PlanTree
from app.services.plans.task_verification import TaskVerificationService


class _RepoStub:
    def __init__(self, tree: PlanTree) -> None:
        self._tree = tree
        self.update_calls: list[tuple[int, int, dict]] = []

    def get_plan_tree(self, plan_id: int) -> PlanTree:
        assert plan_id == self._tree.id
        return self._tree

    def update_task(self, plan_id: int, task_id: int, **kwargs):
        assert plan_id == self._tree.id
        self.update_calls.append((plan_id, task_id, dict(kwargs)))
        node = self._tree.nodes[task_id]
        if kwargs.get("name") is not None:
            node.name = kwargs["name"]
        if kwargs.get("instruction") is not None:
            node.instruction = kwargs["instruction"]
        if kwargs.get("status") is not None:
            node.status = kwargs["status"]
        if kwargs.get("execution_result") is not None:
            node.execution_result = kwargs["execution_result"]
        if kwargs.get("metadata") is not None:
            node.metadata = kwargs["metadata"]
        return node


class _LLMStub:
    def __init__(self, response: ExecutionResponse) -> None:
        self._response = response

    def generate(self, _prompt: str, _config=None, **kwargs):
        return self._response


def _write_valid_ml_metrics(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            "models": {"RandomForest": {"accuracy": 0.91, "macro_f1": 0.82}},
            "metadata": {
                "label_source": "phage_ml.training_metadata_parquet:host_label",
                "row_ids_source": "phage_ml.feature_row_ids_json",
            },
        }),
        encoding="utf-8",
    )


def _write_valid_label_alignment(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            "label_source": "phage_ml.training_metadata_parquet:host_label",
            "label_alignment": "inner join on feature_row_ids_json",
            "training_samples": 2,
            "is_synthetic": False,
        }),
        encoding="utf-8",
    )


def test_find_candidate_source_for_alias_searches_nested_parent_directory(tmp_path):
    parent = tmp_path / "task_19"
    metrics = parent / "ml_traditional" / "validation_metrics.json"
    label_alignment = parent / "phage_ml" / "metadata" / "label_alignment.json"
    _write_valid_ml_metrics(metrics)
    _write_valid_label_alignment(label_alignment)

    assert find_candidate_source_for_alias(
        alias="ml_traditional.validation_metrics_json",
        candidate_paths=[str(parent)],
    ) == str(metrics)
    assert find_candidate_source_for_alias(
        alias="phage_ml.label_alignment_json",
        candidate_paths=[str(parent)],
    ) == str(label_alignment)


def test_task_verifier_allows_artifact_contract_override_for_matching_publish_failures(tmp_path):
    parent = tmp_path / "raw_files" / "task_19"
    _write_valid_ml_metrics(parent / "ml_traditional" / "validation_metrics.json")
    _write_valid_label_alignment(parent / "phage_ml" / "metadata" / "label_alignment.json")
    node = PlanNode(
        id=19,
        plan_id=93,
        name="Train traditional ML models",
        metadata={
            "artifact_contract": {
                "publishes": [
                    "ml_traditional.validation_metrics_json",
                    "phage_ml.label_alignment_json",
                ]
            },
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [
                    {"type": "file_exists", "path": str(tmp_path / "expected" / "ml_traditional" / "validation_metrics.json")},
                    {"type": "file_nonempty", "path": str(tmp_path / "expected" / "phage_ml" / "metadata" / "label_alignment.json")},
                ],
            },
        },
    )

    finalization = TaskVerificationService().finalize_payload(
        node,
        {"status": "completed", "metadata": {"artifact_paths": [str(parent)]}},
        execution_status="completed",
    )

    metadata = finalization.payload["metadata"]
    assert finalization.final_status == "completed"
    assert metadata["verification_status"] == "passed"
    assert metadata["artifact_contract_satisfied_verification"] is True
    assert metadata["verification"]["failures"] == []


def test_task_verifier_does_not_override_unrelated_missing_deliverable(tmp_path):
    parent = tmp_path / "raw_files" / "task_19"
    _write_valid_ml_metrics(parent / "ml_traditional" / "validation_metrics.json")
    _write_valid_label_alignment(parent / "phage_ml" / "metadata" / "label_alignment.json")
    node = PlanNode(
        id=19,
        plan_id=93,
        name="Train traditional ML models and report",
        metadata={
            "artifact_contract": {
                "publishes": [
                    "ml_traditional.validation_metrics_json",
                    "phage_ml.label_alignment_json",
                ]
            },
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [{"type": "file_exists", "path": str(tmp_path / "final_report.pdf")}],
            },
        },
    )

    finalization = TaskVerificationService().finalize_payload(
        node,
        {"status": "completed", "metadata": {"artifact_paths": [str(parent)]}},
        execution_status="completed",
    )

    metadata = finalization.payload["metadata"]
    assert finalization.final_status == "failed"
    assert metadata["verification_status"] == "failed"
    assert metadata.get("artifact_contract_satisfied_verification") is None


def test_task_verifier_does_not_override_when_explicit_publish_missing(tmp_path):
    parent = tmp_path / "raw_files" / "task_19"
    _write_valid_ml_metrics(parent / "ml_traditional" / "validation_metrics.json")
    _write_valid_label_alignment(parent / "phage_ml" / "metadata" / "label_alignment.json")
    node = PlanNode(
        id=19,
        plan_id=93,
        name="Train traditional ML models",
        metadata={
            "artifact_contract": {
                "publishes": [
                    "ml_traditional.validation_metrics_json",
                    "ml_traditional.model_checkpoints_dir",
                    "phage_ml.label_alignment_json",
                ]
            },
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [{"type": "file_exists", "path": str(tmp_path / "expected" / "ml_traditional" / "validation_metrics.json")}],
            },
        },
    )

    finalization = TaskVerificationService().finalize_payload(
        node,
        {"status": "completed", "metadata": {"artifact_paths": [str(parent)]}},
        execution_status="completed",
    )

    metadata = finalization.payload["metadata"]
    assert finalization.final_status == "failed"
    assert metadata["verification_status"] == "failed"
    assert metadata.get("artifact_contract_satisfied_verification") is None


def test_task_verifier_skips_auto_verification_without_explicit_criteria(tmp_path):
    artifact = tmp_path / "report.txt"
    artifact.write_text("hello\n", encoding="utf-8")
    node = PlanNode(id=1, plan_id=1, name="Generate report", metadata={})
    verifier = TaskVerificationService()

    finalization = verifier.finalize_payload(
        node,
        {
            "status": "completed",
            "content": "done",
            "metadata": {"artifact_paths": [str(artifact)]},
        },
        execution_status="completed",
    )

    assert finalization.final_status == "completed"
    assert finalization.verification is not None
    assert finalization.verification["status"] == "skipped"
    assert finalization.verification["generated"] is False
    assert finalization.verification["checks_total"] == 0


def test_task_verifier_fails_blocking_acceptance_criteria(tmp_path):
    missing = tmp_path / "missing.txt"
    node = PlanNode(
        id=2,
        plan_id=1,
        name="Download data",
        metadata={
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [{"type": "file_exists", "path": str(missing)}],
            }
        },
    )
    verifier = TaskVerificationService()

    finalization = verifier.finalize_payload(
        node,
        {"status": "completed", "content": "download attempted", "metadata": {}},
        execution_status="completed",
    )

    assert finalization.final_status == "failed"
    assert finalization.verification is not None
    assert finalization.verification["status"] == "failed"
    assert finalization.verification["failures"][0]["type"] == "file_exists"


def test_task_verifier_records_contract_diff_for_mismatch(tmp_path):
    actual = tmp_path / "results" / "NK_cell_upregulated_genes.csv"
    actual.parent.mkdir(parents=True, exist_ok=True)
    actual.write_text("gene_symbol,logFC\nA,1.0\n", encoding="utf-8")
    node = PlanNode(
        id=34,
        plan_id=68,
        name="差异基因提取与分类",
        metadata={
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [
                    {
                        "type": "file_exists",
                        "path": "results/enrichment/upregulated_genes.csv",
                    }
                ],
            }
        },
    )
    verifier = TaskVerificationService()

    finalization = verifier.finalize_payload(
        node,
        {
            "status": "completed",
            "content": "done",
            "metadata": {
                "artifact_paths": [str(actual)],
                "run_directory": str(tmp_path),
            },
        },
        execution_status="completed",
    )

    metadata = finalization.payload["metadata"]
    assert finalization.final_status == "failed"
    assert metadata["execution_status"] == "completed"
    assert metadata["verification_status"] == "failed"
    assert metadata["failure_kind"] == "contract_mismatch"
    assert metadata["contract_diff"]["missing_required_outputs"] == [
        "results/enrichment/upregulated_genes.csv"
    ]
    assert "results/NK_cell_upregulated_genes.csv" in metadata["contract_diff"]["unexpected_outputs"]
    assert metadata["plan_patch_suggestion"]


def test_task_verifier_accepts_source_discovery_with_actual_source_dir_and_format_alternative(tmp_path):
    source_dir = tmp_path / "results" / "05_CellChat"
    source_dir.mkdir(parents=True)
    for name in [
        "network_circle_OC.png",
        "bubble_all_OC.png",
        "compare_interactions_barplot.png",
        "compare_info_flow.png",
        "chord_TGFb_OC.png",
    ]:
        (source_dir / name).write_bytes(b"png-data")
    signaling_pdf = source_dir / "signaling_role_OC.pdf"
    signaling_pdf.write_bytes(b"%PDF-1.4\n")

    stale_dir = tmp_path / "cellchat"
    expected = [
        stale_dir / "network_circle_OC.png",
        stale_dir / "bubble_all_OC.png",
        stale_dir / "compare_interactions_barplot.png",
        stale_dir / "compare_info_flow.png",
        stale_dir / "chord_TGFb_OC.png",
        stale_dir / "signaling_role_OC.png",
    ]
    node = PlanNode(
        id=28,
        plan_id=95,
        name="Locate existing CellChat plot files",
        instruction="Search the expected input directories for existing CellChat plot files before copying them.",
        metadata={
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [{"type": "file_exists", "path": str(path)} for path in expected],
            }
        },
    )
    verifier = TaskVerificationService()

    finalization = verifier.finalize_payload(
        node,
        {
            "status": "completed",
            "content": (
                "All CellChat files found in results/05_CellChat.\n"
                "network_circle_OC.png FOUND\n"
                "bubble_all_OC.png FOUND\n"
                "compare_interactions_barplot.png FOUND\n"
                "compare_info_flow.png FOUND\n"
                "chord_TGFb_OC.png FOUND\n"
                "signaling_role_OC FOUND as signaling_role_OC.pdf, not PNG\n"
            ),
            "metadata": {
                "artifact_paths": [str(path) for path in source_dir.iterdir()],
            },
        },
        execution_status="completed",
    )

    metadata = finalization.payload["metadata"]
    assert finalization.final_status == "completed"
    assert finalization.verification is not None
    assert finalization.verification["status"] == "passed"
    assert metadata["verification_overridden_by_source_discovery"] is True
    assert metadata["source_discovery_verification"]["status"] == "passed"
    assert metadata["source_discovery_verification"]["format_alternatives"] == [
        {"expected": "signaling_role_OC.png", "actual": str(signaling_pdf)}
    ]
    assert "failure_kind" not in metadata


def test_task_verifier_does_not_apply_source_discovery_override_to_copy_tasks(tmp_path):
    source_dir = tmp_path / "results" / "05_CellChat"
    source_dir.mkdir(parents=True)
    for name in [
        "network_circle_OC.png",
        "bubble_all_OC.png",
        "compare_interactions_barplot.png",
        "compare_info_flow.png",
        "chord_TGFb_OC.png",
    ]:
        (source_dir / name).write_bytes(b"png-data")
    (source_dir / "signaling_role_OC.pdf").write_bytes(b"%PDF-1.4\n")

    figures_dir = tmp_path / "cellchat" / "figures"
    expected = [
        figures_dir / "network_circle_OC.png",
        figures_dir / "bubble_all_OC.png",
        figures_dir / "compare_interactions_barplot.png",
        figures_dir / "compare_info_flow.png",
        figures_dir / "chord_TGFb_OC.png",
        figures_dir / "signaling_role_OC.png",
    ]
    node = PlanNode(
        id=30,
        plan_id=95,
        name="Copy CellChat plots to output figures directory",
        instruction="Copy the verified CellChat plot files from their source location to the figures output directory.",
        metadata={
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [{"type": "file_exists", "path": str(path)} for path in expected],
            }
        },
    )
    verifier = TaskVerificationService()

    finalization = verifier.finalize_payload(
        node,
        {
            "status": "completed",
            "content": "Source files were available, but target copy output was not materialized.",
            "metadata": {"artifact_paths": [str(path) for path in source_dir.iterdir()]},
        },
        execution_status="completed",
    )

    assert finalization.final_status == "failed"
    assert finalization.payload["metadata"]["failure_kind"] == "contract_mismatch"
    assert "source_discovery_verification" not in finalization.payload["metadata"]


def test_task_verifier_keeps_source_discovery_failed_when_expected_identity_missing(tmp_path):
    source_dir = tmp_path / "results" / "05_CellChat"
    source_dir.mkdir(parents=True)
    (source_dir / "network_circle_OC.png").write_bytes(b"png-data")

    stale_dir = tmp_path / "cellchat"
    node = PlanNode(
        id=28,
        plan_id=95,
        name="Locate existing CellChat plot files",
        instruction="Search existing source directories for CellChat plot files.",
        metadata={
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [
                    {"type": "file_exists", "path": str(stale_dir / "network_circle_OC.png")},
                    {"type": "file_exists", "path": str(stale_dir / "bubble_all_OC.png")},
                ],
            }
        },
    )
    verifier = TaskVerificationService()

    finalization = verifier.finalize_payload(
        node,
        {
            "status": "completed",
            "content": "network_circle_OC.png FOUND\nbubble_all_OC.png missing",
            "metadata": {"artifact_paths": [str(source_dir / "network_circle_OC.png")]},
        },
        execution_status="completed",
    )

    assert finalization.final_status == "failed"
    assert finalization.payload["metadata"]["failure_kind"] == "contract_mismatch"


def test_task_verifier_accepts_semantic_general_evidence_alias_match(tmp_path):
    actual = tmp_path / "task_9" / "ncAA_abstract_evidence_summary.md"
    actual.parent.mkdir(parents=True, exist_ok=True)
    actual.write_text("# evidence\n", encoding="utf-8")
    node = PlanNode(
        id=9,
        plan_id=72,
        name="Gather Key Evidence for Abstract",
        metadata={
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [
                    {"type": "file_exists", "path": "general.evidence_md"},
                    {"type": "file_nonempty", "path": "general.evidence_md"},
                ],
            }
        },
    )
    verifier = TaskVerificationService()

    finalization = verifier.finalize_payload(
        node,
        {
            "status": "completed",
            "content": "summary generated",
            "metadata": {"artifact_paths": [str(actual)]},
        },
        execution_status="completed",
    )

    assert finalization.final_status == "completed"
    assert finalization.verification is not None
    assert finalization.verification["status"] == "passed"


def test_task_verifier_materializes_semantic_introduction_evidence_output(tmp_path):
    task_dir = tmp_path / "task_11"
    actual = task_dir / "historical_evidence_summary.md"
    actual.parent.mkdir(parents=True, exist_ok=True)
    actual.write_text("# introduction evidence\n", encoding="utf-8")
    node = PlanNode(
        id=11,
        plan_id=72,
        name="Gather Historical Background Evidence",
        metadata={
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [
                    {"type": "file_exists", "path": "introduction_evidence.md"},
                    {"type": "file_nonempty", "path": "introduction_evidence.md"},
                ],
            }
        },
    )
    verifier = TaskVerificationService()

    finalization = verifier.finalize_payload(
        node,
        {
            "status": "completed",
            "content": "historical summary generated",
            "metadata": {
                "artifact_paths": [str(actual)],
                "task_directory_full": str(task_dir),
            },
        },
        execution_status="completed",
    )

    expected = task_dir / "introduction_evidence.md"
    assert finalization.final_status == "completed"
    assert finalization.verification is not None
    assert finalization.verification["status"] == "passed"
    assert expected.exists()
    assert expected.read_text(encoding="utf-8") == actual.read_text(encoding="utf-8")


def test_task_verifier_materializes_nested_current_research_evidence_output(tmp_path):
    task_dir = tmp_path / "task_15"
    actual = task_dir / "task_15_current_research_directions_evidence.md"
    actual.parent.mkdir(parents=True, exist_ok=True)
    actual.write_text("# current research evidence\n", encoding="utf-8")
    node = PlanNode(
        id=15,
        plan_id=72,
        name="Gather Current Research Directions Evidence",
        metadata={
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [
                    {"type": "file_exists", "path": "evidence/current_research_evidence.md"},
                    {"type": "file_nonempty", "path": "evidence/current_research_evidence.md"},
                ],
            }
        },
    )
    verifier = TaskVerificationService()

    finalization = verifier.finalize_payload(
        node,
        {
            "status": "completed",
            "content": "research directions summary generated",
            "metadata": {
                "artifact_paths": [str(actual)],
                "task_directory_full": str(task_dir),
            },
        },
        execution_status="completed",
    )

    expected = task_dir / "evidence" / "current_research_evidence.md"
    assert finalization.final_status == "completed"
    assert finalization.verification is not None
    assert finalization.verification["status"] == "passed"
    assert expected.exists()
    assert expected.read_text(encoding="utf-8") == actual.read_text(encoding="utf-8")


def test_task_verifier_materializes_nested_evidence_dir_output_without_evidence_token(tmp_path):
    task_dir = tmp_path / "task_18"
    actual = task_dir / "foundational_papers_ncAA_history.md"
    actual.parent.mkdir(parents=True, exist_ok=True)
    actual.write_text("# foundational ncAA history\n", encoding="utf-8")
    node = PlanNode(
        id=18,
        plan_id=74,
        name="Collect Foundational ncAA History Evidence",
        metadata={
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [
                    {"type": "file_exists", "path": "evidence/foundational_ncAA_history.md"},
                    {"type": "file_nonempty", "path": "evidence/foundational_ncAA_history.md"},
                ],
            }
        },
    )
    verifier = TaskVerificationService()

    finalization = verifier.finalize_payload(
        node,
        {
            "status": "completed",
            "content": "foundational history generated",
            "metadata": {
                "artifact_paths": [str(actual)],
                "task_directory_full": str(task_dir),
            },
        },
        execution_status="completed",
    )

    expected = task_dir / "evidence" / "foundational_ncAA_history.md"
    assert finalization.final_status == "completed"
    assert finalization.verification is not None
    assert finalization.verification["status"] == "passed"
    assert expected.exists()
    assert expected.read_text(encoding="utf-8") == actual.read_text(encoding="utf-8")


def test_task_verifier_prefers_best_semantic_evidence_candidate(tmp_path):
    task_dir = tmp_path / "task_19"
    less_specific = task_dir / "challenges_limitations_evidence_summary.md"
    best = task_dir / "challenges_evidence_summary.md"
    upstream = tmp_path / "task_9" / "general_evidence_summary.md"
    task_dir.mkdir(parents=True, exist_ok=True)
    upstream.parent.mkdir(parents=True, exist_ok=True)
    less_specific.write_text("# limitations\n", encoding="utf-8")
    best.write_text("# selected challenge evidence\n", encoding="utf-8")
    upstream.write_text("# upstream evidence\n", encoding="utf-8")
    node = PlanNode(
        id=19,
        plan_id=72,
        name="Gather Challenges Evidence",
        metadata={
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [
                    {"type": "file_exists", "path": "challenges_evidence.md"},
                    {"type": "file_nonempty", "path": "challenges_evidence.md"},
                ],
            }
        },
    )
    verifier = TaskVerificationService()

    finalization = verifier.finalize_payload(
        node,
        {
            "status": "completed",
            "content": "challenges summary generated",
            "metadata": {
                "artifact_paths": [str(upstream), str(less_specific), str(best)],
                "task_directory_full": str(task_dir),
            },
        },
        execution_status="completed",
    )

    expected = task_dir / "challenges_evidence.md"
    assert finalization.final_status == "completed"
    assert finalization.verification is not None
    assert finalization.verification["status"] == "passed"
    assert expected.exists()
    assert expected.read_text(encoding="utf-8") == best.read_text(encoding="utf-8")


def test_task_verifier_materializes_summary_without_evidence_token_for_conclusion(tmp_path):
    task_dir = tmp_path / "task_22"
    actual = task_dir / "key_advances_and_future_outlook_summary.md"
    actual.parent.mkdir(parents=True, exist_ok=True)
    actual.write_text("# conclusion evidence\n", encoding="utf-8")
    node = PlanNode(
        id=22,
        plan_id=72,
        name="Gather Conclusion Evidence",
        metadata={
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [
                    {"type": "file_exists", "path": "conclusion_evidence.md"},
                    {"type": "file_nonempty", "path": "conclusion_evidence.md"},
                ],
            }
        },
    )
    verifier = TaskVerificationService()

    finalization = verifier.finalize_payload(
        node,
        {
            "status": "completed",
            "content": "future outlook summary generated",
            "metadata": {
                "artifact_paths": [str(actual)],
                "task_directory_full": str(task_dir),
            },
        },
        execution_status="completed",
    )

    expected = task_dir / "conclusion_evidence.md"
    assert finalization.final_status == "completed"
    assert finalization.verification is not None
    assert finalization.verification["status"] == "passed"
    assert expected.exists()
    assert expected.read_text(encoding="utf-8") == actual.read_text(encoding="utf-8")


def test_task_verifier_derives_acceptance_criteria_from_instruction(tmp_path):
    actual = tmp_path / "results" / "terminal_code_stats.csv"
    actual.parent.mkdir(parents=True, exist_ok=True)
    actual.write_text("file_name,total_lines\nx,1\n", encoding="utf-8")
    node = PlanNode(
        id=35,
        plan_id=68,
        name="子集定义与质量控制",
        instruction=(
            "Generate the final subset manifest (`subset_manifest.tsv`) and save the summary report to "
            "`results/qc_summary.md`."
        ),
    )
    verifier = TaskVerificationService()

    finalization = verifier.finalize_payload(
        node,
        {
            "status": "completed",
            "content": "done",
            "metadata": {
                "artifact_paths": [str(actual)],
                "run_directory": str(tmp_path),
            },
        },
        execution_status="completed",
    )

    metadata = finalization.payload["metadata"]
    assert finalization.final_status == "failed"
    assert metadata["verification_status"] == "failed"
    assert metadata["artifact_verification"]["tags"] == ["contract_mismatch"]
    assert metadata["contract_diff"]["missing_required_outputs"] == [
        "subset_manifest.tsv",
        "results/qc_summary.md",
    ]


def test_extract_explicit_deliverables_ignores_input_paths_and_tool_scripts() -> None:
    text = (
        "Use the existing pipeline.py script to process samples, read results/subset_manifest.tsv, "
        "and save the final summary to results/qc_summary.csv."
    )

    assert extract_explicit_deliverables_from_text(text) == ["results/qc_summary.csv"]


def test_extract_explicit_deliverables_keeps_multiline_output_sections() -> None:
    text = (
        "Save the following output files to disk:\n"
        "phase1_data.h5ad\n"
        "phase2_data.h5ad\n"
        "final_report.pdf\n"
    )

    assert extract_explicit_deliverables_from_text(text) == [
        "phase1_data.h5ad",
        "phase2_data.h5ad",
        "final_report.pdf",
    ]


def test_task_verifier_marks_cross_extension_outputs_as_wrong_format(tmp_path):
    actual = tmp_path / "results" / "subset_manifest.tsv"
    actual.parent.mkdir(parents=True, exist_ok=True)
    actual.write_text("id\nphage_1\n", encoding="utf-8")
    node = PlanNode(
        id=36,
        plan_id=68,
        name="导出基因清单",
        metadata={
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [
                    {
                        "type": "file_nonempty",
                        "path": "results/subset_manifest.csv",
                    }
                ],
            }
        },
    )
    verifier = TaskVerificationService()

    finalization = verifier.finalize_payload(
        node,
        {
            "status": "completed",
            "content": "done",
            "metadata": {
                "artifact_paths": [str(actual)],
                "run_directory": str(tmp_path),
            },
        },
        execution_status="completed",
    )

    metadata = finalization.payload["metadata"]
    assert finalization.final_status == "failed"
    assert metadata["contract_diff"]["missing_required_outputs"] == [
        "results/subset_manifest.csv"
    ]
    assert metadata["contract_diff"]["wrong_format_outputs"] == [
        "results/subset_manifest.tsv"
    ]


def test_task_verifier_accepts_runtime_artifact_directory_for_stale_absolute_contract(tmp_path):
    runtime_task_dir = tmp_path / "runtime" / "session_test" / "raw_files" / "task_1" / "task_3" / "task_13"
    runtime_task_dir.mkdir(parents=True, exist_ok=True)
    artifact = runtime_task_dir / "ovarian_cancer_annotated.h5ad"
    artifact.write_bytes(b"h5ad payload")
    stale_external_path = tmp_path / "GAgent_backup_20260421_233939" / "data" / artifact.name

    node = PlanNode(
        id=13,
        plan_id=99,
        name="Identify cluster-specific marker genes using scanpy rank_genes_groups",
        metadata={
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [
                    {"type": "file_exists", "path": str(stale_external_path)},
                    {"type": "file_nonempty", "path": str(stale_external_path)},
                ],
            }
        },
    )

    finalization = TaskVerificationService().finalize_payload(
        node,
        {
            "status": "completed",
            "content": "rank_genes_groups completed; authoritative outputs are in the task runtime directory",
            "metadata": {"artifact_paths": [str(runtime_task_dir)]},
        },
        execution_status="completed",
    )

    metadata = finalization.payload["metadata"]
    assert finalization.final_status == "completed"
    assert metadata["verification_status"] == "passed"
    assert str(runtime_task_dir) in metadata["artifact_paths"]
    assert "failure_kind" not in metadata


def test_task_verifier_resolves_session_relative_task_artifacts_for_stale_contract(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runtime_task_dir = tmp_path / "runtime" / "session_test" / "raw_files" / "task_1" / "task_3" / "task_15"
    runtime_task_dir.mkdir(parents=True, exist_ok=True)
    validation_pdf = runtime_task_dir / "cell_type_annotation_validation.pdf"
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})
    })
    stream = DecodedStreamObject()
    stream.set_data(
        (
            "BT /F1 12 Tf 50 740 Td ("
            + "Cell type annotation validation dotplot violin marker expression " * 8
            + ") Tj ET"
        ).encode("latin-1")
    )
    page[NameObject("/Contents")] = writer._add_object(stream)
    with validation_pdf.open("wb") as handle:
        writer.write(handle)
    annotated_h5ad = runtime_task_dir / "ovarian_cancer_annotated.h5ad"
    annotated_h5ad.write_bytes(b"h5ad payload")
    stale_backup = tmp_path / "GAgent_backup_20260421_233939"

    node = PlanNode(
        id=15,
        plan_id=99,
        name="Validate and refine annotations using dotplot and violin plots",
        metadata={
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [
                    {"type": "file_exists", "path": str(stale_backup / "figures" / validation_pdf.name)},
                    {"type": "pdf_valid", "path": str(stale_backup / "figures" / validation_pdf.name)},
                    {"type": "file_nonempty", "path": str(stale_backup / "data" / annotated_h5ad.name)},
                ],
            }
        },
    )

    finalization = TaskVerificationService().finalize_payload(
        node,
        {
            "status": "completed",
            "content": "validation outputs are in raw_files task directory",
            "metadata": {
                "artifact_paths": [
                    "/task_1/task_3/task_15/cell_type_annotation_validation.pdf",
                    "/task_1/task_3/task_15/ovarian_cancer_annotated.h5ad",
                    "/task_1/task_3/task_15/",
                ]
            },
        },
        execution_status="completed",
    )

    metadata = finalization.payload["metadata"]
    assert finalization.final_status == "completed"
    assert metadata["verification_status"] == "passed"
    assert metadata["verification"]["checks_passed"] == 3
    assert str(runtime_task_dir) in metadata["artifact_paths"]
    assert str(validation_pdf) in metadata["artifact_paths"]
    assert str(annotated_h5ad) in metadata["artifact_paths"]
    assert "failure_kind" not in metadata


def test_task_verifier_pdb_residue_present_avoids_text_false_positive(tmp_path):
    valid = tmp_path / "valid_sec.pdb"
    valid.write_text(
        "HEADER TEST\n"
        "HET    SEC  A 502       6\n"
        "HETNAM     SEC SELENOCYSTEINE\n"
        "HETATM 3122  N   SEC A 502      28.159  49.623   5.119  1.00 41.73           N\n",
        encoding="utf-8",
    )
    false_positive = tmp_path / "false_sec.pdb"
    false_positive.write_text(
        "HEADER TEST\n"
        "COMPND   2 MOLECULE: PREPROTEIN TRANSLOCASE SECY SUBUNIT;\n",
        encoding="utf-8",
    )

    verifier = TaskVerificationService()
    node = PlanNode(id=3, plan_id=1, name="Validate structures", metadata={})

    passed = verifier.finalize_payload(
        node,
        {
            "status": "completed",
            "content": "ok",
            "metadata": {},
        },
        execution_status="completed",
    )
    assert passed.final_status == "completed"

    check_node = PlanNode(
        id=4,
        plan_id=1,
        name="Validate SEC",
        metadata={
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [{"type": "pdb_residue_present", "path": str(valid), "residue": "SEC"}],
            }
        },
    )
    assert verifier.finalize_payload(
        check_node,
        {"status": "completed", "content": "ok", "metadata": {}},
        execution_status="completed",
    ).final_status == "completed"

    check_node.metadata["acceptance_criteria"]["checks"][0]["path"] = str(false_positive)
    failed = verifier.finalize_payload(
        check_node,
        {"status": "completed", "content": "ok", "metadata": {}},
        execution_status="completed",
    )
    assert failed.final_status == "failed"
    assert failed.verification is not None
    assert failed.verification["failures"][0]["type"] == "pdb_residue_present"


def test_derived_criteria_reject_header_only_tsv(tmp_path, monkeypatch):
    monkeypatch.setattr(
        TaskVerificationService,
        "_llm_arbitrate_verification",
        lambda *args, **kwargs: True,
    )
    table = tmp_path / "curated_metadata.tsv"
    table.write_text("Phage_ID\tHost_label\n", encoding="utf-8")
    node = PlanNode(
        id=45,
        plan_id=9,
        name="Prepare metadata",
        instruction="Generate and save curated_metadata.tsv",
        metadata={},
    )
    verifier = TaskVerificationService()

    finalization = verifier.finalize_payload(
        node,
        {"status": "completed", "metadata": {"artifact_paths": [str(table)], "run_directory": str(tmp_path)}},
        execution_status="completed",
    )

    assert finalization.final_status == "failed"
    failures = finalization.payload["metadata"]["verification"]["failures"]
    assert any(item["type"] == "json_field_at_least" and item.get("actual") == 0 for item in failures)
    assert finalization.payload["metadata"].get("verification_overridden_by_llm") is None


def test_explicit_weak_criteria_are_strengthened_for_header_only_tsv(tmp_path):
    table = tmp_path / "results" / "smoke_metadata.tsv"
    table.parent.mkdir(parents=True, exist_ok=True)
    table.write_text("Phage_ID\tHost_label\n", encoding="utf-8")
    node = PlanNode(
        id=49,
        plan_id=9,
        name="Create empty TSV file with header",
        instruction="Create results/smoke_metadata.tsv with only the header line and no data rows.",
        metadata={
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [
                    {"type": "file_exists", "path": "results/smoke_metadata.tsv"},
                    {"type": "file_nonempty", "path": "results/smoke_metadata.tsv"},
                ],
            }
        },
    )
    verifier = TaskVerificationService()

    finalization = verifier.finalize_payload(
        node,
        {"status": "completed", "metadata": {"artifact_paths": [str(table)], "run_directory": str(tmp_path)}},
        execution_status="completed",
    )

    assert finalization.final_status == "failed"
    failures = finalization.payload["metadata"]["verification"]["failures"]
    assert any(item["type"] == "json_field_at_least" and item.get("actual") == 0 for item in failures)


def test_derived_criteria_reject_audit_json_without_metadata_rows(tmp_path):
    audit = tmp_path / "data_audit.json"
    audit.write_text(json.dumps({"success": True, "metadata_files": 14}), encoding="utf-8")
    node = PlanNode(
        id=46,
        plan_id=9,
        name="Audit data",
        instruction="Write data_audit.json with audit results",
        metadata={},
    )
    verifier = TaskVerificationService()

    finalization = verifier.finalize_payload(
        node,
        {"status": "completed", "metadata": {"artifact_paths": [str(audit)], "run_directory": str(tmp_path)}},
        execution_status="completed",
    )

    assert finalization.final_status == "failed"
    assert any(item["type"] == "json_field_at_least" for item in finalization.payload["metadata"]["verification"]["failures"])


def test_derived_criteria_reject_metrics_json_without_tree_metrics(tmp_path):
    metrics = tmp_path / "model_metrics.json"
    metrics.write_text(json.dumps({"status": "completed"}), encoding="utf-8")
    node = PlanNode(
        id=47,
        plan_id=9,
        name="Train models",
        instruction="Save model_metrics.json after RandomForest and ExtraTrees evaluation",
        metadata={},
    )
    verifier = TaskVerificationService()

    finalization = verifier.finalize_payload(
        node,
        {"status": "completed", "metadata": {"artifact_paths": [str(metrics)], "run_directory": str(tmp_path)}},
        execution_status="completed",
    )

    assert finalization.final_status == "failed"
    assert finalization.payload["metadata"]["verification"]["failures"][0]["type"] == "model_metrics_valid"

    metrics.write_text(
        json.dumps({"models": {"ExtraTrees": {"accuracy": 0.2, "macro_f1": 0.1}}}),
        encoding="utf-8",
    )
    passed = verifier.finalize_payload(
        node,
        {"status": "completed", "metadata": {"artifact_paths": [str(metrics)], "run_directory": str(tmp_path)}},
        execution_status="completed",
    )
    assert passed.final_status == "completed"

    metrics.write_text(
        json.dumps(
            {
                "models": {
                    "RandomForest": {
                        "train": {"accuracy": 0.9, "macro_f1": 0.8},
                        "test": {"accuracy": 0.4, "macro_f1": 0.15, "weighted_f1": 0.37},
                    },
                    "ExtraTrees": {
                        "validation": {"accuracy": 0.35, "macro_f1": 0.11},
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    nested = verifier.finalize_payload(
        node,
        {"status": "completed", "metadata": {"artifact_paths": [str(metrics)], "run_directory": str(tmp_path)}},
        execution_status="completed",
    )
    assert nested.final_status == "completed"


def test_derived_criteria_reject_fake_pdf(tmp_path):
    pdf = tmp_path / "phagescope_research_topic1_production_report.pdf"
    pdf.write_text("not a real pdf", encoding="utf-8")
    node = PlanNode(
        id=48,
        plan_id=9,
        name="Write PDF",
        instruction="Generate phagescope_research_topic1_production_report.pdf",
        metadata={},
    )
    verifier = TaskVerificationService()

    finalization = verifier.finalize_payload(
        node,
        {"status": "completed", "metadata": {"artifact_paths": [str(pdf)], "run_directory": str(tmp_path)}},
        execution_status="completed",
    )

    assert finalization.final_status == "failed"
    failures = finalization.payload["metadata"]["verification"]["failures"]
    assert any(item["type"] == "pdf_valid" for item in failures)


def test_plan_executor_verifies_artifact_path_mentioned_in_final_answer(tmp_path, monkeypatch):
    output_path = tmp_path / "raw_files" / "task_1" / "task_2" / "data_audit.json"
    output_path.parent.mkdir(parents=True)
    output_path.write_text(json.dumps({"metadata": {"metadata_rows": 5}, "metadata_rows": 5}), encoding="utf-8")
    tree = PlanTree(id=1, title="PhageScope verification plan")
    tree.nodes = {
        2: PlanNode(
            id=2,
            plan_id=1,
            name="Audit PhageScope dataset",
            status="pending",
            instruction="Run phagescope_research action=audit and save data_audit.json.",
            metadata={
                "acceptance_criteria": {
                    "category": "file_data",
                    "blocking": True,
                    "checks": [
                        {"type": "file_nonempty", "path": "data_audit.json"},
                        {"type": "json_field_at_least", "path": "data_audit.json", "key_path": "metadata_rows", "min_value": 1, "hard": True},
                    ],
                }
            },
        ),
    }
    tree.rebuild_adjacency()
    repo = _RepoStub(tree)
    response = ExecutionResponse(
        status="success",
        content=f"Audit completed. Output file: `{output_path}`",
    )
    executor = PlanExecutor(repo=repo, llm_service=_LLMStub(response))
    monkeypatch.setattr(executor, "_should_use_deep_think", lambda _cfg: False)

    result = executor.execute_task(1, 2, config=ExecutionConfig(enable_skills=False))

    assert result.status == "completed"
    assert str(output_path) in result.metadata["artifact_paths"]
    assert result.metadata["verification_status"] == "passed"


def test_plan_executor_marks_task_failed_when_verification_fails(tmp_path, monkeypatch):
    output_path = tmp_path / "report.json"
    tree = PlanTree(id=1, title="Verification Plan")
    tree.nodes = {
        1: PlanNode(
            id=1,
            plan_id=1,
            name="Write report",
            status="pending",
            instruction="Generate the report.",
            metadata={
                "acceptance_criteria": {
                    "category": "file_data",
                    "blocking": True,
                    "checks": [{"type": "file_exists", "path": str(output_path)}],
                }
            },
        ),
    }
    repo = _RepoStub(tree)
    response = ExecutionResponse(
        status="success",
        content="report done",
        notes=[],
        metadata={"output_path": str(output_path)},
    )
    executor = PlanExecutor(repo=repo, llm_service=_LLMStub(response))
    # Bypass deep-think path which requires a full LLM client.
    monkeypatch.setattr(executor, "_should_use_deep_think", lambda _cfg: False)

    result = executor.execute_task(1, 1, config=ExecutionConfig(enable_skills=False))

    assert result.status == "failed"
    assert repo.update_calls[-1][2]["status"] == "failed"
    payload = json.loads(repo.update_calls[-1][2]["execution_result"])
    assert payload["metadata"]["verification"]["status"] == "failed"


def test_verify_task_route_rechecks_existing_output(tmp_path, monkeypatch):
    output_path = tmp_path / "report.json"
    output_path.write_text(json.dumps({"ok": True}), encoding="utf-8")
    payload = {
        "status": "failed",
        "content": "report done",
        "metadata": {
            "execution_status": "completed",
            "artifact_paths": [str(output_path)],
            "verification": {
                "status": "failed",
                "checks_total": 1,
                "checks_passed": 0,
                "failures": [{"type": "file_exists"}],
                "evidence": {"artifact_paths": [str(output_path)]},
            },
        },
    }
    tree = PlanTree(id=55, title="Verify Route Plan")
    tree.nodes = {
        22: PlanNode(
            id=22,
            plan_id=55,
            name="Collect data",
            status="failed",
            instruction="Download the dataset.",
            metadata={
                "acceptance_criteria": {
                    "category": "file_data",
                    "blocking": True,
                    "checks": [{"type": "file_exists", "path": str(output_path)}],
                }
            },
            execution_result=json.dumps(payload, ensure_ascii=False),
        )
    }
    repo = _RepoStub(tree)
    monkeypatch.setattr(plan_routes, "_plan_repo", repo)
    monkeypatch.setattr(plan_routes, "_load_authorized_plan_tree", lambda plan_id, request: tree)

    response = plan_routes.verify_task_result(22, request=None, plan_id=55)

    assert response.success is True
    assert response.result.status == "completed"
    assert repo.update_calls[-1][2]["status"] == "completed"


def test_verify_task_route_fails_when_artifact_authority_demotes_result(tmp_path, monkeypatch):
    output_path = tmp_path / "report.json"
    output_path.write_text(json.dumps({"ok": True}), encoding="utf-8")
    payload = {
        "status": "completed",
        "content": "report done",
        "metadata": {
            "execution_status": "completed",
            "artifact_paths": [str(output_path)],
        },
    }
    tree = PlanTree(id=56, title="Verify Route Plan")
    tree.nodes = {
        23: PlanNode(
            id=23,
            plan_id=56,
            name="Collect evidence",
            status="completed",
            instruction="Download the dataset.",
            metadata={
                "artifact_contract": {"publishes": ["general.evidence_md"]},
                "acceptance_criteria": {
                    "category": "file_data",
                    "blocking": True,
                    "checks": [{"type": "file_exists", "path": str(output_path)}],
                },
            },
            execution_result=json.dumps(payload, ensure_ascii=False),
        )
    }
    repo = _RepoStub(tree)
    monkeypatch.setattr(plan_routes, "_plan_repo", repo)
    monkeypatch.setattr(plan_routes, "_load_authorized_plan_tree", lambda plan_id, request: tree)

    response = plan_routes.verify_task_result(23, request=None, plan_id=56)

    assert response.success is False
    assert response.result.status == "failed"
    assert "artifact authority failed" in response.message.lower()
    assert repo.update_calls[-1][2]["status"] == "failed"


def test_parse_shorthand_criteria_basic():
    verifier = TaskVerificationService()

    result = verifier.parse_shorthand_criteria([
        "file_exists:pdb_files/*.pdb",
        "glob_count_at_least:pdb_files/*.pdb:38",
        "file_nonempty:pdb_files/1KMK_SEC.pdb",
    ])

    assert result["blocking"] is True
    checks = result["checks"]
    assert len(checks) == 3
    assert checks[0] == {"type": "file_exists", "path": "pdb_files/*.pdb"}
    assert checks[1] == {"type": "glob_count_at_least", "glob": "pdb_files/*.pdb", "min_count": 38}
    assert checks[2] == {"type": "file_nonempty", "path": "pdb_files/1KMK_SEC.pdb"}


def test_parse_shorthand_criteria_all_types():
    verifier = TaskVerificationService()

    result = verifier.parse_shorthand_criteria([
        "text_contains:report.txt:HETNAM",
        "json_field_equals:stats.json:statistics.total:38",
        "json_field_at_least:stats.json:statistics.valid_count:20",
        "pdb_residue_present:data/1KMK.pdb:SEC",
    ])

    checks = result["checks"]
    assert len(checks) == 4
    assert checks[0] == {"type": "text_contains", "path": "report.txt", "pattern": "HETNAM"}
    assert checks[1] == {"type": "json_field_equals", "path": "stats.json", "key_path": "statistics.total", "expected": "38"}
    assert checks[2] == {"type": "json_field_at_least", "path": "stats.json", "key_path": "statistics.valid_count", "min_value": 20.0}
    assert checks[3] == {"type": "pdb_residue_present", "path": "data/1KMK.pdb", "residue": "SEC"}


def test_parse_shorthand_criteria_empty_and_invalid():
    verifier = TaskVerificationService()

    result = verifier.parse_shorthand_criteria(["", "  ", "unknown_type:foo"])
    assert result["checks"] == []


def test_verify_task_with_override_criteria(tmp_path):
    """When override_criteria are passed, they should be used even if
    the node has no acceptance_criteria in metadata."""
    report = tmp_path / "report.txt"
    report.write_text("HETNAM     SEC SELENOCYSTEINE\n", encoding="utf-8")
    tree = PlanTree(id=10, title="Override Test")
    tree.nodes = {
        1: PlanNode(
            id=1,
            plan_id=10,
            name="Collect data",
            status="completed",
            metadata={},  # No acceptance_criteria!
            execution_result=json.dumps({
                "status": "completed",
                "content": "done",
                "metadata": {},
            }),
        )
    }
    repo = _RepoStub(tree)
    verifier = TaskVerificationService()

    override = {
        "category": "file_data",
        "blocking": True,
        "checks": [
            {"type": "file_exists", "path": str(report)},
            {"type": "text_contains", "path": str(report), "pattern": "HETNAM"},
        ],
    }
    finalization = verifier.verify_task(
        repo,
        plan_id=10,
        task_id=1,
        trigger="manual",
        override_criteria=override,
    )

    assert finalization.final_status == "completed"
    assert finalization.verification is not None
    assert finalization.verification["status"] == "passed"
    assert finalization.verification["checks_total"] == 2
    assert finalization.verification["checks_passed"] == 2


def test_verify_task_override_criteria_fail(tmp_path):
    """Override criteria that fail should mark the task as failed."""
    missing = tmp_path / "nonexistent.pdb"
    tree = PlanTree(id=11, title="Override Fail Test")
    tree.nodes = {
        1: PlanNode(
            id=1,
            plan_id=11,
            name="Collect PDBs",
            status="completed",
            metadata={},
            execution_result=json.dumps({
                "status": "completed",
                "content": "done",
                "metadata": {},
            }),
        )
    }
    repo = _RepoStub(tree)
    verifier = TaskVerificationService()

    override = {
        "category": "file_data",
        "blocking": True,
        "checks": [{"type": "file_exists", "path": str(missing)}],
    }
    finalization = verifier.verify_task(
        repo,
        plan_id=11,
        task_id=1,
        trigger="manual",
        override_criteria=override,
    )

    assert finalization.final_status == "failed"
    assert finalization.verification["status"] == "failed"
    assert len(finalization.verification["failures"]) == 1


def test_json_field_equals_type_coercion(tmp_path):
    """json_field_equals should match string '38' against int 38 in JSON."""
    stats = tmp_path / "stats.json"
    stats.write_text(json.dumps({"statistics": {"total": 38}}), encoding="utf-8")
    verifier = TaskVerificationService()
    node = PlanNode(
        id=1,
        plan_id=1,
        name="Check stats",
        metadata={
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [
                    {"type": "json_field_equals", "path": str(stats), "key_path": "statistics.total", "expected": "38"},
                ],
            }
        },
    )
    fin = verifier.finalize_payload(
        node,
        {"status": "completed", "content": "ok", "metadata": {}},
        execution_status="completed",
    )
    assert fin.final_status == "completed"
    assert fin.verification["status"] == "passed"


def test_non_blocking_verification_failure_still_completes(tmp_path):
    """When blocking=false, verification failure should NOT change final status to failed."""
    missing = tmp_path / "optional.txt"
    verifier = TaskVerificationService()
    node = PlanNode(
        id=1,
        plan_id=1,
        name="Optional check",
        metadata={
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": False,
                "checks": [{"type": "file_exists", "path": str(missing)}],
            }
        },
    )
    fin = verifier.finalize_payload(
        node,
        {"status": "completed", "content": "ok", "metadata": {}},
        execution_status="completed",
    )
    assert fin.final_status == "completed"
    assert fin.verification["status"] == "failed"
    assert fin.verification["blocking"] is False


def test_task_verifier_does_not_materialize_generic_notes_file_as_evidence(tmp_path):
    task_dir = tmp_path / "task_11"
    actual = task_dir / "notes.md"
    actual.parent.mkdir(parents=True, exist_ok=True)
    actual.write_text("# scratch notes\n", encoding="utf-8")
    node = PlanNode(
        id=11,
        plan_id=72,
        name="Gather Historical Background Evidence",
        metadata={
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [
                    {"type": "file_exists", "path": "introduction_evidence.md"},
                    {"type": "file_nonempty", "path": "introduction_evidence.md"},
                ],
            }
        },
    )
    verifier = TaskVerificationService()

    finalization = verifier.finalize_payload(
        node,
        {
            "status": "completed",
            "content": "notes captured",
            "metadata": {
                "artifact_paths": [str(actual)],
                "task_directory_full": str(task_dir),
            },
        },
        execution_status="completed",
    )

    expected = task_dir / "introduction_evidence.md"
    assert finalization.final_status == "failed"
    assert finalization.verification is not None
    assert finalization.verification["status"] == "failed"
    assert not expected.exists()


def test_task_verifier_does_not_materialize_generic_notes_summary_as_evidence(tmp_path):
    task_dir = tmp_path / "task_11"
    actual = task_dir / "notes_summary.md"
    actual.parent.mkdir(parents=True, exist_ok=True)
    actual.write_text("# scratch notes summary\n", encoding="utf-8")
    node = PlanNode(
        id=11,
        plan_id=72,
        name="Gather Historical Background Evidence",
        metadata={
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [
                    {"type": "file_exists", "path": "introduction_evidence.md"},
                    {"type": "file_nonempty", "path": "introduction_evidence.md"},
                ],
            }
        },
    )
    verifier = TaskVerificationService()

    finalization = verifier.finalize_payload(
        node,
        {
            "status": "completed",
            "content": "notes summarized",
            "metadata": {
                "artifact_paths": [str(actual)],
                "task_directory_full": str(task_dir),
            },
        },
        execution_status="completed",
    )

    expected = task_dir / "introduction_evidence.md"
    assert finalization.final_status == "failed"
    assert finalization.verification is not None
    assert finalization.verification["status"] == "failed"
    assert not expected.exists()


def test_task_verifier_does_not_materialize_unrelated_singleton_summary_as_evidence(tmp_path):
    task_dir = tmp_path / "task_22"
    actual = task_dir / "meeting_minutes_summary.md"
    actual.parent.mkdir(parents=True, exist_ok=True)
    actual.write_text("# meeting minutes\n", encoding="utf-8")
    node = PlanNode(
        id=22,
        plan_id=72,
        name="Gather Conclusion Evidence",
        metadata={
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [
                    {"type": "file_exists", "path": "conclusion_evidence.md"},
                    {"type": "file_nonempty", "path": "conclusion_evidence.md"},
                ],
            }
        },
    )
    verifier = TaskVerificationService()

    finalization = verifier.finalize_payload(
        node,
        {
            "status": "completed",
            "content": "meeting minutes summarized",
            "metadata": {
                "artifact_paths": [str(actual)],
                "task_directory_full": str(task_dir),
            },
        },
        execution_status="completed",
    )

    expected = task_dir / "conclusion_evidence.md"
    assert finalization.final_status == "failed"
    assert finalization.verification is not None
    assert finalization.verification["status"] == "failed"
    assert not expected.exists()


def test_task_verifier_does_not_materialize_semantic_target_outside_task_workspace(tmp_path):
    task_dir = tmp_path / "task_11"
    shared_target = tmp_path / "shared" / "introduction_evidence.md"
    actual = task_dir / "historical_evidence_summary.md"
    actual.parent.mkdir(parents=True, exist_ok=True)
    actual.write_text("# historical evidence\n", encoding="utf-8")
    node = PlanNode(
        id=11,
        plan_id=72,
        name="Gather Historical Background Evidence",
        metadata={
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [
                    {"type": "file_exists", "path": "../shared/introduction_evidence.md"},
                    {"type": "file_nonempty", "path": "../shared/introduction_evidence.md"},
                ],
            }
        },
    )
    verifier = TaskVerificationService()

    finalization = verifier.finalize_payload(
        node,
        {
            "status": "completed",
            "content": "historical evidence summarized",
            "metadata": {
                "artifact_paths": [str(actual)],
                "task_directory_full": str(task_dir),
            },
        },
        execution_status="completed",
    )

    assert finalization.final_status == "failed"
    assert finalization.verification is not None
    assert finalization.verification["status"] == "failed"
    assert not shared_target.exists()


def test_task_verifier_does_not_materialize_semantic_absolute_target(tmp_path):
    task_dir = tmp_path / "task_11"
    absolute_target = tmp_path / "shared_abs" / "introduction_evidence.md"
    actual = task_dir / "historical_evidence_summary.md"
    actual.parent.mkdir(parents=True, exist_ok=True)
    actual.write_text("# historical evidence\n", encoding="utf-8")
    node = PlanNode(
        id=11,
        plan_id=72,
        name="Gather Historical Background Evidence",
        metadata={
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [
                    {"type": "file_exists", "path": str(absolute_target)},
                    {"type": "file_nonempty", "path": str(absolute_target)},
                ],
            }
        },
    )
    verifier = TaskVerificationService()

    finalization = verifier.finalize_payload(
        node,
        {
            "status": "completed",
            "content": "historical evidence summarized",
            "metadata": {
                "artifact_paths": [str(actual)],
                "task_directory_full": str(task_dir),
            },
        },
        execution_status="completed",
    )

    assert finalization.final_status == "failed"
    assert finalization.verification is not None
    assert finalization.verification["status"] == "failed"
    assert not absolute_target.exists()


def test_task_verifier_materializes_topic_aligned_singleton_evidence_file(tmp_path):
    task_dir = tmp_path / "task_11"
    actual = task_dir / "historical_evidence.md"
    actual.parent.mkdir(parents=True, exist_ok=True)
    actual.write_text("# historical evidence\n", encoding="utf-8")
    node = PlanNode(
        id=11,
        plan_id=72,
        name="Gather Historical Background Evidence",
        metadata={
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [
                    {"type": "file_exists", "path": "introduction_evidence.md"},
                    {"type": "file_nonempty", "path": "introduction_evidence.md"},
                ],
            }
        },
    )
    verifier = TaskVerificationService()

    finalization = verifier.finalize_payload(
        node,
        {
            "status": "completed",
            "content": "historical evidence captured",
            "metadata": {
                "artifact_paths": [str(actual)],
                "task_directory_full": str(task_dir),
            },
        },
        execution_status="completed",
    )

    expected = task_dir / "introduction_evidence.md"
    assert finalization.final_status == "completed"
    assert finalization.verification is not None
    assert finalization.verification["status"] == "passed"
    assert expected.exists()
    assert expected.read_text(encoding="utf-8") == actual.read_text(encoding="utf-8")


def test_task_verifier_does_not_materialize_unrelated_singleton_evidence_file(tmp_path):
    task_dir = tmp_path / "task_11"
    actual = task_dir / "meeting_evidence.md"
    actual.parent.mkdir(parents=True, exist_ok=True)
    actual.write_text("# meeting evidence\n", encoding="utf-8")
    node = PlanNode(
        id=11,
        plan_id=72,
        name="Gather Historical Background Evidence",
        metadata={
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [
                    {"type": "file_exists", "path": "introduction_evidence.md"},
                    {"type": "file_nonempty", "path": "introduction_evidence.md"},
                ],
            }
        },
    )
    verifier = TaskVerificationService()

    finalization = verifier.finalize_payload(
        node,
        {
            "status": "completed",
            "content": "meeting evidence captured",
            "metadata": {
                "artifact_paths": [str(actual)],
                "task_directory_full": str(task_dir),
            },
        },
        execution_status="completed",
    )

    expected = task_dir / "introduction_evidence.md"
    assert finalization.final_status == "failed"
    assert finalization.verification is not None
    assert finalization.verification["status"] == "failed"
    assert not expected.exists()


def test_execution_not_completed_skips_verification():
    """When execution status is 'failed', verification should not run."""
    verifier = TaskVerificationService()
    node = PlanNode(
        id=1,
        plan_id=1,
        name="Failed task",
        metadata={
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [{"type": "file_exists", "path": "/some/path.txt"}],
            }
        },
    )
    fin = verifier.finalize_payload(
        node,
        {"status": "failed", "content": "error occurred", "metadata": {}},
        execution_status="failed",
    )
    assert fin.final_status == "failed"
    assert fin.verification is None


def test_glob_count_at_least_check(tmp_path):
    """glob_count_at_least should count matching files."""
    subdir = tmp_path / "pdb_files"
    subdir.mkdir()
    for i in range(5):
        (subdir / f"file_{i}.pdb").write_text(f"HEADER {i}\n")
    verifier = TaskVerificationService()

    node_pass = PlanNode(
        id=1, plan_id=1, name="Glob pass",
        metadata={
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [{"type": "glob_count_at_least", "glob": str(subdir / "*.pdb"), "min_count": 5}],
            }
        },
    )
    fin = verifier.finalize_payload(
        node_pass,
        {"status": "completed", "content": "ok", "metadata": {}},
        execution_status="completed",
    )
    assert fin.final_status == "completed"
    assert fin.verification["status"] == "passed"

    node_fail = PlanNode(
        id=2, plan_id=1, name="Glob fail",
        metadata={
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [{"type": "glob_count_at_least", "glob": str(subdir / "*.pdb"), "min_count": 10}],
            }
        },
    )
    fin2 = verifier.finalize_payload(
        node_fail,
        {"status": "completed", "content": "ok", "metadata": {}},
        execution_status="completed",
    )
    assert fin2.final_status == "failed"
    assert fin2.verification["failures"][0]["count"] == 5


def test_glob_count_at_least_legacy_path_count_shape(tmp_path):
    actual = tmp_path / "results" / "4.1.2"
    actual.mkdir(parents=True)
    (actual / "significant_interactions.csv").write_text("source,target\nA,B\n", encoding="utf-8")
    verifier = TaskVerificationService()

    node = PlanNode(
        id=3,
        plan_id=68,
        name="CellChat 结果整理",
        metadata={
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [
                    {
                        "type": "file_exists",
                        "path": "output/4.1.2/significant_interactions.csv",
                    },
                    {
                        "type": "glob_count_at_least",
                        "path": "output/4.1.2/significant_interactions.csv",
                        "count": 1,
                    }
                ],
            }
        },
    )

    finalization = verifier.finalize_payload(
        node,
        {
            "status": "completed",
            "content": "ok",
            "metadata": {
                "run_directory": str(tmp_path),
                "artifact_paths": [str(actual / "significant_interactions.csv")],
            },
        },
        execution_status="completed",
    )

    assert finalization.final_status == "completed"
    assert finalization.verification is not None
    assert finalization.verification["status"] == "passed"
    assert finalization.verification["checks_passed"] == 3


def test_glob_count_at_least_matches_relocated_artifact_paths(tmp_path):
    relocated = tmp_path / "plan68_task56" / "run_abc" / "results" / "cellchat"
    relocated.mkdir(parents=True)
    (relocated / "visualization_summary.txt").write_text("ok\n", encoding="utf-8")
    for name in ("circle_plot.pdf", "dotplot.pdf", "heatmap.pdf", "visualization_summary.pdf"):
        (relocated / name).write_text("pdf\n", encoding="utf-8")

    verifier = TaskVerificationService()
    node = PlanNode(
        id=56,
        plan_id=68,
        name="CellChat visualization export",
        metadata={
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [
                    {
                        "type": "file_exists",
                        "path": "results/cellchat/visualization_summary.txt",
                    },
                    {
                        "type": "glob_count_at_least",
                        "path": "results/cellchat/*.pdf",
                        "count": 3,
                    },
                ],
            }
        },
    )

    artifact_paths = [str(path) for path in sorted(relocated.iterdir())]
    finalization = verifier.finalize_payload(
        node,
        {
            "status": "completed",
            "content": "ok",
            "metadata": {
                "run_directory": str(tmp_path),
                "artifact_paths": artifact_paths,
            },
        },
        execution_status="completed",
    )

    assert finalization.final_status == "completed"
    assert finalization.verification is not None
    assert finalization.verification["status"] == "passed"
    assert finalization.verification["checks_passed"] == 2


def test_glob_count_at_least_matches_flattened_promoted_raw_files(tmp_path):
    promoted_dir = tmp_path / "runtime" / "session_phagescope" / "raw_files" / "task_1" / "task_6"
    promoted_dir.mkdir(parents=True)
    for index in range(6):
        (promoted_dir / f"figure_{index}.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    manifest = promoted_dir / "figure_manifest.json"
    manifest.write_text('{"figures": 6}\n', encoding="utf-8")

    verifier = TaskVerificationService()
    node = PlanNode(
        id=6,
        plan_id=85,
        name="Generate figures and result tables",
        metadata={
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [
                    {"type": "glob_count_at_least", "path": "figures/*.png", "count": 6},
                    {"type": "file_exists", "path": "figure_manifest.json"},
                ],
            }
        },
    )

    artifact_paths = [str(path) for path in sorted(promoted_dir.iterdir())]
    finalization = verifier.finalize_payload(
        node,
        {
            "status": "completed",
            "content": "Promoted 7 file(s) to unified output dir.",
            "metadata": {
                "run_directory": str(tmp_path / "scratch" / "run_without_figures"),
                "artifact_paths": artifact_paths,
            },
        },
        execution_status="completed",
    )

    assert finalization.final_status == "completed"
    assert finalization.verification is not None
    assert finalization.verification["status"] == "passed"
    assert finalization.verification["checks_passed"] == 2


def test_json_field_equals_derives_mandatory_gates_passed(tmp_path):
    audit = tmp_path / "report_quality_audit.json"
    audit.write_text(
        json.dumps(
            {
                "overall_status": "PASS",
                "mandatory_gates_summary": {
                    "total_mandatory": 8,
                    "passed": 8,
                    "failed": 0,
                    "unchecked": 0,
                },
                "gates": {
                    "dataset_provenance": {"status": "PASS", "mandatory": True, "checked": True},
                    "split_leakage": {"status": "PASS", "mandatory": True, "checked": True},
                },
            }
        ),
        encoding="utf-8",
    )
    verifier = TaskVerificationService()
    node = PlanNode(
        id=9,
        plan_id=85,
        name="Run final report quality audit",
        metadata={
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [
                    {"type": "file_nonempty", "path": "report_quality_audit.json"},
                    {
                        "type": "json_field_equals",
                        "path": "report_quality_audit.json",
                        "key_path": "mandatory_gates_passed",
                        "expected": True,
                    },
                ],
            }
        },
    )

    finalization = verifier.finalize_payload(
        node,
        {
            "status": "completed",
            "content": "audit complete",
            "metadata": {
                "run_directory": str(tmp_path),
                "artifact_paths": [str(audit)],
            },
        },
        execution_status="completed",
    )

    assert finalization.final_status == "completed"
    assert finalization.verification is not None
    assert finalization.verification["status"] == "passed"
    assert finalization.verification["checks_passed"] == 2


def test_manuscript_markdown_quality_passes_for_prose_first_paper(tmp_path):
    manuscript = tmp_path / "manuscript.md"
    long_para = (
        "This section develops a claim through evidence rather than listing artifacts. "
        "The analysis explains why the dataset construction matters, how the leakage-aware split constrains the "
        "interpretation, and why model metrics must be interpreted alongside class-wise behavior. "
        "It also connects the figure callouts to the scientific argument so the text reads like a manuscript, "
        "not like a generated checklist. "
    ) * 3
    manuscript.write_text(
        "\n\n".join(
            [
                "# Feature-leakage-aware PhageScope manuscript",
                "## Background\n\n" + long_para,
                "## Results\n\n### Benchmark construction\n\n" + long_para + "\n\nFigure 1 shows the dataset curation and Table 1 summarizes the split.",
                "### Ablation analysis\n\n" + long_para + "\n\nFigure 2 compares proxy-inclusive and leakage-controlled models.",
                "### Class-wise error analysis\n\n" + long_para + "\n\nFigure 3 and Table 2 report class-wise macro F1 behavior.",
                "## Discussion\n\n" + long_para + "\n\nFigure 4 supports the confusion analysis.",
                "## Methods\n\n" + long_para,
                "## Evidence boundary\n\n" + long_para + "\n\nFigure 5 summarizes the manuscript claim.",
            ]
        ),
        encoding="utf-8",
    )
    verifier = TaskVerificationService()
    node = PlanNode(
        id=7,
        plan_id=85,
        name="Draft manuscript",
        metadata={
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [
                    {
                        "type": "manuscript_markdown_quality",
                        "path": "manuscript.md",
                        "min_text_chars": 4000,
                        "min_sections": 6,
                        "min_long_paragraphs": 5,
                        "max_bullet_ratio": 0.12,
                        "min_figure_callouts": 5,
                        "min_table_callouts": 2,
                        "min_results_subsections": 3,
                        "required_terms": ["Results", "Discussion", "Methods", "ablation", "class-wise", "Evidence boundary"],
                    }
                ],
            }
        },
    )

    finalization = verifier.finalize_payload(
        node,
        {"status": "completed", "metadata": {"run_directory": str(tmp_path), "artifact_paths": [str(manuscript)]}},
        execution_status="completed",
    )

    assert finalization.final_status == "completed"
    assert finalization.verification is not None
    assert finalization.verification["status"] == "passed"


def test_manuscript_markdown_quality_rejects_report_style_bullets(tmp_path):
    manuscript = tmp_path / "manuscript.md"
    manuscript.write_text(
        "# Report\n\n## Results\n\n- accuracy: high\n- macro F1: ok\n- figure made\n- table made\n",
        encoding="utf-8",
    )
    verifier = TaskVerificationService()
    node = PlanNode(
        id=7,
        plan_id=85,
        name="Draft manuscript",
        metadata={
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [
                    {
                        "type": "manuscript_markdown_quality",
                        "path": "manuscript.md",
                        "min_text_chars": 1000,
                        "min_sections": 4,
                        "min_long_paragraphs": 2,
                        "max_bullet_ratio": 0.12,
                        "min_figure_callouts": 1,
                        "min_results_subsections": 1,
                        "required_terms": ["Discussion", "Evidence boundary"],
                    }
                ],
            }
        },
    )

    finalization = verifier.finalize_payload(
        node,
        {"status": "completed", "metadata": {"run_directory": str(tmp_path), "artifact_paths": [str(manuscript)]}},
        execution_status="completed",
    )

    assert finalization.final_status == "failed"
    assert finalization.verification is not None
    assert finalization.verification["status"] == "failed"
    failure = finalization.verification["failures"][0]
    assert failure["type"] == "manuscript_markdown_quality"
    assert "bullet_ratio" in failure["message"] or "text_chars" in failure["message"]


def test_is_local_path_filtering():
    """_is_local_path should reject URLs, plain text, and accept real paths."""
    verifier = TaskVerificationService()
    # Should accept
    assert verifier._is_local_path("/Users/apple/data/report.json") is True
    assert verifier._is_local_path("./output/result.csv") is True
    assert verifier._is_local_path("~/Downloads/file.pdb") is True
    assert verifier._is_local_path("data/output.txt") is True
    assert verifier._is_local_path("report.json") is True
    # Should reject
    assert verifier._is_local_path("https://example.com/file.txt") is False
    assert verifier._is_local_path("some random text without path") is False
    assert verifier._is_local_path("") is False
    assert verifier._is_local_path("s3://bucket/key") is False
    assert verifier._is_local_path("hello world") is False


def test_override_criteria_persisted_in_execution_result(tmp_path):
    """Override criteria should be persisted into execution_result.metadata."""
    report = tmp_path / "report.txt"
    report.write_text("content\n", encoding="utf-8")
    tree = PlanTree(id=12, title="Persist Test")
    tree.nodes = {
        1: PlanNode(
            id=1,
            plan_id=12,
            name="Task",
            status="completed",
            metadata={},
            execution_result=json.dumps({
                "status": "completed",
                "content": "done",
                "metadata": {},
            }),
        )
    }
    repo = _RepoStub(tree)
    verifier = TaskVerificationService()

    override = {
        "category": "file_data",
        "blocking": True,
        "checks": [{"type": "file_exists", "path": str(report)}],
    }
    verifier.verify_task(repo, plan_id=12, task_id=1, override_criteria=override)

    # Check that the persisted execution_result contains acceptance_criteria
    last_update = repo.update_calls[-1][2]
    persisted = json.loads(last_update["execution_result"])
    assert "acceptance_criteria" in persisted["metadata"]
    assert len(persisted["metadata"]["acceptance_criteria"]["checks"]) == 1


def test_task_verifier_filters_internal_tool_output_paths(tmp_path):
    internal_step = tmp_path / "tool_outputs" / "job_dt_x" / "step_1_terminal_session_abc"
    internal_step.mkdir(parents=True, exist_ok=True)
    (internal_step / "result.json").write_text("{}", encoding="utf-8")
    task_dir = tmp_path / "plan68_task35" / "run_1"
    output_file = task_dir / "results" / "enrichment" / "gene_id_mapping.csv"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text("gene,entrez\nA,1\n", encoding="utf-8")

    node = PlanNode(
        id=35,
        plan_id=68,
        name="Task 35",
        metadata={
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [{"type": "file_exists", "path": "results/enrichment/gene_id_mapping.csv"}],
            }
        },
    )
    verifier = TaskVerificationService()

    finalization = verifier.finalize_payload(
        node,
        {
            "status": "completed",
            "content": "done",
            "task_directory_full": str(task_dir),
            "metadata": {
                "storage": {
                    "output_dir": str(internal_step),
                    "result_path": str(internal_step / "result.json"),
                }
            },
        },
        execution_status="completed",
    )

    assert finalization.final_status == "completed"
    assert all("/tool_outputs/" not in path for path in finalization.artifact_paths)


def test_json_field_at_least_supports_legacy_csv_row_count_keys(tmp_path):
    csv_path = tmp_path / "gene_id_mapping.csv"
    csv_path.write_text(
        "gene,entrez\nA,1\nB,2\nC,3\n",
        encoding="utf-8",
    )
    node = PlanNode(
        id=35,
        plan_id=68,
        name="Task 35",
        metadata={
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [
                    {
                        "type": "json_field_at_least",
                        "path": str(csv_path),
                        "field": "row_count",
                        "value": 3,
                    }
                ],
            }
        },
    )
    verifier = TaskVerificationService()

    finalization = verifier.finalize_payload(
        node,
        {"status": "completed", "content": "done", "metadata": {}},
        execution_status="completed",
    )

    assert finalization.final_status == "completed"
    assert finalization.verification["status"] == "passed"


# ---------------------------------------------------------------------------
# glob_nonempty check type
# ---------------------------------------------------------------------------

def test_glob_nonempty_check_passes(tmp_path):
    """glob_nonempty should pass when at least one file matches."""
    subdir = tmp_path / "output"
    subdir.mkdir()
    (subdir / "result.png").write_bytes(b"\x89PNG fake")
    (subdir / "result2.png").write_bytes(b"\x89PNG fake2")

    verifier = TaskVerificationService()
    node = PlanNode(
        id=1, plan_id=1, name="Glob nonempty pass",
        metadata={
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [{"type": "glob_nonempty", "glob": str(subdir / "*.png")}],
            }
        },
    )
    fin = verifier.finalize_payload(
        node,
        {"status": "completed", "content": "ok", "metadata": {}},
        execution_status="completed",
    )
    assert fin.final_status == "completed"
    assert fin.verification["status"] == "passed"


def test_glob_nonempty_check_fails(tmp_path):
    """glob_nonempty should fail when no files match."""
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()

    verifier = TaskVerificationService()
    node = PlanNode(
        id=1, plan_id=1, name="Glob nonempty fail",
        metadata={
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [{"type": "glob_nonempty", "glob": str(empty_dir / "*.csv")}],
            }
        },
    )
    fin = verifier.finalize_payload(
        node,
        {"status": "completed", "content": "ok", "metadata": {}},
        execution_status="completed",
    )
    assert fin.final_status == "failed"
    assert fin.verification["status"] == "failed"


# ---------------------------------------------------------------------------
# Artifact path fallback for file_exists / file_nonempty
# ---------------------------------------------------------------------------


def test_accept_task_result_persists_manual_acceptance_and_patch() -> None:
    tree = PlanTree(id=77, title="Manual Accept")
    tree.nodes = {
        4: PlanNode(
            id=4,
            plan_id=77,
            name="Collect evidence",
            status="failed",
            instruction="Collect the evidence and verify it.",
            execution_result=json.dumps(
                {
                    "status": "failed",
                    "content": "verification failed but files exist",
                    "metadata": {
                        "verification_status": "failed",
                        "artifact_authority": {"status": "failed"},
                    },
                },
                ensure_ascii=False,
            ),
        )
    }
    repo = _RepoStub(tree)
    verifier = TaskVerificationService()

    finalization = verifier.accept_task_result(
        repo,
        plan_id=77,
        task_id=4,
        reason="Artifacts are acceptable after review.",
        accepted_by="tester",
        task_name="Collect reviewed evidence",
        task_instruction="Use the generated artifacts as the accepted baseline.",
    )

    assert finalization.final_status == "completed"
    update = repo.update_calls[-1][2]
    assert update["status"] == "completed"
    assert update["name"] == "Collect reviewed evidence"
    assert update["instruction"] == "Use the generated artifacts as the accepted baseline."

    persisted = json.loads(update["execution_result"])
    manual_acceptance = persisted["metadata"]["manual_acceptance"]
    assert manual_acceptance["status"] == "accepted"
    assert manual_acceptance["reason"] == "Artifacts are acceptable after review."
    assert manual_acceptance["accepted_by"] == "tester"
    assert manual_acceptance["task_patch"] == {
        "name": "Collect reviewed evidence",
        "instruction": "Use the generated artifacts as the accepted baseline.",
    }


def test_accept_task_result_allows_completed_task_with_failed_verification() -> None:
    tree = PlanTree(id=771, title="Manual Accept Verification Failure")
    tree.nodes = {
        4: PlanNode(
            id=4,
            plan_id=771,
            name="Collect evidence",
            status="completed",
            instruction="Collect the evidence and verify it.",
            execution_result=json.dumps(
                {
                    "status": "completed",
                    "content": "execution succeeded but verification failed",
                    "metadata": {
                        "verification": {
                            "status": "failed",
                        },
                    },
                },
                ensure_ascii=False,
            ),
        )
    }
    repo = _RepoStub(tree)
    verifier = TaskVerificationService()

    finalization = verifier.accept_task_result(
        repo,
        plan_id=771,
        task_id=4,
        reason="Reviewed manually after verification failure.",
    )

    assert finalization.final_status == "completed"
    persisted = json.loads(repo.update_calls[-1][2]["execution_result"])
    assert persisted["metadata"]["manual_acceptance"]["verification_status"] == "failed"


def test_accept_task_result_rejects_successful_task_without_review_failure() -> None:
    tree = PlanTree(id=772, title="Manual Accept Reject Success")
    tree.nodes = {
        4: PlanNode(
            id=4,
            plan_id=772,
            name="Collect evidence",
            status="completed",
            instruction="Collect the evidence and verify it.",
            execution_result=json.dumps(
                {
                    "status": "completed",
                    "content": "all checks passed",
                    "metadata": {
                        "verification": {
                            "status": "passed",
                        },
                    },
                },
                ensure_ascii=False,
            ),
        )
    }
    repo = _RepoStub(tree)
    verifier = TaskVerificationService()

    try:
        verifier.accept_task_result(
            repo,
            plan_id=772,
            task_id=4,
            reason="This should be rejected.",
        )
    except ValueError as exc:
        assert "manual acceptance is only allowed" in str(exc)
    else:
        raise AssertionError("Expected manual acceptance validation to reject successful task")


def test_accept_task_route_marks_task_completed(monkeypatch) -> None:
    tree = PlanTree(id=78, title="Manual Accept Route")
    tree.nodes = {
        5: PlanNode(
            id=5,
            plan_id=78,
            name="Collect evidence",
            status="failed",
            instruction="Review the generated outputs.",
            execution_result=json.dumps(
                {
                    "status": "failed",
                    "content": "verification failed",
                    "metadata": {
                        "verification_status": "failed",
                    },
                },
                ensure_ascii=False,
            ),
        )
    }
    repo = _RepoStub(tree)
    monkeypatch.setattr(plan_routes, "_plan_repo", repo)
    monkeypatch.setattr(plan_routes, "_load_authorized_plan_tree", lambda plan_id, request: tree)
    monkeypatch.setattr(plan_routes, "get_request_owner_id", lambda request: "owner-1")

    response = plan_routes.accept_task_result(
        5,
        plan_routes.AcceptTaskRequest(
            reason="Outputs are usable after review.",
            name="Accepted evidence",
            instruction="Use this accepted evidence set for the downstream task.",
        ),
        request=None,
        plan_id=78,
    )

    assert response.success is True
    assert response.result.status == "completed"
    assert response.updated_fields == ["name", "instruction"]
    accepted_update = next(update for _, task_id, update in repo.update_calls if task_id == 5 and "execution_result" in update)
    persisted = json.loads(accepted_update["execution_result"])
    assert persisted["metadata"]["manual_acceptance"]["reason"] == "Outputs are usable after review."
    assert persisted["metadata"]["manual_acceptance"]["accepted_by"] == "owner-1"


def test_accept_task_route_resets_downstream_skipped_tasks(monkeypatch) -> None:
    tree = PlanTree(id=79, title="Manual Accept Route Reset")
    tree.nodes = {
        5: PlanNode(
            id=5,
            plan_id=79,
            name="Collect evidence",
            status="failed",
            execution_result=json.dumps(
                {
                    "status": "failed",
                    "content": "verification failed",
                    "metadata": {"verification_status": "failed"},
                },
                ensure_ascii=False,
            ),
        ),
        6: PlanNode(
            id=6,
            plan_id=79,
            name="Use evidence",
            status="skipped",
            dependencies=[5],
        ),
    }
    tree.rebuild_adjacency()
    repo = _RepoStub(tree)
    monkeypatch.setattr(plan_routes, "_plan_repo", repo)
    monkeypatch.setattr(plan_routes, "_load_authorized_plan_tree", lambda plan_id, request: tree)
    monkeypatch.setattr(plan_routes, "get_request_owner_id", lambda request: "owner-1")

    response = plan_routes.accept_task_result(
        5,
        plan_routes.AcceptTaskRequest(reason="Outputs are usable after review."),
        request=None,
        plan_id=79,
    )

    assert response.success is True
    assert "reset 1 downstream skipped task" in response.message
    assert tree.nodes[6].status == "pending"
    assert any(task_id == 6 and update.get("status") == "pending" for _, task_id, update in repo.update_calls)


def test_accept_task_route_rejects_completed_verified_task(monkeypatch) -> None:
    tree = PlanTree(id=781, title="Manual Accept Route Reject Success")
    tree.nodes = {
        5: PlanNode(
            id=5,
            plan_id=781,
            name="Collect evidence",
            status="completed",
            instruction="Review the generated outputs.",
            execution_result=json.dumps(
                {
                    "status": "completed",
                    "content": "verification passed",
                    "metadata": {
                        "verification_status": "passed",
                    },
                },
                ensure_ascii=False,
            ),
        )
    }
    repo = _RepoStub(tree)
    monkeypatch.setattr(plan_routes, "_plan_repo", repo)
    monkeypatch.setattr(plan_routes, "_load_authorized_plan_tree", lambda plan_id, request: tree)
    monkeypatch.setattr(plan_routes, "get_request_owner_id", lambda request: "owner-1")

    try:
        plan_routes.accept_task_result(
            5,
            plan_routes.AcceptTaskRequest(reason="Should not accept an already verified result."),
            request=None,
            plan_id=781,
        )
    except plan_routes.HTTPException as exc:
        assert exc.status_code == 400
        assert "manual acceptance is only allowed" in str(exc.detail)
    else:
        raise AssertionError("Expected route to reject successful task manual acceptance")

def test_file_exists_fallback_basename_match(tmp_path):
    """file_exists should pass via basename fallback when path is wrong but file exists in artifacts."""
    run_dir = tmp_path / "results" / "plan1_task1" / "run_abc123"
    run_dir.mkdir(parents=True)
    actual_file = run_dir / "report.csv"
    actual_file.write_text("col1,col2\n1,2\n", encoding="utf-8")

    verifier = TaskVerificationService()
    node = PlanNode(
        id=1, plan_id=1, name="Basename fallback",
        metadata={
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [
                    # Wrong path, but basename matches an artifact
                    {"type": "file_exists", "path": "results/output/report.csv"},
                ],
            }
        },
    )
    fin = verifier.finalize_payload(
        node,
        {
            "status": "completed",
            "content": "ok",
            "metadata": {"artifact_paths": [str(actual_file)]},
        },
        execution_status="completed",
    )
    assert fin.final_status == "completed"
    assert fin.verification["status"] == "passed"


def test_file_nonempty_does_not_pass_on_extension_only_match(tmp_path):
    """Plan-first verification should not accept a different filename solely because
    the produced artifact shares the same file extension."""
    run_dir = tmp_path / "run_xyz"
    run_dir.mkdir()
    actual_file = run_dir / "DotPlot.png"
    actual_file.write_bytes(b"\x89PNG fake data here")

    verifier = TaskVerificationService()
    node = PlanNode(
        id=1, plan_id=1, name="Extension fallback",
        metadata={
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [
                    # Different filename, same extension
                    {"type": "file_nonempty", "path": "figures/expression_plot.png"},
                ],
            }
        },
    )
    fin = verifier.finalize_payload(
        node,
        {
            "status": "completed",
            "content": "ok",
            "metadata": {"artifact_paths": [str(actual_file)]},
        },
        execution_status="completed",
    )
    assert fin.final_status == "failed"
    assert fin.verification["status"] == "failed"


def test_file_exists_fallback_lenient_different_extension(tmp_path):
    """file_exists should fail when extension doesn't match — lenient 'any file' fallback is removed.

    Previously this test verified that *any* existing artifact would pass the
    check even with a completely different extension (e.g. criteria says PDF but
    task produced PNG).  That behaviour caused false-positive verification when a
    task only produced partial output.  Now only basename or extension matches are
    accepted as fallbacks.
    """
    run_dir = tmp_path / "run_xyz"
    run_dir.mkdir()
    actual_file = run_dir / "DotPlot.png"
    actual_file.write_bytes(b"\x89PNG fake data")

    verifier = TaskVerificationService()
    node = PlanNode(
        id=1, plan_id=1, name="Lenient fallback",
        metadata={
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [
                    # Criteria says PDF but task produced PNG — different extension
                    {"type": "file_exists", "path": "results/figures/marker_plots.pdf"},
                ],
            }
        },
    )
    fin = verifier.finalize_payload(
        node,
        {
            "status": "completed",
            "content": "ok",
            "metadata": {"artifact_paths": [str(actual_file)]},
        },
        execution_status="completed",
    )
    assert fin.final_status == "failed"
    assert fin.verification["status"] == "failed"


def test_file_exists_no_fallback_without_artifacts(tmp_path):
    """file_exists should still fail when there are no artifacts to fall back on."""
    verifier = TaskVerificationService()
    node = PlanNode(
        id=1, plan_id=1, name="No artifacts",
        metadata={
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [
                    {"type": "file_exists", "path": str(tmp_path / "nonexistent.csv")},
                ],
            }
        },
    )
    fin = verifier.finalize_payload(
        node,
        {"status": "completed", "content": "ok", "metadata": {}},
        execution_status="completed",
    )
    assert fin.final_status == "failed"
    assert fin.verification["status"] == "failed"


def test_relative_checks_prefer_run_directory_over_tool_output_artifacts(tmp_path):
    run_dir = tmp_path / "plan68_task34" / "run_abc123"
    expected = run_dir / "results" / "enrichment" / "upregulated_genes.csv"
    expected.parent.mkdir(parents=True)
    expected.write_text("gene,score\nWFDC2,9.1\n", encoding="utf-8")

    tool_output = tmp_path / "tool_outputs" / "job_123" / "result.json"
    tool_output.parent.mkdir(parents=True)
    tool_output.write_text("{}", encoding="utf-8")

    verifier = TaskVerificationService()
    node = PlanNode(
        id=34,
        plan_id=68,
        name="差异基因提取与分类",
        metadata={
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [
                    {"type": "file_exists", "path": "results/enrichment/upregulated_genes.csv"},
                    {"type": "file_nonempty", "path": "results/enrichment/upregulated_genes.csv"},
                ],
            }
        },
    )

    fin = verifier.finalize_payload(
        node,
        {
            "status": "completed",
            "run_directory": str(run_dir),
            "artifact_paths": [str(tool_output)],
            "metadata": {},
        },
        execution_status="completed",
    )

    assert fin.final_status == "completed"
    assert fin.verification is not None
    assert fin.verification["status"] == "passed"


def test_relative_checks_infer_manuscript_root_from_acceptance_output_dir(tmp_path):
    manuscript_root = tmp_path / "runtime" / "session_x" / "manuscript"
    expected = manuscript_root / "methods" / "data_preprocessing.md"
    expected.parent.mkdir(parents=True)
    expected.write_text("## Methods\nHarmony integration.\n", encoding="utf-8")

    verifier = TaskVerificationService()
    node = PlanNode(
        id=66,
        plan_id=68,
        name="数据来源与预处理方法描述",
        metadata={
            "acceptance_criteria": {
                "category": "paper",
                "blocking": True,
                "checks": [
                    {"type": "file_nonempty", "path": "methods/data_preprocessing.md"},
                ],
            }
        },
    )

    fin = verifier.finalize_payload(
        node,
        {
            "status": "completed",
            "content": "methods draft written",
            "metadata": {
                "artifact_paths": [str(expected)],
            },
        },
        execution_status="completed",
    )

    assert fin.final_status == "completed"
    assert fin.verification is not None
    assert fin.verification["status"] == "passed"


def test_parse_shorthand_glob_nonempty():
    """parse_shorthand_criteria should support glob_nonempty format."""
    criteria = TaskVerificationService.parse_shorthand_criteria([
        "glob_nonempty:**/*.png",
        "glob_nonempty:results/**/*.csv",
    ])
    assert len(criteria["checks"]) == 2
    assert criteria["checks"][0] == {"type": "glob_nonempty", "glob": "**/*.png"}
    assert criteria["checks"][1] == {"type": "glob_nonempty", "glob": "results/**/*.csv"}

# ---------------------------------------------------------------------------
# _normalize_check tests
# ---------------------------------------------------------------------------

def test_normalize_check_field_to_key_path():
    """_normalize_check should map 'field' to 'key_path' when key_path is absent."""
    result = TaskVerificationService._normalize_check({"type": "json_field_at_least", "field": "cryo_em"})
    assert result["key_path"] == "cryo_em"
    assert result["field"] == "cryo_em"  # original key preserved


def test_normalize_check_min_count_to_min_value():
    """_normalize_check should map 'min_count' to 'min_value' when min_value is absent."""
    result = TaskVerificationService._normalize_check({"type": "json_field_at_least", "min_count": 5})
    assert result["min_value"] == 5
    assert result["min_count"] == 5  # original key preserved


def test_normalize_check_preserves_standard_fields():
    """_normalize_check should NOT overwrite existing standard fields."""
    original = {
        "type": "json_field_at_least",
        "key_path": "standard_key",
        "min_value": 10,
        "field": "legacy_field",
        "min_count": 99,
    }
    result = TaskVerificationService._normalize_check(original)
    assert result["key_path"] == "standard_key"
    assert result["min_value"] == 10


def test_normalize_check_no_input_mutation():
    """_normalize_check should not mutate the original dict."""
    original = {"type": "json_field_at_least", "field": "cryo_em", "min_count": 1}
    result = TaskVerificationService._normalize_check(original)
    assert "key_path" not in original
    assert "min_value" not in original
    assert result is not original


def test_normalize_check_non_dict_passthrough():
    """_normalize_check should return non-dict inputs as-is."""
    assert TaskVerificationService._normalize_check("not a dict") == "not a dict"
    assert TaskVerificationService._normalize_check(42) == 42
    assert TaskVerificationService._normalize_check(None) is None


def test_coerce_json_min_value_with_min_count():
    """_coerce_json_min_value should fall back to min_count when min_value is absent."""
    # min_value present → use it
    assert TaskVerificationService._coerce_json_min_value({"min_value": 10, "min_count": 5}) == 10
    # min_value absent, min_count present → use min_count
    assert TaskVerificationService._coerce_json_min_value({"min_count": 5}) == 5
    # both absent, value present → use value
    assert TaskVerificationService._coerce_json_min_value({"value": 3}) == 3
    # all absent → None
    assert TaskVerificationService._coerce_json_min_value({}) is None


def test_json_field_at_least_with_field_and_min_count(tmp_path):
    """End-to-end: json_field_at_least with legacy 'field'/'min_count' should pass.

    This reproduces the schema pattern from plan 68 task 37's acceptance_criteria,
    adapted to use a numeric field value (the verifier does float comparison).
    """
    json_file = tmp_path / "structured_evidence.json"
    json_file.write_text(
        '{"cryo_em": {"count": 3, "evidence_points": [{"title": "3D variability"}]}}',
        encoding="utf-8",
    )
    verifier = TaskVerificationService()
    node = PlanNode(
        id=37,
        plan_id=68,
        name="Cryo-EM evidence extraction",
        metadata={
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [
                    {"type": "file_exists", "path": str(json_file)},
                    {
                        "type": "json_field_at_least",
                        "path": str(json_file),
                        "field": "cryo_em.count",
                        "min_count": 1,
                    },
                ],
            }
        },
    )
    finalization = verifier.finalize_payload(
        node,
        {"status": "completed", "content": "done", "metadata": {}},
        execution_status="completed",
    )
    assert finalization.final_status == "completed"
    assert finalization.verification["status"] == "passed"
    assert finalization.verification["checks_passed"] == 2


# ---------------------------------------------------------------------------
# Step 7: artifact authority — require satisfaction
# ---------------------------------------------------------------------------


def test_artifact_authority_reports_require_satisfaction(tmp_path):
    """When explicit requires are resolved in the manifest, require_status is passed."""
    # Create the artifact file so resolve_manifest_aliases can find it
    refs_file = tmp_path / "refs.bib"
    refs_file.write_text("@article{demo, title={Demo}}", encoding="utf-8")

    verifier = TaskVerificationService()
    node = PlanNode(
        id=5,
        plan_id=1,
        name="Consumer task",
        metadata={
            "artifact_contract": {
                "requires": ["ai_dl.references_bib"],
            }
        },
    )
    from app.services.plans.task_verification import VerificationFinalization

    finalization = VerificationFinalization(
        final_status="completed",
        execution_status="completed",
        payload={"status": "completed", "content": "done", "metadata": {}},
    )
    manifest = {
        "artifacts": {
            "ai_dl.references_bib": {
                "alias": "ai_dl.references_bib",
                "path": str(refs_file),
                "producer_task_id": 3,
            }
        }
    }
    result = verifier.apply_artifact_authority(1, node, finalization, manifest=manifest)
    authority = result.payload["metadata"]["artifact_authority"]
    assert authority["require_status"] == "passed"
    assert authority["satisfied_require_aliases"] == ["ai_dl.references_bib"]
    assert authority["missing_require_aliases"] == []
    assert result.final_status == "completed"


def test_artifact_authority_reports_missing_require_aliases(tmp_path):
    """When explicit requires are NOT in the manifest, require_status is failed."""
    verifier = TaskVerificationService()
    node = PlanNode(
        id=5,
        plan_id=1,
        name="Consumer task",
        metadata={
            "artifact_contract": {
                "requires": ["ai_dl.references_bib"],
            }
        },
    )
    from app.services.plans.task_verification import VerificationFinalization

    finalization = VerificationFinalization(
        final_status="completed",
        execution_status="completed",
        payload={"status": "completed", "content": "done", "metadata": {}},
    )
    # Empty manifest — required alias is missing
    manifest = {"artifacts": {}}
    result = verifier.apply_artifact_authority(1, node, finalization, manifest=manifest)
    authority = result.payload["metadata"]["artifact_authority"]
    assert authority["require_status"] == "failed"
    assert authority["missing_require_aliases"] == ["ai_dl.references_bib"]
    assert authority["status"] == "failed"
    # Note: missing requires does NOT demote completed→failed (only missing publishes do)
    assert result.final_status == "completed"


def test_artifact_authority_combined_publish_and_require(tmp_path):
    """Both publish and require are checked; combined status reflects both."""
    # Create the required artifact file
    refs_file = tmp_path / "refs.bib"
    refs_file.write_text("@article{demo, title={Demo}}", encoding="utf-8")

    verifier = TaskVerificationService()
    node = PlanNode(
        id=5,
        plan_id=1,
        name="Transform task",
        metadata={
            "artifact_contract": {
                "requires": ["ai_dl.references_bib"],
                "publishes": ["ai_dl.evidence_md"],
            }
        },
    )
    from app.services.plans.task_verification import VerificationFinalization

    finalization = VerificationFinalization(
        final_status="completed",
        execution_status="completed",
        payload={"status": "completed", "content": "done", "metadata": {}},
    )
    # Require is satisfied (file exists), publish is NOT (no entry for this task)
    manifest = {
        "artifacts": {
            "ai_dl.references_bib": {
                "alias": "ai_dl.references_bib",
                "path": str(refs_file),
                "producer_task_id": 3,
            }
        }
    }
    result = verifier.apply_artifact_authority(1, node, finalization, manifest=manifest)
    authority = result.payload["metadata"]["artifact_authority"]
    assert authority["require_status"] == "passed"
    assert authority["publish_status"] == "failed"
    assert authority["status"] == "failed"
    # Missing publish demotes completed → failed
    assert result.final_status == "failed"
    assert result.payload["metadata"]["failure_kind"] == "artifact_publish_missing"



def test_task_verifier_rejects_invalid_sparse_npz_schema(tmp_path):
    from scipy import sparse

    artifact = tmp_path / "features" / "kmer_46.npz"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    sparse.save_npz(str(artifact), sparse.csr_matrix((0, 0)))

    node = PlanNode(
        id=15,
        plan_id=93,
        name="Extract k-mer Frequency Profiles",
        instruction="Generate k-mer feature matrix.",
        metadata={
            "artifact_contract": {"publishes": ["phage_ml.kmer_features_npz"]},
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [
                    {"type": "file_exists", "path": "features/kmer_46.npz"},
                    {"type": "file_nonempty", "path": "features/kmer_46.npz"},
                ],
            },
        },
    )
    verifier = TaskVerificationService()

    finalization = verifier.finalize_payload(
        node,
        {
            "status": "completed",
            "metadata": {
                "run_directory": str(tmp_path),
                "artifact_paths": [str(artifact)],
            },
        },
        execution_status="completed",
    )

    metadata = finalization.payload["metadata"]
    assert finalization.final_status == "failed"
    assert metadata["verification_status"] == "failed"
    assert metadata["failure_kind"] == "contract_mismatch"
    schema = metadata["artifact_schema_validation"]["phage_ml.kmer_features_npz"]
    assert schema["validated"] is False
    assert "shape" in schema["failure_reason"]
    assert "artifact_schema_invalid" in metadata["artifact_verification"]["tags"]
    assert metadata["contract_diff"]["invalid_artifacts"]


def test_task_verifier_accepts_valid_sparse_npz_schema(tmp_path):
    from scipy import sparse

    artifact = tmp_path / "features" / "kmer_46.npz"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    sparse.save_npz(str(artifact), sparse.csr_matrix([[1, 0], [0, 2]]))

    node = PlanNode(
        id=15,
        plan_id=93,
        name="Extract k-mer Frequency Profiles",
        instruction="Generate k-mer feature matrix.",
        metadata={
            "artifact_contract": {"publishes": ["phage_ml.kmer_features_npz"]},
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [{"type": "file_nonempty", "path": "features/kmer_46.npz"}],
            },
        },
    )

    finalization = TaskVerificationService().finalize_payload(
        node,
        {"status": "completed", "metadata": {"run_directory": str(tmp_path), "artifact_paths": [str(artifact)]}},
        execution_status="completed",
    )

    assert finalization.final_status == "completed"
    schema = finalization.payload["metadata"]["artifact_schema_validation"]["phage_ml.kmer_features_npz"]
    assert schema["validated"] is True
    assert schema["metadata"]["shape"] == [2, 2]



def test_output_matches_expected_accepts_absolute_suffix_path() -> None:
    assert TaskVerificationService._output_matches_expected(
        "/tmp/run_123/qc_results/filtered_adata.h5ad",
        ["qc_results/filtered_adata.h5ad"],
    ) is True


def test_task_verifier_accepts_contract_required_file_from_run_directory(tmp_path) -> None:
    run_dir = tmp_path / "run"
    artifact = run_dir / "qc_results" / "filtered_adata.h5ad"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"non-empty h5ad placeholder")
    node = PlanNode(
        id=11,
        plan_id=96,
        name="Load QC-filtered AnnData object",
        metadata={
            "acceptance_criteria": {
                "blocking": True,
                "checks": [
                    {"type": "file_exists", "path": "qc_results/filtered_adata.h5ad"},
                    {"type": "file_nonempty", "path": "qc_results/filtered_adata.h5ad"},
                ],
            }
        },
    )

    finalization = TaskVerificationService().finalize_payload(
        node,
        {
            "status": "completed",
            "metadata": {
                "run_directory": str(run_dir),
                "artifact_paths": [str(run_dir), str(artifact)],
            },
        },
        execution_status="completed",
    )

    assert finalization.final_status == "completed"
    metadata = finalization.payload["metadata"]
    assert metadata["verification_status"] == "passed"
    assert metadata["verification"]["checks_passed"] == 2


def test_task_verifier_resolves_relative_checks_against_task_raw_files_dir(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    task_dir = tmp_path / "runtime" / "session_test" / "raw_files" / "task_1" / "task_13" / "task_56"
    deliverable = task_dir / "Deliverables" / "docs" / "cell_communication_summary.txt"
    deliverable.parent.mkdir(parents=True)
    deliverable.write_text("cell communication summary\n", encoding="utf-8")
    session_root = tmp_path / "runtime" / "session_test"

    node = PlanNode(
        id=56,
        plan_id=100,
        name="Verify existence and non-emptiness of all analysis outputs",
        path="/1/13/56",
        metadata={
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [
                    {"type": "file_exists", "path": "Deliverables/docs/cell_communication_summary.txt"},
                    {"type": "file_nonempty", "path": "Deliverables/docs/cell_communication_summary.txt"},
                ],
            }
        },
    )

    finalization = TaskVerificationService().finalize_payload(
        node,
        {
            "status": "completed",
            "metadata": {
                "artifact_paths": [str(session_root), str(task_dir)],
            },
        },
        execution_status="completed",
    )

    metadata = finalization.payload["metadata"]
    assert finalization.final_status == "completed"
    assert metadata["verification_status"] == "passed"
    assert metadata["verification"]["checks_passed"] == 2
    assert metadata["verification"]["failures"] == []


def test_task_verifier_config_error_does_not_fail_completed_task_with_outputs(tmp_path) -> None:
    task_dir = tmp_path / "runtime" / "session_test" / "raw_files" / "task_1" / "task_2" / "task_46"
    task_dir.mkdir(parents=True)
    report = task_dir / "metadata_parsing_report.json"
    report.write_text(
        json.dumps({
            "total_files_expected": 14,
            "files_loaded": 14,
            "files_failed": 0,
            "total_records": 873718,
        }),
        encoding="utf-8",
    )
    node = PlanNode(
        id=46,
        plan_id=105,
        name="Load and Parse All 14 PhageScope Metadata TSV Files",
        path="/1/2/46",
        metadata={
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [{"type": "glob_count_at_least", "min_count": 14}],
            }
        },
    )

    finalization = TaskVerificationService().finalize_payload(
        node,
        {
            "status": "completed",
            "metadata": {"artifact_paths": [str(task_dir), str(report)]},
        },
        execution_status="completed",
    )

    metadata = finalization.payload["metadata"]
    assert finalization.final_status == "completed"
    assert finalization.verification is not None
    assert metadata["verification_status"] == "config_error"
    assert metadata["verification_config_error"] is True
    assert metadata["verification"]["blocking"] is False
    assert metadata["verification_config_errors"][0]["failure_kind"] == "verification_config_error"
    assert "failure_kind" not in metadata
    assert "contract_diff" not in metadata


def test_task_verifier_still_fails_config_error_when_no_output_evidence(tmp_path) -> None:
    node = PlanNode(
        id=47,
        plan_id=105,
        name="Load metadata without outputs",
        metadata={
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [{"type": "glob_count_at_least", "min_count": 1}],
            }
        },
    )

    finalization = TaskVerificationService().finalize_payload(
        node,
        {"status": "completed", "metadata": {"artifact_paths": []}},
        execution_status="completed",
    )

    metadata = finalization.payload["metadata"]
    assert finalization.final_status == "failed"
    assert metadata["verification_status"] == "failed"
    assert metadata["failure_kind"] == "contract_mismatch"
    assert metadata["verification"]["failures"][0]["failure_kind"] == "verification_config_error"


def test_task_verifier_searches_artifact_directory_roots_for_relative_globs(tmp_path) -> None:
    scratch_dir = tmp_path / "scratch" / "plan105_task46" / "run_001"
    promoted_dir = tmp_path / "runtime" / "session_test" / "raw_files" / "task_1" / "task_2" / "task_46"
    scratch_dir.mkdir(parents=True)
    promoted_metadata = promoted_dir / "metadata" / "ml_metadata_table_host.tsv"
    promoted_metadata.parent.mkdir(parents=True)
    promoted_metadata.write_text("id\thost\nphage_1\tE.coli\n", encoding="utf-8")
    node = PlanNode(
        id=46,
        plan_id=105,
        name="Load and Parse All 14 PhageScope Metadata TSV Files",
        path="/1/2/46",
        metadata={
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [{"type": "glob_count_at_least", "glob": "metadata/*.tsv", "min_count": 1}],
            }
        },
    )

    finalization = TaskVerificationService().finalize_payload(
        node,
        {
            "status": "completed",
            "metadata": {
                "run_directory": str(scratch_dir),
                "artifact_paths": [str(promoted_dir), str(promoted_metadata)],
            },
        },
        execution_status="completed",
    )

    metadata = finalization.payload["metadata"]
    assert finalization.final_status == "completed"
    assert metadata["verification_status"] == "passed"
    assert metadata["verification"]["checks_passed"] == 1
    assert metadata["verification"]["failures"] == []


def test_task_verifier_records_path_resolution_diagnostics(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    task_dir = tmp_path / "runtime" / "session_test" / "raw_files" / "task_1" / "task_3" / "task_19"
    artifact = task_dir / "05_CellChat" / "pathway_summary_N.csv"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("pathway,count\nTGFB,1\n", encoding="utf-8")
    node = PlanNode(
        id=19,
        plan_id=100,
        name="Load CellChat pathway summary files",
        path="/1/3/19",
        metadata={
            "acceptance_criteria": {
                "blocking": True,
                "checks": [
                    {"type": "file_exists", "path": "05_CellChat/pathway_summary_N.csv"},
                    {"type": "file_nonempty", "path": "05_CellChat/pathway_summary_N.csv"},
                ],
            }
        },
    )

    finalization = TaskVerificationService().finalize_payload(
        node,
        {
            "status": "completed",
            "metadata": {"artifact_paths": [str(task_dir), str(artifact)]},
        },
        execution_status="completed",
    )

    diagnostics = finalization.payload["metadata"]["verification_diagnostics"]
    assert diagnostics["plan_id"] == 100
    assert diagnostics["task_id"] == 19
    assert diagnostics["node_path"] == "/1/3/19"
    assert diagnostics["chosen_base_dir"] == str(task_dir)
    assert diagnostics["candidate_dirs"]["task_raw_files"] == [str(task_dir)]
    resolved_paths = [item["resolved_path"] for item in diagnostics["resolved_checks"]]
    assert resolved_paths
    assert set(resolved_paths) == {str(artifact)}
    assert {item["type"] for item in diagnostics["resolved_checks"]} == {
        "file_exists",
        "file_nonempty",
        "json_field_at_least",
    }
    assert diagnostics["artifact_path_stats"]["existing_files"] == 1
    assert diagnostics["artifact_path_stats"]["existing_dirs"] == 1
    assert finalization.verification["diagnostics"]["chosen_base_dir"] == str(task_dir)


def test_dry_run_reverify_plan_does_not_persist_and_reports_would_change(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "runtime" / "session_test" / "raw_files" / "task_1" / "task_2" / "report.txt"
    output.parent.mkdir(parents=True)
    output.write_text("ok\n", encoding="utf-8")
    payload = {
        "status": "failed",
        "content": "previous verification failed",
        "metadata": {"execution_status": "completed", "artifact_paths": [str(output)]},
    }
    tree = PlanTree(id=77, title="Dry-run reverify")
    tree.nodes = {
        2: PlanNode(
            id=2,
            plan_id=77,
            name="Write report",
            status="failed",
            path="/1/2",
            metadata={
                "acceptance_criteria": {
                    "blocking": True,
                    "checks": [{"type": "file_nonempty", "path": "report.txt"}],
                }
            },
            execution_result=json.dumps(payload, ensure_ascii=False),
        ),
        3: PlanNode(
            id=3,
            plan_id=77,
            name="Pending task",
            status="pending",
        ),
    }
    tree.rebuild_adjacency()
    repo = _RepoStub(tree)

    result = TaskVerificationService().dry_run_reverify_plan(repo, plan_id=77)

    assert repo.update_calls == []
    assert result["dry_run"] is True
    assert result["summary"] == {
        "total": 2,
        "verifiable": 1,
        "would_pass": 1,
        "would_fail": 0,
        "would_skip": 0,
        "would_change_status": 1,
        "unverifiable": 1,
    }
    item = result["items"][0]
    assert item["task_id"] == 2
    assert item["current_status"] == "failed"
    assert item["dry_run_status"] == "completed"
    assert item["would_change_status"] is True
    assert item["diagnostics"]["chosen_base_dir"] == str(output.parent)
