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
            "name": "propose_plan",
            "description": "第一步：当用户提出复杂目标（如写报告）时，调用此工具来生成一个初步的计划草案。",
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
            "name": "execute_plan",
            "description": "第二步：在计划被用户确认后，调用此工具来批准并执行该计划。",
            "parameters": {
                "type": "object",
                "properties": {
                    "plan_data": {
                        "type": "object",
                        "description": "由 `propose_plan` 工具返回的、未经修改的完整计划JSON对象。",
                    }
                },
                "required": ["plan_data"],
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
        self.base_url = os.getenv("BASE_URL", "http://127.0.0.1:8000")
        self.debug_mode = getattr(args, "debug", False)
        self.console = Console()

        self.io.print_section("Interactive Chat (type /exit or /quit to end)")
        self.io.print_info(f"Server: {self.base_url}, Model: {getattr(args, 'model', 'default')}")

        # Conversation history with structured messages
        history: List[Dict[str, Any]] = [{
            "role": "system",
            "content": (
                "You are a helpful assistant that MUST use tools to fulfill user requests. You have two types of tools:\n"
                "1. **Simple Tools** (`add_todo`, `list_todos`, `complete_todo`): For direct, simple requests about todos (like 'add a new todo', 'list my tasks'), you MUST call the corresponding tool directly.\n"
                "2. **Planning Tools** (`propose_plan`, `execute_plan`): For complex goals that require multiple steps (like 'write a report' or 'learn a new skill'), you MUST follow the two-step process: first `propose_plan`, then after user approval, `execute_plan`.\n"
                "Always prefer using a tool over just chatting. If a user's request is unclear, ask for clarification before calling a tool."
            )
        }]

        turns = 0
        max_turns = getattr(args, "max_turns", 0) or 0
        while True:
            try:
                user_in = self.console.input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if not user_in:
                continue

            if user_in.lower() in {"/exit", "/quit", ":q"}:
                break

            history.append({"role": "user", "content": user_in})

            # --- Main Tool-Calling Loop ---
            while True:
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

                    for tool_call in tool_calls:
                        tool_name = tool_call["function"]["name"]
                        tool_args = json.loads(tool_call["function"]["arguments"])
                        
                        tool_result = self._execute_tool_call(tool_name, tool_args)
                        
                        if self.debug_mode:
                            self.console.log(f"Tool '{tool_name}' result: {tool_result}", style="cyan")

                        history.append({
                            "role": "tool",
                            "tool_call_id": tool_call["id"],
                            "name": tool_name,
                            "content": json.dumps(tool_result, ensure_ascii=False),
                        })
                    # After executing tools, loop back to let the LLM summarize the results
                    continue

                # Case 2: LLM provides a direct answer
                else:
                    ai_content = message.get("content", "")
                    self._print_stream(ai_content)
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
            if tool_name == "add_todo":
                content = tool_args.get("content", "")
                if not content:
                    return {"status": "error", "message": "Content cannot be empty."}
                response = requests.post(f"{self.base_url}/tasks", json={"name": f"[TODO] {content}"})
                response.raise_for_status()
                return {"status": "success", "result": response.json()}

            elif tool_name == "list_todos":
                status = tool_args.get("status", "pending")
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
                    response = requests.put(f"{self.base_url}/tasks/{task_id}", json={"status": "done"})
                    if response.status_code == 200:
                        results.append({"task_id": task_id, "status": "success"})
                    else:
                        results.append({"task_id": task_id, "status": "error", "detail": response.text})
                return {"status": "success", "result": results}

            elif tool_name == "propose_plan":
                return self._propose_plan(tool_args)
            
            elif tool_name == "execute_plan":
                return self._execute_plan(tool_args)

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

        if not plan_data or "title" not in plan_data or not plan_data.get("tasks"):
            return {"status": "error", "message": "LLM failed to generate a valid plan with tasks."}
        
        self.console.print(f"✅ Plan '{plan_data['title']}' proposed successfully. Waiting for user confirmation.")
        # Return the full plan data so the LLM can present it and pass it to the execute_plan tool.
        return {"status": "success", "plan_data": plan_data}

    def _execute_plan(self, tool_args: Dict[str, Any]) -> Dict[str, Any]:
        """Handles the approval and execution of a user-confirmed plan."""
        plan_data = tool_args.get("plan_data")
        if not isinstance(plan_data, dict) or "title" not in plan_data:
            return {"status": "error", "message": "Invalid plan data provided for execution."}
        
        title = plan_data["title"]
        self.console.print(f"👍 User approved plan '{title}'. Starting execution...")

        # 1. Approve plan
        approve_response = requests.post(f"{self.base_url}/plans/approve", json=plan_data, timeout=30)
        approve_response.raise_for_status()
        self.console.print(f"✅ Plan '{title}' approved.")

        # 2. Run plan with all advanced features enabled
        self.console.print(f"🚀 Executing plan '{title}' with full capabilities (decomposition, context, evaluation)...")
        run_payload = {
            "title": title,
            "use_context": True,
            "enable_evaluation": True,
            "use_tools": True,
            "auto_decompose": True,
            "auto_assemble": True,
            "include_summary": True,
        }
        run_response = requests.post(f"{self.base_url}/run", json=run_payload, timeout=1800)
        run_response.raise_for_status()
        run_result = run_response.json()
        self.console.print(f"✅ Plan '{title}' executed successfully.")

        # 3. Process the assembled result from the run response
        self.console.print("📝 Processing the final report...")
        report_data = run_result.get("assembled")
        if report_data and "combined" in report_data:
            report_content = report_data.get("combined", "No content assembled.")
            safe_filename = "".join(c for c in title if c.isalnum() or c in (' ', '_')).rstrip()
            filename = f"report_{safe_filename.replace(' ', '_')}.md"
            with open(filename, "w", encoding="utf-8") as f:
                f.write(report_content)
            self.console.print(f"✅ Report saved to local file: [bold cyan]{filename}[/bold cyan]")
            return {"status": "success", "message": f"Plan '{title}' executed and report saved to '{filename}'."}
        else:
            self.console.print(f"[yellow]Warning: Plan executed but no report was assembled by the backend.[/yellow]")
            return {"status": "warning", "message": f"Plan '{title}' executed, but the final report was not assembled."}

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
