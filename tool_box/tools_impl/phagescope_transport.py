"""PhageScope remote transport helpers."""

import logging
import os
from typing import Any, Dict, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

_TLS_RETRY_WARNING = "TLS certificate verification failed; PhageScope request retried with verify=False."
DEFAULT_BASE_URL = "https://phageapi.deepomics.org"


def _get_base_url(base_url: Optional[str]) -> str:
    return (base_url or os.getenv("PHAGESCOPE_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")


def _ssl_verify_enabled(base_url: str) -> bool:
    raw = str(os.getenv("PHAGESCOPE_SSL_VERIFY", "true")).strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    return True


def _should_retry_without_ssl_verify(base_url: str, exc: Exception) -> bool:
    base = str(base_url or "").lower()
    if "phageapi.deepomics.org" not in base:
        return False
    message = str(exc or "").lower()
    return "certificate verify failed" in message or "certificateverifyfailed" in message


def _attach_transport_warning(payload: Dict[str, Any], message: str) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {"raw": str(payload), "_transport_warnings": [message]}
    warnings = payload.get("_transport_warnings")
    if not isinstance(warnings, list):
        warnings = []
        payload["_transport_warnings"] = warnings
    if message not in warnings:
        warnings.append(message)
    return payload


def _decode_httpx_response(response: httpx.Response) -> Dict[str, Any]:
    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type:
        return response.json()
    return {"raw": response.text}


async def _do_httpx_request(
    method: str,
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    data: Optional[Dict[str, Any]] = None,
    files: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 60.0,
    follow_redirects: bool = False,
    verify: bool = True,
) -> httpx.Response:
    async with httpx.AsyncClient(
        timeout=timeout,
        headers=headers,
        follow_redirects=follow_redirects,
        trust_env=False,
        verify=verify,
    ) as client:
        return await client.request(method, url, params=params, data=data, files=files)


async def _request(
    method: str,
    base_url: str,
    path: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    data: Optional[Dict[str, Any]] = None,
    files: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 60.0,
) -> Tuple[int, Dict[str, Any]]:
    from . import phagescope as facade

    url = f"{base_url}{path}"
    verify = facade._ssl_verify_enabled(base_url)
    try:
        response = await facade._do_httpx_request(
            method,
            url,
            params=params,
            data=data,
            files=files,
            headers=headers,
            timeout=timeout,
            verify=verify,
        )
        return response.status_code, facade._decode_httpx_response(response)
    except httpx.HTTPError as exc:
        if verify and facade._should_retry_without_ssl_verify(base_url, exc):
            logger.warning("PhageScope TLS verification failed for %s; retrying with verify=False", url)
            response = await facade._do_httpx_request(
                method,
                url,
                params=params,
                data=data,
                files=files,
                headers=headers,
                timeout=timeout,
                verify=False,
            )
            payload = facade._attach_transport_warning(
                facade._decode_httpx_response(response), facade._TLS_RETRY_WARNING
            )
            return response.status_code, payload
        raise


__all__ = [
    "_TLS_RETRY_WARNING", "_get_base_url", "_ssl_verify_enabled",
    "_should_retry_without_ssl_verify", "_attach_transport_warning",
    "_decode_httpx_response", "_do_httpx_request", "_request",
]
