"""Refactored modular argument parser with separated parameter groups."""

import argparse
from typing import Any, Dict, List, Optional, Tuple

from .params import (
    ContextParamsHandler,
    CoreParamsHandler,
    DatabaseParamsHandler,
    EvaluationParamsHandler,
    PlanParamsHandler,
    UtilityParamsHandler,
)


class ModularCLIParser:
    """
    Refactored CLI parser with modular parameter handlers.

    Follows SOLID principles:
    - SRP: Each handler manages one parameter group
    - OCP: Easy to add new parameter groups without modifying existing code
    - LSP: All handlers implement the same interface
    - ISP: Handlers only implement methods they need
    - DIP: Parser depends on handler abstractions
    """

    def __init__(self, prog_name: str = "agent_cli"):
        self.parser = argparse.ArgumentParser(
            prog=prog_name,
            description="GLM Agent CLI - Intelligent task management and execution",
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )

        # Initialize parameter handlers (follows DIP - depend on abstractions)
        self.handlers = [
            CoreParamsHandler(),
            PlanParamsHandler(),
            ContextParamsHandler(),
            EvaluationParamsHandler(),
            DatabaseParamsHandler(),
            UtilityParamsHandler(),
        ]

        self._setup_arguments()

    def _setup_arguments(self) -> None:
        """Setup all argument groups using modular handlers."""
        # Each handler adds its own arguments (follows SRP)
        for handler in self.handlers:
            handler.add_arguments(self.parser)

    def parse_args(self, args: Optional[List[str]] = None) -> argparse.Namespace:
        """Parse command line arguments."""
        return self.parser.parse_args(args)

    def extract_and_validate_params(self, args: argparse.Namespace) -> Tuple[Dict[str, Any], Optional[str]]:
        """
        Extract parameters from all handlers and validate them.

        Returns:
            Tuple of (extracted_params_dict, validation_error_message)
        """
        all_params = {}

        # Extract parameters from each handler
        for handler in self.handlers:
            handler_params = handler.extract_values(args)
            if handler_params:
                # Use handler class name as namespace to avoid conflicts
                handler_name = handler.__class__.__name__.replace("ParamsHandler", "").lower()
                all_params[handler_name] = handler_params

        # Validate parameters from each handler
        for handler in self.handlers:
            handler_name = handler.__class__.__name__.replace("ParamsHandler", "").lower()
            handler_params = all_params.get(handler_name, {})

            is_valid, error_msg = handler.validate_values(handler_params)
            if not is_valid:
                return all_params, f"{handler.__class__.__name__}: {error_msg}"

        # Cross-handler validation
        cross_validation_error = self._validate_cross_handler_dependencies(all_params)
        if cross_validation_error:
            return all_params, cross_validation_error

        return all_params, None

    def _validate_cross_handler_dependencies(self, all_params: Dict[str, Any]) -> Optional[str]:
        """Validate dependencies between different parameter groups."""
        database_params = all_params.get("database", {})
        context_params = all_params.get("context", {})
        core_params = all_params.get("core", {})

        # Snapshot operations require task_id
        snapshot_ops = ["list_snapshots", "export_snapshot"]
        if any(context_params.get(op) for op in snapshot_ops):
            if not database_params.get("task_id"):
                return "Snapshot operations require --task-id"

        # Context snapshot export requires label
        if context_params.get("export_snapshot") and not context_params.get("label"):
            return "--export-snapshot requires --label"

        # Goal validation only for plan creation operations
        needs_goal = core_params.get("plan_only") or (
            not any(
                [
                    database_params,
                    any(
                        param in ["eval_stats", "eval_batch", "eval_supervision", "eval_supervision_config"]
                        for param in all_params.get("evaluation", {})
                    ),
                    database_params.get("rerun_task") or database_params.get("rerun_subtree"),
                    any(param in ["list_plans", "load_plan"] for param in all_params.get("plan", {})),
                    any(
                        param in ["index_preview", "index_export", "index_run_root"]
                        for param in all_params.get("utilities", {})
                    ),
                ]
            )
        )

        if needs_goal:
            from .params.core import CoreParamsHandler

            is_valid, error = CoreParamsHandler.validate_for_plan_creation(core_params)
            if not is_valid:
                return error

        return None

    def determine_operation_type(self, args: argparse.Namespace) -> str:
        """
        Determine the primary operation type based on parsed arguments.

        This replaces the complex conditional logic in the original parser.
        """
        # Check each handler for operations (follows OCP - easy to extend)
        if DatabaseParamsHandler.has_database_operation(args):
            return "database"

        if EvaluationParamsHandler.has_evaluation_operation(args):
            return "evaluation"

        if DatabaseParamsHandler.has_rerun_operation(args):
            return "rerun"

        if PlanParamsHandler.has_plan_operation(args):
            return "plan"

        if UtilityParamsHandler.has_utility_operation(args):
            return "utility"

        # Default to plan workflow if goal is provided
        if hasattr(args, "goal") and args.goal:
            return "plan"

        return "help"

    def get_context_options(self, all_params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Build context options from context parameters."""
        context_params = all_params.get("context", {})
        return ContextParamsHandler.build_context_options(context_params)

    def get_handler_by_type(self, handler_type: str) -> Optional[Any]:
        """Get a specific parameter handler by type name."""
        handler_map = {
            "core": CoreParamsHandler,
            "plan": PlanParamsHandler,
            "context": ContextParamsHandler,
            "evaluation": EvaluationParamsHandler,
            "database": DatabaseParamsHandler,
            "utilities": UtilityParamsHandler,
        }

        handler_class = handler_map.get(handler_type.lower())
        if handler_class:
            return next((h for h in self.handlers if isinstance(h, handler_class)), None)
        return None


class LegacyCompatibilityWrapper:
    """
    Wrapper to maintain compatibility with existing code.

    This allows gradual migration from the old parser to the new modular one.
    """

    def __init__(self):
        self.modular_parser = ModularCLIParser()

    def parse_args(self, args: Optional[List[str]] = None) -> argparse.Namespace:
        """Parse args using the new modular parser."""
        return self.modular_parser.parse_args(args)

    def build_from_args(self, args: argparse.Namespace) -> Optional[Dict[str, Any]]:
        """Build context options - maintains compatibility with existing interface."""
        all_params, validation_error = self.modular_parser.extract_and_validate_params(args)

        if validation_error:
            raise ValueError(f"Parameter validation failed: {validation_error}")

        return self.modular_parser.get_context_options(all_params)
