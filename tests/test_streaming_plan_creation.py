import sys
import os
import pytest
import httpx
import json
from unittest.mock import patch, MagicMock

# Add the project root to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.main import app

# Mock LLM responses for the planner
MOCK_TITLE_RESPONSE = "Test Plan for Bacterial Virulence"
MOCK_ROOT_TASKS_RESPONSE = json.dumps({
    "title": "Test Plan",
    "tasks": [
        {"name": "Task 1: Literature Review", "prompt": "Review existing literature on prophage genes.", "task_type": "composite"},
        {"name": "Task 2: Experimental Design", "prompt": "Design experiments to test gene function.", "task_type": "atomic"}
    ]
})
MOCK_DECOMPOSE_RESPONSE = json.dumps({
    "task_name": "Task 1: Literature Review",
    "evaluated_task_type": "composite",
    "reasoning": "Literature review is a multi-step process.",
    "tasks": [
        {"name": "Subtask 1.1: Database Search", "prompt": "Search PubMed and other databases.", "task_type": "atomic"},
        {"name": "Subtask 1.2: Summarize Findings", "prompt": "Summarize key papers.", "task_type": "atomic"}
    ]
})
MOCK_ATOMIC_RESPONSE = json.dumps({
    "task_name": "Task 2: Experimental Design",
    "evaluated_task_type": "atomic",
    "reasoning": "This is a single, actionable step.",
    "tasks": []
})

@pytest.mark.asyncio
async def test_plan_creation_streaming():
    """
    Tests the full SSE streaming plan creation process from the API endpoint.
    """
    # Mock the LLM client's chat method to return different responses based on the prompt
    def mock_chat_method(prompt: str):
        if "Generate a concise, descriptive title" in prompt:
            return MOCK_TITLE_RESPONSE
        elif "Analyze this goal and create the optimal root-level tasks" in prompt:
            return MOCK_ROOT_TASKS_RESPONSE
        elif "re-evaluating ONE task" in prompt and "Literature Review" in prompt:
            return MOCK_DECOMPOSE_RESPONSE
        elif "re-evaluating ONE task" in prompt and "Experimental Design" in prompt:
            return MOCK_ATOMIC_RESPONSE
        return "{}" # Default empty response

    # We patch the get_default_client function to return a mock client
    mock_llm_client = MagicMock()
    mock_llm_client.chat.side_effect = mock_chat_method

    with patch('app.services.planning.get_default_client', return_value=mock_llm_client):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            goal = "Understand the role of prophage genes in bacterial virulence"
            
            # Make the streaming request
            async with client.stream("GET", f"/chat/plans/propose-stream?goal={goal}") as response:
                assert response.status_code == 200
                assert "text/event-stream" in response.headers["content-type"]

                # Collect all events from the stream
                events = []
                raw_data = ""
                async for chunk in response.aiter_bytes():
                    raw_data += chunk.decode('utf-8')
                    # SSE messages are separated by double newlines
                    while '\n\n' in raw_data:
                        message, raw_data = raw_data.split('\n\n', 1)
                        if message.startswith('data: '):
                            try:
                                event_data = json.loads(message.replace('data: ', ''))
                                events.append(event_data)
                            except json.JSONDecodeError:
                                pytest.fail(f"Failed to parse JSON from stream: {message}")
                
                # --- Assertions ---
                assert len(events) > 5, "Should have received multiple streaming events"

                # Check for key stages in the received events
                stages = [e.get('stage') for e in events]
                assert 'initialization' in stages
                assert 'plan_created' in stages
                assert 'generating_root_tasks' in stages
                assert 'root_tasks_generated' in stages
                assert 'root_task_created' in stages
                assert 'processing_layer' in stages
                assert 'evaluating_task' in stages
                assert 'decomposing_task' in stages
                assert 'subtask_created' in stages
                assert 'task_decomposed' in stages
                assert 'task_marked_atomic' in stages
                
                # The last event should be the completion event
                completion_event = next((e for e in events if e.get('stage') == 'completed'), None)
                assert completion_event is not None, "Completion event not found"
                
                # Check the content of the completion event
                result = completion_event.get('result', {})
                assert result.get('success') is True
                assert result.get('total_tasks') == 4 # 2 root + 2 subtasks
                assert result.get('max_layer') == 1
                assert result.get('title') == MOCK_TITLE_RESPONSE
                
                # Verify that the plan and tasks were created (simplified check)
                plan_id = result.get('plan_id')
                assert isinstance(plan_id, int)

                # Check that the number of created tasks matches the plan
                root_task_created_events = [e for e in events if e.get('stage') == 'root_task_created']
                subtask_created_events = [e for e in events if e.get('stage') == 'subtask_created']
                assert len(root_task_created_events) == 2
                assert len(subtask_created_events) == 2