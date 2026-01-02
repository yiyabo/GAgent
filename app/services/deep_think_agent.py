import logging
import json
import asyncio
from datetime import datetime
from typing import List, Dict, Any, Optional, Callable, Union
from dataclasses import dataclass

from app.llm import get_default_client

# Assuming we can import ToolBox or have a way to execute tools. 
# For now, we will assume a callback or interface for tool execution.

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
    status: str = "thinking" # thinking, calling_tool, analyzing, error

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
    """
    def __init__(
        self,
        llm_client: Any, # Typed as Any for now, should be the LLM client protocol
        available_tools: List[str],
        tool_executor: Callable[[str, Dict[str, Any]], Any], # Async callback (tool_name, params) -> result
        max_iterations: int = 10,
        on_thinking: Optional[Callable[[ThinkingStep], Any]] = None,
    ):
        self.llm_client = llm_client
        self.available_tools = available_tools
        self.tool_executor = tool_executor
        self.max_iterations = max_iterations
        self.on_thinking = on_thinking

    async def think(self, user_query: str, context: Optional[Dict[str, Any]] = None) -> DeepThinkResult:
        """
        Executes the deep thinking loop.
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
            
            # 1. Parsing LLM output
            try:
                # Call LLM
                # Pass messages in kwargs, prompt can be empty if messages provided
                response_text = await self.llm_client.chat_async(prompt="", messages=messages, temperature=0.7)
                
                # Create a temporary step object
                current_step = ThinkingStep(
                    iteration=iteration,
                    thought="",
                    action=None,
                    action_result=None,
                    self_correction=None,
                    timestamp=datetime.now()
                )

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
                    break
                
                # Handle Action
                if current_step.action:
                    current_step.status = "calling_tool"
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
                    
                else:
                    # Pure thinking step or self-correction without action
                    messages.append({"role": "assistant", "content": response_text})
                    messages.append({"role": "user", "content": "Please continue thinking. If you are ready to answer, output <ready_to_answer>."})
                
                thinking_steps.append(current_step)
                if self.on_thinking:
                    await self._safe_callback(current_step)

            except Exception as e:
                logger.error(f"Error in deep thinking loop: {e}")
                iteration += 1 # Ensure we don't get stuck
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

    def _build_system_prompt(self) -> str:
        """Constructs the system prompt for the Deep Think Agent."""
        tools_desc = "\n".join([f"- {t}" for t in self.available_tools])
        
        return f"""You are a Deep Thinking AI Assistant.
Your goal is to answer the user's question by performing a multi-step reasoning process.
You must NOT answer immediately. Instead, you should:
1. Analyze the user's request.
2. Formulate a plan.
3. Use tools to gather information if needed.
4. Verify the information.
5. Reflect on your findings and correct yourself if necessary.

IMPORTANT: You must use the available tools to verify facts or gather new information before answering complex queries. Do not hallucinate tools.

Available Tools:
{tools_desc}

Response Format:
You must output your thought process in XML-like tags. 
Do not output markdown code blocks around the tags.

Format for THINKING (with optional ACTION):
<thinking>
Detailed thought process here...
</thinking>

<action>
{{"tool": "tool_name", "params": {{...}}}}
</action>

Format for FINAL ANSWER:
<thinking>
Final wrap up thinking...
</thinking>

<ready_to_answer confidence="0.9">
Your final answer or summary of findings.
</ready_to_answer>
"""

    def _parse_llm_response(self, text: str) -> Dict[str, Any]:
        """
        Parses the raw text response from LLM into a structured dict.
        Returns keys: 'thought', 'action_str', 'tool_name', 'tool_params', 'is_final', 'final_answer', 'confidence'
        """
        logger.debug(f"Parsing LLM response: {text[:200]}...") # Log partial response for debug
        result = {
            "thought": "",
            "action_str": None,
            "tool_name": None,
            "tool_params": {},
            "is_final": False,
            "final_answer": "",
            "confidence": 0.0
        }
        
        # Basic parsing using string searching (could be improved with regex or robust parser)
        # 1. Extract Thinking
        if "<thinking>" in text:
            start = text.find("<thinking>") + len("<thinking>")
            end = text.find("</thinking>")
            if end > start:
                result["thought"] = text[start:end].strip()
        
        # 2. Extract Action
        if "<action>" in text:
            start = text.find("<action>") + len("<action>")
            end = text.find("</action>")
            if end > start:
                action_json = text[start:end].strip()
                result["action_str"] = action_json
                try:
                    # Try interpreting as JSON
                    # Sometimes LLM adds markdown code blocks around json
                    clean_json = action_json.replace("```json", "").replace("```", "").strip()
                    action_data = json.loads(clean_json)
                    result["tool_name"] = action_data.get("tool")
                    result["tool_params"] = action_data.get("params", {})
                except Exception:
                    logger.warning(f"Failed to parse action JSON: {action_json}")
        
        # 3. Extract Final Answer
        if "<ready_to_answer" in text:
            start_tag_end = text.find(">") # This might be risky if attributes exist
            # Better find for the specified tag 
            start_idx = text.find("<ready_to_answer")
            content_start = text.find(">", start_idx) + 1
            end_idx = text.find("</ready_to_answer>")
            
            if end_idx > content_start:
                result["is_final"] = True
                result["final_answer"] = text[content_start:end_idx].strip()
                
                # Try to parse confidence
                tag_content = text[start_idx:content_start-1]
                if 'confidence="' in tag_content:
                    try:
                        conf_str = tag_content.split('confidence="')[1].split('"')[0]
                        result["confidence"] = float(conf_str)
                    except:
                        pass
        
        # Fallback: if no tags found, treat as thought
        if not result["thought"] and not result["action_str"] and not result["is_final"]:
            result["thought"] = text
            
        return result

    def _generate_summary(self, steps: List[ThinkingStep]) -> str:
        """Generates a concise summary of the thinking process."""
        return f"Processed {len(steps)} thinking steps using {len([s for s in steps if s.action])} tool calls."

    async def _safe_callback(self, step: ThinkingStep):
        if self.on_thinking:
            try:
                if asyncio.iscoroutinefunction(self.on_thinking):
                    await self.on_thinking(step)
                else:
                    self.on_thinking(step)
            except Exception as e:
                logger.error(f"Error in on_thinking callback: {e}")
