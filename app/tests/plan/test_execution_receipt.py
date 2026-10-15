import pytest

from app.services.plans.execution_receipt import execution_receipt


@pytest.mark.parametrize("status,word", [("queued", "正在排队"), ("running", "正在执行")])
def test_receipt_uses_authoritative_names_scope_and_status(status, word):
    payload = {"success": True, "plan_id": 187, "job": {
        "job_id": "opaque-internal-uuid", "status": status,
        "metadata": {"target_task_id": 5, "target_task_name": "图表与表格规划",
                     "requested_task_ids": [5]}}}
    text = execution_receipt(payload)
    assert "图表与表格规划（任务 #5）" in text and word in text
    assert "运行记录" in text and "产物" in text and "继续聊天" in text
    assert "opaque-internal-uuid" not in text


def test_multiple_tasks_and_whole_plan_have_distinct_receipts():
    payload = {"success": True, "plan_id": 34, "job": {
        "job_id": "uuid", "metadata": {"plan_title": "综述", "requested_tasks": [
            {"task_id": 5, "name": "图表"}, {"task_id": 8, "name": "整合"}]}}}
    text = execution_receipt(payload, [5, 8])
    assert "图表（任务 #5）、整合（任务 #8）" in text
    assert "综述（计划 #34）" in execution_receipt(payload)
    assert "等 4 个任务" in execution_receipt(payload, [5, 8, 9, 10])


@pytest.mark.parametrize("payload", [
    {"success": False, "error": "Task 5 is already running."},
    {"success": False, "message": "Conflict", "job": {"job_id": "failed-start", "status": "failed"}},
    {"success": True, "message": "All tasks are already completed."},
])
def test_unsuccessful_or_noop_submission_never_claims_new_job(payload):
    text = execution_receipt(payload, [5])
    assert "已提交后台" not in text and "正在排队" not in text
    assert (payload.get("error") or payload["message"]) in text
