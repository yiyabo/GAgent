"""Sequence fetch tool.

Deterministic accession -> FASTA downloader with strict domain allowlist,
file output persistence, and structured error semantics.
"""

from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from uuid import uuid4

import httpx

_ALLOWED_HOSTS = {
    "eutils.ncbi.nlm.nih.gov",
    "www.ebi.ac.uk",
    "rest.uniprot.org",
}

_DEFAULT_TIMEOUT_SEC = 30.0
_DEFAULT_MAX_BYTES = 10 * 1024 * 1024
_ACCESSION_PATTERN = re.compile(r"^[A-Za-z]{1,6}_?\d+(?:\.\d+)?$")
_UNIPROT_PATTERN = re.compile(r"^[A-Za-z0-9]{6,10}$")


class SequenceFetchError(RuntimeError):
    def __init__(self, message: str, *, code: str, stage: str) -> None:
        super().__init__(message)
        self.code = code
        self.stage = stage


def _error_payload(
    message: str,
    *,
    code: str,
    stage: str,
    accessions: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "success": False,
        "error": message,
        "error_code": code,
        "error_stage": stage,
        "no_claude_fallback": True,
        "tool": "sequence_fetch",
    }
    if accessions:
        payload["accessions"] = list(accessions)
    return payload


def _normalize_accessions(
    accession: Optional[str],
    accessions: Optional[Sequence[Any]],
) -> List[str]:
    if accession and accessions:
        raise SequenceFetchError(
            "Provide either accession or accessions, not both.",
            code="accession_input_ambiguous",
            stage="input_validation",
        )

    values: List[str] = []
    if isinstance(accession, str) and accession.strip():
        values = [chunk.strip() for chunk in re.split(r"[\s,;]+", accession.strip()) if chunk.strip()]
    elif isinstance(accessions, (list, tuple, set)):
        for item in accessions:
            if item is None:
                continue
            text = str(item).strip()
            if not text:
                continue
            parts = [chunk.strip() for chunk in re.split(r"[\s,;]+", text) if chunk.strip()]
            values.extend(parts)

    deduped: List[str] = []
    seen = set()
    for raw in values:
        token = raw.strip()
        if not token:
            continue
        token_upper = token.upper()
        if token_upper in seen:
            continue
        seen.add(token_upper)
        deduped.append(token)

    if not deduped:
        raise SequenceFetchError(
            "At least one accession is required.",
            code="missing_accession",
            stage="input_validation",
        )

    invalid = [item for item in deduped if not (_ACCESSION_PATTERN.match(item) or _UNIPROT_PATTERN.match(item))]
    if invalid:
        raise SequenceFetchError(
            f"Invalid accession: {invalid[0]}",
            code="invalid_accession",
            stage="input_validation",
        )

    return deduped


def _parse_max_bytes(max_bytes: Optional[Any]) -> int:
    env_value = os.getenv("SEQUENCE_FETCH_MAX_BYTES")
    if max_bytes is None and env_value:
        max_bytes = env_value
    if max_bytes is None:
        return _DEFAULT_MAX_BYTES
    try:
        parsed = int(max_bytes)
    except (TypeError, ValueError) as exc:
        raise SequenceFetchError(
            "max_bytes must be an integer.",
            code="invalid_max_bytes",
            stage="input_validation",
        ) from exc
    if parsed <= 0:
        raise SequenceFetchError(
            "max_bytes must be positive.",
            code="invalid_max_bytes",
            stage="input_validation",
        )
    return parsed


def _resolve_output_dir(session_id: Optional[str]) -> Path:
    if isinstance(session_id, str) and session_id.strip():
        try:
            from app.services.session_paths import get_session_tool_outputs_dir

            root = get_session_tool_outputs_dir(session_id.strip(), create=True)
            target = (root / "sequence_fetch").resolve()
            target.mkdir(parents=True, exist_ok=True)
            return target
        except Exception as exc:
            raise SequenceFetchError(
                f"Failed to resolve session output directory: {exc}",
                code="output_dir_unavailable",
                stage="output_preparation",
            ) from exc

    project_root = Path(__file__).resolve().parents[2]
    target = (project_root / "runtime" / "sequence_fetch").resolve()
    target.mkdir(parents=True, exist_ok=True)
    return target


def _safe_output_name(value: Optional[str], first_accession: str) -> str:
    if isinstance(value, str) and value.strip():
        name = value.strip()
        if "/" in name or "\\" in name:
            raise SequenceFetchError(
                "output_name must be a file name, not a path.",
                code="invalid_output_name",
                stage="input_validation",
            )
        if not name.lower().endswith(".fasta"):
            name = f"{name}.fasta"
        return name

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    suffix = uuid4().hex[:8]
    safe_acc = re.sub(r"[^A-Za-z0-9_.-]+", "_", first_accession)[:32] or "sequence"
    return f"seqfetch_{safe_acc}_{timestamp}_{suffix}.fasta"


