"""
Result Interpreter Tool

Data analysis and result interpretation tool for CSV/TSV/MAT/NPY files.

Refactor notes:
- `execute` and `analyze` now run through Claude Code.
- `docker_image`/`docker_timeout` are deprecated (kept for backward compatibility).
"""

import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _prepare_data_files(file_paths: List[str]) -> tuple[List[str], str, Optional[str]]:
    """Ensure data files live under a single directory for Docker mounting.

    Returns:
        tuple: (staged_paths, data_dir, staging_dir_to_cleanup)
        - `staging_dir_to_cleanup` is None when no temp directory was created.
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
    return staged_paths, staging_dir, staging_dir  # Return staging_dir for cleanup.


def _cleanup_staging_dir(staging_dir: Optional[str]) -> None:
    """Clean up temporary staging directory."""
    if staging_dir and os.path.isdir(staging_dir):
        try:
            shutil.rmtree(staging_dir)
            logger.info("Cleaned temporary staging directory: %s", staging_dir)
        except Exception as e:
            logger.warning("Failed to clean staging directory %s: %s", staging_dir, e)


async def result_interpreter_handler(
    operation: str,
    file_path: Optional[str] = None,
    file_paths: Optional[List[str]] = None,
    data_paths: Optional[List[str]] = None,
    task_title: Optional[str] = None,
    task_description: Optional[str] = None,
    code: Optional[str] = None,
    work_dir: Optional[str] = None,
    data_dir: Optional[str] = None,
    output_dir: Optional[str] = None,
    max_depth: int = 5,
    node_budget: int = 50,
    # Deprecated parameters kept for backward compatibility.
    docker_image: str = "agent-plotter",
    docker_timeout: Optional[int] = None,
    timeout: Optional[int] = None,
    max_retries: Optional[int] = None,
    **kwargs,
) -> Dict[str, Any]:
    """
    Data analysis and result interpretation tool handler.

    Args:
        operation: Operation type (metadata, generate, execute, analyze)
        file_path: Single data file path (for metadata)
        file_paths: Data file path list (for generate/analyze)
        task_title: Task title
        task_description: Task description
        code: Python code (for execute)
        work_dir: Working directory
        data_dir: Data directory
        docker_image: [Deprecated] Not used
        docker_timeout: [Deprecated] Not used
        timeout: [Deprecated] Not used
        max_retries: [Deprecated] Not used

    Returns:
        Execution result dictionary.
    """
    # Lazy import to avoid circular dependency.
    from app.services.interpreter import (
        DataProcessor,
        CodeGenerator,
        LocalCodeInterpreter,
    )

    try:
        if operation == "metadata":
            # Extract metadata.
            if not file_path:
                return {"success": False, "error": "file_path is required for metadata operation"}

            if not os.path.exists(file_path):
                return {"success": False, "error": f"File not found: {file_path}"}

            metadata = DataProcessor.get_metadata(file_path)
            return {
                "success": True,
                "operation": "metadata",
                "metadata": metadata.model_dump(),
            }

        elif operation == "generate":
            # Generate code.
            paths = file_paths or ([file_path] if file_path else [])
            if not paths:
                return {"success": False, "error": "file_paths or file_path is required"}

            if not task_title or not task_description:
                return {"success": False, "error": "task_title and task_description are required"}

            # Extract metadata.
            metadata_list = []
            for fp in paths:
                if not os.path.exists(fp):
                    return {"success": False, "error": f"File not found: {fp}"}
                metadata_list.append(DataProcessor.get_metadata(fp))

            # Generate code.
            generator = CodeGenerator()
            response = generator.generate(
                metadata_list=metadata_list,
                task_title=task_title,
                task_description=task_description,
            )

            return {
                "success": True,
                "operation": "generate",
                "code": response.code,
                "description": response.description,
                "has_visualization": response.has_visualization,
                "visualization_purpose": response.visualization_purpose,
                "visualization_analysis": response.visualization_analysis,
            }

        elif operation == "execute":
            # Execute code through Claude Code.
            if not code:
                return {"success": False, "error": "code is required for execute operation"}

            from pathlib import Path
            from tool_box.tools_impl.claude_code import claude_code_handler

            exec_work_dir = work_dir or tempfile.mkdtemp(prefix="interpreter_")
            os.makedirs(exec_work_dir, exist_ok=True)

            # Build task description.
            task = f"""Execute the following Python code:

```python
{code}
```

