"""PhageAgent MCP local-analysis tools: catalog and docker run plans for the user's machine."""

from __future__ import annotations

import json

import pytest

from app.services.phagescope_mcp import local_tools, store
from app.services.phagescope_mcp.tools import ToolInputError

from .test_phagescope_mcp import _call, _rpc, mcp_client  # noqa: F401


def test_catalog_has_docker_tools_and_valid_workflows():
    cat = local_tools.catalog()
    assert {"genomad", "checkv", "prodigal", "megahit"} <= set(cat["tools"])
    assert all(entry["image"] for entry in cat["tools"].values())
    assert "phabox2" not in cat["tools"]  # native-only, no image
    for workflow in cat["workflows"].values():
        for step in workflow["steps"]:
            assert step["operation"] in cat["tools"][step["tool"]]["operations"], step


def test_local_packages_and_overrides_merge(monkeypatch, tmp_path):
    extra = {
        "packages": {
            "pharokka": {
                "image": "example.org/pharokka:1.7",
                "description": "Phage annotation",
                "category": "annotation",
                "operations": {
                    "run": {
                        "command": "pharokka.py -i {input} -o /work/{output}",
                        "extra_params": {"output": {"required": True}},
                    }
                },
            }
        },
        "overrides": {"checkv": {"image": "example.org/checkv:2"}},
        "workflows": {},
    }
    path = tmp_path / "local_tools.json"
    path.write_text(json.dumps(extra), encoding="utf-8")
    monkeypatch.setattr(local_tools, "LOCAL_CONFIG", path)
    local_tools.catalog.cache_clear()
    try:
        tools = local_tools.catalog()["tools"]
        assert tools["pharokka"]["source"] == "phageagent_package"
        assert tools["checkv"]["image"] == "example.org/checkv:2"
    finally:
        local_tools.catalog.cache_clear()


async def test_plan_chains_workflow_steps_with_databases_and_warnings():
    plan = await local_tools._plan_local_analysis(
        {"workflow": "phage_discovery", "input": "contigs.fna.gz", "resources": {"cpus": 2, "memory_gb": 8}}
    )
    first, second = plan["steps"]
    assert first["command"].startswith("docker run --rm")
    assert '-v "$PWD":/work' in first["command"]
    assert '"$PHAGEAGENT_DB/genomad/genomad_db":/work/database' in first["command"]
    assert "genomad end-to-end contigs.fna.gz /work/genomad_out /work/database" in first["command"]
    assert "genomad_out/contigs_summary/contigs_virus.fna" in second["command"]
    assert second["command"].endswith("-t 2")
    assert "docker pull quay.io/biocontainers/checkv:1.0.1--pyhdfd78af_0" in plan["setup"]
    assert any("checkv download_database /db" in cmd for cmd in plan["setup"])
    assert any("checkv" in w and "32 GB" in w for w in plan["warnings"])
    assert "check_first" not in plan


async def test_plan_custom_steps_quote_values_and_flag_missing_params():
    plan = await local_tools._plan_local_analysis(
        {"steps": [{"tool": "nanoplot", "operation": "basic", "input": "my reads.fq", "params": {"output": "qc"}}]}
    )
    (step,) = plan["steps"]
    assert "NanoPlot --fastq 'my reads.fq' -o /work/qc --prefix <prefix>" in step["command"]
    assert set(step["fill_in"]) == {"prefix"}
    assert plan["check_first"]


async def test_plan_gpu_tool_and_missing_docker():
    plan = await local_tools._plan_local_analysis(
        {
            "steps": [
                {
                    "tool": "dorado",
                    "operation": "basecall",
                    "input": "pod5",
                    "params": {"model": "sup", "kit": "SQK-NBD114-24", "output": "calls"},
                }
            ],
            "resources": {"gpu": False, "docker": False},
        }
    )
    assert "--gpus all" in plan["steps"][0]["command"]
    assert any("GPU" in w for w in plan["warnings"])
    assert any("Docker is required" in w for w in plan["warnings"])


@pytest.mark.parametrize(
    "args",
    [
        {},
        {"workflow": "phage_quality", "steps": [{"tool": "checkv", "operation": "end_to_end"}]},
        {"workflow": "nope"},
        {"steps": [{"tool": "nope", "operation": "x"}]},
        {"steps": [{"tool": "checkv", "operation": "nope"}]},
        {"steps": [{"tool": "checkv", "operation": "end_to_end", "params": {"rm": "-rf"}}]},
    ],
)
async def test_plan_rejects_bad_arguments(args):
    with pytest.raises(ToolInputError):
        await local_tools._plan_local_analysis(args)


def test_local_tools_over_mcp(mcp_client):
    client, fake = mcp_client
    token, _ = store.issue_token("u1", name="t")
    listed = _rpc(client, token, "tools/list").json()["result"]["tools"]
    names = {tool["name"] for tool in listed}
    assert {"list_local_tools", "plan_local_analysis"} <= names

    result, payload = _call(client, token, "list_local_tools", {"category": "phage"})
    assert result["isError"] is False
    assert payload["source"] == local_tools.SOURCE
    assert {row["tool"] for row in payload["data"]["tools"]} >= {"checkv", "genomad"}
    assert "phage_discovery" in payload["data"]["workflows"]

    _, detail = _call(client, token, "list_local_tools", {"tool": "checkv"})
    assert detail["data"]["tool"]["database"]["host_dir"] == "$PHAGEAGENT_DB/checkv/checkv-db-v1.5"

    result, payload = _call(client, token, "plan_local_analysis", {"workflow": "phage_quality", "input": "phages.fna"})
    assert result["isError"] is False
    assert "checkv end_to_end phages.fna /work/checkv_out" in payload["data"]["steps"][0]["command"]

    result, payload = _call(client, token, "plan_local_analysis", {"workflow": "nope"})
    assert result["isError"] is True
    assert payload["error"]["code"] == "invalid_arguments"
    assert fake.calls == []
