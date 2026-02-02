"""
Claude CLI Executor Tool

Integrates Anthropic's Claude Code CLI for local code execution with full file access.
Uses the official 'claude' command-line tool.
"""

import logging
import subprocess
import json
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable, Awaitable
import asyncio
from app.services.plans.decomposition_jobs import get_current_job, log_job_event

logger = logging.getLogger(__name__)


def _get_available_skills() -> List[str]:
    """获取可用的 skills 列表"""
    try:
        from app.services.skills import get_skills_loader
        loader = get_skills_loader(auto_sync=False)
        skills = loader.list_skills()
        return [s.get("name", "") for s in skills if s.get("name")]
    except Exception as e:
        logger.debug(f"Failed to load skills list: {e}")
        return []

# Project root directory
_PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()

# Claude Code runtime directory
_RUNTIME_DIR = _PROJECT_ROOT / "runtime"
_LOG_DIR = _RUNTIME_DIR / "claude_code_logs"


async def _generate_task_dir_name_llm(task: str) -> str:
    """
    Generate a directory name using pure LLM semantic understanding.
    NO regex, NO keyword matching - fully LLM-based as per research requirements.
    
    Args:
        task: Task description
        
    Returns:
        Directory name like "train_baseline_model_a3f2b1"
    """
    try:
        # Use unified LLM client for semantic analysis
        from app.llm import get_default_client
        import asyncio
        
        client = get_default_client()
        
        prompt = f"""Analyze the following task and generate a concise directory name.

Task: {task}

Requirements:
1. Extract the core semantic meaning of the task
2. Generate 2-4 English words that capture the essence
3. Use lowercase with underscores (e.g., train_model, analyze_data)
4. Be specific and descriptive
5. Return ONLY the directory name, nothing else

Examples:
- Task: "分析 data/code_task 目录，训练 baseline 模型，评估得分" → analyze_train_baseline
- Task: "Generate a report on user behavior" → user_behavior_report
- Task: "Debug the authentication system" → debug_authentication

Directory name:"""
        
        # Run LLM call in executor to avoid blocking
        loop = asyncio.get_event_loop()
        llm_response = await loop.run_in_executor(None, client.chat, prompt)
        
        # Clean and validate LLM response
        dir_name = llm_response.strip().lower()
        
        # Remove any extra text (LLM might add explanation)
        # Take only the first line if multiple lines
        dir_name = dir_name.split('\n')[0].strip()
        
        # Remove common prefixes that LLM might add
        for prefix in ['directory name:', 'name:', 'output:', '→', '-', '>', '*']:
            if dir_name.startswith(prefix):
                dir_name = dir_name[len(prefix):].strip()
        
        # Ensure it's a valid directory name (basic sanitization without regex)
        # Replace spaces with underscores
        dir_name = dir_name.replace(' ', '_')
        
        # If LLM failed to generate a valid name, use a fallback
        if not dir_name or len(dir_name) < 3:
            logger.warning(f"LLM generated invalid directory name: '{llm_response}', using semantic fallback")
            # Use a simple hash-based name as last resort
            dir_name = "llm_task"
        
        # Add hash to ensure uniqueness
        task_hash = hashlib.md5(task.encode('utf-8')).hexdigest()[:6]
        
        return f"{dir_name}_{task_hash}"
        
    except Exception as e:
        logger.error(f"LLM-based directory name generation failed: {e}")
        # Research requirement: fail explicitly rather than silently degrade
        # But for directory naming, we need a fallback to avoid breaking the system
        task_hash = hashlib.md5(task.encode('utf-8')).hexdigest()[:6]
        return f"task_{task_hash}"


