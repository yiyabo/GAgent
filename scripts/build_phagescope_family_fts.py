#!/usr/bin/env python3
"""Build an immutable GAgent-owned FTS5 index from read-only PhageScope families.

Example (host Python with psycopg2 and SQLite FTS5):
  python3 scripts/build_phagescope_family_fts.py \
    --service-env /data3/platform/gagent/phagescope-service/service.env \
    --out-root /data3/platform/gagent/phagescope-family-search --version v1

No source table, website, index, or permission is changed. Failed staging dirs
remain under --out-root for inspection; current is only switched after checks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

SOURCE_TABLE = "phage_protein_family"
FORMAT_VERSION = 1
DEFAULT_SYNONYMS = Path(__file__).resolve().parent.parent / "deploy/phagescope_data_service/family_synonyms.json"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_service_env(path: Path) -> dict[str, str]:
    result = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            name, value = line.split("=", 1)
            result[name.strip()] = value.strip()
    if not all(result.get(key) for key in ("PGHOST", "PGPORT", "PGDATABASE", "PGUSER_RO", "PGPASSWORD_RO")):
        raise ValueError("service.env lacks a required read-only PostgreSQL connection setting")
    if result["PGUSER_RO"] != "phage_ro":
        raise ValueError("Family index build requires the phage_ro source role")
    return result


def read_synonyms(path: Path) -> dict[str, Any]:
    """Check the versioned, human-reviewed alias list before publishing it."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Could not read the family synonym file") from exc
    if not isinstance(data, dict) or data.get("format_version") != 1 or not isinstance(data.get("groups"), list):
        raise ValueError("Expected a version-1 family synonym group list")
    seen: set[str] = set()
    if not data["groups"] or len(data["groups"]) > 100:
        raise ValueError("Family synonym groups must contain 1..100 entries")
    for group in data["groups"]:
        if not isinstance(group, dict) or not isinstance(group.get("aliases"), list):
            raise ValueError("Each synonym group needs a canonical string and aliases")
        terms = [group.get("canonical"), *group["aliases"]]
        if not 2 <= len(terms) <= 12:
            raise ValueError("Each synonym group needs 1..11 aliases")
        for term in terms:
            if not isinstance(term, str) or not term.strip() or len(term) > 120:
                raise ValueError("Invalid family synonym term")
            normalized = " ".join(term.casefold().split())
            if normalized in seen or any(ord(char) < 32 for char in term):
                raise ValueError("Duplicate or control-character family synonym term")
            seen.add(normalized)
    return data


def connect_source(env: dict[str, str]):
    import psycopg2  # host-only build dependency; app imports need none

    return psycopg2.connect(
        host=env["PGHOST"],
        port=env["PGPORT"],
        dbname=env["PGDATABASE"],
        user=env["PGUSER_RO"],
        password=env["PGPASSWORD_RO"],
        options="-c default_transaction_read_only=on -c statement_timeout=300000",
    )


def _clean_row(row: tuple[Any, ...], rowid: int) -> tuple[Any, ...]:
    if len(row) != 9 or not row[0]:
        raise ValueError("Source family row has a missing ID or unexpected schema")
    product, classification = str(row[4] or ""), str(row[3] or "")
    if not product.strip() and not classification.strip():
        raise ValueError("The annotated-family export returned a blank row")
    singleton = None if row[8] is None else (1 if row[8] else 0)
    return (
        rowid,
        str(row[0]),
        row[1],
        row[2],
        classification,
        product,
        row[5],
        row[6],
        row[7],
        singleton,
    )


