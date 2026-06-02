from __future__ import annotations

from starlette.requests import Request

import pytest

from app.routers.plan_audit_repair_routes import _ensure_plan_access
from app.services.request_principal import make_legacy_principal


def _request_with_principal(principal) -> Request:
    request = Request({"type": "http", "method": "POST", "path": "/tasks/1/audit-repair", "headers": []})
    request.state.principal = principal
    return request


def test_audit_repair_plan_access_requires_authenticated_principal():
    request = _request_with_principal(make_legacy_principal(authenticated=False))

    with pytest.raises(Exception) as exc_info:
        _ensure_plan_access(1, request)

    assert getattr(exc_info.value, "status_code", None) == 401
