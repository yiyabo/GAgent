#!/usr/bin/env python3
"""Operator CLI for PhageScope MCP tokens (allowlist issue, list, revoke, audit).

    python scripts/phagescope_mcp_tokens.py issue --user-id <id> --name codex
    python scripts/phagescope_mcp_tokens.py list [--user-id <id>]
    python scripts/phagescope_mcp_tokens.py revoke <token_id>
    python scripts/phagescope_mcp_tokens.py audit <token_id> [--limit 20]

The plaintext token is printed once by ``issue`` and never stored.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import init_db  # noqa: E402
from app.services.phagescope_mcp import store  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    issue = sub.add_parser("issue")
    issue.add_argument("--user-id", required=True)
    issue.add_argument("--name", default="default")
    issue.add_argument("--expires-in-days", type=int, default=90)
    listing = sub.add_parser("list")
    listing.add_argument("--user-id")
    revoke = sub.add_parser("revoke")
    revoke.add_argument("token_id")
    audit = sub.add_parser("audit")
    audit.add_argument("token_id")
    audit.add_argument("--limit", type=int, default=20)
    args = parser.parse_args(argv)

    init_db()
    if args.command == "issue":
        plaintext, record = store.issue_token(
            args.user_id, name=args.name, created_by="operator-cli", expires_in_days=args.expires_in_days or None
        )
        print(json.dumps({"token": plaintext, "record": record.public()}, indent=1))
    elif args.command == "list":
        rows = [
            {**t.public(), "bytes_used_today": store.bytes_used_today(t.id)} for t in store.list_tokens(args.user_id)
        ]
        print(json.dumps(rows, indent=1))
    elif args.command == "revoke":
        if not store.revoke_token(args.token_id):
            print(f"no active token {args.token_id}", file=sys.stderr)
            return 1
        print(f"revoked {args.token_id}")
    else:
        print(json.dumps(store.recent_audit(args.token_id, args.limit), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
