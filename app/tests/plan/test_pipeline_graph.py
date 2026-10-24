"""Pipeline graph validation: every rule reports the node, port or parameter it concerns."""

from __future__ import annotations

import copy

from app.services.pipelines.graph import graph_hash, validate_graph


def lambda_t7_graph():
    return {
        "schema": 1,
        "nodes": {
            "lambda": {"type": "input.genome", "label": "lambda", "params": {"path": "uploads/lambda.fasta"}},
            "t7": {"type": "input.genome", "label": "T7", "params": {"path": "uploads/t7.fasta"}},
            "stats": {"type": "seqkit.stats", "inputs": {"sequences": {"node": "lambda", "port": "file"}}},
            "genes_l": {"type": "prodigal.predict", "params": {"mode": "meta"}, "inputs": {"genome": {"node": "lambda", "port": "file"}}},
            "genes_t": {"type": "prodigal.predict", "inputs": {"genome": {"node": "t7", "port": "file"}}},
            "db": {"type": "blast.makeblastdb_aa", "inputs": {"proteins": {"node": "genes_l", "port": "proteins"}}},
            "blastp": {
                "type": "blast.blastp",
                "inputs": {"query": {"node": "genes_t", "port": "proteins"}, "db": {"node": "db", "port": "db"}},
            },
            "ani": {
                "type": "fastani.compare",
                "inputs": {"query": {"node": "lambda", "port": "file"}, "reference": {"node": "t7", "port": "file"}},
            },
            "report": {
                "type": "agent.report",
                "params": {"language": "en"},
                "inputs": {
                    "evidence": [
                        {"node": "stats", "port": "stats"},
                        {"node": "blastp", "port": "hits"},
                        {"node": "ani", "port": "ani"},
                    ]
                },
            },
        },
        "layout": {"lambda": {"x": 0, "y": 0}},
    }


def codes(result):
    return {(issue.code, issue.node) for issue in result.errors}


def test_a_complete_graph_is_valid_and_ordered_by_dependencies():
    result = validate_graph(lambda_t7_graph(), for_run=True)
    assert result.ok, result.as_dict()
    order = result.order
    assert order.index("lambda") < order.index("genes_l") < order.index("db") < order.index("blastp") < order.index("report")
    assert result.graph["nodes"]["genes_t"]["params"] == {"mode": "meta", "translation_table": 11}
    assert result.graph["layout"] == {"lambda": {"x": 0.0, "y": 0.0}}


def test_unknown_types_params_and_bad_values_are_reported():
    graph = lambda_t7_graph()
    graph["nodes"]["x"] = {"type": "nope.tool"}
    graph["nodes"]["genes_l"]["params"] = {"mode": "turbo", "translation_table": 99, "colour": "red"}
    result = validate_graph(graph)
    found = codes(result)
    assert ("unknown_node_type", "x") in found
    assert ("unknown_param", "genes_l") in found
    params = {issue.param for issue in result.errors if issue.code == "invalid_param"}
    assert params == {"mode", "translation_table"}


def test_connections_are_checked_for_type_existence_and_count():
    graph = lambda_t7_graph()
    graph["nodes"]["genes_t"]["inputs"]["genome"] = {"node": "genes_l", "port": "proteins"}
    graph["nodes"]["db"]["inputs"]["proteins"] = {"node": "ghost", "port": "proteins"}
    graph["nodes"]["stats"]["inputs"]["sequences"] = [
        {"node": "lambda", "port": "file"},
        {"node": "t7", "port": "file"},
    ]
    del graph["nodes"]["ani"]["inputs"]["reference"]
    result = validate_graph(graph)
    found = codes(result)
    assert ("type_mismatch", "genes_t") in found
    assert ("dangling_link", "db") in found
    assert ("too_many_links", "stats") in found
    assert ("missing_input", "ani") in found


def test_loops_are_rejected():
    graph = lambda_t7_graph()
    graph["nodes"]["a"] = {"type": "prodigal.predict", "inputs": {"genome": {"node": "b", "port": "genes"}}}
    graph["nodes"]["b"] = {"type": "prodigal.predict", "inputs": {"genome": {"node": "a", "port": "genes"}}}
    result = validate_graph(graph)
    cycle = [issue for issue in result.errors if issue.code == "cycle"]
    assert cycle and "a" in cycle[0].message and "b" in cycle[0].message


def test_the_report_node_must_be_last():
    graph = lambda_t7_graph()
    graph["nodes"]["stats2"] = {"type": "seqkit.stats", "inputs": {"sequences": {"node": "report", "port": "report"}}}
    result = validate_graph(graph)
    assert ("report_not_last", "report") in codes(result)


def test_input_files_are_required_only_to_run():
    graph = lambda_t7_graph()
    graph["nodes"]["t7"]["params"] = {}
    assert validate_graph(graph).ok
    result = validate_graph(graph, for_run=True)
    assert [(issue.code, issue.node, issue.param) for issue in result.errors] == [("missing_param", "t7", "path")]


def test_accessions_are_normalized_and_checked():
    graph = {"schema": 1, "nodes": {"n": {"type": "ncbi.genome", "params": {"accessions": "NC_001416.1;  NC_001604.1"}}}}
    result = validate_graph(graph)
    assert result.graph["nodes"]["n"]["params"]["accessions"] == "NC_001416.1, NC_001604.1"
    graph["nodes"]["n"]["params"]["accessions"] = "NC_001416.1, rm -rf"
    assert ("invalid_param", "n") in codes(validate_graph(graph))


def test_warnings_do_not_block_and_the_hash_ignores_layout():
    graph = lambda_t7_graph()
    graph["nodes"]["spare"] = {"type": "input.proteins", "params": {"path": "uploads/p.faa"}}
    result = validate_graph(graph)
    assert result.ok
    assert ("unused_node", "spare") in {(issue.code, issue.node) for issue in result.warnings}
    moved = copy.deepcopy(result.graph)
    moved["layout"] = {"lambda": {"x": 500, "y": 9}}
    assert graph_hash(moved) == graph_hash(result.graph)
    changed = copy.deepcopy(result.graph)
    changed["nodes"]["genes_l"]["params"]["mode"] = "single"
    assert graph_hash(changed) != graph_hash(result.graph)


def test_malformed_payloads_are_rejected_cleanly():
    assert not validate_graph(None).ok
    assert not validate_graph({"schema": 2}).ok
    assert not validate_graph({"schema": 1, "nodes": []}).ok
    assert ("invalid_node_id", "bad id!") in codes(validate_graph({"schema": 1, "nodes": {"bad id!": {"type": "input.genome"}}}))
