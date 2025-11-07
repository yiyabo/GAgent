"""Legacy planning services placeholder.

All legacy plan-generation utilities depended on the old tasks table and are
no longer compatible with the PlanTree dialogue workflow.  Importing this
package now surfaces a descriptive runtime error to steer callers toward the
new `/chat` JSON-action interface.
"""

raise RuntimeError(
    "Planning services have been retired. Use the structured chat pipeline "
    "(PlanRepository + PlanTree) instead of the legacy propose/approve APIs."
)
