"""
Tool-Enhanced Executor

This module provides a tool-enhanced executor that integrates Tool Box capabilities
with the existing task execution system for superior performance.
"""

import logging
from typing import Any, Dict, List, Optional
import asyncio

from .base import execute_task as base_execute_task
from ...interfaces import TaskRepository
from ...repository.tasks import default_repo

logger = logging.getLogger(__name__)


class ToolEnhancedExecutor:
    """Tool-enhanced executor with intelligent tool integration"""
    
    def __init__(self, repo: Optional[TaskRepository] = None):
        self.repo = repo or default_repo
        self.tool_router = None
        self._initialized = False
    
    async def initialize(self):
        """Initialize the tool-enhanced executor"""
        if self._initialized:
            return
            
        try:
            from tool_box import get_smart_router
            self.tool_router = await get_smart_router()
            self._initialized = True
            logger.info("Tool-enhanced executor initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize tool-enhanced executor: {e}")
            raise
    
    async def execute_task(self, task, use_context: bool = True, 
                          context_options: Optional[Dict[str, Any]] = None) -> str:
        """
        Execute task with tool enhancement
        
        Args:
            task: Task to execute
            use_context: Whether to use context
            context_options: Context configuration options
            
        Returns:
            Execution status
        """
        if not self._initialized:
            await self.initialize()
        
        try:
            # Get task information
            task_id = task.get("id") if isinstance(task, dict) else task[0]
            task_name = task.get("name") if isinstance(task, dict) else task[1]
            
            # Get task prompt for tool analysis
            task_prompt = self.repo.get_task_input_prompt(task_id)
            if not task_prompt:
                task_prompt = f"Complete the task: {task_name}"
            
            logger.info(f"Analyzing task {task_id} for tool requirements")
            
            # Phase 1: Analyze if task needs external tools
            needs_tools, tool_analysis = await self._analyze_tool_requirements(task_prompt, task)
            
            if needs_tools and self.tool_router:
                logger.info(f"Task {task_id} requires external tools, proceeding with tool-enhanced execution")
                
                # Phase 2: Intelligent tool routing
                routing_context = await self._build_routing_context(task, context_options)
                logger.info(
                    "Tool routing start: task_id=%s, context_enabled=%s",
                    task_id,
                    routing_context.get("context_enabled")
                )
                routing_result = await self.tool_router.route_request(task_prompt, routing_context)
                
                # Phase 3: Execute INFORMATION GATHERING tools only (not output tools)
                info_gathering_tools = []
                output_tools = []
                
                for call in routing_result.get('tool_calls', []):
                    if call['tool_name'] in ['web_search', 'database_query']:
                        info_gathering_tools.append(call)
                    elif call['tool_name'] == 'file_operations':
                        output_tools.append(call)  # Execute after content generation
                
                # Execute information gathering tools first
                tool_outputs = []
                if info_gathering_tools:
                    tool_outputs = await self._execute_tool_calls(info_gathering_tools, task)
                    logger.info("Tool info calls executed: %d", len(tool_outputs))
                
                # Phase 4: Enhance context with tool results
                enhanced_context_options = await self._enhance_context_with_tools(
                    context_options, tool_outputs, tool_analysis
                )
                
                # Phase 5: Execute LLM with enhanced context to generate content
                logger.info(f"Generating content with enhanced context for task {task_id}")
                if enhanced_context_options.get("tool_enhanced"):
                    logger.info(f"Tool context available: {len(enhanced_context_options.get('combined', ''))} chars")
                
                status = base_execute_task(task, use_context=True, context_options=enhanced_context_options)
                
                # Phase 6: Execute output tools AFTER content generation
                if output_tools and status == "done":
                    await self._execute_post_generation_tools(output_tools, task, task_id)
                
                # Phase 7: Record tool usage for learning
                await self._record_tool_usage(task, routing_result, tool_outputs, status)
                
                logger.info(f"Tool-enhanced execution completed for task {task_id}")
                return status
            else:
                # Fallback to standard execution
                logger.info(f"Task {task_id} does not require tools, using standard execution")
                return base_execute_task(task, use_context=use_context, context_options=context_options)
                
        except Exception as e:
            logger.error(f"Tool-enhanced execution failed for task {task_id}: {e}")
            # Fallback to standard execution on error
            return base_execute_task(task, use_context=use_context, context_options=context_options)
    
    async def _analyze_tool_requirements(self, task_prompt: str, task) -> tuple[bool, Dict[str, Any]]:
        """Analyze if task requires external tools"""
        try:
            # Use simple heuristics for tool requirement analysis
            prompt_lower = task_prompt.lower()
            
            # Check for information retrieval needs
            needs_search = any(keyword in prompt_lower for keyword in [
                "搜索", "查找", "最新", "当前", "实时", "新闻", "趋势", "研究",
                "search", "find", "latest", "current", "real-time", "news", "trends", "research"
            ])
            
            # Check for file operation needs  
            needs_files = any(keyword in prompt_lower for keyword in [
                "文件", "保存", "读取", "导出", "报告", "文档",
                "file", "save", "read", "export", "report", "document"
            ])
            
            # Check for data analysis needs
            needs_data = any(keyword in prompt_lower for keyword in [
                "数据", "统计", "分析", "查询", "数据库", "表",
                "data", "statistics", "analysis", "query", "database", "table"
            ])
            
            analysis = {
                "needs_search": needs_search,
                "needs_files": needs_files, 
                "needs_data": needs_data,
                "complexity": "high" if sum([needs_search, needs_files, needs_data]) >= 2 else "medium" if any([needs_search, needs_files, needs_data]) else "low"
            }
            
            needs_tools = any([needs_search, needs_files, needs_data])
            
            return needs_tools, analysis
            
        except Exception as e:
            logger.warning(f"Tool requirement analysis failed: {e}")
            return False, {}
    
    async def _build_routing_context(self, task, context_options) -> Dict[str, Any]:
        """Build context for tool routing"""
        task_id = task.get("id") if isinstance(task, dict) else task[0]
        task_name = task.get("name") if isinstance(task, dict) else task[1]
        
        routing_context = {
            "task_id": task_id,
            "task_name": task_name,
            "task_type": task.get("task_type", "atomic"),
            "depth": task.get("depth", 0),
            "context_enabled": bool(context_options)
        }
        
        # Add dependency information if available
        try:
            dependencies = self.repo.list_dependencies(task_id)
            if dependencies:
                routing_context["dependencies"] = [
                    {"id": dep.get("id"), "name": dep.get("name"), "kind": dep.get("kind")}
                    for dep in dependencies[:3]  # Limit to avoid context overflow
                ]
        except Exception:
            pass
        
        return routing_context
    
    async def _execute_tool_calls(self, tool_calls: List[Dict[str, Any]], task) -> List[Dict[str, Any]]:
        """Execute tool calls and collect results"""
        from tool_box import execute_tool
        
        tool_outputs = []
        task_id = task.get("id") if isinstance(task, dict) else task[0]
        
        for call in tool_calls:
            try:
                logger.info(f"Executing tool {call['tool_name']} for task {task_id}")
                
                result = await execute_tool(call['tool_name'], **call['parameters'])
                
                tool_outputs.append({
                    'tool_name': call['tool_name'],
                    'parameters': call['parameters'],
                    'result': result,
                    'reasoning': call.get('reasoning', ''),
                    'execution_order': call.get('execution_order', 999),
                    'success': True
                })
                
                logger.info(f"Tool {call['tool_name']} executed successfully for task {task_id}")
                
            except Exception as e:
                logger.error(f"Tool {call['tool_name']} execution failed for task {task_id}: {e}")
                
                tool_outputs.append({
                    'tool_name': call['tool_name'],
                    'parameters': call['parameters'],
                    'error': str(e),
                    'success': False
                })
        
        return tool_outputs
    
    async def _enhance_context_with_tools(self, context_options: Optional[Dict[str, Any]], 
                                        tool_outputs: List[Dict[str, Any]], 
                                        tool_analysis: Dict[str, Any]) -> Dict[str, Any]:
        """Enhance context options with tool results"""
        enhanced_options = dict(context_options) if context_options else {}
        
        if not tool_outputs:
            return enhanced_options
        
        # Format tool results for context
        tool_context_parts = []
        
        for output in tool_outputs:
            if output.get('success'):
                tool_name = output['tool_name']
                result = output['result']
                
                if tool_name == "web_search":
                    # Format search results
                    if isinstance(result, dict) and result.get('results'):
                        search_summary = f"搜索查询: {result.get('query', 'N/A')}\n"
                        search_summary += f"找到 {len(result['results'])} 个结果:\n"
                        
                        for i, item in enumerate(result['results'][:3], 1):
                            search_summary += f"{i}. {item.get('title', 'No Title')}\n"
                            search_summary += f"   {item.get('snippet', 'No snippet')[:100]}...\n"
                        
                        tool_context_parts.append(f"## 网页搜索结果\n\n{search_summary}")
                
                elif tool_name == "database_query":
                    # Format database results
                    if isinstance(result, dict) and result.get('success'):
                        db_summary = f"数据库查询: {result.get('sql', 'N/A')}\n"
                        db_summary += f"返回 {result.get('row_count', 0)} 行数据\n"
                        
                        rows = result.get('rows', [])
                        if rows:
                            db_summary += "示例数据:\n"
                            for row in rows[:3]:
                                db_summary += f"  {str(row)}\n"
                        
                        tool_context_parts.append(f"## 数据库查询结果\n\n{db_summary}")
                
                elif tool_name == "file_operations":
                    # Format file operation results
                    if isinstance(result, dict) and result.get('success'):
                        op = result.get('operation', 'unknown')
                        path = result.get('path', 'N/A')
                        
                        if op == "read":
                            content = result.get('content', '')[:500]  # Limit content length
                            file_summary = f"文件读取 ({path}):\n{content}..."
                        else:
                            file_summary = f"文件操作 {op} 在 {path} 执行成功"
                        
                        tool_context_parts.append(f"## 文件操作结果\n\n{file_summary}")
        
        # Add tool context to existing context options
        if tool_context_parts:
            tool_context = "\n\n".join(tool_context_parts)
            
            # CRITICAL FIX: Properly integrate tool context into the context system
            # The base executor expects context in the bundle format
            enhanced_options["tool_enhanced"] = True
            
            # Add tool context as additional context sections
            existing_sections = enhanced_options.get("sections", [])
            
            # Create tool context sections
            tool_sections = []
            for i, part in enumerate(tool_context_parts):
                tool_sections.append({
                    "task_id": f"tool_{i}",
                    "name": f"Tool Result {i+1}",
                    "short_name": f"Tool_{i+1}",
                    "kind": "tool_result",
                    "content": part
                })
            
            # Merge with existing sections
            all_sections = existing_sections + tool_sections
            enhanced_options["sections"] = all_sections
            
            # Rebuild combined context
            combined_parts = []
            for section in all_sections:
                header = section.get("short_name") or section.get("name", "Unknown")
                content = section.get("content", "")
                combined_parts.append(f"## {header}\n\n{content}")
            
            # Add tool context to combined
            enhanced_options["combined"] = "\n\n".join(combined_parts)
            
            enhanced_options["tool_summary"] = {
                "total_tools_used": len(tool_outputs),
                "successful_tools": len([o for o in tool_outputs if o.get('success')]),
                "analysis": tool_analysis
            }
        
        return enhanced_options
    
    async def _record_tool_usage(self, task, routing_result: Dict[str, Any], 
                               tool_outputs: List[Dict[str, Any]], status: str):
        """Record tool usage for future learning and analysis"""
        try:
            task_id = task.get("id") if isinstance(task, dict) else task[0]
            
            # Create tool usage record
            usage_record = {
                "task_id": task_id,
                "routing_method": routing_result.get("routing_method", "pure_llm"),
                "confidence": routing_result.get("confidence", 0.0),
                "tools_used": len(tool_outputs),
                "successful_tools": len([o for o in tool_outputs if o.get('success')]),
                "execution_status": status,
                "tool_effectiveness": self._calculate_tool_effectiveness(tool_outputs, status)
            }
            
            # Log for analysis (could be stored in database for learning)
            logger.info(f"Tool usage recorded for task {task_id}: {usage_record}")
            
            # Store in database if repo supports it
            if hasattr(self.repo, 'store_tool_usage_log'):
                try:
                    self.repo.store_tool_usage_log(task_id, usage_record)
                except Exception as e:
                    logger.warning(f"Failed to store tool usage log: {e}")
        
        except Exception as e:
            logger.warning(f"Failed to record tool usage: {e}")
    
    def _calculate_tool_effectiveness(self, tool_outputs: List[Dict[str, Any]], status: str) -> float:
        """Calculate tool usage effectiveness score"""
        if not tool_outputs:
            return 0.0
        
        successful_tools = len([o for o in tool_outputs if o.get('success')])
        total_tools = len(tool_outputs)
        
        # Base effectiveness on tool success rate and task completion
        tool_success_rate = successful_tools / total_tools
        task_success_bonus = 0.2 if status == "done" else 0.0
        
        return min(tool_success_rate + task_success_bonus, 1.0)

    async def _execute_post_generation_tools(self, output_tools: List[Dict[str, Any]], task, task_id: int):
        """Execute tools that need the generated content (like file_operations)"""
        try:
            # Get the generated content
            generated_content = self.repo.get_task_output_content(task_id)
            if not generated_content:
                logger.warning(f"No content available for post-generation tools in task {task_id}")
                return
            
            logger.info(f"Executing {len(output_tools)} post-generation tools for task {task_id}")
            
            from tool_box import execute_tool
            
            for call in output_tools:
                try:
                    tool_name = call['tool_name']
                    parameters = call['parameters'].copy()  # Don't modify original
                    
                    if tool_name == 'file_operations':
                        # For file operations, use the generated content
                        if parameters.get('operation') == 'write':
                            # Update content with actual generated content
                            parameters['content'] = generated_content
                            
                            # Improve file path if needed
                            if not parameters.get('path') or parameters['path'] in ['report.txt', '']:
                                task_name = task.get('name', 'report')
                                if '因果推断' in task_name:
                                    parameters['path'] = '因果推断报告.md'
                                else:
                                    parameters['path'] = f'报告_{task_id}.md'
                    
                    logger.info(f"Executing post-generation tool {tool_name} for task {task_id}")
                    result = await execute_tool(tool_name, **parameters)
                    
                    if isinstance(result, dict) and result.get('success'):
                        logger.info(f"✅ Post-generation tool {tool_name} executed successfully")
                        if tool_name == 'file_operations' and result.get('operation') == 'write':
                            logger.info(f"📁 Report saved to: {result.get('path', 'unknown')}")
                    else:
                        logger.warning(f"⚠️ Post-generation tool {tool_name} had issues: {result}")
                        
                except Exception as e:
                    logger.error(f"❌ Post-generation tool {call['tool_name']} failed: {e}")
                    
        except Exception as e:
            logger.error(f"Failed to execute post-generation tools: {e}")


