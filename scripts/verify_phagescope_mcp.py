#!/usr/bin/env python3
"""End-to-end check of a PhageScope MCP endpoint with the official MCP client.

    python scripts/verify_phagescope_mcp.py --url http://127.0.0.1:9000/mcp --token gpsk_...

Runs initialize, tools/list, resources/list+read and a set of read-only tool
calls, then checks that a missing token and a bad token are refused. Exits 1
on any failure. The token may also come from PHAGEAGENT_MCP_TOKEN.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time

import httpx2
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

EXPECTED_TOOLS = {
    "catalog_overview",
    "search_phages",
    "cohort_report",
    "aggregate_phages",
    "get_phage",
    "get_sequence",
    "search_proteins",
    "search_families",
    "family_members",
    "query_features",
    "get_structures",
}


async def _session_calls(url: str, token: str) -> list[str]:
    failures: list[str] = []
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx2.AsyncClient(headers=headers, timeout=120) as http:
        async with streamable_http_client(url, http_client=http) as streams:
            read, write = streams[0], streams[1]
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                print(f"initialize: server={init.server_info.name} protocol={init.protocol_version}")
                tools = {t.name: t for t in (await session.list_tools()).tools}
                missing = EXPECTED_TOOLS - set(tools)
                print(f"tools/list: {len(tools)} tools")
                if missing:
                    failures.append(f"missing tools: {sorted(missing)}")
                if not all(t.annotations and t.annotations.read_only_hint for t in tools.values()):
                    failures.append("a tool is not annotated read-only")
                resources = (await session.list_resources()).resources
                if resources:
                    body = await session.read_resource(resources[0].uri)
                    print(f"resources/read: {resources[0].uri} {len(body.contents[0].text)} chars")

                async def call(name: str, args: dict, expect_error: bool = False) -> dict:
                    started = time.monotonic()
                    result = await session.call_tool(name, args)
                    text = result.content[0].text if result.content else ""
                    payload = json.loads(text) if text.startswith("{") else {"raw": text}
                    ms = int((time.monotonic() - started) * 1000)
                    summary = payload.get("error") or payload.get("limits")
                    print(f"tools/call {name}: is_error={result.is_error} {len(text)}B {ms}ms {summary}")
                    if bool(result.is_error) != expect_error:
                        failures.append(f"{name} is_error={result.is_error}: {text[:300]}")
                    return payload

                await call("catalog_overview", {})
                found = await call("search_phages", {"filters": {"host": "Salmonella"}, "pagesize": 3})
                await call("cohort_report", {"filters": {"host": "Salmonella", "lifestyle": "virulent"}})
                await call("aggregate_phages", {"group_by": "lifestyle", "filters": {"host": "Salmonella"}})
                rows = (found.get("data") or {}).get("results") or []
                if rows:
                    phage_id = rows[0].get("id") or rows[0].get("phage_id")
                    await call("get_phage", {"phage_id": phage_id})
                    await call("get_sequence", {"phage_id": phage_id, "max_bases": 500})
                    await call("search_proteins", {"phage_id": phage_id, "pagesize": 3})
                    await call("query_features", {"feature_type": "trna", "phage_id": phage_id})
                families = await call("search_families", {"keyword": "endolysin", "pagesize": 3})
                fam_rows = (families.get("data") or {}).get("results") or []
                if fam_rows and fam_rows[0].get("family_id"):
                    await call("family_members", {"family_id": fam_rows[0]["family_id"], "pagesize": 3})
                await call("search_phages", {"filters": {}}, expect_error=True)
    return failures


async def _refusals(url: str) -> list[str]:
    failures = []
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    async with httpx2.AsyncClient(timeout=30) as http:
        for label, extra in (("no token", {}), ("bad token", {"Authorization": "Bearer gpsk_invalid"})):
            resp = await http.post(url, json=body, headers={**headers, **extra})
            print(f"refusal {label}: HTTP {resp.status_code}")
            if resp.status_code != 401:
                failures.append(f"{label} returned {resp.status_code}")
    return failures


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--token", default=os.environ.get("PHAGEAGENT_MCP_TOKEN", ""))
    args = parser.parse_args()
    if not args.token:
        parser.error("--token or PHAGEAGENT_MCP_TOKEN is required")
    failures = await _session_calls(args.url, args.token) + await _refusals(args.url)
    for failure in failures:
        print(f"FAIL: {failure}", file=sys.stderr)
    print("OK" if not failures else f"{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
