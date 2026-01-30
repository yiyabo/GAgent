"""
Result Interpreter API Routes

提供数据分析和代码执行的 REST API 接口。

重构说明：
- /analyze 和 /execute 端点现在使用 Claude Code 执行
- docker_image/docker_timeout 参数已废弃（保留向后兼容）
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel, Field

from app.services.interpreter import (
    DataProcessor,
    DatasetMetadata,
    CodeGenerator,
    CodeTaskResponse,
)
from app.services.interpreter.interpreter import run_analysis as run_plan_analysis
from . import register_router

logger = logging.getLogger(__name__)

interpreter_router = APIRouter(prefix="/interpreter", tags=["interpreter"])


def _prepare_data_files(file_paths: List[str]) -> tuple[List[str], str, Optional[str]]:
    """Ensure data files live under a single directory for Docker mounting.

    Returns:
        tuple: (staged_paths, data_dir, staging_dir_to_cleanup)
        - staging_dir_to_cleanup 为 None 表示未创建临时目录，无需清理
    """
    data_dirs = {os.path.dirname(os.path.abspath(p)) for p in file_paths}
    if len(data_dirs) <= 1:
        return file_paths, next(iter(data_dirs)) if data_dirs else os.getcwd(), None

    staging_dir = tempfile.mkdtemp(prefix="interpreter_data_")
    used_names = set()
    staged_paths: List[str] = []

    for path in file_paths:
        base = os.path.basename(path)
        name = base
        if name in used_names:
            stem, ext = os.path.splitext(base)
            index = 2
            while f"{stem}_{index}{ext}" in used_names:
                index += 1
            name = f"{stem}_{index}{ext}"
        used_names.add(name)

        dest = os.path.join(staging_dir, name)
        shutil.copy2(path, dest)
        staged_paths.append(dest)

    logger.info("Multiple data directories detected; staged files at %s", staging_dir)
    return staged_paths, staging_dir, staging_dir  # 返回 staging_dir 以便清理


def _cleanup_staging_dir(staging_dir: Optional[str]) -> None:
    """清理临时 staging 目录"""
    if staging_dir and os.path.isdir(staging_dir):
        try:
            shutil.rmtree(staging_dir)
            logger.info("已清理临时目录: %s", staging_dir)
        except Exception as e:
            logger.warning("清理临时目录失败: %s, 错误: %s", staging_dir, e)


# ============== Request/Response Models ==============

class MetadataRequest(BaseModel):
    """元数据提取请求"""
    file_path: str = Field(..., description="数据文件的绝对路径")


class MetadataResponse(BaseModel):
    """元数据提取响应"""
    success: bool
    metadata: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class CodeGenerateRequest(BaseModel):
    """代码生成请求"""
    file_paths: List[str] = Field(..., description="数据文件路径列表")
    task_title: str = Field(..., description="任务标题")
    task_description: str = Field(..., description="任务详细描述")


class CodeGenerateResponse(BaseModel):
    """代码生成响应"""
    success: bool
    code: Optional[str] = None
    description: Optional[str] = None
    has_visualization: bool = False
    visualization_purpose: Optional[str] = None
    visualization_analysis: Optional[str] = None
    error: Optional[str] = None


class CodeExecuteRequest(BaseModel):
    """代码执行请求"""
    code: str = Field(..., description="要执行的 Python 代码")
    work_dir: Optional[str] = Field(None, description="工作目录（用于输出文件）")
    data_dir: Optional[str] = Field(None, description="数据目录")
    # 已废弃参数，保留向后兼容
    docker_image: Optional[str] = Field(None, description="[已废弃] 不再使用")


class CodeExecuteResponse(BaseModel):
    """代码执行响应"""
    success: bool
    status: str  # 'success', 'failed', 'error'
    output: str = ""
    error: str = ""
    exit_code: int = -1


class AnalyzeRequest(BaseModel):
    """完整分析请求（元数据提取 + 代码生成 + 执行）"""
    file_paths: List[str] = Field(..., description="数据文件路径列表")
    task_title: str = Field(..., description="任务标题")
    task_description: str = Field(..., description="任务详细描述")
    work_dir: Optional[str] = Field(None, description="工作目录")
    # 已废弃参数，保留向后兼容
    docker_image: Optional[str] = Field(None, description="[已废弃] 不再使用")


class AnalyzeResponse(BaseModel):
    """完整分析响应"""
    success: bool
    metadata: Optional[List[Dict[str, Any]]] = None
    generated_code: Optional[str] = None
    code_description: Optional[str] = None
    execution_status: Optional[str] = None
    execution_output: Optional[str] = None
    execution_error: Optional[str] = None
    has_visualization: bool = False
    visualization_purpose: Optional[str] = None
    visualization_analysis: Optional[str] = None
    retries_used: int = 0
    error: Optional[str] = None


class PlanAnalyzeRequest(BaseModel):
    """计划式完整分析请求（分解 -> 执行）"""
    data_paths: List[str] = Field(..., description="数据文件路径列表")
    task_title: Optional[str] = Field(None, description="任务标题（可选）")
    task_description: str = Field(..., description="任务详细描述")
    output_dir: Optional[str] = Field(None, description="输出目录")
    max_depth: int = Field(5, ge=1, le=10, description="分解最大深度")
    node_budget: int = Field(50, ge=1, le=200, description="最大任务节点数")
    # 已废弃参数，保留向后兼容
    docker_image: Optional[str] = Field(None, description="[已废弃] 不再使用")
    docker_timeout: Optional[int] = Field(None, description="[已废弃] 不再使用")


class PlanAnalyzeResponse(BaseModel):
    """计划式完整分析响应"""
    success: bool
    plan_id: int
    total_tasks: int
    completed_tasks: int
    failed_tasks: int
    generated_files: List[str] = Field(default_factory=list)
    report_path: Optional[str] = None
    error: Optional[str] = None


# ============== API Endpoints ==============

@interpreter_router.post(
    "/metadata",
    response_model=MetadataResponse,
    summary="提取数据集元数据",
)
def extract_metadata(request: MetadataRequest):
    """
    从数据文件提取元数据，支持 CSV, TSV, MAT, NPY 格式。
    """
    try:
        metadata = DataProcessor.get_metadata(request.file_path)
        return MetadataResponse(
            success=True,
            metadata=metadata.model_dump(),
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Metadata extraction failed")
        return MetadataResponse(success=False, error=str(e))


@interpreter_router.post(
    "/generate",
    response_model=CodeGenerateResponse,
    summary="生成分析代码",
)
def generate_code(request: CodeGenerateRequest):
    """
    根据数据集元数据和任务描述生成 Python 分析代码。
    """
    try:
        # 提取所有文件的元数据
        metadata_list: List[DatasetMetadata] = []
        for file_path in request.file_paths:
            if not os.path.exists(file_path):
                raise HTTPException(
                    status_code=404,
                    detail=f"File not found: {file_path}"
                )
            metadata = DataProcessor.get_metadata(file_path)
            metadata_list.append(metadata)

        # 生成代码
        generator = CodeGenerator()
        response: CodeTaskResponse = generator.generate(
            metadata_list=metadata_list,
            task_title=request.task_title,
            task_description=request.task_description,
        )

        return CodeGenerateResponse(
            success=True,
            code=response.code,
            description=response.description,
            has_visualization=response.has_visualization,
            visualization_purpose=response.visualization_purpose,
            visualization_analysis=response.visualization_analysis,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Code generation failed")
        return CodeGenerateResponse(success=False, error=str(e))


@interpreter_router.post(
    "/execute",
    response_model=CodeExecuteResponse,
    summary="执行 Python 代码",
)
async def execute_code(request: CodeExecuteRequest):
    """
    使用 Claude Code 执行 Python 代码。
    """
    try:
        from pathlib import Path
        from tool_box.tools_impl.claude_code import claude_code_handler

        # 确定工作目录
        work_dir = request.work_dir
        if not work_dir:
            work_dir = tempfile.mkdtemp(prefix="interpreter_")

        os.makedirs(work_dir, exist_ok=True)

        # 构建任务描述
        task = f"""执行以下 Python 代码：