def _ensure_host_allowed(url: str) -> None:
    host = httpx.URL(url).host or ""
    if host.lower() not in _ALLOWED_HOSTS:
        raise SequenceFetchError(
            f"Domain not allowed: {host}",
            code="domain_not_allowed",
            stage="network_request",
        )


def _count_fasta_records(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.startswith(">"))


def _validate_fasta_text(text: str, *, max_bytes: int) -> Tuple[str, int]:
    encoded = text.encode("utf-8", errors="replace")
    byte_count = len(encoded)
    if byte_count > max_bytes:
        raise SequenceFetchError(
            f"Downloaded content exceeds max_bytes={max_bytes}",
            code="payload_too_large",
            stage="validation",
        )

    stripped = text.strip()
    if not stripped:
        raise SequenceFetchError(
            "Downloaded FASTA payload is empty.",
            code="empty_fasta",
            stage="validation",
        )
    if not any(line.startswith(">") for line in stripped.splitlines()):
        raise SequenceFetchError(
            "Downloaded payload is not FASTA.",
            code="invalid_fasta",
            stage="validation",
        )
    records = _count_fasta_records(stripped)
    if records <= 0:
        raise SequenceFetchError(
            "No FASTA records found.",
            code="invalid_fasta",
            stage="validation",
        )
    return stripped + "\n", byte_count


def _normalize_database(database: Optional[str]) -> str:
    value = str(database or "nuccore").strip().lower()
    if value not in {"nuccore", "protein"}:
        raise SequenceFetchError(
            "database must be one of: nuccore, protein",
            code="invalid_database",
            stage="input_validation",
        )
    return value


def _normalize_format(fmt: Optional[str]) -> str:
    value = str(fmt or "fasta").strip().lower()
    if value != "fasta":
        raise SequenceFetchError(
            "Only FASTA format is supported.",
            code="invalid_format",
            stage="input_validation",
        )
    return value


async def _http_get_text(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    timeout_sec: float,
) -> str:
    _ensure_host_allowed(url)
    response = await client.get(url, params=params, timeout=timeout_sec)
    if response.status_code >= 400:
        raise SequenceFetchError(
            f"HTTP {response.status_code} for {url}",
            code="upstream_http_error",
            stage="network_request",
        )
    return response.text


async def _fetch_ncbi_fasta(
    client: httpx.AsyncClient,
    accessions: Sequence[str],
    *,
    database: str,
    timeout_sec: float,
) -> str:
    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    params = {
        "db": database,
        "rettype": "fasta",
        "retmode": "text",
        "id": ",".join(accessions),
    }
    return await _http_get_text(client, url, params=params, timeout_sec=timeout_sec)


async def _fetch_ena_fasta(
    client: httpx.AsyncClient,
    accessions: Sequence[str],
    *,
    timeout_sec: float,
) -> str:
    url = "https://www.ebi.ac.uk/ena/browser/api/fasta"
    params = {
        "accession": ",".join(accessions),
        "download": "true",
    }
    return await _http_get_text(client, url, params=params, timeout_sec=timeout_sec)


async def _fetch_uniprot_fasta(
    client: httpx.AsyncClient,
    accessions: Sequence[str],
    *,
    timeout_sec: float,
) -> str:
    combined: List[str] = []
    for acc in accessions:
        url = f"https://rest.uniprot.org/uniprotkb/{acc}.fasta"
        text = await _http_get_text(client, url, params=None, timeout_sec=timeout_sec)
        cleaned = text.strip()
        if cleaned:
            combined.append(cleaned)
    return "\n".join(combined) + ("\n" if combined else "")


