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


def test_sequence_fetch_persist_false_returns_fasta_without_writing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _must_not_resolve(_sid: Any) -> Path:
        raise AssertionError("output directory must not be resolved when persist=False")

    monkeypatch.setattr(sequence_fetch_module, "_resolve_output_dir", _must_not_resolve)

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
        sequence_fetch_module.sequence_fetch_handler(accession="NC_001416.1", persist=False)
    )

    assert result["success"] is True
    assert result["fasta"].startswith(">NC_001416.1")
    assert result["record_count"] == 1
    assert "output_file" not in result
    assert "output_file_rel" not in result


def test_sequence_fetch_default_still_persists_and_omits_inline_fasta(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(sequence_fetch_module, "_resolve_output_dir", lambda _sid: tmp_path)

    async def _fake_ncbi(
        _client: Any,
        _accessions: Sequence[str],
        *,
        database: str,
        timeout_sec: float,
    ) -> str:
        _ = (database, timeout_sec)
        return ">NC_001416.1\nACGT\n"

    monkeypatch.setattr(sequence_fetch_module, "_fetch_ncbi_fasta", _fake_ncbi)

    result = asyncio.run(sequence_fetch_module.sequence_fetch_handler(accession="NC_001416.1"))

    assert Path(result["output_file"]).exists()
    assert "fasta" not in result


def test_sequence_fetch_tool_schema_does_not_advertise_persist() -> None:
    assert "persist" not in sequence_fetch_module.sequence_fetch_tool["parameters_schema"]["properties"]


# ------------------------------------------------------------ formats, batching, cache (shared NCBI client)
class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text


@pytest.fixture()
def fake_efetch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Replace the shared E-utilities request; records every efetch batch."""
    from tool_box.tools_impl import ncbi_eutils

    sequence_fetch_module._RECORD_CACHE.clear()
    monkeypatch.setattr(sequence_fetch_module, "_resolve_output_dir", lambda _sid: tmp_path)
    calls: list = []
    replies: Dict[str, Any] = {}

    async def _request(endpoint, params, **kwargs):
        calls.append({"endpoint": endpoint, "owner": ncbi_eutils._current_owner.get(), **params})
        reply = replies.get(params["rettype"], "")
        if isinstance(reply, Exception):
            raise reply
        if callable(reply):
            return _FakeResponse(reply(params["id"].split(",")))
        return _FakeResponse(reply)

    monkeypatch.setattr(ncbi_eutils, "request", _request)
    yield calls, replies
    sequence_fetch_module._RECORD_CACHE.clear()


_GENBANK = "LOCUS       NC_001416  48502 bp    DNA     linear   PHG 11-JAN-2023\nDEFINITION  Enterobacteria phage lambda.\n//\n"


def test_genbank_format_uses_rettype_gb_and_a_gb_file(fake_efetch, tmp_path: Path) -> None:
    calls, replies = fake_efetch
    replies["gb"] = _GENBANK
    result = asyncio.run(sequence_fetch_module.sequence_fetch_handler(accession="NC_001416.1", format="genbank", session_id="s9"))
    assert result["success"] is True and result["format"] == "genbank" and result["record_count"] == 1
    assert result["output_file"].endswith(".gb")
    assert Path(result["output_file"]).read_text().startswith("LOCUS")
    assert calls[0]["rettype"] == "gb" and calls[0]["db"] == "nuccore"
    assert calls[0]["owner"] == "s9"


def test_protein_genbank_asks_for_genpept(fake_efetch) -> None:
    calls, replies = fake_efetch
    replies["gp"] = _GENBANK.replace("NC_001416", "NP_040580")
    result = asyncio.run(
        sequence_fetch_module.sequence_fetch_handler(accession="NP_040580.1", database="protein", format="genbank", persist=False)
    )
    assert result["success"] is True and calls[0]["rettype"] == "gp"
    assert result["genbank"].startswith("LOCUS") and "fasta" not in result


def test_cds_proteins_come_back_as_faa_without_ena_fallback(fake_efetch, monkeypatch: pytest.MonkeyPatch) -> None:
    calls, replies = fake_efetch
    replies["fasta_cds_aa"] = ">lcl|NC_001604.1_prot_NP_041954.1_1 [protein=ocr]\nMAMSN\n>lcl|NC_001604.1_prot_NP_041955.1_2 [protein=x]\nMKK\n"

    async def _must_not_fall_back(*_args, **_kwargs):
        raise AssertionError("CDS formats exist only at NCBI")

    monkeypatch.setattr(sequence_fetch_module, "_fetch_ena_fasta", _must_not_fall_back)
    result = asyncio.run(sequence_fetch_module.sequence_fetch_handler(accession="NC_001604.1", format="fasta_cds_aa"))
    assert result["success"] is True and result["record_count"] == 2
    assert result["output_file"].endswith(".faa")
    assert calls[0]["rettype"] == "fasta_cds_aa"


def test_an_unannotated_record_reports_no_cds(fake_efetch) -> None:
    _calls, replies = fake_efetch
    replies["fasta_cds_na"] = ""
    result = asyncio.run(sequence_fetch_module.sequence_fetch_handler(accession="MN000001.1", format="fasta_cds_na"))
    assert result["success"] is False and result["error_code"] == "no_cds_features"


@pytest.mark.parametrize(
    "database, fmt",
    [("nuccore", "embl"), ("protein", "fasta_cds_aa"), ("protein", "fasta_cds_na")],
)
def test_unsupported_formats_are_refused(fake_efetch, database: str, fmt: str) -> None:
    calls, _replies = fake_efetch
    result = asyncio.run(sequence_fetch_module.sequence_fetch_handler(accession="NC_001416.1", database=database, format=fmt))
    assert result["success"] is False and result["error_code"] == "invalid_format"
    assert calls == []


def test_long_accession_lists_go_out_in_batches_of_200_and_are_cached(fake_efetch) -> None:
    calls, replies = fake_efetch
    replies["fasta"] = lambda ids: "".join(f">{acc}\nACGT\n" for acc in ids)
    accessions = [f"MN{n:06d}.1" for n in range(450)]

    first = asyncio.run(sequence_fetch_module.sequence_fetch_handler(accessions=accessions, persist=False))
    assert first["success"] is True and first["record_count"] == 450
    assert [len(call["id"].split(",")) for call in calls] == [200, 200, 50]

    second = asyncio.run(sequence_fetch_module.sequence_fetch_handler(accessions=accessions, persist=False))
    assert second["record_count"] == 450 and len(calls) == 3  # served from the cache


def test_unversioned_accessions_are_not_cached(fake_efetch) -> None:
    calls, replies = fake_efetch
    replies["fasta"] = ">NC_001416.1\nACGT\n"
    for _ in range(2):
        asyncio.run(sequence_fetch_module.sequence_fetch_handler(accession="NC_001416", persist=False))
    assert len(calls) == 2


def test_rate_limited_ncbi_surfaces_its_own_error_code(fake_efetch) -> None:
    from tool_box.tools_impl import ncbi_eutils

    _calls, replies = fake_efetch
    replies["gb"] = ncbi_eutils.NcbiRequestError("NCBI efetch.fcgi rate limit exceeded", status_code=429, rate_limited=True)
    result = asyncio.run(sequence_fetch_module.sequence_fetch_handler(accession="NC_001416.1", format="genbank"))
    assert result["success"] is False and result["error_code"] == "upstream_rate_limited"
    assert result["no_claude_fallback"] is True


def test_explicit_output_names_get_the_format_suffix(fake_efetch) -> None:
    _calls, replies = fake_efetch
    replies["gb"] = _GENBANK
    result = asyncio.run(
        sequence_fetch_module.sequence_fetch_handler(accession="NC_001416.1", format="genbank", output_name="lambda")
    )
    assert result["output_file"].endswith("lambda.gb")
