from typing import Any, Dict, Optional, List
from enum import Enum
import copy
import json
import logging
import os
from datetime import datetime
from fastapi import BackgroundTasks

from ..llm import get_default_client as get_llm
from ..repository.tasks import default_repo
from .plan_session import plan_session_manager, PlanGraphSession
from ..utils import parse_json_obj


logger = logging.getLogger(__name__)



SNAPSHOT_DIR = os.path.join(os.getcwd(), "logs", "plan_snapshots")

_PENDING_INSTRUCTIONS: Dict[int, List[Dict[str, Any]]] = {}


class IntentType(Enum):
    """支持的意图类型"""
    CREATE_PLAN = "create_plan"
    CREATE_TASK = "create_task"
    UPDATE_TASK = "update_task"
    UPDATE_TASK_INSTRUCTION = "update_task_instruction"
    MOVE_TASK = "move_task"
    LIST_PLANS = "list_plans"
    EXECUTE_PLAN = "execute_plan"
    QUERY_STATUS = "query_status"
    SHOW_TASKS = "show_tasks"
    RERUN_TASK = "rerun_task"
    DELETE_PLAN = "delete_plan"
    DELETE_TASK = "delete_task"
    HELP = "help"
    CHAT = "chat"  # 普通聊天
    UNKNOWN = "unknown"


class VisualizationType(Enum):
    """可视化类型"""
    PLAN_LIST = "plan_list"
    PLAN_DETAILS = "plan_details"
    TASK_TREE = "task_tree"
    TASK_LIST = "task_list"
    EXECUTION_PROGRESS = "execution_progress"
    RESULT_DISPLAY = "result_display"
    STATUS_DASHBOARD = "status_dashboard"
    HELP_MENU = "help_menu"
    NONE = "none"


