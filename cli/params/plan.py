"""Plan configuration parameter definitions and parsing."""

from argparse import ArgumentParser
from typing import Any, Dict, Optional


class PlanParamsHandler:
    """Handler for plan configuration parameters following SRP."""

    GROUP_NAME = "Plan Configuration"

    @staticmethod
    def add_arguments(parser: ArgumentParser) -> None:
        """Add plan configuration arguments to parser."""
        group = parser.add_argument_group(PlanParamsHandler.GROUP_NAME)

        # Plan structure parameters
        group.add_argument("--sections", type=int, help="Preferred number of tasks (AI decides if not specified)")
        group.add_argument("--style", type=str, help="Plan style (e.g., academic, concise, detailed)")
        group.add_argument("--notes", type=str, help="Additional notes/hints for plan generation")

        # Plan management operations
        group.add_argument("--load-plan", type=str, help="Load existing plan by title")
        group.add_argument("--list-plans", action="store_true", help="List all existing plans with task counts")

    @staticmethod
    def extract_values(args) -> Dict[str, Any]:
        """Extract plan parameter values from parsed args."""
        values = {}

        # Plan configuration
        plan_attrs = ["sections", "style", "notes", "load_plan"]
        for attr in plan_attrs:
            if hasattr(args, attr):
                value = getattr(args, attr)
                if value is not None:
                    values[attr] = value

        # Boolean flags
        if hasattr(args, "list_plans") and getattr(args, "list_plans"):
            values["list_plans"] = True

        return values

    @staticmethod
    def validate_values(values: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate plan parameter combinations."""
        # Validation rules
        sections = values.get("sections")
        if sections is not None and (sections <= 0 or sections > 20):
            return False, "Section count must be between 1 and 20"

        # Load plan validation
        load_plan = values.get("load_plan")
        if load_plan and (not load_plan.strip() or len(load_plan) > 100):
            return False, "Plan title must be non-empty and under 100 characters"

        return True, None

    @staticmethod
    def has_plan_operation(args) -> bool:
        """Check if any plan management operation is requested."""
        plan_ops = ["list_plans", "load_plan"]
        return any(hasattr(args, attr) and getattr(args, attr) for attr in plan_ops)
