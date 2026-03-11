"""Result interpreter routes for metadata, code generation, and execution."""

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
        - staging_dir_to_cleanup is None when no staging directory is created
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
    return staged_paths, staging_dir, staging_dir  # cleanup path equals staging directory


def _cleanup_staging_dir(staging_dir: Optional[str]) -> None:
    """Cleanup temporary staging directory."""
    if staging_dir and os.path.isdir(staging_dir):
        try:
            shutil.rmtree(staging_dir)
            logger.info("Removed staging directory: %s", staging_dir)
        except Exception as e:
            logger.warning("Failed to clean staging directory %s: %s", staging_dir, e)


# ============== Request/Response Models ==============

class MetadataRequest(BaseModel):
    """Request for metadata extraction from a single file."""
    file_path: str = Field(..., description="Input file path")


class MetadataResponse(BaseModel):
    """Response payload for metadata extraction."""
    success: bool
    metadata: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class CodeGenerateRequest(BaseModel):
    """Request for analysis code generation."""
    file_paths: List[str] = Field(..., description="Input file paths")
    task_title: str = Field(..., description="Task title")
    task_description: str = Field(..., description="Task description")


class CodeGenerateResponse(BaseModel):
    """Response payload for analysis code generation."""
    success: bool
    code: Optional[str] = None
    description: Optional[str] = None
    has_visualization: bool = False
    visualization_purpose: Optional[str] = None
    visualization_analysis: Optional[str] = None
    error: Optional[str] = None


class CodeExecuteRequest(BaseModel):
    """Request for Python code execution."""
    code: str = Field(..., description="Python code to execute")
    work_dir: Optional[str] = Field(None, description="Working directory for execution outputs")
    data_dir: Optional[str] = Field(None, description="Directory containing input data files")
    docker_image: Optional[str] = Field(None, description="Optional Docker image override")


class CodeExecuteResponse(BaseModel):
    """Response payload for code execution."""
    success: bool
    status: str  # 'success', 'failed', 'error'
    output: str = ""
    error: str = ""
    exit_code: int = -1


class AnalyzeRequest(BaseModel):
    """Request for end-to-end analysis (metadata + code + execution)."""
    file_paths: List[str] = Field(..., description="Input file path")
    task_title: str = Field(..., description="Task title")
    task_description: str = Field(..., description="Task description")
    work_dir: Optional[str] = Field(None, description="Working directory for analysis outputs")
    docker_image: Optional[str] = Field(None, description="Optional Docker image override")


class AnalyzeResponse(BaseModel):
    """Response payload for end-to-end analysis."""
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
    skill_trace: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class PlanAnalyzeRequest(BaseModel):
    """Request for plan-based analysis (decompose then execute)."""
    data_paths: List[str] = Field(..., description="Input file path")
    task_title: Optional[str] = Field(None, description="Optional task title")
    task_description: str = Field(..., description="Task description")
    output_dir: Optional[str] = Field(None, description="Output directory")
    max_depth: int = Field(5, ge=1, le=10, description="Maximum decomposition depth")
    node_budget: int = Field(50, ge=1, le=200, description="Node budget limit")
    docker_image: Optional[str] = Field(None, description="Optional Docker image override")
    docker_timeout: Optional[int] = Field(None, description="Optional Docker timeout (seconds)")


class PlanAnalyzeResponse(BaseModel):
    """Response payload for plan-based analysis."""
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
    summary="Extract metadata",
)
def extract_metadata(request: MetadataRequest):
    """
    Extract file metadata. Supports CSV, TSV, MAT, and NPY formats.
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
    summary="Generate analysis code",
)
def generate_code(request: CodeGenerateRequest):
    """
    Generate Python analysis code from task instructions and file metadata.
    """
    try:
        metadata_list: List[DatasetMetadata] = []
        for file_path in request.file_paths:
            if not os.path.exists(file_path):
                raise HTTPException(
                    status_code=404,
                    detail=f"File not found: {file_path}"
                )
            metadata = DataProcessor.get_metadata(file_path)
            metadata_list.append(metadata)

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
    summary="Execute Python code",
)
async def execute_code(request: CodeExecuteRequest):
    """
    Execute Python code with Claude Code in a controlled working directory.
    """
    try:
        from pathlib import Path
        from tool_box.tools_impl.claude_code import claude_code_handler

        work_dir = request.work_dir
        if not work_dir:
            work_dir = tempfile.mkdtemp(prefix="interpreter_")

        os.makedirs(work_dir, exist_ok=True)

        task = f"""Execute the following Python code:

```python
{request.code}
```

Working directory: {work_dir}
"""
        if request.data_dir:
            task += f"\nData directory: {request.data_dir}"

        add_dirs = work_dir
        if request.data_dir:
            add_dirs = f"{work_dir},{request.data_dir}"

        result = await claude_code_handler(
            task=task,
            add_dirs=add_dirs,
            auth_mode="api_env",
            setting_sources="project",
            require_task_context=False,
        )

        task_dir = result.get("task_directory_full", "")
        if task_dir and work_dir:
            src_results = Path(task_dir) / "results"
            dst_results = Path(work_dir) / "results"
            if src_results.exists():
                dst_results.mkdir(parents=True, exist_ok=True)
                for f in src_results.iterdir():
                    if f.is_file():
                        shutil.copy2(f, dst_results / f.name)
                logger.info(f"Copied result files from {src_results} to {dst_results}")

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
    summary="Run end-to-end analysis",
)
async def run_analysis(request: AnalyzeRequest):
    """
    Run end-to-end analysis with Claude Code:
    1. Collect metadata for input files
    2. Run Claude Code to complete the analysis task
    """
    staging_dir_to_cleanup: Optional[str] = None

    try:
        from app.services.interpreter import TaskExecutor

        for file_path in request.file_paths:
            if not os.path.exists(file_path):
                raise HTTPException(
                    status_code=404,
                    detail=f"File not found: {file_path}"
                )

        effective_paths, data_dir, staging_dir_to_cleanup = _prepare_data_files(request.file_paths)
        metadata_list: List[DatasetMetadata] = [
            DataProcessor.get_metadata(path) for path in effective_paths
        ]

        work_dir = request.work_dir
        if not work_dir:
            work_dir = tempfile.mkdtemp(prefix="interpreter_")
        os.makedirs(work_dir, exist_ok=True)

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
            skill_trace=result.skill_trace,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Analysis failed")
        return AnalyzeResponse(success=False, error=str(e))
    finally:
        _cleanup_staging_dir(staging_dir_to_cleanup)


@interpreter_router.post(
    "/plan_analyze",
    response_model=PlanAnalyzeResponse,
    summary="Run plan-based analysis",
)
def run_plan_analysis_endpoint(request: PlanAnalyzeRequest):
    """
    Run plan-based analysis with Claude Code:
    1. Create a plan
    2. Decompose tasks
    3. Execute the plan
    """
    try:
        result = run_plan_analysis(
            description=request.task_description,
            data_paths=request.data_paths,
            title=request.task_title,
            output_dir=request.output_dir or "./results",
            max_depth=request.max_depth,
            node_budget=request.node_budget,
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
    description="Analysis and execution APIs",
)
