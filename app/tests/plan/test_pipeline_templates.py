"""Ready-made pipelines: each validates as it is, needs no upload, and the editor can start from it."""

from __future__ import annotations

import pytest

from app.database import init_db
from app.services.pipelines import service, templates
from app.services.pipelines.graph import validate_graph
from app.services.pipelines.node_specs import get_spec


def test_every_template_is_runnable_as_it_is():
    assert templates.TEMPLATES
    for template in templates.TEMPLATES:
        graph = templates.graph_for(template.id)
        validation = validate_graph(graph, for_run=True)
        assert validation.ok, (template.id, [issue.as_dict() for issue in validation.errors])
        assert validation.warnings == [], (template.id, [issue.as_dict() for issue in validation.warnings])
        assert set(graph["layout"]) == set(graph["nodes"]), template.id
        assert all(get_spec(node["type"]).kind != "input" for node in graph["nodes"].values()), template.id
        assert all(template.title[lang] and template.description[lang] for lang in ("en", "zh")), template.id


def test_graph_for_hands_out_a_fresh_copy():
    first = templates.graph_for("phage_lambda_t7")
    first["nodes"].clear()
    assert templates.graph_for("phage_lambda_t7")["nodes"]
    assert templates.graph_for("nope") is None


def test_the_lambda_t7_template_covers_the_phage_main_line():
    graph = templates.graph_for("phage_lambda_t7")
    types = [node["type"] for node in graph["nodes"].values()]
    accessions = sorted(node["params"]["accessions"] for node in graph["nodes"].values() if node["type"] == "ncbi.genome")
    assert accessions == ["NC_001416.1", "NC_001604.1"]
    for per_phage in ("seqkit.stats", "prodigal.predict", "genomad.end_to_end", "checkv.end_to_end", "phabox2.phatyp"):
        assert types.count(per_phage) == 2, per_phage
    for once in ("fastani.compare", "blast.makeblastdb_aa", "blast.blastp", "mmseqs2.protein_search", "agent.report"):
        assert types.count(once) == 1, once


def test_the_catalog_lists_templates_and_a_pipeline_can_start_from_one(isolated_app_env):
    init_db()
    listed = service.catalog()["templates"]
    assert [item["id"] for item in listed] == [template.id for template in templates.TEMPLATES]
    assert listed[0]["node_count"] == len(templates.graph_for(listed[0]["id"])["nodes"])

    created = service.create("owner-1", "Phage basics", "", None, "phage_lambda_t7")
    assert created["draft"] == templates.graph_for("phage_lambda_t7")
    assert created["validation"]["ok"] is True

    for graph, template in ((None, "nope"), ({"schema": 1, "nodes": {}}, "phage_lambda_t7")):
        with pytest.raises(service.PipelineError) as err:
            service.create("owner-1", "x", "", graph, template)
        assert err.value.status == 422
