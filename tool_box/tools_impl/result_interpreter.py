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

# OpenAI tool schemas only require `operation`; models often omit title/description for
# analyze/generate. Defaults keep the pipeline usable without failing the whole turn.
_DEFAULT_TASK_TITLE = "Data analysis"
_DEFAULT_TASK_DESCRIPTION = (
    "Analyze the provided dataset(s) according to the user's request in the conversation."
)
_PROFILE_SUPPORTED_EXTENSIONS = {".csv", ".tsv", ".mat", ".npy", ".h5ad"}
_PROFILE_LOOKUP_EXTENSIONS = {".txt"}
_PROFILE_ID_COLUMN_HINTS = (
    "phage_id",
    "gene_id",
    "entrezid",
    "entrez_id",
    "symbol",
    "sample_id",
    "feature_id",
    "accession",
    "id",
)

_VISUALIZATION_REQUEST_PHRASES = (
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
    "绘图",
    "画图",
    "可视化",
    "图表",
    "热图",
    "散点",
)


def _coalesce_task_fields(
    task_title: Optional[str],
    task_description: Optional[str],
) -> tuple[str, str]:
    t = (task_title or "").strip() or _DEFAULT_TASK_TITLE
    d = (task_description or "").strip() or _DEFAULT_TASK_DESCRIPTION
    return t, d


def _task_requests_visualization(
    task_title: Optional[str],
    task_description: Optional[str],
) -> bool:
    text = f"{task_title or ''}\n{task_description or ''}".lower()
    return any(token in text for token in _VISUALIZATION_REQUEST_PHRASES)


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


def _profile_supports_path(file_path: str) -> bool:
    suffix = Path(file_path).suffix.lower()
    return suffix in _PROFILE_SUPPORTED_EXTENSIONS or suffix in _PROFILE_LOOKUP_EXTENSIONS


def _format_profile_sample_values(values: Any, *, limit: int = 3) -> List[str]:
    if not isinstance(values, list):
        return []
    formatted: List[str] = []
    for raw in values[:limit]:
        text = str(raw).strip()
        if not text:
            continue
        if len(text) > 60:
            text = text[:57] + "..."
        formatted.append(text)
    return formatted


def _summarize_profile_columns(columns: List[Dict[str, Any]], *, limit: int = 5) -> str:
    column_bits: List[str] = []
    for item in columns[:limit]:
        name = str(item.get("name") or "").strip()
        dtype = str(item.get("dtype") or "").strip()
        if not name:
            continue
        if dtype:
            column_bits.append(f"{name} ({dtype})")
        else:
            column_bits.append(name)
    return ", ".join(column_bits)


def _read_lookup_values(file_path: str) -> List[str]:
    values: List[str] = []
    with open(file_path, "r", encoding="utf-8", errors="ignore") as handle:
        for raw_line in handle:
            text = raw_line.strip()
            if not text:
                continue
            values.append(text)
    return values


def _choose_identifier_column(column_names: List[str]) -> Optional[str]:
    normalized = [(name, name.strip().lower()) for name in column_names if str(name).strip()]
    if not normalized:
        return None

    for hint in _PROFILE_ID_COLUMN_HINTS:
        for original, lowered in normalized:
            if lowered == hint:
                return original

    for hint in _PROFILE_ID_COLUMN_HINTS:
        for original, lowered in normalized:
            if lowered.endswith(f"_{hint}") or lowered.startswith(f"{hint}_"):
                return original

    for original, lowered in normalized:
        if lowered.endswith("id") or lowered.endswith("_id"):
            return original

    return normalized[0][0]


def _compute_identifier_match_summary(
    lookup_path: str,
    lookup_values: List[str],
    table_path: str,
    identifier_column: str,
) -> Optional[Dict[str, Any]]:
    import pandas as pd

    if not lookup_values:
        return None

    suffix = Path(table_path).suffix.lower()
    if suffix not in {".csv", ".tsv"}:
        return None

    separator = "\t" if suffix == ".tsv" else ","
    df = pd.read_csv(
        table_path,
        sep=separator,
        usecols=[identifier_column],
        dtype={identifier_column: "string"},
    )
    observed = {
        str(value).strip()
        for value in df[identifier_column].dropna().tolist()
        if str(value).strip()
    }
    lookup_set = {value.strip() for value in lookup_values if value.strip()}
    if not lookup_set:
        return None

    matched = sorted(lookup_set & observed)
    missing = sorted(lookup_set - observed)
    return {
        "lookup_file": os.path.basename(lookup_path),
        "dataset": os.path.basename(table_path),
        "identifier_column": identifier_column,
        "lookup_count": len(lookup_set),
        "matched_count": len(matched),
        "missing_count": len(missing),
        "matched_examples": matched[:5],
        "missing_examples": missing[:5],
    }


