from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
from typing import Any, Dict, Sequence

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import tool_box
from app.routers import tool_routes
from app.services import session_paths

sequence_fetch_module = importlib.import_module("tool_box.tools_impl.sequence_fetch")


def test_sequence_fetch_single_accession_writes_session_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session_id = "session_abc123"
    session_root = tmp_path / "runtime" / "session_abc123"
    output_dir = session_root / "tool_outputs" / "sequence_fetch"
    output_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(sequence_fetch_module, "_resolve_output_dir", lambda _sid: output_dir)

    def _fake_runtime_session_dir(_sid: str, *, create: bool = False) -> Path:
        if create:
            session_root.mkdir(parents=True, exist_ok=True)
        return session_root

    monkeypatch.setattr(session_paths, "get_runtime_session_dir", _fake_runtime_session_dir)

    async def _fake_ncbi(
        _client: Any,
        _accessions: Sequence[str],
        *,
        database: str,
        timeout_sec: float,
    ) -> str:
        _ = (database, timeout_sec)
        return ">NC_001416.1\nACGTACGT\n"

    monkeypatch.setattr(sequence_fetch_module, "_fetch_ncbi_fasta", _fake_ncbi)

    result = asyncio.run(
        sequence_fetch_module.sequence_fetch_handler(
            accession="NC_001416.1",
            session_id=session_id,
        )
    )

    assert result["success"] is True
    assert result["provider"] == "ncbi_efetch"
    assert result["record_count"] == 1
    assert result["output_file_rel"].startswith("tool_outputs/sequence_fetch/")

    output_file = Path(result["output_file"])
    assert output_file.exists()
    assert output_file.parent == output_dir
    assert output_file.read_text(encoding="utf-8").startswith(">NC_001416.1")


def test_sequence_fetch_multiple_accessions_reports_record_count(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "out"
    output_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(sequence_fetch_module, "_resolve_output_dir", lambda _sid: output_dir)

    async def _fake_ncbi(
        _client: Any,
        accessions: Sequence[str],
        *,
        database: str,
        timeout_sec: float,
    ) -> str:
        _ = (database, timeout_sec)
        assert list(accessions) == ["NC_000001.1", "NC_000002.1"]
        return ">NC_000001.1\nAAAA\n>NC_000002.1\nCCCC\n"

    monkeypatch.setattr(sequence_fetch_module, "_fetch_ncbi_fasta", _fake_ncbi)

    result = asyncio.run(
        sequence_fetch_module.sequence_fetch_handler(
            accessions=["NC_000001.1", "NC_000002.1"],
        )
    )

    assert result["success"] is True
    assert result["record_count"] == 2
    assert result["accessions"] == ["NC_000001.1", "NC_000002.1"]


def test_sequence_fetch_rejects_invalid_accession() -> None:
    result = asyncio.run(
        sequence_fetch_module.sequence_fetch_handler(
            accession="not_an_accession!!",
        )
    )

    assert result["success"] is False
    assert result["error_code"] == "invalid_accession"
    assert result["error_stage"] == "input_validation"
    assert result["no_claude_fallback"] is True


def test_sequence_fetch_falls_back_to_ena_when_ncbi_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "out"
    output_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(sequence_fetch_module, "_resolve_output_dir", lambda _sid: output_dir)

    async def _fail_ncbi(
        _client: Any,
        _accessions: Sequence[str],
        *,
        database: str,
        timeout_sec: float,
    ) -> str:
        _ = (database, timeout_sec)
        raise sequence_fetch_module.SequenceFetchError(
            "NCBI unavailable",
            code="upstream_http_error",
            stage="network_request",
        )

    async def _ok_ena(
        _client: Any,
        _accessions: Sequence[str],
        *,
        timeout_sec: float,
    ) -> str:
        _ = timeout_sec
        return ">ENA_SEQ\nACGT\n"

    monkeypatch.setattr(sequence_fetch_module, "_fetch_ncbi_fasta", _fail_ncbi)
    monkeypatch.setattr(sequence_fetch_module, "_fetch_ena_fasta", _ok_ena)

    result = asyncio.run(
        sequence_fetch_module.sequence_fetch_handler(
            accession="NC_001416.1",
            database="nuccore",
        )
    )

    assert result["success"] is True
    assert result["provider"] == "ena_fasta"
    assert result["record_count"] == 1


def test_sequence_fetch_error_payload_blocks_claude_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "out"
    output_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(sequence_fetch_module, "_resolve_output_dir", lambda _sid: output_dir)

    async def _fail_ncbi(
        _client: Any,
        _accessions: Sequence[str],
        *,
        database: str,
        timeout_sec: float,
    ) -> str:
        _ = (database, timeout_sec)
        raise sequence_fetch_module.SequenceFetchError(
            "upstream timeout",
            code="upstream_http_error",
            stage="network_request",
        )

    async def _fail_ena(
        _client: Any,
        _accessions: Sequence[str],
        *,
        timeout_sec: float,
    ) -> str:
        _ = timeout_sec
        raise sequence_fetch_module.SequenceFetchError(
            "upstream timeout",
            code="upstream_http_error",
            stage="network_request",
        )

    monkeypatch.setattr(sequence_fetch_module, "_fetch_ncbi_fasta", _fail_ncbi)
    monkeypatch.setattr(sequence_fetch_module, "_fetch_ena_fasta", _fail_ena)

    result = asyncio.run(
        sequence_fetch_module.sequence_fetch_handler(
            accession="NC_001416.1",
            database="nuccore",
        )
    )

    assert result["success"] is False
    assert result["error_code"] == "upstream_http_error"
    assert result["no_claude_fallback"] is True


def test_sequence_fetch_rejects_non_allowlisted_domain() -> None:
    with pytest.raises(sequence_fetch_module.SequenceFetchError) as exc:
        sequence_fetch_module._ensure_host_allowed("https://evil.example.com/fasta")

    assert exc.value.code == "domain_not_allowed"
    assert exc.value.stage == "network_request"


def test_sequence_fetch_route_passthrough(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: Dict[str, Any] = {}

    async def _fake_execute_tool(name: str, **kwargs: Any) -> Dict[str, Any]:
        captured["name"] = name
        captured["kwargs"] = kwargs
        return {
            "success": True,
            "tool": "sequence_fetch",
            "output_file": "/tmp/example.fasta",
            "record_count": 1,
        }

    monkeypatch.setattr(tool_box, "execute_tool", _fake_execute_tool)

    app = FastAPI()
    app.include_router(tool_routes.router)
    client = TestClient(app)

    response = client.post(
        "/tools/sequence-fetch",
        json={
            "accession": "NC_001416.1",
            "database": "nuccore",
            "format": "fasta",
            "session_id": "session_demo",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert captured["name"] == "sequence_fetch"
    assert captured["kwargs"]["accession"] == "NC_001416.1"
    assert captured["kwargs"]["session_id"] == "session_demo"
