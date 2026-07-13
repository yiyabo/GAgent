"""Trusted server-to-server client for main-platform BioAgent APIs."""

from __future__ import annotations

import logging
from typing import Any, Dict

import httpx
from fastapi import HTTPException

from app.services.foundation.settings import get_settings

logger = logging.getLogger(__name__)


class PlatformCapabilityUnavailable(HTTPException):
    def __init__(self, capability: str) -> None:
        super().__init__(
            status_code=501,
            detail=f"Main platform does not provide the required capability: {capability}",
        )


class PlatformApiClient:
    """Validate platform responses and keep credentials out of route handlers."""

    def __init__(self) -> None:
        settings = get_settings()
        self.api_base_url = str(settings.platform_api_base_url or "").rstrip("/")
        self.api_key = str(settings.platform_api_key or "").strip()
        self.sso_verify_url = str(settings.platform_sso_verify_url or "").strip()
        self.timeout = max(1.0, float(settings.platform_api_timeout_seconds or 10.0))

    def _headers(self) -> Dict[str, str]:
        if not self.api_key:
            raise PlatformCapabilityUnavailable("platform service authentication")
        return {"X-Api-Key": self.api_key}

    def _project_url(self, platform_user_id: int, project_id: int) -> str:
        if not self.api_base_url:
            raise PlatformCapabilityUnavailable("platform project context")
        return f"{self.api_base_url}/users/{int(platform_user_id)}/projects/{int(project_id)}/"

    @staticmethod
    def _unwrap_response(response: httpx.Response, *, operation: str) -> Dict[str, Any]:
        if response.status_code in {401, 403, 404}:
            raise HTTPException(status_code=response.status_code, detail=f"Platform {operation} was denied")
        if response.status_code >= 500:
            raise HTTPException(status_code=502, detail=f"Platform {operation} is unavailable")
        if response.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Platform {operation} returned an unexpected response")
        try:
            payload = response.json()
        except ValueError as exc:
            raise HTTPException(status_code=502, detail=f"Platform {operation} returned invalid JSON") from exc
        if not isinstance(payload, dict) or payload.get("code") != 0:
            raise HTTPException(status_code=502, detail=f"Platform {operation} failed")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise HTTPException(status_code=502, detail=f"Platform {operation} returned invalid data")
        return data

    async def verify_sso_token(self, token: str) -> Dict[str, Any]:
        if not token:
            raise HTTPException(status_code=401, detail="SSO token is required")
        if not self.sso_verify_url:
            raise PlatformCapabilityUnavailable("SSO token verification")
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    self.sso_verify_url,
                    params={"token": token},
                    headers=self._headers(),
                )
        except httpx.TimeoutException as exc:
            raise HTTPException(status_code=504, detail="Platform SSO verification timed out") from exc
        except httpx.RequestError as exc:
            raise HTTPException(status_code=502, detail="Platform SSO verification failed") from exc
        return self._unwrap_response(response, operation="SSO verification")

    async def get_project_context(self, platform_user_id: int, project_id: int) -> Dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    self._project_url(platform_user_id, project_id),
                    headers=self._headers(),
                )
        except httpx.TimeoutException as exc:
            raise HTTPException(status_code=504, detail="Platform project lookup timed out") from exc
        except httpx.RequestError as exc:
            raise HTTPException(status_code=502, detail="Platform project lookup failed") from exc
        return self._unwrap_response(response, operation="project lookup")

    def require_project_files_capability(self) -> None:
        raise PlatformCapabilityUnavailable("platform project file access")

    def require_project_file_selection_capability(self) -> None:
        raise PlatformCapabilityUnavailable("platform project file selection")


def get_platform_api_client() -> PlatformApiClient:
    return PlatformApiClient()
