"""
任务执行器模块

该模块封装了任务执行的完整流程。
自动判断任务类型，对需要代码的任务使用 Claude Code 执行，对不需要代码的任务直接由LLM处理。

重构说明：
- 原流程：LLM生成代码 → LocalCodeInterpreter执行 → 失败 → LLM修复 → 重试
- 新流程：任务描述 → claude_code_handler 自主完成一切 → 结果
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

        # 获取数据文件所在目录
        data_path = Path(data_file_paths[0]).resolve()
        self.data_dir = str(data_path.parent)
        self.data_filenames = [Path(fp).name for fp in data_file_paths]  # 纯文件名列表

        # 设置输出目录
        if output_dir:
            self.output_dir = str(Path(output_dir).resolve())
            # 确保输出目录存在
            Path(self.output_dir).mkdir(parents=True, exist_ok=True)
        else:
            self.output_dir = self.data_dir

        # 解析所有数据文件的元数据
        self.metadata_list: List[DatasetMetadata] = []
        for fp in data_file_paths:
            logger.info(f"正在解析数据文件元数据: {fp}")
            metadata = DataProcessor.get_metadata(fp)
            self.metadata_list.append(metadata)
            logger.info(f"元数据解析完成: {metadata.filename} - {metadata.total_rows}行 x {metadata.total_columns}列")

        # 初始化LLM服务（仅用于任务类型判断和文本任务）
        self.llm_service = llm_service or get_llm_service()

        # Claude Code 会话隔离参数
        self.session_id = session_id
        self.plan_id = plan_id

        logger.info(f"TaskExecutor 初始化: data_dir={self.data_dir}, output_dir={self.output_dir}")

    def cleanup(self) -> None:
        """清理临时目录（如果存在）"""
        if self._staging_dir and os.path.isdir(self._staging_dir):
            try:
                shutil.rmtree(self._staging_dir)
                logger.info(f"已清理临时目录: {self._staging_dir}")
            except Exception as e:
                logger.warning(f"清理临时目录失败: {self._staging_dir}, 错误: {e}")
            finally:
                self._staging_dir = None

    def __del__(self):
        """析构时自动清理临时目录"""
        self.cleanup()

    def _format_datasets_summary(self) -> str:
        """格式化所有数据集的摘要信息"""
        summaries = []
        for i, metadata in enumerate(self.metadata_list, 1):
            summary = f"""### 数据集 {i}: {metadata.filename}
- 格式: {metadata.file_format}
- 行数: {metadata.total_rows}
- 列数: {metadata.total_columns}
- 数据样例(sample size: 3*3): {"; ".join(
            f"{col.name}: {col.sample_values[:3]}"
            for col in metadata.columns[:3]
        )}"""
            summaries.append(summary)
        return "\n\n".join(summaries)

    def _analyze_task_type(self, task_title: str, task_description: str) -> TaskType:
        """
        使用LLM分析任务类型，判断是否需要编写代码
        
        Args:
            task_title: 任务标题
            task_description: 任务描述
            
        Returns:
            TaskType: 任务类型
        """
        # 构建用户提示词
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
            
            # 尝试解析JSON
            import json
            
            # 清理可能的markdown标记
            if response_text.startswith("```"):
                lines = response_text.splitlines()
                if lines: lines.pop(0)
                if lines and lines[-1].strip() == "```":
                    lines.pop()
                response_text = "\n".join(lines).strip()
            
            # 尝试找到JSON
            start = response_text.find("{")
            end = response_text.rfind("}")
            if start != -1 and end != -1:
                json_str = response_text[start:end+1]
                result = json.loads(json_str)
                task_type_str = result.get("task_type", "code_required")
                
                if task_type_str == "text_only":
                    logger.info(f"任务类型判断 (LLM): TEXT_ONLY")
                    return TaskType.TEXT_ONLY
                else:
                    logger.info(f"任务类型判断 (LLM): CODE_REQUIRED")
                    return TaskType.CODE_REQUIRED
            
        except Exception as e:
            logger.warning(f"LLM任务类型判断失败: {e}，默认使用CODE_REQUIRED")
        
        # 默认认为需要代码
        logger.info(f"任务类型判断: CODE_REQUIRED (默认)")
        return TaskType.CODE_REQUIRED

    async def _execute_code_task(
        self,
        task_title: str,
        task_description: str,
        subtask_results: str = "",
        is_visualization: bool = False,
        task_id: Optional[int] = None,
    ) -> TaskExecutionResult:
        """
        使用 Claude Code 执行需要代码的任务

        Claude Code 会自主完成：
        - 代码生成
        - 代码执行
        - 错误修复和重试
        - 信息收集（如需要）

        Args:
            task_title: 任务标题
            task_description: 任务描述
            subtask_results: 子任务结果
            is_visualization: 是否为可视化任务
            task_id: 任务ID（用于工作区隔离）
        """
        from tool_box.tools_impl.claude_code import claude_code_handler

        logger.info(f"使用 Claude Code 执行任务: {task_title}")

        # 构建增强的任务描述
        datasets_summary = self._format_datasets_summary()

        # 构建数据文件绝对路径列表
        data_files_info = '\n'.join([f"  - {fp}" for fp in self.data_file_paths])

        enhanced_task = f"""## 任务: {task_title}

