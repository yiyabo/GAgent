import asyncio
import logging
import threading
from typing import Any, Dict, Optional

from ...interfaces import TaskRepository
from ...llm import get_default_client
from ...repository.tasks import default_repo
from ...services.context import gather_context
from ...services.context.context_budget import apply_budget
from ...services.embeddings import get_embeddings_service

logger = logging.getLogger(__name__)


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
    # Force real call to avoid mock responses
    return client.chat(prompt, force_real=True)


def _generate_task_embedding_async(task_id: int, content: str, repo: TaskRepository):
    """Asynchronously generate and store task embedding"""

    def _background_embedding():
        try:
            if not content or not content.strip():
                logger.debug(f"Task {task_id} content is empty, skipping embedding generation")
                return

            # Check if embedding already exists
            existing_embedding = repo.get_task_embedding(task_id)
            if existing_embedding:
                logger.debug(f"Task {task_id} already has embedding, skipping generation")
                return

            embeddings_service = get_embeddings_service()

            # Generate embedding
            logger.debug(f"Generating embedding for task {task_id}")
            embedding = embeddings_service.get_single_embedding(content)

            if embedding:
                # Store embedding
                embedding_json = embeddings_service.embedding_to_json(embedding)
                repo.store_task_embedding(task_id, embedding_json)
                logger.debug(f"Successfully stored embedding for task {task_id}")
            else:
                logger.warning(f"Failed to generate embedding for task {task_id}")

        except Exception as e:
            logger.error(f"Error generating embedding for task {task_id}: {e}")

    # Execute in background thread to avoid blocking main process
    thread = threading.Thread(target=_background_embedding, daemon=True)
    thread.start()


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
            # GLM semantic retrieval options
            try:
                semantic_k = int(opts.get("semantic_k")) if (opts.get("semantic_k") is not None) else None
            except Exception:
                semantic_k = None
            try:
                min_similarity = float(opts.get("min_similarity")) if (opts.get("min_similarity") is not None) else None
            except Exception:
                min_similarity = None
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
                semantic_k=semantic_k,
                min_similarity=min_similarity,
            )
            # Budget options (apply defaults if none provided)
            max_chars = opts.get("max_chars")
            per_section_max = opts.get("per_section_max")
            strategy = opts.get("strategy") if isinstance(opts.get("strategy"), str) else None

            def _int_env(name: str, default_val: int) -> int:
                try:
                    import os as _os

                    v = _os.environ.get(name)
                    return int(v) if v is not None and str(v).strip() != "" else int(default_val)
                except Exception:
                    return int(default_val)

            # Determine effective caps
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
            else:
                # Apply safe defaults when use_context=true but no budget provided
                default_total = _int_env("CONTEXT_DEFAULT_MAX_CHARS", 6000)
                default_per_sec = _int_env("CONTEXT_DEFAULT_PER_SECTION", 1200)
                default_strategy = strategy or ("sentence" if default_total or default_per_sec else "truncate")
                bundle = apply_budget(
                    bundle,
                    max_chars=default_total,
                    per_section_max=default_per_sec,
                    strategy=default_strategy,
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
                meta = {
                    "source": "executor",
                    "options": {
                        "include_deps": include_deps,
                        "include_plan": include_plan,
                        "k": k,
                        "manual": manual,
                        "semantic_k": semantic_k,
                        "min_similarity": min_similarity,
                        "max_chars": (max_chars_i if "max_chars_i" in locals() else None),
                        "per_section_max": (per_section_max_i if "per_section_max_i" in locals() else None),
                        "strategy": (strategy or "truncate") if strategy else None,
                    },
                }
                # Attach budget info if present
                if "budget_info" in bundle:
                    meta["budget_info"] = bundle["budget_info"]
                try:
                    repo.upsert_task_context(
                        task_id, bundle.get("combined", ""), bundle.get("sections", []), meta, label=label
                    )
                except Exception:
                    # Repository may not implement snapshots; ignore silently
                    pass
        except Exception:
            pass

    try:
        content = _glm_chat(prompt)
        repo.upsert_task_output(task_id, content)
        logger.info(f"Task {task_id} ({name}) done.")

        # Asynchronously generate embedding (optional)
        try:
            generate_embeddings = True  # Default enabled

            # Check if there's embedding configuration in context_options
            if context_options and isinstance(context_options, dict):
                generate_embeddings = context_options.get("generate_embeddings", True)

            if generate_embeddings:
                _generate_task_embedding_async(task_id, content, repo)
        except Exception as embed_error:
            logger.warning(f"Failed to trigger embedding generation (task {task_id}): {embed_error}")

        return "done"
    except Exception as e:
        logger.error(f"Task {task_id} ({name}) failed: {e}")
        return "failed"
