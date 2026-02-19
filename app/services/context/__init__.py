"""Context utilities for gathering task context.

Provides gather_context() and apply_budget() for assembling contextual
information used in prompt construction.

Note: The PlanTree prompt embedding workflow is the preferred approach for
new code. These utilities are retained for backward compatibility with
modules that still depend on them (e.g. prompt_builder, context_routes).
"""

from .context import gather_context  # noqa: F401
from .context_budget import apply_budget, PRIORITY_ORDER  # noqa: F401
