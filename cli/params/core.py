"""Core workflow parameter definitions and parsing."""

from argparse import ArgumentParser
from typing import Any, Dict, Optional


class CoreParamsHandler:
    """Handler for core workflow parameters following SRP."""

    GROUP_NAME = "Core Workflow"

    @staticmethod
    def add_arguments(parser: ArgumentParser) -> None:
        """Add core workflow arguments to parser."""
        group = parser.add_argument_group(CoreParamsHandler.GROUP_NAME)

        # Primary workflow control
        group.add_argument("--goal", type=str, help="Project goal to plan for (required for new plans)")
        group.add_argument("--title", type=str, help="Plan title for creation or execution")

        # Execution modes
        group.add_argument("--plan-only", action="store_true", help="Generate plan without execution")
        group.add_argument("--execute-only", action="store_true", help="Execute existing plan without creating new one")

        # User interaction control
        group.add_argument("--yes", action="store_true", help="Auto-approve all prompts")
        group.add_argument("--no-open", action="store_true", help="Skip editor opening for plan review")

        # Schedule strategy
        group.add_argument(
            "--schedule", choices=["bfs", "dag", "postorder"], default="bfs", help="Task execution order (default: bfs)"
        )

        # Output configuration
        group.add_argument(
            "--output", type=str, default="output.md", help="Assembled output file path (default: output.md)"
        )

        # Interactive chat mode
        group.add_argument("--chat", action="store_true", help="Enter interactive chat mode")
        group.add_argument("--chat-provider", type=str, help="Chat provider override")
        group.add_argument("--chat-model", type=str, help="Chat model override")
        group.add_argument("--chat-max-turns", type=int, default=0, help="Autostop chat after N turns (0=unlimited)")
        group.add_argument("--chat-stream", action="store_true", help="Stream chat output (print incrementally)")
        group.add_argument("--chat-pretty", action="store_true", help="Pretty chat UI using rich panels")

    @staticmethod
    def extract_values(args) -> Dict[str, Any]:
        """Extract core parameter values from parsed args."""
        values = {}

        # Required parameters
        core_attrs = ["goal", "title", "output", "schedule", "chat_provider", "chat_model", "chat_max_turns"]
        for attr in core_attrs:
            if hasattr(args, attr):
                value = getattr(args, attr)
                if value is not None:
                    values[attr] = value

        # Boolean flags
        bool_attrs = ["plan_only", "execute_only", "yes", "no_open", "chat"]
        for attr in bool_attrs:
            if hasattr(args, attr) and getattr(args, attr):
                values[attr] = True

        return values

    @staticmethod
    def validate_values(values: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate core parameter combinations."""
        # Mutual exclusion checks
        if values.get("plan_only") and values.get("execute_only"):
            return False, "Cannot use --plan-only and --execute-only together"

        # Required parameter checks
        if values.get("execute_only") and not values.get("title"):
            return False, "--execute-only requires --title"

        return True, None

    @staticmethod
    def validate_for_plan_creation(values: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate parameters specifically for plan creation operations."""
        if not values.get("goal") and not values.get("execute_only"):
            return False, "Goal is required for plan creation (use --goal)"
        return True, None
