"""Interactive chat command with lightweight tool-call support (API-driven).

Features:
- Text REPL with conversation memory
- Optional tool calls via JSON envelope {"tool_call": {"name": str, "args": {..}}}
- Built-in todo tools (add/list/check) mapped to our DB/API
- Plan propose/approve/execute via REST API
- ALL API calls go through unified API client for consistency

Note: This is the updated version using unified API client.
"""

import json
import logging
import os
import re
import signal
import time
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
import urllib.parse
from argparse import ArgumentParser, Namespace
from typing import Any, Dict, List, Optional

from .base import BaseCommand
from ..utils.api_client import get_api_client, APIClientError

logger = logging.getLogger(__name__)


# --- Definition of Tools for LLM ---
TOOLS_DEFINITION = [
    {
        "type": "function",
        "function": {
            "name": "intent_router",
            "description": "判定用户意图，仅返回执行建议，不直接执行任何动作。返回 {action, args, confidence}。action ∈ ['show_plan','show_tasks','show_plan_graph','execute_task','search','unknown']。",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "show_plan",
                            "show_tasks", 
                            "show_plan_graph",
                            "execute_task",
                            "search",
                            "unknown"
                        ]
                    },
                    "args": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "task_id": {"type": "integer"},
                            "output_filename": {"type": "string"},
                            "query": {"type": "string"},
                            "max_results": {"type": "integer"}
                        }
                    },
                    "confidence": {"type": "number"}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "propose_plan",
            "description": "第一步：当用户给出一个高层目标时，调用此工具生成一个顶层的、包含复合任务的计划草案。",
            "parameters": {
                "type": "object",
                "properties": {"goal": {"type": "string", "description": "用户的原始目标描述。"}},
                "required": ["goal"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "visualize_plan",
            "description": "在计划被创建或修改后，调用此工具来生成一个可视化的任务树HTML文件，并返回其本地路径。",
            "parameters": {
                "type": "object",
                "properties": {"title": {"type": "string", "description": "要可视化的计划的标题。"}},
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "decompose_task",
            "description": "第二步：当用户选择一个复合(composite)任务进行深化时，调用此工具来将其分解为更小的子任务。",
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "integer", "description": "要分解的复合任务的ID。"}},
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_atomic_task",
            "description": "第三步：当一个任务是原子(atomic)的，并且用户同意执行时，调用此工具来执行这一个任务。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "正在执行的计划的完整标题。"},
                    "task_id": {"type": "integer", "description": "要执行的原子任务的ID。"},
                    "output_filename": {"type": "string", "description": "可选：生成内容要保存的文件名（例如 notes.md）"}
                },
                "required": ["title", "task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "使用联网搜索引擎（默认 Tavily）检索信息并返回摘要结果。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索查询语句"},
                    "max_results": {"type": "integer", "description": "返回结果数量", "default": 5},
                    "search_engine": {
                        "type": "string",
                        "description": "搜索引擎标识，默认 tavily",
                        "enum": ["tavily"],
                        "default": "tavily",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_todo",
            "description": "添加一个简单的待办事项。",
            "parameters": {
                "type": "object",
                "properties": {"content": {"type": "string", "description": "待办事项的具体内容。"}},
                "required": ["content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_todos",
            "description": "列出当前的待办事项。",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["pending", "done", "all"],
                        "description": "要筛选的状态，默认为 'pending'。",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "complete_todo",
            "description": "将一个或多个待办事项标记为完成。",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "要标记为完成的任务ID列表。",
                    }
                },
                "required": ["task_ids"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_content_to_file",
            "description": "将文本内容保存到指定文件中。",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "要保存的文件名，如 'plan.md'"},
                    "content": {"type": "string", "description": "要保存的文件内容"}
                },
                "required": ["filename", "content"],
            },
        },
    },
]


