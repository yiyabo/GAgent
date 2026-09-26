"""Shared runtime guardrails for local and CLI code execution paths."""

from __future__ import annotations

import logging
import os
import re
import shutil
import stat
import time
from pathlib import Path
from typing import Dict, Optional

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


# ---------------------------------------------------------------------------
# One-script delegation guard
#
# Offer-side copy already asks the model not to delegate single-file reads,
# one-off statistics, arithmetic, a single plot or read-only checks. Measured
# 2026-09-26 on 28-task A/B arms, the copy alone does not stop it: the same
# task runs 25-80s when the model writes the script itself (execute_code, the
# in-process kernel) and 100-900s when it delegates, because a delegation
# starts a full coding agent first (35-57s floor measured; 900s observed).
#
# The classifier is deliberately conservative and stateless, so it stays
# escapable: anything mentioning installs, engineering nouns, a failure to
# debug or an already-written code block is never flagged, and re-stating the
# task with real requirements stops it matching.
# ---------------------------------------------------------------------------

ONE_SCRIPT_MAX_CHARS = 900

_ONE_SCRIPT_REGEXES = (
    re.compile(
        r"\b(?:read|open|load|parse|inspect|summari[sz]e|analy[sz]e)\s+"
        r"(?:the|this|these|those|a|that|all|both)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:count|how many|number of)\b", re.IGNORECASE),
    re.compile(r"\b(?:sum|total|average|mean|median|max|min|top\s*\d+)\b", re.IGNORECASE),
    re.compile(r"\brows?\b|\bcolumns?\b", re.IGNORECASE),
    re.compile(r"\b(?:plot|chart|figure|histogram|scatter|pie|heatmap)\b", re.IGNORECASE),
    re.compile(r"\b(?:print|show|list|report)\s+(?:the|all|its|them)\b", re.IGNORECASE),
    re.compile(r"\b(?:verify|check|audit|confirm|validate)\b", re.IGNORECASE),
)

_ONE_SCRIPT_CJK_SUBSTRINGS = (
    "读取", "读一下", "看一下", "看看", "打印", "列出", "输出",
    "统计", "汇总", "分组", "有多少", "多少行", "合计", "总数", "平均", "中位数",
    "最大值", "最小值", "排序", "去重",
    "画一张", "画个图", "画图", "单张图", "柱状图", "折线图", "饼图", "散点图",
    "直方图", "热力图", "箱线图",
    "核验", "核对", "校验", "审计", "检查", "是否符合",
)

_NEEDS_A_CODING_AGENT_REGEXES = (
    re.compile(r"\b(?:pip|conda|apt|apt-get|npm|yarn|poetry|uv)\s+(?:install|add)\b", re.IGNORECASE),
    re.compile(r"\b(?:refactor|migrate|scaffold|implement|debug|port|rewrite)\b", re.IGNORECASE),
    re.compile(r"\b(?:fix|repair|patch|workaround|root cause)\b", re.IGNORECASE),
    re.compile(r"\b(?:multiple files|several files|multi-file|codebase|module|package|class|api|endpoint|service|framework)\b", re.IGNORECASE),
    re.compile(r"\b(?:traceback|exception|import error|stack trace|stderr|exit code|segfault)\b", re.IGNORECASE),
    re.compile(r"\b(?:parallel|multiprocessing|performance|optimi[sz]e|benchmark|cache|pipeline)\b", re.IGNORECASE),
)

_NEEDS_A_CODING_AGENT_CJK_SUBSTRINGS = (
    "安装", "依赖", "重构", "迁移", "多个文件", "多文件", "模块", "接口", "服务",
    "调试", "报错", "异常", "栈", "并行", "性能", "优化", "环境", "脚手架",
)

ONE_SCRIPT_REFUSAL_MESSAGE = (
    "DELEGATION_TOO_SMALL: this reads as one short script, not implementation work. "
    "A delegation starts a full coding agent before any work happens — a measured "
    "30-60s floor, and up to 900s on this platform. Answer it with execute_code "
    "(about five lines of Python in the in-process kernel) or with document_reader / "
    "file_operations / result_interpreter, which do this in seconds. Re-issue the task "
    "here only if it genuinely needs a coding agent: multiple files, installs, "
    "long-running work, or a failure to debug."
)

ONE_SCRIPT_GUARD_ENV = "CODE_EXECUTOR_ONE_SCRIPT_GUARD"

# One refusal per (session, task) is enough to steer the model; a second
# identical ask means it really wants the coding agent, so let it through.
# Bounded and time-limited because this is per-process policy memory, not
# durable state.
REFUSAL_MEMORY_TTL_S = 900.0
REFUSAL_MEMORY_MAX = 256
_REFUSAL_MEMORY: Dict[tuple, float] = {}


def _prune_refusal_memory(now: float) -> None:
    if len(_REFUSAL_MEMORY) < REFUSAL_MEMORY_MAX:
        return
    for key, seen in list(_REFUSAL_MEMORY.items()):
        if (now - seen) >= REFUSAL_MEMORY_TTL_S:
            _REFUSAL_MEMORY.pop(key, None)
    while len(_REFUSAL_MEMORY) >= REFUSAL_MEMORY_MAX:
        _REFUSAL_MEMORY.pop(next(iter(_REFUSAL_MEMORY)), None)


def looks_like_one_script_delegation(*parts: str) -> bool:
    """True when the delegation asks for something one short script answers."""
    text = "\n".join(str(part or "") for part in parts)
    if not text.strip():
        return False
    if len(text) > ONE_SCRIPT_MAX_CHARS:
        return False
    if "```" in text:
        # Already-written code: out of scope for this guard (running someone
        # else's script may legitimately want the isolated execution image).
        return False
    if looks_like_engineering_task(text):
        return False
    if any(pattern.search(text) for pattern in _NEEDS_A_CODING_AGENT_REGEXES):
        return False
    if any(token in text for token in _NEEDS_A_CODING_AGENT_CJK_SUBSTRINGS):
        return False
    if any(pattern.search(text) for pattern in _ONE_SCRIPT_REGEXES):
        return True
    return any(token in text for token in _ONE_SCRIPT_CJK_SUBSTRINGS)


def one_script_guard_enabled() -> bool:
    raw = str(os.getenv(ONE_SCRIPT_GUARD_ENV, "1")).strip().lower()
    return raw not in {"0", "false", "no", "off"}


def should_refuse_one_script_delegation(
    task: str,
    *,
    require_task_context: bool,
    session_key: str = "",
    enabled: Optional[bool] = None,
) -> bool:
    """Policy: refuse only what the model chose freely and the classifier flags.

    Two boundaries keep this from becoming a wall:

    * a plan-bound execution (``require_task_context``) is not the model's
      choice — the plan decided to run code there — so it is never refused;
    * the same task asked again in the same session is let through, so a model
      that really does need the coding agent gets it on the second attempt
      instead of looping on the refusal.
    """
    if enabled is None:
        enabled = one_script_guard_enabled()
    if not enabled:
        return False
    if require_task_context:
        return False
    if not looks_like_one_script_delegation(task):
        return False

    key_text = str(task or "").strip()[:200]
    if session_key and key_text:
        key = (str(session_key), key_text)
        now = time.monotonic()
        first_seen = _REFUSAL_MEMORY.get(key)
        _prune_refusal_memory(now)
        if first_seen is not None and (now - first_seen) < REFUSAL_MEMORY_TTL_S:
            logger.info(
                "one-script delegation repeated in session %s — allowing it through",
                session_key,
            )
            return False
        _REFUSAL_MEMORY[key] = now
    return True



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