Working directory: {exec_work_dir}
"""
            if data_dir:
                task += f"\nData directory: {data_dir}"

            # Run via Claude Code.
            add_dirs = exec_work_dir
            if data_dir:
                add_dirs = f"{exec_work_dir},{data_dir}"

            result = await claude_code_handler(
                task=task,
                add_dirs=add_dirs,
                auth_mode="api_env",
                setting_sources="project",
                require_task_context=False,
            )

            # Copy Claude Code outputs to work_dir.
            task_dir = result.get("task_directory_full", "")
            if task_dir and exec_work_dir:
                src_results = Path(task_dir) / "results"
                dst_results = Path(exec_work_dir) / "results"
                if src_results.exists():
                    dst_results.mkdir(parents=True, exist_ok=True)
                    for f in src_results.iterdir():
                        if f.is_file():
                            shutil.copy2(f, dst_results / f.name)
                    logger.info(f"Copied output files from {src_results} to {dst_results}")

            return {
                "success": result.get("success", False),
                "operation": "execute",
                "status": "success" if result.get("success") else "failed",
                "output": result.get("stdout", ""),
                "error": result.get("stderr", ""),
                "exit_code": result.get("exit_code", -1),
                "work_dir": exec_work_dir,
            }

        elif operation == "analyze":
            # Full analysis workflow using Claude Code.
            paths = file_paths or ([file_path] if file_path else [])
            if not paths:
                return {"success": False, "error": "file_paths or file_path is required"}

            if not task_title or not task_description:
                return {"success": False, "error": "task_title and task_description are required"}

            # Temporary directory reference for cleanup.
            staging_dir_to_cleanup: Optional[str] = None

            try:
                from app.services.interpreter import DataProcessor, TaskExecutor

                # Step 1: Validate file existence and extract metadata.
                for fp in paths:
                    if not os.path.exists(fp):
                        return {"success": False, "error": f"File not found: {fp}"}

                if data_dir:
                    effective_paths = paths
                else:
                    effective_paths, _, staging_dir_to_cleanup = _prepare_data_files(paths)

                metadata_list = [DataProcessor.get_metadata(fp) for fp in effective_paths]

                # Step 2: Prepare working directory.
                exec_work_dir = work_dir or tempfile.mkdtemp(prefix="interpreter_")
                os.makedirs(exec_work_dir, exist_ok=True)

                # Step 3: Run task using TaskExecutor (Claude Code).
                executor = TaskExecutor(
                    data_file_paths=effective_paths,
                    output_dir=exec_work_dir,
                )

                result = await executor.execute(
                    task_title=task_title,
                    task_description=task_description,
                    is_visualization=True,
                )

                return {
                    "success": result.success,
                    "operation": "analyze",
                    "metadata": [m.model_dump() for m in metadata_list],
                    "generated_code": result.final_code,
                    "code_description": result.code_description,
                    "execution_status": "success" if result.success else "failed",
                    "execution_output": result.code_output or "",
                    "execution_error": result.code_error or result.error_message or "",
                    "has_visualization": result.has_visualization,
                    "visualization_purpose": result.visualization_purpose,
                    "visualization_analysis": result.visualization_analysis,
                    "retries_used": result.total_attempts - 1 if result.total_attempts > 0 else 0,
                    "work_dir": exec_work_dir,
                }
            finally:
                # Clean up temporary staging directory.
                _cleanup_staging_dir(staging_dir_to_cleanup)

        elif operation == "plan_analyze":
            # Plan-based full analysis workflow (decompose -> execute).
            paths = data_paths or file_paths or ([file_path] if file_path else [])
            if not paths:
                return {"success": False, "error": "data_paths or file_paths is required"}

            if not task_description:
                return {"success": False, "error": "task_description is required"}

            from app.services.interpreter.interpreter import run_analysis_async

            # Use async entrypoint to avoid asyncio.run() in active event loops.
            plan_result = await run_analysis_async(
                description=task_description,
                data_paths=paths,
                title=task_title,
                output_dir=output_dir or work_dir or "./results",
                max_depth=max_depth,
                node_budget=node_budget,
            )

            return {
                "success": plan_result.success,
                "operation": "plan_analyze",
                "plan_id": plan_result.plan_id,
                "total_tasks": plan_result.total_tasks,
                "completed_tasks": plan_result.completed_tasks,
                "failed_tasks": plan_result.failed_tasks,
                "generated_files": plan_result.generated_files,
                "report_path": plan_result.report_path,
                "error": plan_result.error,
            }

        else:
            return {
                "success": False,
                "error": f"Unknown operation: {operation}. Valid: metadata, generate, execute, analyze, plan_analyze",
            }

    except Exception as e:
        logger.exception(f"Result interpreter error: {e}")
        return {"success": False, "error": str(e)}


# Tool definition
result_interpreter_tool = {
    "name": "result_interpreter",
    "description": """Data analysis and result interpretation tool.
Analyzes CSV, TSV, MAT, NPY data files by generating and executing Python code using Claude Code.

Operations:
- metadata: Extract dataset metadata (columns, types, samples)
- generate: Generate Python analysis code based on task description
- execute: Execute Python code using Claude Code
- analyze: Full pipeline (metadata → generate → execute with auto-fix)
- plan_analyze: Plan-based workflow (decompose → execute)""",
    "category": "analysis",
    "parameters_schema": {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["metadata", "generate", "execute", "analyze", "plan_analyze"],
                "description": "Operation type",
            },
            "file_path": {
                "type": "string",
                "description": "Single data file path (for metadata)",
            },
            "file_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Data file paths list (for generate/analyze)",
            },
            "data_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Data file paths list (for plan_analyze)",
            },
            "task_title": {
                "type": "string",
                "description": "Analysis task title",
            },
            "task_description": {
                "type": "string",
                "description": "Detailed task description",
            },
            "code": {
                "type": "string",
                "description": "Python code to execute (for execute operation)",
            },
            "work_dir": {
                "type": "string",
                "description": "Working directory for output files",
            },
            "data_dir": {
                "type": "string",
                "description": "Data directory for file access",
            },
            "output_dir": {
                "type": "string",
                "description": "Output directory for plan-based analysis",
            },
            "max_depth": {
                "type": "integer",
                "default": 5,
                "description": "Max decomposition depth (plan_analyze)",
            },
            "node_budget": {
                "type": "integer",
                "default": 50,
                "description": "Max tasks to create (plan_analyze)",
            },
        },
        "required": ["operation"],
    },
    "handler": result_interpreter_handler,
    "tags": ["analysis", "data", "python", "claude-code"],
    "examples": [
        {
            "operation": "analyze",
            "file_paths": ["/path/to/data.csv"],
            "task_title": "Data Summary",
            "task_description": "Calculate basic statistics and identify trends",
        },
        {
            "operation": "metadata",
            "file_path": "/path/to/data.csv",
        },
    ],
}
