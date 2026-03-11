"""
Task execution orchestration.

This module routes tasks to one of two paths:
1. Code-required tasks executed via Claude Code.
2. Text-only tasks answered directly by the LLM.

Skills support:
- Workspace skills from `skills/`
- User skills from `~/.claude/skills/`
- Optional LLM-based skill selection hints per task
"""

import asyncio
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from enum import Enum

from pydantic import BaseModel, Field

from app.config.executor_config import get_executor_settings
from app.services.llm.llm_service import LLMService, get_llm_service
from app.services.skills import SkillsLoader, get_skills_loader
from .metadata import DatasetMetadata, DataProcessor
from .prompts.task_executer import (
    TASK_TYPE_SYSTEM_PROMPT,
    TASK_TYPE_USER_PROMPT_TEMPLATE,
    TEXT_TASK_PROMPT_TEMPLATE,
)

logger = logging.getLogger(__name__)


class TaskType(str, Enum):
    """Supported task execution modes."""
    CODE_REQUIRED = "code_required"  # Task requires code execution/tooling.
    TEXT_ONLY = "text_only"  # Task can be answered directly with text.


class TaskExecutionResult(BaseModel):
    """Normalized task execution output."""
    task_type: TaskType = Field(..., description="Task execution type")
    success: bool = Field(..., description="Whether task execution succeeded")

    final_code: Optional[str] = Field(None, description="Final generated code")
    code_description: Optional[str] = Field(None, description="Human-readable code summary")
    code_output: Optional[str] = Field(None, description="Execution stdout")
    code_error: Optional[str] = Field(None, description="Execution stderr")
    total_attempts: int = Field(0, description="Total execution attempts")

    has_visualization: bool = Field(default=False, description="Whether visualization artifacts were produced")
    visualization_purpose: Optional[str] = Field(None, description="Purpose of generated visualization")
    visualization_analysis: Optional[str] = Field(None, description="Interpretation of generated visualization")

    text_response: Optional[str] = Field(None, description="LLM text response")

    gathered_info: Optional[str] = Field(None, description="Optional gathered supporting information")
    info_gathering_rounds: int = Field(0, description="Number of info-gathering rounds")

    error_message: Optional[str] = Field(None, description="Top-level execution error message")
    skill_trace: Optional[Dict[str, Any]] = Field(
        None, description="Structured skill selection/injection trace"
    )


