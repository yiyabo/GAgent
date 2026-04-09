"""Shared runtime guardrails for local and CLI code execution paths."""

from __future__ import annotations

import logging
import os
import re
import shutil
import stat
from pathlib import Path
from typing import Dict

logger = logging.getLogger(__name__)

ENV_GUARD_BIN = ".env_guard/bin"

_ENGINEERING_TASK_SUBSTRINGS = (
    "package.json",
    "requirements.txt",
    "pyproject.toml",
    "dockerfile",
    "makefile",
    "tsconfig.json",
    "vite.config",
    "next.config",
    "project structure",
    "multi-file",
    "multiple files",
    "several files",
    "unit test",
    "integration test",
    "fix bug",
    "bug fix",
    "refactor",
    "scaffold",
    "frontend",
    "backend",
    "fastapi",
    "django",
    "flask",
    "react",
    "typescript",
    "项目结构",
    "多文件",
    "多个文件",
    "脚手架",
    "修 bug",
    "修复 bug",
    "前端",
    "后端",
)

_ENGINEERING_TASK_REGEXES = (
    re.compile(r"\brepo(?:sitory)?\b", re.IGNORECASE),
    re.compile(
        r"\bbuild (?:an?\s+|the\s+)?(?:app|project|service|backend|frontend|package|repo(?:sitory)?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bcompile (?:an?\s+|the\s+)?(?:app|project|service|backend|frontend|package|repo(?:sitory)?|binary)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\binstall (?:dependencies|packages?)\b", re.IGNORECASE),
    re.compile(
        r"\bsetup (?:the\s+)?(?:project|repo(?:sitory)?|workspace|backend|frontend)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bconfigure (?:the\s+)?(?:project|repo(?:sitory)?|workspace|backend|frontend)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bdebug (?:the\s+)?(?:build|service|backend|frontend|project|repo(?:sitory)?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:add|write|create|update|fix)\s+(?:unit tests?|integration tests?)\b",
        re.IGNORECASE,
    ),
)

_CONDA_WRAPPER_TEMPLATE = """\
#!/usr/bin/env python3
\"\"\"Runtime guardrail: block {cmd} mutations to the shared host environment.\"\"\"
import os, shutil, subprocess, sys

_CMD = {cmd!r}
_DIRECT_MUTATING = frozenset({{
    "install", "update", "upgrade", "remove", "uninstall",
    "create", "config", "init", "rename",
}})
_ENV_MUTATING = frozenset({{"create", "remove", "update", "config"}})


def _find_real():
    guard = os.path.dirname(os.path.abspath(__file__))
    clean = os.pathsep.join(
        p for p in os.environ.get("PATH", "").split(os.pathsep)
        if os.path.abspath(p) != guard
    )
    return shutil.which(_CMD, path=clean), clean


def _is_mutating(args):
    if not args:
        return False
    subcmd = args[0].lower()
    if subcmd in _DIRECT_MUTATING:
        return True
    if subcmd == "env":
        nested = args[1].lower() if len(args) > 1 else ""
        return nested in _ENV_MUTATING
    if subcmd == "run":
        return True
    return False


def main():
    args = sys.argv[1:]
    subcmd = args[0].lower() if args else ""
    if _is_mutating(args):
        print(
            f"[RUNTIME GUARDRAIL] '{{_CMD}} {{subcmd}}' blocked: shared host conda "
            f"state is read-only for code execution. Report BLOCKED_DEPENDENCY if a "
            f"new package or solver is required.",
            file=sys.stderr,
        )
        sys.exit(1)
    real, clean_path = _find_real()
    if not real:
        print(f"[RUNTIME GUARDRAIL] {{_CMD}} not found after guard.", file=sys.stderr)
        sys.exit(127)
    env = dict(os.environ)
    env["PATH"] = clean_path
    sys.exit(subprocess.call([real] + args, env=env))


if __name__ == "__main__":
    main()
"""

_NPM_WRAPPER = """\
#!/usr/bin/env python3
\"\"\"Runtime guardrail: block global npm installs.\"\"\"
import os, shutil, subprocess, sys


def _find_real():
    guard = os.path.dirname(os.path.abspath(__file__))
    clean = os.pathsep.join(
        p for p in os.environ.get("PATH", "").split(os.pathsep)
        if os.path.abspath(p) != guard
    )
    return shutil.which("npm", path=clean), clean


def main():
    args = sys.argv[1:]
    subcmd = args[0].lower() if args else ""
    if subcmd in ("install", "i", "add") and (
        "-g" in args or "--global" in args or "--location=global" in args
    ):
        print(
            "[RUNTIME GUARDRAIL] 'npm install -g' blocked: shared host npm globals "
            "are read-only for code execution. Use workspace-local npm installs.",
            file=sys.stderr,
        )
        sys.exit(1)
    real, clean_path = _find_real()
    if not real:
        print("[RUNTIME GUARDRAIL] npm not found after guard.", file=sys.stderr)
        sys.exit(127)
    env = dict(os.environ)
    env["PATH"] = clean_path
    sys.exit(subprocess.call([real] + args, env=env))


if __name__ == "__main__":
    main()
"""


def looks_like_engineering_task(*parts: str) -> bool:
    """Return True when a task strongly resembles engineering work."""
    text = "\n".join(str(part or "") for part in parts).lower()
    if any(token in text for token in _ENGINEERING_TASK_SUBSTRINGS):
        return True
    return any(pattern.search(text) for pattern in _ENGINEERING_TASK_REGEXES)


def _write_wrapper(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _create_env_guard_bin(work_dir: str) -> str:
    guard_bin = Path(work_dir) / ENV_GUARD_BIN
    guard_bin.mkdir(parents=True, exist_ok=True)

    for cmd in ("conda", "mamba", "micromamba"):
        _write_wrapper(
            guard_bin / cmd,
            _CONDA_WRAPPER_TEMPLATE.format(cmd=cmd),
        )
    _write_wrapper(guard_bin / "npm", _NPM_WRAPPER)
    return str(guard_bin)


def inject_env_mutation_guard(env_map: Dict[str, str], work_dir: str) -> None:
    """Inject host-runtime mutation guards into a subprocess environment.

    This does not sandbox filesystem writes. It blocks common host-environment
    mutation paths by setting ``PIP_REQUIRE_VIRTUALENV=1`` and prepending
    workspace-scoped conda/mamba/npm wrapper scripts to ``PATH``.
    """
    try:
        env_map["PIP_REQUIRE_VIRTUALENV"] = "1"
        guard_bin = _create_env_guard_bin(work_dir)
        current_path = env_map.get("PATH") or os.environ.get("PATH", "")
        if not current_path.startswith(guard_bin + os.pathsep) and current_path != guard_bin:
            env_map["PATH"] = guard_bin + os.pathsep + current_path
        logger.debug("[ENV_GUARD] mutation guard active: %s", guard_bin)
    except Exception as exc:  # pragma: no cover
        logger.warning("[ENV_GUARD] Failed to install mutation guard (continuing): %s", exc)
