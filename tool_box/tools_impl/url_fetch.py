"""Public URL download tool.

Download a file from a public HTTP(S) URL into a task/session output
directory with basic SSRF protections and structured artifact metadata.
"""

from __future__ import annotations

import hashlib
import ipaddress
import mimetypes
import os
import re
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urljoin
from uuid import uuid4

import httpx

from tool_box.context import ToolContext

_DEFAULT_TIMEOUT_SEC = 60.0
_DEFAULT_MAX_BYTES = 50 * 1024 * 1024
_DEFAULT_MAX_REDIRECTS = 5
_CONTENT_TYPE_RE = re.compile(r"^[A-Za-z0-9!#$&^_.+-]+/(?:[A-Za-z0-9!#$&^_.+-]+|\*)$")
_UNSAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_BLOCKED_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "127.0.0.1",
    "::1",
    "0.0.0.0",
    "host.docker.internal",
    "metadata",
    "metadata.google.internal",
}


class UrlFetchError(RuntimeError):
    def __init__(self, message: str, *, code: str, stage: str) -> None:
        super().__init__(message)
        self.code = code
        self.stage = stage


def _error_payload(
    message: str,
    *,
    code: str,
    stage: str,
    url: Optional[str] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "success": False,
        "tool": "url_fetch",
        "error": message,
        "error_code": code,
        "error_stage": stage,
    }
    if isinstance(url, str) and url.strip():
        payload["url"] = url.strip()
    return payload


def _normalize_url(raw_url: Optional[str]) -> str:
    text = str(raw_url or "").strip()
    if not text:
        raise UrlFetchError(
            "url is required.",
            code="missing_url",
            stage="input_validation",
        )
    try:
        parsed = httpx.URL(text)
    except Exception as exc:
        raise UrlFetchError(
            f"Invalid URL: {text}",
            code="invalid_url",
            stage="input_validation",
        ) from exc
    if parsed.scheme not in {"http", "https"}:
        raise UrlFetchError(
            "Only http and https URLs are supported.",
            code="invalid_scheme",
            stage="input_validation",
        )
    if not parsed.host:
        raise UrlFetchError(
            "URL must include a host.",
            code="invalid_url",
            stage="input_validation",
        )
    return str(parsed)


def _parse_timeout_sec(value: Optional[Any]) -> float:
    env_value = os.getenv("URL_FETCH_TIMEOUT_SEC")
    if value is None and env_value:
        value = env_value
    if value is None:
        return _DEFAULT_TIMEOUT_SEC
    try:
        timeout = float(value)
    except (TypeError, ValueError) as exc:
        raise UrlFetchError(
            "timeout_sec must be a number.",
            code="invalid_timeout",
            stage="input_validation",
        ) from exc
    if timeout <= 0:
        raise UrlFetchError(
            "timeout_sec must be positive.",
            code="invalid_timeout",
            stage="input_validation",
        )
    return timeout


def _parse_max_bytes(value: Optional[Any]) -> int:
    env_value = os.getenv("URL_FETCH_MAX_BYTES")
    if value is None and env_value:
        value = env_value
    if value is None:
        return _DEFAULT_MAX_BYTES
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise UrlFetchError(
            "max_bytes must be an integer.",
            code="invalid_max_bytes",
            stage="input_validation",
        ) from exc
    if parsed <= 0:
        raise UrlFetchError(
            "max_bytes must be positive.",
            code="invalid_max_bytes",
            stage="input_validation",
        )
    return parsed


def _normalize_allowed_content_types(value: Optional[Any]) -> List[str]:
    if value is None:
        return []
    raw_items: List[str] = []
    if isinstance(value, str):
        raw_items = [chunk.strip() for chunk in value.split(",") if chunk.strip()]
    elif isinstance(value, (list, tuple, set)):
        raw_items = [str(item).strip() for item in value if str(item).strip()]
    else:
        raise UrlFetchError(
            "allowed_content_types must be a list of MIME types.",
            code="invalid_allowed_content_types",
            stage="input_validation",
        )

    normalized: List[str] = []
    seen: set[str] = set()
    for item in raw_items:
        lowered = item.lower()
        if lowered in {"*", "*/*"}:
            lowered = "*/*"
        if lowered != "*/*" and not _CONTENT_TYPE_RE.match(lowered):
            raise UrlFetchError(
                f"Invalid content type matcher: {item}",
                code="invalid_allowed_content_types",
                stage="input_validation",
            )
        if lowered in seen:
            continue
        seen.add(lowered)
        normalized.append(lowered)
    return normalized


