from __future__ import annotations

import asyncio
from typing import Any, Dict

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import plan_routes


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(plan_routes.task_router)
    return TestClient(app)


def test_decomposition_stream_stops_after_terminal_heartbeat(monkeypatch) -> None:
    snapshot_payload: Dict[str, Any] = {
        "job_id": "decompose-stream-1",
        "job_type": "plan_decompose",
        "status": "running",
        "plan_id": 1,
        "task_id": 2,
        "mode": "single_node",
        "stats": {},
        "params": {},
        "metadata": {},
        "logs": [],
        "owner_id": "legacy-local",
    }
    terminal_payload = dict(snapshot_payload)
    terminal_payload["status"] = "succeeded"

    def _get_job_payload(job_id: str, include_logs: bool = True):
        if job_id != "decompose-stream-1":
            return None
        return dict(snapshot_payload if include_logs else terminal_payload)

    class _TimeoutSubscription:
        async def get(self, timeout: float | None = None):
            _ = timeout
            raise asyncio.TimeoutError

        async def close(self) -> None:
            return None

    class _Bus:
        async def subscribe_job_events(self, job_id: str):
            assert job_id == "decompose-stream-1"
            return _TimeoutSubscription()

    monkeypatch.setattr(plan_routes.plan_decomposition_jobs, "get_job_payload", _get_job_payload)

    async def _get_bus():
        return _Bus()

    monkeypatch.setattr(plan_routes, "get_realtime_bus", _get_bus)

    client = _build_client()
    try:
        response = client.get("/tasks/decompose/jobs/decompose-stream-1/stream")
        assert response.status_code == 200
        body = response.text
        assert body.count('"type": "snapshot"') == 1
        assert body.count('"type": "heartbeat"') == 1
        assert '"status": "succeeded"' in body
    finally:
        client.close()
