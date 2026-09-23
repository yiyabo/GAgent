#!/usr/bin/env python3
"""skill_add.py — install an agent skill into the repo's ``skills/`` directory.

Sources (exactly one required):
    --git <url> [--path <subdir>]   clone (shallow, sparse) and take a skill dir
    --dir <local_dir>               copy a local skill directory

Options:
    --name <name>     override the installed skill name (default: frontmatter
                      ``name:`` or the source directory name)
    --yes             skip the interactive confirmation
    --dry-run         print the risk report and planned actions, write nothing

The installer preserves the full relative structure of the skill directory
(SKILL.md plus scripts/, references/, assets/, agents/ and anything else),
runs a structural validation, prints a risk report (executables, network
hints, dependency instructions, name conflicts), and only then writes into
``skills/<name>/``. After installation, propagate with the normal git deploy
flow; with the hot-reload loader (SKILL_RELOAD_TTL_SECONDS) no container
restart is needed once the new tree reaches the host.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SKILLS_ROOT = PROJECT_ROOT / "skills"

_URL_RE = re.compile(r"https?://[^\s)\]>\"']+")
_DEP_RE = re.compile(r"(?:pip3?|conda|uv|apt(?:-get)?|brew|npm|npx)\s+(?:install|add)\b", re.IGNORECASE)
_FRONTMATTER_NAME_RE = re.compile(r"^name\s*:\s*(?P<name>\S+)\s*$", re.MULTILINE)


@dataclass
class RiskReport:
    skill_name: str
    file_count: int = 0
    total_bytes: int = 0
    executables: list[str] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    dependency_hints: list[str] = field(default_factory=list)
    name_conflict: bool = False
    notes: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            f"Skill: {self.skill_name}",
            f"  files: {self.file_count} ({self.total_bytes / 1024:.1f} KiB)",
        ]
        if self.executables:
            lines.append(f"  executables (scripts/ etc.): {', '.join(self.executables)}")
        if self.urls:
            lines.append(f"  external URLs referenced: {len(self.urls)}")
            for url in self.urls[:5]:
                lines.append(f"    - {url}")
        if self.dependency_hints:
            lines.append("  dependency-install instructions found:")
            for hint in self.dependency_hints[:5]:
                lines.append(f"    - {hint}")
        if self.name_conflict:
            lines.append("  WARNING: skills/<name> already exists — install would OVERWRITE it")
        for note in self.notes:
            lines.append(f"  note: {note}")
        return "\n".join(lines)


def _parse_frontmatter_name(skill_md: Path) -> str | None:
    try:
        text = skill_md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end < 0:
        return None
    match = _FRONTMATTER_NAME_RE.search(text[3:end])
    return match.group("name") if match else None


def _validate_skill_dir(skill_dir: Path) -> list[str]:
    problems: list[str] = []
    if not skill_dir.is_dir():
        return [f"not a directory: {skill_dir}"]
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        problems.append("SKILL.md missing")
    elif not _parse_frontmatter_name(skill_md):
        problems.append("SKILL.md frontmatter has no 'name:' field")
    config = skill_dir / "config.json"
    if config.is_file():
        import json

        try:
            data = json.loads(config.read_text(encoding="utf-8"))
            if data.get("version", 1) != 1:
                problems.append("config.json version must be 1")
        except (ValueError, OSError) as exc:
            problems.append(f"config.json unreadable: {exc}")
    return problems


def _collect_risks(skill_dir: Path, skill_name: str, target: Path) -> RiskReport:
    report = RiskReport(skill_name=skill_name, name_conflict=target.exists())
    for path in sorted(skill_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(skill_dir).as_posix()
        report.file_count += 1
        try:
            size = path.stat().st_size
        except OSError:
            continue
        report.total_bytes += size
        if path.name != "SKILL.md" and (
            path.suffix.lower() in {".py", ".sh", ".js", ".ts", ".r", ".pl", ".rb"}
            or rel.split("/", 1)[0] in {"scripts", "agents", "bin"}
        ):
            report.executables.append(rel)
        if path.suffix.lower() in {".md", ".txt", ".json", ".yaml", ".yml", ".toml"} and size <= 2_000_000:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            report.urls.extend(_URL_RE.findall(text))
            for line in text.splitlines():
                if _DEP_RE.search(line):
                    report.dependency_hints.append(f"{rel}: {line.strip()[:100]}")
    report.urls = sorted(set(report.urls))
    if report.file_count == 1:
        report.notes.append("single-file skill (SKILL.md only, no companion files)")
    if report.total_bytes > 5_000_000:
        report.notes.append("large payload — git bundle deploys carry this every time")
    return report


def _copy_skill(skill_dir: Path, target: Path) -> int:
    if target.exists():
        shutil.rmtree(target)
    count = 0
    for path in sorted(skill_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(skill_dir)
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        with path.open("rb") as src, dest.open("wb") as dst:
            shutil.copyfileobj(src, dst)
        count += 1
    return count


def _resolve_git_source(url: str, subpath: str | None) -> tuple[Path, Path]:
    tmp = Path(tempfile.mkdtemp(prefix="skill_add_"))
    clone_argv = ["git", "clone", "--depth", "1", "--filter=blob:none", "--sparse", url, str(tmp)]
    proc = subprocess.run(clone_argv, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        raise SystemExit(f"git clone failed: {proc.stderr.strip()[:400]}")
    if subpath:
        sparse_argv = ["git", "-C", str(tmp), "sparse-checkout", "set", subpath]
        proc = subprocess.run(sparse_argv, capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            shutil.rmtree(tmp, ignore_errors=True)
            raise SystemExit(f"sparse-checkout failed: {proc.stderr.strip()[:400]}")
        candidate = tmp / subpath
    else:
        candidate = tmp
    return candidate, tmp


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install an agent skill into repo skills/.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--git", metavar="URL", help="git repo URL containing the skill")
    source.add_argument("--dir", metavar="DIR", help="local skill directory")
    parser.add_argument("--path", metavar="SUBDIR", default=None, help="skill subdirectory inside the git repo")
    parser.add_argument("--name", metavar="NAME", default=None, help="override installed skill name")
    parser.add_argument("--yes", action="store_true", help="skip confirmation")
    parser.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    args = parser.parse_args(argv)

    tmp: Path | None = None
    try:
        if args.git:
            skill_dir, tmp = _resolve_git_source(args.git, args.path)
        else:
            skill_dir = Path(args.dir).expanduser().resolve()

        problems = _validate_skill_dir(skill_dir)
        if problems:
            for problem in problems:
                print(f"ERROR: {problem}", file=sys.stderr)
            return 2

        skill_name = (
            args.name
            or _parse_frontmatter_name(skill_dir / "SKILL.md")
            or skill_dir.name
        )
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", skill_name):
            print(f"ERROR: invalid skill name {skill_name!r}", file=sys.stderr)
            return 2

        target = SKILLS_ROOT / skill_name
        report = _collect_risks(skill_dir, skill_name, target)
        print("=== Skill install risk report ===")
        print(report.render())
        print(f"Source: {skill_dir}")
        print(f"Target: {target}")

        if args.dry_run:
            print("dry-run: nothing written.")
            return 0

        if not args.yes:
            answer = input("Proceed with installation? [y/N] ").strip().lower()
            if answer not in {"y", "yes"}:
                print("aborted.")
                return 1

        SKILLS_ROOT.mkdir(parents=True, exist_ok=True)
        copied = _copy_skill(skill_dir, target)
        print(f"installed {copied} file(s) into {target}")
        print("next steps: git add skills/ && commit && push && deploy bundle "
              "(hot-reload picks it up; pre-hot-reload builds need a container restart).")
        return 0
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
