"""The public PhageAgent MCP docs list exactly the tools `/mcp` serves."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from app.services.phagescope_mcp import analysis, local_tools, tools

from .test_phagescope_mcp import ACCEPT, PROTOCOL, _setenv, mcp_client  # noqa: F401

ROOT = Path(__file__).resolve().parents[3]
DOCS = (
    ROOT / "integrations/phageagent-mcp/README.md",
    ROOT / "app/services/phagescope_mcp/README.md",
)
_spec = importlib.util.spec_from_file_location(
    "check_tools_doc", ROOT / "integrations/phageagent-mcp/scripts/check_tools_doc.py"
)
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)


def _served(client) -> list[str]:
    headers = dict(ACCEPT, **{"MCP-Protocol-Version": PROTOCOL})
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    resp = client.post("/mcp", headers=headers, content=json.dumps(body))
    assert resp.status_code == 200, resp.text
    return [tool["name"] for tool in resp.json()["result"]["tools"]]


def test_public_docs_match_tools_list(mcp_client, monkeypatch):
    client, _ = mcp_client
    for name in ("PHAGESCOPE_MCP_ANON_DISCOVERY", "PHAGESCOPE_MCP_ANALYSIS_ENABLED", "PHAGESCOPE_MCP_OPEN_ACCESS"):
        _setenv(monkeypatch, name, "1")
    served = _served(client)
    assert set(served) == set(tools.TOOLS_BY_NAME) | set(local_tools.LOCAL_TOOLS_BY_NAME) | set(analysis.ANALYSIS_TOOLS_BY_NAME)
    docs = {str(path.relative_to(ROOT)): path.read_text(encoding="utf-8") for path in DOCS}
    assert check.compare(served, docs) == []


def test_check_reports_missing_extra_duplicate_and_count():
    doc = "All 3 tools.\n<!-- tools:begin -->\n| `a` | uses `b_arg` |\n- `c`\n- `c`\n<!-- tools:end -->\n"
    assert check.compare(["a", "b"], {"doc": doc}) == [
        "doc: served but not documented: b",
        "doc: documented but not served: c",
        "doc: listed more than once: c",
        "doc: says 3 tools, server lists 2",
    ]
    assert check.compare(["a"], {"doc": "no block"}) == ["doc: no <!-- tools:begin --> ... <!-- tools:end --> block"]
