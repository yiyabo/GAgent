"""User-facing receipts derived only from the execution service's returned facts."""

from collections.abc import Mapping


def _label(task_id, name=None):
    # A name is display data, not executable Markdown or an inferred assignment.
    name = " ".join(str(name or "").split())[:120]
    return f"{name}（任务 #{task_id}）" if name else f"任务 #{task_id}"


def execution_receipt(payload, task_ids=None):
    """Keep job IDs in metadata; distinguish queued, running and failed submission."""
    job = payload.get("job") or {}
    job = job if isinstance(job, Mapping) else {}
    job_id = payload.get("job_id") or job.get("job_id")
    if not payload.get("success"):
        reason = payload.get("error") or payload.get("message") or "执行服务未接受请求，请稍后重试。"
        return f"未提交后台执行：{reason}"
    if not job_id:
        return str(payload.get("message") or "未创建新的后台作业，请在计划面板查看当前状态。")
    meta = job.get("metadata") or {}
    meta = meta if isinstance(meta, Mapping) else {}
    result = payload.get("result") or {}
    ids = task_ids if task_ids is not None else (
        meta.get("requested_task_ids") or
        (result.get("requested_task_ids") if isinstance(result, Mapping) else None)
    )
    names = {t["task_id"]: t.get("name") for t in meta.get("requested_tasks", [])
             if isinstance(t, Mapping) and "task_id" in t}
    if meta.get("target_task_id") is not None:
        names.setdefault(meta["target_task_id"], meta.get("target_task_name"))
    if ids:
        label = "、".join(_label(tid, names.get(tid)) for tid in ids[:3])
        if len(ids) > 3:
            label += f"等 {len(ids)} 个任务"
    else:
        plan_id = payload.get("plan_id") or job.get("plan_id")
        title = " ".join(str(meta.get("plan_title") or "").split())[:120]
        label = f"{title}（计划 #{plan_id}）" if title else f"计划 #{plan_id}"
    status = str(job.get("status") or "queued").lower()
    if status in {"failed", "cancelled", "canceled", "error"}:
        return f"后台作业未能继续：{label}。请在计划的「运行记录」查看原因。"
    if status in {"completed", "succeeded", "done"}:
        return f"后台作业已结束：{label}。结果可在计划面板查看。"
    state = "正在执行" if status in {"running", "delegating"} else "正在排队"
    return (f"已提交后台：{label}，{state}。\n\n"
            "你可以继续聊天。进度在计划的「运行记录」查看，生成的文件在「产物」查看。")
