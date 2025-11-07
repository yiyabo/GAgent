from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict, Iterable, Iterator, List, Optional

import pytest

from app.config import get_graph_rag_settings, get_search_settings
from app.routers.chat_routes import StructuredChatAgent
from app.services.llm.structured_response import LLMAction, LLMReply, LLMStructuredResponse
from app.services.plans.plan_session import PlanSession
from app.services.plans.plan_decomposer import DecompositionResult
from app.services.plans.plan_executor import ExecutionConfig, ExecutionResponse, PlanExecutor


def _response(actions: List[LLMAction], message: str = "ok") -> LLMStructuredResponse:
    return LLMStructuredResponse(
        llm_reply=LLMReply(message=message),
        actions=actions,
    )


def _stub_llm(response: LLMStructuredResponse):
    async def _invoke(self, _user_message: str) -> LLMStructuredResponse:
        return response

    return _invoke


def _stub_llm_sequence(responses: Iterable[LLMStructuredResponse]):
    iterator: Iterator[LLMStructuredResponse] = iter(responses)

    async def _invoke(self, _user_message: str) -> LLMStructuredResponse:
        try:
            return next(iterator)
        except StopIteration as exc:  # pragma: no cover - defensive
            raise AssertionError("No more stubbed LLM responses available") from exc

    return _invoke


def _make_agent(
    plan_repo,
    *,
    session: Optional[PlanSession] = None,
    plan_executor: Optional[PlanExecutor] = None,
    plan_decomposer: Optional[Any] = None,
) -> StructuredChatAgent:
    session = session or PlanSession(repo=plan_repo)
    if plan_executor is None:
        plan_executor = PlanExecutor(repo=plan_repo, llm_service=_StubExecutorLLM())
    return StructuredChatAgent(
        plan_session=session,
        plan_executor=plan_executor,
        plan_decomposer=plan_decomposer,
    )


@pytest.mark.asyncio
async def test_create_plan_and_auto_decompose(monkeypatch, plan_repo):
    actions = [
        LLMAction(
            kind="plan_operation",
            name="create_plan",
            order=1,
            parameters={"title": "Agent Demo Plan"},
        ),
    ]
    structured = _response(actions, message="Created plan")

    session = PlanSession(repo=plan_repo)
    executor_stub = _StubExecutorLLM()
    plan_executor = PlanExecutor(repo=plan_repo, llm_service=executor_stub)
    agent = StructuredChatAgent(plan_session=session, plan_executor=plan_executor)
    monkeypatch.setattr(StructuredChatAgent, "_invoke_llm", _stub_llm(structured))

    def _fake_auto(plan_id: int):
        node = plan_repo.create_task(plan_id, name="Auto Child")
        return {
            "result": DecompositionResult(
                plan_id=plan_id,
                mode="auto",
                root_node_id=None,
                processed_nodes=[],
                created_tasks=[node],
                failed_nodes=[],
                stopped_reason=None,
                stats={},
            )
        }

    monkeypatch.setattr(agent, "_auto_decompose_plan", _fake_auto)

    result = await agent.handle("请创建一个新的计划")

    assert result.success is True
    assert result.bound_plan_id is not None
    assert result.steps and result.steps[0].action.name == "create_plan"

    tree = plan_repo.get_plan_tree(result.bound_plan_id)
    assert tree.node_count() == 1


@pytest.mark.asyncio
async def test_create_task_and_update(monkeypatch, plan_repo):
    plan = plan_repo.create_plan("Task Flow Plan")
    root = plan_repo.create_task(plan.id, name="Root Task")

    actions = [
        LLMAction(
            kind="task_operation",
            name="create_task",
            order=1,
            parameters={
                "task_name": "Research topic",
                "instruction": "Study the problem space",
                "parent_id": root.id,
            },
        ),
        LLMAction(
            kind="task_operation",
            name="update_task_instruction",
            order=2,
            parameters={
                "task_id": root.id,
                "instruction": "Analyse existing solutions",
            },
        ),
    ]
    structured = _response(actions, message="Task created and updated")

    session = PlanSession(repo=plan_repo, plan_id=plan.id)
    session.refresh()
    executor_stub = _StubExecutorLLM()
    plan_executor = PlanExecutor(repo=plan_repo, llm_service=executor_stub)
    agent = StructuredChatAgent(plan_session=session, plan_executor=plan_executor)
    monkeypatch.setattr(StructuredChatAgent, "_invoke_llm", _stub_llm(structured))

    result = await agent.handle("帮我补充任务")

    assert result.success is True
    assert len(result.steps) == 2

    tree = plan_repo.get_plan_tree(plan.id)
    assert tree.node_count() == 2
    updated_root = tree.get_node(root.id)
    assert updated_root.instruction == "Analyse existing solutions"