# Convenience function for enhanced execution
async def execute_task_with_tools(task, repo: Optional[TaskRepository] = None, 
                                use_context: bool = True, 
                                context_options: Optional[Dict[str, Any]] = None) -> str:
    """
    Execute task with tool enhancement
    
    This is the main entry point for tool-enhanced task execution.
    """
    executor = ToolEnhancedExecutor(repo)
    return await executor.execute_task(task, use_context, context_options)


# Integration helper for existing codebase
def create_tool_enhanced_context_options(base_options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Create enhanced context options that work well with tools"""
    options = dict(base_options) if base_options else {}
    
    # Optimize for tool integration
    options.update({
        "include_deps": True,  # Tools can benefit from dependency context
        "include_plan": True,  # Plan context helps tool selection
        "semantic_k": 3,       # Reduce semantic retrieval to save space for tool results
        "max_chars": 8000,     # Increase limit to accommodate tool results
        "per_section_max": 1500,  # Allow larger sections for tool outputs
        "strategy": "sentence" # Better summarization for tool integration
    })
    
    return options


# Backward compatibility wrapper
def execute_task_enhanced(task, repo=None, use_context=True, context_options=None):
    """Synchronous wrapper for backward compatibility"""
    try:
        # Always run in new event loop for compatibility with existing sync code
        try:
            # Check if there's an existing loop
            loop = asyncio.get_event_loop()
        except RuntimeError:
            # No event loop exists, we can create one
            loop = None
        
        if loop and loop.is_running():
            # We're in an async context - this is problematic for sync compatibility
            # Fall back to base execution to avoid blocking
            logger.warning("Cannot run async tool execution in running event loop, using base execution")
            return base_execute_task(task, repo, use_context, context_options)
        else:
            # We're not in an async context, safe to run
            return asyncio.run(
                execute_task_with_tools(task, repo, use_context, context_options)
            )
    except Exception as e:
        logger.error(f"Enhanced execution failed, falling back to base execution: {e}")
        # Fallback to base execution
        return base_execute_task(task, repo, use_context, context_options)


# New: combined path — tool-enhanced context + evaluation-driven execution
async def execute_task_with_tools_and_evaluation(
    task,
    repo: Optional[TaskRepository] = None,
    evaluation_mode: str = "llm",           # 'llm' | 'multi_expert' | 'adversarial'
    max_iterations: int = 3,
    quality_threshold: float = 0.8,
    use_context: bool = True,
    context_options: Optional[Dict[str, Any]] = None,
):
    """
    Execute with tool-enhanced context, then run evaluation-driven generation.

    Steps:
      1) Analyze tool needs and run info-gathering tools
      2) Enhance context options with tool outputs
      3) Run evaluation loop (LLM/multi-expert/adversarial)
      4) Execute post-generation output tools (e.g., write file)
    """
    repo = repo or default_repo

    # Initialize router and prepare tool-enhanced context
    executor = ToolEnhancedExecutor(repo)
    await executor.initialize()

    # Get task basic info
    task_id = task.get("id") if isinstance(task, dict) else task[0]
    task_name = task.get("name") if isinstance(task, dict) else task[1]

    # Build task prompt
    task_prompt = repo.get_task_input_prompt(task_id)
    if not task_prompt:
        task_prompt = f"Complete the task: {task_name}"

    # Phase 1: Analyze and route
    try:
        needs_tools, tool_analysis = await executor._analyze_tool_requirements(task_prompt, task)
    except Exception:
        needs_tools, tool_analysis = (False, {})

    info_gathering_tools = []
    output_tools = []
    routing_result = {"tool_calls": []}

    if needs_tools and executor.tool_router:
        routing_context = await executor._build_routing_context(task, context_options)
        try:
            routing_result = await executor.tool_router.route_request(task_prompt, routing_context)
        except Exception as e:
            logger.warning(f"Tool routing failed, continue without tools: {e}")
            routing_result = {"tool_calls": []}

        for call in routing_result.get("tool_calls", []):
            if call.get('tool_name') in ['web_search', 'database_query']:
                info_gathering_tools.append(call)
            elif call.get('tool_name') == 'file_operations':
                output_tools.append(call)

    # Phase 2: Execute info-gathering tools
    tool_outputs = []
    if info_gathering_tools:
        tool_outputs = await executor._execute_tool_calls(info_gathering_tools, task)

    # Phase 3: Enhance context with tool results
    enhanced_context_options = await executor._enhance_context_with_tools(
        context_options, tool_outputs, tool_analysis
    )

    # Phase 4: Evaluation-driven generation
    from .enhanced import (
        execute_task_with_evaluation as _exec_eval,
        execute_task_with_multi_expert_evaluation as _exec_multi,
        execute_task_with_adversarial_evaluation as _exec_adv,
    )

    mode = (evaluation_mode or "llm").strip().lower()
    if mode == "multi_expert":
        result = _exec_multi(
            task=task,
            repo=repo,
            max_iterations=max_iterations,
            quality_threshold=quality_threshold,
            use_context=use_context,
            context_options=enhanced_context_options,
        )
    elif mode == "adversarial":
        result = _exec_adv(
            task=task,
            repo=repo,
            max_iterations=max_iterations,
            quality_threshold=quality_threshold,
            use_context=use_context,
            context_options=enhanced_context_options,
        )
    else:  # default LLM evaluation
        result = _exec_eval(
            task=task,
            repo=repo,
            max_iterations=max_iterations,
            quality_threshold=quality_threshold,
            use_context=use_context,
            context_options=enhanced_context_options,
        )

    # Phase 5: Post-generation output tools
    if output_tools and getattr(result, 'status', None) in ("done", "completed"):
        try:
            await executor._execute_post_generation_tools(output_tools, task, task_id)
        except Exception as e:
            logger.warning(f"Post-generation tools failed: {e}")

    return result
