"""Launch a task scope using the UI's existing plan job worker."""

from app.services.request_principal import get_request_owner_id
from .schemas import ExecuteTaskResponse


def start_task_chain(tree, task_id, selected_task_ids, task_order, dependency_plan, request, raw_request):
    from .. import plan_routes as facade

    plan_id = dependency_plan.plan_id
    locks = []
    job = None

    def release():
        for tid, lock in reversed(locks):
            facade._release_plan_execution_lock(plan_id, tid, lock)
        locks.clear()

    # Reserve the complete scope before creating a durable queued row.
    for tid in sorted(set(task_order) | set(selected_task_ids)):
        lock = facade._acquire_plan_execution_lock(plan_id, tid)
        if lock is None:
            release()
            return ExecuteTaskResponse(
                success=False, message=f"Task {tid} in plan {plan_id} is already being executed. Please wait for it to finish.",
                plan_id=plan_id, task_id=task_id, dependency_plan=dependency_plan,
            )
        locks.append((tid, lock))
    owner_id = get_request_owner_id(raw_request)
    try:
        job = facade.plan_decomposition_jobs.create_job(
            plan_id=plan_id, task_id=task_id, mode="task_chain", job_type="plan_execute",
            owner_id=owner_id, session_id=request.session_id,
            params={"include_dependencies": request.include_dependencies, "include_subtasks": request.include_subtasks,
                    "deep_think": request.deep_think, "paper_mode": request.paper_mode, "steps": len(task_order),
                    "selected_task_ids": selected_task_ids},
            metadata={"session_id": request.session_id, "plan_id": plan_id, "plan_title": tree.title,
                      "target_task_id": task_id, "target_task_name": tree.nodes[task_id].display_name(),
                      "requested_tasks": [{"task_id": tid, "name": tree.nodes[tid].display_name()}
                                          for tid in selected_task_ids],
                      "task_order": task_order, "requested_task_ids": selected_task_ids},
        )
        facade.plan_decomposition_jobs.append_log(
            job.job_id, "info", "Task execution has been queued in background.",
            {"plan_id": plan_id, "task_id": task_id, "task_order": task_order, "steps": len(task_order)},
        )

        def run():
            try:
                facade._run_task_chain_job(
                    job_id=job.job_id, plan_id=plan_id, target_task_id=task_id, task_order=task_order,
                    deep_think=request.deep_think, session_id=request.session_id,
                    paper_mode=request.paper_mode, owner_id=owner_id,
                )
            finally:
                release()

        facade.threading.Thread(target=run, daemon=True).start()
    except Exception as exc:
        release()
        if job is not None:
            facade.plan_decomposition_jobs.mark_failure(job.job_id, f"Failed to start task execution: {exc}")
        return ExecuteTaskResponse(
            success=False, message=f"Failed to start task execution: {exc}", plan_id=plan_id,
            task_id=task_id, dependency_plan=dependency_plan, job=job.to_payload() if job else None,
        )
    return ExecuteTaskResponse(
        success=True, message="Task execution started in background.", plan_id=plan_id, task_id=task_id,
        dependency_plan=dependency_plan, job=job.to_payload(),
        result={"job_id": job.job_id, "status": job.status, "requested_task_ids": selected_task_ids},
    )
