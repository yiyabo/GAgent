"""Read-only, bounded freshness check for the GAgent protein-family FTS5 snapshot.

The daily timer invokes this module separately from the HTTP service. A matching
count plus a fixed sample is evidence of no *observed* drift, not proof that
all 2.8 million indexed annotations remain identical to PostgreSQL.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from .family_fts_engine import FamilyFTSEngine
except ImportError:  # host service installs these as loose files
    from family_fts_engine import FamilyFTSEngine

CHECKER_VERSION = 2
STATUS_FILENAME = "family_freshness_status.json"
MAX_REPORT_AGE = timedelta(hours=36)


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def status_path(index_root: Path) -> Path:
    return Path(index_root).parent / STATUS_FILENAME


def _source_tuple(row: tuple[Any, ...]) -> tuple[Any, ...]:
    return (
        row[0],
        row[1],
        row[2],
        row[3] or "",
        row[4] or "",
        row[5],
        row[6] or None,
        row[7],
        None if row[8] is None else (1 if row[8] else 0),
    )


def _sample_rowids(indexed_rows: int, sample_size: int, *, seed: str) -> list[int]:
    """Pick one row per stratum; the UTC day rotates the inspected families."""
    if indexed_rows <= 0 or not 1 <= sample_size <= 512:
        raise ValueError("sample_size must be 1..512 and the snapshot must have rows")
    size = min(indexed_rows, sample_size)
    selected = []
    for i in range(size):
        start = i * indexed_rows // size
        stop = (i + 1) * indexed_rows // size
        digest = hashlib.blake2b(f"{seed}:{i}".encode(), digest_size=8).digest()
        selected.append(start + 1 + int.from_bytes(digest, "big") % (stop - start))
    return selected


def check_freshness(
    pg_conn: Any, engine: FamilyFTSEngine, *, sample_size: int = 256
) -> dict[str, Any]:
    """Compare counts and deterministic, spread-out row IDs in one source snapshot."""
    if not engine.available or engine.index_path is None:
        raise ValueError("a validated family FTS5 snapshot is required")
    manifest = engine.manifest
    seed = _stamp()[:10]
    ids = _sample_rowids(manifest["indexed_rows"], sample_size, seed=seed)
    with closing(engine._connect()) as local:
        samples = []
        for rowid in ids:
            row = local.execute(
                """SELECT family_id, member_count, db_member_count, dominant_class,
consensus_product, subfamily_count, representative_protein_id, dataset_count, is_singleton
FROM families WHERE rowid = ?""",
                (rowid,),
            ).fetchone()
            if row is None:
                raise ValueError("indexed family sample is missing")
            samples.append(_source_tuple(tuple(row)))
    with pg_conn.cursor() as cursor:
        cursor.execute(
            """SELECT COUNT(*), COUNT(*) FILTER (
  WHERE NULLIF(BTRIM(COALESCE(consensus_product, '')), '') IS NOT NULL
     OR NULLIF(BTRIM(COALESCE(dominant_class, '')), '') IS NOT NULL
) FROM phage_protein_family"""
        )
        counts = cursor.fetchone()
        if counts is None or len(counts) != 2 or any(value is None for value in counts):
            raise ValueError("source count query returned an invalid result")
        cursor.execute(
            """SELECT family_id, member_count, db_member_count, dominant_class,
