"""What the agent can do to a graph: every operation answers with a result it can read and fix, none raises."""

from __future__ import annotations

from app.services.pipelines.agent_context import catalog_text, describe_graph, state_text
from app.services.pipelines.agent_tools import GraphEditor, layered_layout, split_ref, tool_schemas
from app.services.pipelines.graph import validate_graph
from app.services.pipelines.node_specs import NODE_SPECS


def editor(files=None) -> GraphEditor:
    return GraphEditor({"schema": 1, "nodes": {}, "layout": {}}, files or ["uploads/ab12cd34_lambda.fasta"])


def small(ed: GraphEditor) -> GraphEditor:
    assert ed.add_node("ncbi.genome", id="lambda", params={"accessions": "NC_001416.1"})["ok"]
    assert ed.add_node("prodigal.predict", id="genes", inputs={"genome": "lambda.genome"})["ok"]
    return ed


def test_add_node_applies_defaults_params_and_links():
    ed = editor()
    result = ed.add_node("blast.blastp", id="bp", label="lambda vs T7", params={"evalue": 1e-5})
    assert result == {"ok": True, "node": "bp", "type": "blast.blastp"}
    node = ed.nodes["bp"]
    assert node["params"]["evalue"] == 1e-5 and node["params"]["max_target_seqs"] == 500
    assert node["label"] == "lambda vs T7" and "bp" in ed.graph["layout"]
    small(ed)
    assert ed.nodes["genes"]["inputs"]["genome"] == {"node": "lambda", "port": "genome"}


def test_ids_are_generated_checked_and_unique():
    ed = editor()
    assert ed.add_node("seqkit.stats")["node"] == "seqkit_stats_1"
    assert ed.add_node("seqkit.stats")["node"] == "seqkit_stats_2"
    assert not ed.add_node("seqkit.stats", id="seqkit_stats_1")["ok"]
    assert "letters, digits" in ed.add_node("seqkit.stats", id="bad id!")["error"]
    assert "unknown node type" in ed.add_node("nope.nothing")["error"]


def test_connect_checks_types_ports_loops_and_the_report_position():
    ed = small(editor())
    ed.add_node("blast.makeblastdb_aa", id="db")
    assert ed.connect("lambda.genome", "db.proteins")["error"].startswith("type mismatch")
    assert "has no output" in ed.connect("lambda.nothing", "genes.genome")["error"]
    assert "has no input" in ed.connect("lambda.genome", "genes.nothing")["error"]
    assert "node.port" in ed.connect("lambda", "genes.genome")["error"]
    assert "there is no node" in ed.connect("ghost.genome", "genes.genome")["error"]
    assert ed.connect("genes.proteins", "db.proteins")["ok"]
    ed.add_node("agent.report", id="rep")
    assert ed.connect("genes.proteins", "rep.evidence")["ok"]
    ed.add_node("agent.report", id="rep2")
    assert "must be last" in ed.connect("rep.report", "rep2.evidence")["error"]
    ed.add_node("seqkit.filter_length", id="f1", inputs={"sequences": "lambda.genome"})
    ed.add_node("seqkit.filter_length", id="f2", inputs={"sequences": "f1.sequences"})
    assert "loop" in ed.connect("f2.sequences", "f1.sequences")["error"]
    assert "own output" in ed.connect("f1.sequences", "f1.sequences")["error"]


def test_a_single_input_is_replaced_and_the_report_input_collects():
    ed = small(editor())
    ed.add_node("ncbi.genome", id="t7", params={"accessions": "NC_001604.1"})
    answer = ed.connect("t7.genome", "genes.genome")
    assert answer["ok"] and answer["replaced"] == "lambda.genome"
    ed.add_node("agent.report", id="rep")
    ed.connect("genes.proteins", "rep.evidence")
    ed.connect("t7.genome", "rep.evidence")
    assert ed.connect("t7.genome", "rep.evidence")["note"] == "already connected"
    assert [ref["node"] for ref in ed.nodes["rep"]["inputs"]["evidence"]] == ["genes", "t7"]
    assert ed.disconnect("rep.evidence", "t7")["ok"]
    assert [ref["node"] for ref in ed.nodes["rep"]["inputs"]["evidence"]] == ["genes"]
    assert ed.disconnect("rep.evidence")["ok"] and "evidence" not in ed.nodes["rep"]["inputs"]
    assert not ed.disconnect("rep.evidence")["ok"]


