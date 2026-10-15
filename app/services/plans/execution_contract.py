"""Shared execution guidance for native, strict and toolbox tool descriptions."""

PLAN_EXECUTION_GUIDANCE = (
    "For requested existing Plan tasks/stages use execute_task with exact target_task_ids. "
    "A stage targets its composite ID, not its first child; existing descendants run automatically. "
    "IDs are not ordinal stage numbers: match names/ordinals through the Plan reference index. "
    "For multiple targets, send one execute_task call with all IDs. "
    "Use execute_all only for an explicit whole-Plan request, never as a fallback for an unclear retry. "
    "Resolve pronouns from recent dialogue or ask which target. UI selection alone is not authorization. "
    "Status questions, negated/quoted commands, examples and past executions do not request execution. "
    "For questions about a task's purpose or status, use get and answer from its instructions/state. "
    "A routine query normally needs one get followed by a concise answer, not repeated inspection or speculative dependency claims. "
    "Do not call review for a summary or status: review is only for an explicitly requested Plan quality assessment, "
    "and is never a prerequisite for execution or a way to fill missing task instructions. "
    "Execution/retry does not authorize creating, splitting, editing or deleting Plan tasks. "
    "Plan execution submits a background job: return its receipt immediately without polling or doing "
    "the task's work in chat. Ordinary search, code and file work stays in chat."
)


def execution_target_ids_schema():
    return {"type": "array", "maxItems": 64, "items": {"type": "integer", "minimum": 1},
            "description": "Exact existing task IDs for execute_task (required). For a stage use its composite ID, not a child or ordinal number."}
