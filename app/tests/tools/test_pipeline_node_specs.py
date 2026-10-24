"""Node types stay in step with tools_config.json, and the catalog is complete for the editor."""

from __future__ import annotations

import json

import pytest

from app.services.pipelines.node_specs import DATA_TYPES, NODE_SPECS, public_catalog
from tool_box.bio_tools import get_tools_config

TOOL_SPECS = [spec for spec in NODE_SPECS if spec.kind == "tool"]


@pytest.mark.parametrize("spec", TOOL_SPECS, ids=lambda spec: spec.type)
def test_every_tool_node_matches_its_bio_tools_operation(spec):
    config = get_tools_config()
    assert spec.tool in config, spec.type
    operation = config[spec.tool]["operations"].get(spec.operation)
    assert operation is not None, f"{spec.type}: {spec.tool} has no operation {spec.operation}"
    declared = set((operation.get("extra_params") or {}).keys())

    for name in spec.fixed_params:
        assert name in declared, f"{spec.type}: fixed parameter {name} is not declared by {spec.tool} {spec.operation}"
    for param in spec.params:
        assert param.name in declared, f"{spec.type}: parameter {param.name} is not declared"
    for port in spec.inputs:
        if port.param:
            assert port.param in declared, f"{spec.type}: input {port.name} feeds undeclared {port.param}"

    primary = [port for port in spec.inputs if port.param is None]
    if operation.get("requires_input"):
        assert len(primary) == 1, f"{spec.type}: the operation needs exactly one input_file port"
    else:
        assert not primary, f"{spec.type}: the operation takes no input_file"
    assert "native" in config[spec.tool], f"{spec.tool} cannot run natively"


@pytest.mark.parametrize("spec", TOOL_SPECS, ids=lambda spec: spec.type)
def test_tool_outputs_live_under_the_names_the_node_fixes(spec):
    fixed_values = {str(value) for value in spec.fixed_params.values()}
    for port in spec.outputs:
        assert port.type in DATA_TYPES
        if port.kind == "stdout":
            continue
        top = port.source.split("/", 1)[0]
        # Some tools take an output prefix and add their own suffixes (mmseqs easy-linclust: <prefix>_rep_seq.fasta).
        assert top in fixed_values or any(top.startswith(value + "_") for value in fixed_values), (
            f"{spec.type}: output {port.name} ({port.source}) is not under a fixed output name"
        )


def test_catalog_is_bilingual_serializable_and_has_one_terminal_report():
    catalog = public_catalog()
    json.dumps(catalog)
    types = [node["type"] for node in catalog["node_types"]]
    assert len(types) == len(set(types))
    for node in catalog["node_types"]:
        for text in (node["title"], node["description"]):
            assert text["en"].strip() and text["zh"].strip(), node["type"]
        for port in node["inputs"] + node["outputs"]:
            assert port["title"]["en"] and port["title"]["zh"], (node["type"], port["name"])
        for param in node["params"]:
            assert param["title"]["en"] and param["title"]["zh"], (node["type"], param["name"])
    terminal = [node["type"] for node in catalog["node_types"] if node["terminal"]]
    assert terminal == ["agent.report"]
    assert {item["id"] for item in catalog["categories"]} >= {node["category"] for node in catalog["node_types"]}
    assert len(TOOL_SPECS) >= 15