```python
{request.code}
```

工作目录: {work_dir}
"""
        if request.data_dir:
            task += f"\n数据目录: {request.data_dir}"

        # 使用 Claude Code 执行
        add_dirs = work_dir
        if request.data_dir:
            add_dirs = f"{work_dir},{request.data_dir}"

        result = await claude_code_handler(
            task=task,
            add_dirs=add_dirs,
        )

        # 复制 Claude Code 产出到 work_dir
        task_dir = result.get("task_directory_full", "")
        if task_dir and work_dir:
            src_results = Path(task_dir) / "results"
            dst_results = Path(work_dir) / "results"
            if src_results.exists():
                dst_results.mkdir(parents=True, exist_ok=True)
                for f in src_results.iterdir():
                    if f.is_file():
                        shutil.copy2(f, dst_results / f.name)
                logger.info(f"已复制产出文件从 {src_results} 到 {dst_results}")

        return CodeExecuteResponse(
            success=result.get("success", False),
            status="success" if result.get("success") else "failed",
            output=result.get("stdout", ""),
            error=result.get("stderr", ""),
            exit_code=result.get("exit_code", -1),
        )

    except Exception as e:
        logger.exception("Code execution failed")
        return CodeExecuteResponse(
            success=False,
            status="error",
            error=str(e),
            exit_code=-1,
        )


@interpreter_router.post(
    "/analyze",
    response_model=AnalyzeResponse,
    summary="完整数据分析流程",
)
async def run_analysis(request: AnalyzeRequest):
    """
    执行完整的数据分析流程（使用 Claude Code）：
    1. 提取数据集元数据
    2. 使用 Claude Code 自主完成分析任务
    """
    # 用于清理的临时目录引用
    staging_dir_to_cleanup: Optional[str] = None

    try:
        from app.services.interpreter import TaskExecutor

        # Step 1: 验证文件存在
        for file_path in request.file_paths:
            if not os.path.exists(file_path):
                raise HTTPException(
                    status_code=404,
                    detail=f"File not found: {file_path}"
                )

        # Step 2: 准备数据文件
        effective_paths, data_dir, staging_dir_to_cleanup = _prepare_data_files(request.file_paths)
        metadata_list: List[DatasetMetadata] = [
            DataProcessor.get_metadata(path) for path in effective_paths
        ]

        # Step 3: 准备工作目录
        work_dir = request.work_dir
        if not work_dir:
            work_dir = tempfile.mkdtemp(prefix="interpreter_")
        os.makedirs(work_dir, exist_ok=True)

        # Step 4: 使用 TaskExecutor (Claude Code) 执行任务
        executor = TaskExecutor(
            data_file_paths=effective_paths,
            output_dir=work_dir,
        )

        result = await executor.execute(
            task_title=request.task_title,
            task_description=request.task_description,
            is_visualization=True,
        )

        return AnalyzeResponse(
            success=result.success,
            metadata=[m.model_dump() for m in metadata_list],
            generated_code=result.final_code,
            code_description=result.code_description,
            execution_status="success" if result.success else "failed",
            execution_output=result.code_output or "",
            execution_error=result.code_error or result.error_message,
            has_visualization=result.has_visualization,
            visualization_purpose=result.visualization_purpose,
            visualization_analysis=result.visualization_analysis,
            retries_used=result.total_attempts - 1 if result.total_attempts > 0 else 0,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Analysis failed")
        return AnalyzeResponse(success=False, error=str(e))
    finally:
        # 清理临时 staging 目录
        _cleanup_staging_dir(staging_dir_to_cleanup)


@interpreter_router.post(
    "/plan_analyze",
    response_model=PlanAnalyzeResponse,
    summary="计划式完整数据分析流程",
)
def run_plan_analysis_endpoint(request: PlanAnalyzeRequest):
    """
    执行计划式的数据分析流程（使用 Claude Code）：
    1. 创建计划
    2. 分解任务
    3. 执行计划
    """
    try:
        result = run_plan_analysis(
            description=request.task_description,
            data_paths=request.data_paths,
            title=request.task_title,
            output_dir=request.output_dir or "./results",
            max_depth=request.max_depth,
            node_budget=request.node_budget,
            # 已废弃参数不再传递
        )

        return PlanAnalyzeResponse(
            success=result.success,
            plan_id=result.plan_id,
            total_tasks=result.total_tasks,
            completed_tasks=result.completed_tasks,
            failed_tasks=result.failed_tasks,
            generated_files=result.generated_files,
            report_path=result.report_path,
            error=result.error,
        )
    except Exception as e:
        logger.exception("Plan analysis failed")
        return PlanAnalyzeResponse(
            success=False,
            plan_id=-1,
            total_tasks=0,
            completed_tasks=0,
            failed_tasks=0,
            generated_files=[],
            error=str(e),
        )


# ============== Router Registration ==============

register_router(
    namespace="interpreter",
    version="v1",
    path="/interpreter",
    router=interpreter_router,
    tags=["interpreter"],
    description="数据分析与结果解释 API",
)
