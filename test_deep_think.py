import asyncio
import sys
import logging
from typing import Dict, Any, List

# Add project root to path
sys.path.append('/Users/apple/LLM/agent')

from app.services.deep_think_agent import DeepThinkAgent, ThinkingStep

# Config logging
logging.basicConfig(level=logging.INFO)

async def mock_tool_executor(name: str, params: Dict[str, Any]) -> str:
    print(f"  [MockTool] Executing {name} with {params}")
    if name == "web_search":
        return "Python 3.12 was released on Oct 2, 2023. Key features include improved error messages, perf improvements, etc."
    return "Mock result"

async def mock_llm_client():
    class MockClient:
        async def chat_async(self, prompt: str, messages: List[Dict[str, str]] = None, **kwargs):
            messages = messages or []
            last_msg = messages[-1]["content"] if messages else prompt
            print(f"  [MockLLM] Received request with {len(messages)} messages")
            
            # Simple mock simulation of a thinking process
            # Check history to decide what to return
            history_len = len(messages)
            
            # Note: history also includes system prompt, so length is >= 2 usually.
            
            if history_len <= 2:
                # First turn: Think and call search
                return """<thinking>
I need to find out when Python 3.12 was released. I will use web_search.
</thinking>
<action>
{"tool": "web_search", "params": {"query": "Python 3.12 release date"}}
</action>"""
            
            elif history_len <= 4:
                # Second turn: Analyze search result and answer
                # History: [System, User, Assistant(Action), User(ToolResult)] -> 4
                return """<thinking>
The search result says Python 3.12 was released on Oct 2, 2023. I have enough information.
</thinking>
<ready_to_answer confidence="0.95">
Python 3.12 was released on October 2, 2023.
</ready_to_answer>"""
            
            return "Error: looped too many times"

    return MockClient()

async def test():
    print("Initializing DeepThinkAgent Test...")
    
    mock_llm = await mock_llm_client()
    
    async def on_thinking(step: ThinkingStep):
        print(f"  [Callback] Step {step.iteration}: {step.status} - Thought len: {len(step.thought)}")

    agent = DeepThinkAgent(
        llm_client=mock_llm,
        available_tools=["web_search"],
        tool_executor=mock_tool_executor,
        max_iterations=5,
        on_thinking=on_thinking
    )
    
    print("Running think()...")
    result = await agent.think("When was Python 3.12 released?")
    
    print("\n=== Result ===")
    print(f"Final Answer: {result.final_answer}")
    print(f"Summary: {result.thinking_summary}")
    print(f"Total Steps: {result.total_iterations}")
    print(f"Tools Used: {result.tools_used}")
    print(f"Confidence: {result.confidence}")
    
    assert "Oct" in result.final_answer
    assert result.total_iterations >= 2
    print("✅ Test Passed!")

if __name__ == "__main__":
    asyncio.run(test())
