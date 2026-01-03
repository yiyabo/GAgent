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
    def __init__(
        self,
        llm_client: Any,
        available_tools: List[str],
        tool_executor: Callable[[str, Dict[str, Any]], Any],
        max_iterations: int = 10,
        on_thinking: Optional[Callable[[ThinkingStep], Any]] = None,
        on_thinking_delta: Optional[Callable[[int, str], Any]] = None,  # (iteration, delta_text) -> None
        on_final_delta: Optional[Callable[[str], Any]] = None,  # (delta_text) -> None
    ):
        self.llm_client = llm_client
        self.available_tools = available_tools
        self.tool_executor = tool_executor
        self.max_iterations = max_iterations
        self.on_thinking = on_thinking
        self.on_thinking_delta = on_thinking_delta
        self.on_final_delta = on_final_delta

    async def think(self, user_query: str, context: Optional[Dict[str, Any]] = None) -> DeepThinkResult:
        """
        Executes the deep thinking loop with streaming output.
        """
        context = context or {}
        thinking_steps: List[ThinkingStep] = []
        tools_used: List[str] = []
        
        # Initial system prompt construction
        system_prompt = self._build_system_prompt()
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"User Query: {user_query}"}
        ]

        iteration = 0
        final_answer = ""
        confidence = 0.0
        
        logger.info(f"Starting DeepThink for query: {user_query[:50]}...")

        while iteration < self.max_iterations:
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
                        current_step.action_result = f"Error: Tool '{tool_name}' is not available."
                    else:
                        if tool_name not in tools_used:
                            tools_used.append(tool_name)
                        try:
                            # Execute tool
                            result = await self.tool_executor(tool_name, tool_params)
                            current_step.action_result = str(result)
                        except Exception as e:
                            current_step.action_result = f"Error executing tool: {str(e)}"
                    
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

        # Generate summary
        summary = self._generate_summary(thinking_steps)

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

    def _build_system_prompt(self) -> str:
        """Constructs the system prompt for the Deep Think Agent."""
        tools_desc = "\n".join([f"- {t}" for t in self.available_tools])
        
        return f"""You are a Deep Thinking AI Assistant.
Your goal is to answer the user's question by performing a multi-step reasoning process.
You must NOT answer immediately. Instead, you should:
1. Analyze the user's request.
2. Think step-by-step about how to approach the problem.
3. Use tools if necessary to gather information.
4. Reflect on the information you have gathered.
5. Only when you have sufficient information, provide the final answer.

Available Tools:
{tools_desc}

Output Format:
You MUST structure your response as follows:

<thinking>
Your reasoning process here.
</thinking>

If you need to call a tool:
<action>
{{"tool": "tool_name", "params": {{"param1": "value1"}}}}
</action>

When you are ready to give the final answer:
<ready_to_answer confidence="0.0-1.0">
Your final answer here.
</ready_to_answer>

CRITICAL INSTRUCTIONS:
1. Do NOT include the final answer inside <thinking> tags. The <thinking> tag is ONLY for reasoning and planning.
2. If you have enough information to answer, you MUST use the <ready_to_answer> tag immediately.
3. Do not loop unnecessarily.
"""

    def _get_next_step_prompt(self, iteration: int) -> str:
        """Generate prompt for the next step, encouraging completion if steps are getting long."""
        if iteration > 8:
            return "You have taken many steps. Please consolidate your thinking and provide the final answer using <ready_to_answer>."
        elif iteration > 5:
            return "Scan your previous thoughts. If you have sufficient information, please output <ready_to_answer> now. Otherwise, continue thinking."
        else:
            return "Please continue thinking. If you are ready to answer, output <ready_to_answer>."

    def _parse_llm_response(self, response: str) -> Dict[str, Any]:
        """Parse the structured LLM response."""
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
        import re
        thinking_match = re.search(r'<thinking>(.*?)</thinking>', response, re.DOTALL)
        if thinking_match:
            result["thought"] = thinking_match.group(1).strip()
        
        # Check for final answer
        ready_match = re.search(r'<ready_to_answer\s+confidence=["\']?([\d.]+)["\']?>(.*?)</ready_to_answer>', response, re.DOTALL)
        if ready_match:
            result["is_final"] = True
            result["confidence"] = float(ready_match.group(1))
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

    def _generate_summary(self, steps: List[ThinkingStep]) -> str:
        """Generate a concise summary of the thinking process."""
        if not steps:
            return "No thinking steps recorded."
        
        tool_calls = [s for s in steps if s.action]
        return f"Processed {len(steps)} thinking steps using {len(tool_calls)} tool calls."