## 任务描述:
{task_description}

## 数据文件信息:
{datasets_summary}

## 数据文件路径（请使用这些绝对路径读取数据）:
{data_files_info}

## 目录信息:
- 数据目录: {self.data_dir}
- 输出目录: {self.output_dir}
"""

        if subtask_results:
            enhanced_task += f"\n## 子任务执行结果（可作为参考）:\n{subtask_results}\n"

        if is_visualization:
            enhanced_task += "\n## 特殊要求:\n请生成可视化图表，并将图片保存到输出目录。\n"

        # 构建允许访问的目录列表
        add_dirs_list = [self.data_dir]
        if self.output_dir != self.data_dir:
            add_dirs_list.append(self.output_dir)
        add_dirs = ",".join(add_dirs_list)

        # 调用 Claude Code 执行任务
        try:
            result = await claude_code_handler(
                task=enhanced_task,
                add_dirs=add_dirs,
                session_id=self.session_id,
                plan_id=self.plan_id,
                task_id=task_id,
            )

            success = result.get("success", False)
            stdout = result.get("stdout", "")
            stderr = result.get("stderr", "")

            # 复制 Claude Code 产出文件到 output_dir
            task_dir = result.get("task_directory_full", "")
            if task_dir and self.output_dir:
                src_results = Path(task_dir) / "results"
                dst_results = Path(self.output_dir) / "results"
                if src_results.exists():
                    dst_results.mkdir(parents=True, exist_ok=True)
                    for f in src_results.iterdir():
                        if f.is_file():
                            shutil.copy2(f, dst_results / f.name)
                    logger.info(f"已复制产出文件从 {src_results} 到 {dst_results}")

            # 解析输出，尝试提取可视化信息
            has_visualization = False
            visualization_purpose = None
            visualization_analysis = None

            # 检查是否生成了图片文件
            task_dir = result.get("task_directory_full", "")
            if task_dir:
                result_dir = Path(task_dir) / "results"
                if result_dir.exists():
                    image_extensions = {'.png', '.jpg', '.jpeg', '.svg', '.pdf'}
                    image_files = [f for f in result_dir.iterdir()
                                   if f.is_file() and f.suffix.lower() in image_extensions]
                    if image_files:
                        has_visualization = True
                        visualization_purpose = f"任务 '{task_title}' 生成的可视化"

            return TaskExecutionResult(
                task_type=TaskType.CODE_REQUIRED,
                success=success,
                final_code=None,  # Claude Code 自主管理代码
                code_description=f"由 Claude Code 自主完成任务: {task_title}",
                code_output=stdout,
                code_error=stderr if stderr else None,
                total_attempts=1,  # Claude Code 内部处理重试
                has_visualization=has_visualization,
                visualization_purpose=visualization_purpose,
                visualization_analysis=visualization_analysis,
                error_message=stderr if not success else None
            )

        except Exception as e:
            logger.error(f"Claude Code 执行失败: {e}")
            return TaskExecutionResult(
                task_type=TaskType.CODE_REQUIRED,
                success=False,
                error_message=f"Claude Code 执行失败: {str(e)}"
            )

    def _execute_text_task(
        self,
        task_title: str,
        task_description: str,
        subtask_results: str = "",
        gathered_info: str = ""
    ) -> TaskExecutionResult:
        """
        执行纯文本任务（不需要代码）
        """
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
        """格式化所有数据集的详细信息（包含列信息）"""
        details = []
        for i, metadata in enumerate(self.metadata_list, 1):
            cols_text = self._format_columns_for_metadata(metadata)
            detail = f"""### 数据集 {i}: {metadata.filename}
