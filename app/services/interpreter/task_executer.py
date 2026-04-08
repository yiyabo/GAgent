"""
Task execution orchestration.

This module routes tasks to one of two paths:
1. Code-required tasks executed locally (LLM generates code → subprocess runs it)
   or via Claude Code CLI (legacy, configurable via CODE_EXECUTION_BACKEND).
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
from uuid import uuid4

from pydantic import BaseModel, Field

from app.config.executor_config import (
    get_executor_settings,
    resolve_code_execution_docker_image,
)
from app.services.llm.llm_service import LLMService, get_llm_service
from app.services.skills import SkillsLoader, get_skills_loader
from .code_execution import execute_code_locally, CodeExecutionOutcome
from .metadata import DatasetMetadata, DataProcessor
from .prompts.task_executer import (
    TASK_TYPE_SYSTEM_PROMPT,
    TASK_TYPE_USER_PROMPT_TEMPLATE,
    TEXT_TASK_PROMPT_TEMPLATE,
)

logger = logging.getLogger(__name__)

_LIGHTWEIGHT_OVERVIEW_CUES = (
    "dataset overview",
    "data overview",
    "overview",
    "quick overview",
    "quick summary",
    "schema",
    "metadata",
    "preview",
    "what's inside",
    "what is inside",
    "row count",
    "row counts",
    "column",
    "columns",
    "column names",
    "headers",
    "header",
    "structure",
    "inspect",
    "inside",
    "数据概览",
    "概览",
    "概况",
    "总览",
    "快速概览",
    "结构",
    "元数据",
    "预览",
    "列名",
    "表头",
    "字段",
    "行数",
    "列数",
    "内容",
    "里面有什么",
    "有哪些数据",
)

_LIGHTWEIGHT_SAMPLE_CUES = (
    "sample",
    "samples",
    "example",
    "examples",
    "preview",
    "head",
    "示例",
    "样例",
    "样本",
    "前几行",
)

_HEAVY_ANALYSIS_CUES = (
    "plot",
    "plots",
    "figure",
    "figures",
    "visualization",
    "visualize",
    "chart",
    "heatmap",
    "scatter",
    "histogram",
    "umap",
    "tsne",
    "pca",
    "graph",
    "network",
    "cluster",
    "clustering",
    "integrate",
    "integration",
    "harmony",
    "bbknn",
    "batch correction",
    "regression",
    "model",
    "train",
    "predict",
    "classification",
    "differential",
    "enrichment",
    "gsea",
    "kegg",
    "gene set",
    "anova",
    "statistical test",
    "correlation",
    "transform",
    "merge",
    "filter",
    "export",
    "run code",
    "画图",
    "绘图",
    "可视化",
    "图表",
    "热图",
    "散点",
    "聚类",
    "整合",
    "批次校正",
    "回归",
    "模型",
    "训练",
    "预测",
    "分类",
    "差异",
    "富集",
    "统计检验",
    "相关性",
    "转换",
    "合并",
    "筛选",
    "导出",
    "执行代码",
)


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
        docker_image: Optional[str] = None,
        docker_timeout: Optional[int] = None,
        output_dir: Optional[str] = None,
        session_id: Optional[str] = None,
        plan_id: Optional[int] = None,
    ):
        """
        Initialize task executor.

        Args:
            data_file_paths: Dataset file paths (e.g., CSV/TSV/MAT).
            llm_service: Optional LLM service instance.
            docker_image: Optional Docker image override for code execution tasks.
            docker_timeout: Optional execution timeout override for code tasks.
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
        self.docker_image = str(docker_image).strip() if docker_image else None
        self.docker_timeout = int(docker_timeout) if docker_timeout and int(docker_timeout) > 0 else None

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
- Key columns: {", ".join(col.name for col in metadata.columns[:6])}"""
            summaries.append(summary)
        return "\n\n".join(summaries)

    @staticmethod
    def _format_sample_values(values: Any, *, limit: int = 3) -> str:
        if not isinstance(values, list):
            return ""
        cleaned = [
            str(value).strip()
            for value in values[:limit]
            if value is not None and str(value).strip()
        ]
        return ", ".join(cleaned)

    def _is_lightweight_overview_task(
        self,
        task_title: str,
        task_description: str,
        *,
        is_visualization: bool = False,
    ) -> bool:
        if is_visualization or not self.metadata_list:
            return False
        combined = f"{task_title}\n{task_description}".lower()
        if any(token in combined for token in _HEAVY_ANALYSIS_CUES):
            return False
        return any(token in combined for token in _LIGHTWEIGHT_OVERVIEW_CUES)

    def _build_lightweight_overview_result(
        self,
        task_title: str,
        task_description: str,
    ) -> TaskExecutionResult:
        combined = f"{task_title}\n{task_description}".lower()
        include_samples = any(token in combined for token in _LIGHTWEIGHT_SAMPLE_CUES)
        lines = ["Dataset overview based on extracted metadata:"]

        for metadata in self.metadata_list[:5]:
            column_names = [
                str(getattr(col, "name", "") or "").strip()
                for col in getattr(metadata, "columns", [])[:8]
                if str(getattr(col, "name", "") or "").strip()
            ]
            line = (
                f"- {metadata.filename}: {metadata.file_format}, "
                f"{metadata.total_rows} rows x {metadata.total_columns} columns"
            )
            if column_names:
                line += f"; key columns: {', '.join(column_names)}"
            if include_samples:
                sample_parts: List[str] = []
                for col in getattr(metadata, "columns", [])[:3]:
                    name = str(getattr(col, "name", "") or "").strip()
                    sample_text = self._format_sample_values(getattr(col, "sample_values", []))
                    if name and sample_text:
                        sample_parts.append(f"{name}={sample_text}")
                if sample_parts:
                    line += f"; sample values: {'; '.join(sample_parts)}"
            lines.append(line)

        if len(self.metadata_list) > 5:
            lines.append(f"- {len(self.metadata_list) - 5} additional dataset(s) omitted for brevity.")

        lines.append("This is a metadata-level overview; no code execution or plotting was required.")
        return TaskExecutionResult(
            task_type=TaskType.TEXT_ONLY,
            success=True,
            text_response="\n".join(lines),
            code_description="Direct metadata overview",
            total_attempts=1,
            gathered_info="metadata_overview",
        )

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
            if selection_result.selected_skill_ids:
                skill_list = ", ".join(selection_result.selected_skill_ids)
                return (
                    "\n## Skill Hints\n"
                    f"- Relevant skills: {skill_list}\n"
                    "- Use them only if they materially help complete this task.\n"
                ), trace
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
        """Execute a code-required task.

        Dispatches to local execution (LLM generates code → subprocess runs it)
        or Claude Code CLI based on the CODE_EXECUTION_BACKEND setting.
        """
        settings = get_executor_settings()
        if settings.code_execution_backend in ("claude_code", "qwen_code"):
            return await self._execute_code_task_legacy_cli(
                task_title, task_description, subtask_results,
                is_visualization, task_id, skill_hints,
            )

        return await self._execute_code_task_local(
            task_title, task_description, subtask_results,
            is_visualization, task_id, skill_hints,
        )

    @staticmethod
    def _local_code_filename(task_id: Optional[int]) -> str:
        if task_id is not None:
            return f"task_{int(task_id)}_code.py"
        return f"task_code_{uuid4().hex[:8]}.py"

    async def _execute_code_task_local(
        self,
        task_title: str,
        task_description: str,
        subtask_results: str = "",
        is_visualization: bool = False,
        task_id: Optional[int] = None,
        skill_hints: str = "",
    ) -> TaskExecutionResult:
        """Execute a code-required task via unified local code execution."""
        settings = get_executor_settings()
        local_runtime = settings.code_execution_local_runtime
        execution_backend = "docker" if local_runtime == "docker" else "local"
        docker_image = None
        if execution_backend == "docker":
            docker_image = resolve_code_execution_docker_image(
                self.docker_image,
                default=settings.code_execution_docker_image,
            )
        execution_timeout = self.docker_timeout or settings.code_execution_timeout

        logger.info(
            "Executing task with %s runtime: %s",
            local_runtime,
            task_title,
        )

        # ---- Build augmented task description ----
        data_files_info = '\n'.join([f"  - {fp}" for fp in self.data_file_paths])
        augmented_desc = f"""{task_description}

