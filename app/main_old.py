"""FastAPI main application module for AI-Driven Task Orchestration System.

This module provides the main FastAPI application with comprehensive API endpoints
for task management, plan orchestration, evaluation, and tool-enhanced execution.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from tool_box import get_cache_stats, initialize_toolbox
from tool_box import list_available_tools as _list_available_tools
from tool_box import route_user_request

# Memory system integration
from .api.memory_api import memory_router
from .database import init_db
from .database_pool import get_db
from .errors import (
    BaseError,
    BusinessError,
    ErrorCode,
    ValidationError,
    handle_api_error,
)
from .errors.exceptions import ErrorCategory
from .errors.exceptions import SystemError as CustomSystemError
from .execution.executors import execute_task
from .execution.executors.enhanced import (
    execute_task_with_adversarial_evaluation,
    execute_task_with_evaluation,
    execute_task_with_multi_expert_evaluation,
)
from .execution.executors.tool_enhanced import (
    execute_task_enhanced,
    execute_task_with_tools,
    execute_task_with_tools_and_evaluation,
)
from .llm import get_default_client
from .models import (
    ContextPreviewRequest,
    ExecuteWithEvaluationRequest,
    MoveTaskRequest,
    RerunSelectedTasksRequest,
    RerunTaskSubtreeRequest,
    RunRequest,
    TaskCreate,
)
from .repository.tasks import default_repo
from .scheduler import bfs_schedule, postorder_schedule, requires_dag_order
from .services.evaluation.benchmark import run_benchmark
from .services.context.context import gather_context
from .services.context.context_budget import apply_budget
from .services.planning.decomposition_with_evaluation import (
    decompose_task_with_evaluation,
    should_decompose_with_quality_check,
)
from .services.foundation.logging_config import setup_logging
from .services.planning.planning import approve_plan_service, propose_plan_service

# Recursive decomposition feature
from .services.planning.recursive_decomposition import (
    MAX_DECOMPOSITION_DEPTH,
    decompose_task,
    determine_task_type,
    evaluate_task_complexity,
    recursive_decompose_plan,
    should_decompose_task,
)
from .services.foundation.settings import get_settings
from .services.planning.tool_aware_decomposition import (
    analyze_task_tool_requirements,
    decompose_task_with_tool_awareness,
)
from .utils import plan_prefix, run_async, split_prefix


def _parse_bool(val, default: bool = False) -> bool:
    if isinstance(val, bool):
        return val
    if val is None:
        return default
    if isinstance(val, (int, float)):
        return bool(val)
    if isinstance(val, str):
        v = val.strip().lower()
        if v in {"true", "1", "yes", "on"}:
            return True
        if v in {"false", "0", "no", "off"}:
            return False
    return default


def _parse_int(val, default: int, min_value: int, max_value: int) -> int:
    try:
        i = int(val)
    except (ValueError, TypeError, OverflowError):
        return default
    try:
        i = max(min_value, min(int(i), max_value))
    except (ValueError, TypeError):
        return default
    return i


def _parse_opt_float(val, min_value: float, max_value: float):
    if val is None:
        return None
    try:
        f = float(val)
    except (ValueError, TypeError, OverflowError):
        return None
    try:
        f = max(min_value, min(float(f), max_value))
    except (ValueError, TypeError):
        return None
    return f


def _parse_opt_int(val, min_value: int, max_value: int):
    if val is None:
        return None
    try:
        i = int(val)
    except (ValueError, TypeError, OverflowError):
        return None
    try:
        i = max(min_value, min(int(i), max_value))
    except (ValueError, TypeError):
        return None
    return i


def _parse_strategy(val) -> str:
    if not isinstance(val, str):
        return "truncate"
    v = val.strip().lower()
    return v if v in {"truncate", "sentence"} else "truncate"


def _parse_schedule(val) -> str:
    """Parse scheduling strategy for /run: 'bfs' (default), 'dag', or 'postorder'."""
    if not isinstance(val, str):
        return "bfs"
    v = val.strip().lower()
    return v if v in {"bfs", "dag", "postorder"} else "bfs"


# GLM semantic retrieval is now the only method


def _sanitize_manual_list(vals) -> Optional[List[int]]:
    if not isinstance(vals, list):
        return None
    out: List[int] = []
    for x in vals:
        try:
            out.append(int(x))
        except (ValueError, TypeError, OverflowError):
            continue
    if not out:
        return None
    # dedup and cap size
    dedup = list(dict.fromkeys(out))
    return dedup[:50]


def _sanitize_context_options(co: Dict[str, Any]) -> Dict[str, Any]:
    co = co or {}
    return {
        "include_deps": _parse_bool(co.get("include_deps"), default=True),
        "include_plan": _parse_bool(co.get("include_plan"), default=True),
        "k": _parse_int(co.get("k", 5), default=5, min_value=0, max_value=50),
        "manual": _sanitize_manual_list(co.get("manual")),
        # GLM semantic retrieval options (now default enabled)
        "semantic_k": _parse_int(co.get("semantic_k", 5), default=5, min_value=0, max_value=50),
        "min_similarity": _parse_opt_float(co.get("min_similarity", 0.1), min_value=0.0, max_value=1.0) or 0.1,
        # hierarchy options (Phase 5)
        "include_ancestors": _parse_bool(co.get("include_ancestors"), default=False),
        "include_siblings": _parse_bool(co.get("include_siblings"), default=False),
        "hierarchy_k": _parse_int(co.get("hierarchy_k", 3), default=3, min_value=0, max_value=20),
        # budgeting options
        "max_chars": _parse_opt_int(co.get("max_chars"), min_value=0, max_value=100_000),
        # non-positive per_section_max considered invalid → None
        "per_section_max": (
            None
            if (co.get("per_section_max") is not None and _parse_opt_int(co.get("per_section_max"), 1, 50_000) is None)
            else _parse_opt_int(co.get("per_section_max"), min_value=1, max_value=50_000)
        ),
        "strategy": _parse_strategy(co.get("strategy")),
        # snapshot controls
        "save_snapshot": _parse_bool(co.get("save_snapshot"), default=False),
        "label": (str(co.get("label")).strip()[:64] if co.get("label") else None),
    }


@asynccontextmanager
async def lifespan(_fastapi_app: FastAPI):
    """Application lifespan context manager for FastAPI startup and shutdown.

    Handles initialization of core components including logging, database,
    database integrity checks, and tool box integration during startup.
    Provides cleanup during shutdown.

    Args:
        _fastapi_app: FastAPI application instance (unused parameter required by FastAPI)

    Yields:
        None
    """
    # Initialize Structured Logging with Global Configuration
    setup_logging()
    _ = get_settings()  # Trigger loading to make it easy to see in the logs if the configuration took effect or not
    init_db()
    # DB Lightweight integrity check (logging only, no service interruption)
    try:
        with get_db() as _conn:
            row = _conn.execute("PRAGMA integrity_check").fetchone()
            msg = None
            try:
                msg = row[0]
            except (ValueError, TypeError):
                msg = str(row)
            logging.getLogger("app.main").info("DB integrity_check: %s", msg)
    except (ValueError, TypeError) as _e:
        logging.getLogger("app.main").warning("DB integrity check skipped: %s", _e)

    # Initialize Tool Box for enhanced agent capabilities
    try:
        await initialize_toolbox()
        logging.getLogger("app.main").info("Tool Box integrated successfully - Enhanced AI capabilities enabled")
    except (ValueError, TypeError) as e:
        logging.getLogger("app.main").warning("Tool Box initialization failed: %s", e)

    yield


app = FastAPI(lifespan=lifespan)


# 注册统一异常处理器
@app.exception_handler(BaseError)
async def base_error_handler(_request: Request, exc: BaseError):
    """统一处理自定义业务异常."""
    error_response = handle_api_error(exc, include_debug=False)
    return JSONResponse(status_code=_map_error_to_http_status(exc), content=error_response)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_request: Request, exc: RequestValidationError):
    """处理FastAPI参数验证错误."""
    validation_error = ValidationError(
        message="Request parameter validation failed",
        error_code=ErrorCode.SCHEMA_VALIDATION_FAILED,
        context={"errors": exc.errors(), "body": str(exc.body) if exc.body else None},
        suggestions=["检查请求参数格式", "确保必填字段完整", "参考API文档修正参数"],
    )
    error_response = handle_api_error(validation_error, include_debug=False)
    return JSONResponse(status_code=422, content=error_response)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """处理HTTP异常."""
    if exc.status_code == 404:
        error = BusinessError(
            message="Requested resource not found",
            error_code=ErrorCode.TASK_NOT_FOUND,
            context={"path": str(request.url), "method": request.method},
        )
    elif exc.status_code == 405:
        error = ValidationError(
            message="HTTP method not allowed",
            error_code=ErrorCode.INVALID_FIELD_FORMAT,
            context={"method": request.method, "path": str(request.url)},
        )
    else:
        error = CustomSystemError(
            message=exc.detail if exc.detail else f"HTTP error {exc.status_code}",
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            context={"status_code": exc.status_code},
        )

    error_response = handle_api_error(error, include_debug=False)
    return JSONResponse(status_code=exc.status_code, content=error_response)


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """处理未捕获的通用异常."""
    system_error = CustomSystemError(
        message="Internal server error",
        error_code=ErrorCode.INTERNAL_SERVER_ERROR,
        cause=exc,
        context={"path": str(request.url), "method": request.method, "exception_type": type(exc).__name__},
        suggestions=["稍后重试", "如问题持续存在，请联系技术支持"],
    )
    # 根据环境变量控制是否返回调试信息（默认生产环境关闭）
    debug_env = os.environ.get("API_DEBUG") or os.environ.get("APP_DEBUG") or os.environ.get("DEBUG")
    include_debug = _parse_bool(debug_env, default=False)
    error_response = handle_api_error(system_error, include_debug=include_debug)
    return JSONResponse(status_code=500, content=error_response)


def _map_error_to_http_status(error: BaseError) -> int:
    """将自定义错误映射到HTTP状态码."""
    if error.category == ErrorCategory.VALIDATION:
        return 400
    elif error.category == ErrorCategory.AUTHENTICATION:
        return 401
    elif error.category == ErrorCategory.AUTHORIZATION:
        return 403
    elif error.category == ErrorCategory.BUSINESS and error.error_code == ErrorCode.TASK_NOT_FOUND:
        return 404
    elif error.category == ErrorCategory.NETWORK:
        return 502
    elif error.category == ErrorCategory.EXTERNAL_SERVICE:
        return 503
    elif error.category in [ErrorCategory.SYSTEM, ErrorCategory.DATABASE]:
        return 500
    else:
        return 400  # 默认客户端错误


# Include memory API router
app.include_router(memory_router)


# -------------------------------
# Generic plan helpers
# -------------------------------
# helpers centralized in app/utils.py: plan_prefix, split_prefix, parse_json_obj


@app.post("/tasks")
def create_task(task: TaskCreate):
    """Create a new task with the provided details.

    Args:
        task: TaskCreate object containing task name and type

    Returns:
        dict: Dictionary containing the created task ID
    """
    task_id = default_repo.create_task(task.name, status="pending", priority=None, task_type=task.task_type)
    return {"id": task_id}


@app.get("/tasks")
def list_tasks():
    """List all tasks in the system.

    Returns:
        list: List of all tasks
    """
    return default_repo.list_all_tasks()


# -------------------------------
# Generic planning endpoints
# -------------------------------
@app.post("/plans/propose")
def propose_plan(payload: Dict[str, Any]):
    """Propose a new plan based on the provided payload.

    Args:
        payload: Dictionary containing plan proposal parameters

    Returns:
        dict: Proposed plan details

    Raises:
        ValidationError: If plan proposal validation fails
    """
    try:
        return propose_plan_service(payload)
    except ValueError as e:
        raise ValidationError(
            message=f"Plan proposal validation failed: {str(e)}", error_code=ErrorCode.GOAL_VALIDATION_FAILED, cause=e
        ) from e


@app.post("/plans/approve")
def approve_plan(plan: Dict[str, Any]):
    """Approve a proposed plan.

    Args:
        plan: Dictionary containing plan details to approve

    Returns:
        dict: Approved plan details

    Raises:
        BusinessError: If plan approval fails
    """
    try:
        return approve_plan_service(plan)
    except ValueError as e:
        raise BusinessError(
            message=f"Plan approval failed: {str(e)}", error_code=ErrorCode.BUSINESS_RULE_VIOLATION, cause=e
        ) from e


@app.get("/plans")
def list_plans():
    """List all available plans.

    Returns:
        dict: Dictionary containing list of plan titles
    """
    return {"plans": default_repo.list_plan_titles()}


@app.get("/plans/{title}/tasks")
def get_plan_tasks(title: str):
    """Get all tasks for a specific plan.

    Args:
        title: Plan title/name to retrieve tasks for

    Returns:
        list: List of task dictionaries with id, name, short_name, status,
              priority, task_type, depth, and parent_id
    """
    rows = default_repo.list_plan_tasks(title)
    out: List[Dict[str, Any]] = []
    for r in rows:
        rid, nm, st, pr = r["id"], r["name"], r.get("status"), r.get("priority")
        _, short = split_prefix(nm)
        out.append(
            {
                "id": rid,
                "name": nm,
                "short_name": short,
                "status": st,
                "priority": pr,
                "task_type": r.get("task_type", "atomic"),
                "depth": r.get("depth", 0),
                "parent_id": r.get("parent_id"),
            }
        )
    return out


@app.get("/plans/{title}/assembled")
def get_plan_assembled(title: str):
    """Get assembled content for all tasks in a plan.

    Args:
        title: Plan title/name to retrieve assembled content for

    Returns:
        dict: Dictionary with title, sections, and combined content
    """
    items = default_repo.list_plan_outputs(title)
    sections = [{"name": it["short_name"], "content": it["content"]} for it in items]
    combined = "\n\n".join([f"{s['name']}\n\n{s['content']}" for s in sections])
    return {"title": title, "sections": sections, "combined": combined}


@app.get("/health/llm")
def llm_health(ping: bool = False):
    """Check LLM service health and configuration.

    Args:
        ping: Whether to perform an actual ping test

    Returns:
        dict: LLM client configuration and ping status
    """
    client = get_default_client()
    info: Dict[str, Any] = client.config()
    if ping:
        info["ping_ok"] = client.ping()
    else:
        info["ping_ok"] = None
    return info


# removed legacy endpoint: /reports/protein_binding_site


@app.get("/tasks/{task_id}/output")
def get_task_output(task_id: int):
    """Get the output content for a specific task.

    Args:
        task_id: The ID of the task to retrieve output for

    Returns:
        dict: Dictionary containing task ID and content

    Raises:
        HTTPException: If task output is not found (404)
    """
    content = default_repo.get_task_output_content(task_id)
    if content is None:
        raise HTTPException(status_code=404, detail="output not found")
    return {"id": task_id, "content": content}


# removed legacy endpoint: /reports/protein_binding_site/assembled


@app.post("/run")
def run_tasks(payload: Optional[Dict[str, Any]] = Body(None)):
    """
    Execute tasks. If body contains {"title": "..."}, only run tasks for that plan (by name prefix).
    Otherwise, run all pending tasks (original behavior).
    Supports evaluation mode with enable_evaluation parameter.
    """
    # Strongly-typed optional parsing (backward compatible)
    try:
        rr = RunRequest.model_validate(payload or {})
    except (ValidationError, ValueError):
        rr = RunRequest()
    title = (rr.title or "").strip() or None
    use_context = bool(rr.use_context)
    # Phase 3: scheduling strategy (bfs|dag)
    schedule = _parse_schedule(rr.schedule)
    # Phase 2: optional context options forwarded to executor (sanitized)
    context_options = None
    if rr.context_options is not None:
        try:
            context_options = _sanitize_context_options(rr.context_options.model_dump())
        except (ValueError, TypeError, AttributeError):
            context_options = None

    # New: Evaluation mode support
    enable_evaluation = bool(rr.enable_evaluation)
    evaluation_mode = (rr.evaluation_mode or "llm").strip().lower() if enable_evaluation else None
    # Validate evaluation mode
    if evaluation_mode not in {None, "llm", "multi_expert", "adversarial"}:
        logging.getLogger("app.main").warning("Unknown evaluation_mode '%s', fallback to 'llm'", evaluation_mode)
        evaluation_mode = "llm"
    evaluation_config = None
    if enable_evaluation:
        ev = rr.evaluation_options or ExecuteWithEvaluationRequest().model_dump()
        if not isinstance(ev, dict):
            ev = {}
        evaluation_config = {
            "max_iterations": _parse_int((ev or {}).get("max_iterations", 3), default=3, min_value=1, max_value=10),
            "quality_threshold": _parse_opt_float((ev or {}).get("quality_threshold"), 0.0, 1.0) or 0.8,
        }

    # New: Tool-enhanced flag
    use_tools = bool(getattr(rr, "use_tools", False))

    # New: Auto-decompose (plan-level) before execution if title provided
    auto_decompose = bool(getattr(rr, "auto_decompose", False))
    decompose_max_depth = None
    try:
        if getattr(rr, "decompose_max_depth", None) is not None:
            decompose_max_depth = _parse_int(rr.decompose_max_depth, default=3, min_value=1, max_value=5)
    except (ValueError, TypeError):
        decompose_max_depth = None

    if auto_decompose and not title:
        logging.getLogger("app.main").warning(
            "auto_decompose requested but no title provided; skipping auto decomposition"
        )
    if auto_decompose and title:
        try:
            # Prefer postorder for hierarchical execution when auto-decomposing
            if rr.schedule is None:
                schedule = "postorder"
            result = recursive_decompose_plan(title, repo=default_repo, max_depth=decompose_max_depth or 3)
            if not isinstance(result, dict) or (not result.get("success", False)):
                logging.getLogger("app.main").warning("Auto-decompose failed or no-op for plan '%s': %s", title, result)
        except (ValueError, TypeError, RuntimeError) as e:
            logging.getLogger("app.main").warning("Auto-decompose error for plan '%s': %s", title, e)

    results = []
    # For optional run summary
    summary = {
        "total": 0,
        "completed": 0,
        "failed": 0,
        "avg_iterations": 0.0,
        "avg_score": 0.0,
        "tools": {"planned_info": 0, "planned_output": 0, "routing_failed": 0},
    }

    def _accumulate(result_obj, status_val):
        try:
            summary["total"] += 1
            if status_val in ("done", "completed"):
                summary["completed"] += 1
            else:
                summary["failed"] += 1
            # iterations/score when available
            it = getattr(result_obj, "iterations", None)
            sc = None
            ev = getattr(result_obj, "evaluation", None)
            if ev is not None:
                try:
                    sc = float(ev.overall_score)
                except (ValueError, TypeError, AttributeError):
                    sc = None
            if it is not None:
                summary["avg_iterations"] += float(it)
            if sc is not None:
                summary["avg_score"] += float(sc)
            # tool metadata
            md = getattr(result_obj, "metadata", None)
            if isinstance(md, dict):
                tc = md.get("tool_calls") or {}
                summary["tools"]["planned_info"] += int(tc.get("info_planned") or 0)
                summary["tools"]["planned_output"] += int(tc.get("output_planned") or 0)
                if md.get("tool_routing_failed"):
                    summary["tools"]["routing_failed"] += 1
        except (KeyError, TypeError, AttributeError):
            pass

    if not title:
        # Original behavior: run all pending tasks using scheduler
        if schedule == "dag":
            ordered, cycle = requires_dag_order(None)
            if cycle:
                raise BusinessError(
                    message="Task dependency cycle detected",
                    error_code=ErrorCode.INVALID_TASK_STATE,
                    context={"cycle_info": cycle},
                )
            tasks_iter = ordered
        elif schedule == "postorder":
            tasks_iter = postorder_schedule()
        else:
            tasks_iter = bfs_schedule()
        for task in tasks_iter:
            if enable_evaluation:
                # Use enhanced executor with evaluation (optionally tool-enhanced)
                if use_tools:
                    # Combined tool + evaluation path (async wrapper)
                    result = run_async(
                        execute_task_with_tools_and_evaluation(
                            task=task,
                            repo=default_repo,
                            evaluation_mode=evaluation_mode or "llm",
                            max_iterations=evaluation_config["max_iterations"],
                            quality_threshold=evaluation_config["quality_threshold"],
                            use_context=use_context,
                            context_options=context_options,
                        )
                    )
                else:
                    # Select evaluation mode
                    if evaluation_mode == "multi_expert":
                        _exec = execute_task_with_multi_expert_evaluation
                    elif evaluation_mode == "adversarial":
                        _exec = execute_task_with_adversarial_evaluation
                    else:
                        _exec = execute_task_with_evaluation
                    result = _exec(
                        task=task,
                        repo=default_repo,
                        max_iterations=evaluation_config["max_iterations"],
                        quality_threshold=evaluation_config["quality_threshold"],
                        use_context=use_context,
                        context_options=context_options,
                    )
                task_id = result.task_id
                status = result.status
                default_repo.update_task_status(task_id, status)
                _accumulate(result, status)
                results.append(
                    {
                        "id": task_id,
                        "status": status,
                        "evaluation_mode": (evaluation_mode or "none") if enable_evaluation else "none",
                        "evaluation": (
                            {
                                "score": result.evaluation.overall_score if result.evaluation else None,
                                "iterations": result.iterations,
                            }
                            if enable_evaluation
                            else None
                        ),
                    }
                )
            else:
                # Use original executor (optionally tool-enhanced)
                if use_tools:
                    status = execute_task_enhanced(
                        task, repo=default_repo, use_context=use_context, context_options=context_options
                    )
                else:
                    status = execute_task(task, use_context=use_context, context_options=context_options)
                task_id = task["id"] if isinstance(task, dict) else task[0]
                default_repo.update_task_status(task_id, status)
                summary["total"] += 1
                if status in ("done", "completed"):
                    summary["completed"] += 1
                else:
                    summary["failed"] += 1
                results.append({"id": task_id, "status": status})
        return results

    # Filtered by plan title
    if schedule == "dag":
        ordered, cycle = requires_dag_order(title)
        if cycle:
            raise BusinessError(
                message="检测到任务依赖环", error_code=ErrorCode.INVALID_TASK_STATE, context={"cycle_info": cycle}
            )
        tasks_iter = ordered
    elif schedule == "postorder":
        tasks_iter = postorder_schedule(title)
    else:
        tasks_iter = bfs_schedule(title)

    for task in tasks_iter:
        if enable_evaluation:
            # Use enhanced executor with evaluation (optionally tool-enhanced)
            if use_tools:
                result = run_async(
                    execute_task_with_tools_and_evaluation(
                        task=task,
                        repo=default_repo,
                        evaluation_mode=evaluation_mode or "llm",
                        max_iterations=evaluation_config["max_iterations"],
                        quality_threshold=evaluation_config["quality_threshold"],
                        use_context=use_context,
                        context_options=context_options,
                    )
                )
            else:
                if evaluation_mode == "multi_expert":
                    _exec = execute_task_with_multi_expert_evaluation
                elif evaluation_mode == "adversarial":
                    _exec = execute_task_with_adversarial_evaluation
                else:
                    _exec = execute_task_with_evaluation
                result = _exec(
                    task=task,
                    repo=default_repo,
                    max_iterations=evaluation_config["max_iterations"],
                    quality_threshold=evaluation_config["quality_threshold"],
                    use_context=use_context,
                    context_options=context_options,
                )
            task_id = result.task_id
            status = result.status
            default_repo.update_task_status(task_id, status)
            _accumulate(result, status)
            results.append(
                {
                    "id": task_id,
                    "status": status,
                    "evaluation_mode": (evaluation_mode or "none") if enable_evaluation else "none",
                    "evaluation": (
                        {
                            "score": result.evaluation.overall_score if result.evaluation else None,
                            "iterations": result.iterations,
                        }
                        if enable_evaluation
                        else None
                    ),
                }
            )
        else:
            # Use original executor (optionally tool-enhanced)
            if use_tools:
                status = execute_task_enhanced(
                    task, repo=default_repo, use_context=use_context, context_options=context_options
                )
            else:
                status = execute_task(task, use_context=use_context, context_options=context_options)
            task_id = task["id"] if isinstance(task, dict) else task[0]
            default_repo.update_task_status(task_id, status)
            summary["total"] += 1
            if status in ("done", "completed"):
                summary["completed"] += 1
            else:
                summary["failed"] += 1
            results.append({"id": task_id, "status": status})
    # Finalize summary averages if requested
    include_summary = bool(getattr(rr, "include_summary", False))
    auto_assemble = bool(getattr(rr, "auto_assemble", False))
    if include_summary or auto_assemble:
        try:
            if summary["completed"] > 0:
                summary["avg_iterations"] = round(summary["avg_iterations"] / summary["completed"], 2)
                # Average score across tasks where score available (approx by completed)
                summary["avg_score"] = round(summary["avg_score"] / summary["completed"], 3)
            else:
                summary["avg_iterations"] = 0.0
                summary["avg_score"] = 0.0
        except (ZeroDivisionError, TypeError, KeyError):
            pass
        out = {"results": results, "summary": summary}
        if auto_assemble and title:
            try:
                items = default_repo.list_plan_outputs(title)
                sections = [{"name": it["short_name"], "content": it["content"]} for it in items]
                combined = "\n\n".join([f"{s['name']}\n\n{s['content']}" for s in sections])
                out["assembled"] = {"title": title, "sections": sections, "combined": combined}
            except (AttributeError, TypeError, KeyError):
                out["assembled"] = {"title": title, "sections": [], "combined": ""}
        return out
    return results


# -------------------------------
# Recursive decomposition endpoints (Phase 6)
# -------------------------------


@app.post("/tasks/{task_id}/decompose")
def decompose_task_endpoint(task_id: int, payload: Dict[str, Any] = Body(default={})):
    """Decompose a task into subtasks using AI-driven recursive decomposition.

    Body parameters:
    - max_subtasks: Maximum number of subtasks to create (default: 8)
    - force: Force decomposition even if task already has subtasks (default: false)
    - tool_aware: Use tool-aware decomposition (default: true)
    """
    max_subtasks = _parse_int(payload.get("max_subtasks", 8), default=8, min_value=2, max_value=20)
    force = _parse_bool(payload.get("force"), default=False)
    tool_aware = _parse_bool(payload.get("tool_aware"), default=True)

    try:
        if tool_aware:
            # Use tool-aware decomposition
            result = asyncio.run(
                decompose_task_with_tool_awareness(
                    task_id=task_id, repo=default_repo, max_subtasks=max_subtasks, force=force
                )
            )
        else:
            # Use standard decomposition
            result = decompose_task(task_id, repo=default_repo, max_subtasks=max_subtasks, force=force)

        if not result.get("success"):
            raise BusinessError(
                message=result.get("error", "Task decomposition failed"),
                error_code=ErrorCode.BUSINESS_RULE_VIOLATION,
                context={"task_id": task_id},
            )

        return result
    except (ValueError, TypeError) as e:
        raise CustomSystemError(
            message="Task decomposition failed due to system error",
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            cause=e,
            context={"task_id": task_id},
        ) from e


@app.get("/tasks/{task_id}/tool-requirements")
async def get_task_tool_requirements(task_id: int):
    """Analyze tool requirements for a specific task"""
    try:
        requirements = await analyze_task_tool_requirements(task_id, default_repo)

        return {
            "task_id": task_id,
            "tool_requirements": requirements,
            "recommendations": {
                "use_tool_enhanced_execution": len(requirements.get("requirements", [])) > 0,
                "expected_improvement": (
                    "15-30% quality improvement" if requirements.get("confidence", 0) > 0.7 else "Tool usage uncertain"
                ),
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Tool requirement analysis failed: {str(e)}") from e


@app.post("/tasks/{task_id}/decompose/tool-aware")
async def decompose_task_tool_aware_endpoint(task_id: int, payload: Dict[str, Any] = Body(default={})):
    """Advanced tool-aware task decomposition with enhanced capabilities

    Body parameters:
    - max_subtasks: Maximum number of subtasks to create (default: 8)
    - force: Force decomposition even if task already has subtasks (default: false)
    """
    max_subtasks = _parse_int(payload.get("max_subtasks", 8), default=8, min_value=2, max_value=20)
    force = _parse_bool(payload.get("force"), default=False)

    try:
        result = await decompose_task_with_tool_awareness(
            task_id=task_id, repo=default_repo, max_subtasks=max_subtasks, force=force
        )

        if not result.get("success"):
            raise BusinessError(
                message=result.get("error", "Tool-aware decomposition failed"),
                error_code=ErrorCode.BUSINESS_RULE_VIOLATION,
                context={"task_id": task_id},
            )

        return result
    except (ValueError, TypeError) as e:
        raise CustomSystemError(
            message="Tool-aware decomposition failed due to system error",
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            cause=e,
            context={"task_id": task_id},
        ) from e


@app.post("/plans/{title}/decompose")
def decompose_plan_endpoint(title: str, payload: Dict[str, Any] = Body(default={})):
    """Recursively decompose all tasks in a plan.

    Body parameters:
    - max_depth: Maximum decomposition depth (default: 3)
    """
    max_depth = _parse_int(payload.get("max_depth", 3), default=3, min_value=1, max_value=5)

    try:
        result = recursive_decompose_plan(title, repo=default_repo, max_depth=max_depth)

        if not result.get("success"):
            raise BusinessError(
                message=result.get("error", "Plan decomposition failed"),
                error_code=ErrorCode.BUSINESS_RULE_VIOLATION,
                context={"plan_title": title},
            )

        return result
    except Exception as e:
        raise CustomSystemError(
            message="Plan decomposition failed due to system error",
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            cause=e,
            context={"plan_title": title},
        ) from e


@app.get("/tasks/{task_id}/complexity")
def evaluate_task_complexity_endpoint(task_id: int):
    """Evaluate the complexity of a task for decomposition planning."""
    try:
        task = default_repo.get_task_info(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        task_name = task.get("name", "")
        task_prompt = default_repo.get_task_input_prompt(task_id) or ""

        complexity = evaluate_task_complexity(task_name, task_prompt)
        task_type = determine_task_type(task)
        should_decompose = should_decompose_task(task, default_repo)

        return {
            "task_id": task_id,
            "name": task_name,
            "complexity": complexity,
            "task_type": task_type.value,
            "should_decompose": should_decompose,
            "depth": task.get("depth", 0),
            "max_decomposition_depth": MAX_DECOMPOSITION_DEPTH,
        }
    except (ValueError, TypeError) as e:
        raise CustomSystemError(
            message="Task complexity evaluation failed",
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            cause=e,
            context={"task_id": task_id},
        ) from e


@app.post("/tasks/{task_id}/decompose/with-evaluation")
def decompose_task_with_evaluation_endpoint(task_id: int, payload: Dict[str, Any] = Body(default={})):
    """Decompose a task with quality evaluation and iterative improvement.

    Body parameters:
    - max_subtasks: Maximum number of subtasks to create (default: 8)
    - force: Force decomposition even if task already has subtasks (default: false)
    - quality_threshold: Minimum quality score required (default: 0.7)
    - max_iterations: Maximum number of decomposition attempts (default: 2)
    """
    max_subtasks = _parse_int(payload.get("max_subtasks", 8), default=8, min_value=2, max_value=20)
    force = _parse_bool(payload.get("force"), default=False)
    quality_threshold = _parse_opt_float(payload.get("quality_threshold"), 0.0, 1.0) or 0.7
    max_iterations = _parse_int(payload.get("max_iterations", 2), default=2, min_value=1, max_value=5)

    try:
        result = decompose_task_with_evaluation(
            task_id=task_id,
            repo=default_repo,
            max_subtasks=max_subtasks,
            force=force,
            quality_threshold=quality_threshold,
            max_iterations=max_iterations,
        )

        if not result.get("success"):
            raise BusinessError(
                message=result.get("error", "Enhanced task decomposition failed"),
                error_code=ErrorCode.BUSINESS_RULE_VIOLATION,
                context={"task_id": task_id},
            )

        return result
    except Exception as e:
        raise CustomSystemError(
            message="Enhanced task decomposition failed due to system error",
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            cause=e,
            context={"task_id": task_id},
        ) from e


@app.get("/tasks/{task_id}/decomposition/recommendation")
def get_decomposition_recommendation(task_id: int, min_complexity_score: float = 0.6):
    """Get intelligent decomposition recommendation for a task."""
    try:
        task = default_repo.get_task_info(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        min_complexity = _parse_opt_float(min_complexity_score, 0.0, 1.0) or 0.6
        recommendation = should_decompose_with_quality_check(
            task=task, repo=default_repo, min_complexity_score=min_complexity
        )

        return {"task_id": task_id, "recommendation": recommendation, "timestamp": datetime.now().isoformat()}
    except HTTPException:
        raise
    except Exception as e:
        raise CustomSystemError(
            message="Failed to generate decomposition recommendation",
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            cause=e,
            context={"task_id": task_id},
        ) from e


# -------------------------------
# Global INDEX.md endpoints (Phase 4)
# -------------------------------


def _global_index_path() -> str:
    p = os.environ.get("GLOBAL_INDEX_PATH")
    return p if (isinstance(p, str) and p.strip()) else "INDEX.md"


@app.get("/index")
def get_global_index():
    """获取全局索引文件内容"""
    path = _global_index_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        content = ""
    return {"path": path, "content": content}


@app.put("/index")
def put_global_index(payload: Dict[str, Any] = Body(...)):
    """更新全局索引文件内容"""
    content = payload.get("content") if isinstance(payload, dict) else None
    if not isinstance(content, str):
        raise HTTPException(status_code=400, detail="content (string) is required")
    path = payload.get("path") if isinstance(payload, dict) else None
    if not isinstance(path, str) or not path.strip():
        path = _global_index_path()
    try:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"write failed: {e}")
    return {"ok": True, "path": path, "bytes": len(content)}


# -------------------------------
# Context graph endpoints (Phase 1)
# -------------------------------


@app.post("/context/links")
def create_link(payload: Dict[str, Any]):
    """创建任务间的上下文链接"""
    try:
        from_id = int(payload.get("from_id"))
        to_id = int(payload.get("to_id"))
        kind = str(payload.get("kind") or "").strip()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid payload")
    if not from_id or not to_id or not kind:
        raise HTTPException(status_code=400, detail="from_id, to_id, kind are required")
    default_repo.create_link(from_id, to_id, kind)
    return {"ok": True, "link": {"from_id": from_id, "to_id": to_id, "kind": kind}}


@app.delete("/context/links")
def delete_link(payload: Dict[str, Any]):
    """删除任务间的上下文链接"""
    try:
        from_id = int(payload.get("from_id"))
        to_id = int(payload.get("to_id"))
        kind = str(payload.get("kind") or "").strip()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid payload")
    if not from_id or not to_id or not kind:
        raise HTTPException(status_code=400, detail="from_id, to_id, kind are required")
    default_repo.delete_link(from_id, to_id, kind)
    return {"ok": True}


@app.get("/context/links/{task_id}")
def get_links(task_id: int):
    """获取指定任务的所有入站和出站链接"""
    inbound = default_repo.list_links(to_id=task_id)
    outbound = default_repo.list_links(from_id=task_id)
    return {"task_id": task_id, "inbound": inbound, "outbound": outbound}


@app.post("/tasks/{task_id}/context/preview")
def context_preview(task_id: int, payload: Optional[Dict[str, Any]] = Body(None)):
    """预览任务的上下文信息"""
    # Typed parsing
    try:
        req = ContextPreviewRequest.model_validate(payload or {})
    except Exception:
        req = ContextPreviewRequest()
    include_deps = bool(req.include_deps)
    include_plan = bool(req.include_plan)
    k = _parse_int(req.k, default=5, min_value=0, max_value=50)
    # Phase 2 options (optional): budgeting
    max_chars = _parse_opt_int(req.max_chars, min_value=0, max_value=100_000)
    per_section_max = _parse_opt_int(req.per_section_max, min_value=1, max_value=50_000)
    # Optional summarization strategy: 'truncate' (default) or 'sentence'
    strategy = _parse_strategy(req.strategy) if (max_chars is not None or per_section_max is not None) else None
    # GLM semantic retrieval options
    semantic_k = _parse_int(req.semantic_k, default=5, min_value=0, max_value=50)
    min_similarity = _parse_opt_float(req.min_similarity, min_value=0.0, max_value=1.0) or 0.1
    # Hierarchy options (Phase 5)
    include_ancestors = bool(req.include_ancestors)
    include_siblings = bool(req.include_siblings)
    hierarchy_k = _parse_int(req.hierarchy_k, default=3, min_value=0, max_value=20)
    manual = _sanitize_manual_list(req.manual)
    bundle = gather_context(
        task_id,
        repo=default_repo,
        include_deps=include_deps,
        include_plan=include_plan,
        k=k,
        manual=manual,
        semantic_k=semantic_k,
        min_similarity=min_similarity,
        include_ancestors=include_ancestors,
        include_siblings=include_siblings,
        hierarchy_k=hierarchy_k,
    )
    # Apply budget only when options are provided (backward compatible)
    if (max_chars is not None) or (per_section_max is not None):
        max_chars = _parse_opt_int(max_chars, min_value=0, max_value=100_000)
        per_section_max = _parse_opt_int(per_section_max, min_value=1, max_value=50_000)
        strategy = _parse_strategy(strategy) if strategy else "truncate"
        bundle = apply_budget(bundle, max_chars=max_chars, per_section_max=per_section_max, strategy=strategy)
    return bundle


# -------------------------------
# Context snapshots endpoints (Phase 3)
# -------------------------------


@app.get("/tasks/{task_id}/context/snapshots")
def list_task_contexts_api(task_id: int):
    """列出指定任务的所有上下文快照"""
    snaps = default_repo.list_task_contexts(task_id)
    return {"task_id": task_id, "snapshots": snaps}


@app.get("/tasks/{task_id}/context/snapshots/{label}")
def get_task_context_api(task_id: int, label: str):
    """获取指定任务和标签的上下文快照"""
    snap = default_repo.get_task_context(task_id, label)
    if not snap:
        raise HTTPException(status_code=404, detail="snapshot not found")
    return snap


# -------------------------------
# Hierarchy endpoints (Phase 5)
# -------------------------------


@app.get("/tasks/{task_id}/children")
def get_task_children(task_id: int):
    """获取指定任务的所有子任务"""
    children = default_repo.get_children(task_id)
    return {"task_id": task_id, "children": children}


@app.get("/tasks/{task_id}/subtree")
def get_task_subtree(task_id: int):
    """获取指定任务的完整子树结构"""
    subtree = default_repo.get_subtree(task_id)
    if not subtree:
        raise HTTPException(status_code=404, detail="task not found")
    return {"task_id": task_id, "subtree": subtree}


@app.post("/tasks/{task_id}/move")
def move_task(task_id: int, payload: Dict[str, Any] = Body(...)):
    """移动任务到新的父任务下"""
    # Typed parsing
    try:
        req = MoveTaskRequest.model_validate(payload or {})
    except (ValidationError, ValueError) as e:
        raise HTTPException(status_code=400, detail="invalid payload") from e
    new_parent_id = req.new_parent_id
    try:
        default_repo.update_task_parent(task_id, new_parent_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, "task_id": task_id, "new_parent_id": new_parent_id}


@app.post("/tasks/{task_id}/rerun")
def rerun_single_task(task_id: int, payload: Optional[Dict[str, Any]] = Body(None)):
    """
    Re-execute a single task, reset status to pending and clear output

    Body parameters (optional):
    - use_context: Whether to use context (default false)
    - context_options: Context configuration options
    """
    task = default_repo.get_task_info(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Reset task status
    default_repo.update_task_status(task_id, "pending")

    # Clear task output
    default_repo.upsert_task_output(task_id, "")

    # Parse parameters
    try:
        # Reuse ExecuteWithEvaluationRequest for context options structure
        req = ExecuteWithEvaluationRequest.model_validate(payload or {})
    except Exception:
        req = ExecuteWithEvaluationRequest()
    use_context = bool(req.use_context)
    context_options = None
    if req.context_options is not None:
        context_options = _sanitize_context_options(req.context_options.model_dump())

    # Execute single task
    status = execute_task(task, use_context=use_context, context_options=context_options)
    default_repo.update_task_status(task_id, status)

    return {"task_id": task_id, "status": status, "rerun_type": "single"}


@app.post("/tasks/{task_id}/rerun/subtree")
def rerun_task_subtree(task_id: int, payload: Optional[Dict[str, Any]] = Body(None)):
    """
    Re-execute a single task and all its subtasks

    Body parameters (optional):
    - use_context: Whether to use context (default false)
    - context_options: Context configuration options
    - include_parent: Whether to include parent task itself (default true)
    """
    task = default_repo.get_task_info(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Get subtree (including parent task and all subtasks)
    try:
        req = RerunTaskSubtreeRequest.model_validate(payload or {})
    except Exception:
        req = RerunTaskSubtreeRequest()
    include_parent = bool(req.include_parent)
    if include_parent:
        tasks_to_rerun = [task] + default_repo.get_descendants(task_id)
    else:
        tasks_to_rerun = default_repo.get_descendants(task_id)

    if not tasks_to_rerun:
        return {"task_id": task_id, "rerun_tasks": [], "message": "No tasks to rerun"}

    # Parse parameters
    use_context = bool(req.use_context)
    context_options = None
    if req.context_options is not None:
        context_options = _sanitize_context_options(req.context_options.model_dump())

    # Execute sorted by priority
    tasks_to_rerun.sort(key=lambda t: (t.get("priority", 100), t.get("id", 0)))

    results = []
    for task_to_run in tasks_to_rerun:
        # Reset task status
        task_id_to_run = task_to_run["id"]
        default_repo.update_task_status(task_id_to_run, "pending")
        default_repo.upsert_task_output(task_id_to_run, "")

        # Execute task
        status = execute_task(task_to_run, use_context=use_context, context_options=context_options)
        default_repo.update_task_status(task_id_to_run, status)

        results.append({"task_id": task_id_to_run, "name": task_to_run["name"], "status": status})

    return {"parent_task_id": task_id, "rerun_type": "subtree", "total_tasks": len(results), "results": results}


@app.post("/tasks/rerun/selected")
def rerun_selected_tasks(payload: Dict[str, Any] = Body(...)):
    """
    Re-execute selected multiple tasks

    Body parameters:
    - task_ids: List of task IDs to re-execute
    - use_context: Whether to use context (default false)
    - context_options: Context configuration options
    """
    try:
        req = RerunSelectedTasksRequest.model_validate(payload or {})
    except Exception as exc:
        raise HTTPException(status_code=400, detail="task_ids must be a non-empty list") from exc
    task_ids = req.task_ids

    # Validate all task IDs
    tasks_to_rerun = []
    for task_id in task_ids:
        try:
            task_id = int(task_id)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=f"Invalid task_id: {task_id}") from exc

        task = default_repo.get_task_info(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

        tasks_to_rerun.append(task)

    if not tasks_to_rerun:
        raise HTTPException(status_code=400, detail="No valid tasks to rerun")

    # Parse parameters
    use_context = bool(req.use_context)
    context_options = None
    if req.context_options is not None:
        context_options = _sanitize_context_options(req.context_options.model_dump())

    # Execute sorted by priority
    tasks_to_rerun.sort(key=lambda t: (t.get("priority", 100), t.get("id", 0)))

    results = []
    successful_count = 0
    failed_count = 0

    for task_to_run in tasks_to_rerun:
        task_id_to_run = task_to_run["id"]

        # Reset task status
        default_repo.update_task_status(task_id_to_run, "pending")
        default_repo.upsert_task_output(task_id_to_run, "")

        # Execute task
        status = execute_task(task_to_run, use_context=use_context, context_options=context_options)
        default_repo.update_task_status(task_id_to_run, status)

        results.append({"task_id": task_id_to_run, "name": task_to_run["name"], "status": status})

        if status in ["completed", "done"]:
            successful_count += 1
        else:
            failed_count += 1

    return {
        "rerun_type": "selected",
        "total_tasks": len(results),
        "successful": successful_count,
        "failed": failed_count,
        "results": results,
    }


# -------------------------------
# Evaluation System Endpoints
# -------------------------------


@app.post("/tasks/{task_id}/evaluation/config")
def set_evaluation_config(task_id: int, config: Dict[str, Any] = Body(...)):
    """Set evaluation configuration for a task"""
    try:
        quality_threshold = _parse_opt_float(config.get("quality_threshold"), 0.0, 1.0) or 0.8
        max_iterations = _parse_int(config.get("max_iterations", 3), default=3, min_value=1, max_value=10)
        evaluation_dimensions = config.get("evaluation_dimensions")
        domain_specific = _parse_bool(config.get("domain_specific"), default=False)
        strict_mode = _parse_bool(config.get("strict_mode"), default=False)
        custom_weights = config.get("custom_weights")

        # Validate custom weights if provided
        if custom_weights and not isinstance(custom_weights, dict):
            raise HTTPException(status_code=400, detail="custom_weights must be a dictionary")

        # Validate evaluation dimensions if provided
        if evaluation_dimensions and not isinstance(evaluation_dimensions, list):
            raise HTTPException(status_code=400, detail="evaluation_dimensions must be a list")

        default_repo.store_evaluation_config(
            task_id=task_id,
            quality_threshold=quality_threshold,
            max_iterations=max_iterations,
            evaluation_dimensions=evaluation_dimensions,
            domain_specific=domain_specific,
            strict_mode=strict_mode,
            custom_weights=custom_weights,
        )

        return {
            "task_id": task_id,
            "config": {
                "quality_threshold": quality_threshold,
                "max_iterations": max_iterations,
                "evaluation_dimensions": evaluation_dimensions,
                "domain_specific": domain_specific,
                "strict_mode": strict_mode,
                "custom_weights": custom_weights,
            },
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.get("/tasks/{task_id}/evaluation/config")
def get_evaluation_config(task_id: int):
    """Get evaluation configuration for a task"""
    config = default_repo.get_evaluation_config(task_id)
    if not config:
        # Return default configuration
        return {
            "task_id": task_id,
            "config": {
                "quality_threshold": 0.8,
                "max_iterations": 3,
                "evaluation_dimensions": ["relevance", "completeness", "accuracy", "clarity", "coherence"],
                "domain_specific": False,
                "strict_mode": False,
                "custom_weights": None,
            },
            "is_default": True,
        }

    return {"task_id": task_id, "config": config, "is_default": False}


@app.get("/tasks/{task_id}/evaluation/history")
def get_evaluation_history(task_id: int):
    """Get evaluation history for a task"""
    history = default_repo.get_evaluation_history(task_id)

    if not history:
        return {"task_id": task_id, "history": [], "total_iterations": 0}

    return {
        "task_id": task_id,
        "history": history,
        "total_iterations": len(history),
        "latest_score": history[-1]["overall_score"] if history else None,
        "best_score": max(h["overall_score"] for h in history) if history else None,
    }


@app.get("/tasks/{task_id}/evaluation/latest")
def get_latest_evaluation(task_id: int):
    """Get the latest evaluation for a task"""
    evaluation = default_repo.get_latest_evaluation(task_id)
    if not evaluation:
        raise HTTPException(status_code=404, detail="No evaluation found for this task")

    return {"task_id": task_id, "evaluation": evaluation}


@app.post("/tasks/{task_id}/evaluation/override")
def override_evaluation(task_id: int, payload: Dict[str, Any] = Body(...)):
    """Override evaluation result with human feedback"""
    try:
        human_score = _parse_opt_float(payload.get("human_score"), 0.0, 1.0)
        human_feedback = payload.get("human_feedback", "")
        override_reason = payload.get("override_reason", "")

        if human_score is None:
            raise HTTPException(status_code=400, detail="human_score is required")

        # Get latest evaluation
        latest_eval = default_repo.get_latest_evaluation(task_id)
        if not latest_eval:
            raise HTTPException(status_code=404, detail="No evaluation found to override")

        # Store override as new evaluation entry
        iteration = latest_eval["iteration"] + 1
        metadata = {
            "override": True,
            "original_score": latest_eval["overall_score"],
            "human_feedback": human_feedback,
            "override_reason": override_reason,
            "override_timestamp": datetime.now().isoformat(),
        }

        default_repo.store_evaluation_history(
            task_id=task_id,
            iteration=iteration,
            content=latest_eval["content"],
            overall_score=human_score,
            dimension_scores=latest_eval["dimension_scores"],
            suggestions=[human_feedback] if human_feedback else [],
            needs_revision=human_score < 0.8,
            metadata=metadata,
        )

        return {
            "task_id": task_id,
            "override_applied": True,
            "new_score": human_score,
            "previous_score": latest_eval["overall_score"],
            "iteration": iteration,
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/tasks/{task_id}/execute/with-evaluation")
def execute_task_with_evaluation_api(task_id: int, payload: Optional[Dict[str, Any]] = Body(None)):
    """Execute task with evaluation-driven iterative improvement"""
    try:
        # Get task info
        task = default_repo.get_task_info(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        # Parse parameters
        try:
            req = ExecuteWithEvaluationRequest.model_validate(payload or {})
        except (ValidationError, ValueError, TypeError):
            req = ExecuteWithEvaluationRequest()
        max_iterations = _parse_int(req.max_iterations, default=3, min_value=1, max_value=10)
        quality_threshold = _parse_opt_float(req.quality_threshold, 0.0, 1.0) or 0.8
        use_context = bool(req.use_context)
        # Context options
        context_options = None
        if req.context_options is not None:
            context_options = _sanitize_context_options(req.context_options.model_dump())

        # Execute with evaluation
        result = execute_task_with_evaluation(
            task=task,
            repo=default_repo,
            max_iterations=max_iterations,
            quality_threshold=quality_threshold,
            use_context=use_context,
            context_options=context_options,
        )

        # Update task status
        default_repo.update_task_status(task_id, result.status)

        # 兼容模拟评估对象（MockEvaluation/MockDimensions）
        eval_payload = None
        if result.evaluation:
            dims = result.evaluation.dimensions
            # 支持 pydantic/dataclass/mock 三种风格
            if hasattr(dims, "model_dump"):
                dim_dict = dims.model_dump()
            elif hasattr(dims, "dict"):
                dim_dict = dims.dict()
            else:
                # 反射读取常见字段
                keys = ["relevance", "completeness", "accuracy", "clarity", "coherence", "scientific_rigor"]
                dim_dict = {k: getattr(dims, k, None) for k in keys if hasattr(dims, k)}

            needs_revision = getattr(result.evaluation, "needs_revision", None)
            if needs_revision is None and hasattr(result, "iterations"):
                # 简单兜底：按分数与阈值推断（若阈值不可得，则按0.8）
                score = getattr(result.evaluation, "overall_score", 0.0)
                needs_revision = bool(score < 0.8)

            eval_payload = {
                "overall_score": result.evaluation.overall_score,
                "dimensions": dim_dict,
                "suggestions": getattr(result.evaluation, "suggestions", []),
                "needs_revision": needs_revision,
            }

        return {
            "task_id": result.task_id,
            "status": result.status,
            "iterations": result.iterations,
            "execution_time": result.execution_time,
            "final_score": result.evaluation.overall_score if result.evaluation else None,
            "evaluation": eval_payload,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Execution failed: {str(e)}") from e


@app.get("/evaluation/stats")
def get_evaluation_stats():
    """Get overall evaluation system statistics"""
    try:
        stats = default_repo.get_evaluation_stats()
        return {
            "evaluation_stats": stats,
            "system_info": {"evaluation_enabled": True, "default_quality_threshold": 0.8, "default_max_iterations": 3},
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get stats: {str(e)}") from e


@app.delete("/tasks/{task_id}/evaluation/history")
def clear_evaluation_history(task_id: int):
    """Clear all evaluation history for a task"""
    try:
        default_repo.delete_evaluation_history(task_id)
        return {"task_id": task_id, "history_cleared": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to clear history: {str(e)}") from e


# -------------------------------
# Tool Box Integration Endpoints
# -------------------------------


@app.get("/tools/available")
async def list_available_tools():
    """List all available tools from Tool Box"""
    try:
        tools = await _list_available_tools()
        return {"tools": tools, "count": len(tools), "tool_box_version": "2.0.0"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list tools: {str(e)}") from e


@app.post("/tools/analyze")
async def analyze_tool_requirements(payload: Dict[str, Any] = Body(...)):
    """Analyze task requirements for tool usage"""
    try:
        request = payload.get("request", "")
        context = payload.get("context", {})

        if not request:
            raise HTTPException(status_code=400, detail="request is required")

        routing_result = await route_user_request(request, context)

        return {
            "analysis": routing_result,
            "tool_requirements": {
                "needs_tools": len(routing_result.get("tool_calls", [])) > 0,
                "estimated_improvement": "20-40% quality improvement expected",
                "complexity": routing_result.get("analysis", {}).get("complexity", "unknown"),
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Tool analysis failed: {str(e)}") from e


@app.post("/tasks/{task_id}/execute/tool-enhanced")
async def execute_task_with_tools_api(task_id: int, payload: Optional[Dict[str, Any]] = Body(None)):
    """Execute task with Tool Box enhancement"""
    try:
        # Get task info
        task = default_repo.get_task_info(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        # Parse options
        use_context = True
        context_options = None

        if payload:
            use_context = _parse_bool(payload.get("use_context"), default=True)
            context_options = payload.get("context_options")
            if context_options:
                context_options = _sanitize_context_options(context_options)

        # Use tool-enhanced executor
        # Execute with tools
        status = await execute_task_with_tools(
            task=task, repo=default_repo, use_context=use_context, context_options=context_options
        )

        # Update task status
        default_repo.update_task_status(task_id, status)

        return {"task_id": task_id, "status": status, "execution_type": "tool_enhanced", "enhanced_capabilities": True}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Tool-enhanced execution failed: {str(e)}") from e


@app.get("/tools/stats")
async def get_tool_stats():
    """Get Tool Box usage statistics"""
    try:
        cache_stats = await get_cache_stats()

        return {
            "cache_performance": cache_stats,
            "system_status": "operational",
            "features": {
                "intelligent_routing": True,
                "multi_tool_coordination": True,
                "performance_caching": True,
                "security_validation": True,
            },
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get tool stats: {str(e)}") from e


# -------------------------------
# Benchmark Endpoint
# -------------------------------


@app.post("/benchmark")
def benchmark_api(payload: Dict[str, Any] = Body(...)):
    """Run benchmark: generate reports under different configs and evaluate.
    Body:
      - topic: str
      - configs: List[str] like ["base,use_context=False","ctx,use_context=True,max_chars=3000"]
      - sections: int (default 5)
    """
    try:
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="invalid payload")
        topic = payload.get("topic")
        configs = payload.get("configs")
        sections = payload.get("sections", 5)
        if not isinstance(topic, str) or not topic.strip():
            raise HTTPException(status_code=400, detail="topic is required")
        if not isinstance(configs, list) or not configs:
            raise HTTPException(status_code=400, detail="configs must be a non-empty list")
        try:
            sections = int(sections)
        except (ValueError, TypeError):
            sections = 5

        out = run_benchmark(topic.strip(), configs, sections=sections)
        return out
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Benchmark failed: {str(e)}") from e
