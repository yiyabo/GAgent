from typing import Optional, Dict, Any
from .llm import get_default_client
from .interfaces import TaskRepository
from .repository.tasks import default_repo
from .services.context import gather_context
from .services.context_budget import apply_budget


def _get_task_id_and_name(task):
    """Support both sqlite3.Row (mapping) and tuple-style rows."""
    try:
        task_id = task["id"]  # sqlite3.Row mapping
        name = task["name"]
    except Exception:
        task_id = task[0]
        name = task[1]
    return task_id, name


def _fetch_prompt(task_id, default_prompt, repo: TaskRepository):
    prompt = repo.get_task_input_prompt(task_id)
    return prompt if (isinstance(prompt, str) and prompt.strip()) else default_prompt


def _glm_chat(prompt: str) -> str:
    # Delegate to default LLM client (Phase 1 abstraction)
    client = get_default_client()
    return client.chat(prompt)


def execute_task(
    task,
    repo: Optional[TaskRepository] = None,
    use_context: bool = False,
    context_options: Optional[Dict[str, Any]] = None,
):
    repo = repo or default_repo
    task_id, name = _get_task_id_and_name(task)

    default_prompt = (
        f"Write a concise, clear section that fulfills the following task.\n"
        f"Task: {name}.\n"
        f"Length: ~200 words. Use a neutral, professional tone. Avoid domain-specific assumptions unless explicitly provided."
    )
    prompt = _fetch_prompt(task_id, default_prompt, repo)

    # Optionally gather and prepend contextual information
    if use_context:
        opts: Dict[str, Any] = context_options or {}
        try:
            # Selection options
            include_deps = bool(opts.get("include_deps", True))
            include_plan = bool(opts.get("include_plan", True))
            try:
                k = int(opts.get("k", 5))
            except Exception:
                k = 5
            # Optional TF-IDF retrieval count
            try:
                tfidf_k = int(opts.get("tfidf_k")) if (opts.get("tfidf_k") is not None) else None
            except Exception:
                tfidf_k = None
            manual = None
            mids = opts.get("manual")
            if isinstance(mids, list):
                try:
                    manual = [int(x) for x in mids]
                except Exception:
                    manual = None

            bundle = gather_context(
                task_id,
                repo=repo,
                include_deps=include_deps,
                include_plan=include_plan,
                k=k,
                manual=manual,
                tfidf_k=tfidf_k,
            )
            # Budget options
            max_chars = opts.get("max_chars")
            per_section_max = opts.get("per_section_max")
            strategy = opts.get("strategy") if isinstance(opts.get("strategy"), str) else None
            if (max_chars is not None) or (per_section_max is not None):
                try:
                    max_chars_i = int(max_chars) if max_chars is not None else None
                except Exception:
                    max_chars_i = None
                try:
                    per_section_max_i = int(per_section_max) if per_section_max is not None else None
                except Exception:
                    per_section_max_i = None
                bundle = apply_budget(
                    bundle,
                    max_chars=max_chars_i,
                    per_section_max=per_section_max_i,
                    strategy=strategy or "truncate",
                )

            ctx = bundle.get("combined") if isinstance(bundle, dict) else None
        except Exception:
            ctx = None
        if ctx:
            prompt = f"[Context]\n\n{ctx}\n\n[Task Instruction]\n\n{prompt}"

        # Optional: persist context snapshot if requested
        try:
            if isinstance(bundle, dict) and bool(opts.get("save_snapshot", False)):
                label = opts.get("label") or "latest"
                meta = {"source": "executor", "options": {
                    "include_deps": include_deps,
                    "include_plan": include_plan,
                    "k": k,
                    "manual": manual,
                    "tfidf_k": tfidf_k,
                    "max_chars": (max_chars_i if 'max_chars_i' in locals() else None),
                    "per_section_max": (per_section_max_i if 'per_section_max_i' in locals() else None),
                    "strategy": (strategy or "truncate") if strategy else None,
                }}
                # Attach budget info if present
                if "budget_info" in bundle:
                    meta["budget_info"] = bundle["budget_info"]
                try:
                    repo.upsert_task_context(task_id, bundle.get("combined", ""), bundle.get("sections", []), meta, label=label)
                except Exception:
                    # Repository may not implement snapshots; ignore silently
                    pass
        except Exception:
            pass

    try:
        content = _glm_chat(prompt)
        repo.upsert_task_output(task_id, content)
        print(f"Task {task_id} ({name}) done.")
        return "done"
    except Exception as e:
        print(f"Task {task_id} ({name}) failed: {e}")
        return "failed"