- 格式: {metadata.file_format}
- 行数: {metadata.total_rows}
- 列数: {metadata.total_columns}
- 列信息:
{cols_text}"""
            details.append(detail)
        return "\n\n".join(details)

    def _format_columns_for_metadata(self, metadata: DatasetMetadata) -> str:
        """格式化单个数据集的列信息"""
        lines = []
        for col in metadata.columns[:20]:
            lines.append(f"  - {col.name} ({col.dtype}): 样例值 {col.sample_values[:3]}")
        if len(metadata.columns) > 20:
            lines.append(f"  ... (还有 {len(metadata.columns) - 20} 列)")
        return "\n".join(lines)

    async def execute(
        self,
        task_title: str,
        task_description: str,
        subtask_results: str = "",
        force_code: Optional[bool] = None,
        skip_info_gathering: bool = True,  # 默认跳过，Claude Code 自主处理
        is_visualization: bool = False,
        task_id: Optional[int] = None,
    ) -> TaskExecutionResult:
        """
        执行任务的主入口（异步版本）
        自动判断任务类型并执行相应的处理流程。

        对于 CODE_REQUIRED 任务，使用 Claude Code 自主完成：
        - 信息收集（如需要）
        - 代码生成
        - 代码执行
        - 错误修复和重试

        Args:
            task_title: 任务标题
            task_description: 任务描述
            subtask_results: 子任务结果（用于非叶子节点）
            force_code: 强制指定任务类型（True=代码任务, False=文本任务, None=自动判断）
            skip_info_gathering: [已废弃] Claude Code 自主处理信息收集
            is_visualization: 是否为可视化任务
            task_id: 任务ID（用于工作区隔离）
        """
        logger.info(f"开始执行任务: {task_title}")

        # 判断任务类型
        if force_code is True:
            task_type = TaskType.CODE_REQUIRED
            logger.info("任务类型: CODE_REQUIRED (强制指定)")
        elif force_code is False:
            task_type = TaskType.TEXT_ONLY
            logger.info("任务类型: TEXT_ONLY (强制指定)")
        else:
            task_type = self._analyze_task_type(task_title, task_description)

        # 根据任务类型执行
        if task_type == TaskType.CODE_REQUIRED:
            # 使用 Claude Code 执行代码任务
            result = await self._execute_code_task(
                task_title,
                task_description,
                subtask_results=subtask_results,
                is_visualization=is_visualization,
                task_id=task_id,
            )
        else:
            # 文本任务仍使用 LLM 直接处理
            result = self._execute_text_task(
                task_title,
                task_description,
                subtask_results=subtask_results,
                gathered_info=""
            )

        logger.info(f"任务执行完成: success={result.success}")

        # 显式清理临时 staging 目录
        self.cleanup()

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
    return await executor.execute(
        task_title=task_title,
        task_description=task_description,
        subtask_results=subtask_results,
        skip_info_gathering=skip_info_gathering,
        is_visualization=is_visualization
    )


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
    return asyncio.run(execute_task(
        data_file_paths=data_file_paths,
        task_title=task_title,
        task_description=task_description,
        subtask_results=subtask_results,
        skip_info_gathering=skip_info_gathering,
        is_visualization=is_visualization,
        **kwargs
    ))