async def claude_code_handler(
    task: str,
    allowed_tools: Optional[str] = None,
    add_dirs: Optional[str] = None,
    skip_permissions: bool = True,
    output_format: str = "json",
    session_id: Optional[str] = None,
    plan_id: Optional[int] = None,
    task_id: Optional[int] = None,
    on_stdout: Optional[Callable[[str], Awaitable[None]]] = None,
    on_stderr: Optional[Callable[[str], Awaitable[None]]] = None,
) -> Dict[str, Any]:
    """
    Execute a task using Claude Code (official CLI) with local file access.
    
    Args:
        task: Task description for Claude to complete
        allowed_tools: Comma-separated list of allowed tools (e.g. "Bash Edit")
        add_dirs: Comma-separated list of additional directories to allow access
        skip_permissions: Skip permission checks (recommended for trusted environments)
        output_format: Output format: "text" or "json"
        session_id: Session ID for workspace isolation
        plan_id: Plan ID for workspace isolation
        task_id: Task ID for workspace isolation
        on_stdout: Async callback for stdout lines
        on_stderr: Async callback for stderr lines
        
    Returns:
        Dict containing execution results
    """
    log_file = None
    log_path = None
    log_lock = asyncio.Lock()

    try:
        # Ensure runtime directory exists
        _RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

        # Session-level directory: All tasks are placed under session_<id>/
        session_label = f"session_{session_id}" if session_id else "session_adhoc"
        session_dir = _RUNTIME_DIR / session_label
        session_dir.mkdir(parents=True, exist_ok=True)

        # Task-level directory: Each task has its own directory with only result/code/data/docs subdirectories
        run_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        task_dir_base = None
        if task_id is not None:
            task_dir_base = f"task{task_id}"
            if plan_id is not None:
                task_dir_base = f"plan{plan_id}_{task_dir_base}"
        else:
            task_dir_name = await _generate_task_dir_name_llm(task)
            if plan_id is not None:
                task_dir_name = f"plan{plan_id}_{task_dir_name}"
            task_dir_base = f"{task_dir_name}_{run_id}"

        task_work_dir = session_dir / task_dir_base
        task_work_dir.mkdir(parents=True, exist_ok=True)

        task_subdirs = ["results", "code", "data", "docs"]
        for subdir in task_subdirs:
            (task_work_dir / subdir).mkdir(parents=True, exist_ok=True)

        file_prefix = f"run_{run_id}"

        logger.info(f"Using task workspace: {task_work_dir}")

        try:
            job_id = get_current_job()
            if job_id:
                _LOG_DIR.mkdir(parents=True, exist_ok=True)
                log_path = _LOG_DIR / f"{job_id}.log"
            else:
                log_path = task_work_dir / "results" / f"{file_prefix}_claude_code.log"

            log_file = open(log_path, "a", encoding="utf-8")
            log_file.write(f"[{datetime.utcnow().isoformat()}Z] Claude Code started\n")
            log_file.write(f"task: {task}\n")
            log_file.write(f"workspace: {task_work_dir}\n")
            log_file.flush()
            log_job_event("info", "Claude Code log file initialized.", {"log_path": str(log_path)})
            log_job_event("info", "Claude Code process starting.", {"workspace": str(task_work_dir)})
        except Exception as log_exc:
            logger.warning(f"Failed to initialize Claude Code log file: {log_exc}")
        
        # Process additional directories to allow access, convert to absolute paths
        allowed_dirs = []
        
        # Always include project's data directory by default
        default_data_dir = _PROJECT_ROOT / "data"
        if default_data_dir.exists():
            allowed_dirs.append(str(default_data_dir))
            logger.info(f"Auto-added default data directory: {default_data_dir}")
        
        # Auto-add session's runtime directory for cross-task access within same session
        if session_dir.exists():
            allowed_dirs.append(str(session_dir))
            logger.info(f"Auto-added session runtime directory: {session_dir}")
        
        if add_dirs:
            for dir_path in add_dirs.split(','):
                dir_path = dir_path.strip()
                # Check if already an absolute path
                if Path(dir_path).is_absolute():
                    if dir_path not in allowed_dirs:
                        allowed_dirs.append(dir_path)
                else:
                    # Convert relative path to absolute
                    abs_path = _PROJECT_ROOT / dir_path
                    if str(abs_path) not in allowed_dirs:
                        allowed_dirs.append(str(abs_path))
            
        # Build concise task prompt with skills info and execution directive
        # Get available skills for reference
        available_skills = _get_available_skills()
        skills_info = ""
        if available_skills:
            skills_info = (
                f"Available skills: {', '.join(available_skills)}\n"
            )
        
        enhanced_task = (
            f"[SINGLE TASK EXECUTION MODE]\n"
            f"You are executing ONE specific task assigned by the outer agent. Do NOT plan or execute additional tasks.\n\n"
            f"CONSTRAINTS:\n"
            f"- Execute ONLY the task described below, nothing more\n"
            f"- Do NOT decompose into multiple sub-projects or expand scope\n"
            f"- If the task is too complex, report it and stop (let the outer agent decompose it)\n"
            f"- Focus on producing concrete outputs for THIS task only\n\n"
            f"Working directory: {task_work_dir}\n"
            f"Output folders: results/ (figures), code/ (scripts), data/ (tables), docs/ (reports)\n"
            f"File prefix: {file_prefix}\n"
            f"{skills_info}\n"
            f"Task:\n{task}\n\n"
            f"Requirements:\n"
            f"1. Write executable scripts to code/\n"
            f"2. Run the scripts and capture outputs\n"
            f"3. Save all figures/results to results/\n"
            f"4. Provide a summary of actual outputs produced"
        )
        
        # Build command
        cmd = [
            'claude',
            '-p',  # Print mode (non-interactive)
            enhanced_task,
            '--output-format', output_format,
            '--max-turns', '50',  # Allow more turns for complex tasks
        ]
        
        # Add tool restrictions
        if allowed_tools:
            cmd.extend(['--allowed-tools', allowed_tools])
        
        # Add directory access permissions (paths relative to project root)
        for abs_path in allowed_dirs:
            cmd.extend(['--add-dir', abs_path])
        
        # Explicitly inform Claude about accessible absolute paths
        allowed_dirs_info = ""
        if allowed_dirs:
            allowed_dirs_info = (
                f"\n\nIMPORTANT: You have access to these additional directories (use ABSOLUTE paths):\n"
                + "\n".join(f"  - {d}" for d in allowed_dirs)
            )
        
        # Append directory access info to task description
        if allowed_dirs_info and not enhanced_task.endswith(allowed_dirs_info):
            enhanced_task += allowed_dirs_info
        
        # Skip permission checks (research environment)
        if skip_permissions:
            cmd.append('--dangerously-skip-permissions')
        
        logger.info(f"Executing Claude CLI in task workspace: {task_work_dir}")
        
        # Use asyncio.create_subprocess_exec for non-blocking stream handling
        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(task_work_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # No timeout limit for long-running tasks
        )

        stdout_lines = []
        stderr_lines = []

        async def read_stream(stream, lines, callback, stream_name: str):
            while True:
                line = await stream.readline()
                if not line:
                    break
                decoded_line = line.decode(errors="replace").rstrip()
                lines.append(decoded_line)
                formatted_line = f"[{stream_name}] {decoded_line}" if decoded_line else f"[{stream_name}]"
                if log_file:
                    try:
                        async with log_lock:
                            log_file.write(formatted_line + "\n")
                            log_file.flush()
                    except Exception as log_err:
                        logger.warning(f"Failed to write Claude Code log line: {log_err}")
                if callback:
                    try:
                        capped_line = formatted_line
                        if len(capped_line) > 4000:
                            capped_line = capped_line[:3997] + "..."
                        await callback(capped_line)
                    except Exception as e:
                        logger.error(f"Error in stream callback: {e}")

        # Create tasks to read stdout and stderr concurrently
        stdout_task = asyncio.create_task(
            read_stream(process.stdout, stdout_lines, on_stdout, "stdout")
        )
        stderr_task = asyncio.create_task(
            read_stream(process.stderr, stderr_lines, on_stderr, "stderr")
        )

        # Wait for process to finish and streams to close
        await asyncio.wait([stdout_task, stderr_task])
        return_code = await process.wait()

        success = return_code == 0
        stdout = "\n".join(stdout_lines)
        stderr = "\n".join(stderr_lines)
        
        # Parse output
        output_data = None
        if output_format == "json" and stdout:
            try:
                output_data = json.loads(stdout)
            except json.JSONDecodeError:
                logger.warning("Failed to parse JSON output, using raw text")
                output_data = {"raw_output": stdout}
        
        if log_file:
            try:
                log_file.write(f"[{datetime.utcnow().isoformat()}Z] Claude Code finished (exit={return_code})\n")
                log_file.flush()
            except Exception as log_err:
                logger.warning(f"Failed to finalize Claude Code log file: {log_err}")

        # Build return result
        return {
            "tool": "claude_code",
            "task": task,
            "task_directory": task_dir_base,
            "task_directory_full": str(task_work_dir),
            "task_subdirectories": task_subdirs,
            "file_prefix": file_prefix,
            "session_directory": str(session_dir),
            "success": success,
            "stdout": stdout,
            "stderr": stderr,
            "output_data": output_data,
            "exit_code": return_code,
            "execution_mode": "claude_code_local",
            "working_directory": str(task_work_dir),
            "log_path": str(log_path) if log_path else None,
        }
        
    except subprocess.TimeoutExpired:
        # Should not trigger since timeout=None, but kept as a safeguard
        return {
            "success": False,
            "error": "Claude CLI execution was interrupted unexpectedly",
            "task": task,
        }
    except FileNotFoundError:
        return {
            "success": False,
            "error": "Claude CLI not found. Please install it: npm install -g @anthropic-ai/claude-code",
            "task": task,
        }
    except Exception as e:
        logger.exception(f"Claude CLI execution failed: {e}")
        return {
            "success": False,
            "error": str(e),
            "task": task,
        }
    finally:
        if log_file:
            try:
                log_file.flush()
                log_file.close()
            except Exception:
                pass


# ToolBox tool definition
claude_code_tool = {
    "name": "claude_code",
    "description": (
        "**PRIMARY TOOL FOR COMPLEX CODING TASKS** - Execute tasks using Claude Code (Anthropic's official AI assistant) with full local file access. "
        "Claude is an expert-level AI that excels at: analyzing codebases, writing production-quality code, training ML models, data analysis, debugging, and solving multi-step problems. "
        "RECOMMENDED FOR: machine learning tasks, data science projects, complex file processing, code generation, and any task requiring deep understanding and reasoning. "
        "Has access to Bash, Edit, file operations, and other advanced tools. Works directly in your project directory with full file system access."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "Detailed task description for Claude to complete"
            },
            "allowed_tools": {
                "type": "string",
                "description": "Comma-separated list of allowed tools (e.g. 'Bash,Edit'). Leave empty to allow all."
            },
            "add_dirs": {
                "type": "string",
                "description": "Comma-separated list of additional directories to allow access (e.g. 'data/code_task,models')"
            },
        },
        "required": ["task"]
    },
    "handler": claude_code_handler,
}
