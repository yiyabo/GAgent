"""Legacy context utilities placeholder.

The new workflow embeds PlanTree information directly in prompts, making the
old context aggregation stack obsolete.

Note: The gather_context function in context.py is still available for backward
compatibility, but new code should use the PlanTree prompt embedding workflow.
"""

# Re-export gather_context for backward compatibility
# New code should use PlanTree prompt embedding workflow instead
from .context import gather_context  # noqa: F401
from .context_budget import apply_budget  # noqa: F401

__all__ = ["gather_context", "apply_budget"]
