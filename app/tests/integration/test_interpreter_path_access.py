"""Interpreter HTTP routes only read files inside the caller's own sessions."""

from __future__ import annotations

import io
import os
import pickle
import zipfile
from pathlib import Path

import pytest

from app.services.auth import rate_limiter


@pytest.fixture(autouse=True)
def _isolated_auth_rate_limits():
    rate_limiter._buckets.clear()
    yield
    rate_limiter._buckets.clear()


class _ExploitPayload:
    def __init__(self, marker: Path) -> None:
        self.marker = marker

    def __reduce__(self):
        return (exec, (f"open({str(self.marker)!r}, 'w').write('pwned')",))


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, payload in files.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def _register(client, email: str) -> None:
    response = client.post("/auth/register", json={"email": email, "password": "Password123"})
    assert response.status_code == 200, response.text


def _upload_zip(client, session_id: str, files: dict[str, bytes]) -> Path:
    response = client.post(
        "/upload/file",
        data={"session_id": session_id},
        files={"file": ("bundle.zip", _zip_bytes(files), "application/zip")},
    )
    assert response.status_code == 200, response.text
    extracted = response.json()["extracted_path"]
    assert extracted
    return Path(extracted)


@pytest.fixture
def open_signup_client(app_client_factory, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AUTH_MODE", "local")
    monkeypatch.setenv("AUTH_OPEN_SIGNUP", "1")
    with app_client_factory() as client:
        yield client


@pytest.mark.integration
def test_uploaded_pickle_is_rejected_without_running_code(
    open_signup_client, tmp_path: Path
) -> None:
    client = open_signup_client
    marker = tmp_path / "pwned.txt"
    _register(client, "attacker@example.com")
    extracted = _upload_zip(
        client,
        "attacker-session",
        {"evil.pkl": pickle.dumps(_ExploitPayload(marker))},
    )

    response = client.post(
        "/interpreter/metadata", json={"file_path": str(extracted / "evil.pkl")}
    )

    assert response.status_code == 400
    assert "Pickle files" in response.json()["error"]["message"]
    assert not marker.exists()


@pytest.mark.integration
def test_metadata_reads_own_upload_but_not_other_users_or_server_files(
    open_signup_client, isolated_app_env, tmp_path: Path
) -> None:
    client = open_signup_client
    _register(client, "alice@example.com")
    alice_dir = _upload_zip(client, "alice-session", {"data.csv": b"a,b\n1,x\n2,y\n"})
    alice_csv = alice_dir / "data.csv"

    own = client.post("/interpreter/metadata", json={"file_path": str(alice_csv)})
    assert own.status_code == 200, own.text
    assert own.json()["metadata"]["total_rows"] == 2

    server_secret = tmp_path / "server_secret.csv"
    server_secret.write_text("token\nsecret-value\n", encoding="utf-8")
    escape_link = alice_dir / "link.csv"
    os.symlink(server_secret, escape_link)

    for path in (
        server_secret,
        escape_link,
        alice_dir / ".." / ".." / ".." / ".." / server_secret.name,
        isolated_app_env["runtime_root"],
    ):
        response = client.post("/interpreter/metadata", json={"file_path": str(path)})
        assert response.status_code == 403, (path, response.text)
        assert "secret-value" not in response.text

    client.post("/auth/logout")
    _register(client, "bob@example.com")

    cross_user = [
        ("/interpreter/metadata", {"file_path": str(alice_csv)}),
        (
            "/interpreter/generate",
            {"file_paths": [str(alice_csv)], "task_title": "t", "task_description": "d"},
        ),
        (
            "/interpreter/analyze",
            {"file_paths": [str(alice_csv)], "task_title": "t", "task_description": "d"},
        ),
        (
            "/interpreter/plan_analyze",
            {"data_paths": [str(alice_csv)], "task_description": "d"},
        ),
        ("/interpreter/execute", {"code": "print(1)", "data_dir": str(alice_dir)}),
        ("/interpreter/execute", {"code": "print(1)", "work_dir": str(alice_dir)}),
    ]
    for route, payload in cross_user:
        response = client.post(route, json=payload)
        assert response.status_code == 403, (route, response.text)
        assert "secret-value" not in response.text
