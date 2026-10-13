"""Merge selected task scopes through the existing dependency planner."""

from dataclasses import replace


def selected_dependency_plan(tree, task_ids, request, state_by_task):
    from .. import plan_routes as facade

    plans = [facade._build_execution_dependency_plan(
        tree, task_id, include_dependencies=bool(request.include_dependencies),
        include_subtasks=bool(request.include_subtasks), state_by_task=state_by_task,
    ) for task_id in task_ids]
    if len(plans) == 1:
        return plans[0]
    selected = {task_id for plan in plans for task_id in plan.execution_order}
    order, cycle = facade._topological_task_order(tree, selected)
    union = lambda field: sorted({value for plan in plans for value in getattr(plan, field)})
    return replace(
        plans[0], execution_order=order,
        direct_dependencies=union('direct_dependencies'),
        closure_dependencies=union('closure_dependencies'),
        missing_dependencies=union('missing_dependencies'),
        running_dependencies=union('running_dependencies'),
        cycle_detected=cycle or any(plan.cycle_detected for plan in plans),
        cycle_paths=facade._dedupe_cycle_paths([path for plan in plans for path in plan.cycle_paths]),
    )