## Data Files (absolute paths)
{data_files_info}

## Directories
- Data directory: {self.data_dir}
- Output directory: {self.output_dir}
- Save results to: {self.output_dir}/results/
"""
        if subtask_results:
            augmented_desc += f"\n## Subtask Results (for reference)\n{subtask_results}\n"
        if is_visualization:
            augmented_desc += (
                "\n## Visualization Requirement\n"
                "Generate the requested visualizations and save image outputs "
                f"to {self.output_dir}/results/\n"
            )
        if skill_hints:
            augmented_desc += skill_hints

        # ---- Delegate to unified execution function ----
        outcome: CodeExecutionOutcome = await execute_code_locally(
            task_title=task_title,
            task_description=augmented_desc,
            metadata_list=self.metadata_list,
            llm_service=self.llm_service,
            work_dir=self.output_dir,
            data_dir=self.data_dir,
            code_filename=self._local_code_filename(task_id),
            timeout=execution_timeout,
            execution_backend=execution_backend,
            docker_image=docker_image,
        )

        viz_purpose = None
        if outcome.visualization_files:
            viz_purpose = f"Visualization generated for task '{task_title}'"

        return TaskExecutionResult(
            task_type=TaskType.CODE_REQUIRED,
            success=outcome.success,
            final_code=outcome.code,
            code_description=outcome.description,
            code_output=outcome.stdout,
            code_error=outcome.stderr if not outcome.success else None,
            total_attempts=outcome.attempts,
            has_visualization=outcome.has_visualization,
            visualization_purpose=viz_purpose or outcome.visualization_purpose,
            visualization_analysis=outcome.visualization_analysis,
            error_message=outcome.stderr if not outcome.success else None,
        )

    async def _execute_code_task_legacy_cli(
        self,
        task_title: str,
        task_description: str,
        subtask_results: str = "",
        is_visualization: bool = False,
        task_id: Optional[int] = None,
        skill_hints: str = "",
    ) -> TaskExecutionResult:
        """Legacy: execute a code-required task through Claude Code CLI."""
        from tool_box.tools_impl.code_executor import code_executor_handler

        logger.info(f"Executing task with Claude Code: {task_title}")

        # Build enriched task description.
        datasets_summary = self._format_datasets_summary()
        data_files_info = '\n'.join([f"  - {fp}" for fp in self.data_file_paths])

        enhanced_task = f"""## Task
{task_title}