class TaskExecutor:
    """
    Execute tasks with Claude Code or direct LLM responses.

    Example:
        executor = TaskExecutor(data_file_paths=["/path/to/data1.csv", "/path/to/data2.csv"])
        result = await executor.execute(
            task_title="Generate distribution plots",
            task_description="Create histograms for the key metrics"
        )
    """

    def __init__(
        self,
        data_file_paths: List[str],
        llm_service: Optional[LLMService] = None,
        docker_image: str = "agent-plotter",  # parameter, 
        docker_timeout: int = 60,  # parameter, 
        output_dir: Optional[str] = None,
        session_id: Optional[str] = None,
        plan_id: Optional[int] = None,
    ):
        """
        Initialize task executor.

        Args:
            data_file_paths: Dataset file paths (e.g., CSV/TSV/MAT).
            llm_service: Optional LLM service instance.
            docker_image: Reserved compatibility parameter.
            docker_timeout: Reserved compatibility parameter.
            output_dir: Output directory for generated artifacts.
            session_id: Optional session ID for Claude Code scope.
            plan_id: Optional plan ID for Claude Code scope.
        """
        if isinstance(data_file_paths, str):
            data_file_paths = [data_file_paths]

        self._staging_dir: Optional[str] = None

        data_dirs = {str(Path(fp).resolve().parent) for fp in data_file_paths}
        if len(data_dirs) > 1:
            staging_dir = tempfile.mkdtemp(prefix="interpreter_data_")
            self._staging_dir = staging_dir  # save
            used_names = set()
            staged_paths: List[str] = []

            for fp in data_file_paths:
                src_path = Path(fp).resolve()
                base = src_path.name
                name = base
                if name in used_names:
                    stem = src_path.stem
                    suffix = src_path.suffix
                    index = 2
                    while f"{stem}_{index}{suffix}" in used_names:
                        index += 1
                    name = f"{stem}_{index}{suffix}"
                used_names.add(name)

                dest_path = Path(staging_dir) / name
                shutil.copy2(src_path, dest_path)
                staged_paths.append(str(dest_path))

            data_file_paths = staged_paths
            logger.info("Multiple data directories detected; staged files at %s", staging_dir)

        self.data_file_paths = data_file_paths

        # Resolve data directory from the first staged file path.
        data_path = Path(data_file_paths[0]).resolve()
        self.data_dir = str(data_path.parent)
        self.data_filenames = [Path(fp).name for fp in data_file_paths]  # Filenames only.

        # Configure output directory.
        if output_dir:
            self.output_dir = str(Path(output_dir).resolve())
            # Ensure output directory exists.
            Path(self.output_dir).mkdir(parents=True, exist_ok=True)
        else:
            self.output_dir = self.data_dir

        # Parse metadata for all datasets.
        self.metadata_list: List[DatasetMetadata] = []
        for fp in data_file_paths:
            logger.info(f"Parsing dataset metadata: {fp}")
            metadata = DataProcessor.get_metadata(fp)
            self.metadata_list.append(metadata)
            logger.info(
                f"Metadata parsed: {metadata.filename} - "
                f"{metadata.total_rows} rows x {metadata.total_columns} columns"
            )

        # Initialize LLM service (used for task type classification and text-only tasks).
        self.llm_service = llm_service or get_llm_service()
        self.skill_settings = get_executor_settings()

        # Claude Code workspace isolation parameters.
        self.session_id = session_id
        self.plan_id = plan_id

        # Initialize Skills manager (ensures skills are synced to ~/.claude/skills/).
        # Claude Code can then load skills automatically based on task semantics.
        try:
            self.skills_loader = get_skills_loader(auto_sync=True)
            skills_count = len(self.skills_loader.list_skills())
            logger.info(f"Skills synced successfully; {skills_count} skills available")
        except Exception as e:
            logger.warning(f"Skills initialization failed (non-blocking): {e}")
            self.skills_loader = None

        logger.info(f"TaskExecutor initialized: data_dir={self.data_dir}, output_dir={self.output_dir}")

    def cleanup(self) -> None:
        """Clean up the temporary staging directory if it exists."""
        if self._staging_dir and os.path.isdir(self._staging_dir):
            try:
                shutil.rmtree(self._staging_dir)
                logger.info(f"Removed temporary staging directory: {self._staging_dir}")
            except Exception as e:
                logger.warning(f"Failed to remove staging directory {self._staging_dir}: {e}")
            finally:
                self._staging_dir = None

    def __del__(self):
        """Automatically clean up staging resources on object destruction."""
        self.cleanup()

    def _format_datasets_summary(self) -> str:
        """Format a concise summary for all datasets."""
        summaries = []
        for i, metadata in enumerate(self.metadata_list, 1):
            summary = f"""### Dataset {i}: {metadata.filename}
- Format: {metadata.file_format}
- Rows: {metadata.total_rows}
- Columns: {metadata.total_columns}
- Sample values (first 3 columns x 3 values): {"; ".join(
            f"{col.name}: {col.sample_values[:3]}"
            for col in metadata.columns[:3]
        )}"""
            summaries.append(summary)
        return "\n\n".join(summaries)

    def _analyze_task_type(self, task_title: str, task_description: str) -> TaskType:
        """Use the LLM to classify whether the task requires code execution."""
        # Build user prompt.
        datasets_summary = self._format_datasets_summary()
        user_prompt = TASK_TYPE_USER_PROMPT_TEMPLATE.format(
            datasets_info=datasets_summary,
            task_title=task_title,
            task_description=task_description
        )

        full_prompt = f"{TASK_TYPE_SYSTEM_PROMPT}\n\n{user_prompt}"

        try:
            response = self.llm_service.chat(prompt=full_prompt)
            response_text = response.strip()

            # Try to parse JSON.
            import json

            # Strip optional markdown fences.
            if response_text.startswith("```"):
                lines = response_text.splitlines()
                if lines: lines.pop(0)
                if lines and lines[-1].strip() == "```":
                    lines.pop()
                response_text = "\n".join(lines).strip()

            # Find the JSON object.
            start = response_text.find("{")
            end = response_text.rfind("}")
            if start != -1 and end != -1:
                json_str = response_text[start:end+1]
                result = json.loads(json_str)
                task_type_str = result.get("task_type", "code_required")

                if task_type_str == "text_only":
                    logger.info("Task type classification (LLM): TEXT_ONLY")
                    return TaskType.TEXT_ONLY
                else:
                    logger.info("Task type classification (LLM): CODE_REQUIRED")
                    return TaskType.CODE_REQUIRED

        except Exception as e:
            logger.warning(f"LLM task classification failed: {e}; defaulting to CODE_REQUIRED")

        # Default to code-required for safety.
        logger.info("Task type classification: CODE_REQUIRED (default)")
        return TaskType.CODE_REQUIRED

    async def _select_skills_for_task(
        self,
        task_title: str,
        task_description: str,
        *,
        tool_hints: Optional[List[str]] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """Select task skills and build the injected guidance block."""
        trace: Dict[str, Any] = {
            "candidate_skill_ids": [],
            "selected_skill_ids": [],
            "selection_source": "disabled",
            "injection_mode_by_skill": {},
            "injected_chars": 0,
            "selection_latency_ms": 0.0,
        }
        if not self.skill_settings.enable_skills or not self.skills_loader:
            return "", trace

        try:
            selection_result = await self.skills_loader.select_skills(
                task_title=task_title,
                task_description=task_description,
                llm_service=self.llm_service,
                dependency_paths=self.data_file_paths,
                tool_hints=tool_hints or [],
                selection_mode=self.skill_settings.skill_selection_mode,
                max_skills=self.skill_settings.skill_max_per_task,
                scope="task",
            )
            injection_result = self.skills_loader.build_skill_context(
                selection_result.selected_skill_ids,
                max_chars=self.skill_settings.skill_budget_chars,
            )
            trace = {
                "candidate_skill_ids": list(selection_result.candidate_skill_ids),
                "selected_skill_ids": list(selection_result.selected_skill_ids),
                "selection_source": selection_result.selection_source,
                "injection_mode_by_skill": dict(injection_result.injection_mode_by_skill),
                "injected_chars": int(injection_result.injected_chars),
                "selection_latency_ms": selection_result.selection_latency_ms,
            }
            if self.skill_settings.skill_trace_enabled:
                logger.info("TaskExecutor skill trace: %s", trace)
            if injection_result.content:
                return f"\n## Skill Guidance\n{injection_result.content}\n", trace
        except Exception as e:
            logger.warning(f"Skill guidance preparation failed: {e}")
        return "", trace

    async def _execute_code_task(
        self,
        task_title: str,
        task_description: str,
        subtask_results: str = "",
        is_visualization: bool = False,
        task_id: Optional[int] = None,
        skill_hints: str = "",
    ) -> TaskExecutionResult:
        """Execute a code-required task through Claude Code."""
        from tool_box.tools_impl.claude_code import claude_code_handler

        logger.info(f"Executing task with Claude Code: {task_title}")

        # Build enriched task description.
        datasets_summary = self._format_datasets_summary()

        # Build absolute data file list.
        data_files_info = '\n'.join([f"  - {fp}" for fp in self.data_file_paths])

        enhanced_task = f"""## Task: {task_title}

## Task Description
{task_description}

## Dataset Summary
{datasets_summary}

## Data File Paths (use these absolute paths)
{data_files_info}

## Directory Info
- Data directory: {self.data_dir}
- Output directory: {self.output_dir}
"""

        # Append optional skill guidance.
        if skill_hints:
            enhanced_task += skill_hints

        if subtask_results:
            enhanced_task += f"\n## Subtask Results (for reference)\n{subtask_results}\n"

        if is_visualization:
            enhanced_task += (
                "\n## Visualization Requirement\n"
                "Generate the requested visualizations and save image outputs to the output directory.\n"
            )

        # Build allowed directory list.
        add_dirs_list = [self.data_dir]
        if self.output_dir != self.data_dir:
            add_dirs_list.append(self.output_dir)
        add_dirs = ",".join(add_dirs_list)

        # Invoke Claude Code.
        try:
            strict_task_context = self.plan_id is not None and task_id is not None
            result = await claude_code_handler(
                task=enhanced_task,
                add_dirs=add_dirs,
                session_id=self.session_id,
                plan_id=self.plan_id,
                task_id=task_id,
                auth_mode="api_env",
                setting_sources="project",
                require_task_context=strict_task_context,
            )

            success = result.get("success", False)
            stdout = result.get("stdout", "")
            stderr = result.get("stderr", "")

            # Copy Claude Code artifacts to output_dir.
            # Handle all 4 sub-directories: results/, code/, data/, docs/.
            task_dir = result.get("task_directory_full", "")
            if task_dir and self.output_dir:
                subdirs_to_copy = ["results", "code", "data", "docs"]
                for subdir in subdirs_to_copy:
                    src_dir = Path(task_dir) / subdir
                    dst_dir = Path(self.output_dir) / subdir
                    if src_dir.exists():
                        dst_dir.mkdir(parents=True, exist_ok=True)
                        for f in src_dir.iterdir():
                            if f.is_file():
                                shutil.copy2(f, dst_dir / f.name)
                        logger.info(f"Copied artifacts from {src_dir} to {dst_dir}")

            # Parse output and infer visualization metadata.
            has_visualization = False
            visualization_purpose = None
            visualization_analysis = None

            # Detect generated image files.
            task_dir = result.get("task_directory_full", "")
            if task_dir:
                result_dir = Path(task_dir) / "results"
                if result_dir.exists():
                    image_extensions = {'.png', '.jpg', '.jpeg', '.svg', '.pdf'}
                    image_files = [f for f in result_dir.iterdir()
                                   if f.is_file() and f.suffix.lower() in image_extensions]
                    if image_files:
                        has_visualization = True
                        visualization_purpose = f"Visualization generated for task '{task_title}'"

            return TaskExecutionResult(
                task_type=TaskType.CODE_REQUIRED,
                success=success,
                final_code=None,  # Claude Code manages generated code internally.
                code_description=f"Task completed by Claude Code: {task_title}",
                code_output=stdout,
                code_error=stderr if stderr else None,
                total_attempts=1,  # Retries are handled internally by Claude Code.
                has_visualization=has_visualization,
                visualization_purpose=visualization_purpose,
                visualization_analysis=visualization_analysis,
                error_message=stderr if not success else None
            )

        except Exception as e:
            logger.error(f"Claude Code execution failed: {e}")
            return TaskExecutionResult(
                task_type=TaskType.CODE_REQUIRED,
                success=False,
                error_message=f"Claude Code execution failed: {str(e)}"
            )

    def _execute_text_task(
        self,
        task_title: str,
        task_description: str,
        subtask_results: str = "",
        gathered_info: str = "",
        skill_hints: str = "",
    ) -> TaskExecutionResult:
        """Execute a text-only task without writing code."""
        datasets_detail = self._format_all_datasets_detail()
        prompt = TEXT_TASK_PROMPT_TEMPLATE.format(
            datasets_info=datasets_detail,
            subtask_results=subtask_results if subtask_results else "(No sub-task results)",
            gathered_info=gathered_info if gathered_info else "(No additional information gathered)",
            task_title=task_title,
            task_description=task_description
        )
        if skill_hints:
            prompt += f"\n{skill_hints.strip()}\n"

        response = self.llm_service.chat(prompt=prompt)
        return TaskExecutionResult(
            task_type=TaskType.TEXT_ONLY,
            success=True,
            text_response=response,
            gathered_info=gathered_info if gathered_info else None
        )

    def _format_all_datasets_detail(self) -> str:
        """Format detailed dataset information, including column-level details."""
        details = []
        for i, metadata in enumerate(self.metadata_list, 1):
            cols_text = self._format_columns_for_metadata(metadata)
            detail = f"""### Dataset {i}: {metadata.filename}
- Format: {metadata.file_format}
- Rows: {metadata.total_rows}
- Columns: {metadata.total_columns}
- Column Details:
{cols_text}"""
            details.append(detail)
        return "\n\n".join(details)

    def _format_columns_for_metadata(self, metadata: DatasetMetadata) -> str:
        """Format column details for a single dataset."""
        lines = []
        for col in metadata.columns[:20]:
            lines.append(f"  - {col.name} ({col.dtype}): sample values {col.sample_values[:3]}")
        if len(metadata.columns) > 20:
            lines.append(f"  ... ({len(metadata.columns) - 20} more columns)")
        return "\n".join(lines)

    async def execute(
        self,
        task_title: str,
        task_description: str,
        subtask_results: str = "",
        force_code: Optional[bool] = None,
        skip_info_gathering: bool = True,  # Deprecated: Claude Code handles this internally.
        is_visualization: bool = False,
        task_id: Optional[int] = None,
    ) -> TaskExecutionResult:
        """Main async entrypoint for executing a task."""
        logger.info(f"Starting task execution: {task_title}")

        # Determine task type.
        if force_code is True:
            task_type = TaskType.CODE_REQUIRED
            logger.info("Task type: CODE_REQUIRED (forced)")
        elif force_code is False:
            task_type = TaskType.TEXT_ONLY
            logger.info("Task type: TEXT_ONLY (forced)")
        else:
            task_type = self._analyze_task_type(task_title, task_description)

        tool_hints: List[str] = []
        task_text = f"{task_title}\n{task_description}".lower()
        if task_type == TaskType.CODE_REQUIRED:
            tool_hints.append("claude_code")
        if any(str(path).lower().endswith((".fasta", ".fa", ".fna", ".fastq", ".fq", ".sam", ".bam")) for path in self.data_file_paths) or any(
            token in task_text
            for token in ("fasta", "fastq", "sequence", "genome", "alignment", "assembly", "annotation", "phage")
        ):
            tool_hints.append("bio_tools")
        if any(token in task_text for token in ("report", "paper", "manuscript", "methods", "results")):
            tool_hints.append("manuscript_writer")
        skill_hints, _skill_trace = await self._select_skills_for_task(
            task_title,
            task_description,
            tool_hints=sorted(set(tool_hints)),
        )

        # Execute by task type.
        if task_type == TaskType.CODE_REQUIRED:
            # Use Claude Code for code-required tasks.
            result = await self._execute_code_task(
                task_title,
                task_description,
                subtask_results=subtask_results,
                is_visualization=is_visualization,
                task_id=task_id,
                skill_hints=skill_hints,
            )
        else:
            # Text-only tasks are handled directly by LLM.
            result = self._execute_text_task(
                task_title,
                task_description,
                subtask_results=subtask_results,
                gathered_info="",
                skill_hints=skill_hints,
            )

        logger.info(f"Task execution finished: success={result.success}")
        if self.skill_settings.skill_trace_enabled:
            result.skill_trace = _skill_trace
        else:
            result.skill_trace = None

        return result


# ============================================================
# ============================================================

async def execute_task(
    data_file_paths: List[str],
    task_title: str,
    task_description: str,
    subtask_results: str = "",
    skip_info_gathering: bool = True,
    is_visualization: bool = False,
    **kwargs
) -> TaskExecutionResult:
    """
    Async helper to execute one task with a temporary `TaskExecutor`.

    Args:
        data_file_paths: Input dataset paths.
        task_title: Task title.
        task_description: Task description/instruction.
        subtask_results: Optional upstream task outputs.
        skip_info_gathering: Deprecated; retained for compatibility.
        is_visualization: Whether visualization output is expected.
        **kwargs: Additional `TaskExecutor` parameters.

    Returns:
        TaskExecutionResult.
    """
    executor = TaskExecutor(data_file_paths=data_file_paths, **kwargs)
    try:
        return await executor.execute(
            task_title=task_title,
            task_description=task_description,
            subtask_results=subtask_results,
            skip_info_gathering=skip_info_gathering,
            is_visualization=is_visualization
        )
    finally:
        executor.cleanup()


def execute_task_sync(
    data_file_paths: List[str],
    task_title: str,
    task_description: str,
    subtask_results: str = "",
    skip_info_gathering: bool = True,
    is_visualization: bool = False,
    **kwargs
) -> TaskExecutionResult:
    """
    Sync helper wrapping `execute_task` with `asyncio.run`.

    Args:
        data_file_paths: Input dataset paths.
        task_title: Task title.
        task_description: Task description/instruction.
        subtask_results: Optional upstream task outputs.
        skip_info_gathering: Deprecated; retained for compatibility.
        is_visualization: Whether visualization output is expected.
        **kwargs: Additional `TaskExecutor` parameters.

    Returns:
        TaskExecutionResult.
    """
    return asyncio.run(execute_task(
        data_file_paths=data_file_paths,
        task_title=task_title,
        task_description=task_description,
        subtask_results=subtask_results,
        skip_info_gathering=skip_info_gathering,
        is_visualization=is_visualization,
        **kwargs
    ))
