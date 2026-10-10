"""Decomposer and executor prompts carry the artifact contract end to end (LOCAL_INFRA §119).

The decomposition prompt already had ``artifact_contract`` in its schema, but
only showed fixed ML aliases and never explained the dynamic grammar, so a
literature-review plan (#183) was decomposed with every ``publishes`` empty.
The executor prompt listed publish aliases without saying which file to write.
"""

from __future__ import annotations

from app.config.decomposer_config import DecomposerSettings
from app.services.llm.decomposer_service import DecompositionChild
from app.services.plans.executor_prompts import ExecutorPromptBuilder
from app.services.plans.plan_decomposer import DecompositionPromptBuilder, PlanDecomposer
from app.services.plans.plan_models import PlanNode, PlanTree


def _decomposition_prompt() -> str:
    tree = PlanTree(id=7, title="噬菌体解聚酶综述", description="systematic literature review")
    return DecompositionPromptBuilder().build(
        plan=tree,
        node=None,
        outline="(empty plan)",
        mode="plan_bfs",
        settings=DecomposerSettings(),
        depth=0,
        max_depth=2,
    )


def test_decomposition_prompt_explains_the_dynamic_alias_grammar() -> None:
    prompt = _decomposition_prompt()
    assert "ARTIFACT CONTRACT RULES (MANDATORY" in prompt
    assert "`<namespace>.<file_stem>_<ext>`" in prompt
    assert "`results/search_corpus.jsonl` -> `<namespace>.search_corpus_jsonl`" in prompt
    assert "Pick ONE `<namespace>` for the whole plan" in prompt
    assert "lit_review.extraction_matrix_csv" in prompt
    assert "png" in prompt and "xlsx" in prompt
    # The schema example no longer points only at ML-specific aliases.
    assert "optional canonical alias like ai_dl.evidence_md" not in prompt
    # Existing guarantees stay in place.
    assert "SINGLE-PRODUCER RULE" in prompt


def test_invalid_required_outputs_do_not_abort_decomposition() -> None:
    child = DecompositionChild(
        name="数据提取与分类矩阵构建",
        instruction="构建矩阵",
        metadata={
            "required_outputs": [{"kind": "data", "min_count": "three", "target_path": "results/matrix.csv"}],
            "artifact_contract": {"publishes": ["lit_review.matrix_csv"]},
        },
    )
    # ``_derive_paper_metadata`` only uses static helpers from ``self``.
    metadata = PlanDecomposer._derive_paper_metadata(PlanDecomposer, child)
    assert "required_outputs" not in metadata
    assert "output_spec" not in metadata
    assert metadata["artifact_contract"] == {"publishes": ["lit_review.matrix_csv"]}


def test_valid_required_outputs_become_a_planner_output_spec() -> None:
    child = DecompositionChild(
        name="数据提取与分类矩阵构建",
        instruction="构建矩阵",
        metadata={
            "required_outputs": [{"kind": "data", "min_count": 1, "extensions": [".csv"], "target_path": "results/matrix.csv"}],
        },
    )
    metadata = PlanDecomposer._derive_paper_metadata(PlanDecomposer, child)
    assert metadata["output_spec"]["source"] == "planner"
    assert metadata["output_spec"]["required_outputs"][0]["target_path"] == "results/matrix.csv"


def test_executor_prompt_names_the_file_behind_each_publish_alias() -> None:
    node = PlanNode(
        id=3,
        plan_id=7,
        name="文献检索与双盲筛选",
        instruction="检索 PubMed 并去重",
        metadata={"artifact_contract": {"publishes": ["lit_review.search_corpus_jsonl"], "requires": []}},
    )
    prompt = ExecutorPromptBuilder().build(
        node=node,
        parent=None,
        dependencies=[],
        plan_outline=None,
        include_context=False,
        session_context={"session_id": None},
    )
    assert "Published artifact aliases: ['lit_review.search_corpus_jsonl']" in prompt
    assert "- lit_review.search_corpus_jsonl: write the file `search_corpus.jsonl` in your task output directory" in prompt
    assert "search_corpus.jsonl)" in prompt  # canonical landing path is shown
    assert "Report every produced file path in your final answer" in prompt


def test_executor_prompt_flags_unregistrable_aliases() -> None:
    node = PlanNode(
        id=3,
        plan_id=7,
        name="Legacy",
        metadata={"artifact_contract": {"publishes": ["output.report.md"]}},
    )
    prompt = ExecutorPromptBuilder().build(
        node=node, parent=None, dependencies=[], plan_outline=None, include_context=False, session_context={}
    )
    assert "- output.report.md: not a registrable alias" in prompt