async def sequence_fetch_handler(
    accession: Optional[str] = None,
    accessions: Optional[Sequence[Any]] = None,
    database: str = "nuccore",
    format: str = "fasta",
    session_id: Optional[str] = None,
    output_name: Optional[str] = None,
    timeout_sec: Optional[float] = None,
    max_bytes: Optional[Any] = None,
) -> Dict[str, Any]:
    """Download FASTA records for accession(s) and persist to runtime output."""

    try:
        normalized_accessions = _normalize_accessions(accession, accessions)
        normalized_database = _normalize_database(database)
        _normalize_format(format)
        max_bytes_value = _parse_max_bytes(max_bytes)

        timeout = float(timeout_sec) if timeout_sec is not None else _DEFAULT_TIMEOUT_SEC
        if timeout <= 0:
            raise SequenceFetchError(
                "timeout_sec must be positive.",
                code="invalid_timeout",
                stage="input_validation",
            )

        output_dir = _resolve_output_dir(session_id)
        filename = _safe_output_name(output_name, normalized_accessions[0])
        output_path = (output_dir / filename).resolve()
        if output_path.parent != output_dir:
            raise SequenceFetchError(
                "Resolved output path escapes target directory.",
                code="invalid_output_name",
                stage="output_preparation",
            )

        provider = "ncbi_efetch"
        fasta_text = ""
        byte_count = 0
        last_error: Optional[SequenceFetchError] = None

        async with httpx.AsyncClient(follow_redirects=True) as client:
            provider_chain = [
                (
                    "ncbi_efetch",
                    lambda: _fetch_ncbi_fasta(
                        client,
                        normalized_accessions,
                        database=normalized_database,
                        timeout_sec=timeout,
                    ),
                ),
            ]
            if normalized_database == "nuccore":
                provider_chain.append(
                    (
                        "ena_fasta",
                        lambda: _fetch_ena_fasta(
                            client,
                            normalized_accessions,
                            timeout_sec=timeout,
                        ),
                    )
                )
            elif normalized_database == "protein":
                provider_chain.append(
                    (
                        "uniprot_fasta",
                        lambda: _fetch_uniprot_fasta(
                            client,
                            normalized_accessions,
                            timeout_sec=timeout,
                        ),
                    )
                )

            for candidate_provider, fetcher in provider_chain:
                try:
                    candidate_text = await fetcher()
                    validated_text, validated_bytes = _validate_fasta_text(
                        candidate_text,
                        max_bytes=max_bytes_value,
                    )
                    provider = candidate_provider
                    fasta_text = validated_text
                    byte_count = validated_bytes
                    last_error = None
                    break
                except SequenceFetchError as exc:
                    last_error = exc

            if not fasta_text:
                if last_error is not None:
                    raise last_error
                raise SequenceFetchError(
                    "No sequence provider returned valid FASTA data.",
                    code="download_failed",
                    stage="network_request",
                )

        output_path.write_text(fasta_text, encoding="utf-8")

        sha256 = hashlib.sha256(fasta_text.encode("utf-8", errors="replace")).hexdigest()
        record_count = _count_fasta_records(fasta_text)

        output_file_rel: Optional[str] = None
        if isinstance(session_id, str) and session_id.strip():
            try:
                from app.services.session_paths import get_runtime_session_dir

                session_root = get_runtime_session_dir(session_id.strip(), create=True)
                output_file_rel = str(output_path.relative_to(session_root))
            except Exception:
                output_file_rel = None

        payload: Dict[str, Any] = {
            "success": True,
            "tool": "sequence_fetch",
            "accessions": normalized_accessions,
            "database": normalized_database,
            "format": "fasta",
            "provider": provider,
            "output_file": str(output_path),
            "record_count": record_count,
            "bytes": byte_count,
            "sha256": sha256,
            "no_claude_fallback": False,
        }
        if output_file_rel:
            payload["output_file_rel"] = output_file_rel
        return payload

    except SequenceFetchError as exc:
        return _error_payload(
            str(exc),
            code=exc.code,
            stage=exc.stage,
            accessions=None,
        )
    except Exception as exc:  # pragma: no cover - defensive
        return _error_payload(
            f"sequence_fetch failed: {exc}",
            code="sequence_fetch_internal_error",
            stage="internal",
            accessions=None,
        )


sequence_fetch_tool = {
    "name": "sequence_fetch",
    "description": (
        "Deterministic accession-to-FASTA downloader with strict allowlist. "
        "Use this when users ask to download sequence FASTA by accession IDs."
    ),
    "category": "bioinformatics",
    "parameters_schema": {
        "type": "object",
        "properties": {
            "accession": {
                "type": "string",
                "description": "Single accession ID (mutually exclusive with accessions).",
            },
            "accessions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Multiple accession IDs (mutually exclusive with accession).",
            },
            "database": {
                "type": "string",
                "enum": ["nuccore", "protein"],
                "default": "nuccore",
                "description": "NCBI database type.",
            },
            "format": {
                "type": "string",
                "enum": ["fasta"],
                "default": "fasta",
                "description": "Sequence output format.",
            },
            "session_id": {
                "type": "string",
                "description": "Optional chat session id for session-scoped output storage.",
            },
            "output_name": {
                "type": "string",
                "description": "Optional output filename ('.fasta' appended if missing).",
            },
            "timeout_sec": {
                "type": "number",
                "default": _DEFAULT_TIMEOUT_SEC,
                "description": "Network timeout in seconds.",
            },
            "max_bytes": {
                "type": "integer",
                "default": _DEFAULT_MAX_BYTES,
                "description": "Maximum allowed response bytes.",
            },
        },
        "anyOf": [
            {"required": ["accession"]},
            {"required": ["accessions"]},
        ],
    },
    "handler": sequence_fetch_handler,
    "tags": ["sequence", "fasta", "accession", "download", "ncbi", "ena"],
    "examples": [
        "Download NC_001416.1 as FASTA",
        "Download accessions NC_001416.1 and NC_001417.1",
    ],
}
