from types import SimpleNamespace

from app.routers.chat.guardrail_handlers import apply_phagescope_fallback
from app.routers.chat.guardrails import should_force_plan_first
from app.services.llm.structured_response import LLMAction, LLMReply, LLMStructuredResponse


def test_explicit_plan_workflow_forces_plan_first_even_with_report_analysis_words():
    assert should_force_plan_first(
        "Use plan_operation create_plan, review_plan, optimize_plan, execute_all to generate a PhageScope report"
    )


def test_phagescope_fallback_does_not_override_existing_plan_operation():
    agent = SimpleNamespace(
        _current_user_message="Use plan_operation create_plan then review_plan and optimize_plan to save a production report",
        history=[],
    )
    structured = LLMStructuredResponse(
        llm_reply=LLMReply(message="Creating plan."),
        actions=[
            LLMAction(
                kind="plan_operation",
                name="create_plan",
                parameters={"title": "PhageScope report", "goal": "Generate report"},
                order=1,
                blocking=True,
            )
        ],
    )

    patched = apply_phagescope_fallback(agent, structured)

    assert len(patched.actions) == 1
    assert patched.actions[0].kind == "plan_operation"
    assert patched.actions[0].name == "create_plan"


def test_phagescope_fallback_does_not_inject_save_all_for_explicit_plan_workflow():
    agent = SimpleNamespace(
        _current_user_message=(
            "Build a plan, review the plan, optimize the plan, then execute_all to analyze "
            "PhageScope data and save a final report"
        ),
        history=[],
    )
    structured = LLMStructuredResponse(
        llm_reply=LLMReply(message="I will inspect files."),
        actions=[
            LLMAction(
                kind="tool_operation",
                name="file_operations",
                parameters={"operation": "read", "path": "README.md"},
                order=1,
                blocking=True,
            )
        ],
    )

    patched = apply_phagescope_fallback(agent, structured)

    assert len(patched.actions) == 1
    assert patched.actions[0].name == "file_operations"
    assert not any(action.name == "phagescope" for action in patched.actions)

from app.routers.chat.guardrail_handlers import apply_plan_review_optimize_guardrail


def test_explicit_optimize_plan_text_injects_plan_operation_over_wrong_tool():
    agent = SimpleNamespace(
        _current_user_message="Run plan_operation optimize_plan with plan_id=77 before execution.",
        plan_session=SimpleNamespace(plan_id=77),
        extra_context={"route_reason_codes": []},
    )
    structured = LLMStructuredResponse(
        llm_reply=LLMReply(message="I will draft changes."),
        actions=[
            LLMAction(
                kind="tool_operation",
                name="manuscript_writer",
                parameters={"task": "optimize the plan"},
                order=1,
                blocking=True,
            )
        ],
    )

    patched = apply_plan_review_optimize_guardrail(agent, structured)

    assert patched.actions[0].kind == "plan_operation"
    assert patched.actions[0].name == "optimize_plan"
    assert patched.actions[0].parameters == {"plan_id": 77}
    assert patched.actions[1].name == "manuscript_writer"

def test_create_plan_goal_becomes_description_when_description_missing():
    from app.routers.chat.action_handlers import _coerce_plan_description

    goal = "Generate a PhageScope report with concrete audit and PDF deliverables."

    assert _coerce_plan_description(None, goal) == goal
    assert _coerce_plan_description("Existing description", goal) == "Existing description"


def test_explicit_task_lines_in_create_plan_goal_become_ordered_seed_tasks():
    from app.routers.chat.action_handlers import _extract_explicit_plan_tasks_from_goal

    goal = (
        "Create a report.\n"
        "- Task 1: phagescope_research audit for /home/zczhao/Phage-Agent/phagescope; acceptance requires metadata_rows reported.\n"
        "- Task 2: phagescope_research prepare_metadata_table with label_level=genus; acceptance requires TSV and JSON paths.\n"
        "- Task 3: code_executor trains RandomForest and ExtraTrees.\n"
    )

    tasks = _extract_explicit_plan_tasks_from_goal(goal)

    assert [task["metadata"]["explicit_task_number"] for task in tasks] == [1, 2, 3]
    assert tasks[0]["dependencies"] == []
    assert tasks[1]["dependencies"] == [tasks[0]["name"]]
    assert tasks[2]["dependencies"] == [tasks[1]["name"]]
    assert "phagescope_research audit" in tasks[0]["instruction"]
    assert "prepare_metadata_table" in tasks[1]["instruction"]
    assert "RandomForest" in tasks[2]["instruction"]

def test_inline_explicit_task_blocks_in_create_plan_goal_become_seed_tasks():
    from app.routers.chat.action_handlers import _extract_explicit_plan_tasks_from_goal

    goal = "Goal text. Task 1: Run phagescope_research audit. Task 2: Run prepare_metadata_table. Task 3: Write PDF report."

    tasks = _extract_explicit_plan_tasks_from_goal(goal)

    assert [task["metadata"]["explicit_task_number"] for task in tasks] == [1, 2, 3]
    assert "phagescope_research audit" in tasks[0]["instruction"]
    assert "prepare_metadata_table" in tasks[1]["instruction"]
    assert "Write PDF report" in tasks[2]["instruction"]


def test_phagescope_research_paper_goal_gets_strict_seed_plan():
    from app.routers.chat.action_handlers import _build_phagescope_research_seed_tasks

    goal = (
        "Create a rigorous executable research plan for PhageScope Research Topic 1: predict host genus labels "
        "from /home/zczhao/Phage-Agent/phagescope and produce phagescope_research_topic1_production_report.pdf "
        "as a publishable paper."
    )

    tasks = _build_phagescope_research_seed_tasks(goal)

    assert len(tasks) == 9
    assert tasks[0]["dependencies"] == []
    assert tasks[-1]["dependencies"] == [tasks[-2]["name"]]
    all_text = "\n".join(task["instruction"] for task in tasks)
    for required in (
        "metadata_rows",
        "Host-derived labels only",
        "split_group=cluster",
        "Cluster-level leakage must be zero",
        "RandomForest",
        "ExtraTrees",
        "model_metrics.json",
        "report_quality_audit.json",
        "phagescope_research_topic1_production_report.pdf",
    ):
        assert required in all_text
    checks = tasks[1]["metadata"]["acceptance_criteria"]["checks"]
    assert any(check["type"] == "json_field_at_least" and check["key_path"] == "rows_written" for check in checks)
    assert any(
        check["type"] == "pdf_valid" and check["min_text_chars"] >= 3000
        for check in tasks[6]["metadata"]["acceptance_criteria"]["checks"]
    )
