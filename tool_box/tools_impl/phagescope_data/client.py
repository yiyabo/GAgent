"""Async HTTP client for the GAgent PhageScope V2 Read-Only Data Service."""

from __future__ import annotations

import importlib
from typing import Any
from urllib.parse import urlsplit

from .config import PhageScopeDataError, service_endpoint


async def call_service(
    path: str,
    *,
    method: str = "GET",
    query: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    timeout: float = 45.0,
) -> dict[str, Any]:
    base, key = service_endpoint()
    if not base or not key:
        raise PhageScopeDataError(
            "configuration_required",
            "PhageScope data service is not configured (set PHAGESCOPE_DATA_SERVICE_URL and PHAGESCOPE_DATA_SERVICE_KEY).",
            503,
        )
    parsed = urlsplit(base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise PhageScopeDataError(
            "configuration_error",
            "PhageScope data service URL must be a valid HTTP(S) endpoint.",
            503,
        )
    rel = path.lstrip("/")
    url = f"{base}/{rel}"
    httpx = importlib.import_module("httpx")
    try:
        async with httpx.AsyncClient(
            timeout=min(max(float(timeout), 2.0), 120.0),
            trust_env=False,
            follow_redirects=False,
        ) as http:
            resp = await http.request(
                method,
                url,
                params=query,
                json=body,
                headers={"X-API-Key": key, "Accept": "application/json"},
            )
    except httpx.TimeoutException as exc:
        raise PhageScopeDataError(
            "service_timeout",
            "PhageScope data service timed out (10s SQL guard or network timeout).",
            408,
        ) from exc
    except httpx.HTTPError as exc:
        raise PhageScopeDataError(
            "service_unavailable",
            f"Could not reach PhageScope data service: {type(exc).__name__}",
            502,
        ) from exc

    try:
        payload = resp.json()
    except ValueError as exc:
        raise PhageScopeDataError(
            "invalid_service_response",
            "PhageScope data service returned non-JSON response.",
            502,
        ) from exc

    if resp.status_code >= 400 or (isinstance(payload, dict) and "error" in payload):
        err = payload.get("error") if isinstance(payload, dict) else None
        code = err.get("code", "service_error") if isinstance(err, dict) else "service_error"
        msg = err.get("message", f"HTTP {resp.status_code}") if isinstance(err, dict) else str(err or f"HTTP {resp.status_code}")
        raise PhageScopeDataError(str(code), str(msg), int(resp.status_code))

    if not isinstance(payload, dict):
        return {"results": payload}
    return payload
