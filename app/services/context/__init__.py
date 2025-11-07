"""Legacy context utilities placeholder.

The new workflow embeds PlanTree information directly in prompts, making the
old context aggregation stack obsolete.  Importing this module now raises a
runtime error to prevent accidental use of outdated logic.
"""

raise RuntimeError(
    "Context utilities have been retired. Use the PlanTree prompt embedding "
    "workflow instead of legacy gather_context APIs."
)
