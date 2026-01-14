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
        "claude_code": 1200,      # 10 分钟 - 代码执行可能很长
        "web_search": 180,        # 90 秒
        "document_reader": 200,   # 1 分钟
        "graph_rag": 600,         # 1 分钟
        "file_operations": 90,   # 30 秒 - 文件操作应该很快
        "vision_reader": 1200,    # 10 分钟 - 视觉模型处理 PDF
        "bio_tools": 86400,      # 24 小时 - 生物信息学工具不限制
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

        # 如果循环结束但没有生成最终答案，强制生成一个
        if not final_answer and thinking_steps:
            logger.info("[DEEP_THINK] Iterations exhausted, forcing conclusion...")
            
            # 构建强制结论的提示
            conclusion_prompt = """You have reached the thinking limit. Based on all the information you've gathered, 
you MUST now provide a final answer. Synthesize everything you've learned into a comprehensive response.

Respond with ONLY a JSON object:
{
  "thinking": "I've gathered the following key information: [summarize key findings]",
  "action": null,
  "final_answer": {"answer": "Your comprehensive answer based on all gathered information", "confidence": 0.7}
}"""
            
            messages.append({"role": "user", "content": conclusion_prompt})
            
            try:
                # 调用 LLM 获取强制结论
                if hasattr(self.llm_client, 'stream_chat_async'):
                    response_text = ""
                    async for delta in self.llm_client.stream_chat_async(prompt="", messages=messages):
                        response_text += delta
                        if self.on_thinking_delta:
                            await self._safe_delta_callback(iteration + 1, delta)
                else:
                    response_text = await self.llm_client.chat_async(prompt="", messages=messages, temperature=0.5)
                
                parsed = self._parse_llm_response(response_text)
                if parsed.get("is_final"):
                    final_answer = parsed.get("final_answer", "")
                    confidence = parsed.get("confidence", 0.7)
                    
                    # 发送最终答案
                    if self.on_final_delta and final_answer:
                        for char in final_answer:
                            await self._safe_final_delta_callback(char)
                            await asyncio.sleep(0.01)
                else:
                    # 如果还是没有提取到，使用 thinking 内容作为答案
                    final_answer = parsed.get("thought", "基于已收集的信息，我无法得出明确结论。请查看上面的思考过程了解详情。")
                    confidence = 0.5
                    
            except Exception as e:
                logger.error(f"Failed to generate forced conclusion: {e}")
                final_answer = "思考过程中收集了信息，但未能生成完整答案。请查看上面的思考步骤了解详情。"
                confidence = 0.3

        # Generate summary (使用 LLM 生成有意义的摘要)
        summary = await self._generate_summary(thinking_steps, user_query)

        return DeepThinkResult(
            final_answer=final_answer or "思考过程完成，但未生成明确答案。",
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
            "claude_code": "Execute Python/shell code. FALLBACK TOOL: Use this ONLY when bio_tools cannot handle the task (e.g., custom analysis scripts, complex data processing). For FASTA/FASTQ sequence stats or standard bioinformatics tasks, ALWAYS try bio_tools first. Params: {\"task\": \"description\"}",
            "web_search": "Search the internet for information. USE THIS ONLY for web-based queries, NOT for local files. Params: {\"query\": \"search query\"}",
            "graph_rag": "Query knowledge graph for structured information. Params: {\"query\": \"your question\", \"mode\": \"global|local|hybrid\"}",
            "file_operations": "File system operations: list directories, read/write files, copy/move/delete. USE THIS for quick directory listing or file reading. Params: {\"operation\": \"list|read|write|copy|move|delete\", \"path\": \"/path\"}",
            "vision_reader": "Read PDFs and images using vision model. For PDF reading and document understanding. After reading a document, YOU should analyze and summarize it directly - do not call other tools. Params: {\"operation\": \"read_pdf|read_image|ocr_page\", \"file_path\": \"/path/to/file\"}",
            "bio_tools": "PREFERRED for bioinformatics: Execute Docker-based tools for FASTA/FASTQ/sequence analysis. For sequence stats, use seqkit. Example: {\"tool_name\": \"seqkit\", \"operation\": \"stats\", \"input_file\": \"/absolute/path/to/file.fasta\"}. NOTE: input_file MUST be absolute path. Available tools: seqkit (stats, grep, seq), blast (blastn, blastp), prodigal (predict genes), hmmer (hmmscan), checkv (virus quality). Use operation='help' to see tool usage.",
        }
        
        tools_desc = []
        for t in self.available_tools:
            if t in tool_descriptions:
                tools_desc.append(f"- {t}: {tool_descriptions[t]}")
            else:
                tools_desc.append(f"- {t}")
        tools_text = "\n".join(tools_desc)
        
        base_prompt = f"""You are a Deep Thinking AI Assistant with UNLIMITED resources.
Your goal is to provide the MOST COMPREHENSIVE answer by using ALL available tools aggressively.

=== CORE PRINCIPLES ===
1. You have UNLIMITED tokens and API calls - DO NOT worry about costs.
2. Use MULTIPLE TOOLS to gather complete information from different angles.
3. Be THOROUGH - it's better to call 5 tools than to give an incomplete answer.
4. Each tool provides unique insights - combine them for best results.

=== THINKING PROCESS ===
1. Break down the user's question into sub-problems.
2. For EACH sub-problem, determine which tool(s) can help.
3. Call tools ONE BY ONE, analyze results, then decide what else is needed.
4. Synthesize information from ALL tool results into a comprehensive answer.
5. Only provide final_answer when you have gathered enough information.

=== AVAILABLE TOOLS ===
{tools_text}

=== MULTI-TOOL STRATEGY ===
For comprehensive analysis, consider this workflow:
1. **Explore**: Use file_operations(list) to see what files exist
2. **Read**: Use document_reader or file_operations(read) to read key files
3. **Analyze**: Use claude_code for complex data analysis or code execution
4. **Visualize**: Use claude_code to create charts/visualizations if needed
5. **Research**: Use web_search for external context or comparison
6. **Vision**: Use vision_reader for images, figures, or scanned documents
7. **Knowledge**: Use graph_rag for structured knowledge queries

=== TOOL SELECTION GUIDE ===
- LOCAL DIRECTORY LISTING: file_operations(operation="list", path="...")
- READ TEXT FILES: file_operations(operation="read") or document_reader
- COMPLEX ANALYSIS/CODE: claude_code - for any code execution, data analysis, visualization
- IMAGES/FIGURES/PDF: vision_reader - for OCR, figure description, equation reading
- WEB INFORMATION: web_search - for internet queries, background research
- KNOWLEDGE QUERIES: graph_rag - for structured knowledge retrieval

=== IMPORTANT ===
- DO NOT hesitate to use multiple tools - resources are unlimited!
- Call different tools to get different perspectives on the same data.
- If one tool's result is incomplete, try another tool for more info.
- For file analysis: first LIST the directory, then READ interesting files, then ANALYZE with code.

=== OUTPUT FORMAT ===
Respond with valid JSON only (no markdown fences):

{{
  "thinking": "Describe what you're analyzing and WHY you're choosing this tool...",
  "action": {{"tool": "tool_name", "params": {{...}}}},
  "final_answer": null
}}

When ready to answer (after using multiple tools):
{{
  "thinking": "Synthesizing information from [tool1], [tool2], [tool3]...",
  "action": null,
  "final_answer": {{"answer": "Comprehensive answer here", "confidence": 0.9}}
}}

=== RULES ===
1. Output valid JSON only.
2. Use "action" to call tools, one at a time per response.
3. Call MULTIPLE tools before providing final_answer.
4. Include which tools you used in your final answer's thinking.
5. Be thorough - incomplete answers are worse than using more tools.
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