def create_index(db_path: Path, batches: Iterable[list[tuple[Any, ...]]], expected_indexed: int) -> dict[str, int]:
    """Stream catalog rows into a disposable SQLite file; never load all families in RAM."""
    conn = sqlite3.connect(str(db_path))
    indexed = non_singleton = 0
    try:
        conn.execute("PRAGMA page_size=8192")
        conn.execute("PRAGMA journal_mode=OFF")  # only an unpublished disposable staging file
        conn.execute("PRAGMA synchronous=OFF")
        conn.execute("PRAGMA cache_size=-131072")
        conn.execute("""CREATE TABLE families (
  family_id TEXT NOT NULL UNIQUE,
  member_count INTEGER,
  db_member_count INTEGER,
  dominant_class TEXT NOT NULL,
  consensus_product TEXT NOT NULL,
  subfamily_count INTEGER,
  representative_protein_id TEXT,
  dataset_count INTEGER,
  is_singleton INTEGER
)""")
        conn.execute("""CREATE VIRTUAL TABLE family_fts USING fts5(
  consensus_product, dominant_class,
  content='families', content_rowid='rowid',
  tokenize='unicode61 remove_diacritics 2'
)""")
        for batch in batches:
            if not batch:
                continue
            rows = [_clean_row(row, indexed + i + 1) for i, row in enumerate(batch)]
            conn.executemany(
                """INSERT INTO families
(rowid, family_id, member_count, db_member_count, dominant_class,
 consensus_product, subfamily_count, representative_protein_id, dataset_count, is_singleton)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
            conn.executemany(
                "INSERT INTO family_fts(rowid, consensus_product, dominant_class) VALUES (?, ?, ?)",
                ((row[0], row[5], row[4]) for row in rows),
            )
            conn.commit()
            indexed += len(rows)
            non_singleton += sum(row[9] == 0 for row in rows)
            if indexed % 250000 < len(rows):
                print(f"      indexed {indexed:,} families", flush=True)
        if indexed != expected_indexed:
            raise ValueError(f"Source/indexed row mismatch: {indexed} != {expected_indexed}")
        print("[3/5] optimizing and checking FTS5…", flush=True)
        conn.execute("INSERT INTO family_fts(family_fts) VALUES('optimize')")
        conn.execute("INSERT INTO family_fts(family_fts, rank) VALUES('integrity-check', 1)")
        conn.commit()
        count = conn.execute("SELECT COUNT(*) FROM families").fetchone()[0]
        if count != indexed or conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("SQLite family index failed its integrity or count check")
        return {"indexed_rows": indexed, "indexed_non_singleton_rows": non_singleton}
    finally:
        conn.close()


def validate_index(db_path: Path, expected_indexed: int) -> None:
    """Reopen the final file read-only to catch a partial or unusable build."""
    conn = sqlite3.connect(db_path.as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        if conn.execute("SELECT COUNT(*) FROM families").fetchone()[0] != expected_indexed:
            raise ValueError("Published family count differs from source snapshot")
        conn.execute(
            "SELECT rowid FROM family_fts WHERE family_fts MATCH ? LIMIT 1",
            ('"endolysin"',),
        ).fetchone()
    finally:
        conn.close()


def publish(staging: Path, version: Path, current: Path) -> None:
    if version.exists() or version.is_symlink():
        raise FileExistsError(f"Family index version already exists: {version}")
    if current.exists() and not current.is_symlink():
        raise ValueError(f"Refusing to replace a non-symlink current: {current}")
    try:
        os.rename(staging, version)
    except OSError as exc:
        raise RuntimeError(f"Could not publish family index at {version}") from exc
    candidate = current.with_name(f"{current.name}.{os.getpid()}.tmp")
    try:
        candidate.symlink_to(version.name)
        os.replace(str(candidate), str(current))
    finally:
        if candidate.is_symlink():
            candidate.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service-env", type=Path, required=True)
    parser.add_argument("--out-root", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--synonyms", type=Path, default=DEFAULT_SYNONYMS)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", args.version) or args.version in ("current", ".", ".."):
        parser.error("--version must be a new simple directory name")
    if not args.out_root.is_dir():
        parser.error("--out-root must be an existing GAgent-owned directory")
    final = args.out_root / args.version
    if final.exists() or final.is_symlink():
        raise FileExistsError(f"Family index version already exists: {final}")
    read_synonyms(args.synonyms)
    env = load_service_env(args.service_env)
    staging = Path(tempfile.mkdtemp(prefix=f".{args.version}-", dir=str(args.out_root)))
    start = time.perf_counter()
    print(
        "[1/5] counting family rows in a read-only repeatable-read snapshot…",
        flush=True,
    )
    pg_conn = connect_source(env)
    try:
        pg_conn.set_session(readonly=True, isolation_level="REPEATABLE READ")
        with pg_conn.cursor() as cur:
            cur.execute("""SELECT COUNT(*),
       COUNT(*) FILTER (WHERE (NULLIF(BTRIM(COALESCE(consensus_product, '')), '') IS NOT NULL
                        OR NULLIF(BTRIM(COALESCE(dominant_class, '')), '') IS NOT NULL)),
       COUNT(*) FILTER (WHERE NOT is_singleton)
FROM phage_protein_family""")
            counts = cur.fetchone()
            if counts is None or len(counts) != 3 or any(value is None for value in counts):
                raise ValueError("Family source count query returned an invalid result")
            source_rows, expected_indexed, source_non_singleton = counts
        print(f"      total {source_rows:,}, searchable {expected_indexed:,}", flush=True)
        print("[2/5] streaming annotated families into private SQLite FTS5…", flush=True)
        with pg_conn.cursor(name="family_fts_export") as cursor:
            cursor.itersize = 10000
            cursor.execute("""SELECT family_id, member_count, db_member_count, dominant_class,
       consensus_product, subfamily_count, representative_protein_id,
       dataset_count, is_singleton
FROM phage_protein_family
WHERE (NULLIF(BTRIM(COALESCE(consensus_product, '')), '') IS NOT NULL
    OR NULLIF(BTRIM(COALESCE(dominant_class, '')), '') IS NOT NULL)""")

            def batches():
                while True:
                    batch = cursor.fetchmany(10000)
                    if not batch:
                        break
                    yield batch

            stats = create_index(staging / "families.sqlite", batches(), expected_indexed)
    finally:
        pg_conn.close()
    print("[4/5] verifying read-only reopen, manifest hashes and publication…", flush=True)
    index_path = staging / "families.sqlite"
    validate_index(index_path, expected_indexed)
    synonym_path = staging / "family_synonyms.json"
    shutil.copyfile(str(args.synonyms), str(synonym_path))
    manifest = {
        "format_version": FORMAT_VERSION,
        "version": args.version,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_table": SOURCE_TABLE,
        "source_role": "phage_ro",
        "source_isolation": "repeatable_read",
        "source_rows": source_rows,
        "source_non_singleton_rows": source_non_singleton,
        "indexed_rows": stats["indexed_rows"],
        "indexed_non_singleton_rows": stats["indexed_non_singleton_rows"],
        "index_scope": "families with a nonblank consensus_product or dominant_class",
        "sqlite_version": sqlite3.sqlite_version,
        "files": {
            path.name: {"bytes": path.stat().st_size, "sha256": sha256_of(path)} for path in (index_path, synonym_path)
        },
        "build_seconds": round(time.perf_counter() - start, 1),
    }
    (staging / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"[5/5] atomically publishing {args.version}…", flush=True)
    publish(staging, final, args.out_root / "current")
    print(
        f"DONE in {manifest['build_seconds']}s: {expected_indexed:,} indexed of "
        f"{source_rows:,} source rows; {(final / 'families.sqlite').stat().st_size} bytes",
        flush=True,
    )


if __name__ == "__main__":
    main()