class ConversationalAgent:
    """
    增强版对话代理，支持意图识别和可视化指令生成
    """

    def __init__(
        self, 
        plan_id: Optional[int] = None, 
        background_tasks: Optional[BackgroundTasks] = None,
        conversation_history: Optional[List[Dict]] = None,
        conversation_id: Optional[int] = None,
    ):
        self.plan_id = plan_id
        self.llm = get_llm()
        self.background_tasks = background_tasks
        self.history = conversation_history or []
        self.conversation_id = conversation_id
        self.plan_session: Optional[PlanGraphSession] = None
        self._id_aliases: Dict[int, int] = {}
        self._graph_summary_cache: Optional[Dict[str, Any]] = None
        self._subgraph_cache: Dict[int, Dict[str, Any]] = {}
        if self.plan_id:
            plan_session_manager.flush_stale()
            self.plan_session = plan_session_manager.activate_plan(self.plan_id)
        
    async def process_command(self, user_command: str, confirmed: bool = False) -> Dict[str, Any]:
        """统一处理用户命令：LLM判断是否需要工具调用或直接对话"""
        
        logger.info(f"🚀 NEW process_command called with: '{user_command[:50]}...', confirmed={confirmed}\n")

        try:
            # 使用统一的LLM提示进行意图识别和响应生成
            result = await self._unified_intent_and_response(user_command, confirmed)
            logger.info(f"🎯 NEW process_command returning: {result.get('intent', 'unknown')}")
            return result
            
        except Exception as e:
            logger.error(f"Error in process_command: {e}")
            fallback_response = "抱歉，处理您的请求时出现错误。请重试或换个方式表达。"
            return {
                "response": fallback_response,
                "initial_response": fallback_response,
                "execution_feedback": None,
                "intent": "error",
                "visualization": {"type": "none", "data": {}, "config": {}},
                "action_result": {"success": False, "message": str(e)},
                "success": False
            }

    async def _find_task_id_by_name(self, task_name: str) -> Dict[str, Any]:
        """使用LLM模糊查找任务ID"""
        if not self.plan_id:
            return {"success": False, "message": "I don't know which plan you're referring to. Please specify a plan first."}

        logger.info(f"Fuzzy searching for task '{task_name}' in plan {self.plan_id}")
        if self.plan_session:
            tasks_summary = self.plan_session.list_task_summaries()
        else:
            tasks_summary = default_repo.get_plan_tasks_summary(self.plan_id)

        if not tasks_summary:
            return {"success": False, "message": "This plan has no tasks."}

        # 使用LLM进行模糊匹配
        prompt = f"""From the following list of tasks, find the one that best matches the user's request.

User's request refers to: "{task_name}"

List of available tasks:
{json.dumps(tasks_summary, indent=2)}

Respond with JSON containing the ID of the best match. For example: {{'best_match_id': 123}}
If no task is a clear match, respond with: {{'best_match_id': null}}
"""
        try:
            response_str = self.llm.chat(prompt)
            response_json = parse_json_obj(response_str)
            task_id = response_json.get("best_match_id")

            if not task_id:
                return {"success": False, "message": f"I couldn't find a task that clearly matches '{task_name}'."}

            if self.plan_session:
                # Convert best_match_id (db) to logical if needed
                logical = self.plan_session.get_logical_id(task_id)
                if logical is not None:
                    task_id = logical

            matched_task = None
            for task in tasks_summary:
                candidate_id = task.get("id")
                candidate_db = task.get("db_id")
                if candidate_id == task_id or candidate_db == task_id:
                    matched_task = task
                    break
            if not matched_task:
                 return {"success": False, "message": "LLM returned an invalid task ID."}

            return {"success": True, "task": matched_task}

        except Exception as e:
            logger.error(f"LLM fuzzy search failed: {e}")
            return {"success": False, "message": "I had trouble searching for the task."}
    
    async def _unified_intent_and_response(self, user_command: str, confirmed: bool = False) -> Dict[str, Any]:
        """统一的意图识别和响应生成"""

        logger.info(f"🔄 _unified_intent_and_response called with: '{user_command[:50]}...\n'")

        # 构建上下文信息
        context_sections: List[str] = []
        graph_summary_payload: Optional[Dict[str, Any]] = None

        if self.plan_id is not None:
            context_sections.append(f"Current plan ID: {self.plan_id}.")

        if self.plan_session:
            try:
                graph_summary_payload = self._build_graph_summary_payload()
            except Exception as exc:
                logger.warning("Failed to build graph summary payload: %s", exc)
                graph_summary_payload = None
            if graph_summary_payload:
                summary_text = json.dumps(graph_summary_payload, ensure_ascii=False, indent=2)
                context_sections.append(
                    "GraphSummary (current layer + direct children):\n" + summary_text
                )
                self._save_plan_snapshot(graph_summary_payload, label="graph_summary")

        context_info = "\n\n".join(context_sections).strip()
        if context_info:
            context_info += "\n\n"

        if not confirmed and self.conversation_id is not None:
            # Drop any stale instructions awaiting confirmation if the user issues a new command
            _PENDING_INSTRUCTIONS.pop(self.conversation_id, None)

        instructions: Optional[List[Dict[str, Any]]] = None
        raw_result: Optional[str] = None

        if confirmed and self.conversation_id is not None:
            cached = _PENDING_INSTRUCTIONS.pop(self.conversation_id, None)
            if cached:
                instructions = copy.deepcopy(cached)
                logger.info(
                    "Using cached instructions for confirmation run (conversation_id=%s)",
                    self.conversation_id,
                )

        if instructions is None:
            llm_history = list(self.history or [])
            provided_subgraphs: Dict[int, Dict[str, Any]] = {}
            max_subgraph_rounds = 6

            def extract_subgraph_requests(items: List[Dict[str, Any]]) -> List[int]:
                request_ids: List[int] = []
                for entry in items:
                    intent_val = (entry.get("intent") or "").strip().lower()
                    if intent_val != "request_subgraph":
                        continue
                    params = entry.get("parameters") or {}
                    logical = params.get("logical_id", params.get("task_id"))
                    try:
                        logical_id = int(logical)
                    except (TypeError, ValueError):
                        continue
                    request_ids.append(logical_id)
                return request_ids

            def format_subgraph_payloads(payloads: List[Dict[str, Any]]) -> str:
                sections: List[str] = []
                for payload in payloads:
                    detail_json = json.dumps(payload, ensure_ascii=False, indent=2)
                    sections.append(
                        f"SubgraphDetail for logical_id {payload['root_id']} (current layer + direct children):\n{detail_json}"
                    )
                return "\n\n".join(sections)

            def build_invalid_request_message(missing: List[int], repeated: List[int]) -> str:
                parts: List[str] = []
                if missing:
                    parts.append(
                        "The following logical_id values are not available in the current plan: "
                        + ", ".join(str(mid) for mid in sorted(set(missing)))
                        + "."
                    )
                if repeated:
                    parts.append(
                        "You already have the subgraph details for logical_id(s): "
                        + ", ".join(str(rid) for rid in sorted(set(repeated)))
                        + "."
                    )
                parts.append(
                    "Select another logical_id from the GraphSummary or proceed using the available context."
                )
                parts.append(
                    "If you still require more detail, issue a new 'request_subgraph' instruction. Otherwise, return the actionable instruction list in the required JSON format."
                )
                return "\n".join(parts)

            unified_prompt = f"""You are an AI assistant for a research plan management system. {context_info}Analyze the user's message and break it down into an ordered list of instructions that should be executed sequentially.

Respond with JSON ONLY using the following structure:
{{
  "instructions": [
    {{
      "needs_tool": true,
      "intent": "create_plan|create_task|update_task|update_task_instruction|list_plans|execute_plan|show_tasks|query_status|delete_plan|delete_task|rerun_task|help|request_subgraph",
      "parameters": {{ "goal": "...", "plan_id": "...", "task_id": "...", "task_name": "...", "name": "...", "status": "...", "instruction": "...", "logical_id": "..." }},
      "initial_response": "I'll help you with that. Let me [action description]..."
    }},
    {{
      "needs_tool": false,
      "intent": "chat",
      "response": "Your natural conversational follow-up after the tools finish..."
    }}
  ]
}}

Rules:
- Always return the array in execution order (step 1 first).
- Include at least one instruction. Use a single item when only one action is needed.
- Only set "needs_tool" to true when a repository tool must be called. Casual replies should use "needs_tool": false and "intent": "chat".
- Reuse tool intents exactly as listed below.

Tool intents:
- create_plan: Create new research plans (extract goal, title, sections, etc.)
- create_task: Create a new task within a plan (extract task_name, parent_id, and plan_id if available)
- update_task: Modify a task's METADATA (extract task_id OR task_name, and fields to change like name or status). Does NOT change the instruction.
- update_task_instruction: Change the detailed instructions/prompt for a task (extract task_id or task_name, and the new instruction)
- move_task: Reparent a task under a new parent (extract task_id and new_parent_id; use null/None to move to root)
- list_plans: Show all existing plans
- execute_plan: Start executing a specific plan
- show_tasks: Display tasks in a plan
- query_status: Check status/progress of plans or tasks  
- delete_plan: Remove a plan
- delete_task: Remove a task (and its subtasks) from a plan
- rerun_task: Restart a specific task
- help: Show available commands
- request_subgraph: Ask for a deeper view of a task when the GraphSummary is insufficient (set "needs_tool": false and include {{'logical_id': <id>}}). Wait for the new context before issuing other actions.

Additional rules:
- The `GraphSummary` may include a `"has_more_children": true` flag on a task. If the user's request might involve these unlisted children, you MUST use `request_subgraph` with that task's `id` to get more details before proceeding. Do not guess.
- If you schedule a create_plan instruction, do not include any create_task instructions for the same request. The plan generator initializes the entire task graph automatically.
- When you emit a request_subgraph instruction, it should be the only instruction in that response. After you receive the SubgraphDetail, issue the actionable instruction list.

Examples:
- "change the status of the 'data analysis' task to done" → {{'instructions': [{{'needs_tool': true, 'intent': 'update_task', 'parameters': {{'task_name': 'data analysis', 'status': 'done'}}, 'initial_response': 'Updating the task status...'}}]}}
- "create a plan and then show it" → {{'instructions': [{{'needs_tool': true, 'intent': 'create_plan', 'parameters': {{'goal': '...'}}, 'initial_response': 'Creating the plan...'}}, {{'needs_tool': true, 'intent': 'show_tasks', 'parameters': {{'plan_id': '...'}}, 'initial_response': 'Listing the tasks...'}}]}} (note: no create_task step needed)
- "drill into task 7" → {{'instructions': [{{'needs_tool': false, 'intent': 'request_subgraph', 'parameters': {{'logical_id': 7}}, 'initial_response': 'Fetching the detailed subgraph for task 7...'}}]}}
- "thanks" → {{'instructions': [{{'needs_tool': false, 'intent': 'chat', 'response': 'You\'re welcome!'}}]}}

User message: "{user_command}"

Respond with JSON only:"""

            prompt = unified_prompt
            rounds = 0

            while True:
                rounds += 1
                logger.info(f"--- LLM Call (Round {rounds}) ---")
                logger.info(f"Prompt sent to LLM:\n{prompt}")
                try:
                    raw_result = self.llm.chat(prompt, history=llm_history).strip()
                    logger.info(f"Unified LLM response: {raw_result}")
                    llm_history.append({"role": "assistant", "content": raw_result})

                    instructions = self._parse_instruction_sequence(raw_result)
                    logger.info(f"Parsed instruction count: {len(instructions)}")
                except (json.JSONDecodeError, ValueError):
                    logger.error(f"Failed to parse LLM JSON response: {raw_result}")
                    return await self._fallback_processing(user_command)
                except Exception as e:
                    logger.error(f"Error in unified processing: {e}")
                    raise e

                request_ids = extract_subgraph_requests(instructions)
                if not request_ids:
                    break

                logger.info(f"LLM requested subgraph for logical_id(s): {request_ids}")

                if rounds >= max_subgraph_rounds:
                    raise RuntimeError("Exceeded maximum subgraph request rounds")

                new_payloads: List[Dict[str, Any]] = []
                invalid_ids: List[int] = []
                repeated_ids: List[int] = []
                for logical_id in request_ids:
                    if logical_id in provided_subgraphs:
                        repeated_ids.append(logical_id)
                        continue
                    payload = self._build_subgraph_payload(logical_id)
                    if payload:
                        provided_subgraphs[logical_id] = payload
                        self._save_plan_snapshot(payload, label=f"subgraph_{logical_id}")
                        new_payloads.append(payload)
                    else:
                        invalid_ids.append(logical_id)

                if new_payloads:
                    prompt = (
                        "You requested additional task subgraphs. Here is the information you asked for:\n\n"
                        + format_subgraph_payloads(new_payloads)
                        + "\n\nUse this information together with the original GraphSummary to continue processing the user's command. "
                        "If you still require more detail, return another 'request_subgraph'. Otherwise, output the actionable instruction list in the required JSON format."
                    )
                    continue

                prompt = build_invalid_request_message(invalid_ids, repeated_ids)

            self.history = llm_history

        if instructions:
            instructions = [
                instr
                for instr in instructions
                if (instr.get("intent") or "").strip().lower() != "request_subgraph"
            ]

        if not instructions:
            raise ValueError("No instructions generated")

        # Remap any temporary identifiers before showing them to the user
        for instruction in instructions:
            params = instruction.get("parameters")
            if isinstance(params, dict):
                instruction["parameters"] = self._remap_instruction_ids(params)

        requires_confirmation = any(bool(instr.get("needs_tool")) for instr in instructions)

        if not confirmed and requires_confirmation:
            logger.info("Instructions require confirmation; deferring execution.")
            if self.conversation_id is not None:
                _PENDING_INSTRUCTIONS[self.conversation_id] = copy.deepcopy(instructions)
            self._id_aliases.clear()
            return self._format_confirmation_request(instructions)

        step_results: List[Dict[str, Any]] = []
        self._id_aliases.clear()
        for idx, instruction in enumerate(instructions):
            params = instruction.get("parameters")
            if isinstance(params, dict):
                instruction["parameters"] = self._remap_instruction_ids(params)
            needs_tool = bool(instruction.get("needs_tool"))
            if needs_tool:
                step_result = await self._handle_tool_request(instruction, user_command, confirmed)
            else:
                step_result = self._handle_chat_response(instruction, user_command)

            annotated_step = {
                **step_result,
                "step_index": idx,
                "instruction": instruction,
                "needs_tool": needs_tool,
            }
            step_results.append(annotated_step)

            if annotated_step.get("requires_confirmation"):
                logger.info("Step requires user confirmation; halting further execution.")
                break

        return self._format_final_response(step_results)

    def _strip_json_code_fence(self, raw: str) -> str:
        """Remove markdown code fences from an LLM JSON string if present."""
        if not raw:
            return raw

        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()
        return cleaned

    def _parse_instruction_sequence(self, raw_response: str) -> List[Dict[str, Any]]:
        """Parse the LLM JSON output into an ordered instruction list."""
        json_payload = self._strip_json_code_fence(raw_response)
        logger.debug(f"Parsing instruction payload: {json_payload}")

        parsed_obj = json.loads(json_payload)

        if isinstance(parsed_obj, dict):
            if "instructions" in parsed_obj:
                instructions_candidate = parsed_obj.get("instructions")
            else:
                instructions_candidate = [parsed_obj]
        else:
            instructions_candidate = parsed_obj

        if isinstance(instructions_candidate, dict):
            instructions_candidate = [instructions_candidate]

        if not isinstance(instructions_candidate, list):
            raise ValueError("LLM response did not contain a list of instructions")

        instructions: List[Dict[str, Any]] = []
        for idx, item in enumerate(instructions_candidate):
            if not isinstance(item, dict):
                logger.warning(f"Ignoring non-dict instruction at index {idx}: {item}")
                continue
            normalized = {**item}
            intent = normalized.get("intent")
            if isinstance(intent, str):
                normalized["intent"] = intent.strip()
            instructions.append(normalized)

        if not instructions:
            raise ValueError("No valid instructions parsed from LLM response")

        return instructions

    def _format_confirmation_request(self, instructions: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Build a response prompting the user to confirm pending instructions."""

        steps: List[Dict[str, Any]] = []
        for idx, instruction in enumerate(instructions):
            needs_tool = bool(instruction.get("needs_tool"))
            preview = instruction.get("initial_response") or instruction.get("response") or ""
            steps.append(
                {
                    "step_index": idx,
                    "instruction": instruction,
                    "needs_tool": needs_tool,
                    "response": preview,
                    "initial_response": instruction.get("initial_response"),
                }
            )

        default_visualization = {"type": "none", "data": {}, "config": {}}
        message = "已生成以下操作步骤，请确认后继续。"

        return {
            "response": message,
            "initial_response": message,
            "execution_feedback": None,
            "intent": "confirmation_required",
            "visualization": default_visualization,
            "action_result": {
                "success": True,
                "requires_confirmation": True,
                "instructions": instructions,
            },
            "success": True,
            "steps": steps,
        }

    def _format_final_response(self, steps: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Aggregate step results into the final response contract."""
        default_visualization = {"type": "none", "data": {}, "config": {}}

        if not steps:
            return {
                "response": "抱歉，我未能解析出有效的执行步骤。请再描述一次。",
                "initial_response": "",
                "execution_feedback": None,
                "intent": "unknown",
                "visualization": default_visualization,
                "action_result": {"success": False, "message": "no_steps"},
                "success": False,
                "steps": []
            }

        # Combine textual responses
        response_parts = []
        for step in steps:
            text = step.get("response") or step.get("message")
            if text:
                response_parts.append(text.strip())
        combined_response = "\n\n".join(part for part in response_parts if part)
        if not combined_response:
            combined_response = steps[-1].get("response", "")

        initial_response = next(
            (step.get("initial_response") for step in steps if step.get("initial_response")),
            steps[0].get("initial_response", "")
        )

        execution_feedback_parts = [
            step.get("execution_feedback") for step in steps if step.get("execution_feedback")
        ]
        execution_feedback = "\n\n".join(execution_feedback_parts) if execution_feedback_parts else None

        success = all(step.get("success", True) for step in steps)
        if any(step.get("requires_confirmation") for step in steps):
            success = False
        intents = [step.get("intent") for step in steps if step.get("intent")]
        primary_intent = intents[-1] if intents else "unknown"

        visualization = default_visualization
        for step in reversed(steps):
            viz = step.get("visualization")
            if viz and viz.get("type") and viz.get("type") != "none":
                visualization = viz
                break
        if visualization is default_visualization:
            # Fallback to the last available visualization even if 'none'
            for step in reversed(steps):
                viz = step.get("visualization")
                if viz:
                    visualization = viz
                    break

        action_result = None
        for step in reversed(steps):
            act = step.get("action_result")
            if not act:
                continue
            if step.get("needs_tool") or act.get("should_execute") or act.get("type") not in (None, "chat"):
                action_result = act
                break
        if not action_result:
            for step in reversed(steps):
                act = step.get("action_result")
                if act:
                    action_result = act
                    break
        if not action_result:
            action_result = {"success": success}

        return {
            "response": combined_response,
            "initial_response": initial_response,
            "execution_feedback": execution_feedback,
            "intent": primary_intent,
            "visualization": visualization,
            "action_result": action_result,
            "success": success,
            "steps": steps,
        }
    
    async def _handle_tool_request(self, parsed_response: Dict, user_command: str, confirmed: bool = False) -> Dict[str, Any]:
        """处理需要工具调用的请求"""
        try:
            # 转换字符串intent为IntentType枚举
            intent_str = parsed_response.get("intent", "help")
            try:
                intent = IntentType(intent_str)
            except ValueError:
                logger.warning(f"Unknown intent: {intent_str}, using HELP")
                intent = IntentType.HELP
            
            params = parsed_response.get("parameters", {})
            if params:
                params = self._remap_instruction_ids(params)
                parsed_response["parameters"] = params
            initial_response = parsed_response.get("initial_response", "Let me help you with that...")

            logger.info(f"Tool request - Intent: {intent.value}, Params: {params}")

            # 执行工具动作
            action_result = await self._execute_action(intent, params, confirmed)

            # 如果需要确认，则提前返回
            if action_result.get("requires_confirmation"):
                return action_result

            # Commit staged plan updates once per tool action
            if self.plan_session and self.plan_session.has_pending_changes():
                self.plan_session.commit()
                self._refresh_action_result_from_session(action_result)
                self._invalidate_plan_graph_cache()

            if self.plan_session and intent in {
                IntentType.CREATE_TASK,
                IntentType.UPDATE_TASK,
                IntentType.UPDATE_TASK_INSTRUCTION,
                IntentType.MOVE_TASK,
                IntentType.DELETE_TASK,
            }:
                task_id = (
                    action_result.get("task", {}).get("id")
                    if isinstance(action_result.get("task"), dict)
                    else action_result.get("task_id")
                )
                if task_id:
                    refreshed = self.plan_session.get_task(task_id)
                    if refreshed:
                        action_result["task"] = refreshed
                action_result["tasks"] = self.plan_session.list_tasks()
                if self.plan_id:
                    action_result["task_tree"] = self._get_plan_task_tree(self.plan_id)
                self._invalidate_plan_graph_cache()

            # 生成执行后的反馈
            execution_feedback = self._generate_execution_feedback(intent, action_result)
            
            # 生成可视化
            visualization = self._generate_visualization(intent, action_result)
            
            return {
                "response": f"{initial_response}\n\n{execution_feedback}" if execution_feedback else initial_response,
                "initial_response": initial_response,
                "execution_feedback": execution_feedback,
                "intent": intent.value,
                "visualization": {
                    "type": visualization["type"] if isinstance(visualization["type"], str) else visualization["type"].value,
                    "data": visualization["data"],
                    "config": visualization.get("config", {})
                },
                "action_result": action_result,
                "success": action_result.get("success", True)
            }
            
        except Exception as e:
            logger.error(f"Error handling tool request: {e}")
            return {
                "response": f"抱歉，执行操作时出现错误：{str(e)}",
                "initial_response": "Let me try to help you...",
                "execution_feedback": f"Error: {str(e)}",
                "intent": "error",
                "visualization": {"type": "none", "data": {}, "config": {}},
                "action_result": {"success": False, "message": str(e)},
                "success": False
            }
    
    def _handle_chat_response(self, parsed_response: Dict, user_command: str) -> Dict[str, Any]:
        """处理普通对话响应"""
        chat_response = parsed_response.get("response", "I'm here to help!")
        
        return {
            "response": chat_response,
            "initial_response": chat_response,  # 对于聊天，immediate response就是完整回复
            "execution_feedback": None,   # 聊天不需要执行反馈
            "intent": "chat",
            "visualization": {"type": "none", "data": {}, "config": {}},
            "action_result": {
                "success": True,
                "type": "chat", 
                "is_casual_chat": True
            },
            "success": True
        }
    
    async def _fallback_processing(self, user_command: str) -> Dict[str, Any]:
        """降级处理：当JSON解析失败时的备用方案"""
        # 使用原来的简单关键词检测作为备用
        if self._is_casual_chat_simple(user_command):
            chat_instruction = {
                "needs_tool": False,
                "intent": "chat",
                "response": "I understand you're chatting with me. How can I help you today?"
            }
            chat_step = self._handle_chat_response(chat_instruction, user_command)
            chat_step.update({
                "step_index": 0,
                "instruction": chat_instruction,
                "needs_tool": False,
            })
            return self._format_final_response([chat_step])

        help_instruction = {
            "needs_tool": True,
            "intent": "help",
            "parameters": {},
            "initial_response": "I'm not sure what you're asking for. Let me show you what I can help with..."
        }
        help_step = await self._handle_tool_request(help_instruction, user_command, confirmed=False)
        help_step.update({
            "step_index": 0,
            "instruction": help_instruction,
            "needs_tool": True,
        })
        return self._format_final_response([help_step])
    
    def _is_casual_chat_simple(self, command: str) -> bool:
        """简单的关键词检测（用作备用方案）"""
        casual_patterns = ['hello', 'hi', 'thanks', 'thank', 'good', 'how are', '你好', '谢谢', '好的', '嗯']
        command_lower = command.lower()
        return any(pattern in command_lower for pattern in casual_patterns) or len(command.strip()) < 6

    def _refresh_action_result_from_session(self, action_result: Dict[str, Any]) -> None:
        """Ensure action results reflect the latest session state (logical + db IDs)."""

        if not action_result or not self.plan_session:
            return

        task_entry = action_result.get("task")
        if isinstance(task_entry, dict):
            logical_id = task_entry.get("id")
            if logical_id is not None:
                refreshed = self.plan_session.get_task(logical_id)
                if refreshed:
                    action_result["task"] = refreshed
                    action_result["task_id"] = logical_id

        if "tasks" in action_result:
            action_result["tasks"] = self.plan_session.list_tasks()

        plan_id = action_result.get("plan_id", self.plan_id)
        if plan_id and "task_tree" in action_result:
            action_result["task_tree"] = self._get_plan_task_tree(plan_id)

    def _invalidate_plan_graph_cache(self) -> None:
        self._graph_summary_cache = None
        self._subgraph_cache.clear()

    def _remap_instruction_ids(self, params: Dict[str, Any]) -> Dict[str, Any]:
        if not self._id_aliases or not params:
            return params

        remapped = dict(params)

        def try_convert(val):
            try:
                return int(val)
            except (TypeError, ValueError):
                return val

        keys = ["task_id", "parent_id", "new_parent_id", "from_task_id", "to_task_id"]
        for key in keys:
            if key in remapped:
                converted = try_convert(remapped[key])
                if isinstance(converted, int) and converted in self._id_aliases:
                    remapped[key] = self._id_aliases[converted]

        for key, value in list(remapped.items()):
            if key.endswith("_ids") and isinstance(value, (list, tuple)):
                updated = []
                for item in value:
                    converted = try_convert(item)
                    if isinstance(converted, int) and converted in self._id_aliases:
                        updated.append(self._id_aliases[converted])
                    else:
                        updated.append(item)
                remapped[key] = updated

        return remapped

    def _generate_execution_feedback(self, intent: IntentType, action_result: Dict) -> str:
        """生成工具执行后的反馈"""
        
        if not action_result.get("success"):
            return f"❌ {action_result.get('message', 'Operation failed')}"
        
        if intent == IntentType.CREATE_PLAN:
            return "✅ Plan generation started. You will see tasks appearing in real-time in the visualization panel."
        
        elif intent == IntentType.UPDATE_TASK:
            task_id = action_result.get("task_id")
            return f"✅ Task {task_id} has been successfully updated."

        elif intent == IntentType.UPDATE_TASK_INSTRUCTION:
            task_id = action_result.get("task_id")
            return f"✅ Instruction for task {task_id} has been successfully updated."

        elif intent == IntentType.LIST_PLANS:
            plans = action_result.get("plans", [])
            return f"✅ Found {len(plans)} plan(s). You can see the details in the visualization panel."
        
        elif intent == IntentType.EXECUTE_PLAN:
            pending_count = action_result.get("pending_count", 0)
            total_count = action_result.get("total_count", 0)
            if pending_count > 0:
                return f"✅ Started executing {pending_count} tasks out of {total_count} total. You can monitor the progress in real-time."
            else:
                return f"✅ All {total_count} tasks are already completed or no tasks found to execute."
        
        elif intent == IntentType.QUERY_STATUS:
            if action_result.get("type") == "plan":
                total = action_result.get("total_tasks", 0)
                return f"✅ Plan status retrieved. Total {total} tasks. Check the status dashboard for detailed breakdown."
            else:
                return "✅ Status information retrieved successfully."
        
        elif intent == IntentType.SHOW_TASKS:
            tasks = action_result.get("tasks", [])
            return f"✅ Displaying {len(tasks)} tasks in the task tree view."
        
        elif intent == IntentType.RERUN_TASK:
            return "✅ Task has been reset and queued for re-execution."

        elif intent == IntentType.DELETE_PLAN:
            return "✅ Plan has been successfully deleted."

        elif intent == IntentType.DELETE_TASK:
            deleted = action_result.get("deleted_task") or {}
            task_label = deleted.get("name") or f"Task {action_result.get('task_id')}"
            return f"✅ {task_label} and any subtasks have been removed."

        elif intent == IntentType.MOVE_TASK:
            target = action_result.get("new_parent_id")
            if target is None:
                return "✅ Task moved to the root level."
            return f"✅ Task reparented under {target}."

        elif intent == IntentType.HELP:
            return "You can click on any command above to quickly execute it, or type your request naturally."

        else:
            return "✅ Operation completed successfully."
    
    async def _execute_action(self, intent: IntentType, params: Dict, confirmed: bool = False) -> Dict[str, Any]:
        """Execute specific backend actions based on the identified intent"""
        
        try:
            if intent == IntentType.CREATE_PLAN:
                return self._create_plan(params)
            elif intent == IntentType.CREATE_TASK:
                return self._create_task(params)
            elif intent == IntentType.UPDATE_TASK:
                return await self._update_task(params, confirmed)
            elif intent == IntentType.UPDATE_TASK_INSTRUCTION:
                return await self._update_task_instruction(params, confirmed)
            elif intent == IntentType.MOVE_TASK:
                return await self._move_task(params)
            elif intent == IntentType.LIST_PLANS:
                return self._list_plans()
            elif intent == IntentType.EXECUTE_PLAN:
                return self._execute_plan(params)
            elif intent == IntentType.QUERY_STATUS:
                return self._query_status(params)
            elif intent == IntentType.SHOW_TASKS:
                return self._show_tasks(params)
            elif intent == IntentType.RERUN_TASK:
                return self._rerun_task(params)
            elif intent == IntentType.DELETE_PLAN:
                return self._delete_plan(params)
            elif intent == IntentType.DELETE_TASK:
                return await self._delete_task(params)
            elif intent == IntentType.HELP:
                return self._show_help()
            elif intent == IntentType.CHAT:
                return {"success": True, "message": "Chat handled", "type": "chat"}
            else:
                return {"success": False, "message": "I couldn't understand your request. Try rephrasing or type 'help' to see available commands."}
        except Exception as e:
            logger.error(f"Action execution failed: {e}")
            return {"success": False, "message": f"Error executing operation: {str(e)}"}

    def _create_task(self, params: Dict) -> Dict[str, Any]:
        """Creates a new task in a plan."""
        task_name = params.get("task_name")
        parent_id = params.get("parent_id")
        plan_id = params.get("plan_id", self.plan_id)

        if not task_name:
            return {"success": False, "message": "Please provide a name for the task."}
        
        # If plan_id is missing, try to infer it from the parent task
        if not plan_id and parent_id:
            try:
                inferred_plan_id = default_repo.get_plan_for_task(int(parent_id))
                if inferred_plan_id:
                    plan_id = inferred_plan_id
                else:
                    return {"success": False, "message": f"Could not find a plan associated with parent task {parent_id}."}
            except Exception as e:
                logger.error(f"Error inferring plan_id from parent_id {parent_id}: {e}")
                return {"success": False, "message": f"Could not find the parent task {parent_id}."}

        if not plan_id:
            return {"success": False, "message": "I don't know which plan to add this task to. Please specify a plan."}

        try:
            plan_id = int(plan_id)

            if parent_id is not None:
                parent_id = int(parent_id)

            if self.plan_session and self.plan_session.plan_id == plan_id:
                new_task = self.plan_session.create_task(
                    name=task_name,
                    parent_id=parent_id,
                    task_type=params.get("task_type", "atomic"),
                    priority=params.get("priority"),
                )
                task_id = new_task["id"]
            else:
                task_id = default_repo.create_task(
                    name=task_name,
                    parent_id=parent_id,
                    task_type=params.get("task_type", "atomic"),
                    priority=params.get("priority"),
                )
                default_repo.link_task_to_plan(plan_id, task_id)
                new_task = default_repo.get_task_info(task_id)

            return {
                "success": True,
                "message": f"Successfully created task '{task_name}' (ID: {task_id}) in plan {plan_id}.",
                "task": new_task,
                "plan_id": plan_id
            }
        except Exception as e:
            logger.error(f"Failed to create task: {e}")
            return {"success": False, "message": f"Failed to create task: {str(e)}"}
    
    def _execute_update_task(self, params: Dict) -> Dict[str, Any]:
        """Performs the actual task update after confirmation."""
        task_id = params.get("task_id")
        updates = params.get("updates", {})
        try:
            if self.plan_session:
                success = self.plan_session.update_task(task_id=task_id, **updates)
            else:
                success = default_repo.update_task(task_id=task_id, **updates)
            if not success:
                return {"success": False, "message": f"Task {task_id} not found or failed to update."}
            plan_id = self.plan_id or (self.plan_session.plan_id if self.plan_session else default_repo.get_plan_for_task(task_id))
            return {"success": True, "task_id": task_id, "plan_id": plan_id}
        except Exception as e:
            logger.error(f"Failed to execute update for task {task_id}: {e}")
            return {"success": False, "message": str(e)}

    async def _update_task(self, params: Dict, confirmed: bool = False) -> Dict[str, Any]:
        """Handles the intent to update a task, potentially requiring confirmation."""
        task_id = params.get("task_id")
        task_name = params.get("task_name")

        updates = {}
        if "name" in params: updates["name"] = params["name"]
        if "status" in params: updates["status"] = params["status"]
        if "priority" in params: updates["priority"] = params["priority"]
        if "task_type" in params: updates["task_type"] = params["task_type"]

        if not updates:
            return {"success": False, "message": "Please provide at least one field to update (e.g., name, status)."}
        if not task_id and not task_name:
            return {"success": False, "message": "Please provide a task ID or a task name to update."}

        if not task_id:
            find_result = await self._find_task_id_by_name(task_name)
            if not find_result["success"]:
                return find_result
            
            matched_task = find_result["task"]
            task_id = matched_task["id"]
            
            if not confirmed:
                return {
                    "requires_confirmation": True,
                    "response": f"Did you mean to update the task '{matched_task['name']}' (ID: {task_id})? Please confirm.",
                    "intent": "confirmation_required"
                }

        return self._execute_update_task({"task_id": int(task_id), "updates": updates})

    def _execute_update_task_instruction(self, params: Dict) -> Dict[str, Any]:
        """Performs the actual task instruction update after confirmation."""
        task_id = params.get("task_id")
        user_instruction = params.get("instruction")
        try:
            # 1. Fetch the existing instruction
            existing_instruction = (
                self.plan_session.get_or_fetch_instruction(task_id)
                if self.plan_session
                else default_repo.get_task_input(task_id)
            )

            # 2. Create a new prompt for the LLM to merge the instructions
            merge_prompt = f"""You are an AI assistant tasked with refining task instructions.
            
Original instruction:
---
{existing_instruction}
---

The user wants to update it with the following request:
---
{user_instruction}
---

Please synthesize these two pieces of information into a new, clear, and comprehensive instruction for the task. The new instruction should incorporate the user's update while maintaining the core of the original instruction.

Respond with only the new, revised instruction text."""

            # 3. Call the LLM to get the merged instruction
            new_instruction = self.llm.chat(merge_prompt)

            # 4. Update the task with the new instruction
            if self.plan_session:
                self.plan_session.set_instruction(task_id, new_instruction)
                plan_id = self.plan_session.plan_id
            else:
                default_repo.upsert_task_input(task_id, new_instruction)
                plan_id = self.plan_id or default_repo.get_plan_for_task(task_id)
            return {"success": True, "task_id": task_id, "plan_id": plan_id}
        except Exception as e:
            logger.error(f"Failed to execute instruction update for task {task_id}: {e}")
            return {"success": False, "message": str(e)}

    async def _update_task_instruction(self, params: Dict, confirmed: bool = False) -> Dict[str, Any]:
        """Handles the intent to update a task's instruction, potentially requiring confirmation."""
        task_id = params.get("task_id")
        task_name = params.get("task_name")
        instruction = params.get("instruction")

        if not instruction:
            return {"success": False, "message": "Please provide the new instruction for the task."}
        if not task_id and not task_name:
            return {"success": False, "message": "Please provide a task ID or a task name to update."}

        if not task_id:
            find_result = await self._find_task_id_by_name(task_name)
            if not find_result["success"]:
                return find_result
            
            matched_task = find_result["task"]
            task_id = matched_task["id"]
            
            if not confirmed:
                return {
                    "requires_confirmation": True,
                    "response": f"Did you mean to update the instruction for '{matched_task['name']}' (ID: {task_id})? Please confirm.",
                    "intent": "confirmation_required"
                }

        return self._execute_update_task_instruction({"task_id": int(task_id), "instruction": instruction})

    async def _move_task(self, params: Dict) -> Dict[str, Any]:
        """Handles moving a task under a new parent within the plan graph."""

        task_id = params.get("task_id")
        task_name = params.get("task_name")
        new_parent_id = params.get("new_parent_id")
        new_parent_name = params.get("new_parent_name")

        if task_id is None and not task_name:
            return {"success": False, "message": "Please provide the task_id or task_name to move."}

        if self.plan_session is None:
            return {"success": False, "message": "Plan session is not initialized."}

        try:
            if task_id is None:
                find_result = await self._find_task_id_by_name(task_name)
                if not find_result.get("success"):
                    return find_result
                task_id = find_result["task"]["id"]

            task_id = int(task_id)
            parent_id_int: Optional[int]
            if (new_parent_id in (None, "null", "None", "root") and not new_parent_name):
                parent_id_int = None
            else:
                if new_parent_id not in (None, "null", "None", "root"):
                    parent_id_int = int(new_parent_id)
                elif new_parent_name:
                    find_parent = await self._find_task_id_by_name(new_parent_name)
                    if not find_parent.get("success"):
                        return find_parent
                    parent_id_int = find_parent["task"]["id"]
                else:
                    parent_id_int = None

            self.plan_session.move_task(task_id, parent_id_int)
            task_info = self.plan_session.get_task(task_id)
            return {
                "success": True,
                "task_id": task_id,
                "plan_id": self.plan_id,
                "new_parent_id": parent_id_int,
                "task": task_info,
                "message": (
                    f"Moved task {task_id} under parent {parent_id_int}" if parent_id_int is not None
                    else f"Moved task {task_id} to root"
                ),
                "tasks": self.plan_session.list_tasks(),
                "task_tree": self.plan_session.build_task_tree(),
            }
        except ValueError as e:
            return {"success": False, "message": str(e)}
        except Exception as e:
            logger.error(f"Failed to move task: {e}")
            return {"success": False, "message": f"Failed to move task: {str(e)}"}

    def _create_plan(self, params: Dict) -> Dict[str, Any]:
        """为流式创建计划做准备"""
        goal = params.get("goal", "")
        if not goal:
            return {"success": False, "message": "Please provide a goal for the plan."}

        # 不再启动后台任务，而是返回一个指令给前端
        return {
            "success": True,
            "message": "Ready to generate plan. Frontend should now initiate a stream.",
            "action": "stream",  # 指示前端需要发起流式请求
            "stream_endpoint": "/chat/plans/propose-stream", # 告诉前端调用哪个端点
            "stream_payload": {"goal": goal} # 告诉前端用什么参数
        }

    def _list_plans(self) -> Dict[str, Any]:
        """列出所有计划"""
        try:
            plans = default_repo.list_plans()
            if not plans:
                return {
                    "success": True,
                    "plans": [],
                    "message": "当前没有任何计划"
                }
            
            # 为每个计划获取任务统计
            for plan in plans:
                tasks = self._get_plan_tasks_cached(plan["id"])
                plan["task_count"] = len(tasks)
                plan["completed_count"] = len([t for t in tasks if t.get("status") == "done"])
                plan["progress"] = plan["completed_count"] / plan["task_count"] if plan["task_count"] > 0 else 0
            
            return {
                "success": True,
                "plans": plans,
                "message": f"找到 {len(plans)} 个计划"
            }
        except Exception as e:
            return {"success": False, "message": f"获取计划列表失败：{str(e)}"}
    
    def _execute_plan(self, params: Dict) -> Dict[str, Any]:
        """执行计划"""
        plan_id = params.get("plan_id")
        if not plan_id:
            return {"success": False, "message": "请提供计划ID"}
        
        try:
            # 首先获取计划任务列表，用于返回给前端显示
            plan_id_int = int(plan_id)
            tasks = self._get_plan_tasks_cached(plan_id_int)
            
            # 过滤出待执行的任务
            pending_tasks = [t for t in tasks if t.get("status") == "pending"]
            
            if not pending_tasks:
                return {
                    "success": True,
                    "message": "没有待执行的任务",
                    "tasks": tasks,
                    "plan_id": plan_id_int
                }
            
            # 标记需要执行，实际执行将在路由层处理
            return {
                "success": True,
                "message": f"准备执行 {len(pending_tasks)} 个任务（共 {len(tasks)} 个）",
                "tasks": tasks,
                "pending_count": len(pending_tasks),
                "total_count": len(tasks),
                "plan_id": plan_id_int,
                "should_execute": True  # 标记需要执行
            }
        except Exception as e:
            logger.error(f"Failed to prepare plan execution: {e}")
            return {"success": False, "message": f"执行计划失败：{str(e)}"}
    
    def _query_status(self, params: Dict) -> Dict[str, Any]:
        """查询状态"""
        plan_id = params.get("plan_id")
        task_id = params.get("task_id")

        # If no ID is provided, fall back to the current plan_id from context
        if not plan_id and not task_id:
            plan_id = self.plan_id

        try:
            if task_id:
                # Validate that task_id is an integer
                try:
                    task_id = int(task_id)
                except (ValueError, TypeError):
                    # If task_id is not a valid int, fall back to plan_id
                    logger.warning(f"Invalid task_id '{task_id}', falling back to plan context.")
                    plan_id = self.plan_id
                    if not plan_id:
                        return {"success": False, "message": "Please provide a valid task ID or specify a plan."}
                else:
                    cached_task = (
                        self.plan_session.get_task(task_id)
                        if self.plan_session
                        else None
                    )
                    task = cached_task or default_repo.get_task_info(task_id)
                    if not task:
                        return {"success": False, "message": f"未找到任务 {task_id}"}
                    
                    # To show the tree, we need all tasks of the plan this task belongs to
                    containing_plan_id = default_repo.get_plan_for_task(task_id)
                    tasks = self._get_plan_tasks_cached(containing_plan_id)
                    return {
                        "success": True,
                        "type": "task",
                        "plan_id": containing_plan_id,
                        "tasks": tasks,
                        "message": f"任务 {task['name']} 状态：{task['status']}"
                    }
            if plan_id:
                plan_id_int = int(plan_id)
                tasks = self._get_plan_tasks_cached(plan_id_int)
                status_count = {}
                for task in tasks:
                    status = task.get("status", "unknown")
                    status_count[status] = status_count.get(status, 0) + 1
                
                return {
                    "success": True,
                    "type": "plan",
                    "plan_id": plan_id_int,
                    "total_tasks": len(tasks),
                    "status_count": status_count,
                    "tasks": tasks,
                    "message": f"计划 {plan_id_int} 共有 {len(tasks)} 个任务"
                }
            else:
                return {"success": False, "message": "I'm not sure which plan you're referring to. Please select a plan to see its status."}
        except Exception as e:
            return {"success": False, "message": f"查询状态失败：{str(e)}"}
    
    def _show_tasks(self, params: Dict) -> Dict[str, Any]:
        """显示任务列表"""
        plan_id = params.get("plan_id", self.plan_id)
        if not plan_id:
            return {"success": False, "message": "请提供计划ID"}
        
        try:
            plan_id_int = int(plan_id)
            tasks = self._get_plan_tasks_cached(plan_id_int)
            task_tree = self._get_plan_task_tree(plan_id_int)
            
            return {
                "success": True,
                "plan_id": plan_id_int,
                "tasks": tasks,
                "task_tree": task_tree,
                "message": f"计划 {plan_id_int} 包含 {len(tasks)} 个任务"
            }
        except Exception as e:
            return {"success": False, "message": f"获取任务失败：{str(e)}"}
    
    def _build_task_tree(self, tasks: List[Dict]) -> List[Dict]:
        """构建任务树结构"""
        task_map = {task["id"]: {**task, "children": []} for task in tasks}
        roots = []

        for task in tasks:
            parent_id = task.get("parent_id")
            if parent_id and parent_id in task_map:
                task_map[parent_id]["children"].append(task_map[task["id"]])
            else:
                roots.append(task_map[task["id"]])

        def sort_children(node: Dict[str, Any]) -> None:
            if node.get("children"):
                node["children"].sort(key=lambda c: c["id"])
                for child in node["children"]:
                    sort_children(child)

        for root in roots:
            sort_children(root)

        roots.sort(key=lambda c: c["id"])
        return roots

    def _get_plan_title(self) -> Optional[str]:
        if self.plan_session and self.plan_session.plan:
            return self.plan_session.plan.get("title")
        return None

    def _collect_task_snapshot(self, logical_id: int, max_depth: int = 0) -> Optional[Dict[str, Any]]:
        if not self.plan_session:
            return None

        base = self.plan_session.get_task(logical_id)
        if not base:
            return None

        snapshot = {
            "id": base.get("id"),
            "name": base.get("name"),
            "status": base.get("status"),
        }

        child_ids = self.plan_session.get_child_ids(logical_id)
        
        children: List[Dict[str, Any]] = []
        if child_ids:
            snapshot["has_more_children"] = True
            if max_depth > 0:
                for child_id in child_ids:
                    child_snapshot = self._collect_task_snapshot(child_id, max_depth=max_depth - 1)
                    if child_snapshot:
                        children.append(child_snapshot)
        
        if children:
            snapshot["children"] = children

        return snapshot

    def _build_graph_summary_payload(self) -> Optional[Dict[str, Any]]:
        if not self.plan_session:
            return None

        if self._graph_summary_cache is not None:
            return copy.deepcopy(self._graph_summary_cache)

        root_ids = self.plan_session.get_root_task_ids()
        nodes: List[Dict[str, Any]] = []
        for logical_id in root_ids:
            snapshot = self._collect_task_snapshot(logical_id, max_depth=1)
            if snapshot:
                nodes.append(snapshot)

        payload = {
            "type": "GraphSummary",
            "plan_id": self.plan_id,
            "plan_title": self._get_plan_title(),
            "generated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "layer_depth": 0,
            "root_task_ids": list(root_ids),
            "nodes": nodes,
        }

        cached = copy.deepcopy(payload)
        self._graph_summary_cache = cached
        return copy.deepcopy(cached)

    def _build_subgraph_payload(self, logical_id: int) -> Optional[Dict[str, Any]]:
        if not self.plan_session:
            return None

        if logical_id in self._subgraph_cache:
            return copy.deepcopy(self._subgraph_cache[logical_id])

        snapshot = self._collect_task_snapshot(logical_id, max_depth=1)
        if not snapshot:
            return None

        payload = {
            "type": "SubgraphDetail",
            "plan_id": self.plan_id,
            "plan_title": self._get_plan_title(),
            "generated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "root_id": logical_id,
            "node": snapshot,
        }

        cached = copy.deepcopy(payload)
        self._subgraph_cache[logical_id] = cached
        return copy.deepcopy(cached)

    def _summarize_plan_graph(self, max_tasks: int = 60) -> str:
        """Build a human-readable summary of the current plan graph for LLM prompts."""

        if not self.plan_id or not self.plan_session:
            return ""

        tree = self.plan_session.build_task_tree()
        summary_lines: List[str] = []
        tasks_rendered = 0

        context_cache: Dict[int, List[Dict[str, Any]]] = {}
        output_cache: Dict[int, Optional[str]] = {}

        def get_instruction_preview(logical_id: int, db_id: Optional[int]) -> Optional[str]:
            if self.plan_session:
                instruction = self.plan_session.get_or_fetch_instruction(logical_id)
            else:
                lookup_id = db_id if db_id is not None else logical_id
                instruction = default_repo.get_task_input(lookup_id)
            if not instruction:
                return None
            snippet = instruction.strip().replace("\n", " ")
            if len(snippet) > 200:
                snippet = snippet[:197] + "…"
            return snippet

        def get_context_previews(logical_id: int, db_id: Optional[int]) -> List[str]:
            cache_key = logical_id if self.plan_session else (db_id if db_id is not None else logical_id)
            if cache_key not in context_cache:
                lookup_id = db_id if db_id is not None else logical_id
                try:
                    contexts = default_repo.list_task_contexts(lookup_id)
                except Exception:
                    contexts = []
                context_cache[cache_key] = contexts or []

            previews: List[str] = []
            contexts = context_cache.get(cache_key, [])
            if not contexts:
                return previews

            limit = 2
            for index, payload in enumerate(contexts[:limit]):
                label = payload.get("label") or "latest"
                combined = (payload.get("combined") or "").strip()
                if combined:
                    previews.append(f"Context [{label}]: {combined}")

                sections = payload.get("sections") or []
                if isinstance(sections, list) and sections:
                    first_section = sections[0]
                    if isinstance(first_section, dict):
                        section_title = first_section.get("title") or "section"
                        section_content = (first_section.get("content") or "").strip()
                        if section_content:
                            previews.append(f"  Section [{section_title}]: {section_content}")

                meta = payload.get("meta")
                if isinstance(meta, dict) and meta:
                    meta_summary = json.dumps(meta, ensure_ascii=False)
                    previews.append(f"  Meta: {meta_summary}")
                elif meta:
                    previews.append(f"  Meta: {meta}")

            remaining = max(0, len(contexts) - limit)
            if remaining > 0:
                previews.append(f"  (Additional {remaining} context snapshot(s) omitted)")

            return previews

        def get_output_preview(logical_id: int, db_id: Optional[int]) -> Optional[str]:
            cache_key = logical_id if self.plan_session else (db_id if db_id is not None else logical_id)
            if cache_key not in output_cache:
                lookup_id = db_id if db_id is not None else logical_id
                try:
                    output_cache[cache_key] = default_repo.get_task_output_content(lookup_id)
                except Exception:
                    output_cache[cache_key] = None
            content = output_cache.get(cache_key)
            if not content:
                return None
            snippet = content.strip().replace("\n", " ")
            if not snippet:
                return None
            if len(snippet) > 200:
                snippet = snippet[:197] + "…"
            return f"Output: {snippet}"

        def render(nodes: List[Dict[str, Any]], prefix: str = "") -> None:
            nonlocal summary_lines, tasks_rendered
            ordered = sorted(nodes, key=lambda n: (n.get("depth", 0) or 0, n.get("id", 0)))
            total = len(ordered)
            for index, node in enumerate(ordered):
                is_last = index == total - 1
                if tasks_rendered >= max_tasks:
                    summary_lines.append(f"{prefix}└─ …")
                    return

                connector = "└─" if is_last else "├─"
                status = node.get("status")
                priority = node.get("priority")
                parent_id = node.get("parent_id")
                children_nodes = node.get("children") or []
                if isinstance(children_nodes, list):
                    child_ids = [child.get("id") if isinstance(child, dict) else child for child in children_nodes]
                else:
                    child_ids = []

                meta_parts = []
                if status:
                    meta_parts.append(f"status: {status}")
                if priority is not None:
                    meta_parts.append(f"priority: {priority}")
                meta_parts.append(f"parent: {parent_id if parent_id is not None else 'None'}")
                meta_parts.append(f"children: {child_ids if child_ids else '[]'}")
                meta_suffix = f" ({'; '.join(meta_parts)})"
                line = f"{prefix}{connector} [#{node['id']}] {node.get('name', 'Untitled')}{meta_suffix}"
                summary_lines.append(line)
                tasks_rendered += 1

                child_prefix = prefix + ("   " if is_last else "│  ")

                logical_id = node["id"]
                db_id = node.get("db_id")

                instruction_snippet = get_instruction_preview(logical_id, db_id)
                if instruction_snippet:
                    summary_lines.append(f"{child_prefix}Instruction: {instruction_snippet}")

                context_snippets = get_context_previews(logical_id, db_id)
                for snippet in context_snippets:
                    summary_lines.append(f"{child_prefix}{snippet}")

                output_snippet = get_output_preview(logical_id, db_id)
                if output_snippet:
                    summary_lines.append(f"{child_prefix}{output_snippet}")

                children = node.get("children") or []
                if isinstance(children, list):
                    children = sorted(children, key=lambda c: c.get("id", 0) if isinstance(c, dict) else c)
                if children and tasks_rendered < max_tasks:
                    next_prefix = prefix + ("   " if is_last else "│  ")
                    render(children, next_prefix)
                if tasks_rendered >= max_tasks:
                    return

        render(tree)
        if not summary_lines:
            summary_lines.append("(Plan has no tasks yet.)")
        return "\n".join(summary_lines)

    def _build_plan_graph_context(self) -> str:
        """Return formatted plan graph context for inclusion in prompts."""

        try:
            payload = self._build_graph_summary_payload()
            if not payload:
                return ""
            plan_title = ""
            title = self._get_plan_title()
            if title:
                plan_title = f"Plan title: {title}\n"
            summary_text = json.dumps(payload, ensure_ascii=False, indent=2)
            full_context = (
                f"{plan_title}GraphSummary (current layer + direct children):\n"
                f"{summary_text}\n"
            )
            self._save_plan_snapshot(payload, label="graph_summary")
            return full_context
        except Exception as exc:
            logger.warning(f"Failed to summarize plan graph: {exc}")
            return ""

    def _save_plan_snapshot(self, payload: Any, label: str = "plan") -> None:
        if payload in (None, ""):
            return
        try:
            os.makedirs(SNAPSHOT_DIR, exist_ok=True)
            plan_identifier = self.plan_id if self.plan_id is not None else "unknown"
            timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
            safe_label = label.strip().replace(" ", "_") or "snapshot"
            safe_label = "".join(
                ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in safe_label
            )
            content: str
            extension = "txt"
            if isinstance(payload, str):
                content = payload
            else:
                content = json.dumps(payload, ensure_ascii=False, indent=2)
                extension = "json"
            filename = f"plan_{plan_identifier}_{timestamp}_{safe_label}.{extension}"
            filepath = os.path.join(SNAPSHOT_DIR, filename)
            with open(filepath, "w", encoding="utf-8") as handle:
                handle.write(content)
        except Exception:
            logger.warning("Failed to persist plan snapshot", exc_info=True)

    def _get_plan_tasks_cached(self, plan_id: int) -> List[Dict[str, Any]]:
        if self.plan_session and self.plan_session.plan_id == plan_id:
            return self.plan_session.list_tasks()
        return default_repo.get_plan_tasks(plan_id)

    def _get_plan_task_tree(self, plan_id: int) -> List[Dict[str, Any]]:
        if self.plan_session and self.plan_session.plan_id == plan_id:
            return self.plan_session.build_task_tree()
        tasks = default_repo.get_plan_tasks(plan_id)
        return self._build_task_tree(tasks)
    
    def _rerun_task(self, params: Dict) -> Dict[str, Any]:
        """重新执行任务"""
        task_id = params.get("task_id")
        if not task_id:
            return {"success": False, "message": "请提供任务ID"}
        
        try:
            task = default_repo.get_task_info(int(task_id))
            if not task:
                return {"success": False, "message": f"未找到任务 {task_id}"}
            
            # 重置任务状态
            default_repo.update_task_status(int(task_id), "pending")
            
            return {
                "success": True,
                "task": task,
                "message": f"任务 {task['name']} 已重置为待执行状态",
                "should_execute": True
            }
        except Exception as e:
            return {"success": False, "message": f"重新执行任务失败：{str(e)}"}

    def _delete_plan(self, params: Dict) -> Dict[str, Any]:
        """删除计划"""
        plan_id = params.get("plan_id")
        if not plan_id:
            return {"success": False, "message": "请提供计划ID"}
        
        try:
            deleted = default_repo.delete_plan(int(plan_id))
            if deleted:
                return {
                    "success": True,
                    "message": f"成功删除计划 {plan_id}"
                }
            else:
                return {
                    "success": False,
                    "message": f"计划 {plan_id} 不存在或已删除"
                }
        except Exception as e:
            return {"success": False, "message": f"删除计划失败：{str(e)}"}

    async def _delete_task(self, params: Dict, confirmed: bool = False) -> Dict[str, Any]:
        """删除指定任务及其子任务"""

        task_id = params.get("task_id")
        task_name = params.get("task_name")

        if not task_id and not task_name:
            return {"success": False, "message": "请提供任务ID或任务名称"}

        matched_task: Optional[Dict[str, Any]] = None

        session_task: Optional[Dict[str, Any]] = None

        if task_id is not None:
            try:
                task_id = int(task_id)
            except (TypeError, ValueError):
                return {"success": False, "message": "无效的任务ID"}

            if self.plan_session:
                session_task = self.plan_session.get_task(task_id)

            matched_task = session_task or default_repo.get_task_info(task_id)
            if not matched_task:
                return {"success": False, "message": f"未找到任务 {task_id}"}
        else:
            find_result = await self._find_task_id_by_name(task_name)
            if not find_result.get("success"):
                return find_result
            matched_task = find_result.get("task")
            task_id = matched_task["id"]
            session_task = self.plan_session.get_task(task_id) if self.plan_session else None

        plan_id_param = params.get("plan_id")
        if plan_id_param is not None:
            try:
                plan_id = int(plan_id_param)
            except (TypeError, ValueError):
                return {"success": False, "message": "无效的计划ID"}
        else:
            plan_id = None

        if not plan_id:
            if self.plan_session and session_task:
                plan_id = self.plan_session.plan_id
            else:
                plan_id = self.plan_id or default_repo.get_plan_for_task(int(task_id))

        if plan_id is None:
            return {"success": False, "message": "无法确定任务所属的计划"}

        # Stage deletion in session when available; otherwise delete directly
        deletion_snapshot: Dict[str, Any]
        tasks_snapshot: Optional[List[Dict[str, Any]]] = None
        task_tree_snapshot: Optional[List[Dict[str, Any]]] = None

        if self.plan_session and self.plan_session.plan_id == plan_id:
            try:
                deletion_snapshot = self.plan_session.delete_task(int(task_id))
            except ValueError as exc:
                return {"success": False, "message": str(exc)}
        else:
            deleted = default_repo.delete_task(int(task_id))
            if not deleted:
                return {"success": False, "message": f"任务 {task_id} 不存在或已删除"}
            deletion_snapshot = {
                "removed_ids": [int(task_id)],
                "removed_nodes": [matched_task] if matched_task else [],
                "parent_id": matched_task.get("parent_id") if matched_task else None,
            }
            tasks_snapshot = default_repo.get_plan_tasks(plan_id) if plan_id else []
            task_tree_snapshot = self._build_task_tree(tasks_snapshot) if tasks_snapshot else []

        removed_ids = deletion_snapshot.get("removed_ids", [])
        descendant_count = max(len(removed_ids) - 1, 0)
        if descendant_count > 0:
            message = f"成功删除任务 {task_id} 及其 {descendant_count} 个子任务"
        else:
            message = f"成功删除任务 {task_id}"

        return {
            "success": True,
            "message": message,
            "plan_id": int(plan_id) if plan_id is not None else None,
            "task_id": int(task_id),
            "deleted_task": matched_task,
            "removed_task_ids": removed_ids,
            "parent_id": deletion_snapshot.get("parent_id"),
            "tasks": tasks_snapshot,
            "task_tree": task_tree_snapshot,
        }
    
    def _show_help(self) -> Dict[str, Any]:
        """显示帮助信息"""
        help_text = """
Available Commands:
1. Create Plan: Create a research plan about [topic]
2. List Plans: Show all plans
3. Execute Plan: Execute plan [ID]
4. Query Status: Check status of plan [ID]
5. Show Tasks: Display tasks in plan [ID]
6. Rerun Task: Retry task [ID]
7. Delete Plan: Remove plan [ID]
8. Help: Show this help information
        """
        
        return {
            "success": True,
            "message": help_text.strip(),
            "commands": [
                {"command": "Create Plan", "description": "Create a new research plan"},
                {"command": "Show Plans", "description": "View all plan lists"},
                {"command": "Execute Plan", "description": "Execute tasks in specified plan"},
                {"command": "Check Status", "description": "View plan or task execution status"},
                {"command": "Show Tasks", "description": "View task tree of a plan"},
                {"command": "Rerun Task", "description": "Retry failed tasks"},
                {"command": "Delete Plan", "description": "Delete specified plan"},
                {"command": "Delete Task", "description": "Remove a task and its descendants"},
            ]
        }
    
    def _generate_visualization(self, intent: IntentType, data: Dict) -> Dict[str, Any]:
        """根据意图和数据生成可视化配置"""
        
        if intent == IntentType.CREATE_PLAN:
            return {
                "type": VisualizationType.NONE,
                "data": [],
                "config": {
                    "title": f"New Plan for: {data.get('stream_payload', {}).get('goal', '')[:30]}...",
                    "showTaskTree": True,
                    "showActions": ["execute", "edit", "delete"],
                    "plan_id": data.get("plan_id")
                }
            }
        elif intent == IntentType.CREATE_TASK:
            plan_id = data.get("plan_id") or self.plan_id
            task_tree = data.get("task_tree")
            if not task_tree and plan_id:
                task_tree = self._get_plan_task_tree(plan_id)
            return {
                "type": VisualizationType.TASK_TREE,
                "data": task_tree or [],
                "config": {
                    "title": f"Plan {plan_id} - Task Added" if plan_id else "Task Added",
                    "highlight_task_id": data.get("task", {}).get("id")
                }
            }
        elif intent == IntentType.MOVE_TASK:
            plan_id = data.get("plan_id") or self.plan_id
            task_tree = data.get("task_tree")
            if not task_tree and plan_id:
                task_tree = self._get_plan_task_tree(plan_id)
            return {
                "type": VisualizationType.TASK_TREE,
                "data": task_tree or [],
                "config": {
                    "title": f"Plan {plan_id} - Task Moved" if plan_id else "Task Moved",
                    "highlight_task_id": data.get("task_id")
                }
            }
        elif intent == IntentType.DELETE_TASK:
            plan_id = data.get("plan_id") or self.plan_id
            task_tree = data.get("task_tree")
            if not task_tree and plan_id:
                task_tree = self._get_plan_task_tree(plan_id)
            return {
                "type": VisualizationType.TASK_TREE,
                "data": task_tree or [],
                "config": {
                    "title": f"Plan {plan_id} - Task Deleted" if plan_id else "Task Deleted",
                    "plan_id": plan_id
                }
            }
        elif intent in (IntentType.UPDATE_TASK, IntentType.UPDATE_TASK_INSTRUCTION):
            plan_id = data.get("plan_id") or self.plan_id
            task_tree = data.get("task_tree")
            if not task_tree and plan_id:
                task_tree = self._get_plan_task_tree(plan_id)
            return {
                "type": VisualizationType.TASK_TREE,
                "data": task_tree or [],
                "config": {
                    "title": f"Plan {plan_id} - Task Updated" if plan_id else "Task Updated",
                    "highlight_task_id": data.get("task_id")
                }
            }
        elif intent == IntentType.LIST_PLANS:
            return {
                "type": VisualizationType.PLAN_LIST,
                "data": data.get("plans", []),
                "config": {
                    "showProgress": True,
                    "showActions": True
                }
            }
        elif intent == IntentType.EXECUTE_PLAN:
            return {
                "type": VisualizationType.TASK_TREE,
                "data": data.get("tasks", []),
                "config": {
                    "autoRefresh": True,
                    "refreshInterval": 2000,
                    "plan_id": data.get("plan_id")
                }
            }
        elif intent == IntentType.SHOW_TASKS:
            # 使用任务列表视图展示任务
            tasks_data = data.get("task_tree", data.get("tasks", []))
            return {
                "type": VisualizationType.TASK_TREE,
                "data": tasks_data,
                "config": {
                    "title": f"Plan {data.get('plan_id', '')} Tasks",
                    "plan_id": data.get("plan_id")
                }
            }
        elif intent == IntentType.QUERY_STATUS:
            return {
                "type": VisualizationType.TASK_TREE,
                "data": data.get("tasks", []),
                "config": {
                    "title": f"Plan {data.get('plan_id', '')} Status",
                    "plan_id": data.get("plan_id")
                }
            }
        elif intent == IntentType.HELP:
            return {
                "type": VisualizationType.HELP_MENU,
                "data": data.get("commands", []),
                "config": {}
            }
        else:
            return {
                "type": VisualizationType.NONE,
                "data": {},
                "config": {}
            }
    
    def _format_response(self, intent: IntentType, result: Dict) -> str:
        """Format response text"""
        if not result.get("success"):
            return result.get("message", "Operation failed")
        
        return result.get("message", "Operation completed")
