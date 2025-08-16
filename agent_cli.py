import json
import os
import re
import sys
import argparse
from typing import Any, Dict, List, Optional

from app.database import init_db
from app.main import propose_plan, approve_plan, run_tasks, get_plan_assembled


PLAN_MD = "plan.md"
PLAN_JSON = "plan.json"
OUTPUT_MD = "output.md"


def _render_plan_md(plan: Dict[str, Any]) -> str:
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
    try:
        if hasattr(sys.stdin, "reconfigure"):
            sys.stdin.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _safe_input(prompt: str) -> str:
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


def main():
    parser = argparse.ArgumentParser(prog="GLM Agent", description="Plan → review → execute with LLM")
    parser.add_argument("--plan-only", action="store_true", help="Only generate plan.md/plan.json and exit")
    parser.add_argument("--execute-only", action="store_true", help="Only execute tasks for a plan title")
    parser.add_argument("--goal", type=str, help="Goal to plan for")
    parser.add_argument("--title", type=str, help="Plan title")
    parser.add_argument("--sections", type=int, default=6, help="Preferred number of tasks")
    parser.add_argument("--style", type=str, help="Optional style (e.g., academic, concise)")
    parser.add_argument("--notes", type=str, help="Optional notes/hints")
    parser.add_argument("--output", type=str, default=OUTPUT_MD, help="Assembled output path (default: output.md)")
    parser.add_argument("--yes", action="store_true", help="Non-interactive: auto-approve and auto-execute where applicable")
    parser.add_argument("--no-open", action="store_true", help="Do not open editor for plan.md")
    # Context-related options for execution
    parser.add_argument("--use-context", action="store_true", help="Enable context assembly and budgeting during execution")
    parser.add_argument("--include-deps", dest="include_deps", action="store_true", help="Include dependency context (default True)")
    parser.add_argument("--exclude-deps", dest="include_deps", action="store_false", help="Exclude dependency context")
    parser.set_defaults(include_deps=None)
    parser.add_argument("--include-plan", dest="include_plan", action="store_true", help="Include sibling/plan context (default True)")
    parser.add_argument("--exclude-plan", dest="include_plan", action="store_false", help="Exclude sibling/plan context")
    parser.set_defaults(include_plan=None)
    parser.add_argument("--tfidf-k", dest="tfidf_k", type=int, help="Number of TF-IDF retrieved items")
    parser.add_argument("--max-chars", dest="max_chars", type=int, help="Total character budget across sections")
    parser.add_argument("--per-section-max", dest="per_section_max", type=int, help="Max characters per section")
    parser.add_argument("--strategy", choices=["truncate", "sentence"], help="Budgeting strategy")
    parser.add_argument("--save-snapshot", dest="save_snapshot", action="store_true", help="Save context snapshot per execution")
    parser.add_argument("--label", type=str, help="Snapshot label when saving context")
    args = parser.parse_args()

    print("=== GLM Agent ===")
    print("This CLI helps you plan and execute a project with the LLM.")
    print("")
    _ensure_stdio_utf8()

    # helper to build context options from args
    def _build_context_options_from_args(a) -> Optional[Dict[str, Any]]:
        co: Dict[str, Any] = {}
        if a.include_deps is not None:
            co["include_deps"] = bool(a.include_deps)
        if a.include_plan is not None:
            co["include_plan"] = bool(a.include_plan)
        if a.tfidf_k is not None:
            co["tfidf_k"] = int(a.tfidf_k)
        if a.max_chars is not None:
            co["max_chars"] = int(a.max_chars)
        if a.per_section_max is not None:
            co["per_section_max"] = int(a.per_section_max)
        if a.strategy:
            co["strategy"] = str(a.strategy)
        if a.save_snapshot:
            co["save_snapshot"] = True
        if a.label:
            co["label"] = str(a.label)
        return co or None

    # 0) Fast path: execute-only
    if args.execute_only:
        init_db()
        title = args.title
        if not title:
            # try reading plan.json
            try:
                with open(PLAN_JSON, "r", encoding="utf-8") as f:
                    title = (json.load(f) or {}).get("title")
            except Exception:
                title = None
        if not title:
            if sys.stdin.isatty():
                title = _safe_input("Plan title to execute: ").strip()
        if not title:
            print("Title is required for --execute-only. Exiting.")
            return
        try:
            payload_run: Dict[str, Any] = {"title": title}
            if args.use_context:
                payload_run["use_context"] = True
                co = _build_context_options_from_args(args)
                if co:
                    payload_run["context_options"] = co
            exec_res = run_tasks(payload_run)
            print(f"Executed {len(exec_res or [])} tasks.")
        except Exception as e:
            print(f"Execution failed: {e}")
            return
        # assemble
        try:
            assembled = get_plan_assembled(title)
            sections = assembled.get("sections") or []
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(f"# {assembled.get('title')}\n\n")
                for s in sections:
                    f.write(f"## {s.get('name')}\n\n{s.get('content','')}\n\n")
            print(f"Assembled output written to {args.output}.")
        except Exception as e:
            print(f"Assemble failed: {e}")
        print("\nAll done.")
        return

    # 1) Collect inputs (interactive if missing)
    goal = args.goal
    if not goal:
        if sys.stdin.isatty():
            goal = _safe_input("What do you want to build? (goal): ").strip()
        if not goal:
            print("Goal is required. Exiting.")
            return
    title = args.title
    if title is None and sys.stdin.isatty():
        title = _safe_input("Project title (leave blank to derive from goal): ").strip()
    sections_n = args.sections
    if args.goal is None and sys.stdin.isatty():
        raw = _safe_input(f"Preferred number of tasks [{sections_n}]: ").strip()
        if raw:
            try:
                sections_n = int(raw)
            except Exception:
                pass
    style = args.style
    if style is None and sys.stdin.isatty():
        style = _safe_input("Optional style (e.g., academic, concise): ").strip()
    notes = args.notes
    if notes is None and sys.stdin.isatty():
        notes = _safe_input("Optional notes/hints: ").strip()

    # 2) Init DB
    init_db()

    # 3) Propose plan via internal function
    payload = {"goal": goal, "title": title or goal[:60], "sections": sections_n, "style": style, "notes": notes}
    try:
        plan = propose_plan(payload)  # FastAPI route fn returns dict
        if not isinstance(plan, dict):
            raise RuntimeError("Unexpected response from propose_plan")
    except Exception as e:
        print(f"Plan proposal failed: {e}")
        return

    # Enforce user-input title to avoid mismatch later
    if title:
        plan["title"] = title

    # Ensure priorities
    plan = _ensure_priorities(plan)

    # 4) Write plan.md and plan.json
    with open(PLAN_JSON, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
    with open(PLAN_MD, "w", encoding="utf-8") as f:
        f.write(_render_plan_md(plan))

    print(f"\nPlan written to {PLAN_MD} (and {PLAN_JSON}).")
    open_editor = (not args.no_open) and sys.stdin.isatty() and (not args.yes) and (not args.plan_only)
    if open_editor:
        choice = _safe_input("Open the plan in your editor now? [Y/n]: ").strip().lower()
        if choice in ("", "y", "yes"):
            _open_in_editor(PLAN_MD)

    if sys.stdin.isatty() and (not args.yes) and (not args.plan_only):
        _safe_input("Review and edit the plan if needed, then save the file. Press Enter to continue...")

    if args.plan_only:
        print("Plan-only mode: done.")
        return

    # 5) Read back and parse plan from MD (prefer MD, fallback to JSON)
    try:
        with open(PLAN_MD, "r", encoding="utf-8") as f:
            md_text = f.read()
        parsed = _extract_plan_from_md(md_text)
    except Exception:
        parsed = None

    if not parsed:
        try:
            with open(PLAN_JSON, "r", encoding="utf-8") as f:
                parsed = json.load(f)
        except Exception as e:
            print(f"Failed to load {PLAN_JSON}: {e}")
            return

    parsed = _ensure_priorities(parsed)

    # 6) Confirm and approve
    print(f"\nPlan title: {parsed.get('title')}")
    print(f"Tasks: {len(parsed.get('tasks') or [])}")
    approve_ok = args.yes
    if not approve_ok and sys.stdin.isatty():
        go = _safe_input("Approve and persist this plan? [Y/n]: ").strip().lower()
        approve_ok = go in ("", "y", "yes")
    if not approve_ok:
        print("Aborted.")
        return

    try:
        res = approve_plan(parsed)
        if not isinstance(res, dict):
            raise RuntimeError("Unexpected response from approve_plan")
        print("Plan approved.")
    except Exception as e:
        print(f"Approve failed: {e}")
        return

    # 7) Execute
    do_run = args.yes
    if not do_run and sys.stdin.isatty():
        start = _safe_input("Start execution now? [Y/n]: ").strip().lower()
        do_run = start in ("", "y", "yes")
    if do_run:
        title = parsed.get("title")
        try:
            payload_run: Dict[str, Any] = {"title": title}
            if args.use_context:
                payload_run["use_context"] = True
                co = _build_context_options_from_args(args)
                if co:
                    payload_run["context_options"] = co
            exec_res = run_tasks(payload_run)
            print(f"Executed {len(exec_res or [])} tasks.")
        except Exception as e:
            print(f"Execution failed: {e}")
            return

    # 8) Assemble output
    try:
        assembled = get_plan_assembled(parsed.get("title"))
        sections = assembled.get("sections") or []
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(f"# {assembled.get('title')}\n\n")
            for s in sections:
                f.write(f"## {s.get('name')}\n\n{s.get('content','')}\n\n")
        print(f"Assembled output written to {args.output}.")
    except Exception as e:
        print(f"Assemble failed: {e}")

    print("\nAll done.")


if __name__ == "__main__":
    # Ensure GLM_API_KEY is visible to this process if you want LLM planning/execution
    if not os.getenv("GLM_API_KEY"):
        print("[WARN] GLM_API_KEY is not set; propose/execute may fail.")
    main()