@pytest.mark.asyncio
async def test_move_and_delete_task(monkeypatch, plan_repo):
    plan = plan_repo.create_plan("Rearrange Plan")
    root = plan_repo.create_task(plan.id, name="Root")
    task_a = plan_repo.create_task(plan.id, name="Task A", parent_id=root.id)
    task_b = plan_repo.create_task(plan.id, name="Task B", parent_id=root.id)

    actions = [
        LLMAction(
            kind="task_operation",
            name="move_task",
            order=1,
            parameters={"task_id": task_b.id, "new_parent_id": None},
        ),
        LLMAction(
            kind="task_operation",
            name="delete_task",
            order=2,
            parameters={"task_id": task_a.id},
        ),
    ]
    structured = _response(actions, message="Tasks rearranged")

    session = PlanSession(repo=plan_repo, plan_id=plan.id)
    session.refresh()
    executor_stub = _StubExecutorLLM()
    plan_executor = PlanExecutor(repo=plan_repo, llm_service=executor_stub)
    agent = StructuredChatAgent(plan_session=session, plan_executor=plan_executor)
    monkeypatch.setattr(StructuredChatAgent, "_invoke_llm", _stub_llm(structured))

    result = await agent.handle("调整任务结构")

    assert result.success is True

    tree = plan_repo.get_plan_tree(plan.id)
    assert tree.has_node(task_b.id)
    assert tree.get_node(task_b.id).parent_id is None
    assert not tree.has_node(task_a.id)


@pytest.mark.asyncio
async def test_execute_plan_action(monkeypatch, plan_repo):
    plan = plan_repo.create_plan("Execution Demo")
    root = plan_repo.create_task(plan.id, name="Root")
    child = plan_repo.create_task(plan.id, name="Child", parent_id=root.id)

    actions = [
        LLMAction(
            kind="plan_operation",
            name="execute_plan",
            order=1,
            parameters={},
        ),
    ]
    structured = _response(actions, message="Plan executed")

    session = PlanSession(repo=plan_repo, plan_id=plan.id)
    session.refresh()
    executor_stub = _StubExecutorLLM()
    plan_executor = PlanExecutor(repo=plan_repo, llm_service=executor_stub)
    agent = StructuredChatAgent(plan_session=session, plan_executor=plan_executor)
    monkeypatch.setattr(StructuredChatAgent, "_invoke_llm", _stub_llm(structured))

    result = await agent.handle("执行计划")

    assert result.success is True
    assert result.steps[0].action.name == "execute_plan"

    tree = plan_repo.get_plan_tree(plan.id)
    for node in tree.iter_nodes():
        assert node.execution_result is not None


@pytest.mark.asyncio
async def test_list_and_delete_plan_actions(monkeypatch, plan_repo):
    plan_a = plan_repo.create_plan("Plan A")
    plan_b = plan_repo.create_plan("Plan B")

    session = PlanSession(repo=plan_repo)
    agent = _make_agent(plan_repo, session=session)

    actions_sequence = [
        _response(
            [
                LLMAction(
                    kind="plan_operation",
                    name="list_plans",
                    order=1,
                    parameters={},
                )
            ],
            message="Listed plans",
        ),
        _response(
            [
                LLMAction(
                    kind="plan_operation",
                    name="delete_plan",
                    order=1,
                    parameters={"plan_id": plan_a.id},
                )
            ],
            message="Deleted plan",
        ),
    ]
    monkeypatch.setattr(
        StructuredChatAgent, "_invoke_llm", _stub_llm_sequence(actions_sequence)
    )

    list_result = await agent.handle("列出现有计划")
    list_step = list_result.steps[0]
    assert list_step.action.name == "list_plans"
    listed_ids = {plan["id"] for plan in list_step.details["plans"]}
    assert {plan_a.id, plan_b.id}.issubset(listed_ids)

    delete_result = await agent.handle("删除其中一个计划")
    delete_step = delete_result.steps[0]
    assert delete_step.action.name == "delete_plan"
    remaining_ids = {summary.id for summary in plan_repo.list_plans()}
    assert plan_a.id not in remaining_ids
    assert plan_b.id in remaining_ids


