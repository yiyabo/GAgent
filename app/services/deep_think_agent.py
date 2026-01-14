import logging
import json
import asyncio
from datetime import datetime
from typing import List, Dict, Any, Optional, Callable, Union, AsyncIterator

from dataclasses import dataclass

from app.llm import get_default_client

logger = logging.getLogger(__name__)

@dataclass
class ThinkingStep:
    """Represents a single step in the thinking process."""
    iteration: int
    thought: str           # The reasoning content
    action: Optional[str]  # Tool call JSON string or description
    action_result: Optional[str]  # Result from the tool
    self_correction: Optional[str]  # Any self-correction made during this step
    timestamp: datetime = datetime.now()
    status: str = "thinking" # thinking, calling_tool, analyzing, done, error

@dataclass
class DeepThinkResult:
    """The final result of the deep thinking process."""
    final_answer: str
    thinking_steps: List[ThinkingStep]
    total_iterations: int
    tools_used: List[str]
    confidence: float  # 0.0 to 1.0
    thinking_summary: str  # A concise summary for the user

class DeepThinkAgent:
    """
    Agent that performs multi-step reasoning and tool calling before answering.
    Supports streaming output for real-time display of thinking process.
    """
    
    # 默认工具执行超时时间（秒）
    DEFAULT_TOOL_TIMEOUT = 60
    
    # 按工具类型设置不同超时（秒）
    TOOL_TIMEOUTS = {
        "claude_code": 600,      # 10 分钟 - 代码执行可能很长
        "web_search": 90,        # 90 秒
        "document_reader": 60,   # 1 分钟
        "graph_rag": 60,         # 1 分钟
        "file_operations": 30,   # 30 秒 - 文件操作应该很快
        "vision_reader": 120,    # 2 分钟 - 视觉模型调用
    }
    
    def __init__(
        self,
        llm_client: Any,
        available_tools: List[str],
        tool_executor: Callable[[str, Dict[str, Any]], Any],
        max_iterations: int = 10,
        tool_timeout: int = DEFAULT_TOOL_TIMEOUT,
        cancel_event: Optional[asyncio.Event] = None,
        on_thinking: Optional[Callable[[ThinkingStep], Any]] = None,
        on_thinking_delta: Optional[Callable[[int, str], Any]] = None,  # (iteration, delta_text) -> None
        on_final_delta: Optional[Callable[[str], Any]] = None,  # (delta_text) -> None
    ):
        self.llm_client = llm_client
        self.available_tools = available_tools
        self.tool_executor = tool_executor
        self.max_iterations = max_iterations
        self.tool_timeout = tool_timeout
        self.cancel_event = cancel_event
        self.on_thinking = on_thinking
        self.on_thinking_delta = on_thinking_delta
        self.on_final_delta = on_final_delta

    async def think(self, user_query: str, context: Optional[Dict[str, Any]] = None) -> DeepThinkResult:
        """
        Executes the deep thinking loop with streaming output.
        
        Args:
            user_query: The user's question/request
            context: Optional context including chat_history, session_id, etc.
            
        Returns:
            DeepThinkResult with final answer and thinking steps
            
        Raises:
            ValueError: If user_query is empty or too long
        """
        # 输入验证
        if not user_query or not user_query.strip():
            raise ValueError("User query cannot be empty")
        if len(user_query) > 10000:
            raise ValueError("User query too long (max 10000 chars)")
        
        user_query = user_query.strip()
        context = context or {}
        thinking_steps: List[ThinkingStep] = []
        tools_used: List[str] = []
        cancelled = False
        
        # 构建 System Prompt（传入上下文以支持对话历史）
        system_prompt = self._build_system_prompt(context)
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"User Query: {user_query}"}
        ]

        iteration = 0
        final_answer = ""
        confidence = 0.0
        
        logger.info(f"Starting DeepThink for query: {user_query[:50]}...")

        while iteration < self.max_iterations:
            # 检查取消
            if self.cancel_event and self.cancel_event.is_set():
                logger.info("DeepThink cancelled by user")
                cancelled = True
                break
            
            iteration += 1
            
            try:
                # Create a temporary step object
                current_step = ThinkingStep(
                    iteration=iteration,
                    thought="",
                    action=None,
                    action_result=None,
                    self_correction=None,
                    timestamp=datetime.now(),
                    status="thinking"
                )
                
                # Notify start of new step
                if self.on_thinking:
                    await self._safe_callback(current_step)

                # Use streaming LLM call to get response token by token
                response_text = ""
                
                # Check if LLM client supports streaming
                has_stream = hasattr(self.llm_client, 'stream_chat_async')
                logger.info(f"[DEEP_THINK] LLM streaming check: has_stream_chat_async={has_stream}")
                
                if has_stream:
                    logger.info("[DEEP_THINK] Using streaming LLM call")
                    async for delta in self.llm_client.stream_chat_async(prompt="", messages=messages):
                        response_text += delta
                        # Send delta to frontend
                        if self.on_thinking_delta:
                            await self._safe_delta_callback(iteration, delta)
                else:
                    # Fallback to non-streaming
                    logger.info("[DEEP_THINK] Fallback to non-streaming LLM call")
                    response_text = await self.llm_client.chat_async(prompt="", messages=messages, temperature=0.7)

                # Parse response
                parsed = self._parse_llm_response(response_text)
                current_step.thought = parsed.get("thought", "")
                current_step.action = parsed.get("action_str", None)
                
                # Check for final answer
                if parsed.get("is_final"):
                    final_answer = parsed.get("final_answer", "")
                    confidence = parsed.get("confidence", 0.8)
                    current_step.status = "done"
                    thinking_steps.append(current_step)
                    if self.on_thinking:
                        await self._safe_callback(current_step)
                    
                    # Stream final answer if callback provided
                    if self.on_final_delta and final_answer:
                        # Send final answer as stream
                        for char in final_answer:
                            await self._safe_final_delta_callback(char)
                            await asyncio.sleep(0.01)  # Small delay for visual effect
                    break
                
                # Handle Action
                if current_step.action:
                    current_step.status = "calling_tool"
                    thinking_steps.append(current_step)
                    if self.on_thinking:
                        await self._safe_callback(current_step)
                        
                    tool_name = parsed.get("tool_name")
                    tool_params = parsed.get("tool_params")
                    
                    if tool_name not in self.available_tools:
                        current_step.action_result = f"Error: Tool '{tool_name}' is not available. Available: {self.available_tools}"
                    elif tool_params is not None and not isinstance(tool_params, dict):
                        current_step.action_result = f"Error: Tool params must be a dict, got {type(tool_params).__name__}"
                    else:
                        if tool_name not in tools_used:
                            tools_used.append(tool_name)
                        try:
                            # 根据工具类型获取超时时间
                            timeout = self.TOOL_TIMEOUTS.get(tool_name, self.tool_timeout)
                            
                            # Execute tool with timeout
                            result = await asyncio.wait_for(
                                self.tool_executor(tool_name, tool_params or {}),
                                timeout=timeout
                            )
                            current_step.action_result = str(result)
                        except asyncio.TimeoutError:
                            timeout = self.TOOL_TIMEOUTS.get(tool_name, self.tool_timeout)
                            current_step.action_result = f"Error: Tool '{tool_name}' execution timed out after {timeout}s"
                            logger.warning(f"Tool {tool_name} timed out after {timeout}s")
                        except Exception as e:
                            current_step.action_result = f"Error executing tool: {str(e)}"
                            logger.exception(f"Tool {tool_name} execution failed")
                    
                    # Update conversation history with result
                    messages.append({"role": "assistant", "content": response_text})
                    messages.append({"role": "user", "content": f"Tool Output: {current_step.action_result}"})
                    
                    current_step.status = "analyzing"
                    # Update step with action result
                    if self.on_thinking:
                        await self._safe_callback(current_step)
                    
                else:
                    # Pure thinking step or self-correction without action
                    thinking_steps.append(current_step)
                    if self.on_thinking:
                        await self._safe_callback(current_step)
                    messages.append({"role": "assistant", "content": response_text})
                    messages.append({"role": "user", "content": self._get_next_step_prompt(iteration)})

            except Exception as e:
                logger.error(f"Error in deep thinking loop: {e}")
                current_step.status = "error"
                current_step.thought = f"Error: {str(e)}"
                thinking_steps.append(current_step)
                if self.on_thinking:
                    await self._safe_callback(current_step)
                continue

        # Generate summary (使用 LLM 生成有意义的摘要)
        summary = await self._generate_summary(thinking_steps, user_query)

        return DeepThinkResult(
            final_answer=final_answer or "I could not generate a definitive answer after deep thinking.",
            thinking_steps=thinking_steps,
            total_iterations=iteration,
            tools_used=tools_used,
            confidence=confidence,
            thinking_summary=summary
        )

    async def _safe_callback(self, step: ThinkingStep):
        if self.on_thinking:
            try:
                if asyncio.iscoroutinefunction(self.on_thinking):
                    await self.on_thinking(step)
                else:
                    self.on_thinking(step)
            except Exception as e:
                logger.error(f"Error in on_thinking callback: {e}")

    async def _safe_delta_callback(self, iteration: int, delta: str):
        if self.on_thinking_delta:
            try:
                if asyncio.iscoroutinefunction(self.on_thinking_delta):
                    await self.on_thinking_delta(iteration, delta)
                else:
                    self.on_thinking_delta(iteration, delta)
            except Exception as e:
                logger.error(f"Error in on_thinking_delta callback: {e}")

    async def _safe_final_delta_callback(self, delta: str):
        if self.on_final_delta:
            try:
                if asyncio.iscoroutinefunction(self.on_final_delta):
                    await self.on_final_delta(delta)
                else:
                    self.on_final_delta(delta)
            except Exception as e:
                logger.error(f"Error in on_final_delta callback: {e}")

    def _build_system_prompt(self, context: Optional[Dict[str, Any]] = None) -> str:
        """Constructs the system prompt for the Deep Think Agent."""
        # 构建工具详细描述
        tool_descriptions = {
            "claude_code": "Execute code, access local files, run shell commands. USE THIS for complex analysis, running scripts, code execution. Params: {\"task\": \"description of what to do\"}",
            "web_search": "Search the internet for information. USE THIS ONLY for web-based queries, NOT for local files. Params: {\"query\": \"search query\"}",
            "document_reader": "Read a specific file content. Requires exact file path. Params: {\"operation\": \"read_any\", \"file_path\": \"/path/to/file\"}",
            "graph_rag": "Query knowledge graph for structured information. Params: {\"query\": \"your question\", \"mode\": \"global|local|hybrid\"}",
            "file_operations": "File system operations: list directories, read/write files, copy/move/delete. USE THIS for quick directory listing or file reading. Params: {\"operation\": \"list|read|write|copy|move|delete\", \"path\": \"/path\"}",
            "vision_reader": "Vision-based reading using multimodal AI (qwen3-vl). OCR pages, read equations, describe figures. Params: {\"operation\": \"ocr_page|read_equation_image|describe_figure|extract_table\", \"image_path\": \"/path/to/image\"}",
        }
        
        tools_desc = []
        for t in self.available_tools:
            if t in tool_descriptions:
                tools_desc.append(f"- {t}: {tool_descriptions[t]}")
            else:
                tools_desc.append(f"- {t}")
        tools_text = "\n".join(tools_desc)
        
        base_prompt = f"""You are a Deep Thinking AI Assistant.
Your goal is to answer the user's question by performing a multi-step reasoning process.
You must NOT answer immediately. Instead, you should:
1. Analyze the user's request.
2. Think step-by-step about how to approach the problem.
3. Use tools if necessary to gather information.
4. Reflect on the information you have gathered.
5. Only when you have sufficient information, provide the final answer.

=== AVAILABLE TOOLS ===
{tools_text}

=== IMPORTANT TOOL SELECTION GUIDELINES ===
- For LOCAL FILES/DIRECTORIES: Use "claude_code" with task like "list files in data/experiment_1" or "read and analyze files in data/xxx"
- For INTERNET SEARCH: Use "web_search" ONLY for web-based queries
- For READING SPECIFIC FILE: Use "document_reader" with exact file path
- For KNOWLEDGE BASE: Use "graph_rag" for structured knowledge queries

=== OUTPUT FORMAT ===
You MUST respond with a valid JSON object (no markdown fences). Use this structure:

{{
  "thinking": "Your reasoning process here. Explain your analysis step by step.",
  "action": null,
  "final_answer": null
}}

If you need to call a tool, include the action:
{{
  "thinking": "I need to search for more information about...",
  "action": {{"tool": "tool_name", "params": {{"param1": "value1"}}}},
  "final_answer": null
}}

When you are ready to give the final answer:
{{
  "thinking": "Based on my analysis...",
  "action": null,
  "final_answer": {{"answer": "Your complete answer here", "confidence": 0.9}}
}}

=== CRITICAL RULES ===
1. Always output valid JSON only. No markdown code fences.
2. The "thinking" field should contain your reasoning process.
3. Use "action" when you need to call a tool. Set to null otherwise.
4. Use "final_answer" only when you have enough information. Set to null otherwise.
5. Do NOT include your answer in "thinking". Put it in "final_answer" only.
6. Do not loop unnecessarily - provide final_answer when ready.
"""
        
        # 添加对话历史上下文
        if context:
            chat_history = context.get("chat_history", [])
            if chat_history:
                history_lines = []
                recent = chat_history[-6:] if len(chat_history) > 6 else chat_history
                for msg in recent:
                    role = msg.get("role", "unknown")
                    content = msg.get("content", "")
                    if len(content) > 500:
                        content = content[:500] + "..."
                    history_lines.append(f"[{role}]: {content}")
                base_prompt += f"\n=== RECENT CONVERSATION ===\n" + "\n".join(history_lines)
        
        return base_prompt

    def _get_next_step_prompt(self, iteration: int) -> str:
        """Generate prompt for the next step, encouraging completion if steps are getting long."""
        if iteration > 8:
            return 'You have taken many steps. Please consolidate your thinking and provide the final answer using "final_answer" in your JSON response.'
        elif iteration > 5:
            return 'Scan your previous thoughts. If you have sufficient information, please output "final_answer" now. Otherwise, continue thinking.'
        else:
            return 'Please continue thinking. If you are ready to answer, include "final_answer" in your JSON response.'

    def _parse_llm_response(self, response: str) -> Dict[str, Any]:
        """Parse the structured LLM response. Tries JSON first, falls back to XML."""
        result = {
            "thought": "",
            "is_final": False,
            "final_answer": "",
            "confidence": 0.0,
            "tool_name": None,
            "tool_params": None,
            "action_str": None,
        }
        
        # 1. 尝试 JSON 解析
        try:
            json_str = self._extract_json(response)
            parsed = json.loads(json_str)
            
            # 提取 thinking
            result["thought"] = parsed.get("thinking", "")
            
            # 检查 final_answer
            final_ans = parsed.get("final_answer")
            if final_ans and isinstance(final_ans, dict):
                result["is_final"] = True
                result["final_answer"] = final_ans.get("answer", "")
                result["confidence"] = min(max(float(final_ans.get("confidence", 0.8)), 0.0), 1.0)
                return result
            
            # 检查 action
            action = parsed.get("action")
            if action and isinstance(action, dict):
                result["tool_name"] = action.get("tool")
                result["tool_params"] = action.get("params", {})
                result["action_str"] = json.dumps(action)
            
            return result
            
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.debug(f"JSON parsing failed, trying XML fallback: {e}")
        
        # 2. Fallback: XML 解析（向后兼容）
        return self._parse_xml_fallback(response)
    
    def _extract_json(self, text: str) -> str:
        """Extract JSON object from potentially mixed content."""
        import re
        
        # 移除 markdown code fences
        text = re.sub(r'```json\s*', '', text)
        text = re.sub(r'```\s*', '', text)
        text = text.strip()
        
        # 尝试找到 JSON 对象
        # 匹配最外层的花括号
        brace_count = 0
        start_idx = -1
        for i, char in enumerate(text):
            if char == '{':
                if brace_count == 0:
                    start_idx = i
                brace_count += 1
            elif char == '}':
                brace_count -= 1
                if brace_count == 0 and start_idx >= 0:
                    return text[start_idx:i+1]
        
        # 如果没找到完整的 JSON，返回原始文本
        return text
    
    def _parse_xml_fallback(self, response: str) -> Dict[str, Any]:
        """Parse XML-formatted response (backwards compatibility)."""
        import re
        result = {
            "thought": "",
            "is_final": False,
            "final_answer": "",
            "confidence": 0.0,
            "tool_name": None,
            "tool_params": None,
            "action_str": None,
        }
        
        # Extract thinking
        thinking_match = re.search(r'<thinking>(.*?)</thinking>', response, re.DOTALL)
        if thinking_match:
            result["thought"] = thinking_match.group(1).strip()
        
        # Check for final answer
        ready_match = re.search(r'<ready_to_answer\s+confidence=["\']?([\d.]+)["\']?>(.*?)</ready_to_answer>', response, re.DOTALL)
        if ready_match:
            result["is_final"] = True
            result["confidence"] = min(max(float(ready_match.group(1)), 0.0), 1.0)
            result["final_answer"] = ready_match.group(2).strip()
            return result
        
        # Check for action
        action_match = re.search(r'<action>(.*?)</action>', response, re.DOTALL)
        if action_match:
            action_str = action_match.group(1).strip()
            result["action_str"] = action_str
            try:
                action_obj = json.loads(action_str)
                result["tool_name"] = action_obj.get("tool")
                result["tool_params"] = action_obj.get("params", {})
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse action JSON: {action_str}")
        
        return result

    async def _generate_summary(self, steps: List[ThinkingStep], user_query: str) -> str:
        """Generate a meaningful summary of the thinking process using LLM.
        
        Args:
            steps: List of thinking steps
            user_query: Original user query for context
            
        Returns:
            A concise summary string
        """
        if not steps:
            return "No thinking steps recorded."
        
        # 收集工具使用信息
        tool_calls = [s for s in steps if s.action]
        tools_used = []
        for s in tool_calls:
            if s.action and '"tool":' in s.action:
                try:
                    tools_used.append(s.action.split('"tool":')[1].split('"')[1])
                except (IndexError, AttributeError):
                    pass
        tools_used = list(set(tools_used))
        
        # 构建思考步骤摘要
        steps_text = ""
        for s in steps[:5]:  # 最多 5 步
            thought_preview = s.thought[:150] + "..." if len(s.thought) > 150 else s.thought
            steps_text += f"- Step {s.iteration}: {thought_preview}\n"
        
        # 尝试使用 LLM 生成摘要
        try:
            if hasattr(self.llm_client, 'chat_async'):
                prompt = f"""Summarize this thinking process in 1-2 sentences (Chinese preferred):
User Question: {user_query[:200]}
Thinking Steps:
{steps_text}
Tools Used: {', '.join(tools_used) if tools_used else 'None'}

Summary:"""
                summary = await asyncio.wait_for(
                    self.llm_client.chat_async(prompt=prompt, max_tokens=150),
                    timeout=10  # 10秒超时
                )
                if summary and len(summary.strip()) > 10:
                    return summary.strip()
        except Exception as e:
            logger.debug(f"LLM summary generation failed: {e}")
        
        # Fallback: 简单摘要
        if tools_used:
            tools_str = ", ".join(tools_used)
            return f"Completed {len(steps)} reasoning steps, used tools: {tools_str}."
        else:
            return f"Completed {len(steps)} reasoning steps through pure analysis."