class ChatCommands(BaseCommand):
    """Interactive chat command with LLM-powered tool calling (API-driven)."""

    def __init__(self):
        super().__init__()
        self.api_client = get_api_client()
        self.console = Console()  # 确保console在初始化时就可用
        self.debug_mode = False  # 默认关闭调试模式

    @property
    def name(self) -> str:
        return "chat"

    @property
    def description(self) -> str:
        return "Interactive chat with tool-call support (API-driven)"

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("--provider", type=str, default=None, help="LLM provider (default: from settings)")
        parser.add_argument("--model", type=str, default=None, help="Model name (default: from settings)")
        parser.add_argument("--session", type=str, default=None, help="Session label (optional)")
        parser.add_argument("--max-turns", type=int, default=0, help="Autostop after N turns (0=unlimited)")
        parser.add_argument("--debug", action="store_true", help="Enable debug logging for tool calls")

    def _execute_impl(self, args: Namespace) -> int:
        self.debug_mode = getattr(args, "debug", False)
        self.console = Console()
        
        # 设置智能路由模式 (默认启用，除非手动模式)
        manual_mode = getattr(args, "chat_manual", False)
        self._smart_mode = not manual_mode

        # --- Enhanced line editing with prompt_toolkit (fallback to basic input) ---
        self._ptk_available = False
        self._session = None
        try:
            from prompt_toolkit import PromptSession
            from prompt_toolkit.history import FileHistory
            from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
            from prompt_toolkit.completion import WordCompleter
            from prompt_toolkit.key_binding import KeyBindings
            from prompt_toolkit.patch_stdout import patch_stdout

            # Prepare history file under project data/
            try:
                os.makedirs("data", exist_ok=True)
                hist_path = os.path.join("data", ".chat_history")
            except Exception:
                hist_path = os.path.expanduser("~/.agent_chat_history")

            # Common tool names for quick completion
            tool_names = [
                "propose_plan",
                "visualize_plan", 
                "decompose_task",
                "execute_atomic_task",
                "web_search",
                "add_todo",
                "list_todos",
                "complete_todo",
                "save_content_to_file",
            ]
            # Add system commands for better UX
            system_commands = ["help", "exit", "quit", "clear", "debug", "session", "switch", "provider"]
            completer = WordCompleter(tool_names + system_commands, ignore_case=True)

            kb = KeyBindings()

            @kb.add("c-d")
            def _(event):
                # Exit on Ctrl+D
                event.app.exit(exception=EOFError)

            @kb.add("c-c")
            def _(event):
                # Clear current buffer on Ctrl+C (do not exit)
                event.current_buffer.reset()

            self._session = PromptSession(
                history=FileHistory(hist_path),
                auto_suggest=AutoSuggestFromHistory(),
                completer=completer,
                key_bindings=kb,
            )
            self._ptk_available = True
            self._ptk_patch_stdout = patch_stdout
        except Exception:
            self._ptk_available = False
            self._session = None

        self.io.print_section("🚀 双引擎智能聊天系统 (输入 /exit 或 /quit 退出)")
        
        # 显示当前模式和智能提示
        from app.services.foundation.settings import get_settings
        settings = get_settings()
        current_provider = getattr(args, "provider", None) or settings.llm_provider
        
        if self._smart_mode:
            self.console.print(Panel.fit(
                "[bold magenta]🤖 智能路由模式已激活 (默认)[/bold magenta]\n"
                "[cyan]自动特性:[/cyan] 根据您的请求自动选择最佳AI引擎\n"
                "[yellow]🌐 信息查询:[/yellow] 自动使用Perplexity (实时搜索)\n"
                "[yellow]🛠️ 工具操作:[/yellow] 自动使用GLM (任务执行)\n"
                "[dim]💡 手动模式：/smart off  |  查看路由：/route  |  帮助：/help[/dim]",
                border_style="magenta"
            ))
        elif current_provider == "perplexity":
            self.console.print(Panel.fit(
                "[bold green]🌐 Perplexity模式已激活[/bold green]\n"
                "[cyan]特色功能:[/cyan] 实时信息查询、知识问答、趋势分析\n"
                "[yellow]适合场景:[/yellow] \"今天AI有什么新闻？\" \"解释量子计算\" \"最新疫情情况\"\n"
                "[dim]💡 需要工具操作？输入 /switch glm  |  智能模式：/smart on[/dim]",
                border_style="green"
            ))
        else:
            self.console.print(Panel.fit(
                "[bold blue]🛠️ GLM工具模式已激活[/bold blue]\n" 
                "[cyan]特色功能:[/cyan] 工具调用、任务执行、文件操作、待办管理\n"
                "[yellow]适合场景:[/yellow] \"添加待办\" \"搜索论文\" \"执行任务\" \"保存文件\"\n"
                "[dim]💡 需要联网查询？输入 /switch perplexity  |  智能模式：/smart on[/dim]",
                border_style="blue"
            ))
        
        self.console.print(f"[dim]🌍 服务器: {self.api_client.base_url}  📱 模型: {getattr(args, 'model', 'auto')}  ❓ 帮助: /help[/dim]\n")

        # Conversation history with structured messages
        # 根据当前提供商设置系统提示
        if current_provider == "perplexity":
            system_content = (
                "You are Perplexity AI, a helpful assistant with real-time web search capabilities. "
                "You can access the latest information and provide up-to-date answers. "
                "When users ask about current events, recent news, or need the latest information, "
                "you automatically search the web and provide comprehensive answers with source citations."
            )
        else:
            system_content = (
                "You are GLM (General Language Model) by ZhipuAI, a tool-driven assistant. "
                "Always follow this decision protocol:\n\n"
                "- Step 1: Call `intent_router` to decide the action among ['show_plan','show_tasks','show_plan_graph','execute_task','search','unknown'].\n"
                "- Step 2: For display-only actions (show_* / search), you MAY directly call the corresponding tool(s).\n"
                "- Step 3: For execution actions (execute_task), DO NOT execute directly. Wait for human confirmation.\n"
                "- Never bypass confirmation by calling `execute_atomic_task` directly.\n\n"
                "Available tools:\n"
                "1. Simple: `web_search`, `add_todo`, `list_todos`, `complete_todo`\n"
                "2. Planning: `propose_plan`, `decompose_task`, `visualize_plan`, `execute_atomic_task` (needs human confirmation)\n\n"
                "If the user's request is unclear, ask for clarification before choosing an action."
            )
        
        system_prompt_message = {
            "role": "system",
            "content": system_content
        }
        history: List[Dict[str, Any]] = [system_prompt_message]

        turns = 0
        max_turns = getattr(args, "max_turns", 0) or 0
        # Execution guard: only allow execute_atomic_task when explicitly confirmed
        self._execution_guard = False

        while True:
            try:
                if self._ptk_available and self._session is not None:
                    # Prevent background prints from breaking the input line
                    with self._ptk_patch_stdout():  # type: ignore[attr-defined]
                        user_in = self._session.prompt("you> ").strip()
                else:
                    user_in = self.console.input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if not user_in:
                continue

            if user_in.lower() in {"/exit", "/quit", ":q"}:
                break

            # Slash commands (client-side)
            if user_in.startswith("/"):
                cmd_parts = user_in.strip().split()
                cmd = cmd_parts[0].lower()
                
                if cmd == "/clear":
                    # Reset conversation memory to only system prompt
                    history = [system_prompt_message]
                    try:
                        # Also clear the visible screen to give a fresh start (optional)
                        self.console.clear()
                    except Exception:
                        pass
                    self.console.print("[green]Context cleared. Starting fresh.[/green]")
                    continue
                    
                elif cmd in {"/switch", "/provider"}:
                    # 模型切换命令
                    if len(cmd_parts) > 1:
                        new_provider = cmd_parts[1].lower()
                        if new_provider in {"perplexity", "glm"}:
                            # 检查GLM密钥是否有效
                            if new_provider == "glm":
                                from app.services.foundation.settings import get_settings
                                settings = get_settings()
                                if not settings.glm_api_key:
                                    self.console.print("[red]❌ GLM API密钥未配置[/red]")
                                    self.console.print("[yellow]💡 请先设置 GLM_API_KEY 环境变量，或继续使用Perplexity模式[/yellow]")
                                    continue
                            
                            # 更新args中的provider设置
                            args.provider = new_provider
                            # 动态获取当前配置的模型
                            if new_provider == "perplexity":
                                current_model = settings.perplexity_model
                            else:
                                current_model = settings.glm_model
                            
                            self.console.print(f"[green]✅ 已切换到 {new_provider.upper()} 引擎[/green]")
                            self.console.print(f"[cyan]📱 模型: {current_model}[/cyan]")
                            
                            if new_provider == "perplexity":
                                self.console.print("[yellow]💡 Perplexity模式: 智能对话 + 自动联网搜索[/yellow]")
                            else:
                                self.console.print("[yellow]🛠️  GLM模式: 工具调用 + 结构化搜索[/yellow]")
                        else:
                            self.console.print("[red]❌ 不支持的提供商。可用选项: perplexity, glm[/red]")
                    else:
                        # 显示当前提供商
                        from app.services.foundation.settings import get_settings
                        settings = get_settings()
                        current_provider = getattr(args, "provider", None) or settings.llm_provider
                        self.console.print(f"[cyan]当前提供商: {current_provider}[/cyan]")
                        self.console.print("[yellow]用法: /switch perplexity 或 /switch glm[/yellow]")
                    continue
                    
                elif cmd in {"/smart"}:
                    # 智能路由模式切换
                    if len(cmd_parts) > 1:
                        smart_action = cmd_parts[1].lower()
                        if smart_action == "on":
                            self._smart_mode = True
                            self.console.print("[green]✅ 智能路由模式已开启[/green]")
                            self.console.print("[yellow]🤖 系统将根据您的请求自动选择最佳AI引擎[/yellow]")
                        elif smart_action == "off":
                            self._smart_mode = False
                            self.console.print("[yellow]📱 智能路由模式已关闭，回到手动模式[/yellow]")
                        else:
                            self.console.print("[red]❌ 无效选项。使用: /smart on 或 /smart off[/red]")
                    else:
                        current_smart = self._smart_mode
                        status = "开启" if current_smart else "关闭"
                        self.console.print(f"[cyan]当前智能路由模式: {status}[/cyan]")
                        self.console.print("[yellow]用法: /smart on 开启 | /smart off 关闭[/yellow]")
                    continue
                    
                elif cmd in {"/route"}:
                    # 显示路由分析（仅在智能模式下）
                    if not self._smart_mode:
                        self.console.print("[yellow]⚠️ 路由分析仅在智能模式下可用。使用 /smart on 开启[/yellow]")
                    else:
                        self.console.print("[cyan]💡 在下一次对话中，系统会显示路由分析过程[/cyan]")
                    continue
                
                elif cmd == "/help":
                    # 显示帮助信息
                    smart_status = "开启" if self._smart_mode else "关闭"
                    self.console.print(Panel.fit(
                        "[bold cyan]🚀 双引擎聊天系统帮助[/bold cyan]\n\n"
                        "[yellow]系统命令:[/yellow]\n"
                        "  /switch perplexity  - 切换到Perplexity (智能对话+自动搜索)\n"
                        "  /switch glm        - 切换到GLM (工具调用+结构化操作)\n"
                        "  /smart on/off      - 开启/关闭智能自动路由\n"
                        "  /route             - 显示路由分析 (智能模式)\n"
                        "  /clear             - 清除对话历史\n"
                        "  /help              - 显示此帮助\n"
                        "  /exit, /quit       - 退出聊天\n\n"
                        "[yellow]引擎特色:[/yellow]\n"
                        "  🌐 Perplexity: 实时信息查询、知识问答、趋势分析\n"
                        "  🛠️  GLM: 工具调用、任务执行、文件操作、待办管理\n"
                        "  🤖 智能路由: 自动选择最佳引擎 (当前:" + smart_status + ")\n\n"
                        "[yellow]使用示例:[/yellow]\n"
                        "  \"今天AI有什么新闻?\" → 自动选择Perplexity\n"
                        "  \"添加待办:学习Python\" → 自动选择GLM\n"
                        "  \"搜索最新论文\" → 自动选择GLM+搜索工具\n",
                        title="[bold blue]帮助[/bold blue]",
                        border_style="blue"
                    ))
                    continue
                
                # Unknown slash command hint
                if cmd not in {"/clear", "/switch", "/provider", "/help", "/smart", "/route"}:
                    self.console.print("[yellow]Unknown command. Try /help for available commands[/yellow]")
                    continue

            history.append({"role": "user", "content": user_in})

            # --- 智能路由处理 ---
            original_provider = getattr(args, "provider", None) or settings.llm_provider
            selected_provider = original_provider
            
            if self._smart_mode:
                # 使用新的统一智能路由架构
                from ..utils.unified_router import get_unified_router
                from ..utils.perplexity_search import get_perplexity_search
                
                router = get_unified_router(self.api_client, self.console)
                
                try:
                    # 分析用户意图并提供思考过程反馈
                    decision = router.analyze_intent_with_thinking(user_in)
                    
                    # 显示路由决策
                    confidence_pct = decision.confidence * 100
                    confidence_level = "高" if decision.confidence > 0.8 else "中" if decision.confidence > 0.6 else "低"
                    
                    self.console.print(f"🤖 统一智能路由 (置信度:{confidence_level} {confidence_pct:.1f}%)")
                    self.console.print(f"📋 推荐工具: {', '.join(decision.recommended_tools)}")
                    self.console.print(f"🎯 执行策略: {decision.execution_strategy}")
                    
                    if decision.use_web_search:
                        self.console.print(f"[dim]🌐 将使用Perplexity进行网络搜索[/dim]")
                    
                    # 统一使用GLM作为主引擎（支持工具调用）
                    selected_provider = "glm"
                    args.provider = selected_provider
                    
                    # 存储推荐的工具列表供后续使用
                    args._recommended_tools = decision.recommended_tools
                    args._use_web_search = decision.use_web_search
                        
                except Exception as e:
                    logger.error(f"统一路由失败: {e}")
                    self.console.print(f"[yellow]⚠️ 智能路由失败，使用默认引擎: {original_provider}[/yellow]")
                    selected_provider = original_provider

            # --- Main Tool-Calling Loop ---
            while True:
                # Show spinner while waiting for LLM
                with self.console.status("[bold cyan]LLM processing...", spinner="dots"):
                    response = self._call_llm_with_tools(history, args)
                if not response:
                    self.console.print("[bold red]Error: Failed to get response from LLM.[/bold red]")
                    break # Break inner loop to get next user input

                # 确保response是字典类型
                if not isinstance(response, dict):
                    self.console.print(f"[bold red]Error: Invalid response type: {type(response)}. Expected dict.[/bold red]")
                    if self.debug_mode:
                        self.console.print(f"[bold red]Response content: {response}[/bold red]")
                    break

                message = response.get("choices", [{}])[0].get("message", {})
                
                # Case 1: LLM wants to call a tool
                if message.get("tool_calls"):
                    history.append(message) # Add assistant's tool-calling request to history
                    tool_calls = message["tool_calls"]
                    
                    if self.debug_mode:
                        self.console.log(f"LLM requests tool calls: {tool_calls}", style="yellow")

                    should_break_after_tools = False
                    for tool_call in tool_calls:
                        tool_name = tool_call["function"]["name"]
                        raw_args = tool_call["function"].get("arguments", {})
                        tool_args = self._safe_parse_tool_args(raw_args)

                        # Route intent first (never executes directly)
                        if tool_name == "intent_router":
                            tool_result = self._route_intent(tool_args)
                        else:
                            tool_result = self._execute_tool_call(tool_name, tool_args)
                        
                        if self.debug_mode:
                            self.console.log(f"Tool '{tool_name}' result: {tool_result}", style="cyan")

                        compact = self._compact_tool_content(tool_name, tool_result)
                        history.append({
                            "role": "tool",
                            "tool_call_id": tool_call["id"],
                            "name": tool_name,
                            "content": json.dumps(compact, ensure_ascii=False),
                        })

                        # If a plan was successfully proposed, we already printed details.
                        # Skip LLM summarization to avoid contradictory answers.
                        if tool_name == "propose_plan" and tool_result.get("status") == "success":
                            should_break_after_tools = True

                    if should_break_after_tools:
                        break
                    # Otherwise, let the LLM summarize the tool results
                    continue

                # Case 2: LLM provides a direct answer
                else:
                    ai_content = message.get("content", "")
                    self._print_markdown_or_text(ai_content)
                    history.append({"role": "assistant", "content": ai_content})
                    break # Break inner loop, we have a final answer
            
            turns += 1
            if max_turns > 0 and turns >= max_turns:
                self.console.print("[bold yellow]Max turns reached. Exiting.[/bold yellow]")
                break
        
        return 0

    # --- Core Tool-Calling and Execution Logic ---

    def _call_llm_with_tools(self, history: List[Dict[str, Any]], args: Namespace) -> Optional[Dict[str, Any]]:
        """Calls the LLM API directly with tool definitions, supporting multiple providers."""
        try:
            from app.services.foundation.settings import get_settings
            settings = get_settings()

            # 确定使用的提供商
            provider = getattr(args, "provider", None) or os.getenv("LLM_PROVIDER") or settings.llm_provider or "glm"
            
            # 根据提供商获取配置
            if provider.lower() == "perplexity":
                api_key = settings.perplexity_api_key
                api_url = settings.perplexity_api_url
                model = getattr(args, "model", None) or settings.perplexity_model
                if not api_key:
                    self.console.print("[bold red]Error: PERPLEXITY_API_KEY is not set.[/bold red]")
                    return None
            else:  # 默认GLM
                api_key = settings.glm_api_key
                api_url = settings.glm_api_url
                model = getattr(args, "model", None) or settings.glm_model
                if not api_key:
                    self.console.print("[bold red]Error: GLM_API_KEY is not set.[/bold red]")
                    return None
            
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }

            payload = {
                "model": model,
                "messages": history,
            }
            
            # 只有GLM等支持工具调用的提供商才添加工具定义
            if provider.lower() != "perplexity":
                payload["tools"] = TOOLS_DEFINITION
                payload["tool_choice"] = "auto"

            if self.debug_mode:
                self.console.log(f"LLM request to {api_url}: {json.dumps(payload, indent=2, ensure_ascii=False)}", style="bold green")

            # Use requests with signal-based timeout for robust handling
            class TimeoutException(Exception):
                pass

            def timeout_handler(signum, frame):
                raise TimeoutException()

            signal.signal(signal.SIGALRM, timeout_handler)
            timeout_seconds = settings.glm_request_timeout or 180
            signal.alarm(timeout_seconds)

            try:
                # Direct requests call for LLM API (external, not our backend)
                import requests
                response = requests.post(api_url, headers=headers, json=payload, timeout=timeout_seconds)
            finally:
                signal.alarm(0)  # Disable the alarm
            response.raise_for_status()
            response_json = response.json()
            
            if self.debug_mode:
                self.console.log(f"LLM response: {json.dumps(response_json, indent=2, ensure_ascii=False)}", style="bold blue")

            return response_json

        except Exception as e:
            import traceback
            if self.debug_mode:
                self.console.print(f"[bold red]Full traceback: {traceback.format_exc()}[/bold red]")
            
            if "HTTPError" in str(type(e)):
                try:
                    status_code = getattr(e, 'response', None)
                    if hasattr(status_code, 'status_code'):
                        status_code = status_code.status_code
                    else:
                        status_code = 'N/A'
                    self.console.print(f"[bold red]LLM API HTTP Error: {status_code}[/bold red]")
                except Exception:
                    self.console.print(f"[bold red]LLM API HTTP Error: {e}[/bold red]")
            elif "TimeoutException" in str(type(e)) or "timeout" in str(e).lower():
                self.console.print(f"[bold red]LLM API call timed out after {timeout_seconds} seconds.[/bold red]")
            else:
                self.console.print(f"[bold red]LLM API Error: {e}[/bold red]")
            return None

    def _execute_tool_call(self, tool_name: str, tool_args: Dict[str, Any]) -> Dict[str, Any]:
        """Executes a tool call by mapping it to a backend API endpoint via unified client."""
        try:
            # Block direct execution if not confirmed
            if tool_name == "execute_atomic_task" and not getattr(self, "_execution_guard", False):
                return {"status": "error", "message": "Execution requires human confirmation. Use intent_router first."}
            
            if tool_name == "add_todo":
                content = tool_args.get("content", "")
                if not content:
                    return {"status": "error", "message": "Content cannot be empty."}
                with self.console.status("[cyan]Executing add_todo via API...", spinner="dots"):
                    result = self.api_client.post("/tasks", json_data={"name": f"[TODO] {content}"})
                return {"status": "success", "result": result}

            elif tool_name == "list_todos":
                status = tool_args.get("status", "pending")
                with self.console.status("[cyan]Fetching todos via API...", spinner="dots"):
                    all_tasks = self.api_client.get("/tasks")
                filtered = [
                    t for t in all_tasks
                    if t.get("name", "").startswith("[TODO]") and (status == "all" or t.get("status") == status)
                ]
                return {"status": "success", "result": filtered}

            elif tool_name == "complete_todo":
                task_ids = tool_args.get("task_ids", [])
                if not task_ids:
                    return {"status": "error", "message": "Task ID list cannot be empty."}
                results = []
                for task_id in task_ids:
                    with self.console.status(f"[cyan]Completing todo {task_id} via API...", spinner="dots"):
                        try:
                            result = self.api_client.put(f"/tasks/{task_id}", json_data={"status": "done"})
                            results.append({"task_id": task_id, "status": "success", "result": result})
                        except APIClientError as e:
                            results.append({"task_id": task_id, "status": "error", "detail": str(e)})
                return {"status": "success", "result": results}

            elif tool_name == "propose_plan":
                return self._propose_plan(tool_args)
            
            elif tool_name == "decompose_task":
                return self._decompose_task(tool_args)
            
            elif tool_name == "visualize_plan":
                return self._visualize_plan(tool_args)

            elif tool_name == "execute_atomic_task":
                return self._execute_atomic_task(tool_args)

            elif tool_name == "web_search":
                query = (tool_args or {}).get("query", "").strip()
                if not query:
                    return {"status": "error", "message": "Query cannot be empty."}
                max_results = tool_args.get("max_results", 5)
                search_engine = tool_args.get("search_engine", "tavily")
                
                try:
                    # 使用新的Perplexity搜索工具
                    from ..utils.perplexity_search import get_perplexity_search
                    search_tool = get_perplexity_search()
                    
                    with self.console.status(f"[cyan]🌐 Perplexity搜索: {query}...", spinner="dots"):
                        search_result = search_tool.search(query, max_results)
                    
                    if search_result["status"] == "success":
                        # 显示搜索结果
                        content = search_result.get("content", "")
                        
                        # 创建搜索结果面板
                        from rich.panel import Panel
                        self.console.print(Panel.fit(
                            content,
                            title=f"🌐 搜索结果: {query}",
                            border_style="green"
                        ))
                        
                        return {"status": "success", "result": search_result}
                    else:
                        error_msg = search_result.get("message", "搜索失败")
                        self.console.print(f"[red]❌ 搜索失败: {error_msg}[/red]")
                        return {"status": "error", "message": error_msg}
                        
                except Exception as e:
                    self.console.print(f"[red]❌ 网络搜索异常: {e}[/red]")
                    return {"status": "error", "message": f"Search failed: {str(e)}"}

            elif tool_name == "save_content_to_file":
                filename = tool_args.get("filename", "").strip()
                content = tool_args.get("content", "").strip()
                
                if not filename:
                    return {"status": "error", "message": "Filename cannot be empty."}
                if not content:
                    return {"status": "error", "message": "Content cannot be empty."}
                
                try:
                    with self.console.status(f"[cyan]Saving content to {filename}...", spinner="dots"):
                        # 确保文件保存在安全的位置
                        safe_filename = os.path.basename(filename)  # 防止路径遍历攻击
                        full_path = os.path.abspath(safe_filename)
                        
                        with open(full_path, 'w', encoding='utf-8') as f:
                            f.write(content)
                    
                    self.console.print(f"✅ 内容已保存到文件: {full_path}")
                    return {
                        "status": "success", 
                        "message": f"Content saved to {full_path}",
                        "filepath": full_path
                    }
                except Exception as e:
                    return {"status": "error", "message": f"Failed to save file: {str(e)}"}

            else:
                return {"status": "error", "message": f"Unknown tool: {tool_name}"}

        except APIClientError as e:
            self.console.print(f"[bold red]API call failed: {e}[/bold red]")
            return {"status": "error", "message": f"API call failed during tool execution: {e}"}
        except Exception as e:
            self.console.print(f"[bold red]An unexpected error occurred during tool execution: {e}[/bold red]")
            return {"status": "error", "message": f"An unexpected error occurred: {e}"}

    def _fallback_to_perplexity_search(self, query: str) -> Dict[str, Any]:
        """当Tavily搜索失败时，回退到Perplexity进行搜索"""
        from app.services.foundation.settings import get_settings
        settings = get_settings()
        
        if not settings.perplexity_api_key:
            raise Exception("Perplexity API key not configured")
        
        headers = {
            "Authorization": f"Bearer {settings.perplexity_api_key}",
            "Content-Type": "application/json",
        }
        
        # 构造搜索提示
        search_prompt = f"请搜索并总结关于以下主题的最新信息：{query}"
        
        payload = {
            "model": settings.perplexity_model,
            "messages": [
                {"role": "user", "content": search_prompt}
            ],
            "max_tokens": 500
        }
        
        import requests
        response = requests.post(settings.perplexity_api_url, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        
        result = response.json()
        if 'choices' in result and len(result['choices']) > 0:
            content = result['choices'][0]['message']['content']
            
            # 显示Perplexity搜索结果
            from rich.panel import Panel
            self.console.print(Panel.fit(
                content,
                title=f"🌐 Perplexity搜索结果: {query}",
                border_style="green"
            ))
            
            return {
                "status": "success", 
                "result": {
                    "query": query,
                    "source": "perplexity_fallback",
                    "content": content,
                    "search_engine": "perplexity"
                }
            }
        else:
            raise Exception("Invalid response from Perplexity API")

    # --- Intent routing (no direct execution) ---
    def _route_intent(self, tool_args: Dict[str, Any]) -> Dict[str, Any]:
        action = (tool_args or {}).get("action") or "unknown"
        args = (tool_args or {}).get("args") or {}
        confidence = (tool_args or {}).get("confidence")

        try:
            if action in {"show_plan", "show_tasks"}:
                # Ensure plan exists, then list tasks
                title = args.get("title")
                if not title:
                    return {"status": "error", "message": "Missing title for show_* action"}
                # Try fetch tasks directly via API client
                encoded_title = urllib.parse.quote(title)
                try:
                    tasks = self.api_client.get(f"/plans/{encoded_title}/tasks")
                except APIClientError:
                    # Fallback: propose + approve, then refetch
                    self._propose_plan({"goal": title})
                    self.api_client.post("/plans/approve", json_data={"title": title})
                    try:
                        tasks = self.api_client.get(f"/plans/{encoded_title}/tasks")
                    except APIClientError:
                        tasks = []
                self._render_tasks_table(tasks, title)
                return {"status": "success", "action": action, "count": len(tasks), "confidence": confidence}

            if action == "show_plan_graph":
                title = args.get("title")
                return self._visualize_plan({"title": title})

            if action == "search":
                # Delegate to web_search
                return self._execute_tool_call("web_search", args)

            if action == "execute_task":
                task_id = args.get("task_id")
                if not isinstance(task_id, int):
                    return {"status": "error", "message": "Missing/invalid task_id for execute_task"}
                title = args.get("title") or ""
                output_filename = args.get("output_filename")
                # Confirm with user
                confirm = self._confirm(f"Confirm execute task {task_id}{' → ' + output_filename if output_filename else ''}? [y/N] ")
                if not confirm:
                    self.console.print("[yellow]Cancelled by user.[/yellow]")
                    return {"status": "cancelled", "action": action}
                # Allow single execution
                self._execution_guard = True
                try:
                    payload = {"title": title, "task_id": task_id}
                    if output_filename:
                        payload["output_filename"] = output_filename
                    return self._execute_atomic_task(payload)
                finally:
                    self._execution_guard = False

            return {"status": "unknown", "action": action, "confidence": confidence}
        except Exception as e:
            return {"status": "error", "message": str(e), "action": action}

    def _render_tasks_table(self, tasks: List[Dict[str, Any]], title: str) -> None:
        try:
            table = Table(title=f"Plan Tasks: {title} (via API)", show_lines=False, expand=True)
            table.add_column("ID", style="bold", width=6)
            table.add_column("Name", style="cyan")
            table.add_column("Type", style="magenta", width=10)
            table.add_column("Status", style="green", width=10)
            table.add_column("Priority", style="white", width=8)
            for t in tasks or []:
                table.add_row(
                    str(t.get("id")),
                    str(t.get("short_name") or t.get("name")),
                    str(t.get("task_type", "atomic")),
                    str(t.get("status", "pending")),
                    str(t.get("priority", "")),
                )
            self.console.print(table)
        except Exception:
            # Fallback to JSON
            self.console.print(json.dumps(tasks, ensure_ascii=False, indent=2))

    def _confirm(self, prompt: str) -> bool:
        try:
            if self._ptk_available and self._session is not None:
                with self._ptk_patch_stdout():
                    s = self._session.prompt(prompt)
            else:
                s = input(prompt)
            return s.strip().lower() in {"y", "yes"}
        except Exception:
            return False

    # --- Robust tool-args parsing ---
    def _safe_parse_tool_args(self, raw: Any) -> Dict[str, Any]:
        """Parse tool arguments that may not be strict JSON.

        Handles single-quoted dicts, unescaped newlines, or already-parsed dicts.
        """
        if isinstance(raw, dict):
            return raw
        if not isinstance(raw, str):
            try:
                return dict(raw)  # type: ignore[arg-type]
            except Exception:
                return {}
        s = raw
        try:
            return json.loads(s)
        except Exception:
            pass
        try:
            import ast
            obj = ast.literal_eval(s)
            return obj if isinstance(obj, dict) else {}
        except Exception:
            pass
        try:
            s2 = s.replace("\n", "\\n")
            return json.loads(s2)
        except Exception:
            return {}

    def _looks_like_markdown(self, s: str) -> bool:
        s = s or ""
        if "```" in s:
            return True
        md_markers = ["# ", "## ", "- ", "* ", "1. ", "[", "]("]
        return any(m in s for m in md_markers)

    def _print_markdown_or_text(self, content: str) -> None:
        try:
            if self._looks_like_markdown(content):
                # Lazy import if Markdown is not imported at top
                try:
                    from rich.markdown import Markdown as _MD
                    self.console.print(_MD(content, code_theme="monokai"))
                except Exception:
                    self.console.print(content)
            else:
                self._print_stream(content)
        except Exception:
            self._print_stream(content)

    def _render_message(self, role: str, content: str) -> None:
        try:
            ts = ""
            try:
                from datetime import datetime
                ts = datetime.now().strftime("%H:%M:%S")
            except Exception:
                ts = ""
            title = f"{role}{(' • ' + ts) if ts else ''}"
            style = "cyan" if role == "assistant" else ("magenta" if role == "tool" else "white")
            if self._looks_like_markdown(content):
                try:
                    from rich.markdown import Markdown as _MD
                    body = _MD(content, code_theme="monokai")
                except Exception:
                    body = content
            else:
                body = content
            self.console.print(Panel(body, title=title, border_style=style, expand=True))
        except Exception:
            self._print_markdown_or_text(content)

    def _compact_tool_content(self, tool_name: str, tool_result: Dict[str, Any]) -> Dict[str, Any]:
        """Reduce tool result size to keep LLM prompt responsive."""
        try:
            if not isinstance(tool_result, dict):
                return {"status": "success", "result": str(tool_result)[:800]}
            out = dict(tool_result)
            # Trim long string fields
            for k, v in list(out.items()):
                if isinstance(v, str) and len(v) > 1200:
                    out[k] = v[:1200] + "..."
            # For search results, keep top-3
            if tool_name == "web_search":
                results = out.get("result") or out.get("results") or []
                if isinstance(results, list) and len(results) > 3:
                    out["results"] = results[:3]
                    out["total_results"] = max(out.get("total_results", len(results)), len(results))
            # For file ops, drop content echo
            if tool_name == "file_operations":
                if "content" in out:
                    out["content"] = "[truncated]"
            return out
        except Exception:
            return tool_result

    def _propose_plan(self, tool_args: Dict[str, Any]) -> Dict[str, Any]:
        """Handles the plan proposal step via API client."""
        goal = tool_args.get("goal", "")
        if not goal:
            return {"status": "error", "message": "Goal for proposing a plan cannot be empty."}
        
        self.console.print(f"🤖 Proposing a plan for: '{goal}' via API...")
        self.console.print("⏳ Contacting LLM to generate the plan... this may take several minutes.")
        
        try:
            plan_data = self.api_client.post("/plans/propose", json_data={"goal": goal})

            # If tasks missing, try to fetch from /plans/{title}/tasks once (decomposition fallback may have lag)
            if not plan_data or "title" not in plan_data or not plan_data.get("tasks"):
                try:
                    title = plan_data.get("title") if isinstance(plan_data, dict) else None
                    if title:
                        encoded_title = urllib.parse.quote(title)
                        tasks = self.api_client.get(f"/plans/{encoded_title}/tasks")
                        if isinstance(tasks, list) and tasks:
                            plan_data["tasks"] = tasks
                except APIClientError:
                    pass

            if not plan_data or "title" not in plan_data or not plan_data.get("tasks"):
                return {"status": "error", "message": "LLM failed to generate a valid plan with tasks."}
            
            self.console.print(f"✅ Plan '{plan_data['title']}' proposed successfully via API. Waiting for user confirmation.")
            # Return the full plan data so the LLM can present it and pass it to the execute_plan tool.
            return {"status": "success", "plan_data": plan_data}
        except APIClientError as e:
            return {"status": "error", "message": f"Plan proposal failed: {e}"}

    def _decompose_task(self, tool_args: Dict[str, Any]) -> Dict[str, Any]:
        """Handles the decomposition of a single task via API client."""
        task_id = tool_args.get("task_id")
        if not isinstance(task_id, int):
            return {"status": "error", "message": "Invalid task_id for decomposition."}
        
        self.console.print(f"🔬 Decomposing task ID {task_id} into sub-tasks via API...")
        try:
            result = self.api_client.post(f"/tasks/{task_id}/decompose")
            self.console.print(f"✅ Task {task_id} decomposed successfully into {len(result.get('subtasks', []))} sub-tasks.")
            return {"status": "success", "result": result}
        except APIClientError as e:
            return {"status": "error", "message": f"Task decomposition failed: {e}"}

    def _visualize_plan(self, tool_args: Dict[str, Any]) -> Dict[str, Any]:
        """Generates and opens an HTML visualization of the plan via API client."""
        title = tool_args.get("title")
        if not title:
            return {"status": "error", "message": "Invalid title for visualization."}
        
        try:
            import webbrowser
            encoded_title = urllib.parse.quote(title)
            data = self.api_client.get(f"/plans/{encoded_title}/visualize")
            mermaid_graph = data.get("mermaid_graph", "graph TD\n    A[No plan data found]")

            html_content = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>Plan: {title}</title>
                <script src="https://cdn.jsdelivr.net/npm/mermaid/dist/mermaid.min.js"></script>
                <script>mermaid.initialize({{startOnLoad:true}});</script>
            </head>
            <body>
                <h1>Plan: {title}</h1>
                <div class="mermaid">
                    {mermaid_graph}
                </div>
            </body>
            </html>
            """
            filepath = os.path.abspath(f"plan_{''.join(c for c in title if c.isalnum())}.html")
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(html_content)
            
            webbrowser.open(f"file://{filepath}")
            self.console.print(f"📊 Plan visualization opened in your browser via API. File saved at: {filepath}")
            return {"status": "success", "message": f"Plan visualized and opened in browser at {filepath}"}
        except APIClientError as e:
            return {"status": "error", "message": f"Plan visualization failed: {e}"}
        except Exception as e:
            self.console.print(f"[bold red]Could not generate or open visualization: {e}[/bold red]")
            return {"status": "error", "message": str(e)}

    def _execute_atomic_task(self, tool_args: Dict[str, Any]) -> Dict[str, Any]:
        """Executes a single atomic task via API client."""
        title = tool_args.get("title")
        task_id = tool_args.get("task_id")
        output_filename = tool_args.get("output_filename")
        if not title or not isinstance(task_id, int):
            return {"status": "error", "message": "Invalid title or task_id for executing a single task."}
        
        # In this new workflow, we need to approve the plan before the first execution.
        # A more robust solution would check if the plan is already approved.
        # For this implementation, we simplify by approving every time, as it's idempotent.
        self.console.print(f"🚀 Executing atomic task ID {task_id} for plan '{title}' via API...")
        
        try:
            encoded_title = urllib.parse.quote(title)
            tasks = self.api_client.get(f"/plans/{encoded_title}/tasks")
            plan_data = {"title": title, "tasks": tasks}
            
            self.api_client.post("/plans/approve", json_data=plan_data)
            self.console.print(f"✅ Plan '{title}' approved/updated in database via API.")
        except APIClientError as e:
            self.console.print(f"[yellow]Warning: Could not re-approve plan before execution via API. This might fail if tasks are not yet in DB. Error: {e}[/yellow]")

        run_payload = {
            "title": title,
            "target_task_id": task_id,
            "use_context": True,
            "enable_evaluation": True,
            "use_tools": True,
            "auto_save_output": True,
        }
        if output_filename:
            run_payload["output_filename"] = output_filename
        
        try:
            run_result = self.api_client.post("/run", json_data=run_payload)
            # Print saved artifact paths if provided
            try:
                artifacts = []
                if isinstance(run_result, dict):
                    for r in run_result.get("results", []):
                        af = r.get("artifacts")
                        if af:
                            artifacts.extend(af if isinstance(af, list) else [af])
                if artifacts:
                    try:
                        abs_paths = []
                        for p in artifacts:
                            abs_paths.append(os.path.abspath(p))
                        self.console.print(f"📁 Saved files: {', '.join(abs_paths)}")
                    except Exception:
                        self.console.print(f"📁 Saved files: {', '.join(artifacts)}")
            except Exception:
                pass
            
            task_result = next((r for r in run_result.get("results", []) if r.get("id") == task_id), None)

            if task_result and task_result.get("status") in ("done", "completed"):
                self.console.print(f"✅ Task {task_id} executed successfully via API.")
                try:
                    task_output_data = self.api_client.get(f"/tasks/{task_id}/output")
                    task_output = task_output_data.get("content", "No content produced.")
                    return {"status": "success", "task_id": task_id, "result": task_output}
                except APIClientError:
                    return {"status": "success", "task_id": task_id, "result": "Task completed, but could not retrieve output."}
            else:
                self.console.print(f"⚠️ Task {task_id} failed or status unknown.")
                return {"status": "error", "message": f"Execution of task {task_id} failed.", "details": task_result}
        except APIClientError as e:
            return {"status": "error", "message": f"Task execution failed: {e}"}

    # --- UI Helpers ---
    def _print_stream(self, text: str, delay: float = 0.01) -> None:
        """Prints text to the console with a streaming effect."""
        self.console.print("ai> ", end="")
        words = re.split(r"(\s+)", str(text))
        for word in words:
            self.console.out(word, end="")
            self.console.file.flush()
            time.sleep(delay)
        self.console.print()


def register_chat_commands_refactored():
    """Register refactored chat commands with CLI"""
    return ChatCommandsRefactored()