@pytest.mark.asyncio
async def test_show_and_query_task_actions(monkeypatch, plan_repo):
    plan = plan_repo.create_plan("Status Plan")
    root = plan_repo.create_task(plan.id, name="Root Node")
    plan_repo.create_task(plan.id, name="Child Node", parent_id=root.id)

    session = PlanSession(repo=plan_repo, plan_id=plan.id)
    session.refresh()
    agent = _make_agent(plan_repo, session=session)

    actions = [
        LLMAction(
            kind="task_operation",
            name="show_tasks",
            order=1,
            parameters={},
        ),
        LLMAction(
            kind="task_operation",
            name="query_status",
            order=2,
            parameters={},
        ),
    ]
    monkeypatch.setattr(
        StructuredChatAgent, "_invoke_llm", _stub_llm(_response(actions, "展示任务"))
    )

    result = await agent.handle("查看任务概览")
    assert len(result.steps) == 2
    outline = result.steps[0].details["outline"]
    assert f"[{root.id}]" in outline
    assert result.steps[1].details["task_count"] >= 2


@pytest.mark.asyncio
async def test_update_task_metadata_and_dependencies(monkeypatch, plan_repo):
    plan = plan_repo.create_plan("Metadata Plan")
    root = plan_repo.create_task(plan.id, name="Root")
    dep_a = plan_repo.create_task(plan.id, name="Dep A")
    dep_b = plan_repo.create_task(plan.id, name="Dep B")

    session = PlanSession(repo=plan_repo, plan_id=plan.id)
    session.refresh()
    agent = _make_agent(plan_repo, session=session)

    actions = [
        LLMAction(
            kind="task_operation",
            name="update_task",
            order=1,
            parameters={
                "task_id": root.id,
                "name": "Root Updated",
                "instruction": "Revised instruction",
                "metadata": {"owner": "tester"},
                "dependencies": [dep_a.id, dep_a.id, dep_b.id],
            },
        )
    ]
    monkeypatch.setattr(
        StructuredChatAgent,
        "_invoke_llm",
        _stub_llm(_response(actions, "任务已更新")),
    )

    await agent.handle("更新任务信息")
    node = plan_repo.get_node(plan.id, root.id)
    assert node.name == "Root Updated"
    assert node.instruction == "Revised instruction"
    assert node.metadata.get("owner") == "tester"
    assert node.metadata.get("dependencies") == [dep_a.id, dep_b.id]


@pytest.mark.asyncio
async def test_rerun_task_action(monkeypatch, plan_repo):
    plan = plan_repo.create_plan("Rerun Plan")
    root = plan_repo.create_task(plan.id, name="Root")
    child = plan_repo.create_task(plan.id, name="Child", parent_id=root.id)

    session = PlanSession(repo=plan_repo, plan_id=plan.id)
    session.refresh()
    agent = _make_agent(plan_repo, session=session)

    actions = [
        LLMAction(
            kind="task_operation",
            name="rerun_task",
            order=1,
            parameters={"task_id": child.id},
        )
    ]
    monkeypatch.setattr(
        StructuredChatAgent,
        "_invoke_llm",
        _stub_llm(_response(actions, "重新执行任务")),
    )

    result = await agent.handle("重新执行指定任务")
    step = result.steps[0]
    assert step.action.name == "rerun_task"
    assert step.details["status"] == "completed"
    node = plan_repo.get_node(plan.id, child.id)
    assert node.execution_result is not None
    assert node.status == "completed"


@pytest.mark.asyncio
async def test_decompose_task_action(monkeypatch, plan_repo):
    plan = plan_repo.create_plan("Decompose Plan")
    root = plan_repo.create_task(plan.id, name="Root")

    session = PlanSession(repo=plan_repo, plan_id=plan.id)
    session.refresh()
    decomposer = _StubPlanDecomposer(plan_repo)
    agent = _make_agent(plan_repo, session=session, plan_decomposer=decomposer)
    agent.decomposer_settings = replace(
        agent.decomposer_settings, model="stub-model", auto_on_create=False
    )

    actions = [
        LLMAction(
            kind="task_operation",
            name="decompose_task",
            order=1,
            parameters={"task_id": root.id, "expand_depth": 1, "node_budget": 3},
        )
    ]
    monkeypatch.setattr(
        StructuredChatAgent,
        "_invoke_llm",
        _stub_llm(_response(actions, "分解任务")),
    )

    result = await agent.handle("分解当前任务")
    step = result.steps[0]
    assert step.action.name == "decompose_task"
    assert step.success is True
    created = step.details["created"]
    assert len(created) == 2
    tree = plan_repo.get_plan_tree(plan.id)
    assert len(tree.children_ids(root.id)) == 2
    assert decomposer.last_plan_id == plan.id
    assert decomposer.last_node_id == root.id


