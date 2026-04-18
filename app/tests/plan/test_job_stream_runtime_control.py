from __future__ import annotations

import asyncio
from typing import Any, Dict

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import job_routes


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(job_routes.job_router)
    return TestClient(app)


def test_job_stream_includes_runtime_control_event(monkeypatch) -> None:
    snapshot_payload: Dict[str, Any] = {
        "job_id": "job-stream-1",
        "job_type": "plan_execute",
        "status": "running",
        "plan_id": 1,
        "task_id": 2,
        "mode": "task_chain",
        "stats": {},
        "params": {},
        "metadata": {},
        "logs": [],
        "owner_id": "legacy-local",
    }

    def _get_job_payload(job_id: str, include_logs: bool = True):
        _ = include_logs
        if job_id == "job-stream-1":
            return dict(snapshot_payload)
        return None

    class _Subscription:
        def __init__(self) -> None:
            self._queue: asyncio.Queue = asyncio.Queue()
            self._queue.put_nowait(
                {
                    "job_id": "job-stream-1",
                    "status": "failed",
                    "event": {
                        "timestamp": "2026-02-20T00:00:00Z",
                        "level": "info",
                        "message": "Runtime control command accepted.",
                        "metadata": {
                            "sub_type": "runtime_control",
                            "action": "pause",
                        },
                    },
                }
            )

        async def get(self, timeout: float | None = None):
            if timeout is None:
                return await self._queue.get()
            return await asyncio.wait_for(self._queue.get(), timeout=timeout)

        async def close(self) -> None:
            return None

    class _Bus:
        async def subscribe_job_events(self, job_id: str):
            assert job_id == "job-stream-1"
            return _Subscription()

    monkeypatch.setattr(job_routes.plan_decomposition_jobs, "get_job_payload", _get_job_payload)
    
    async def _get_bus():
        return _Bus()

    monkeypatch.setattr(job_routes, "get_realtime_bus", _get_bus)

    client = _build_client()
    try:
        response = client.get("/jobs/job-stream-1/stream")
        assert response.status_code == 200
        body = response.text
        assert '"type": "snapshot"' in body
        assert '"sub_type": "runtime_control"' in body
        assert '"action": "pause"' in body
    finally:
        client.close()


def test_job_stream_stops_after_terminal_heartbeat(monkeypatch) -> None:
    snapshot_payload: Dict[str, Any] = {
        "job_id": "job-stream-2",
        "job_type": "plan_execute",
        "status": "running",
        "plan_id": 1,
        "task_id": 2,
        "mode": "task_chain",
        "stats": {},
        "params": {},
        "metadata": {},
        "logs": [],
        "owner_id": "legacy-local",
    }
    terminal_payload = dict(snapshot_payload)
    terminal_payload["status"] = "succeeded"

    def _get_job_payload(job_id: str, include_logs: bool = True):
        if job_id != "job-stream-2":
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
            assert job_id == "job-stream-2"
            return _TimeoutSubscription()

    monkeypatch.setattr(job_routes.plan_decomposition_jobs, "get_job_payload", _get_job_payload)

    async def _get_bus():
        return _Bus()

    monkeypatch.setattr(job_routes, "get_realtime_bus", _get_bus)

    client = _build_client()
    try:
        response = client.get("/jobs/job-stream-2/stream")
        assert response.status_code == 200
        body = response.text
        assert body.count('"type": "snapshot"') == 1
        assert body.count('"type": "heartbeat"') == 1
        assert '"status": "succeeded"' in body
    finally:
        client.close()
