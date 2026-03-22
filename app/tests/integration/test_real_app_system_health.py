from __future__ import annotations

import pytest


@pytest.mark.integration
def test_real_app_system_health_reports_vector_status(
    app_client_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.routers.system_health_routes as system_health_routes

    class _HealthyStorage:
        async def get_storage_stats(self):
            return {"migration_mode": "sqlite_only", "sqlite": {"count": 1}}

        async def store_embedding(self, *_args, **_kwargs):
            return True

        async def search_similar(self, *_args, **_kwargs):
            return [{"text_hash": "health-check"}]

    async def _get_storage():
        return _HealthyStorage()

    monkeypatch.setattr(system_health_routes, "get_hybrid_storage", _get_storage)
    monkeypatch.setattr(
        system_health_routes,
        "_get_system_resources",
        lambda: {
            "cpu_usage_percent": 12.5,
            "memory_usage_percent": 35.0,
            "disk_usage_percent": 41.2,
            "memory_available_gb": 12.0,
            "disk_free_gb": 120.0,
            "network_io": {"bytes_sent": 10, "bytes_recv": 20},
        },
    )

    with app_client_factory() as client:
        response = client.get("/system/health")
        assert response.status_code == 200
        payload = response.json()
        assert payload["overall_status"] == "healthy"
        assert payload["performance_metrics"]["cpu_usage_percent"] == 12.5
        assert payload["components"]["vector_storage"]["status"] == "healthy"
        assert (
            payload["components"]["vector_storage"]["storage_stats"]["migration_mode"]
            == "sqlite_only"
        )


@pytest.mark.integration
def test_real_app_system_health_surfaces_vector_failures(
    app_client_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.routers.system_health_routes as system_health_routes

    async def _broken_storage():
        raise RuntimeError("vector backend unavailable")

    monkeypatch.setattr(system_health_routes, "get_hybrid_storage", _broken_storage)
    monkeypatch.setattr(
        system_health_routes,
        "_get_system_resources",
        lambda: {
            "cpu_usage_percent": 18.0,
            "memory_usage_percent": 44.0,
            "disk_usage_percent": 55.0,
            "memory_available_gb": 10.0,
            "disk_free_gb": 80.0,
            "network_io": {"bytes_sent": 10, "bytes_recv": 20},
        },
    )

    with app_client_factory() as client:
        response = client.get("/system/health")
        assert response.status_code == 200
        payload = response.json()
        assert payload["overall_status"] == "error"
        assert payload["components"]["vector_storage"]["status"] == "error"
        assert (
            "vector backend unavailable"
            in payload["components"]["vector_storage"]["error"]
        )
        assert payload["recommendations"]