@pytest.mark.asyncio
async def test_context_request_action(monkeypatch, plan_repo):
    plan = plan_repo.create_plan("Context Plan")
    root = plan_repo.create_task(plan.id, name="Root")
    child = plan_repo.create_task(plan.id, name="Child", parent_id=root.id)

    session = PlanSession(repo=plan_repo, plan_id=plan.id)
    session.refresh()
    agent = _make_agent(plan_repo, session=session)

    actions = [
        LLMAction(
            kind="context_request",
            name="request_subgraph",
            order=1,
            parameters={"task_id": root.id, "max_depth": 1},
        )
    ]
    monkeypatch.setattr(
        StructuredChatAgent,
        "_invoke_llm",
        _stub_llm(_response(actions, "请求子图")),
    )

    result = await agent.handle("获取子图")
    step = result.steps[0]
    assert step.action.name == "request_subgraph"
    returned_ids = {node["id"] for node in step.details["nodes"]}
    assert {root.id, child.id}.issubset(returned_ids)


@pytest.mark.asyncio
async def test_system_help_action(monkeypatch, plan_repo):
    agent = _make_agent(plan_repo)
    actions = [
        LLMAction(
            kind="system_operation",
            name="help",
            order=1,
            parameters={},
        )
    ]
    monkeypatch.setattr(
        StructuredChatAgent,
        "_invoke_llm",
        _stub_llm(_response(actions, "展示帮助")),
    )

    result = await agent.handle("帮我介绍当前能力")
    step = result.steps[0]
    assert step.action.name == "help"
    assert "System help" in step.message


@pytest.mark.asyncio
async def test_tool_operation_web_search(monkeypatch, plan_repo):
    actions = [
        LLMAction(
            kind="tool_operation",
            name="web_search",
            order=1,
            parameters={"query": "最新 AI 新闻", "max_results": 3},
        )
    ]
    structured = _response(actions, message="这里是搜索到的最新资讯。")

    async def fake_llm(self, _user_message: str) -> LLMStructuredResponse:
        return structured

    async def fake_execute(tool_name: str, **kwargs):
        assert tool_name == "web_search"
        assert kwargs["query"] == "最新 AI 新闻"
        assert kwargs["provider"] == get_search_settings().default_provider
        return {
            "query": kwargs["query"],
            "success": True,
            "response": "AI 行业今日发布多项新研究。",
            "results": [
                {
                    "title": "AI Research Summit Highlights",
                    "url": "https://example.com/ai",
                    "snippet": "A roundup of cutting-edge AI research.",
                    "source": "Example News",
                }
            ],
            "total_results": 1,
            "provider": "perplexity",
        }

    monkeypatch.setattr(StructuredChatAgent, "_invoke_llm", fake_llm)
    monkeypatch.setattr("app.routers.chat_routes.execute_tool", fake_execute)

    agent = StructuredChatAgent(plan_session=PlanSession(repo=plan_repo))

    result = await agent.handle("请帮我搜索最新的 AI 新闻")

    assert result.reply.startswith("这里是搜索到的最新资讯。")
    assert "Action summary:" in result.reply
    assert result.success is True
    assert len(result.steps) == 1
    step = result.steps[0]
    assert step.action.kind == "tool_operation"
    assert step.success is True
    details = step.details
    assert details["tool"] == "web_search"
    assert details["result"]["results"][0]["title"] == "AI Research Summit Highlights"
    assert agent.extra_context["recent_tool_results"]
    history_entry = agent.extra_context["recent_tool_results"][-1]
    assert history_entry["result"]["query"] == "最新 AI 新闻"


