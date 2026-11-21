"""Chunked uploads: large files arrive as numbered pieces that can be re-sent."""
from __future__ import annotations

import gzip
import io
import os
import time
import zipfile

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.database import get_db
from app.database_pool import initialize_connection_pool
from app.routers import upload_routes
from app.services.request_principal import RequestPrincipal

SESSION = "session_owner1"


@pytest.fixture
def client(monkeypatch, tmp_path, isolated_app_env):
    initialize_connection_pool(db_path=str(tmp_path / "chunks.db"), pool_size=1, max_overflow=1)
    with get_db() as conn:
        conn.execute("""CREATE TABLE chat_sessions (
            id TEXT PRIMARY KEY, owner_id TEXT, name TEXT, name_source TEXT,
            is_user_named INTEGER, metadata TEXT, last_message_at TEXT,
            created_at TEXT, updated_at TEXT, is_active INTEGER)""")
        conn.execute("INSERT INTO chat_sessions(id,owner_id) VALUES (?,?)", (SESSION, "alice"))
        conn.commit()
    monkeypatch.setenv("APP_RUNTIME_ROOT", str(tmp_path / "runtime"))
    app = FastAPI()

    @app.middleware("http")
    async def principal(request: Request, call_next):
        request.state.principal = RequestPrincipal(user_id=request.headers.get("x-user", "alice"), email=None)
        return await call_next(request)

    app.include_router(upload_routes.router)
    with TestClient(app) as test_client:
        yield test_client, tmp_path / "runtime" / SESSION


def _pieces(data: bytes, size: int) -> list[bytes]:
    return [data[i:i + size] for i in range(0, len(data), size)]


def _form(upload_id: str, name: str, data: bytes, total: int, **extra) -> dict:
    return {"session_id": SESSION, "upload_id": upload_id, "file_name": name,
            "total": str(total), "total_size": str(len(data)), **extra}


def _send(client, upload_id, name, data, pieces, index, headers=None):
    return client.post(
        "/upload/chunk",
        data=_form(upload_id, name, data, len(pieces), index=str(index)),
        files={"chunk": ("blob", pieces[index], "application/octet-stream")},
        headers=headers or {},
    )


def test_chunked_fastq_upload_joins_pieces_and_resent_piece_is_idempotent(client) -> None:
    test_client, session_root = client
    data = gzip.compress(os.urandom(300_000))
    pieces = _pieces(data, 64 * 1024)
    upload_id = "upl_abcdef12"

    for index in range(len(pieces)):
        assert _send(test_client, upload_id, "A514_R2.fq.gz", data, pieces, index).status_code == 200
    # A piece whose response was lost is sent again; it replaces the stored copy.
    assert _send(test_client, upload_id, "A514_R2.fq.gz", data, pieces, 1).status_code == 200

    status = test_client.get("/upload/chunk/status", params={"session_id": SESSION, "upload_id": upload_id}).json()
    assert status["received"] == list(range(len(pieces))) and status["complete"] is False

    done = test_client.post("/upload/chunk/complete", data=_form(upload_id, "A514_R2.fq.gz", data, len(pieces)))
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["category"] == "bioinformatics" and body["original_name"] == "A514_R2.fq.gz"
    stored = session_root / "uploads" / body["file_name"]
    assert stored.read_bytes() == data

    # A repeated complete (first response lost) returns the same file, not a second copy.
    again = test_client.post("/upload/chunk/complete", data=_form(upload_id, "A514_R2.fq.gz", data, len(pieces)))
    assert again.status_code == 200 and again.json()["file_id"] == body["file_id"]
    listed = test_client.get("/upload/list", params={"session_id": SESSION}).json()
    assert [f["file_name"] for f in listed["files"]] == [body["file_name"]]
    assert not list((session_root / upload_routes.CHUNK_SUBDIR / upload_id).glob("*.part"))


def test_complete_refuses_missing_pieces(client) -> None:
    test_client, _ = client
    data = os.urandom(200_000)
    pieces = _pieces(data, 64 * 1024)
    for index in (0, 2, 3):
        assert _send(test_client, "upl_missing1", "reads.fastq", data, pieces, index).status_code == 200
    done = test_client.post("/upload/chunk/complete", data=_form("upl_missing1", "reads.fastq", data, len(pieces)))
    assert done.status_code == 409
    assert "first missing index 1" in done.json()["detail"]


def test_chunk_rejects_mismatched_metadata_unknown_types_and_bad_ids(client) -> None:
    test_client, _ = client
    data = os.urandom(10_000)
    pieces = _pieces(data, 4_000)
    assert _send(test_client, "upl_meta0001", "reads.fastq", data, pieces, 0).status_code == 200
    other = test_client.post(
        "/upload/chunk",
        data=_form("upl_meta0001", "other.fastq", data, len(pieces), index="1"),
        files={"chunk": ("blob", pieces[1], "application/octet-stream")},
    )
    assert other.status_code == 409
    assert _send(test_client, "upl_meta0002", "payload.exe", data, pieces, 0).status_code == 400
    assert _send(test_client, "../escape", "reads.fastq", data, pieces, 0).status_code == 400
    out_of_range = test_client.post(
        "/upload/chunk",
        data=_form("upl_meta0003", "reads.fastq", data, len(pieces), index=str(len(pieces))),
        files={"chunk": ("blob", pieces[0], "application/octet-stream")},
    )
    assert out_of_range.status_code == 400


def test_chunk_respects_upload_size_limit_and_session_owner(client, monkeypatch) -> None:
    test_client, _ = client
    monkeypatch.setattr(upload_routes, "_get_max_size", lambda _category: 1000)
    data = os.urandom(5_000)
    pieces = _pieces(data, 2_500)
    assert _send(test_client, "upl_large001", "reads.fastq", data, pieces, 0).status_code == 413
    monkeypatch.undo()
    assert _send(test_client, "upl_owner001", "reads.fastq", data, pieces, 0, headers={"x-user": "bob"}).status_code == 403


def test_chunked_zip_is_extracted_like_a_single_upload(client) -> None:
    test_client, _ = client
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("a.fasta", ">a\nACGT\n")
    data = buffer.getvalue()
    pieces = _pieces(data, 50)
    for index in range(len(pieces)):
        assert _send(test_client, "upl_zip00001", "genomes.zip", data, pieces, index).status_code == 200
    done = test_client.post("/upload/chunk/complete", data=_form("upl_zip00001", "genomes.zip", data, len(pieces)))
    assert done.status_code == 200, done.text
    assert done.json()["is_archive"] is True and done.json()["extracted_files"] == 1


def test_stale_chunk_dirs_are_purged_when_a_new_upload_starts(client) -> None:
    test_client, session_root = client
    stale = session_root / upload_routes.CHUNK_SUBDIR / "upl_stale001"
    stale.mkdir(parents=True)
    (stale / "0.part").write_bytes(b"x")
    old = time.time() - upload_routes.CHUNK_STALE_SECONDS - 60
    os.utime(stale, (old, old))
    data = os.urandom(100)
    assert _send(test_client, "upl_fresh001", "reads.fastq", data, [data], 0).status_code == 200
    assert not stale.exists()
