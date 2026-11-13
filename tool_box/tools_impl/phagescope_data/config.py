"""Configuration and path-containment helpers for the phagescope_data tool."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from app.services.foundation.settings import get_settings

ENV_ENABLED = "PHAGESCOPE_DATA_ENABLED"
ENV_SERVICE_URL = "PHAGESCOPE_DATA_SERVICE_URL"
ENV_SERVICE_KEY = "PHAGESCOPE_DATA_SERVICE_KEY"
ENV_SEQ_DIR = "PHAGESCOPE_SEQ_DIR"
ENV_CSV_DIR = "PHAGESCOPE_CSV_DOWNLOAD_DIR"
ENV_PDB_DIR = "PHAGESCOPE_PDB_DIR"

DEFAULT_SEQ_DIR = "/phagescope/phage_sequence"
DEFAULT_CSV_DIR = "/phagescope/csv_data/Download"
DEFAULT_PDB_DIR = "/phagescope/esmfold_pdb"


class PhageScopeDataError(Exception):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def phagescope_data_enabled() -> bool:
    return os.environ.get(ENV_ENABLED, "").strip() == "1"


def service_endpoint() -> tuple[str, str]:
    settings = get_settings()
    url = (
        os.environ.get(ENV_SERVICE_URL, "").strip()
        or str(getattr(settings, "phagescope_gateway_data_url", "") or "").strip()
        or str(getattr(settings, "phagescope_gateway_base_url", "") or "").strip()
    ).rstrip("/")
    key = (
        os.environ.get(ENV_SERVICE_KEY, "").strip()
        or str(getattr(settings, "phagescope_gateway_data_key", "") or "").strip()
    )
    suffix = "/agent-api/v1/data"
    if url and not url.endswith(suffix):
        url += suffix
    return url, key


def seq_root() -> Path | None:
    raw = os.environ.get(ENV_SEQ_DIR, DEFAULT_SEQ_DIR).strip()
    if not raw:
        return None
    p = Path(raw)
    return p if (p / "phage_fasta").is_dir() else None


def csv_download_root() -> Path | None:
    raw = os.environ.get(ENV_CSV_DIR, DEFAULT_CSV_DIR).strip()
    if not raw:
        return None
    p = Path(raw)
    return p if (p / "Phage_meta_data").is_dir() else None


def pdb_root() -> Path | None:
    raw = os.environ.get(ENV_PDB_DIR, DEFAULT_PDB_DIR).strip()
    if not raw:
        return None
    p = Path(raw)
    return p if p.is_dir() else None


def local_mounts_status() -> dict[str, Any]:
    sr = seq_root()
    cr = csv_download_root()
    pr = pdb_root()
    return {
        "sequence_mount_ready": sr is not None,
        "sequence_mount_path": str(sr) if sr is not None else None,
        "csv_download_mount_ready": cr is not None,
        "csv_download_mount_path": str(cr) if cr is not None else None,
        "pdb_mount_ready": pr is not None,
        "pdb_mount_path": str(pr) if pr is not None else None,
    }


def safe_component(value: Any) -> str:
    text = "" if value is None else str(value).strip()
    if not text or "/" in text or "\\" in text or text in {".", ".."} or ".." in text:
        raise PhageScopeDataError("path_rejected", f"Unsafe path component: {text!r}")
    return text


def safe_filename_stem(value: Any) -> str:
    raw = str(value or "item").strip()
    cleaned = "".join(ch if (ch.isalnum() or ch in "._-") else "_" for ch in raw).strip("._")
    return (cleaned or "item")[:160]


def contained(path: Path, root: Path) -> Path:
    root_real = os.path.realpath(str(root))
    path_real = os.path.realpath(str(path))
    if path_real != root_real and not path_real.startswith(root_real + os.sep):
        raise PhageScopeDataError("path_rejected", "Resolved path escaped allowed root.")
    return Path(path_real)
