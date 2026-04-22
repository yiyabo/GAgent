"""PhageScope bulk dataset download with resume support.

Downloads public datasets from the PhageScope download page
(https://phagescope.deepomics.org/download) with:
- Concurrent streaming downloads
- HTTP Range-based resume for interrupted transfers
- SOCKS5 / HTTP proxy support (same env vars as literature_pipeline)
- Session-scoped output directory integration
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_API_BASE = "https://phageapi.deepomics.org"

# ---------------------------------------------------------------------------
# Dataset catalog — mirrors https://phagescope.deepomics.org/download
# ---------------------------------------------------------------------------

_DATASOURCES: List[str] = [
    "refseq", "genbank", "embl", "ddbj", "phagesdb",
    "gvd", "gpd", "mgv", "temphd", "chvd", "igvd", "img_vr", "gov2", "stv",
]

# Datasources that do NOT have certain data types on the download page.
_DATASOURCE_EXCLUSIONS: Dict[str, set] = {
    "embl": {"antimicrobial_resistance_gene"},
    "ddbj": {"antimicrobial_resistance_gene"},
    "phagesdb": {"antimicrobial_resistance_gene", "virulent_factor"},
    "temphd": {"virulent_factor"},
    "chvd": {"virulent_factor"},
    "igvd": {"virulent_factor"},
    "img_vr": {"virulent_factor"},
    "gov2": {"virulent_factor"},
    "stv": {"virulent_factor"},
}


def _resolve_api_base(base_url: Optional[str] = None) -> str:
    """Resolve the API base URL from explicit param, env var, or default."""
    text = str(base_url or "").strip().rstrip("/")
    if text:
        return text
    env_val = str(os.getenv("PHAGESCOPE_BASE_URL") or "").strip().rstrip("/")
    if env_val:
        return env_val
    return _DEFAULT_API_BASE


def _ds_file_key(datasource: str) -> str:
    """Map datasource name to the filename token used in URLs."""
    return {
        "refseq": "refseq",
        "genbank": "genbank",
        "embl": "embl",
        "ddbj": "ddbj",
        "phagesdb": "phagesdb",
        "gvd": "gvd",
        "gpd": "gpd",
        "mgv": "mgv",
        "temphd": "temphd",
        "chvd": "chvd",
        "igvd": "igvd",
        "img_vr": "img_vr",
        "gov2": "gov2",
        "stv": "stv",
    }.get(datasource.lower(), datasource.lower())


def _ds_display(datasource: str) -> str:
    """Map datasource to the display-case token used in some URL paths."""
    return {
        "refseq": "RefSeq",
        "genbank": "Genbank",
        "embl": "EMBL",
        "ddbj": "DDBJ",
        "phagesdb": "PhagesDB",
        "gvd": "GVD",
        "gpd": "GPD",
        "mgv": "MGV",
        "temphd": "TemPhD",
        "chvd": "CHVD",
        "igvd": "IGVD",
        "img_vr": "IMG_VR",
        "gov2": "GOV2",
        "stv": "STV",
    }.get(datasource.lower(), datasource)


# Each data type maps to (url_template, filename_template, subdir).
# {base} = API base URL, {ds} = _ds_file_key, {DS} = _ds_display
_DATA_TYPES: Dict[str, Dict[str, str]] = {
    "phage_meta_data": {
        "url": "{base}/files/Download/Phage_meta_data/{ds}_phage_meta_data.tsv",
        "filename": "{ds}_phage_meta_data.tsv",
        "subdir": "meta_data",
    },
    "annotated_protein": {
        "url": "{base}/files/Download/Annotated_protein_meta_data_v2/{ds}_annotated_protein_meta_data.tsv",
        "filename": "{ds}_annotated_protein_meta_data.tsv",
        "subdir": "annotated_protein",
    },
    "transcription_terminator": {
        "url": "{base}/files/Download/Transcription_terminators_meta_data/{ds}_transcription_terminator_meta_data.tsv",
        "filename": "{ds}_transcription_terminator_meta_data.tsv",
        "subdir": "transcription_terminator",
    },
    "trna_tmrna": {
        "url": "{base}/files/Download/tRNA_tmRNA_gene_meta_data_v2/{ds}_trna_gene_meta_data.tsv",
        "filename": "{ds}_trna_gene_meta_data.tsv",
        "subdir": "trna_tmrna",
    },
    "anticrispr_protein": {
        "url": "{base}/files/Download/AntiCRISPR_protein_meta_data_v2/{ds}_anticrispr_protein_meta_data.tsv",
        "filename": "{ds}_anticrispr_protein_meta_data.tsv",
        "subdir": "anticrispr_protein",
    },
    "crispr_array": {
        "url": "{base}/files/Download/CRISPR_array_meta_data/{ds}_crispr_array_meta_data.tsv",
        "filename": "{ds}_crispr_array_meta_data.tsv",
        "subdir": "crispr_array",
    },
    "antimicrobial_resistance_gene": {
        "url": "{base}/files/Download/Antimicrobial_resistance_gene_meta_data_v2/{DS}_antimicrobial_resistance_gene_data.tsv",
        "filename": "{DS}_antimicrobial_resistance_gene_data.tsv",
        "subdir": "antimicrobial_resistance_gene",
    },
    "virulent_factor": {
        "url": "{base}/files/Download/Virulent_factor_meta_data_v2/{DS}_virulent_factor_data.tsv",
        "filename": "{DS}_virulent_factor_data.tsv",
        "subdir": "virulent_factor",
    },
    "transmembrane_protein": {
        "url": "{base}/files/Download/Transmembrane_protein_meta_data/{ds}_transmembrane_protein_meta_data.tsv",
        "filename": "{ds}_transmembrane_protein_meta_data.tsv",
        "subdir": "transmembrane_protein",
    },
    "phage_fasta": {
        "url": "{base}/fasta/phage_sequence/phage_fasta/{DS}.fasta",
        "filename": "{DS}_phage.fasta",
        "subdir": "phage_fasta",
    },
    "protein_fasta": {
        "url": "{base}/fasta/phage_sequence/proteins/{DS}.tar.gz",
        "filename": "{DS}_proteins.tar.gz",
        "subdir": "protein_fasta",
    },
    "gff3": {
        "url": "{base}/fasta/phage_sequence/phage_gff3/{DS}.gff3",
        "filename": "{DS}.gff3",
        "subdir": "gff3",
    },
}

_ALL_DATA_TYPES = sorted(_DATA_TYPES.keys())

# ---------------------------------------------------------------------------
# Proxy resolution (same pattern as literature_pipeline)
# ---------------------------------------------------------------------------


def _resolve_proxy(explicit: Optional[str]) -> Optional[str]:
    text = str(explicit or "").strip()
    if text:
        return text
    for env_name in (
        "PHAGESCOPE_PROXY",
        "LITERATURE_PIPELINE_PROXY",
        "LITERATURE_PROXY",
    ):
        val = str(os.getenv(env_name) or "").strip()
        if val:
            return val
    return None


def _redact_proxy(proxy: str) -> str:
    text = str(proxy or "").strip()
    if not text:
        return ""
    return re.sub(r"://([^:@/]+):([^@/]+)@", r"://\\1:***@", text)


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------

_DEFAULT_CONCURRENCY = 3
_DEFAULT_TIMEOUT = 43200.0  # 12 hours
_CHUNK_SIZE = 256 * 1024  # 256 KB
_MAX_RETRIES = 3
_RETRY_BACKOFF = 2.0


async def _download_one(
    client: httpx.AsyncClient,
    url: str,
    dest: Path,
    *,
    semaphore: asyncio.Semaphore,
    timeout: float,
) -> Dict[str, Any]:
    """Download a single file with HTTP Range resume support.

    If *dest* already exists with some bytes, sends a Range header to
    resume from where it left off.  The server may ignore the Range
    header (no 206), in which case we overwrite from scratch.
    """
    async with semaphore:
        existing_bytes = 0
        part_path = dest.with_suffix(dest.suffix + ".part")

        # If a previous .part file exists, resume from it.
        if part_path.exists():
            existing_bytes = part_path.stat().st_size
        # If the final file already exists, treat as done.
        elif dest.exists() and dest.stat().st_size > 0:
            return {
                "url": url,
                "file": str(dest),
                "status": "skipped",
                "bytes": dest.stat().st_size,
                "resumed": False,
            }

        headers: Dict[str, str] = {}
        if existing_bytes > 0:
            headers["Range"] = f"bytes={existing_bytes}-"

        attempt = 0
        last_error: Optional[str] = None

        while attempt < _MAX_RETRIES:
            attempt += 1
            try:
                async with client.stream(
                    "GET",
                    url,
                    headers=headers,
                    timeout=timeout,
                    follow_redirects=True,
                ) as response:
                    if response.status_code == 416:
                        # Range not satisfiable — file is already complete
                        if part_path.exists():
                            part_path.rename(dest)
                        return {
                            "url": url,
                            "file": str(dest),
                            "status": "ok",
                            "bytes": existing_bytes,
                            "resumed": True,
                        }

                    if response.status_code >= 400:
                        last_error = f"HTTP {response.status_code}"
                        if attempt < _MAX_RETRIES:
                            await asyncio.sleep(_RETRY_BACKOFF * attempt)
                            continue
                        break

                    resumed = response.status_code == 206
                    mode = "ab" if resumed else "wb"
                    if not resumed:
                        existing_bytes = 0

                    dest.parent.mkdir(parents=True, exist_ok=True)
                    byte_count = existing_bytes
                    with part_path.open(mode) as fh:
                        async for chunk in response.aiter_bytes(chunk_size=_CHUNK_SIZE):
                            fh.write(chunk)
                            byte_count += len(chunk)

                    # Rename .part → final
                    part_path.rename(dest)
                    return {
                        "url": url,
                        "file": str(dest),
                        "status": "ok",
                        "bytes": byte_count,
                        "resumed": resumed and existing_bytes > 0,
                    }

            except (httpx.TimeoutException, httpx.NetworkError, OSError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                # Update existing_bytes for next resume attempt
                if part_path.exists():
                    existing_bytes = part_path.stat().st_size
                    headers["Range"] = f"bytes={existing_bytes}-"
                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(_RETRY_BACKOFF * attempt)
                    continue

        return {
            "url": url,
            "file": str(dest),
            "status": "failed",
            "error": last_error or "unknown",
            "bytes": existing_bytes,
        }


# ---------------------------------------------------------------------------
# Catalog builder
# ---------------------------------------------------------------------------


def _build_download_plan(
    datasources: Optional[Sequence[str]],
    data_types: Optional[Sequence[str]],
    base_url: Optional[str] = None,
) -> List[Dict[str, str]]:
    """Build a list of {url, filename, subdir} dicts for the requested scope."""
    api_base = _resolve_api_base(base_url)
    ds_list = [s.strip().lower() for s in (datasources or _DATASOURCES) if s.strip()]
    dt_list = [t.strip().lower() for t in (data_types or _ALL_DATA_TYPES) if t.strip()]

    # Validate
    valid_ds = {s.lower() for s in _DATASOURCES}
    valid_dt = set(_ALL_DATA_TYPES)
    for ds in ds_list:
        if ds not in valid_ds:
            raise ValueError(f"Unknown datasource: {ds}. Valid: {sorted(valid_ds)}")
    for dt in dt_list:
        if dt not in valid_dt:
            raise ValueError(f"Unknown data_type: {dt}. Valid: {sorted(valid_dt)}")

    plan: List[Dict[str, str]] = []
    for ds in ds_list:
        exclusions = _DATASOURCE_EXCLUSIONS.get(ds, set())
        for dt in dt_list:
            if dt in exclusions:
                continue
            spec = _DATA_TYPES[dt]
            ds_key = _ds_file_key(ds)
            ds_disp = _ds_display(ds)
            plan.append({
                "url": spec["url"].format(base=api_base, ds=ds_key, DS=ds_disp),
                "filename": spec["filename"].format(ds=ds_key, DS=ds_disp),
                "subdir": spec["subdir"],
                "datasource": ds,
                "data_type": dt,
            })
    return plan


# ---------------------------------------------------------------------------
# Output directory resolution
# ---------------------------------------------------------------------------


def _resolve_output_dir(
    session_id: Optional[str],
    save_path: Optional[str],
    task_id: Optional[int] = None,
    ancestor_chain: Optional[List[int]] = None,
) -> Path:
    if save_path:
        p = Path(save_path).expanduser().resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    # Prefer task-scoped directory when running inside a plan step.
    token = str(session_id or "").strip()
    if token and task_id is not None:
        try:
            from app.services.path_router import get_path_router
            router = get_path_router()
            target = router.get_task_output_dir(
                token, task_id, ancestor_chain, create=True,
            )
            target.mkdir(parents=True, exist_ok=True)
            return target
        except Exception:
            pass

    if token:
        try:
            from app.services.session_paths import get_session_tool_outputs_dir
            root = get_session_tool_outputs_dir(token, create=True)
            target = (root / "phagescope_datasets").resolve()
            target.mkdir(parents=True, exist_ok=True)
            return target
        except Exception:
            pass

    target = Path("runtime/phagescope_datasets").resolve()
    target.mkdir(parents=True, exist_ok=True)
    return target


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------


async def phagescope_bulk_download(
    *,
    datasources: Optional[Sequence[str]] = None,
    data_types: Optional[Sequence[str]] = None,
    base_url: Optional[str] = None,
    proxy: Optional[str] = None,
    session_id: Optional[str] = None,
    task_id: Optional[int] = None,
    ancestor_chain: Optional[List[int]] = None,
    save_path: Optional[str] = None,
    concurrency: int = _DEFAULT_CONCURRENCY,
    timeout: float = _DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """Download PhageScope public datasets with resume support.

    Args:
        datasources: List of datasource names (e.g. ["refseq", "genbank"]).
                     None or empty means all 14 datasources.
        data_types:  List of data type names (e.g. ["phage_meta_data", "gff3"]).
                     None or empty means all data types.
        base_url:    API base URL override. Falls back to PHAGESCOPE_BASE_URL
                     env var, then to the public production endpoint.
        proxy:       Explicit proxy URL (http/socks5). Falls back to env vars.
        session_id:  Session id for output path scoping.
        task_id:     Plan task id for task-scoped output isolation.
        ancestor_chain: Ancestor task ids for nested plan steps.
        save_path:   Explicit output directory override.
        concurrency: Max parallel downloads (default 4).
        timeout:     Per-file download timeout in seconds (default 120).

    Returns:
        Structured result dict with per-file status and manifest.
    """
    try:
        plan = _build_download_plan(datasources, data_types, base_url=base_url)
    except ValueError as exc:
        return {"success": False, "tool": "phagescope", "action": "bulk_download", "error": str(exc)}

    if not plan:
        return {
            "success": False,
            "tool": "phagescope",
            "action": "bulk_download",
            "error": "No files to download for the given datasources/data_types combination.",
        }

    output_dir = _resolve_output_dir(session_id, save_path, task_id=task_id, ancestor_chain=ancestor_chain)
    effective_proxy = _resolve_proxy(proxy)

    if effective_proxy:
        logger.info("[BULK_DOWNLOAD] Using proxy %s", _redact_proxy(effective_proxy))
    logger.info("[BULK_DOWNLOAD] %d files planned → %s", len(plan), output_dir)

    semaphore = asyncio.Semaphore(max(1, concurrency))
    started_at = time.monotonic()

    async with httpx.AsyncClient(
        follow_redirects=True,
        proxy=effective_proxy,
        trust_env=False if effective_proxy else True,
    ) as client:
        tasks = []
        for item in plan:
            dest = output_dir / item["subdir"] / item["filename"]
            tasks.append(
                _download_one(client, item["url"], dest, semaphore=semaphore, timeout=timeout)
            )
        results = await asyncio.gather(*tasks, return_exceptions=True)

    elapsed = time.monotonic() - started_at
    file_results: List[Dict[str, Any]] = []
    ok_count = 0
    skip_count = 0
    fail_count = 0
    total_bytes = 0

    for i, res in enumerate(results):
        if isinstance(res, Exception):
            file_results.append({
                "url": plan[i]["url"],
                "file": str(output_dir / plan[i]["subdir"] / plan[i]["filename"]),
                "status": "failed",
                "error": f"{type(res).__name__}: {res}",
                "datasource": plan[i]["datasource"],
                "data_type": plan[i]["data_type"],
            })
            fail_count += 1
        else:
            res["datasource"] = plan[i]["datasource"]
            res["data_type"] = plan[i]["data_type"]
            file_results.append(res)
            if res["status"] == "ok":
                ok_count += 1
                total_bytes += res.get("bytes", 0)
            elif res["status"] == "skipped":
                skip_count += 1
                total_bytes += res.get("bytes", 0)
            else:
                fail_count += 1

    # Write manifest
    manifest = {
        "tool": "phagescope",
        "action": "bulk_download",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "output_dir": str(output_dir),
        "datasources": [p["datasource"] for p in plan],
        "data_types": sorted({p["data_type"] for p in plan}),
        "total_files": len(plan),
        "ok": ok_count,
        "skipped": skip_count,
        "failed": fail_count,
        "total_bytes": total_bytes,
        "elapsed_sec": round(elapsed, 1),
        "proxy_configured": bool(effective_proxy),
        "files": file_results,
    }
    manifest_path = output_dir / "download_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    return {
        "success": fail_count == 0,
        "tool": "phagescope",
        "action": "bulk_download",
        "output_dir": str(output_dir),
        "manifest_path": str(manifest_path),
        "total_files": len(plan),
        "ok": ok_count,
        "skipped": skip_count,
        "failed": fail_count,
        "total_bytes": total_bytes,
        "elapsed_sec": round(elapsed, 1),
        "proxy_configured": bool(effective_proxy),
        "errors": [
            {"datasource": r["datasource"], "data_type": r["data_type"], "error": r.get("error")}
            for r in file_results if r["status"] == "failed"
        ] or None,
        "available_datasources": sorted(_DATASOURCES),
        "available_data_types": _ALL_DATA_TYPES,
    }
