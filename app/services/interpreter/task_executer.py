"""
任务执行器模块

该模块封装了任务执行的完整流程。
自动判断任务类型，对需要代码的任务使用 Claude Code 执行，对不需要代码的任务直接由LLM处理。

重构说明：
- 原流程：LLM生成代码 → LocalCodeInterpreter执行 → 失败 → LLM修复 → 重试
- 新流程：任务描述 → claude_code_handler 自主完成一切 → 结果

Skills 集成：
- Skills 源文件存放在项目 skills/ 目录
- 运行时同步到 ~/.claude/skills/
- Claude Code 自动加载并根据任务语义决定使用哪些 skills
- 可选：使用 LLM 驱动的 skill 选择进行更精细控制
"""

import asyncio
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional, List
from enum import Enum

from pydantic import BaseModel, Field

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
    """任务类型枚举"""
    CODE_REQUIRED = "code_required"      # 需要编写代码的任务（计算、绘图、数据处理等）
    TEXT_ONLY = "text_only"              # 纯文本任务（解释、总结、问答等）
    

class TaskExecutionResult(BaseModel):
    """任务执行的最终结果"""
    task_type: TaskType = Field(..., description="任务类型")
    success: bool = Field(..., description="任务是否成功完成")
    
    # 代码相关（仅当 task_type == CODE_REQUIRED 时有值）
    final_code: Optional[str] = Field(None, description="最终执行的代码")
    code_description: Optional[str] = Field(None, description="代码功能描述")
    code_output: Optional[str] = Field(None, description="代码执行的标准输出")
    code_error: Optional[str] = Field(None, description="代码执行的错误信息")
    total_attempts: int = Field(0, description="代码执行总尝试次数")
    
    # 可视化相关
    has_visualization: bool = Field(default=False, description="是否包含可视化")
    visualization_purpose: Optional[str] = Field(None, description="可视化目的：为什么画这个图，想分析什么")
    visualization_analysis: Optional[str] = Field(None, description="可视化分析：图表展示什么结果，特征，计算公式等")
    
    # 文本相关（仅当 task_type == TEXT_ONLY 时有值）
    text_response: Optional[str] = Field(None, description="LLM直接回答的文本")
    
    # 信息收集相关
    gathered_info: Optional[str] = Field(None, description="信息收集阶段获取的额外数据信息")
    info_gathering_rounds: int = Field(0, description="信息收集轮次")
    
    # 通用
    error_message: Optional[str] = Field(None, description="系统级错误信息")


