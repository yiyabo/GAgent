import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import init_db
from app.repository.tasks import SqliteTaskRepository
from app.services.plan_session import plan_session_manager
from app.services.conversational_agent import ConversationalAgent, _PENDING_INSTRUCTIONS


class StubLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    def chat(self, prompt, history=None):
        self.prompts.append(prompt)
        if not self.responses:
            return json.dumps({
                "instructions": [
                    {"needs_tool": False, "intent": "chat", "response": "(no-op)"}
                ]
            })
        response = self.responses.pop(0)
        if isinstance(response, dict):
            return json.dumps(response)
        return response


@pytest.fixture
def repo(tmp_path, monkeypatch):
    test_db = tmp_path / "test.db"
    monkeypatch.setattr("app.database.DB_PATH", str(test_db), raising=False)
    init_db()
    return SqliteTaskRepository()


def test_llm_prompt_reflects_deleted_tasks(monkeypatch, repo):
    monkeypatch.setenv("LLM_MOCK", "0")

    plan_id = repo.create_plan("Plan With Tree")
    root_id = repo.create_task("Root Task")
    repo.link_task_to_plan(plan_id, root_id)
    child_id = repo.create_task("Child Task", parent_id=root_id)
    repo.link_task_to_plan(plan_id, child_id)
    grandchild_id = repo.create_task("Grandchild Task", parent_id=child_id)
    repo.link_task_to_plan(plan_id, grandchild_id)

    delete_instruction = {
        "instructions": [
            {
                "needs_tool": True,
                "intent": "delete_task",
                "parameters": {"task_name": "Child Task"},
                "initial_response": "准备删除子任务"
            }
        ]
    }
    followup_instruction = {
        "instructions": [
            {
                "needs_tool": False,
                "intent": "chat",
                "response": "当前任务列表已更新。"
            }
        ]
    }

    lookup_response = {"best_match_id": child_id}
    stub_llm = StubLLM([delete_instruction, lookup_response, followup_instruction])
    monkeypatch.setattr("app.llm.get_default_client", lambda: stub_llm)

    _PENDING_INSTRUCTIONS.clear()

    async def scenario():
        agent = ConversationalAgent(plan_id=plan_id, conversation_id=42)
        agent.llm = stub_llm

        first_response = await agent.process_command("删除 Child Task", confirmed=False)
        second_response = await agent.process_command("删除 Child Task", confirmed=True)
        third_response = await agent.process_command("显示下一个任务", confirmed=False)

        if agent.plan_session:
            return agent.plan_session.list_tasks(), first_response, second_response, third_response
        return [], first_response, second_response, third_response

    tasks_after, first_response, second_response, third_response = asyncio.run(scenario())

    assert first_response["intent"] == "confirmation_required"
    assert second_response["success"] is True, second_response
    assert third_response["success"] is True

    remaining_ids = {task["id"] for task in tasks_after}
    assert child_id not in remaining_ids, f"plan session still has child: {tasks_after}"
    assert grandchild_id not in remaining_ids, f"plan session still has grandchild: {tasks_after}"

    assert len(stub_llm.prompts) >= 2
    first_prompt = stub_llm.prompts[0]
    latest_prompt = stub_llm.prompts[-1]

    assert "Child Task" in first_prompt
    assert "Grandchild Task" not in first_prompt
    assert "Child Task" not in latest_prompt
    assert "Grandchild Task" not in latest_prompt

    plan_session_manager.release_session(plan_id)


