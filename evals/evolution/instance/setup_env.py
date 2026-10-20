#!/usr/bin/env python3
"""Derive the evaluation instance's .env from the live .env on 620b.

Run on the server (secrets never leave it and are never printed):

    ssh phage-server 'python3 -' < evals/evolution/instance/setup_env.py

Every line of ~/gagent/src/deploy/.env is copied unchanged except the keys
listed in OVERRIDES. The output is written with mode 0600 and only key names
are printed.
"""
import os
import re

HOME = os.path.expanduser("~")
SRC = os.path.join(HOME, "gagent/src/deploy/.env")
DST = os.path.join(HOME, "gagent-eval/deploy/.env")

OVERRIDES = {
    "GAGENT_TAG": "p11",
    "GAGENT_RUNTIME_DIR": os.path.join(HOME, "gagent-eval/runtime"),
    "GAGENT_BACKUP_DIR": os.path.join(HOME, "gagent-eval/backups"),
    "EVAL_OUT_DIR": os.path.join(HOME, "gagent-eval/evo-out"),
    # Isolation switches: no skill learning, no background quality scoring,
    # no AMEM memory service, no LightRAG gateway, no MCP extensions (these
    # reach external retrieval services). Live value of EXTENSIONS_MCP_ENABLED is 1.
    "SKILL_LEARNING_ENABLED": "0",
    "QUALITY_EVALUATION_ENABLED": "0",
    "AMEM_ENABLED": "0",
    "LIGHTRAG_GATEWAY_ENABLED": "0",
    "EXTENSIONS_MCP_ENABLED": "0",
}


def main() -> None:
    with open(SRC, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    out = []
    seen = set()
    for line in lines:
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=", line)
        if match and match.group(1) in OVERRIDES:
            key = match.group(1)
            out.append(f"{key}={OVERRIDES[key]}")
            seen.add(key)
        else:
            out.append(line)
    for key, value in OVERRIDES.items():
        if key not in seen:
            out.append(f"{key}={value}")
    tmp = DST + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    os.replace(tmp, DST)
    print("wrote", DST, "mode 0600; overridden keys:", ", ".join(sorted(OVERRIDES)))


if __name__ == "__main__":
    main()
