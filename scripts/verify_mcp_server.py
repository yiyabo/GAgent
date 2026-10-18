#!/usr/bin/env python3
"""Check a catalog MCP server against what the catalog says about it.

    python scripts/verify_mcp_server.py fetch uniprot
    python scripts/verify_mcp_server.py --all [--json]

For each server it starts the real container exactly as the app would and checks:

  handshake      the server starts, completes MCP initialize, lists tools
  tools_match    the tools it advertises are the tools the catalog declares
  hardening      the live container has the sandbox profile (non-root, read-only
                 root, no capabilities, no mounts, pid/memory limits, our labels)
  environment    no secret-looking variables and no host variables inside it
  egress         the egress proxy refused nothing during the probe (this run only
                 lists tools; the refusal rules are covered by test_mcp_container)
  cleanup        the container is gone after the connection stops

Sampling (a server asking the host to run its model) is covered by
app/tests/tools/test_mcp_mount.py, not here.

It does NOT prove the server's code is safe; it proves the box we put it in
behaves as documented. Exit code 0 only when every check passed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.extensions.service import load_catalog  # noqa: E402
from tool_box.mcp_mount.connection import MountConnection  # noqa: E402
from tool_box.mcp_mount.egress import get_egress_proxy  # noqa: E402

SECRET_HINTS = ("KEY", "SECRET", "TOKEN", "PASSWORD", "PASSWD", "CREDENTIAL", "DASHSCOPE", "OPENAI", "ANTHROPIC")
EXPECTED_ENV = {"HOME", "PATH", "HOSTNAME", "PYTHONDONTWRITEBYTECODE", "PYTHONIOENCODING", "PYTHONUTF8", "PYTHONUNBUFFERED",
                "PIP_NO_CACHE_DIR", "PIP_DISABLE_PIP_VERSION_CHECK", "LANG", "GPG_KEY", "PYTHON_VERSION", "PYTHON_SHA256",
                "NODE_VERSION", "YARN_VERSION", "NODE_ENV", "NODE_USE_ENV_PROXY", "MCP_TRANSPORT_TYPE", "MCP_LOG_LEVEL",
                "GAGENT_MCP_ENDPOINT", "GAGENT_MCP_TOKEN", "HTTPS_PROXY", "https_proxy"}


def _docker(*args: str) -> str:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=60).stdout


def _check(name: str, passed: bool, detail: str) -> Dict[str, Any]:
    return {"name": name, "passed": bool(passed), "detail": detail}


def _hardening(info: Dict[str, Any]) -> List[Dict[str, Any]]:
    host, config = info["HostConfig"], info["Config"]
    problems = []
    if config.get("User") in ("", "0", "root", None):
        problems.append(f"runs as {config.get('User') or 'root'}")
    if not host.get("ReadonlyRootfs"):
        problems.append("root filesystem is writable")
    if host.get("CapDrop") != ["ALL"] or host.get("CapAdd"):
        problems.append(f"capabilities: drop={host.get('CapDrop')} add={host.get('CapAdd')}")
    if host.get("Privileged"):
        problems.append("privileged")
    if "no-new-privileges" not in (host.get("SecurityOpt") or []):
        problems.append("no-new-privileges missing")
    if host.get("Binds") or info.get("Mounts"):
        problems.append(f"mounts present: {[m['Destination'] for m in info.get('Mounts', [])]}")
    if not host.get("PidsLimit"):
        problems.append("no pid limit")
    if not host.get("Memory"):
        problems.append("no memory limit")
    if host.get("NetworkMode") in ("host",) or host.get("PortBindings"):
        problems.append(f"network={host.get('NetworkMode')} ports={host.get('PortBindings')}")
    if "gagent.kind" not in config.get("Labels", {}):
        problems.append("missing gagent.* labels")
    return [_check("hardening", not problems, "; ".join(problems) or "non-root, read-only root, caps dropped, no mounts, pid/memory limits, labelled")]


async def verify(server_id: str) -> List[Dict[str, Any]]:
    entry = load_catalog()[server_id]
    spec = entry.spec()
    proxy = get_egress_proxy()
    before_denied = len(proxy.denied)
    conn = MountConnection(spec)
    results: List[Dict[str, Any]] = []
    try:
        started = await conn.start()
        results.append(_check("handshake", started, "initialized and listed tools" if started else f"failed: {conn.error}"))
        if not started:
            return results
        live = sorted(t["name"] for t in conn.tools)
        declared = sorted(t["name"] for t in entry.raw["tools"])
        results.append(_check("tools_match", live == declared, f"live={live} declared={declared}" if live != declared else f"{len(live)} tools, as declared"))

        ids = _docker("ps", "-q", "--filter", f"label=gagent.mcp={server_id}").split()
        if len(ids) != 1:
            results.append(_check("hardening", False, f"expected 1 running container, found {len(ids)}"))
        else:
            info = json.loads(_docker("inspect", ids[0]))[0]
            results.extend(_hardening(info))
            env = dict(item.split("=", 1) for item in info["Config"]["Env"])
            # Names the base images ship (e.g. GPG_KEY, a public build-key id) are not ours to flag.
            secrets = [k for k in env if any(h in k.upper() for h in SECRET_HINTS) and k not in EXPECTED_ENV]
            extra = sorted(set(env) - EXPECTED_ENV - set(spec.env))
            results.append(_check("environment", not secrets and not extra, f"secret-like={secrets} unexpected={extra}" if secrets or extra else "only image defaults and the mount's own variables"))

        denied = proxy.denied[before_denied:]
        results.append(_check("egress", not denied, f"refused during probe: {denied}" if denied else f"proxy refused nothing; tunnels opened: {dict(proxy.allowed)}"))
    finally:
        await conn.stop()
        left = _docker("ps", "-aq", "--filter", f"label=gagent.mcp={server_id}").split()
        results.append(_check("cleanup", not left, f"{len(left)} container(s) left behind" if left else "container removed on stop"))
    return results


async def main_async(ids: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    out = {}
    for sid in ids:
        out[sid] = await verify(sid)
    await get_egress_proxy().stop()
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("servers", nargs="*")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    catalog = load_catalog()
    ids = sorted(catalog) if args.all else args.servers
    unknown = [i for i in ids if i not in catalog]
    if not ids or unknown:
        print(f"unknown or missing server ids: {unknown or '(none given)'}; catalog has {sorted(catalog)}", file=sys.stderr)
        return 2
    report = asyncio.run(main_async(ids))
    ok = all(c["passed"] for checks in report.values() for c in checks)
    if args.json:
        print(json.dumps({"ok": ok, "servers": report}, ensure_ascii=False, indent=2))
    else:
        for sid, checks in report.items():
            print(f"== {sid}")
            for c in checks:
                print(f"  [{'PASS' if c['passed'] else 'FAIL'}] {c['name']:<12} {c['detail']}")
        print("ALL PASSED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