def test_plan_context_includes_context_and_output(repo):
    plan_id = repo.create_plan("Context Plan")
    root_id = repo.create_task("Root Context Task")
    repo.link_task_to_plan(plan_id, root_id)

    repo.upsert_task_input(root_id, "Root instruction details")
    repo.upsert_task_context(root_id, combined="Detailed task context for root", sections=[{"title": "Summary", "content": "Some context"}], meta={"source": "test"}, label="latest")
    repo.upsert_task_context(root_id, combined="Initial AI generated context", sections=[{"title": "Init", "content": "Initial notes"}], meta={"origin": "ai"}, label="ai-initial")
    repo.upsert_task_output(root_id, "Result of executing root task")

    snapshot_dir = Path("logs/plan_snapshots")
    if snapshot_dir.exists():
        existing_files = set(snapshot_dir.glob("plan_*"))
    else:
        existing_files = set()

    agent = ConversationalAgent(plan_id=plan_id, conversation_id=7)
    context_snippet = agent._build_plan_graph_context()

    assert "GraphSummary (current layer + direct children)" in context_snippet

    json_start = context_snippet.find("{")
    assert json_start != -1, "Expected JSON payload in graph summary"
    summary_payload = json.loads(context_snippet[json_start:])

    assert summary_payload["type"] == "GraphSummary"
    assert summary_payload["nodes"], "Expected graph summary to include nodes"
    root_node = summary_payload["nodes"][0]
    assert root_node["instruction"] == "Root instruction details"

    def gather_outputs(node):
        collected = []
        output = node.get("output")
        if output:
            collected.append(output)
        for child in node.get("children", []) or []:
            collected.extend(gather_outputs(child))
        return collected

    outputs = gather_outputs(root_node)
    assert "Result of executing root task" in outputs

    labels = {ctx.get("label") for ctx in root_node.get("contexts", [])}
    assert {"latest", "ai-initial"}.issubset(labels)

    latest_meta = next(ctx for ctx in root_node["contexts"] if ctx.get("label") == "latest")
    ai_meta = next(ctx for ctx in root_node["contexts"] if ctx.get("label") == "ai-initial")
    assert latest_meta.get("meta", {}).get("source") == "test"
    assert ai_meta.get("meta", {}).get("origin") == "ai"

    if snapshot_dir.exists():
        plan_files = list(snapshot_dir.glob("plan_*_graph_summary.json"))
        assert plan_files, "Expected graph summary snapshot file to exist"
        latest_file = max(plan_files, key=lambda p: p.stat().st_mtime)
        snapshot_data = json.loads(latest_file.read_text(encoding="utf-8"))
        assert snapshot_data["type"] == "GraphSummary"
        snapshot_root = snapshot_data["nodes"][0]
        assert snapshot_root["instruction"] == "Root instruction details"
        assert snapshot_root["contexts"]
        assert snapshot_root["output"] == "Result of executing root task"
        if latest_file not in existing_files:
            latest_file.unlink()

    plan_session_manager.release_session(plan_id)


def test_request_subgraph_flow(monkeypatch, repo):
    plan_id = repo.create_plan("Subgraph Plan")
    root_db_id = repo.create_task("Root Task")
    repo.link_task_to_plan(plan_id, root_db_id)
    child_db_id = repo.create_task("Child Task", parent_id=root_db_id)
    repo.link_task_to_plan(plan_id, child_db_id)
    grand_db_id = repo.create_task("Grandchild Task", parent_id=child_db_id)
    repo.link_task_to_plan(plan_id, grand_db_id)

    repo.upsert_task_input(child_db_id, "Child instruction")
    repo.upsert_task_context(
        child_db_id,
        combined="Child context",
        sections=[{"title": "Focus", "content": "Details"}],
        meta={"extra": "info"},
        label="latest",
    )

    request_template = {
        "instructions": [
            {
                "needs_tool": False,
                "intent": "request_subgraph",
                "parameters": {"logical_id": None},
                "initial_response": "Need deeper task information."
            }
        ]
    }
    followup_instruction = {
        "instructions": [
            {
                "needs_tool": False,
                "intent": "chat",
                "response": "Thanks, I have everything I need now."
            }
        ]
    }

    stub_llm = StubLLM([request_template, followup_instruction])
    monkeypatch.setattr("app.llm.get_default_client", lambda: stub_llm)

    agent = ConversationalAgent(plan_id=plan_id, conversation_id=88)
    agent.llm = stub_llm
    assert agent.plan_session is not None

    task_tree = agent.plan_session.build_task_tree()
    assert task_tree and task_tree[0]["children"], "Expected root to have children"
    child_logical_id = task_tree[0]["children"][0]["id"]
    stub_llm.responses[0]["instructions"][0]["parameters"]["logical_id"] = child_logical_id

    snapshot_dir = Path("logs/plan_snapshots")
    existing_files = set(snapshot_dir.glob("plan_*")) if snapshot_dir.exists() else set()

    async def scenario():
        return await agent.process_command("帮我分析更深层的任务", confirmed=False)

    final_response = asyncio.run(scenario())

    assert final_response["intent"] == "chat"
    assert len(stub_llm.prompts) == 2
    assert f"SubgraphDetail for logical_id {child_logical_id}" in stub_llm.prompts[1]

    if snapshot_dir.exists():
        subgraph_files = list(snapshot_dir.glob(f"plan_*_subgraph_{child_logical_id}.json"))
        assert subgraph_files, "Expected subgraph snapshot to be written"
        latest_file = max(subgraph_files, key=lambda p: p.stat().st_mtime)
        subgraph_payload = json.loads(latest_file.read_text(encoding="utf-8"))
        assert subgraph_payload["type"] == "SubgraphDetail"
        assert subgraph_payload["node"]["id"] == child_logical_id
        assert subgraph_payload["node"]["children"], "Subgraph should include nested children"
        if latest_file not in existing_files:
            latest_file.unlink()

    plan_session_manager.release_session(plan_id)
