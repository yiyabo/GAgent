from typing import Any, Dict, Optional, List
from enum import Enum
import logging
import json
from fastapi import BackgroundTasks

from ..llm import get_default_client as get_llm
from ..repository.tasks import default_repo
from ..utils import parse_json_obj


logger = logging.getLogger(__name__)


class IntentType(Enum):
    """支持的意图类型"""
    CREATE_PLAN = "create_plan"
    CREATE_TASK = "create_task"
    UPDATE_TASK = "update_task"
    UPDATE_TASK_INSTRUCTION = "update_task_instruction"
    LIST_PLANS = "list_plans"
    EXECUTE_PLAN = "execute_plan"
    QUERY_STATUS = "query_status"
    SHOW_TASKS = "show_tasks"
    RERUN_TASK = "rerun_task"
    DELETE_PLAN = "delete_plan"
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
        conversation_history: Optional[List[Dict]] = None
    ):
        self.plan_id = plan_id
        self.llm = get_llm()
        self.background_tasks = background_tasks
        self.history = conversation_history or []
        
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

            matched_task = next((task for task in tasks_summary if task['id'] == task_id), None)
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
        context_info = ""
        if self.plan_id:
            context_info = f"Current plan ID: {self.plan_id}. "
        
        # 统一的LLM提示
        unified_prompt = f"""You are an AI assistant for a research plan management system. {context_info}

Analyze the user's message and decide whether it requires tool usage or is casual conversation.

**If it's a TASK that needs tools**, respond with JSON format:
{{
  "needs_tool": true,
  "intent": "create_plan|create_task|update_task|update_task_instruction|list_plans|execute_plan|show_tasks|query_status|delete_plan|rerun_task|help",
  "parameters": {{ "goal": "...", "plan_id": "...", "task_id": "...", "task_name": "...", "name": "...", "status": "...", "instruction": "..."}},
  "initial_response": "I'll help you with that. Let me [action description]..."
}}

**If it's CASUAL CHAT**, respond with JSON format:
{{
  "needs_tool": false,
  "intent": "chat", 
  "response": "Your natural conversational response here..."
}}

Available tool intents:
- create_plan: Create new research plans (extract goal, title, sections, etc.)
- create_task: Create a new task within a plan (extract task_name, parent_id, and plan_id if available)
- update_task: Modify a task's METADATA (extract task_id OR task_name, and fields to change like name or status). Does NOT change the instruction.
- update_task_instruction: Change the detailed instructions/prompt for a task (extract task_id or task_name, and the new instruction)
- list_plans: Show all existing plans
- execute_plan: Start executing a specific plan
- show_tasks: Display tasks in a plan
- query_status: Check status/progress of plans or tasks  
- delete_plan: Remove a plan
- rerun_task: Restart a specific task
- help: Show available commands

Examples:
- "change the status of the 'data analysis' task to done" -> {{'intent': 'update_task', 'parameters': {{'task_name': 'data analysis', 'status': 'done'}}}} 
- "update instruction for 'literature review' to focus on 2023 papers" -> {{'intent': 'update_task_instruction', 'parameters': {{'task_name': 'literature review', 'instruction': 'focus on papers published after 2023'}}}} 

User message: "{user_command}"

Respond with JSON only:"""

        try:
            result = self.llm.chat(unified_prompt, history=self.history).strip()
            logger.info(f"Unified LLM response: {result}")
            
            # 解析JSON响应，处理可能的markdown代码块包装
            import json
            
            # 提取JSON内容，去除可能的markdown代码块标记
            json_content = result
            if result.startswith('```json'):
                # 提取```json和```之间的内容
                start_marker = '```json'
                end_marker = '```'
                start_idx = result.find(start_marker)
                if start_idx != -1:
                    start_idx += len(start_marker)
                    end_idx = result.find(end_marker, start_idx)
                    if end_idx != -1:
                        json_content = result[start_idx:end_idx].strip()
            elif result.startswith('```'):
                # 处理普通的```包装
                start_idx = result.find('\n')
                if start_idx != -1:
                    end_idx = result.rfind('```')
                    if end_idx != -1 and end_idx > start_idx:
                        json_content = result[start_idx:end_idx].strip()
            
            logger.info(f"Extracted JSON content: {json_content}")
            parsed = json.loads(json_content)
            
            if parsed.get("needs_tool", False):
                # 需要工具调用
                return await self._handle_tool_request(parsed, user_command, confirmed)
            else:
                # 普通对话
                return self._handle_chat_response(parsed, user_command)
                
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM JSON response: {result}")
            # 降级处理：尝试理解意图
            return await self._fallback_processing(user_command)
        except Exception as e:
            logger.error(f"Error in unified processing: {e}")
            raise e
    
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
            initial_response = parsed_response.get("initial_response", "Let me help you with that...")
            
            logger.info(f"Tool request - Intent: {intent.value}, Params: {params}")
            
            # 执行工具动作
            action_result = await self._execute_action(intent, params, confirmed)

            # 如果需要确认，则提前返回
            if action_result.get("requires_confirmation"):
                return action_result
            
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
            return {
                "response": "I understand you're chatting with me. How can I help you today?",
                "initial_response": "I understand you're chatting with me. How can I help you today?", 
                "execution_feedback": None,
                "intent": "chat",
                "visualization": {"type": "none", "data": {}, "config": {}},
                "action_result": {"success": True, "type": "chat", "is_casual_chat": True},
                "success": True
            }
        else:
            # 尝试解析为help请求
            return await self._handle_tool_request({
                "needs_tool": True,
                "intent": "help",
                "parameters": {},
                "initial_response": "I'm not sure what you're asking for. Let me show you what I can help with..."
            }, user_command)
    
    def _is_casual_chat_simple(self, command: str) -> bool:
        """简单的关键词检测（用作备用方案）"""
        casual_patterns = ['hello', 'hi', 'thanks', 'thank', 'good', 'how are', '你好', '谢谢', '好的', '嗯']
        command_lower = command.lower()
        return any(pattern in command_lower for pattern in casual_patterns) or len(command.strip()) < 6
    
    def _generate_initial_response(self, intent: IntentType, params: Dict, user_command: str) -> str:
        """生成即时响应（在执行工具之前）"""
        
        if intent == IntentType.CREATE_PLAN:
            goal = params.get("goal", "research project")
            return f"I'll help you create a research plan about '{goal}'. Let me generate the plan structure and tasks for you..."
        
        elif intent == IntentType.LIST_PLANS:
            return "Let me fetch all your current plans..."
        
        elif intent == IntentType.EXECUTE_PLAN:
            plan_id = params.get("plan_id", "the specified")
            return f"I'll start executing plan {plan_id}. Let me check the tasks and begin execution..."
        
        elif intent == IntentType.QUERY_STATUS:
            if params.get("plan_id"):
                return f"Let me check the status of plan {params.get('plan_id')}..."
            elif params.get("task_id"):
                return f"Checking the status of task {params.get('task_id')}..."
            else:
                return "Let me check the overall status..."
        
        elif intent == IntentType.SHOW_TASKS:
            plan_id = params.get("plan_id", "the specified")
            return f"I'll show you all tasks in plan {plan_id}..."
        
        elif intent == IntentType.RERUN_TASK:
            task_id = params.get("task_id", "the specified")
            return f"I'll restart task {task_id} for you..."
        
        elif intent == IntentType.DELETE_PLAN:
            plan_id = params.get("plan_id", "the specified")
            return f"I'll delete plan {plan_id} as requested..."
        
        elif intent == IntentType.HELP:
            return "Here's what I can help you with..."
        
        else:
            return "Let me process your request..."
    
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
        
        elif intent == IntentType.HELP:
            return "You can click on any command above to quickly execute it, or type your request naturally."
        
        else:
            return "✅ Operation completed successfully."
    
    def _is_casual_chat(self, command: str) -> bool:
        """使用LLM智能判断是否是普通聊天而非工具调用"""
        command_lower = command.lower().strip()
        
        # 明确的问候、感谢等一定是普通聊天
        casual_patterns = [
            'hello', 'hi', 'hey', 'good morning', 'good afternoon', 'good evening',
            'thank', 'thanks', 'sorry', 'excuse me', 'how are you', 'what\'s up',
            'nice to meet', 'goodbye', 'bye', 'see you', 'lol', 'haha', 'wow',
            '你好', '您好', '早上好', '下午好', '晚上好', '谢谢', '感谢', '对不起',
            '不好意思', '你怎么样', '最近怎样', '再见', '拜拜', '哈哈', '哇', '嗯', '好的'
        ]
        
        # 如果是明确的问候或很短的消息，直接判断为聊天
        if len(command.strip()) < 6 or any(pattern in command_lower for pattern in casual_patterns):
            return True
        
        # 使用LLM进行智能判断
        try:
            llm_prompt = f"""You are an intent classifier for a research plan management system. Analyze the user's message and determine if it's casual chat or a task command.

**Task Commands** - User wants to DO something:
- Create/generate/build plans or tasks: \"create a plan\", \"generate a research outline\"
- Execute/run/start operations: \"execute plan 1\", \"run the tasks\", \"start the workflow\"  
- View/show/list/display information: \"show all plans\", \"list tasks\", \"display status\"
- Manage/modify/delete items: \"delete plan 2\", \"update task\", \"modify the plan\"
- Query specific status/progress: \"what's the status of plan 3\", \"check task progress\"

**Casual Chat** - User wants to DISCUSS or EXPRESS:
- Opinions/comments: \"this looks good\", \"I like this plan\", \"that's interesting\"
- Emotional reactions: \"great!\", \"awesome!\", \"I'm excited\", \"this is confusing\"
- General questions: \"how does this work?\", \"what do you think?\", \"is this normal?\"
- Social interaction: \"thank you\", \"hello\", \"how are you?\", \"goodbye\"
- Satisfaction/feedback without specific requests: \"the results look promising\"

User message: "{command}"

Respond with JSON only:
{{'intent': 'CHAT'}} or {{'intent': 'TASK'}} """

            result = self.llm.chat(llm_prompt).strip()
            # 尝试解析JSON
            import json
            try:
                parsed = json.loads(result)
                intent_detected = parsed.get("intent") == "CHAT"
                logger.info(f"LLM intent classification: {result} → is_chat: {intent_detected}")
                return intent_detected
            except json.JSONDecodeError as je:
                # 如果JSON解析失败，尝试从文本中提取
                logger.warning(f"JSON parse failed for: {result}, using fallback")
                return "CHAT" in result.upper()
            
        except Exception as e:
            logger.warning(f"Failed to determine chat vs task intent: {e}")
            # 如果LLM调用失败，使用保守的关键词检查
            task_keywords = [
                'create', 'generate', 'make', 'build', 'execute', 'run', 'start', 
                'show', 'display', 'list', 'view', 'status', 'progress', 'delete',
                '创建', '生成', '执行', '运行', '显示', '列表', '查看', '状态', '删除'
            ]
            return not any(keyword in command_lower for keyword in task_keywords)
    
    def _handle_casual_chat(self, command: str) -> Dict[str, Any]:
        """处理普通聊天"""
        try:
            # 构建上下文相关的聊天提示
            context_info = ""
            if self.plan_id:
                context_info = f" You are currently working with plan ID {self.plan_id}. "
            
            # 使用LLM进行自然对话
            chat_prompt = f"""You are a helpful AI assistant for a research plan management system.{context_info} 
The user is having a casual conversation with you. Respond naturally and helpfully.

This is casual chat, not a task command. Be conversational, friendly, and supportive. 
You can reference the current plan context if relevant, but don't assume the user wants to perform any specific task.

User: {command}

Respond in a friendly, conversational way. Keep it natural and engaging."""
            
            response = self.llm.chat(chat_prompt)
            
            return {
                "response": response,
                "initial_response": response,  # 对于聊天，immediate response就是完整回复
                "execution_feedback": None,   # 聊天不需要执行反馈
                "intent": "chat",
                "visualization": {
                    "type": "none",
                    "data": {},
                    "config": {}
                },
                "action_result": {
                    "success": True, 
                    "type": "chat",
                    "is_casual_chat": True  # 明确标识这是casual chat
                },
                "success": True
            }
        except Exception as e:
            logger.error(f"Error in casual chat: {e}")
            fallback_response = "I'm here to help! You can ask me to create plans, execute tasks, or just chat."
            return {
                "response": fallback_response,
                "initial_response": fallback_response,
                "execution_feedback": None,
                "intent": "chat", 
                "visualization": {
                    "type": "none",
                    "data": {},
                    "config": {}
                },
                "action_result": {
                    "success": True, 
                    "type": "chat",
                    "is_casual_chat": True
                },
                "success": True
            }
    
    def _identify_intent(self, command: str) -> Dict[str, Any]:
        """使用LLM识别用户意图"""
        
        prompt = f"""You are a research plan management assistant. Analyze the user's command and determine what action to take.

User command: {command}

Available actions:
- CREATE_PLAN: Create a new research plan (extract the research topic/goal)
- LIST_PLANS: Show all existing plans  
- EXECUTE_PLAN: Start executing a plan (extract plan ID/number)
- QUERY_STATUS: Check execution status (extract plan/task ID if mentioned)
- SHOW_TASKS: Display tasks in a plan (extract plan ID if mentioned)
- RERUN_TASK: Retry a failed task (extract task ID)
- DELETE_PLAN: Remove a plan (extract plan ID)
- HELP: Show available commands
- CHAT: General conversation/casual chat
- UNKNOWN: Cannot understand the request

Extract any relevant parameters (IDs, goals, topics) from the user's natural language input.

Examples:
- "Create a machine learning research plan" → {{'intent': 'CREATE_PLAN', 'parameters': {{'goal': 'machine learning research plan'}}}} 
- "Show me all plans" → {{'intent': 'LIST_PLANS', 'parameters': {{}}}} 
- "Execute plan 2" → {{'intent': 'EXECUTE_PLAN', 'parameters': {{'plan_id': '2'}}}} 
- "What's the status?" → {{'intent': 'QUERY_STATUS', 'parameters': {{}}}} 

Return only JSON format: {{'intent': 'ACTION_NAME', 'parameters': {{'key': 'value'}}}} """ 
        
        try:
            logger.info(f"[_identify_intent] Analyzing command: {command[:100]}...\n")
            response = self.llm.chat(prompt)
            logger.info(f"[_identify_intent] LLM response: {response[:200]}...\n")
            
            result = parse_json_obj(response)
            if not result:
                logger.error(f"[_identify_intent] Failed to parse JSON from: {response[:200]}...")
                raise ValueError("Failed to parse JSON")
                
            intent_str = result.get("intent", "UNKNOWN")
            logger.info(f"[_identify_intent] Identified intent: {intent_str}, params: {result.get('parameters', {})}")
            
            try:
                intent = IntentType[intent_str]
            except KeyError:
                logger.warning(f"[_identify_intent] Unknown intent: {intent_str}")
                intent = IntentType.UNKNOWN
                
            return {
                "intent": intent,
                "parameters": result.get("parameters", {})
            }
        except Exception as e:
            logger.error(f"[_identify_intent] Intent identification failed: {e}", exc_info=True)
            return {
                "intent": IntentType.UNKNOWN,
                "parameters": {}
            }
    
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
            
            # Ensure parent_id is an integer if it exists
            if parent_id:
                parent_id = int(parent_id)

            task_id = default_repo.create_task(
                name=task_name,
                parent_id=parent_id,
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
            success = default_repo.update_task(task_id=task_id, **updates)
            if not success:
                return {"success": False, "message": f"Task {task_id} not found or failed to update."}
            plan_id = self.plan_id or default_repo.get_plan_for_task(task_id)
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
            existing_instruction = default_repo.get_task_input(task_id)

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
                tasks = default_repo.get_plan_tasks(plan["id"])
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
            tasks = default_repo.get_plan_tasks(int(plan_id))
            
            # 过滤出待执行的任务
            pending_tasks = [t for t in tasks if t.get("status") == "pending"]
            
            if not pending_tasks:
                return {
                    "success": True,
                    "message": "没有待执行的任务",
                    "tasks": tasks,
                    "plan_id": plan_id
                }
            
            # 标记需要执行，实际执行将在路由层处理
            return {
                "success": True,
                "message": f"准备执行 {len(pending_tasks)} 个任务（共 {len(tasks)} 个）",
                "tasks": tasks,
                "pending_count": len(pending_tasks),
                "total_count": len(tasks),
                "plan_id": plan_id,
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
                    task = default_repo.get_task_info(task_id)
                    if not task:
                        return {"success": False, "message": f"未找到任务 {task_id}"}
                    
                    # To show the tree, we need all tasks of the plan this task belongs to
                    containing_plan_id = default_repo.get_plan_for_task(task_id)
                    tasks = default_repo.get_plan_tasks(containing_plan_id)
                    return {
                        "success": True,
                        "type": "task",
                        "plan_id": containing_plan_id,
                        "tasks": tasks,
                        "message": f"任务 {task['name']} 状态：{task['status']}"
                    }

            if plan_id:
                tasks = default_repo.get_plan_tasks(int(plan_id))
                status_count = {}
                for task in tasks:
                    status = task.get("status", "unknown")
                    status_count[status] = status_count.get(status, 0) + 1
                
                return {
                    "success": True,
                    "type": "plan",
                    "plan_id": plan_id,
                    "total_tasks": len(tasks),
                    "status_count": status_count,
                    "tasks": tasks,
                    "message": f"计划 {plan_id} 共有 {len(tasks)} 个任务"
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
            tasks = default_repo.get_plan_tasks(int(plan_id))
            
            # 构建任务树结构
            task_tree = self._build_task_tree(tasks)
            
            return {
                "success": True,
                "plan_id": int(plan_id),
                "tasks": tasks,
                "task_tree": task_tree,
                "message": f"计划 {plan_id} 包含 {len(tasks)} 个任务"
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
        
        return roots
    
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
            return {
                "type": VisualizationType.TASK_TREE,
                "data": { "refresh": True, "plan_id": data.get("plan_id") },
                "config": {
                    "title": f"Plan {data.get('plan_id')} - Task Added",
                    "highlight_task_id": data.get("task", {}).get("id")
                }
            }
        elif intent in (IntentType.UPDATE_TASK, IntentType.UPDATE_TASK_INSTRUCTION):
            return {
                "type": VisualizationType.TASK_TREE,
                "data": { "refresh": True, "plan_id": data.get("plan_id") },
                "config": {
                    "title": f"Plan {data.get('plan_id')} - Task Updated",
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
