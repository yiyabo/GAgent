import asyncio
from copy import deepcopy

import pytest

from app.services.foundation.settings import get_settings
from app.services.plans.plan_generation import create_plan_and_generate
from app.tests.plan.test_plan_generation_pipeline import _GenerationRepoStub, _FailingDecomposerStub


@pytest.fixture(autouse=True)
def reset_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def definitions():
    return [
        {
            "key": "stage",
            "name": "local_analysis",
            "task_type": "composite",
            "instruction": "Perform exactly the three specified operations.",
        },
        {
            "key": "clean",
            "name": "clean",
            "parent_key": "stage",
            "task_type": "atomic",
            "instruction": "Reject non-finite scores, then keep the first valid ID. Never add a missingness threshold.",
            "metadata": {
                "required_outputs": [
                    {"kind": "data", "target_path": "results/clean.csv"},
                    {"kind": "data", "target_path": "results/quality_report.json"},
                ]
            },
        },
        {
            "key": "stats",
            "name": "stats",
            "parent_key": "stage",
            "task_type": "atomic",
            "dependencies": ["clean"],
            "instruction": "Read clean.csv; save group count/mean/median as summary.json.",
            "metadata": {"required_outputs": [{"kind": "data", "target_path": "results/summary.json"}]},
        },
        {
            "key": "report",
            "name": "report",
            "parent_key": "stage",
            "task_type": "atomic",
            "dependencies": ["clean", "stats"],
            "instruction": "Reuse earlier files and finish report.md.",
            "metadata": {"required_outputs": [{"kind": "document", "target_path": "results/report.md"}]},
        },
    ]


def test_exact_tree_preserves_methods_outputs_and_dependencies_without_approval_rules(monkeypatch):
    monkeypatch.setenv("PLAN_ACCEPTANCE_ENABLED", "0")
    get_settings.cache_clear()

    def forbidden(*args, **kwargs):
        pytest.fail("Explicit complete structure must not start hidden research, review, titles or decomposition")

    for name in ["collect_plan_generation_materials", "_auto_review_and_optimize_plan", "generate_phase_titles"]:
        monkeypatch.setattr("app.services.plans.plan_generation." + name, forbidden)
    seeds = definitions()
    original = deepcopy(seeds)
    repo = _GenerationRepoStub()
    outcome = asyncio.run(
        create_plan_and_generate(
            title="Local analysis",
            description="Keep exact requirements",
            tasks=seeds,
            repo=repo,
            decomposer=_FailingDecomposerStub(),
        )
    )
    tree = outcome.plan_tree
    by_name = {n.name: n for n in tree.nodes.values()}
    assert len(tree.nodes) == 5 and tree.nodes[tree.root_node_ids()[0]].name == "Local analysis"
    assert by_name["local_analysis"].parent_id == tree.root_node_ids()[0]
    for seed in seeds[1:]:
        node = by_name[seed["name"]]
        assert node.parent_id == by_name["local_analysis"].id
        assert node.instruction == seed["instruction"]
        assert node.metadata["required_outputs"] == seed["metadata"]["required_outputs"]
        assert "acceptance_criteria" not in node.metadata
    assert by_name["stats"].dependencies == [by_name["clean"].id]
    assert by_name["report"].dependencies == [by_name["clean"].id, by_name["stats"].id]
    assert outcome.decomposition_status == "skipped_seeded"
    assert outcome.auto_review["optimization_attempted"] is False
    assert seeds == original


def test_parent_keys_resolve_forward_references(monkeypatch):
    seeds = definitions()
    seeds = [*seeds[1:], seeds[0]]
    repo = _GenerationRepoStub()
    out = asyncio.run(
        create_plan_and_generate(
            title="Forward references", description="", tasks=seeds, repo=repo, decomposer=_FailingDecomposerStub()
        )
    )
    tree = out.plan_tree
    stage = next(n for n in tree.nodes.values() if n.name == "local_analysis")
    assert len(tree.children_ids(stage.id)) == 3


def test_both_tool_interfaces_expose_the_same_explicit_hierarchy():
    from app.services.tool_schemas import build_tool_schemas
    from tool_box.tools_impl.plan_tools import plan_operation_tool

    native = build_tool_schemas(["plan_operation"])[0]["function"]["parameters"]["properties"]["tasks"]
    implementation = plan_operation_tool["parameters_schema"]["properties"]["tasks"]
    for key in ("key", "parent_key", "task_type", "metadata", "dependencies"):
        assert native["items"]["properties"][key] == implementation["items"]["properties"][key]
    assert native["description"] == implementation["description"]


def test_explicit_hierarchy_survives_sqlite_reload(isolated_app_env):
    from app.database import init_db
    from app.repository.plan_repository import PlanRepository

    init_db()
    repo = PlanRepository()
    outcome = asyncio.run(
        create_plan_and_generate(
            title="Local analysis",
            description="Keep exact requirements",
            tasks=definitions(),
            repo=repo,
            decomposer=_FailingDecomposerStub(),
        )
    )
    tree = repo.get_plan_tree(outcome.plan_tree.id)
    nodes = {node.name: node for node in tree.nodes.values()}
    assert tree.node_count() == 5
    assert nodes["local_analysis"].parent_id == outcome.root_task_id
    assert tree.children_ids(nodes["local_analysis"].id) == [nodes[name].id for name in ("clean", "stats", "report")]
    assert nodes["stats"].dependencies == [nodes["clean"].id]
    assert nodes["report"].dependencies == [nodes["clean"].id, nodes["stats"].id]
    assert nodes["clean"].instruction == definitions()[1]["instruction"]
    assert nodes["clean"].metadata["required_outputs"][0]["target_path"] == "results/clean.csv"


@pytest.mark.parametrize(
    "change", ["unknown_parent", "parent_cycle", "unknown_dependency", "self_dependency", "duplicate_key"]
)
def test_invalid_explicit_references_fail_before_plan_is_created(change):
    seeds = definitions()
    if change == "unknown_parent":
        seeds[1]["parent_key"] = "absent"
    elif change == "parent_cycle":
        seeds[0]["parent_key"] = "stage"
    elif change == "unknown_dependency":
        seeds[2]["dependencies"] = ["absent"]
    elif change == "self_dependency":
        seeds[1]["dependencies"] = ["clean"]
    else:
        seeds[2]["key"] = "clean"
    repo = _GenerationRepoStub()
    with pytest.raises(ValueError):
        asyncio.run(
            create_plan_and_generate(
                title="Invalid", description="", tasks=seeds, repo=repo, decomposer=_FailingDecomposerStub()
            )
        )
    assert repo.tree is None
