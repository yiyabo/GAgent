"""Archive upload hardening: link members rejected, expansion budget enforced.

``_safe_extract_zip`` used to accept symlink members (a later member can then
write *through* the link) and neither extractor had a size/entry budget
(P5, 2026-10-11). The tar path already rejected links; these tests pin both.
"""

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.routers.upload_routes import _safe_extract_tar, _safe_extract_zip
from app.services.foundation.settings import get_settings


@pytest.fixture(autouse=True)
def _fresh_settings_cache():
    # get_settings is lru_cached; without this, the first test to touch it
    # freezes the UPLOAD_* values for the whole process (2026-10-11).
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _zip_path(tmp_path: Path) -> Path:
    return tmp_path / "archive.zip"


def _write_zip(path: Path, entries) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries:
            zf.writestr(name, data)


def test_zip_symlink_member_is_rejected(tmp_path: Path) -> None:
    path = _zip_path(tmp_path)
    with zipfile.ZipFile(path, "w") as zf:
        info = zipfile.ZipInfo("link")
        info.external_attr = 0o120777 << 16
        zf.writestr(info, "target")

    dest = tmp_path / "out"
    with zipfile.ZipFile(path) as zf, pytest.raises(HTTPException) as exc:
        _safe_extract_zip(zf, dest)

    assert exc.value.status_code == 400
    assert "links are not allowed" in exc.value.detail


def test_zip_entry_count_cap_is_enforced(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("UPLOAD_EXTRACT_MAX_ENTRIES", "2")
    path = _zip_path(tmp_path)
    _write_zip(path, [(f"f{i}.txt", "x") for i in range(3)])

    dest = tmp_path / "out"
    with zipfile.ZipFile(path) as zf, pytest.raises(HTTPException) as exc:
        _safe_extract_zip(zf, dest)

    assert exc.value.status_code == 413
    assert not dest.exists() or not any(dest.rglob("*"))


def test_zip_expansion_bytes_cap_is_enforced(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("UPLOAD_EXTRACT_MAX_BYTES", "150")
    path = _zip_path(tmp_path)
    _write_zip(path, [("a.bin", "x" * 100), ("b.bin", "x" * 100)])

    dest = tmp_path / "out"
    with zipfile.ZipFile(path) as zf, pytest.raises(HTTPException) as exc:
        _safe_extract_zip(zf, dest)

    assert exc.value.status_code == 413


def test_zip_within_caps_extracts_normally(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("UPLOAD_EXTRACT_MAX_ENTRIES", "10")
    monkeypatch.setenv("UPLOAD_EXTRACT_MAX_BYTES", "1024")
    path = _zip_path(tmp_path)
    _write_zip(path, [("a.txt", "hello"), ("b.txt", "world")])

    dest = tmp_path / "out"
    with zipfile.ZipFile(path) as zf:
        count = _safe_extract_zip(zf, dest)

    assert count == 2
    assert (dest / "a.txt").read_text(encoding="utf-8") == "hello"


def test_tar_symlink_member_is_rejected(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tf:
        info = tarfile.TarInfo("link")
        info.type = tarfile.SYMTYPE
        info.linkname = "target"
        tf.addfile(info)
    buffer.seek(0)

    dest = tmp_path / "out"
    with tarfile.open(fileobj=buffer) as tf, pytest.raises(HTTPException) as exc:
        _safe_extract_tar(tf, dest)

    assert exc.value.status_code == 400