def test_set_params_coerces_clamps_resets_and_explains():
    ed = editor()
    ed.add_node("blast.blastn", id="bn")
    assert ed.set_params("bn", {"task": "dc-megablast", "evalue": "1e-5"})["set"] == {"task": "dc-megablast", "evalue": 1e-5}
    assert "must be one of" in ed.set_params("bn", {"task": "fast"})["error"]
    assert "at most" in ed.set_params("bn", {"perc_identity": 150})["error"]
    assert "has no parameter" in ed.set_params("bn", {"threads": 4})["error"]
    assert ed.set_params("bn", {"evalue": None})["set"] == {"evalue": 10}
    mixed = ed.set_params("bn", {"evalue": 0.01, "task": "nonsense"})
    assert mixed["ok"] and mixed["set"] == {"evalue": 0.01} and "problems" in mixed
    assert not ed.set_params("bn", {})["ok"]


def test_input_files_must_be_uploaded_ones():
    ed = editor()
    ed.add_node("input.genome", id="g")
    assert ed.set_params("g", {"path": "lambda.fasta"})["set"] == {"path": "uploads/ab12cd34_lambda.fasta"}
    assert ed.set_params("g", {"path": "uploads/ab12cd34_lambda.fasta"})["ok"]
    missing = ed.set_params("g", {"path": "/etc/passwd"})
    assert not missing["ok"] and "must upload" in missing["error"]


def test_remove_and_label_and_tidy():
    ed = small(editor())
    assert ed.set_label("genes", "  lambda genes ")["label"] == "lambda genes"
    assert ed.remove_node("lambda")["ok"]
    assert ed.nodes["genes"]["inputs"] == {} and "lambda" not in ed.graph["layout"]
    assert "there is no node" in ed.remove_node("lambda")["error"]
    small_ = small(editor())
    layout = layered_layout(small_.graph)
    assert layout["genes"]["x"] > layout["lambda"]["x"]
    assert small_.tidy()["ok"]


def test_validate_reports_what_the_run_button_would():
    ed = editor()
    ed.add_node("input.genome", id="g")
    ed.add_node("seqkit.stats", id="s", inputs={"sequences": "g.file"})
    check = ed.validate()
    assert not check["ready_to_run"] and any(item["code"] == "missing_param" for item in check["errors"])
    ed.set_params("g", {"path": "lambda.fasta"})
    assert ed.validate()["ready_to_run"]
    assert validate_graph(ed.graph, for_run=True).ok


def test_the_working_copy_does_not_touch_the_original():
    original = {"schema": 1, "nodes": {}, "layout": {}}
    ed = GraphEditor(original)
    ed.add_node("seqkit.stats")
    assert original["nodes"] == {} and ed.changed


def test_tool_schemas_are_complete_and_the_catalog_lists_every_node():
    names = {item["function"]["name"] for item in tool_schemas()}
    assert names == {"add_node", "remove_node", "connect", "disconnect", "set_params", "set_label", "tidy_layout", "validate", "read_output", "request_run"}
    text = catalog_text()
    for spec in NODE_SPECS:
        assert spec.type in text, spec.type
    assert "evalue" in text and "How to read it" in text and len(text) < 60000


def test_state_text_describes_graph_selection_and_problems():
    ed = small(editor())
    ed.set_params("genes", {"mode": "single"})
    text = state_text(name="Demo", graph=ed.graph, files=ed.files, selected="genes", run=None)
    assert "genes [prodigal.predict]" in text and "genome<-lambda.genome" in text and "mode=single" in text
    assert "selected node genes" in text and "No run yet" in text
    assert "The canvas is empty." == describe_graph({"nodes": {}})
    assert split_ref("a.b") == ("a", "b") and split_ref("ab") is None
