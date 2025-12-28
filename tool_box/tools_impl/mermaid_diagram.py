"""
Mermaid Diagram Generator Tool

Generates Mermaid diagrams from task descriptions using LLM.
Supports flowcharts, sequence diagrams, Gantt charts, and more.
"""

import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)


async def mermaid_diagram_handler(
    task_description: str,
    diagram_type: str = "flowchart",
    style: str = "default",
    save_path: str = None,
) -> Dict[str, Any]:
    """
    Generate a Mermaid diagram based on task description.

    Args:
        task_description: Description of what to visualize
        diagram_type: Type of diagram (flowchart, sequence, gantt, er, class, state)
        style: Style preset (default, forest, dark, neutral)
        save_path: Optional path to save the diagram markdown

    Returns:
        Dict containing Mermaid code and metadata
    """
    try:
        from app.llm import get_default_client

        client = get_default_client()

        # Build prompt
        prompt = f"""Generate a Mermaid diagram for the following task:

Task Description: {task_description}

Diagram Type: {diagram_type}
Style: {style}

Requirements:
1. Generate ONLY valid Mermaid syntax
2. Use clear, concise labels
3. Follow Mermaid best practices
4. Include appropriate styling directives
5. Return ONLY the Mermaid code block, starting with ```mermaid and ending with ```

Examples:

**Flowchart Example:**
```mermaid
flowchart TD
    Start[Start] --> Process[Process Data]
    Process --> Decision{{Is Valid?}}
    Decision -->|Yes| Success[Success]
    Decision -->|No| Error[Error]
    Success --> End[End]
    Error --> End

    classDef successStyle fill:#ABD1BC,stroke:#22c55e
    classDef errorStyle fill:#FCB6A5,stroke:#ef4444
    class Success successStyle
    class Error errorStyle
```

**Sequence Diagram Example:**
```mermaid
sequenceDiagram
    participant User
    participant Frontend
    participant Backend
    participant Database

    User->>Frontend: Submit Request
    Frontend->>Backend: API Call
    Backend->>Database: Query Data
    Database-->>Backend: Return Results
    Backend-->>Frontend: JSON Response
    Frontend-->>User: Display Results
```

Now generate the Mermaid diagram:"""

        # Call LLM
        response = client.chat(prompt)

        # Extract Mermaid code
        mermaid_code = _extract_mermaid_code(response)

        # Save to file (if specified)
        if save_path:
            with open(save_path, 'w', encoding='utf-8') as f:
                f.write(f"```mermaid\n{mermaid_code}\n```\n")
            logger.info(f"Mermaid diagram saved to {save_path}")

        return {
            "success": True,
            "diagram_type": diagram_type,
            "mermaid_code": mermaid_code,
            "raw_response": response,
            "saved_to": save_path,
            "preview_url": f"https://mermaid.live/edit#pako:{_encode_mermaid(mermaid_code)}",
        }

    except Exception as e:
        logger.exception(f"Mermaid diagram generation failed: {e}")
        return {
            "success": False,
            "error": str(e),
            "task_description": task_description,
        }


def _extract_mermaid_code(response: str) -> str:
    """Extract Mermaid code from LLM response"""
    # Extract content between ```mermaid ... ```
    import re

    # Try to match code block
    pattern = r'```mermaid\s*(.*?)\s*```'
    match = re.search(pattern, response, re.DOTALL)

    if match:
        return match.group(1).strip()

    # If no code block markers found, return the entire response
    return response.strip()


def _encode_mermaid(code: str) -> str:
    """Encode Mermaid code for mermaid.live URL"""
    import base64
    import json

    # Mermaid Live Editor uses pako compression + base64
    # Simplified version: using only base64 (needs optimization to pako later)
    encoded = base64.b64encode(code.encode('utf-8')).decode('utf-8')
    return encoded


# ToolBox tool definition
mermaid_diagram_tool = {
    "name": "mermaid_diagram",
    "description": (
        "Generate Mermaid diagrams from task descriptions. "
        "Supports flowcharts, sequence diagrams, Gantt charts, ER diagrams, class diagrams, and state diagrams. "
        "Returns Mermaid syntax that can be rendered in Markdown or exported as images."
    ),
    "category": "visualization",
    "parameters": {
        "type": "object",
        "properties": {
            "task_description": {
                "type": "string",
                "description": "Description of what to visualize (e.g., 'Show the workflow for user authentication')"
            },
            "diagram_type": {
                "type": "string",
                "description": "Type of diagram: flowchart, sequence, gantt, er, class, state",
                "default": "flowchart"
            },
            "style": {
                "type": "string",
                "description": "Style preset: default, forest, dark, neutral",
                "default": "default"
            },
            "save_path": {
                "type": "string",
                "description": "Optional path to save the diagram (e.g., 'docs/architecture.md')"
            },
        },
        "required": ["task_description"]
    },
    "handler": mermaid_diagram_handler,
    "tags": ["visualization", "diagram", "mermaid", "flowchart"],
    "examples": [
        "Generate a flowchart showing the task execution pipeline",
        "Create a sequence diagram for the chat API flow",
    ],
}
