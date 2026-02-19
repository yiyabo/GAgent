"""
Task path generation utilities.

Output conventions:
- ROOT task -> `results/[root_name]/`
- COMPOSITE task -> `results/[root_name]/[composite_name]/`
- ATOMIC task -> `results/[root_name]/[composite_name]/[atomic_name].md`
"""

import os
import re
from pathlib import Path
from typing import Optional, Tuple


def slugify(text: str) -> str:
    """Normalize task names to filesystem-safe slugs."""
    text = re.sub(r'^(ROOT|COMPOSITE|ATOMIC):\s*', '', text, flags=re.IGNORECASE)

    text = text.strip().lower()
    text = re.sub(r'[^\w\s-]', '', text)  # Remove non-word characters.
    text = re.sub(r'[\s_]+', '_', text)  # Normalize spaces/underscores.
    text = re.sub(r'-+', '-', text)  # Collapse repeated hyphens.

    if len(text) > 50:
        text = text[:50]

    return text or 'unnamed'


def get_task_file_path(task: dict, repo=None) -> str:
    """
    Build canonical output path for a task.

    Args:
        task: Task record (dict or tuple).
        repo: Optional repository used to resolve parent/root tasks.

    Returns:
        Canonical output path.

    Examples:
        ROOT task: "results/root_task_name/"
        COMPOSITE task: "results/root_name/composite_name/"
        ATOMIC task: "results/root_name/composite_name/atomic_name.md"
    """
    if isinstance(task, dict):
        task_id = task.get('id')
        task_name = task.get('name', 'unnamed')
        task_type = task.get('task_type', 'atomic')
        parent_id = task.get('parent_id')
        root_id = task.get('root_id')
    else:
        # tuple format: (id, name, status, ...)
        task_id = task[0] if len(task) > 0 else None
        task_name = task[1] if len(task) > 1 else 'unnamed'
        task_type = task[7] if len(task) > 7 else 'atomic'
        parent_id = task[5] if len(task) > 5 else None
        root_id = task[10] if len(task) > 10 else None

    clean_name = slugify(task_name)

    if task_type == 'root':
        return f"results/{clean_name}/"

    path_parts = [clean_name]

    if repo and parent_id:
        try:
            parent_task = repo.get_task_info(parent_id)
            if parent_task:
                parent_name = parent_task.get('name') if isinstance(parent_task, dict) else parent_task[1]
                parent_type = parent_task.get('task_type') if isinstance(parent_task, dict) else parent_task[7]
                parent_clean = slugify(parent_name)

                if parent_type == 'root':
                    path_parts.insert(0, parent_clean)
                elif parent_type == 'composite':
                    path_parts.insert(0, parent_clean)
                    if root_id and root_id != parent_id:
                        root_task = repo.get_task_info(root_id)
                        if root_task:
                            root_name = root_task.get('name') if isinstance(root_task, dict) else root_task[1]
                            root_clean = slugify(root_name)
                            path_parts.insert(0, root_clean)
        except Exception as e:
            print(f"Warning: Failed to resolve parent task: {e}")

    if repo and root_id and not parent_id:
        try:
            root_task = repo.get_task_info(root_id)
            if root_task:
                root_name = root_task.get('name') if isinstance(root_task, dict) else root_task[1]
                root_clean = slugify(root_name)
                if root_clean not in path_parts:
                    path_parts.insert(0, root_clean)
        except Exception:
            pass

    if len(path_parts) == 1:
        path_parts.insert(0, 'default_project')

    if task_type == 'composite':
        return f"results/{'/'.join(path_parts)}/"

    return f"results/{'/'.join(path_parts)}.md"


def ensure_task_directory(file_path: str) -> bool:
    """
    Ensure directories required by a task output path exist.

    Args:
        file_path: Output directory path or file path.

    Returns:
        True when creation/check succeeds.
    """
    try:
        path = Path(file_path)
        if file_path.endswith('/'):
            path.mkdir(parents=True, exist_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
        return True
    except Exception as e:
        print(f"Failed to create directory for {file_path}: {e}")
        return False


def get_task_output_path(task: dict, repo=None) -> Tuple[str, str]:
    """
    Return `(directory_path, full_file_path)` for a task output target.

    Returns:
        (directory_path, full_file_path)
    """
    full_path = get_task_file_path(task, repo)

    if full_path.endswith('/'):
        return (full_path, full_path)
    else:
        directory = str(Path(full_path).parent) + '/'
        return (directory, full_path)
