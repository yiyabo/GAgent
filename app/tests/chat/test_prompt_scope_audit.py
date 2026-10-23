"""Exercise composed prompts, including references and delivery declarations."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.routers.chat import prompt_builder
from app.services.deep_think.prompts import _extract_history_messages
from app.services.deep_think_agent import DeepThinkAgent, ThinkingStep, TaskExecutionContext
from app.services.memory.context_recall import format_recall_context
from app.services.plans.executor_prompts import ExecutorPromptBuilder
from app.services.plans.plan_executor import PlanExecutor
from app.services.plans.plan_models import PlanNode
from app.services.prompt_policy import PLAN_SCOPE_POLICY, REFERENCE_SCOPE_POLICY
from app.services.skill_learning.context import format_skill_context
from app.services import tool_schemas


def chat_agent(plan_id=None, **overrides):
    return SimpleNamespace(
        plan_session=SimpleNamespace(plan_id=plan_id, outline=lambda **_: "[plan outline]",
                                     summaries_for_prompt=lambda **_: "[plan catalog]"),
        extra_context=overrides.get("extra_context", {}),
        history=overrides.get("history", []), mode="chat", conversation_id="test",
        schema_json='{"llm_reply": {}, "actions": []}', _extract_tool_name=lambda _: None,
    )


@pytest.mark.parametrize("plan_id", [None, 42])
def test_structured_prompt_has_one_scope_policy_without_legacy_auto_plan_directives(plan_id):
    prompt = prompt_builder.build_prompt(chat_agent(plan_id), "帮我分析这个数据，画一张图")
    assert prompt.count(PLAN_SCOPE_POLICY) == 1
    assert "internal todo-list" in prompt
    for forbidden in (
        "OR when the request clearly requires code execution", "create/decompose tasks first",
        "expand or refine the plan via", "modify the current plan structure at any time",
        "requires running code across multiple steps", "200-500 words typical",
    ):
        assert forbidden not in prompt


@pytest.mark.parametrize("profile", [
    {"plan_tree_edit_allowed": False},
    {"current_plan_id": 42, "plan_tree_edit_allowed": False, "chat_plan_execution_background_only": True},
    {"current_plan_id": 42, "plan_tree_edit_allowed": False, "plan_review_required": True},
])
@pytest.mark.parametrize("builder", ["_build_native_system_prompt", "_build_system_prompt"])
def test_deepthink_composed_prompt_preserves_chat_review_and_retry_scope(profile, builder):
    agent = DeepThinkAgent(llm_client=SimpleNamespace(), available_tools=["file_operations", "plan_operation"],
                          tool_executor=lambda *_: None, request_profile=profile)
    prompt = getattr(agent, builder)()
    assert prompt.count(PLAN_SCOPE_POLICY) == 1
    assert "A status question or review does not authorize execution" in prompt
    assert "Ordinary searches, code, file handling and analysis stay in chat" in prompt
    assert "Execution, retry and acceptance repair preserve" in prompt
    assert "The user requested plan mutation" not in prompt
    assert "use background workflows only" not in prompt
    assert "Try at least 3" not in prompt
    assert "Do not add PDF/SVG rendering" in prompt
    assert "DO NOT state that the previous figure is missing" not in prompt


def test_actual_memory_and_skill_formatters_do_not_authorize_prior_workflows():
    context = {
        "recall_context": {"memories": [{"content": "以前自动创建综述计划"}], "history": []},
        "learned_skill_context": {"skills": [{"name": "example", "description": "create a Plan"}]},
    }
    original = deepcopy(context)
    for text in (format_recall_context(context), format_skill_context(context)):
        assert REFERENCE_SCOPE_POLICY in text
        assert "not authorization for new Plan actions" in text
    assert context == original


def test_native_and_registered_plan_tool_descriptions_do_not_require_post_create_review():
    from tool_box.tools_impl.plan_tools import plan_operation_tool
    descriptions = [tool_schemas._build_registry()["plan_operation"]["function"]["description"],
                    plan_operation_tool["description"]]
    for text in descriptions:
        assert "review is read-only" in text.lower()
        assert "do not automatically review, optimize or execute" in text.lower()
        assert "then prefer review" not in text


@pytest.mark.parametrize("builder", [prompt_builder.build_prompt, prompt_builder.build_simple_stream_chat_prompt])
def test_large_reference_context_keeps_latest_user_correction_and_output_rules(builder):
    query = "只修改颜色，不要创建计划；保存到 final.md"
    agent = chat_agent(history=[{"role": "assistant", "content": "old workflow " * 12000}])
    prompt = builder(agent, query)
    assert query in prompt
    assert "Reference omitted" in prompt
    assert len(prompt) <= prompt_builder._MAX_PROMPT_CHARS
    assert "Respond" in prompt


@pytest.mark.parametrize("builder", [prompt_builder.build_prompt, prompt_builder.build_simple_stream_chat_prompt])
def test_oversized_live_request_fails_explicitly_instead_of_silently_cutting_it(builder):
    with pytest.raises(ValueError, match="Context insufficient"):
        builder(chat_agent(), "x" * 100001 + "不要执行")


def test_long_live_user_message_is_not_repeated_as_clipped_history():
    query = "说明这个概念 " * 100 + "不要执行或创建Plan"
    context = {"chat_history": [{"role": "assistant", "content": "旧讨论"}, {"role": "user", "content": query}]}
    original = deepcopy(context)
    assert _extract_history_messages(context, current_user_query=query) == [{"role": "assistant", "content": "旧讨论"}]
    assert context == original


def test_writing_task_prompt_keeps_precise_file_contract_and_method():
    node = PlanNode(id=4, plan_id=42, name="write sections", instruction="复用已有 evidence.md，按原章节写作",
        metadata={"required_outputs": [{"kind": "document", "min_count": 3, "extensions": [".md"],
                                        "target_path": "sections", "constraints": "保留引文与来源"}],
                  "output_spec_base_dir": "/tmp/task-4"})
    prompt = ExecutorPromptBuilder().build(node=node, parent=None, dependencies=[], plan_outline=None, include_context=False)
    assert '"min_count": 3' in prompt and '"target_path": "sections"' in prompt
    assert "保留引文与来源" in prompt and node.instruction in prompt
    assert "text only if the output contract permits" in prompt
    assert "do not create, split, delete, move or rewrite Plan nodes" in prompt
    assert "Resolve relative output targets against: /tmp/task-4" in prompt


def test_repair_query_honors_exact_absolute_output_and_multiple_missing_files():
    query = PlanExecutor._build_contract_repair_query(
        node=PlanNode(id=4, plan_id=42, name="write"), attempt=1,
        contract_diff={"missing_required_outputs": ["/tmp/export/sections"]},
        failed_checks=[{"path": "/tmp/export/sections", "required_count": 5, "actual_count": 2,
                        "extensions": [".md"]}], base_dir="/tmp/task-4")
    assert "3 additional files directly in /tmp/export/sections" in query
    assert "preserve an explicitly required absolute target" in query
    assert "instead of silently substituting another path" in query
    assert "instead of copying it to an external absolute path" not in query


def test_executor_oversized_instruction_is_not_silently_cut():
    with pytest.raises(ValueError, match="Context insufficient"):
        ExecutorPromptBuilder().build(node=PlanNode(id=4, plan_id=42, name="write", instruction="x" * 100001),
            parent=None, dependencies=[], plan_outline=None, include_context=False)


def test_dependency_summary_preserves_full_evidence_paths_from_omitted_middle():
    path = "runtime/session_test/task_2/results/evidence.md"
    bib = "runtime/session_test/task_2/references.bib"
    raw = "head " * 200 + path + " " + bib + " tail" * 200
    summary = ExecutorPromptBuilder()._summarize_long_result(raw, max_length=200)
    assert path in summary and bib in summary
    assert "[Key files:" in summary


def test_strict_plan_catalog_and_retry_nudge_use_supported_operations():
    agent = DeepThinkAgent(llm_client=SimpleNamespace(), available_tools=["plan_operation"],
                          tool_executor=lambda *_: None, request_profile={"current_plan_id": 42})
    prompt = agent._build_system_prompt()
    assert '- execute_task:' in prompt and '"target_task_ids": [4]' in prompt
    assert '- execute_all:' in prompt and 'A retry of one task is not a whole-plan request' in prompt
    assert '(review/optimize/update)' not in agent._get_structured_plan_retry_prompt()


@pytest.mark.asyncio
async def test_closing_summary_sends_the_live_request_and_bound_task_instruction_unclipped():
    from app.services.deep_think import synthesis
    captured = []
    async def summarize(messages, *, max_tokens, **_):
        captured.append(messages)
        return "The available records describe the current state. No new execution was requested or started."
    agent = DeepThinkAgent(llm_client=SimpleNamespace(), available_tools=[],
                          tool_executor=lambda *_: None, request_profile={"intent_type": "execute_task"})
    agent._chat_messages_streaming = summarize
    query = "背景信息 " * 600 + "仅说明当前状态，不要执行计划。"
    instruction = "原任务说明 " * 150 + "原有章节数量必须保留。"
    task = TaskExecutionContext(task_id=4, task_instruction=instruction)
    history = [{"role": "system", "content": "system prompt with " + instruction},
               {"role": "user", "content": query}]
    steps = [ThinkingStep(iteration=1, thought="The existing evidence is sufficient to report the current state.",
                          action=None, action_result=None, self_correction=None, status="done")]
    await synthesis._forced_synthesis_from_steps(agent, steps, query, history, task_context=task)
    assert len(captured) == 1
    # the real conversation goes through whole and in order; only the nudge is added
    assert captured[0][:-1] == history
    assert captured[0][-1]["role"] == "user" and "不要再调用任何工具" in captured[0][-1]["content"]
    # and the caller's list (the continuation later turns inherit) is untouched
    assert len(history) == 2
