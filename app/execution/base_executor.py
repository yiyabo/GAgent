"""
Base Task Executor

Core execution logic and utility functions extracted from executor_enhanced.py.
Provides the foundation for all execution strategies.
"""

import logging
import time
from datetime import datetime
from typing import Any, Dict, Optional

from ..interfaces import TaskRepository
from ..models import TaskExecutionResult
from ..repository.tasks import default_repo
from ..services.embeddings import get_embeddings_service
from ..services.llm.llm_service import get_llm_service, TaskPromptBuilder

logger = logging.getLogger(__name__)


class BaseTaskExecutor:
    """Base class for task execution with common utilities."""

    def __init__(self, repo: Optional[TaskRepository] = None):
        self.repo = repo or default_repo
        self.llm_service = get_llm_service()
        self.embeddings_service = get_embeddings_service()
        self.prompt_builder = TaskPromptBuilder()

    def get_task_id_and_name(self, task) -> tuple[int, str]:
        """Extract task ID and name from task object."""
        if hasattr(task, "id") and hasattr(task, "name"):
            return task.id, task.name
        elif isinstance(task, dict):
            return task.get("id"), task.get("name", "Untitled")
        else:
            raise ValueError(f"Invalid task format: {type(task)}")

    def fetch_prompt(self, task_id: int, default_prompt: str) -> str:
        """Fetch task prompt from repository or use default."""
        try:
            stored_prompt = self.repo.get_task_input_prompt(task_id)
            return stored_prompt if stored_prompt else default_prompt
        except Exception:
            return default_prompt

    def execute_llm_chat(self, prompt: str) -> str:
        """Execute LLM chat using unified LLM service."""
        # Force real model inference to avoid mock outputs leaking into persisted artifacts
        return self.llm_service.chat(prompt, force_real=True)

    def generate_task_embedding_async(self, task_id: int, content: str):
        """Generate embeddings for task content asynchronously."""
        try:
            embedding = self.embeddings_service.get_single_embedding(content)
            if embedding:
                # Convert embedding list to string for database storage
                import json

                embedding_str = json.dumps(embedding) if isinstance(embedding, list) else str(embedding)
                self.repo.store_task_embedding(task_id, embedding_str)
                logger.debug(f"Generated embedding for task {task_id}")
        except Exception as e:
            logger.warning(f"Failed to generate embedding for task {task_id}: {e}")

    def execute_legacy_task(
        self, task, use_context: bool = False, context_options: Optional[Dict[str, Any]] = None
    ) -> TaskExecutionResult:
        """
        Execute task using legacy single-pass method.
        Preserved for backward compatibility.
        """
        start_time = time.time()
        task_id, name = self.get_task_id_and_name(task)

        logger.info("Executing legacy task %s: %s", task_id, name)

        # Build default prompt
        default_prompt = (
            f"Write a concise, clear section that fulfills the following task.\\n"
            f"Task: {name}.\\n"
            f"Length: ~200 words. Use a neutral, professional tone."
        )

        try:
            # Get task prompt
            prompt = self.fetch_prompt(task_id, default_prompt)

            # Execute LLM
            content = self.execute_llm_chat(prompt)

            # Store results
            # Align with repository API naming: upsert_task_output
            try:
                self.repo.upsert_task_output(task_id, content)
            except AttributeError:
                # Backward compatibility if older repo exposes store_task_output
                self.repo.store_task_output(task_id, content)  # type: ignore[attr-defined]
            self.repo.update_task_status(task_id, "done")

            # Generate embedding asynchronously
            self.generate_task_embedding_async(task_id, content)

            execution_time = time.time() - start_time

            return TaskExecutionResult(
                task_id=task_id, status="done", content=content, iterations=1, execution_time=execution_time
            )

        except Exception as e:
            logger.error("Legacy task execution failed for %s: %s", task_id, e)
            self.repo.update_task_status(task_id, "failed")

            return TaskExecutionResult(
                task_id=task_id, status="failed", content=None, iterations=1, execution_time=time.time() - start_time
            )

    def build_task_context(self, task) -> Dict[str, Any]:
        """Build task context dictionary for evaluation."""
        task_id, name = self.get_task_id_and_name(task)
        return {
            "name": name,
            "task_id": task_id,
            "task_type": (
                getattr(task, "task_type", "atomic")
                if hasattr(task, "task_type")
                else task.get("task_type", "atomic") if isinstance(task, dict) else "atomic"
            ),
        }
