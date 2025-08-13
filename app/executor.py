import json
import os
from urllib import request, error

from .database import get_db


def _get_task_id_and_name(task):
    """Support both sqlite3.Row (mapping) and tuple-style rows."""
    try:
        task_id = task["id"]  # sqlite3.Row mapping
        name = task["name"]
    except Exception:
        task_id = task[0]
        name = task[1]
    return task_id, name


def _fetch_prompt(task_id, default_prompt):
    with get_db() as conn:
        row = conn.execute("SELECT prompt FROM task_inputs WHERE task_id=?", (task_id,)).fetchone()
    if row and ("prompt" in row.keys() if hasattr(row, "keys") else True):
        try:
            return row["prompt"]
        except Exception:
            return row[0]
    return default_prompt


def _glm_chat(prompt: str) -> str:
    api_key = os.getenv("GLM_API_KEY")
    if not api_key:
        raise RuntimeError("GLM_API_KEY is not set in environment")

    url = os.getenv(
        "GLM_API_URL",
        "https://open.bigmodel.cn/api/paas/v4/chat/completions",
    )
    model = os.getenv("GLM_MODEL", "glm-4-flash")

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a scientific writing assistant."},
            {"role": "user", "content": prompt},
        ],
    }

    data = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=data)
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {api_key}")

    try:
        with request.urlopen(req, timeout=60) as resp:
            resp_text = resp.read().decode("utf-8")
            obj = json.loads(resp_text)
    except error.HTTPError as e:
        msg = e.read().decode("utf-8") if hasattr(e, "read") else str(e)
        raise RuntimeError(f"HTTPError {e.code}: {msg}")
    except error.URLError as e:
        raise RuntimeError(f"URLError: {e}")

    # Expected schema: { choices: [ { message: { content: "..." } } ] }
    try:
        return obj["choices"][0]["message"]["content"]
    except Exception:
        # Fallback: try common alternative fields
        if isinstance(obj, dict):
            for k in ("content", "text"):
                if k in obj and isinstance(obj[k], str):
                    return obj[k]
        raise RuntimeError(f"Unexpected response schema: {obj}")


def execute_task(task):
    task_id, name = _get_task_id_and_name(task)

    default_prompt = (
        f"Write a concise, clear section that fulfills the following task.\n"
        f"Task: {name}.\n"
        f"Length: ~200 words. Use a neutral, professional tone. Avoid domain-specific assumptions unless explicitly provided."
    )
    prompt = _fetch_prompt(task_id, default_prompt)

    try:
        content = _glm_chat(prompt)
        with get_db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO task_outputs (task_id, content) VALUES (?, ?)",
                (task_id, content),
            )
            conn.commit()
        print(f"Task {task_id} ({name}) done.")
        return "done"
    except Exception as e:
        print(f"Task {task_id} ({name}) failed: {e}")
        return "failed"