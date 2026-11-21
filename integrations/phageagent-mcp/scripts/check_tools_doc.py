#!/usr/bin/env python3
"""Check that PhageAgent MCP docs list exactly the tools the server serves.

    python scripts/check_tools_doc.py --url https://phageagent.deepomics.org/mcp README.md

Reads `tools/list` from --url (no token needed while anonymous discovery is
on; otherwise pass --token or set PHAGEAGENT_MCP_TOKEN) and compares it, by
name, with the tool names between the `<!-- tools:begin -->` and
`<!-- tools:end -->` markers of each doc (a path or an http(s) URL): the first
backticked name of each table row or list item. Every
"<N> tools" phrase in a doc must also equal the served count. Exits 1 on any
difference. Standard library only, so it runs on any release host or CI.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from typing import Iterable, Optional

BEGIN, END = "<!-- tools:begin -->", "<!-- tools:end -->"
_ROW = re.compile(r"^\s*(?:\||[-*])\s*`([a-z][a-z0-9_]*)`", re.M)
_COUNT = re.compile(r"\b(\d+) tools\b")
PROTOCOL = "2025-11-25"


def doc_tool_names(text: str) -> Optional[list[str]]:
    """Tool names of the rows inside the marker block, in order; None without a block."""
    start, end = text.find(BEGIN), text.find(END)
    if start < 0 or end < start:
        return None
    return _ROW.findall(text[start + len(BEGIN) : end])


def compare(served: Iterable[str], docs: dict[str, str]) -> list[str]:
    """Differences between the served tool names and each doc, as readable lines."""
    served_set = set(served)
    failures: list[str] = []
    for label, text in docs.items():
        names = doc_tool_names(text)
        if names is None:
            failures.append(f"{label}: no {BEGIN} ... {END} block")
            continue
        listed = set(names)
        if missing := sorted(served_set - listed):
            failures.append(f"{label}: served but not documented: {', '.join(missing)}")
        if extra := sorted(listed - served_set):
            failures.append(f"{label}: documented but not served: {', '.join(extra)}")
        if dupes := sorted({n for n in names if names.count(n) > 1}):
            failures.append(f"{label}: listed more than once: {', '.join(dupes)}")
        for count in sorted({int(c) for c in _COUNT.findall(text)} - {len(served_set)}):
            failures.append(f"{label}: says {count} tools, server lists {len(served_set)}")
    return failures


def _post(url: str, token: str, body: dict, session: Optional[str]) -> tuple[dict, Optional[str]]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": PROTOCOL,
        "User-Agent": "phageagent-check-tools-doc",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if session:
        headers["Mcp-Session-Id"] = session
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read().decode()
        session = resp.headers.get("Mcp-Session-Id") or session
    if raw.lstrip().startswith("{"):
        return json.loads(raw), session
    for line in raw.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:]), session
    raise ValueError(f"unexpected tools/list response: {raw[:200]}")


def served_tools(url: str, token: str = "") -> list[str]:
    init = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": PROTOCOL, "capabilities": {}, "clientInfo": {"name": "check-tools-doc", "version": "1"}},
    }
    _, session = _post(url, token, init, None)
    names: list[str] = []
    cursor = None
    for rpc_id in range(2, 52):
        params = {"cursor": cursor} if cursor else {}
        reply, session = _post(url, token, {"jsonrpc": "2.0", "id": rpc_id, "method": "tools/list", "params": params}, session)
        if "error" in reply:
            raise RuntimeError(f"tools/list failed: {reply['error']}")
        names += [tool["name"] for tool in reply["result"]["tools"]]
        cursor = reply["result"].get("nextCursor")
        if not cursor:
            break
    return names


def _read(source: str) -> str:
    if source.startswith(("http://", "https://")):
        with urllib.request.urlopen(source, timeout=60) as resp:
            return resp.read().decode()
    with open(source, encoding="utf-8") as handle:
        return handle.read()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="https://phageagent.deepomics.org/mcp", help="MCP endpoint")
    parser.add_argument("--token", default=os.environ.get("PHAGEAGENT_MCP_TOKEN", ""))
    parser.add_argument("docs", nargs="+", help="doc paths or URLs")
    args = parser.parse_args(argv)
    served = served_tools(args.url, args.token)
    print(f"{args.url}: {len(served)} tools: {', '.join(served)}")
    failures = compare(served, {doc: _read(doc) for doc in args.docs})
    for failure in failures:
        print(f"FAIL: {failure}", file=sys.stderr)
    print("OK: docs match tools/list" if not failures else f"{len(failures)} difference(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
