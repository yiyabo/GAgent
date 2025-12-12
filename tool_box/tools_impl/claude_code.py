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
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# 项目根目录
_PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()

# Claude Code 运行时目录
_RUNTIME_DIR = _PROJECT_ROOT / "runtime"


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
) -> Dict[str, Any]:
    """
    Execute a task using Claude Code (official CLI) with local file access.
    
    Args:
        task: Task description for Claude to complete
        allowed_tools: Comma-separated list of allowed tools (e.g. "Bash Edit")
        add_dirs: Comma-separated list of additional directories to allow access
        skip_permissions: Skip permission checks (recommended for trusted environments)
        output_format: Output format: "text" or "json"
        
    Returns:
        Dict containing execution results
    """
    try:
        # 确保 runtime 目录存在
        _RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

        # 会话级目录 + 共享输入目录
        session_label = f"session_{session_id}" if session_id else "session_adhoc"
        session_dir = _RUNTIME_DIR / session_label
        shared_dir = session_dir / "shared"
        shared_dir.mkdir(parents=True, exist_ok=True)

        # 按日期归档的任务目录
        task_dir_name = await _generate_task_dir_name_llm(task)
        date_prefix = datetime.utcnow().strftime("%Y-%m-%d")
        task_parent = session_dir / "tasks" / date_prefix
        task_parent.mkdir(parents=True, exist_ok=True)

        # 任务子目录：语义名 + 短时间戳，避免重名
        time_suffix = datetime.utcnow().strftime("%H%M%S")
        task_dir_base = f"{task_dir_name}_{time_suffix}"
        if task_id is not None:
            task_dir_base = f"task{task_id}_{task_dir_base}"
        if plan_id is not None:
            task_dir_base = f"plan{plan_id}_{task_dir_base}"

        task_work_dir = task_parent / task_dir_base
        idx = 1
        while task_work_dir.exists():
            task_work_dir = task_parent / f"{task_dir_base}_{idx}"
            idx += 1
        task_work_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"Created task directory: {task_work_dir}")
        
        # 在任务描述中明确工作目录、文件保存位置，并注入研究任务专用 system prompt
        enhanced_task = (
            "You are an AI research assistant focused on rigorous scientific work. "
            "Your primary objective is to faithfully reproduce and critically analyze published research, "
            "including implementing methods as described in papers, reproducing figures and tables, "
            "explaining numerical results, and producing transparent, reproducible code and analysis.\n\n"
            "When working with papers, treat every numerical value as important scientific evidence. "
            "For each important number (metrics, coefficients, hyperparameters, table entries, figure annotations), "
            "explain what it represents, its units or scale when applicable, how it is computed or derived, "
            "and what it tells us scientifically. When your implementation cannot match the reported numbers or "
            "trends, do not ignore the discrepancy; instead, discuss plausible reasons such as data differences, "
            "random seeds, preprocessing, or missing details.\n\n"
            "When reproducing figures, aim to match the original scientific intent and style as closely as practical: "
            "align axis labels and units, axis ranges and tick spacing, legend entries and ordering, line styles and "
            "markers, and figure or subfigure titles. For each plot, briefly explain what each curve or group "
            "represents, what the axes mean, and what key conclusion a researcher should draw.\n\n"
            "PREFERRED COLOR PALETTE for visualizations (use these colors in order for consistency across figures):\n"
            "  Primary: #ABD1BC (sage green), #BED0F9 (soft blue), #CCCC99 (olive), #DBE4FB (light periwinkle)\n"
            "  Secondary: #E3BBED (lavender), #EDC3A5 (peach), #F1F1F1 (light gray), #FCB6A5 (coral), #FDEBAA (cream)\n"
            "When creating plots with matplotlib/seaborn, define this palette at the start:\n"
            "  COLORS = ['#ABD1BC', '#BED0F9', '#CCCC99', '#DBE4FB', '#E3BBED', '#EDC3A5', '#F1F1F1', '#FCB6A5', '#FDEBAA']\n"
            "Use these colors for bar charts, line plots, scatter plots, heatmaps, and other visualizations. "
            "Ensure sufficient contrast for accessibility and use consistent color assignments across related figures.\n\n"
            "Always design code and experiments for reproducibility: set and document random seeds, clearly specify "
            "key hyperparameters and whether they come from the paper or from you, and provide example commands for "
            "running the code. Clearly distinguish actual expected outputs from purely illustrative examples, and "
            "label illustrative results as such.\n\n"
            f"You are working in the dedicated task directory '{task_work_dir.relative_to(_PROJECT_ROOT)}'. All newly generated files "
            "(code, logs, models, processed datasets, etc.) must be saved inside this directory or its subdirectories. "
            "Do not overwrite original raw datasets; if preprocessing is needed, write new processed files and explain "
            "how they were produced. When reading existing project data, use paths that are consistent and "
            "reproducible from the project root.\n\n"
            "Maintain scientific integrity: do not fabricate real experimental data, real published papers, or "
            "citations. Clearly distinguish statements supported by the given paper or data from your own hypotheses, "
            "and label hypotheses as such.\n\n"
            "If the task description or the paper is ambiguous or under-specified, ask clarifying questions before "
            "committing to a specific implementation. Structure your responses for researchers: start with a concise "
            "high-level summary, then list concrete steps or code, and finally provide explanations and caveats. "
            "All code, comments, variable names, figure labels, and documentation you produce should be in English, "
            "even if the user communicates in another language.\n\n"
            "User task:\n"
            f"{task}"
        )
        
        # 构建命令
        cmd = [
            'claude',
            '-p',  # Print mode (non-interactive)
            enhanced_task,
            '--output-format', output_format,
        ]
        
        # 添加工具限制
        if allowed_tools:
            cmd.extend(['--allowed-tools', allowed_tools])
        
        # 添加目录访问权限（相对于项目根目录的路径）
        if add_dirs:
            for dir_path in add_dirs.split(','):
                # 转换为绝对路径
                abs_path = _PROJECT_ROOT / dir_path.strip()
                cmd.extend(['--add-dir', str(abs_path)])
        
        # 跳过权限检查（科研环境）
        if skip_permissions:
            cmd.append('--dangerously-skip-permissions')
        
        logger.info(f"Executing Claude CLI in task directory: {task_work_dir}")
        
        # 在任务专属目录执行（无超时限制，允许长时间运行）
        result = subprocess.run(
            cmd,
            cwd=str(task_work_dir),
            capture_output=True,
            text=True,
            timeout=None,  # 无超时限制，支持长时间任务（如模型训练）
        )
        
        success = result.returncode == 0
        stdout = result.stdout
        stderr = result.stderr
        
        # 解析输出
        output_data = None
        if output_format == "json" and stdout:
            try:
                output_data = json.loads(stdout)
            except json.JSONDecodeError:
                logger.warning("Failed to parse JSON output, using raw text")
                output_data = {"raw_output": stdout}
        
        # 构建返回结果
        return {
            "tool": "claude_code",
            "task": task,
            "task_directory": task_dir_base,
            "task_directory_full": str(task_work_dir),
            "session_directory": str(session_dir),
            "shared_directory": str(shared_dir),
            "success": success,
            "stdout": stdout,
            "stderr": stderr,
            "output_data": output_data,
            "exit_code": result.returncode,
            "execution_mode": "claude_code_local",
            "working_directory": str(task_work_dir),
        }
        
    except subprocess.TimeoutExpired:
        # 理论上不会触发，因为timeout=None，但保留以防万一
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


# ToolBox 工具定义
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
