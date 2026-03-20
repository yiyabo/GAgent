#!/usr/bin/env python3
"""Probe bio_tools help: (1) local JSON catalog via bio_tools_handler, (2) remote docker --help per CLI.

Uses BIO_TOOLS_* env (same as production). Run from repo root::

    python scripts/bio_tools_remote_help_probe.py

Requires: network, ssh/scp/sshpass as configured, remote docker.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shlex
import sys
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

# Repo root
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _cli_tokens_from_config(tool_cfg: Dict[str, Any]) -> Set[str]:
    """First token of each operation command (executable path or name in container)."""
    tokens: Set[str] = set()
    for _op_name, op in (tool_cfg.get("operations") or {}).items():
        cmd = (op.get("command") or "").strip()
        if not cmd:
            continue
        first = cmd.split(None, 1)[0]
        if not first or first.startswith("{"):
            continue
        if first in {"bash", "sh", "python", "python3", "Rscript"}:
            continue
        # e.g. /opt/dorado-.../bin/dorado — use full path for docker run
        if first.startswith("/"):
            tokens.add(first)
            continue
        if "/" in first:
            continue
        tokens.add(first)
    return tokens


def _load_config() -> Dict[str, Any]:
    path = ROOT / "tool_box" / "bio_tools" / "tools_config.json"
    return json.loads(path.read_text(encoding="utf-8"))


async def _local_json_help(
    tool_names: List[str],
) -> Tuple[int, int, List[Tuple[str, str]]]:
    from tool_box.bio_tools.bio_tools_handler import bio_tools_handler

    ok = 0
    fail = 0
    errors: List[Tuple[str, str]] = []
    total = len(tool_names)
    for i, name in enumerate(tool_names, 1):
        print(f"\r[local JSON help] {i}/{total} {name}...", end="", flush=True)
        r = await bio_tools_handler(tool_name=name, operation="help", timeout=60)
        if r.get("success"):
            ok += 1
        else:
            fail += 1
            errors.append((name, str(r.get("error") or r)))
    print()
    return ok, fail, errors


async def _remote_docker_help(
    *,
    config: Any,
    auth: Any,
    pairs: List[Tuple[str, str, str]],
    per_call_timeout: int,
) -> Tuple[int, int, List[Tuple[str, str, str, str]]]:
    from tool_box.bio_tools.remote_executor import execute_remote_command

    ok = 0
    fail = 0
    errors: List[Tuple[str, str, str, str]] = []
    total = len(pairs)
    for i, (tool, image, binary) in enumerate(pairs, 1):
        print(f"\r[remote docker] {i}/{total} {tool} :: {binary}...", end="", flush=True)
        img_q = shlex.quote(image)
        bin_q = shlex.quote(binary)
        cmd = f"timeout {per_call_timeout} docker run --rm {img_q} {bin_q} --help"
        r = await execute_remote_command(
            config,
            auth,
            cmd,
            timeout=per_call_timeout + 90,
        )
        out = (r.get("stdout") or "") + (r.get("stderr") or "")
        low = out.lower()
        image_pull_fail = any(
            x in low
            for x in (
                "unable to find image",
                "pull access denied",
                "repository does not exist",
                "manifest unknown",
            )
        )
        success = bool(r.get("success")) or (
            len(out.strip()) > 60
            and not image_pull_fail
            and ("usage" in low or "help" in low or "options" in low or "version" in low)
        )
        if success:
            ok += 1
        else:
            fail += 1
            tail = (r.get("stderr") or r.get("stdout") or str(r.get("error")))[:400]
            errors.append((tool, image, binary, tail))
    print()
    return ok, fail, errors


async def main_async(args: argparse.Namespace) -> int:
    from tool_box.bio_tools.remote_executor import RemoteExecutionConfig, resolve_auth

    cfg = _load_config()
    if args.tools:
        tool_names = [t.strip() for t in args.tools.split(",") if t.strip()]
        unknown = [t for t in tool_names if t not in cfg]
        if unknown:
            print("Unknown tools:", unknown, file=sys.stderr)
            return 2
    else:
        tool_names = sorted(cfg.keys())
    print(f"tools_config.json: {len(tool_names)} tools")

    loc_ok, loc_fail, loc_err = await _local_json_help(tool_names)
    print(f"Local bio_tools_handler(operation=help): OK={loc_ok} FAIL={loc_fail}")
    for name, msg in loc_err[:8]:
        print(f"  - {name}: {msg[:200]}")
    if len(loc_err) > 8:
        print(f"  ... +{len(loc_err) - 8} more")

    if args.json_only:
        return 0 if loc_fail == 0 else 1

    remote_cfg = RemoteExecutionConfig.from_env()
    miss = remote_cfg.missing_required()
    if miss:
        print("Remote probe skipped: incomplete RemoteExecutionConfig:", miss)
        return 0 if loc_fail == 0 else 1

    print(
        f"Resolving SSH auth -> {remote_cfg.user}@{remote_cfg.host}:{remote_cfg.port} ..."
    )
    auth = await resolve_auth(remote_cfg)

    pairs: List[Tuple[str, str, str]] = []
    for tool in tool_names:
        info = cfg[tool]
        image = (info.get("image") or "").strip()
        if not image:
            continue
        binaries = sorted(_cli_tokens_from_config(info))
        if not binaries:
            continue
        # One representative binary per tool keeps full-matrix runtime reasonable.
        pairs.append((tool, image, binaries[0]))

    print(f"Remote docker --help probes: {len(pairs)} (tool × binary)")
    rem_ok, rem_fail, rem_err = await _remote_docker_help(
        config=remote_cfg,
        auth=auth,
        pairs=pairs,
        per_call_timeout=args.timeout,
    )
    print(f"Remote docker run ... --help: OK={rem_ok} FAIL={rem_fail}")
    for tool, image, binary, tail in rem_err[:12]:
        print(f"  - {tool} / {binary} ({image[:48]}...): {tail[:180]}")
    if len(rem_err) > 12:
        print(f"  ... +{len(rem_err) - 12} more")

    return 0 if loc_fail == 0 and rem_fail == 0 else 1


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--json-only",
        action="store_true",
        help="Only verify local JSON help (no SSH/docker).",
    )
    p.add_argument(
        "--timeout",
        type=int,
        default=240,
        help="Per-tool docker run timeout on remote (seconds).",
    )
    p.add_argument(
        "--tools",
        type=str,
        default="",
        help="Comma-separated tool names (default: all in tools_config.json).",
    )
    args = p.parse_args()
    try:
        code = asyncio.run(main_async(args))
    except KeyboardInterrupt:
        code = 130
    raise SystemExit(code)


if __name__ == "__main__":
    main()