consensus_product, subfamily_count, representative_protein_id, dataset_count, is_singleton
FROM phage_protein_family WHERE family_id = ANY(%s)""",
            ([row[0] for row in samples],),
        )
        source = {row[0]: _source_tuple(row) for row in cursor.fetchall()}
    mismatches = [row[0] for row in samples if source.get(row[0]) != row]
    source_rows, indexable_rows = counts
    drift = (
        source_rows != manifest["source_rows"]
        or indexable_rows != manifest["indexed_rows"]
        or bool(mismatches)
    )
    return {
        "checker_version": CHECKER_VERSION,
        "status": "drift_detected" if drift else "matched_counts_and_samples",
        "checked_at": _stamp(),
        "snapshot_version": manifest["version"],
        "snapshot_built_at": manifest["built_at"],
        "source_rows": source_rows,
        "snapshot_source_rows": manifest["source_rows"],
        "source_indexable_rows": indexable_rows,
        "snapshot_indexed_rows": manifest["indexed_rows"],
        "samples_checked": len(samples),
        "sample_scheme": "daily_stratified_v1",
        "sample_seed": seed,
        "sample_mismatch_count": len(mismatches),
        "sample_mismatch_examples": mismatches[:20],
        "limits": "Matching counts and samples do not prove every annotation is unchanged.",
    }


def write_status(path: Path, report: dict[str, Any]) -> None:
    """Replace a GAgent-owned status file atomically; do not store credentials."""
    if not path.parent.is_dir():
        raise ValueError("status directory does not exist")
    staging = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    if staging.exists() or staging.is_symlink():
        raise FileExistsError("freshness status staging file already exists")
    try:
        with staging.open("x", encoding="utf-8") as stream:
            os.chmod(staging, 0o600)
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staging, path)
    finally:
        if staging.exists():
            staging.unlink()


def read_status(index_root: Path, engine: FamilyFTSEngine) -> dict[str, Any]:
    """Serve the most recent check without running a database scan per request."""
    if not engine.available:
        return {"status": "index_unavailable", "checked_at": None}
    base = {
        "status": "not_checked",
        "checked_at": None,
        "snapshot_version": engine.manifest["version"],
        "snapshot_built_at": engine.manifest["built_at"],
        "limits": "A matching check compares counts and samples, not all annotations.",
    }
    path = status_path(index_root)
    if path.is_symlink():
        return {**base, "status": "invalid_report"}
    if not path.is_file():
        return base
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(report, dict)
            or report.get("checker_version") != CHECKER_VERSION
        ):
            raise ValueError("invalid report format")
        if report.get("snapshot_version") != engine.manifest["version"]:
            return {**base, "status": "version_mismatch"}
        checked_at = datetime.fromisoformat(
            str(report["checked_at"]).replace("Z", "+00:00")
        )
        if checked_at.tzinfo is None or checked_at > datetime.now(timezone.utc):
            raise ValueError("invalid report timestamp")
        if report.get("status") not in (
            "matched_counts_and_samples",
            "drift_detected",
            "check_error",
        ):
            raise ValueError("invalid report status")
        age = datetime.now(timezone.utc) - checked_at
        if age > MAX_REPORT_AGE:
            return {**report, "last_check_status": report["status"], "status": "check_overdue"}
        return report
    except (OSError, ValueError, KeyError, TypeError):
        return {**base, "status": "invalid_report"}


def _load_service_env(path: Path) -> dict[str, str]:
    env = dict(
        line.split("=", 1)
        for line in path.read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.startswith("#")
    )
    if env.get("PGUSER_RO") != "phage_ro" or not all(
        env.get(name) for name in ("PGHOST", "PGPORT", "PGDATABASE", "PGPASSWORD_RO")
    ):
        raise ValueError("a complete phage_ro configuration is required")
    return env


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service-env", type=Path, required=True)
    parser.add_argument("--index-root", type=Path, required=True)
    parser.add_argument("--sample-size", type=int, default=256)
    args = parser.parse_args()
    if not 1 <= args.sample_size <= 512:
        parser.error("sample-size must be 1..512")
    report: dict[str, Any] = {
        "checker_version": CHECKER_VERSION,
        "status": "check_error",
        "checked_at": _stamp(),
        "snapshot_version": None,
        "reason": "source_or_index_unavailable",
    }
    code = 3
    try:
        env = _load_service_env(args.service_env)
        engine = FamilyFTSEngine(args.index_root)
        if not engine.available:
            report["status"] = "index_unavailable"
            code = 2
        else:
            report["snapshot_version"] = engine.manifest["version"]
            import psycopg2  # installed on the host; not needed by app runtime

            pg_conn = psycopg2.connect(
                host=env["PGHOST"],
                port=env["PGPORT"],
                dbname=env["PGDATABASE"],
                user=env["PGUSER_RO"],
                password=env["PGPASSWORD_RO"],
                options="-c default_transaction_read_only=on -c statement_timeout=20000",
            )
            try:
                pg_conn.set_session(readonly=True, isolation_level="REPEATABLE READ")
                report = check_freshness(pg_conn, engine, sample_size=args.sample_size)
            finally:
                pg_conn.close()
            code = 2 if report["status"] == "drift_detected" else 0
    except Exception as exc:  # fail closed and persist a non-green checker status
        report["reason"] = type(exc).__name__
    write_status(status_path(args.index_root), report)
    print(
        json.dumps(
            {
                k: report.get(k)
                for k in (
                    "status",
                    "checked_at",
                    "snapshot_version",
                    "source_rows",
                    "source_indexable_rows",
                    "samples_checked",
                    "sample_mismatch_count",
                    "reason",
                )
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return code


if __name__ == "__main__":
    sys.exit(main())