class TaskExecutor:
    """
    任务执行器

    使用 Claude Code 自主完成代码生成、执行和修复的完整流程。
    自动判断任务类型，对需要代码的任务使用 Claude Code 执行，
    对不需要代码的任务直接由LLM处理。

    使用示例:
        executor = TaskExecutor(data_file_paths=["/path/to/data1.csv", "/path/to/data2.csv"])
        result = await executor.execute(
            task_title="计算平均值",
            task_description="计算销售额的平均值并绘制柱状图"
        )
    """

    def __init__(
        self,
        data_file_paths: List[str],
        llm_service: Optional[LLMService] = None,
        docker_image: str = "agent-plotter",  # 保留参数兼容性，但不再使用
        docker_timeout: int = 60,  # 保留参数兼容性，但不再使用
        output_dir: Optional[str] = None,
        session_id: Optional[str] = None,
        plan_id: Optional[int] = None,
    ):
        """
        初始化任务执行器

        Args:
            data_file_paths: 数据文件路径列表（支持 csv, tsv, mat 格式）
            llm_service: LLM 服务实例（可选，默认使用 get_llm_service()）
            docker_image: [已废弃] Docker镜像名称（保留兼容性）
            docker_timeout: [已废弃] Docker执行超时时间（保留兼容性）
            output_dir: 输出目录，Claude Code生成的文件将保存在此目录
                       如果不指定，则使用数据文件所在目录
            session_id: 会话ID，用于 Claude Code 工作区隔离
            plan_id: 计划ID，用于 Claude Code 工作区隔离
        """
        # 兼容单个文件路径的情况
        if isinstance(data_file_paths, str):
            data_file_paths = [data_file_paths]

        # 临时目录引用（用于清理）
        self._staging_dir: Optional[str] = None

        # 如果数据文件分散在多个目录，统一复制到临时目录
        data_dirs = {str(Path(fp).resolve().parent) for fp in data_file_paths}
        if len(data_dirs) > 1:
            staging_dir = tempfile.mkdtemp(prefix="interpreter_data_")
            self._staging_dir = staging_dir  # 保存引用以便后续清理
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
        task_description: str
    ) -> List[str]:
        """Use LLM semantic understanding to select relevant skills."""
        if not self.skills_loader:
            return []

        try:
            selected = await self.skills_loader.select_skills_for_task(
                task_title=task_title,
                task_description=task_description,
                llm_service=self.llm_service
            )
            return selected
        except Exception as e:
            logger.warning(f"LLM skill selection failed: {e}")
            return []

    async def _execute_code_task(
        self,
        task_title: str,
        task_description: str,
        subtask_results: str = "",
        is_visualization: bool = False,
        task_id: Optional[int] = None,
        use_skill_hints: bool = True,
    ) -> TaskExecutionResult:
        """Execute a code-required task through Claude Code."""
        from tool_box.tools_impl.claude_code import claude_code_handler

        logger.info(f"Executing task with Claude Code: {task_title}")

        # Optionally ask LLM to select relevant skills.
        skill_hints = ""
        if use_skill_hints and self.skills_loader:
            selected_skills = await self._select_skills_for_task(task_title, task_description)
            if selected_skills:
                skill_hints = "\n## Suggested Skills\n"
                skill_hints += "The following skills may help with this task:\n"
                for skill_name in selected_skills:
                    skill_hints += f"- {skill_name}\n"
                logger.info(f"LLM selected skills: {selected_skills}")

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

        # Append optional skill hints.
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
        gathered_info: str = ""
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

        # Execute by task type.
        if task_type == TaskType.CODE_REQUIRED:
            # Use Claude Code for code-required tasks.
            result = await self._execute_code_task(
                task_title,
                task_description,
                subtask_results=subtask_results,
                is_visualization=is_visualization,
                task_id=task_id,
            )
        else:
            # Text-only tasks are handled directly by LLM.
            result = self._execute_text_task(
                task_title,
                task_description,
                subtask_results=subtask_results,
                gathered_info=""
            )

        logger.info(f"Task execution finished: success={result.success}")

        # 注意：不再在此处调用 cleanup()
        # 当 PlanExecutorInterpreter 复用同一个 TaskExecutor 执行多个节点时，
        # 过早清理 staging 目录会导致后续节点找不到数据文件。
        # cleanup() 应由调用方在所有节点执行完成后统一调用，
        # 或由析构函数 __del__ 自动处理。

        return result


# ============================================================
# 便捷函数
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
    便捷函数：一次性执行任务（异步版本）

    Args:
        data_file_paths: 数据文件路径列表（也支持单个路径字符串）
        task_title: 任务标题
        task_description: 任务描述
        subtask_results: 子任务结果（可选）
        skip_info_gathering: [已废弃] Claude Code 自主处理信息收集
        is_visualization: 是否为可视化任务
        **kwargs: 传递给TaskExecutor的其他参数

    Returns:
        TaskExecutionResult: 任务执行结果
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
        # 显式清理临时 staging 目录，不依赖 __del__
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
    便捷函数：一次性执行任务（同步包装版本）

    在非异步上下文中使用此函数。

    Args:
        data_file_paths: 数据文件路径列表（也支持单个路径字符串）
        task_title: 任务标题
        task_description: 任务描述
        subtask_results: 子任务结果（可选）
        skip_info_gathering: [已废弃] Claude Code 自主处理信息收集
        is_visualization: 是否为可视化任务
        **kwargs: 传递给TaskExecutor的其他参数

    Returns:
        TaskExecutionResult: 任务执行结果
    """
    # execute_task 内部已有 try/finally 处理 cleanup，直接调用即可
    return asyncio.run(execute_task(
        data_file_paths=data_file_paths,
        task_title=task_title,
        task_description=task_description,
        subtask_results=subtask_results,
        skip_info_gathering=skip_info_gathering,
        is_visualization=is_visualization,
        **kwargs
    ))