def _normalize_expected_sha256(value: Optional[Any]) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    if not re.fullmatch(r"[0-9a-f]{64}", text):
        raise UrlFetchError(
            "sha256 must be a 64-character hex string.",
            code="invalid_sha256",
            stage="input_validation",
        )
    return text


def _ensure_public_ip(ip: ipaddress._BaseAddress, *, host: str) -> None:
    if ip.is_global:
        return
    raise UrlFetchError(
        f"URL host is not publicly routable: {host}",
        code="non_public_host",
        stage="network_request",
    )


def _ensure_public_host(url: str) -> None:
    parsed = httpx.URL(url)
    host = str(parsed.host or "").strip().rstrip(".").lower()
    if not host:
        raise UrlFetchError(
            "URL must include a host.",
            code="invalid_url",
            stage="input_validation",
        )
    if host in _BLOCKED_HOSTS or host.endswith(".local") or host.endswith(".internal"):
        raise UrlFetchError(
            f"URL host is not allowed: {host}",
            code="non_public_host",
            stage="network_request",
        )

    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        ip = None

    if ip is not None:
        _ensure_public_ip(ip, host=host)
        return

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        addrinfo = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UrlFetchError(
            f"Failed to resolve host: {host}",
            code="dns_resolution_failed",
            stage="network_request",
        ) from exc

    if not addrinfo:
        raise UrlFetchError(
            f"Failed to resolve host: {host}",
            code="dns_resolution_failed",
            stage="network_request",
        )

    seen_ips: set[str] = set()
    for item in addrinfo:
        sockaddr = item[4] if len(item) >= 5 else ()
        if not sockaddr:
            continue
        address = str(sockaddr[0]).strip()
        if not address or address in seen_ips:
            continue
        seen_ips.add(address)
        try:
            resolved_ip = ipaddress.ip_address(address)
        except ValueError:
            continue
        _ensure_public_ip(resolved_ip, host=host)


def _normalize_content_type(value: Optional[str]) -> Optional[str]:
    text = str(value or "").strip().lower()
    if not text:
        return None
    return text.split(";", 1)[0].strip() or None


def _content_type_allowed(
    actual: Optional[str],
    allowed: Sequence[str],
) -> bool:
    if not allowed:
        return True
    if actual is None:
        return False
    lowered = actual.lower()
    for pattern in allowed:
        if pattern == "*/*":
            return True
        if pattern.endswith("/*"):
            prefix = pattern[:-1]
            if lowered.startswith(prefix):
                return True
        elif lowered == pattern:
            return True
    return False


def _resolve_output_dir(
    session_id: Optional[str],
    *,
    tool_context: Optional[ToolContext] = None,
) -> Path:
    work_dir = str(getattr(tool_context, "work_dir", "") or "").strip()
    if work_dir:
        target = Path(work_dir).expanduser().resolve(strict=False)
        target.mkdir(parents=True, exist_ok=True)
        return target

    token = str(session_id or "").strip()
    if token:
        try:
            from app.services.session_paths import get_session_tool_outputs_dir

            root = get_session_tool_outputs_dir(token, create=True)
            target = (root / "url_fetch").resolve()
            target.mkdir(parents=True, exist_ok=True)
            return target
        except Exception as exc:
            raise UrlFetchError(
                f"Failed to resolve session output directory: {exc}",
                code="output_dir_unavailable",
                stage="output_preparation",
            ) from exc

    project_root = Path(__file__).resolve().parents[2]
    target = (project_root / "runtime" / "url_fetch").resolve()
    target.mkdir(parents=True, exist_ok=True)
    return target


def _session_relative_path(path: Path, session_id: Optional[str]) -> Optional[str]:
    token = str(session_id or "").strip()
    if not token:
        return None
    try:
        from app.services.session_paths import get_runtime_session_dir

        session_root = get_runtime_session_dir(token, create=True).resolve()
        return str(path.resolve().relative_to(session_root)).replace("\\", "/")
    except Exception:
        return None


def _sanitize_filename(value: str) -> str:
    parts = Path(value).parts
    name = parts[-1] if parts else value
    text = str(name or "").strip()
    if not text:
        return ""
    return _UNSAFE_NAME_RE.sub("_", text).strip("._")


