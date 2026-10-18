#!/usr/bin/env python3
"""vet_skill.py — static vetting of agent skill directories.

Usage:
    python scripts/vet_skill.py skills/<name> [skills/<other> ...] [--json]
    python scripts/vet_skill.py --all skills/ [--json]

Exit status is 0 when no scanned skill has a blocking finding, 1 otherwise
(2 on usage errors). Nothing inside a skill is executed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.skills.vetting import SCANNER_VERSION, scan_skill_dir  # noqa: E402


def _collect_dirs(paths: list[str], all_roots: list[str]) -> list[Path]:
    dirs: list[Path] = [Path(p) for p in paths]
    for root in all_roots:
        base = Path(root)
        if not base.is_dir():
            raise SystemExit(f"--all: {root} is not a directory")
        dirs.extend(sorted(child for child in base.iterdir() if child.is_dir() and not child.name.startswith(".")))
    return dirs


def _render_text(name: str, result: dict) -> str:
    stats = result["stats"]
    lines = [
        f"{name}: {result['status'].upper()}  "
        f"(SKILL.md {stats['skill_md_lines']} lines, {stats['files']} files, "
        f"{stats['scripts']} scripts, {len(stats['external_urls'])} external URLs)"
    ]
    for finding in result["findings"]:
        if finding["severity"] == "info" and finding["rule"] == "net.url":
            continue
        where = finding["file"] + (f":{finding['line']}" if finding["line"] else "")
        lines.append(f"  [{finding['severity']:5}] {finding['rule']:<24} {where}  {finding['detail']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Statically vet agent skill directories.")
    parser.add_argument("paths", nargs="*", help="skill directories (each contains SKILL.md)")
    parser.add_argument("--all", dest="all_roots", action="append", default=[], metavar="DIR",
                        help="scan every immediate subdirectory of DIR")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    args = parser.parse_args(argv)

    dirs = _collect_dirs(args.paths, args.all_roots)
    if not dirs:
        parser.error("give at least one skill directory or --all DIR")

    results = {path.name: scan_skill_dir(path) for path in dirs}
    ok = all(result["ok"] for result in results.values())

    if args.json:
        summary = {
            "scanner_version": SCANNER_VERSION,
            "ok": ok,
            "counts": {
                status: sum(1 for r in results.values() if r["status"] == status)
                for status in ("passed", "warnings", "blocked")
            },
            "skills": results,
        }
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        for name, result in results.items():
            print(_render_text(name, result))
        counts = {s: sum(1 for r in results.values() if r["status"] == s) for s in ("passed", "warnings", "blocked")}
        print(f"\nscanner v{SCANNER_VERSION}: {len(results)} skill(s) — "
              f"{counts['passed']} passed, {counts['warnings']} with warnings, {counts['blocked']} blocked")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
