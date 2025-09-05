
from typing import Any, Dict, Optional, List
from enum import Enum
import json
import logging

from ..llm import get_default_client as get_llm
from ..repository.tasks import default_repo
from ..utils import parse_json_obj

logger = logging.getLogger(__name__)


class IntentType(Enum):
    """支持的意图类型"""
    CREATE_PLAN = "create_plan"
    LIST_PLANS = "list_plans"
    EXECUTE_PLAN = "execute_plan"
    QUERY_STATUS = "query_status"
    SHOW_TASKS = "show_tasks"
    RERUN_TASK = "rerun_task"
    DELETE_PLAN = "delete_plan"
    HELP = "help"
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

    def __init__(self, plan_id: Optional[int] = None):
        self.plan_id = plan_id
        self.llm = get_llm()
        self.context = {}  # 存储会话上下文
        
    def process_command(self, user_command: str) -> Dict[str, Any]:
        """处理用户命令并返回响应和可视化指令"""
        
        # 1. 识别意图和提取参数
        intent_result = self._identify_intent(user_command)
        intent = intent_result["intent"]
        params = intent_result["parameters"]
        
        logger.info(f"Identified intent: {intent.value}, params: {params}")
        
        # 2. 执行相应动作
        action_result = self._execute_action(intent, params)
        
        # 3. 生成可视化指令
        visualization = self._generate_visualization(intent, action_result)
        
        # 4. 生成对话响应
        response_text = self._format_response(intent, action_result)
        
        return {
            "response": response_text,
            "intent": intent.value,
            "visualization": {
                "type": visualization["type"] if isinstance(visualization["type"], str) else visualization["type"].value,
                "data": visualization["data"],
                "config": visualization.get("config", {})
            },
            "action_result": action_result,
            "success": action_result.get("success", True)
        }
    
    def _identify_intent(self, command: str) -> Dict[str, Any]:
        """使用LLM识别用户意图"""
        
        prompt = f"""Analyze the user's command and identify the intent and extract parameters.
Support both English and Chinese commands.

User command: {command}

Possible intents:
- CREATE_PLAN: Create a new plan (parameter: goal)
- LIST_PLANS: List all plans
- EXECUTE_PLAN: Execute a plan (parameter: plan_id)
- QUERY_STATUS: Query status (parameter: plan_id or task_id)
- SHOW_TASKS: Show tasks (parameter: plan_id)
- RERUN_TASK: Rerun task (parameter: task_id)
- DELETE_PLAN: Delete plan (parameter: plan_id)
- HELP: Help information

Recognition rules:
1. If mentions "create", "make", "generate", "build", "创建", "新建", "制定", "生成" with "plan", "research", "project", "计划", "方案" -> CREATE_PLAN
   - Extract the topic/goal as the goal parameter
   - Examples: "Create a research plan about X", "创建一个关于X的计划"
   
2. If mentions "show", "list", "display", "view", "显示", "列出", "查看" with "plans", "计划" -> LIST_PLANS

3. If mentions "execute", "run", "start", "执行", "运行", "启动" with plan or number -> EXECUTE_PLAN
   - Extract number as plan_id

4. If mentions "status", "progress", "状态", "进度" -> QUERY_STATUS
   - Extract number as plan_id or task_id if present

5. If mentions "tasks", "任务" with show/view -> SHOW_TASKS
   - Extract number as plan_id

6. If mentions "rerun", "retry", "重新", "重试" with execute -> RERUN_TASK
   - Extract number as task_id

7. If mentions "delete", "remove", "删除", "移除" -> DELETE_PLAN
   - Extract number as plan_id

8. If mentions "help", "帮助" -> HELP

Examples:
- "Create a research plan about Prophages as Modulators of the Human Gut Microbiome" -> {{"intent": "CREATE_PLAN", "parameters": {{"goal": "Prophages as Modulators of the Human Gut Microbiome and Their Influence on Host Health"}}}}
- "创建一个关于深度学习的研究计划" -> {{"intent": "CREATE_PLAN", "parameters": {{"goal": "深度学习的研究计划"}}}}
- "Execute plan 3" -> {{"intent": "EXECUTE_PLAN", "parameters": {{"plan_id": "3"}}}}
- "Show all plans" -> {{"intent": "LIST_PLANS", "parameters": {{}}}}

Return JSON format only, no other content."""
        
        try:
            logger.info(f"[_identify_intent] Analyzing command: {command[:100]}...")
            response = self.llm.chat(prompt)
            logger.info(f"[_identify_intent] LLM response: {response[:200]}...")
            
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
    
    def _execute_action(self, intent: IntentType, params: Dict) -> Dict[str, Any]:
        """执行具体的后端操作"""
        
        try:
            if intent == IntentType.CREATE_PLAN:
                return self._create_plan(params)
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
            else:
                return {"success": False, "message": "无法理解您的指令，请尝试其他表述或输入'帮助'查看可用命令"}
        except Exception as e:
            logger.error(f"Action execution failed: {e}")
            return {"success": False, "message": f"执行操作时出错：{str(e)}"}
    
    def _create_plan(self, params: Dict) -> Dict[str, Any]:
        """创建新计划"""
        from ..services.planning import propose_plan_service
        
        goal = params.get("goal", "")
        logger.info(f"[_create_plan] Creating plan with goal: {goal}")
        
        if not goal:
            return {"success": False, "message": "请提供计划目标"}
        
        try:
            # 调用 propose_plan_service，它会通过 BFS_planner 创建计划和任务
            logger.info(f"[_create_plan] Calling propose_plan_service with goal: {goal}")
            
            # Build payload with optional parameters
            payload = {"goal": goal}
            if params.get("sections"):
                payload["sections"] = params["sections"]
            if params.get("title"):
                payload["title"] = params["title"]
            if params.get("style"):
                payload["style"] = params["style"]
            if params.get("notes"):
                payload["notes"] = params["notes"]
                
            result = propose_plan_service(payload)
            logger.info(f"[_create_plan] propose_plan_service returned: success={result.get('success')}, plan_id={result.get('plan_id')}, total_tasks={result.get('total_tasks')}")
            
            # Check if the plan was actually created (plan_id is the key indicator)
            if not result.get("plan_id"):
                error_msg = result.get('error', 'Failed to generate plan - no plan_id returned')
                logger.error(f"[_create_plan] Plan creation failed: {error_msg}")
                return {
                    "success": False,
                    "message": f"创建计划失败：{error_msg}"
                }
            
            # Extract plan information
            plan_id = result.get("plan_id")
            title = result.get("title", goal[:60])
            total_tasks = result.get("total_tasks", 0)
            max_layer = result.get("max_layer", 0)
            
            # Create plan info for response
            plan_info = {
                "id": plan_id,
                "title": title,
                "total_tasks": total_tasks,
                "max_layer": max_layer
            }
            
            logger.info(f"[_create_plan] Successfully created plan: {plan_info}")
            
            # Return success with plan details
            return {
                "success": True,
                "plan": plan_info,
                "plan_id": plan_id,
                "message": f"成功创建计划「{title}」，包含 {total_tasks} 个任务，最大深度 {max_layer} 层"
            }
        except Exception as e:
            logger.error(f"[_create_plan] Failed to create plan: {e}", exc_info=True)
            return {"success": False, "message": f"创建计划失败：{str(e)}"}
    
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
        
        try:
            if task_id:
                task = default_repo.get_task_info(int(task_id))
                if not task:
                    return {"success": False, "message": f"未找到任务 {task_id}"}
                return {
                    "success": True,
                    "type": "task",
                    "task": task,
                    "message": f"任务 {task['name']} 状态：{task['status']}"
                }
            elif plan_id:
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
                return {"success": False, "message": "请提供计划ID或任务ID"}
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
                "tasks": tasks,
                "task_tree": task_tree,
                "message": f"计划包含 {len(tasks)} 个任务"
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
可用命令：
1. 创建计划：创建一个关于[主题]的计划
2. 显示计划：显示所有计划
3. 执行计划：执行计划[ID]
4. 查询状态：查询计划[ID]的状态
5. 显示任务：显示计划[ID]的任务
6. 重新执行：重新执行任务[ID]
7. 删除计划：删除计划[ID]
8. 帮助：显示此帮助信息
        """
        
        return {
            "success": True,
            "message": help_text.strip(),
            "commands": [
                {"command": "创建计划", "description": "创建新的研究计划"},
                {"command": "显示计划", "description": "查看所有计划列表"},
                {"command": "执行计划", "description": "执行指定计划的任务"},
                {"command": "查询状态", "description": "查看计划或任务的执行状态"},
                {"command": "显示任务", "description": "查看计划的任务树"},
                {"command": "重新执行", "description": "重新执行失败的任务"},
                {"command": "删除计划", "description": "删除指定的计划"},
            ]
        }
    
    def _generate_visualization(self, intent: IntentType, data: Dict) -> Dict[str, Any]:
        """根据意图和数据生成可视化配置"""
        
        if intent == IntentType.CREATE_PLAN:
            # Show the created plan graph visualization
            plan_info = data.get("plan", {})
            return {
                "type": "plan_graph",  # 使用新的图形视图
                "data": plan_info,
                "config": {
                    "title": plan_info.get("title", "新计划"),
                    "showTaskTree": True,
                    "showActions": ["execute", "edit", "delete"],
                    "plan_id": data.get("plan_id")
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
                "type": VisualizationType.EXECUTION_PROGRESS,
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
                "type": VisualizationType.TASK_LIST,  # 使用任务列表视图
                "data": tasks_data if isinstance(tasks_data, list) else tasks_data.get("tasks", []),
                "config": {
                    "title": f"计划 {data.get('plan_id', '')} 任务列表",
                    "showProgress": True,
                    "showActions": True,
                    "plan_id": data.get("plan_id")
                }
            }
        elif intent == IntentType.QUERY_STATUS:
            return {
                "type": VisualizationType.STATUS_DASHBOARD,
                "data": data,
                "config": {
                    "showMetrics": True
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
        """格式化响应文本"""
        if not result.get("success"):
            return result.get("message", "操作失败")
        
        return result.get("message", "操作完成")