@pytest.mark.asyncio
async def test_tool_operation_missing_query(monkeypatch, plan_repo):
    actions = [
        LLMAction(
            kind="tool_operation",
            name="web_search",
            order=1,
            parameters={},
        )
    ]
    structured = _response(actions, message="尝试搜索。")

    async def fake_llm(self, _user_message: str) -> LLMStructuredResponse:
        return structured

    monkeypatch.setattr(StructuredChatAgent, "_invoke_llm", fake_llm)

    agent = StructuredChatAgent(plan_session=PlanSession(repo=plan_repo))
    result = await agent.handle("搜索一下")

    assert result.success is False
    assert len(result.steps) == 1
    step = result.steps[0]
    assert step.action.kind == "tool_operation"
    assert step.success is False
    assert "requires a non-empty query" in step.message


@pytest.mark.asyncio
async def test_tool_operation_graph_rag(monkeypatch, plan_repo):
    actions = [
        LLMAction(
            kind="tool_operation",
            name="graph_rag",
            order=1,
            parameters={
                "query": "噬菌体如何感染细菌？",
                "top_k": 15,
                "hops": 2,
                "focus_entities": ["噬菌体", "细菌"],
            },
        )
    ]
    structured = _response(actions, message="图谱检索结果如下。")

    async def fake_llm(self, _user_message: str) -> LLMStructuredResponse:
        return structured

    captured_params: Dict[str, Any] = {}

    async def fake_execute(tool_name: str, **kwargs):
        captured_params.update(kwargs)
        assert tool_name == "graph_rag"
        return {
            "query": kwargs["query"],
            "success": True,
            "result": {
                "prompt": "prompt",
                "triples": [
                    {"entity1": "噬菌体", "relation": "感染", "entity2": "细菌"}
                ],
                "metadata": {"top_k": kwargs["top_k"], "hops": kwargs["hops"]},
            },
        }

    monkeypatch.setattr(StructuredChatAgent, "_invoke_llm", fake_llm)
    monkeypatch.setattr("app.routers.chat_routes.execute_tool", fake_execute)

    agent = StructuredChatAgent(plan_session=PlanSession(repo=plan_repo))

    result = await agent.handle("查询噬菌体知识图谱")

    assert result.success is True
    assert len(result.steps) == 1
    step = result.steps[0]
    assert step.action.name == "graph_rag"
    assert step.success is True
    details = step.details
    assert details["tool"] == "graph_rag"
    assert details["result"]["triples"][0]["entity1"] == "噬菌体"
    assert captured_params["top_k"] <= get_graph_rag_settings().max_top_k
    assert captured_params["hops"] <= get_graph_rag_settings().max_hops


class _StubExecutorLLM:
    def __init__(self):
        pass

    def generate(self, prompt: str, config: ExecutionConfig) -> ExecutionResponse:
        return ExecutionResponse(
            status="success",
            content="done",
            notes=[],
            metadata={},
        )


class _StubPlanDecomposer:
    def __init__(self, repo):
        self._repo = repo
        self.last_plan_id: Optional[int] = None
        self.last_node_id: Optional[int] = None

    def run_plan(
        self,
        plan_id: int,
        *,
        max_depth: Optional[int] = None,
        node_budget: Optional[int] = None,
    ) -> DecompositionResult:
        self.last_plan_id = plan_id
        self.last_node_id = None
        created = [
            self._repo.create_task(plan_id, name="Auto Task A"),
            self._repo.create_task(plan_id, name="Auto Task B"),
        ]
        return DecompositionResult(
            plan_id=plan_id,
            mode="plan_bfs",
            root_node_id=None,
            processed_nodes=[None],
            created_tasks=created,
            failed_nodes=[],
            stopped_reason=None,
            stats={"max_depth": max_depth, "node_budget": node_budget},
        )

    def decompose_node(
        self,
        plan_id: int,
        node_id: int,
        *,
        expand_depth: Optional[int] = None,
        node_budget: Optional[int] = None,
        allow_existing_children: Optional[bool] = None,
    ) -> DecompositionResult:
        self.last_plan_id = plan_id
        self.last_node_id = node_id
        created = [
            self._repo.create_task(plan_id, name=f"Child {node_id}-1", parent_id=node_id),
            self._repo.create_task(plan_id, name=f"Child {node_id}-2", parent_id=node_id),
        ]
        return DecompositionResult(
            plan_id=plan_id,
            mode="single_node",
            root_node_id=node_id,
            processed_nodes=[node_id],
            created_tasks=created,
            failed_nodes=[],
            stopped_reason=None,
            stats={
                "expand_depth": expand_depth,
                "node_budget": node_budget,
                "allow_existing_children": allow_existing_children,
            },
        )
