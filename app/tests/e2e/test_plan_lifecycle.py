"""E2E test for the full plan lifecycle: create → decompose → execute → verify.

This test exercises the system with **real LLM calls** — no mocking.
It validates that a plan can be created, decomposed into tasks, executed,
and that all tasks reach a terminal status with non-empty results.

Requires a valid LLM API key in the environment (see conftest.py for
provider-to-key mapping).
"""

from __future__ import annotations

import pytest

from app.repository.plan_repository import PlanRepository

# Ensure the external marker is applied even if conftest pytestmark
# inheritance varies across pytest versions.
pytestmark = pytest.mark.external

_DEFAULT_OWNER = "tester"
_DEFAULT_HEADERS = {"X-Forwarded-User": _DEFAULT_OWNER}


@pytest.mark.asyncio
@pytest.mark.timeout(300)
async def test_plan_full_lifecycle(e2e_app_client) -> None:
    """Create plan → decompose → execute → verify tree and result."""

    # ------------------------------------------------------------------
    # Step 1: Create a plan via PlanRepository
    # ------------------------------------------------------------------
    repo = PlanRepository()
    tree = repo.create_plan(
        "List common bioinformatics file formats",
        owner=_DEFAULT_OWNER,
        description="Create a brief summary of common bioinformatics file formats.",
    )
    plan_id = tree.id
    assert plan_id is not None

    # The plan starts empty — create a root task for decomposition
    root_task = repo.create_task(
        plan_id,
        name="List common bioinformatics file formats",
        instruction=(
            "List and briefly describe three common bioinformatics file "
            "formats: FASTA, FASTQ, and GFF3. For each format, provide "
            "a one-sentence description of its purpose."
        ),
    )
    root_task_id = root_task.id

    # ------------------------------------------------------------------
    # Step 2: Decompose the plan via the task decompose endpoint
    # ------------------------------------------------------------------
    decompose_resp = e2e_app_client.post(
        f"/tasks/{root_task_id}/decompose",
        json={"plan_id": plan_id},
        headers=_DEFAULT_HEADERS,
    )
    assert decompose_resp.status_code == 200, (
        f"Decompose failed with {decompose_resp.status_code}: "
        f"{decompose_resp.text}"
    )
    decompose_data = decompose_resp.json()
    assert decompose_data["success"] is True, (
        f"Decompose returned success=False: {decompose_data.get('message')}"
    )

    # ------------------------------------------------------------------
    # Step 3: Assert decomposition produced at least one task node
    # ------------------------------------------------------------------
    tree_resp = e2e_app_client.get(
        f"/plans/{plan_id}/tree",
        headers=_DEFAULT_HEADERS,
    )
    assert tree_resp.status_code == 200
    tree_data = tree_resp.json()
    nodes = tree_data.get("nodes", {})
    # Exclude the root task we created — we want at least one *decomposed* child
    child_nodes = {
        nid: node for nid, node in nodes.items() if int(nid) != root_task_id
    }
    assert len(child_nodes) >= 1, (
        f"Expected at least one decomposed task node, got {len(child_nodes)}. "
        f"All nodes: {list(nodes.keys())}"
    )

    # ------------------------------------------------------------------
    # Step 4: Execute the decomposed tasks via the execute-full endpoint
    # ------------------------------------------------------------------
    execute_resp = e2e_app_client.post(
        f"/plans/{plan_id}/execute-full",
        json={"async_mode": False, "stop_on_failure": False},
        headers=_DEFAULT_HEADERS,
    )
    assert execute_resp.status_code == 200, (
        f"Execute failed with {execute_resp.status_code}: "
        f"{execute_resp.text}"
    )
    execute_data = execute_resp.json()
    assert execute_data.get("plan_id") == plan_id

    # ------------------------------------------------------------------
    # Step 5: Assert that the result summary contains non-empty content
    # ------------------------------------------------------------------
    result = execute_data.get("result") or {}
    executed_ids = result.get("executed_task_ids", [])
    assert len(executed_ids) > 0, (
        f"Expected at least one executed task, got none. "
        f"Response: {execute_data.get('message')}"
    )

    # Verify at least one task has non-empty execution_result content
    updated_tree = repo.get_plan_tree(plan_id)
    has_content = False
    for node in updated_tree.nodes.values():
        if node.execution_result and node.execution_result.strip():
            has_content = True
            break
    assert has_content, "Expected at least one task with non-empty execution_result"

    # ------------------------------------------------------------------
    # Step 6: Assert all task nodes are in a terminal status
    # ------------------------------------------------------------------
    final_tree_resp = e2e_app_client.get(
        f"/plans/{plan_id}/tree",
        headers=_DEFAULT_HEADERS,
    )
    assert final_tree_resp.status_code == 200
    final_tree_data = final_tree_resp.json()
    final_nodes = final_tree_data.get("nodes", {})

    terminal_statuses = {"completed", "failed", "skipped"}
    non_terminal = []
    for nid, node in final_nodes.items():
        status = node.get("status", "").lower()
        if status not in terminal_statuses:
            non_terminal.append((nid, status))

    assert len(non_terminal) == 0, (
        f"Expected all task nodes in terminal status, but found "
        f"{len(non_terminal)} non-terminal: {non_terminal}"
    )