def _build_deterministic_profile(
    paths: List[str],
    *,
    DataProcessor: Any,
) -> Dict[str, Any]:
    structured_profiles: List[Dict[str, Any]] = []
    lookup_profiles: List[Dict[str, Any]] = []
    identifier_match_summaries: List[Dict[str, Any]] = []
    metadata_payloads: List[Dict[str, Any]] = []

    lookup_sources: List[tuple[str, List[str]]] = []
    structured_sources: List[tuple[str, Dict[str, Any]]] = []

    for file_path in paths:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")
        if not _profile_supports_path(file_path):
            raise ValueError(
                f"Unsupported file format for profile: {file_path}. "
                "Supported: .csv, .tsv, .mat, .npy, .h5ad, .txt"
            )

        suffix = Path(file_path).suffix.lower()
        basename = os.path.basename(file_path)
        if suffix in _PROFILE_LOOKUP_EXTENSIONS:
            lookup_values = _read_lookup_values(file_path)
            lookup_profile = {
                "filename": basename,
                "entry_count": len(lookup_values),
                "sample_values": lookup_values[:5],
            }
            lookup_profiles.append(lookup_profile)
            lookup_sources.append((file_path, lookup_values))
            continue

        metadata = DataProcessor.get_metadata(file_path)
        metadata_dict = metadata.model_dump()
        metadata_payloads.append(metadata_dict)

        columns = metadata_dict.get("columns") or []
        compact_columns: List[Dict[str, Any]] = []
        column_names: List[str] = []
        for column in columns[:8]:
            if not isinstance(column, dict):
                continue
            name = str(column.get("name") or "").strip()
            if not name:
                continue
            column_names.append(name)
            compact_columns.append(
                {
                    "name": name,
                    "dtype": str(column.get("dtype") or "").strip(),
                    "null_count": int(column.get("null_count") or 0),
                    "unique_count": int(column.get("unique_count") or 0),
                    "sample_values": _format_profile_sample_values(
                        column.get("sample_values")
                    ),
                }
            )

        structured_profile = {
            "filename": basename,
            "file_path": file_path,
            "file_format": metadata_dict.get("file_format"),
            "file_size_bytes": metadata_dict.get("file_size_bytes"),
            "total_rows": metadata_dict.get("total_rows"),
            "total_columns": metadata_dict.get("total_columns"),
            "column_names": column_names,
            "columns": compact_columns,
        }
        structured_profiles.append(structured_profile)
        structured_sources.append((file_path, structured_profile))

    for lookup_path, lookup_values in lookup_sources:
        for table_path, structured_profile in structured_sources:
            identifier_column = _choose_identifier_column(
                structured_profile.get("column_names") or []
            )
            if not identifier_column:
                continue
            try:
                match_summary = _compute_identifier_match_summary(
                    lookup_path,
                    lookup_values,
                    table_path,
                    identifier_column,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to compute identifier match summary for %s vs %s: %s",
                    lookup_path,
                    table_path,
                    exc,
                )
                continue
            if match_summary:
                identifier_match_summaries.append(match_summary)

    summary_lines = [
        "Deterministic dataset profile (code-derived, no model synthesis):"
    ]
    for profile in structured_profiles:
        line = (
            f"- {profile['filename']}: {profile['total_rows']} rows x "
            f"{profile['total_columns']} columns"
        )
        column_summary = _summarize_profile_columns(profile.get("columns") or [])
        if column_summary:
            line += f"; leading columns: {column_summary}"
        summary_lines.append(line)

    for lookup_profile in lookup_profiles:
        lookup_line = (
            f"- {lookup_profile['filename']}: {lookup_profile['entry_count']} lookup IDs"
        )
        samples = lookup_profile.get("sample_values") or []
        if samples:
            lookup_line += f"; examples: {', '.join(str(v) for v in samples[:3])}"
        summary_lines.append(lookup_line)

    for match in identifier_match_summaries:
        summary_lines.append(
            "- ID match "
            f"{match['lookup_file']} -> {match['dataset']} "
            f"(column {match['identifier_column']}): "
            f"{match['matched_count']}/{match['lookup_count']} matched, "
            f"{match['missing_count']} missing"
        )

    return {
        "metadata": metadata_payloads,
        "structured_datasets": structured_profiles,
        "lookup_files": lookup_profiles,
        "identifier_matches": identifier_match_summaries,
        "summary": "\n".join(summary_lines),
    }


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
    from app.services.interpreter import DataProcessor, CodeGenerator

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

        elif operation == "profile":
            paths = file_paths or ([file_path] if file_path else [])
            if not paths:
                return {"success": False, "error": "file_paths or file_path is required"}

            profile = _build_deterministic_profile(paths, DataProcessor=DataProcessor)
            return {
                "success": True,
                "operation": "profile",
                "task_type": "text_only",
                "profile_mode": "deterministic",
                "metadata": profile["metadata"],
                "profile": {
                    "structured_datasets": profile["structured_datasets"],
                    "lookup_files": profile["lookup_files"],
                    "identifier_matches": profile["identifier_matches"],
                    "summary": profile["summary"],
                },
                "code_description": "Deterministic dataset profile",
                "execution_status": "success",
                "execution_output": profile["summary"],
                "has_visualization": False,
            }

        elif operation == "generate":
            # Generate code.
            paths = file_paths or ([file_path] if file_path else [])
            if not paths:
                return {"success": False, "error": "file_paths or file_path is required"}

            task_title, task_description = _coalesce_task_fields(task_title, task_description)

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
            # Execute code through the scoped code executor.
            if not code:
                return {"success": False, "error": "code is required for execute operation"}

            from tool_box.tools_impl.code_executor import code_executor_handler

            exec_work_dir = work_dir or tempfile.mkdtemp(prefix="interpreter_")
            os.makedirs(exec_work_dir, exist_ok=True)
            os.makedirs(os.path.join(exec_work_dir, "results"), exist_ok=True)

            task = f"""Execute the following Python code:

```python
{code}
```

Working directory: {exec_work_dir}
"""
            if data_dir:
                task += f"\nData directory: {data_dir}"

            add_dirs = exec_work_dir
            if data_dir:
                add_dirs = f"{exec_work_dir},{data_dir}"

            result = await code_executor_handler(
                task=task,
                add_dirs=add_dirs,
                auth_mode="api_env",
                setting_sources="project",
                require_task_context=False,
            )

            task_dir = result.get("task_directory_full", "")
            if task_dir and exec_work_dir:
                src_results = Path(task_dir) / "results"
                dst_results = Path(exec_work_dir) / "results"
                if src_results.exists():
                    try:
                        same_results_dir = src_results.resolve() == dst_results.resolve()
                    except Exception:
                        same_results_dir = False
                    if same_results_dir:
                        logger.info(
                            "Skipping result copy because source and destination are identical: %s",
                            src_results,
                        )
                    else:
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

            task_title, task_description = _coalesce_task_fields(task_title, task_description)

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
                    is_visualization=_task_requests_visualization(
                        task_title,
                        task_description,
                    ),
                )
                execution_output = result.code_output or result.text_response or ""
                code_description = result.code_description
                if not code_description and result.text_response:
                    code_description = "Direct metadata overview generated without code execution"

                return {
                    "success": result.success,
                    "operation": "analyze",
                    "task_type": result.task_type.value,
                    "metadata": [m.model_dump() for m in metadata_list],
                    "generated_code": result.final_code,
                    "code_description": code_description,
                    "execution_status": "success" if result.success else "failed",
                    "execution_output": execution_output,
                    "execution_error": result.code_error or result.error_message or "",
                    "has_visualization": result.has_visualization,
                    "visualization_purpose": result.visualization_purpose,
                    "visualization_analysis": result.visualization_analysis,
                    "retries_used": max(0, result.total_attempts - 1),
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

            task_title, task_description = _coalesce_task_fields(task_title, task_description)

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
                "error": (
                    f"Unknown operation: {operation}. Valid: metadata, profile, "
                    "generate, execute, analyze, plan_analyze"
                ),
            }

    except Exception as e:
        logger.exception(f"Result interpreter error: {e}")
        return {"success": False, "error": str(e)}


# Tool definition
result_interpreter_tool = {
    "name": "result_interpreter",
    "description": """Data analysis and result interpretation tool.
Analyzes CSV, TSV, MAT, NPY, H5AD, and TXT helper files.

Operations:
- metadata: Extract dataset metadata (columns, types, samples)
- profile: Deterministic profile for dataset overview, schema, counts, and simple ID matching
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
                "enum": ["metadata", "profile", "generate", "execute", "analyze", "plan_analyze"],
                "description": "Operation type",
            },
            "file_path": {
                "type": "string",
                "description": "Single data file path (for metadata/profile)",
            },
            "file_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Data file paths list (for profile/generate/analyze)",
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
            "operation": "profile",
            "file_paths": [
                "/path/to/gvd_phage_meta_data.tsv",
                "/path/to/batch_test_phageids.txt",
            ],
            "task_title": "Dataset profile",
            "task_description": "Report rows, columns, sample values, and ID overlap only.",
        },
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