def _explicit_output_name(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    name = str(value).strip()
    if not name:
        return None
    if "/" in name or "\\" in name:
        raise UrlFetchError(
            "output_name must be a file name, not a path.",
            code="invalid_output_name",
            stage="input_validation",
        )
    sanitized = _sanitize_filename(name)
    if not sanitized:
        raise UrlFetchError(
            "output_name is invalid after sanitization.",
            code="invalid_output_name",
            stage="input_validation",
        )
    return sanitized


def _derived_output_name(
    *,
    output_name: Optional[str],
    final_url: str,
    content_type: Optional[str],
) -> str:
    explicit = _explicit_output_name(output_name)
    if explicit is not None:
        return explicit

    parsed = httpx.URL(final_url)
    raw_name = Path(parsed.path).name
    sanitized = _sanitize_filename(raw_name)
    if not sanitized:
        sanitized = "download"

    stem = Path(sanitized).stem or "download"
    suffix = Path(sanitized).suffix
    if not suffix:
        guessed = mimetypes.guess_extension(content_type or "")
        if guessed:
            suffix = guessed
    safe_stem = _sanitize_filename(stem) or "download"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"urlfetch_{safe_stem[:40]}_{timestamp}_{uuid4().hex[:8]}{suffix}"


async def _download_public_url(
    client: httpx.AsyncClient,
    url: str,
    *,
    temp_path: Path,
    timeout_sec: float,
    max_bytes: int,
    allowed_content_types: Sequence[str],
    max_redirects: int = _DEFAULT_MAX_REDIRECTS,
) -> Dict[str, Any]:
    current_url = url
    redirect_count = 0

    while True:
        current_url = _normalize_url(current_url)
        _ensure_public_host(current_url)
        async with client.stream("GET", current_url, timeout=timeout_sec, follow_redirects=False) as response:
            if response.status_code in {301, 302, 303, 307, 308}:
                if redirect_count >= max_redirects:
                    raise UrlFetchError(
                        "Too many redirects.",
                        code="too_many_redirects",
                        stage="network_request",
                    )
                location = str(response.headers.get("location") or "").strip()
                if not location:
                    raise UrlFetchError(
                        "Redirect response did not include a Location header.",
                        code="invalid_redirect",
                        stage="network_request",
                    )
                current_url = urljoin(current_url, location)
                redirect_count += 1
                continue

            if response.status_code >= 400:
                raise UrlFetchError(
                    f"HTTP {response.status_code} while downloading {current_url}",
                    code="upstream_http_error",
                    stage="network_request",
                )

            content_type = _normalize_content_type(response.headers.get("content-type"))
            if not _content_type_allowed(content_type, allowed_content_types):
                raise UrlFetchError(
                    f"Downloaded content type is not allowed: {content_type or 'unknown'}",
                    code="content_type_not_allowed",
                    stage="validation",
                )

            content_length = response.headers.get("content-length")
            if content_length is not None:
                try:
                    expected_bytes = int(content_length)
                except (TypeError, ValueError):
                    expected_bytes = None
                if expected_bytes is not None and expected_bytes > max_bytes:
                    raise UrlFetchError(
                        f"Downloaded content exceeds max_bytes={max_bytes}",
                        code="payload_too_large",
                        stage="validation",
                    )

            sha256 = hashlib.sha256()
            byte_count = 0
            try:
                with temp_path.open("wb") as handle:
                    async for chunk in response.aiter_bytes():
                        if not chunk:
                            continue
                        byte_count += len(chunk)
                        if byte_count > max_bytes:
                            raise UrlFetchError(
                                f"Downloaded content exceeds max_bytes={max_bytes}",
                                code="payload_too_large",
                                stage="validation",
                            )
                        handle.write(chunk)
                        sha256.update(chunk)
            except Exception:
                temp_path.unlink(missing_ok=True)
                raise

            return {
                "final_url": str(response.url),
                "status_code": int(response.status_code),
                "content_type": content_type,
                "bytes": byte_count,
                "sha256": sha256.hexdigest(),
            }


def _attach_output_metadata(
    payload: Dict[str, Any],
    *,
    output_path: Path,
    base_dir: Path,
    session_id: Optional[str],
    task_id: Optional[int],
    ancestor_chain: Optional[Sequence[int]],
) -> Dict[str, Any]:
    resolved_output = output_path.expanduser().resolve()
    resolved_base = base_dir.expanduser().resolve()
    out = dict(payload)
    out["output_file"] = str(resolved_output)
    out["saved_path"] = str(resolved_output)
    out["artifact_paths"] = [str(resolved_output)]
    out["produced_files"] = [str(resolved_output)]

    rel_path = _session_relative_path(resolved_output, session_id)
    files: List[str]
    if rel_path:
        out["output_file_rel"] = rel_path
        out["saved_path_rel"] = rel_path
        out["session_artifact_paths"] = [rel_path]
        files = [rel_path]
    else:
        files = [str(resolved_output)]

    out["output_location"] = {
        "type": "task" if task_id is not None else "tmp",
        "session_id": session_id,
        "task_id": task_id,
        "ancestor_chain": list(ancestor_chain or []) or None,
        "base_dir": str(resolved_base),
        "files": files,
    }
    return out


async def url_fetch_handler(
    url: str,
    output_name: Optional[str] = None,
    session_id: Optional[str] = None,
    timeout_sec: Optional[Any] = None,
    max_bytes: Optional[Any] = None,
    allowed_content_types: Optional[Any] = None,
    sha256: Optional[Any] = None,
    task_id: Optional[int] = None,
    ancestor_chain: Optional[Sequence[int]] = None,
    tool_context: Optional[ToolContext] = None,
) -> Dict[str, Any]:
    """Download a public URL into the current task/session output directory."""

    normalized_url = str(url or "").strip()
    effective_session_id = (
        str(session_id).strip()
        if isinstance(session_id, str) and session_id.strip()
        else str(getattr(tool_context, "session_id", "") or "").strip() or None
    )
    effective_task_id = task_id if task_id is not None else getattr(tool_context, "task_id", None)
    effective_ancestor_chain = list(ancestor_chain or [])

    try:
        normalized_url = _normalize_url(url)
        timeout = _parse_timeout_sec(timeout_sec)
        max_bytes_value = _parse_max_bytes(max_bytes)
        allowed_types = _normalize_allowed_content_types(allowed_content_types)
        expected_sha256 = _normalize_expected_sha256(sha256)

        output_dir = _resolve_output_dir(effective_session_id, tool_context=tool_context)
        temp_path = output_dir / f".url_fetch_{uuid4().hex}.part"

        async with httpx.AsyncClient() as client:
            download = await _download_public_url(
                client,
                normalized_url,
                temp_path=temp_path,
                timeout_sec=timeout,
                max_bytes=max_bytes_value,
                allowed_content_types=allowed_types,
            )

        actual_sha256 = str(download["sha256"])
        if expected_sha256 and actual_sha256 != expected_sha256:
            temp_path.unlink(missing_ok=True)
            raise UrlFetchError(
                "Downloaded file sha256 did not match the expected value.",
                code="sha256_mismatch",
                stage="validation",
            )

        filename = _derived_output_name(
            output_name=output_name,
            final_url=str(download["final_url"]),
            content_type=download.get("content_type"),
        )
        output_path = (output_dir / filename).resolve()
        if output_path.parent != output_dir:
            temp_path.unlink(missing_ok=True)
            raise UrlFetchError(
                "Resolved output path escapes target directory.",
                code="invalid_output_name",
                stage="output_preparation",
            )
        temp_path.replace(output_path)

        payload: Dict[str, Any] = {
            "success": True,
            "tool": "url_fetch",
            "url": normalized_url,
            "final_url": str(download["final_url"]),
            "status_code": int(download["status_code"]),
            "content_type": download.get("content_type"),
            "bytes": int(download["bytes"]),
            "sha256": actual_sha256,
        }
        return _attach_output_metadata(
            payload,
            output_path=output_path,
            base_dir=output_dir,
            session_id=effective_session_id,
            task_id=effective_task_id,
            ancestor_chain=effective_ancestor_chain,
        )

    except UrlFetchError as exc:
        return _error_payload(
            str(exc),
            code=exc.code,
            stage=exc.stage,
            url=normalized_url or str(url or "").strip(),
        )
    except Exception as exc:  # pragma: no cover - defensive
        return _error_payload(
            f"url_fetch failed: {exc}",
            code="url_fetch_internal_error",
            stage="internal",
            url=normalized_url or str(url or "").strip(),
        )


url_fetch_tool = {
    "name": "url_fetch",
    "description": (
        "Download a file from a public http/https URL into the current task/session output directory. "
        "Supports optional content-type and sha256 validation."
    ),
    "category": "network",
    "parameters_schema": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Public http/https URL to download.",
            },
            "output_name": {
                "type": "string",
                "description": "Optional output file name. Must be a file name, not a path.",
            },
            "session_id": {
                "type": "string",
                "description": "Optional session id for session-scoped output storage.",
            },
            "timeout_sec": {
                "type": "number",
                "default": _DEFAULT_TIMEOUT_SEC,
                "description": "Network timeout in seconds.",
            },
            "max_bytes": {
                "type": "integer",
                "default": _DEFAULT_MAX_BYTES,
                "description": "Maximum number of bytes to download.",
            },
            "allowed_content_types": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional allowed MIME types (exact match or major-type wildcard like text/*).",
            },
            "sha256": {
                "type": "string",
                "description": "Optional expected sha256 hex digest for integrity verification.",
            },
        },
        "required": ["url"],
    },
    "handler": url_fetch_handler,
    "tags": ["download", "url", "http", "https", "artifact"],
    "examples": [
        "Download a public PDF from a direct https link",
        "Download a CSV and require text/csv content type",
    ],
}
