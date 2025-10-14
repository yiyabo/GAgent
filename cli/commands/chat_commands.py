"""Interactive chat command with lightweight tool-call support.

Features:
- Text REPL with conversation memory
- Optional tool calls via JSON envelope {"tool_call": {"name": str, "args": {..}}}
- Built-in todo tools (add/list/check) mapped to our DB/API
- Plan propose/approve/execute via REST API

Note: This is a minimal MVP (text only). Realtime/voice can be added later.
"""

import json
import os
import re
import signal
import sys
import time
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
import urllib.parse
from argparse import ArgumentParser, Namespace
from typing import Any, Dict, List, Optional

import requests

from .base import BaseCommand


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
            "description": "第二步：当用户选择一个“复合(composite)”任务进行深化时，调用此工具来将其分解为更小的子任务。",
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
            "description": "第三步：当一个任务是“原子(atomic)”的，并且用户同意执行时，调用此工具来执行这一个任务。",
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
]


class ChatCommands(BaseCommand):
    """Interactive chat command with LLM-powered tool calling."""

    @property
    def name(self) -> str:
        return "chat"

    @property
    def description(self) -> str:
        return "Interactive chat with tool-call support"

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("--provider", type=str, default=None, help="LLM provider (default: from settings)")
        parser.add_argument("--model", type=str, default=None, help="Model name (default: from settings)")
        parser.add_argument("--session", type=str, default=None, help="Session label (optional)")
        parser.add_argument("--max-turns", type=int, default=0, help="Autostop after N turns (0=unlimited)")
        parser.add_argument("--debug", action="store_true", help="Enable debug logging for tool calls")

    def _execute_impl(self, args: Namespace) -> int:
        self.base_url = os.getenv("BASE_URL", "http://127.0.0.1:9000")
        self.debug_mode = getattr(args, "debug", False)
        self.console = Console()

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
            ]
            completer = WordCompleter(tool_names, ignore_case=True)

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

        self.io.print_section("Interactive Chat (type /exit or /quit to end)")
        self.io.print_info(f"Server: {self.base_url}, Model: {getattr(args, 'model', 'default')}")

        # Conversation history with structured messages
        system_prompt_message = {
            "role": "system",
            "content": (
                "You are a tool-driven assistant. Always follow this decision protocol:\n\n"
                "- Step 1: Call `intent_router` to decide the action among ['show_plan','show_tasks','show_plan_graph','execute_task','search','unknown'].\n"
                "- Step 2: For display-only actions (show_* / search), you MAY directly call the corresponding tool(s).\n"
                "- Step 3: For execution actions (execute_task), DO NOT execute directly. Wait for human confirmation.\n"
                "- Never bypass confirmation by calling `execute_atomic_task` directly.\n\n"
                "Available tools:\n"
                "1. Simple: `web_search`, `add_todo`, `list_todos`, `complete_todo`\n"
                "2. Planning: `propose_plan`, `decompose_task`, `visualize_plan`, `execute_atomic_task` (needs human confirmation)\n\n"
                "If the user's request is unclear, ask for clarification before choosing an action."
            )
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
                cmd = user_in.strip().split()[0].lower()
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
                # Unknown slash command hint
                if cmd not in {"/clear"}:
                    self.console.print("[yellow]Unknown command. Try /clear or /exit[/yellow]")
                    continue

            history.append({"role": "user", "content": user_in})

            # --- Main Tool-Calling Loop ---
            while True:
                # Show spinner while waiting for LLM
                with self.console.status("[bold cyan]LLM processing...", spinner="dots"):
                    response = self._call_llm_with_tools(history, args)
                if not response:
                    self.console.print("[bold red]Error: Failed to get response from LLM.[/bold red]")
                    break # Break inner loop to get next user input

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
        """Calls the LLM API directly with tool definitions, bypassing local client abstractions."""
        try:
            from app.services.foundation.settings import get_settings
            settings = get_settings()

            api_key = settings.glm_api_key
            api_url = settings.glm_api_url

            if not api_key:
                self.console.print("[bold red]Error: GLM_API_KEY is not set.[/bold red]")
                return None

            model = getattr(args, "model", None) or settings.glm_model
            
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }

            payload = {
                "model": model,
                "messages": history,
                "tools": TOOLS_DEFINITION,
                "tool_choice": "auto",
            }

            if self.debug_mode:
                self.console.log(f"LLM request to {api_url}: {json.dumps(payload, indent=2, ensure_ascii=False)}", style="bold green")

            # Use a signal-based timeout for robust handling of unresponsive APIs
            class TimeoutException(Exception):
                pass

            def timeout_handler(signum, frame):
                raise TimeoutException()

            signal.signal(signal.SIGALRM, timeout_handler)
            timeout_seconds = settings.glm_request_timeout or 180
            signal.alarm(timeout_seconds)

            try:
                response = requests.post(api_url, headers=headers, json=payload, timeout=timeout_seconds)
            finally:
                signal.alarm(0)  # Disable the alarm
            response.raise_for_status()
            response_json = response.json()
            
            if self.debug_mode:
                 self.console.log(f"LLM response: {json.dumps(response_json, indent=2, ensure_ascii=False)}", style="bold blue")

            return response_json

        except requests.HTTPError as e:
            self.console.print(f"[bold red]LLM API HTTP Error: {e.response.status_code} {e.response.text}[/bold red]")
            return None
        except TimeoutException:
            self.console.print(f"[bold red]LLM API call timed out after {timeout_seconds} seconds.[/bold red]")
            return None
        except Exception as e:
            self.console.print(f"[bold red]LLM API Error: {e}[/bold red]")
            return None

    def _execute_tool_call(self, tool_name: str, tool_args: Dict[str, Any]) -> Dict[str, Any]:
        """Executes a tool call by mapping it to a backend API endpoint."""
        try:
            # Block direct execution if not confirmed
            if tool_name == "execute_atomic_task" and not getattr(self, "_execution_guard", False):
                return {"status": "error", "message": "Execution requires human confirmation. Use intent_router first."}
            if tool_name == "add_todo":
                content = tool_args.get("content", "")
                if not content:
                    return {"status": "error", "message": "Content cannot be empty."}
                with self.console.status("[cyan]Executing add_todo...", spinner="dots"):
                    response = requests.post(f"{self.base_url}/tasks", json={"name": f"[TODO] {content}"})
                response.raise_for_status()
                return {"status": "success", "result": response.json()}

            elif tool_name == "list_todos":
                status = tool_args.get("status", "pending")
                with self.console.status("[cyan]Fetching todos...", spinner="dots"):
                    response = requests.get(f"{self.base_url}/tasks")
                response.raise_for_status()
                all_tasks = response.json()
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
                    with self.console.status(f"[cyan]Completing todo {task_id}...", spinner="dots"):
                        response = requests.put(f"{self.base_url}/tasks/{task_id}", json={"status": "done"})
                    if response.status_code == 200:
                        results.append({"task_id": task_id, "status": "success"})
                    else:
                        results.append({"task_id": task_id, "status": "error", "detail": response.text})
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
                with self.console.status(f"[cyan]Searching: {query}...", spinner="dots"):
                    resp = requests.post(
                        f"{self.base_url}/tools/web-search",
                        json={"query": query, "max_results": max_results, "search_engine": search_engine},
                        timeout=30,
                    )
                resp.raise_for_status()
                data = resp.json()
                # Pretty print results table
                results = data.get("results", [])
                table = Table(title=f"Web Search: {data.get('query', query)}", show_lines=False, expand=True)
                table.add_column("#", style="bold", width=3)
                table.add_column("Title", style="cyan")
                table.add_column("Source", style="magenta", width=18)
                table.add_column("Snippet", style="white")
                for i, item in enumerate(results[:3], 1):
                    title = item.get("title") or "(no title)"
                    src = item.get("source") or ""
                    snip = (item.get("snippet") or "").strip()
                    table.add_row(str(i), title, src, snip)
                self.console.print(table)
                return {"status": "success", "result": data}

            else:
                return {"status": "error", "message": f"Unknown tool: {tool_name}"}

        except requests.HTTPError as e:
            error_details = e.response.text if e.response else str(e)
            self.console.print(f"[bold red]API call failed (HTTP Error). Status: {getattr(e.response, 'status_code', 'N/A')}. Details: {error_details}[/bold red]")
            return {"status": "error", "message": f"API call failed during tool execution. Details: {error_details}"}
        except requests.RequestException as e:
            self.console.print(f"[bold red]API call failed (Network Error). Could not connect to {self.base_url}. Is the backend server running? Details: {e}[/bold red]")
            return {"status": "error", "message": "Could not connect to the backend server. Please ensure it is running."}
        except Exception as e:
            self.console.print(f"[bold red]An unexpected error occurred during tool execution: {e}[/bold red]")
            return {"status": "error", "message": f"An unexpected error occurred: {e}"}

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
                # Try fetch tasks directly
                encoded_title = urllib.parse.quote(title)
                resp = requests.get(f"{self.base_url}/plans/{encoded_title}/tasks", timeout=20)
                if resp.status_code != 200:
                    # Fallback: propose + approve, then refetch
                    self._propose_plan({"goal": title})
                    requests.post(f"{self.base_url}/plans/approve", json={"title": title}, timeout=20)
                    resp = requests.get(f"{self.base_url}/plans/{encoded_title}/tasks", timeout=20)
                tasks = resp.json() if resp.status_code == 200 else []
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
            table = Table(title=f"Plan Tasks: {title}", show_lines=False, expand=True)
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
        """Handles the plan proposal step."""
        goal = tool_args.get("goal", "")
        if not goal:
            return {"status": "error", "message": "Goal for proposing a plan cannot be empty."}
        
        self.console.print(f"🤖 Proposing a plan for: '{goal}'...")
        self.console.print("⏳ Contacting LLM to generate the plan... this may take several minutes. Timeout is set to 300 seconds.")
        propose_response = requests.post(f"{self.base_url}/plans/propose", json={"goal": goal}, timeout=300)
        propose_response.raise_for_status()
        plan_data = propose_response.json()

        # If tasks missing, try to fetch from /plans/{title}/tasks once (decomposition fallback may have lag)
        if not plan_data or "title" not in plan_data or not plan_data.get("tasks"):
            try:
                title = plan_data.get("title") if isinstance(plan_data, dict) else None
                if title:
                    encoded_title = urllib.parse.quote(title)
                    r = requests.get(f"{self.base_url}/plans/{encoded_title}/tasks", timeout=10)
                    if r.status_code == 200:
                        tasks = r.json()
                        if isinstance(tasks, list) and tasks:
                            plan_data["tasks"] = tasks
            except Exception:
                pass

        if not plan_data or "title" not in plan_data or not plan_data.get("tasks"):
            return {"status": "error", "message": "LLM failed to generate a valid plan with tasks."}
        
        self.console.print(f"✅ Plan '{plan_data['title']}' proposed successfully. Waiting for user confirmation.")
        # Return the full plan data so the LLM can present it and pass it to the execute_plan tool.
        return {"status": "success", "plan_data": plan_data}

    def _decompose_task(self, tool_args: Dict[str, Any]) -> Dict[str, Any]:
        """Handles the decomposition of a single task."""
        task_id = tool_args.get("task_id")
        if not isinstance(task_id, int):
            return {"status": "error", "message": "Invalid task_id for decomposition."}
        
        self.console.print(f"🔬 Decomposing task ID {task_id} into sub-tasks...")
        response = requests.post(f"{self.base_url}/tasks/{task_id}/decompose")
        response.raise_for_status()
        result = response.json()
        self.console.print(f"✅ Task {task_id} decomposed successfully into {len(result.get('subtasks', []))} sub-tasks.")
        return {"status": "success", "result": result}

    def _visualize_plan(self, tool_args: Dict[str, Any]) -> Dict[str, Any]:
        """Generates and opens an HTML visualization of the plan."""
        title = tool_args.get("title")
        if not title:
            return {"status": "error", "message": "Invalid title for visualization."}
        
        try:
            import webbrowser
            encoded_title = urllib.parse.quote(title)
            response = requests.get(f"{self.base_url}/plans/{encoded_title}/visualize")
            response.raise_for_status()
            data = response.json()
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
            self.console.print(f"📊 Plan visualization opened in your browser. File saved at: {filepath}")
            return {"status": "success", "message": f"Plan visualized and opened in browser at {filepath}"}
        except Exception as e:
            self.console.print(f"[bold red]Could not generate or open visualization: {e}[/bold red]")
            return {"status": "error", "message": str(e)}

    def _execute_atomic_task(self, tool_args: Dict[str, Any]) -> Dict[str, Any]:
        """Executes a single atomic task."""
        title = tool_args.get("title")
        task_id = tool_args.get("task_id")
        output_filename = tool_args.get("output_filename")
        if not title or not isinstance(task_id, int):
            return {"status": "error", "message": "Invalid title or task_id for executing a single task."}
        
        # In this new workflow, we need to approve the plan before the first execution.
        # A more robust solution would check if the plan is already approved.
        # For this implementation, we simplify by approving every time, as it's idempotent.
        self.console.print(f"🚀 Executing atomic task ID {task_id} for plan '{title}'...")
        
        try:
            tasks_response = requests.get(f"{self.base_url}/plans/{urllib.parse.quote(title)}/tasks")
            tasks_response.raise_for_status()
            tasks = tasks_response.json()
            plan_data = {"title": title, "tasks": tasks}
            
            approve_response = requests.post(f"{self.base_url}/plans/approve", json=plan_data, timeout=30)
            approve_response.raise_for_status()
            self.console.print(f"✅ Plan '{title}' approved/updated in database.")
        except Exception as e:
            self.console.print(f"[yellow]Warning: Could not re-approve plan before execution. This might fail if tasks are not yet in DB. Error: {e}[/yellow]")

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
        run_response = requests.post(f"{self.base_url}/run", json=run_payload, timeout=1800)
        run_response.raise_for_status()
        run_result = run_response.json()
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
             self.console.print(f"✅ Task {task_id} executed successfully.")
             output_response = requests.get(f"{self.base_url}/tasks/{task_id}/output", timeout=60)
             if output_response.status_code == 200:
                 task_output = output_response.json().get("content", "No content produced.")
                 return {"status": "success", "task_id": task_id, "result": task_output}
             else:
                return {"status": "success", "task_id": task_id, "result": "Task completed, but could not retrieve output."}
        else:
            self.console.print(f"⚠️ Task {task_id} failed or status unknown.")
            return {"status": "error", "message": f"Execution of task {task_id} failed.", "details": task_result}

    # --- UI Helpers ---
    def _print_stream(self, text: str, delay: float = 0.01) -> None:
        """Prints text to the console with a streaming effect."""
        self.console.print(f"ai> ", end="")
        words = re.split(r"(\s+)", str(text))
        for word in words:
            self.console.out(word, end="")
            self.console.file.flush()
            time.sleep(delay)
        self.console.print()
