from typing import Optional
from .llm import get_default_client
from .interfaces import TaskRepository
from .repository.tasks import default_repo
from .services.context import gather_context


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


def execute_task(task, repo: Optional[TaskRepository] = None, use_context: bool = False):
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
        try:
            bundle = gather_context(task_id, repo=repo)
            ctx = bundle.get("combined") if isinstance(bundle, dict) else None
        except Exception:
            ctx = None
        if ctx:
            prompt = f"[Context]\n\n{ctx}\n\n[Task Instruction]\n\n{prompt}"

    try:
        content = _glm_chat(prompt)
        repo.upsert_task_output(task_id, content)
        print(f"Task {task_id} ({name}) done.")
        return "done"
    except Exception as e:
        print(f"Task {task_id} ({name}) failed: {e}")
        return "failed"