from __future__ import annotations

from typing import Any, Dict

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import job_routes


def _build_client() -> TestClient:
    app = FastAPI()
    app.include_router(job_routes.job_router)
    return TestClient(app)


def test_job_control_returns_404_when_job_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        job_routes.plan_decomposition_jobs,
        "get_job_payload",
        lambda job_id, include_logs=False: None,
    )
    client = _build_client()
    try:
        response = client.post("/jobs/missing-job/control", json={"action": "pause"})
        assert response.status_code == 404
    finally:
        client.close()


def test_job_control_accepts_pause(monkeypatch) -> None:
    payload: Dict[str, Any] = {
        "job_id": "job-1",
        "status": "running",
    }

    def _get_job_payload(job_id: str, include_logs: bool = False):
        _ = include_logs
        if job_id == "job-1":
            return dict(payload)
        return None

    monkeypatch.setattr(job_routes.plan_decomposition_jobs, "get_job_payload", _get_job_payload)
    monkeypatch.setattr(
        job_routes.plan_decomposition_jobs,
        "control_runtime",
        lambda job_id, action: job_id == "job-1" and action == "pause",
    )
    client = _build_client()
    try:
        response = client.post("/jobs/job-1/control", json={"action": "pause"})
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["action"] == "pause"
        assert data["status"] == "running"
    finally:
        client.close()


def test_job_control_reports_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(
        job_routes.plan_decomposition_jobs,
        "get_job_payload",
        lambda job_id, include_logs=False: {"job_id": job_id, "status": "succeeded"},
    )
    monkeypatch.setattr(
        job_routes.plan_decomposition_jobs,
        "control_runtime",
        lambda job_id, action: False,
    )
    client = _build_client()
    try:
        response = client.post("/jobs/job-2/control", json={"action": "resume"})
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert data["status"] == "succeeded"
    finally:
        client.close()

