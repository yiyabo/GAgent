#!/usr/bin/env python3
"""Admin password reset tool for GAgent local accounts.

Usage (on the server, conda env LLM):
    python scripts/admin_reset_password.py <email> [--password <new>] [--db <path>]

- Prints the new password to stdout (generated if not supplied) for recording
  in the account registry table.
- Only touches local (non-SSO) accounts by default; pass --include-sso to
  override (SSO users authenticate via the platform and rarely need this).
"""

from __future__ import annotations

import argparse
import secrets
import sqlite3
import string
import sys
from pathlib import Path

DEFAULT_DB = "/home/zczhao/Phage-Agent/data/databases/main/plan_registry.db"


def generate_password(length: int = 14) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def main() -> int:
    parser = argparse.ArgumentParser(description="Reset a GAgent local account password.")
    parser.add_argument("email", help="account email")
    parser.add_argument("--password", help="new password (generated if omitted)")
    parser.add_argument("--db", default=DEFAULT_DB, help="users database path")
    parser.add_argument("--include-sso", action="store_true", help="allow resetting SSO accounts")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.is_file():
        print(f"ERROR: database not found: {db_path}", file=sys.stderr)
        return 2

    from app.services.auth import hash_password

    con = sqlite3.connect(str(db_path))
    row = con.execute(
        "SELECT id, email, role, is_active, sso_enabled FROM users WHERE email = ?",
        (args.email.strip().lower(),),
    ).fetchone()
    if row is None:
        print(f"ERROR: no account with email {args.email}", file=sys.stderr)
        return 3
    user_id, email, role, is_active, sso_enabled = row
    if sso_enabled and not args.include_sso:
        print(
            f"ERROR: {email} is an SSO account (platform-managed password). "
            "Use --include-sso only if you really mean it.",
            file=sys.stderr,
        )
        return 4

    new_password = args.password or generate_password()
    con.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (hash_password(new_password), user_id),
    )
    con.commit()

    from app.services.auth import verify_password

    stored = con.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,)).fetchone()[0]
    if not verify_password(stored, new_password):
        print("ERROR: verification after update failed", file=sys.stderr)
        return 5

    print(f"OK reset password for {email} (role={role}, active={is_active})")
    print(f"NEW_PASSWORD={new_password}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
