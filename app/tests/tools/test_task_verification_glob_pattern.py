from __future__ import annotations

from pathlib import Path

from app.services.plans.plan_models import PlanNode
from app.services.plans.task_verification import TaskVerificationService


def test_glob_count_at_least_accepts_pattern_key(tmp_path: Path) -> None:
    subdir = tmp_path / "advanced_model_ready_data"
    subdir.mkdir()
    for name in ("train.pt", "val.pt", "test.pt"):
        (subdir / name).write_text("tensor placeholder", encoding="utf-8")

    node = PlanNode(
        id=101,
        plan_id=105,
        name="Format model-specific arrays",
        metadata={
            "acceptance_criteria": {
                "category": "file_data",
                "blocking": True,
                "checks": [
                    {
                        "type": "glob_count_at_least",
                        "pattern": str(subdir / "*.pt"),
                        "count": 3,
                    }
                ],
            }
        },
    )
    finalization = TaskVerificationService().finalize_payload(
        node,
        {"status": "completed", "content": "ok", "metadata": {}},
        execution_status="completed",
    )

    assert finalization.final_status == "completed"
    assert finalization.verification is not None
    assert finalization.verification["status"] == "passed"