## Objective
{task_description}

## Datasets
{datasets_summary}

## Data Files
{data_files_info}

## Directories
- Data: {self.data_dir}
- Output: {self.output_dir}
"""

        if skill_hints:
            enhanced_task += skill_hints
        if subtask_results:
            enhanced_task += f"\n## Subtask Results (for reference)\n{subtask_results}\n"
        if is_visualization:
            enhanced_task += (
                "\n## Visualization Requirement\n"
                "Generate the requested visualizations and save image outputs to the output directory.\n"
            )

        add_dirs_list = [self.data_dir]
        if self.output_dir != self.data_dir:
            add_dirs_list.append(self.output_dir)
        add_dirs = ",".join(add_dirs_list)

        try:
            strict_task_context = self.plan_id is not None and task_id is not None
            result = await code_executor_handler(
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

            err_text: Optional[str] = None
            if not success:
                err_text = (stderr.strip() if stderr else "") or (
                    str(result.get("error") or "").strip()
                )
                if not err_text:
                    rc = result.get("exit_code")
                    err_text = (
                        f"Claude Code exited with exit_code={rc}"
                        if rc is not None
                        else "Claude Code finished without success"
                    )

            # Copy Claude Code artifacts to output_dir.
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

            has_visualization = False
            visualization_purpose = None
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
                final_code=None,
                code_description=f"Task completed by Claude Code: {task_title}",
                code_output=stdout,
                code_error=(stderr if stderr else err_text) if not success else None,
                total_attempts=1,
                has_visualization=has_visualization,
                visualization_purpose=visualization_purpose,
                error_message=err_text,
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
        lightweight_overview = self._is_lightweight_overview_task(
            task_title,
            task_description,
            is_visualization=is_visualization,
        )

        # Determine task type.
        if force_code is True:
            task_type = TaskType.CODE_REQUIRED
            logger.info("Task type: CODE_REQUIRED (forced)")
        elif force_code is False:
            task_type = TaskType.TEXT_ONLY
            logger.info("Task type: TEXT_ONLY (forced)")
        elif lightweight_overview:
            task_type = TaskType.TEXT_ONLY
            logger.info("Task type: TEXT_ONLY (lightweight overview heuristic)")
        else:
            task_type = self._analyze_task_type(task_title, task_description)

        tool_hints: List[str] = []
        task_text = f"{task_title}\n{task_description}".lower()
        if task_type == TaskType.CODE_REQUIRED:
            tool_hints.append("code_executor")
        if any(str(path).lower().endswith((".fasta", ".fa", ".fna", ".fastq", ".fq", ".sam", ".bam")) for path in self.data_file_paths) or any(
            token in task_text
            for token in ("fasta", "fastq", "sequence", "genome", "alignment", "assembly", "annotation", "phage")
        ):
            tool_hints.append("bio_tools")
        if any(token in task_text for token in ("report", "paper", "manuscript", "methods", "results")):
            tool_hints.append("manuscript_writer")
        skill_hints = ""
        if lightweight_overview:
            _skill_trace = {
                "candidate_skill_ids": [],
                "selected_skill_ids": [],
                "selection_source": "skipped_lightweight_overview",
                "injection_mode_by_skill": {},
                "injected_chars": 0,
                "selection_latency_ms": 0.0,
            }
        else:
            skill_hints, _skill_trace = await self._select_skills_for_task(
                task_title,
                task_description,
                tool_hints=sorted(set(tool_hints)),
            )

        # Execute by task type.
        if lightweight_overview:
            result = self._build_lightweight_overview_result(task_title, task_description)
        elif task_type == TaskType.CODE_REQUIRED:
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
