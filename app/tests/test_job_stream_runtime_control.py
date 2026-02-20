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
    }

    def _get_job_payload(job_id: str, include_logs: bool = True):
        _ = include_logs
        if job_id == "job-stream-1":
            return dict(snapshot_payload)
        return None

    def _register_subscriber(job_id: str, loop: asyncio.AbstractEventLoop):
        _ = loop
        if job_id != "job-stream-1":
            return None
        q: asyncio.Queue = asyncio.Queue()
        q.put_nowait(
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
        return q

    monkeypatch.setattr(job_routes.plan_decomposition_jobs, "get_job_payload", _get_job_payload)
    monkeypatch.setattr(job_routes.plan_decomposition_jobs, "register_subscriber", _register_subscriber)
    monkeypatch.setattr(job_routes.plan_decomposition_jobs, "unregister_subscriber", lambda job_id, queue: None)

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

