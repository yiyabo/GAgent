#!/usr/bin/env python3
"""
Modern GLM Agent CLI - Improved Architecture

This is the new entry point that uses the modular CLI structure.
It provides backward compatibility with the old interface.
"""

import sys
import os
from typing import Optional, List

# Add current directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cli.main import ModernCLIApp
from cli.utils import IOUtils

# Legacy compatibility functions for backward compatibility
import json
import re
from typing import Any, Dict


def _render_plan_md(plan: Dict[str, Any]) -> str:
    """Legacy function for backward compatibility."""
    title = plan.get("title", "Untitled Plan")
    tasks = plan.get("tasks") or []
    lines = []
    lines.append(f"# Plan: {title}")
    lines.append("")
    lines.append("This document describes the proposed plan. You can edit the JSON block below (title, tasks, priorities).\n")
    lines.append("- Edit the JSON in the code block, then save.")
    lines.append("- After saving, return to the terminal and press Enter to continue.")
    lines.append("")
    # Embed a machine-readable JSON plan block
    lines.append("```json plan")
    lines.append(json.dumps(plan, ensure_ascii=False, indent=2))
    lines.append("```")
    lines.append("")
    lines.append("## Tasks (preview)")
    for t in tasks:
        name = t.get("name", "")
        prio = t.get("priority", "")
        prompt = t.get("prompt", "")
        lines.append(f"- [{prio}] {name}: {prompt}")
    lines.append("")
    return "\n".join(lines)


def _extract_plan_from_md(md: str) -> Optional[Dict[str, Any]]:
    """Legacy function for backward compatibility."""
    # Look for a fenced code block starting with ```json plan
    m = re.search(r"```json\s*plan\s*\n(.*?)```", md, flags=re.S | re.I)
    if not m:
        return None
    block = m.group(1)
    try:
        obj = json.loads(block)
        if isinstance(obj, dict) and "tasks" in obj:
            return obj
    except Exception:
        return None
    return None


def _ensure_priorities(plan: Dict[str, Any]) -> Dict[str, Any]:
    """Legacy function for backward compatibility."""
    tasks: List[Dict[str, Any]] = plan.get("tasks") or []
    # If any task lacks priority, assign 10,20,30...
    changed = False
    for idx, t in enumerate(tasks):
        if not isinstance(t.get("priority"), int):
            t["priority"] = (idx + 1) * 10
            changed = True
    if changed:
        plan["tasks"] = tasks
    return plan


def _open_in_editor(path: str) -> None:
    """Legacy function for backward compatibility."""
    editor = os.environ.get("EDITOR")
    if editor:
        try:
            os.system(f"{editor} {path}")
            return
        except Exception:
            pass
    # macOS fallback
    if os.name == "posix" and os.uname().sysname == "Darwin":
        os.system(f"open -t {path}")
        return
    # generic fallback
    os.system(f"nano {path}")


def _ensure_stdio_utf8() -> None:
    """Legacy function for backward compatibility."""
    try:
        if hasattr(sys.stdin, "reconfigure"):
            sys.stdin.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _safe_input(prompt: str) -> str:
    """Legacy function for backward compatibility."""
    try:
        return input(prompt)
    except UnicodeDecodeError:
        try:
            sys.stdout.write(prompt)
            sys.stdout.flush()
            data = sys.stdin.buffer.readline()
            try:
                return data.decode("utf-8").rstrip("\r\n")
            except Exception:
                return data.decode("utf-8", errors="replace").rstrip("\r\n")
        except Exception:
            return ""


# Legacy constants for compatibility
PLAN_MD = "plan.md"
PLAN_JSON = "plan.json"
OUTPUT_MD = "output.md"


def main() -> int:
    """
    Main entry point for the CLI application.
    
    This uses the new modular CLI architecture but provides
    backward compatibility with the legacy interface.
    """
    # Environment check
    if not os.getenv("GLM_API_KEY"):
        io = IOUtils()
        io.print_warning("GLM_API_KEY is not set; some operations may fail")
    
    # Use the modern CLI application
    app = ModernCLIApp()
    return app.run()


# Keep legacy functions available for backward compatibility but deprecate them
def rerun_single_task(task_id: int, use_context: bool = False, context_options: Optional[Dict[str, Any]] = None):
    """Deprecated: Use new CLI interface instead."""
    from cli.commands.rerun_commands import RerunCommands
    from argparse import Namespace
    
    cmd = RerunCommands()
    args = Namespace()
    args.rerun_task = task_id
    args.use_context = use_context
    
    # Convert context_options to args attributes
    if context_options:
        for key, value in context_options.items():
            setattr(args, key, value)
    
    return cmd.handle_single_task(args)


def rerun_subtree(task_id: int, use_context: bool = False, include_parent: bool = True, context_options: Optional[Dict[str, Any]] = None):
    """Deprecated: Use new CLI interface instead."""
    from cli.commands.rerun_commands import RerunCommands
    from argparse import Namespace
    
    cmd = RerunCommands()
    args = Namespace()
    args.rerun_subtree = task_id
    args.rerun_include_parent = include_parent
    args.use_context = use_context
    
    # Convert context_options to args attributes
    if context_options:
        for key, value in context_options.items():
            setattr(args, key, value)
    
    return cmd.handle_subtree(args)


if __name__ == "__main__":
    sys.exit(main())
