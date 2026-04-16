"""Integration tests for plan lifecycle.

Validates: plan creation via repository → API query → task status → owner isolation.
"""

from __future__ import annotations

import pytest

from app.database_pool import get_db
from app.repository.plan_repository import PlanRepository

# The conftest sets AUTH_MODE=proxy. Without X-Forwarded-User the middleware
# resolves to a default owner (typically "default" or "anonymous").  When
# creating plans via the repository we must use the same owner string that
# the API will resolve for the given request headers.

_DEFAULT_OWNER = "tester"
_DEFAULT_HEADERS = {"X-Forwarded-User": _DEFAULT_OWNER}


@pytest.mark.integration
def test_plan_create_and_list_via_api(app_client_factory) -> None:
    with app_client_factory() as client:
        repo = PlanRepository()
        tree = repo.create_plan(
            "Integration Test Plan",
            owner=_DEFAULT_OWNER,
            description="A plan created for integration testing",
        )
        plan_id = tree.id

        resp = client.get("/plans", headers=_DEFAULT_HEADERS)
        assert resp.status_code == 200
        plans = resp.json()
        plan_ids = [p["id"] for p in plans]
        assert plan_id in plan_ids

        matching = [p for p in plans if p["id"] == plan_id][0]
        assert matching["title"] == "Integration Test Plan"


@pytest.mark.integration
def test_plan_tree_endpoint_returns_structure(app_client_factory) -> None:
    with app_client_factory() as client:
        repo = PlanRepository()
        tree = repo.create_plan(
            "Tree Test Plan",
            owner=_DEFAULT_OWNER,
            description="Plan with tree structure",
        )
        plan_id = tree.id

        resp = client.get(f"/plans/{plan_id}/tree", headers=_DEFAULT_HEADERS)
        assert resp.status_code == 200
        payload = resp.json()
        assert payload.get("plan_id", payload.get("id")) == plan_id
        assert "nodes" in payload


@pytest.mark.integration
def test_plan_owner_isolation(app_client_factory) -> None:
    alice_headers = {"X-Forwarded-User": "alice"}
    bob_headers = {"X-Forwarded-User": "bob"}

    with app_client_factory() as client:
        repo = PlanRepository()
        tree = repo.create_plan(
            "Alice Private Plan",
            owner="alice",
            description="Only alice should see this",
        )
        plan_id = tree.id

        alice_resp = client.get("/plans", headers=alice_headers)
        assert alice_resp.status_code == 200
        alice_plan_ids = [p["id"] for p in alice_resp.json()]
        assert plan_id in alice_plan_ids

        bob_resp = client.get("/plans", headers=bob_headers)
        assert bob_resp.status_code == 200
        bob_plan_ids = [p["id"] for p in bob_resp.json()]
        assert plan_id not in bob_plan_ids
