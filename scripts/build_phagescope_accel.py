#!/usr/bin/env python3
"""Build the GAgent-private PhageScope columnar acceleration layer (Phase 2).

Everything is read-only against the sources:
- PostgreSQL through the ``phage_ro`` role (phages / phage_lifestyle /
  phage_hosts / datasets and the 7 functional-feature tables) — the SAME
  authority the live SQL endpoints use, so the Parquet layer matches the SQL
  fallback row-for-row by construction;
- the local FASTA/FAA/GFF3 trees (only the *filenames* are read, to build the
  availability bitmask used by ``require_files`` and materialize prechecks).

The production database and the PhageScope website are never modified.

Outputs, under ``<out_root>/<version>/``:
  phages_wide.parquet   one row per catalogued phage with seven annotation counts
                        and dataset-specific file availability bits
  manifest.json         row counts, per-file sha256, source inventory, timing
The feature-level tables are out of scope for this build; the seven counts are
catalog annotations, not a statement of clinical safety. ``current`` changes
only after validation; previously published versions remain immutable.

Run on the 620b host (has pyarrow + psycopg2 + pandas), e.g.:
  python3 scripts/build_phagescope_accel.py \
      --service-env /data3/platform/gagent/phagescope-service/service.env \
      --out-root /data3/platform/gagent/phagescope-accel --version v2
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, cast

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

# Functional feature tracks: name -> (table, phage-key column, key kind).
# "id"  = numeric phages.id (phage_trna)
# "acc" has no dataset key (CRISPR/terminator): counts for repeated
# accessions remain unknown. "dataset_acc" includes the source dataset.
FEATURE_SQL_ID = {
    "n_trna": "SELECT phage_id, COUNT(*) FROM phage_trna GROUP BY phage_id",
}
FEATURE_SQL_ACC = {
    "n_crispr": 'SELECT "Phage_id", COUNT(*) FROM phage_crispr GROUP BY "Phage_id"',
    "n_terminator": 'SELECT "Phage_id", COUNT(*) FROM phage_terminators GROUP BY "Phage_id"',
}
FEATURE_SQL_DATASET_ACC = {
    "n_anticrispr": 'SELECT "Phage_Acession_ID", dataset, COUNT(*) FROM phage_protein_anticrispr GROUP BY "Phage_Acession_ID", dataset',
    "n_virulence": 'SELECT "Phage_Acession_ID", dataset, COUNT(*) FROM phage_virulent_factor GROUP BY "Phage_Acession_ID", dataset',
    "n_amr": 'SELECT "Phage_Acession_ID", dataset, COUNT(*) FROM phage_antimicrobial_resistance_gene GROUP BY "Phage_Acession_ID", dataset',
}
TMHMM_SUFFIXES = (
    "NCBI",
    "PhagesDB",
    "GPD",
    "GVD",
    "MGV",
    "TemPhD",
    "CHVD",
    "IGVD",
    "IMG_VR",
    "GOV2",
    "STV",
    "GSV",
    "UHGV",
    "HPGC",
    "URPC",
    "ELGV",
    "OVD",
    "VMGC",
    "OPD",
    "BAPS",
    "SVD",
    "TYMEFLIES_Viral",
    "MetaVR",
)

AVAIL_FASTA, AVAIL_FAA, AVAIL_GFF3 = 1, 2, 4
COUNT_COLUMNS = ("n_trna", "n_crispr", "n_terminator", "n_anticrispr", "n_virulence", "n_amr", "n_transmembrane")


def load_service_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def connect(env: dict[str, str]):
    # Offline build: read-only, but a generous statement_timeout for the big
    # GROUP BY aggregations (the online service uses 10s; that is a per-request
    # guard, not a property of the phage_ro role).
    import psycopg2

    return psycopg2.connect(
        host=env["PGHOST"],
        port=env["PGPORT"],
        dbname=env["PGDATABASE"],
        user=env["PGUSER_RO"],
        password=env["PGPASSWORD_RO"],
        options="-c default_transaction_read_only=on -c statement_timeout=300000",
    )


def df_from_query(conn, sql, columns: list[str], batch: int = 50000) -> pd.DataFrame:
    """Stream a query through a server-side cursor into a DataFrame.

    ``sql`` is either a closed constant string (the FEATURE_SQL_* tables) or a
    ``psycopg2.sql.Composed`` object whose identifiers are quoted via
    ``psycopg2.sql.Identifier`` — values are never interpolated into SQL text.
    """
    frames: list[pd.DataFrame] = []
    with conn.cursor(name=f"accel_{abs(hash(str(sql))) % 99999}") as cur:
        cur.itersize = batch
        # pi-lens-ignore: python-sql-injection -- closed constant SQL / psycopg2.sql.Identifier only
        cur.execute(sql)
        while True:
            rows = cur.fetchmany(batch)
            if not rows:
                break
            frames.append(pd.DataFrame(rows, columns=columns))
    if not frames:
        return pd.DataFrame(columns=columns)
    return pd.concat(frames, ignore_index=True)


def export_phages(conn) -> pd.DataFrame:
    datasets = df_from_query(conn, "SELECT id, name FROM datasets ORDER BY id", ["id", "name"])
    if not datasets["id"].is_unique or bool(datasets["name"].isna().to_numpy().any()):
        raise ValueError("Dataset IDs must be unique and named")
    ds_by_id = {int(row["id"]): str(row["name"]) for row in datasets.to_dict("records")}

    phages = df_from_query(
        conn,
        'SELECT id, "Acession_ID", "Data_Sets_id", length, gc_content, host, '
        "completeness, taxonomy, cluster, subcluster, is_mapped, is_rep FROM phages",
        [
            "id",
            "accession",
            "dataset_id",
            "length",
            "gc_content",
            "host",
            "completeness",
            "taxonomy",
            "cluster",
            "subcluster",
            "is_mapped",
            "is_rep",
        ],
    )
    if phages.empty or not phages["id"].is_unique or bool(phages["id"].isna().to_numpy().any()):
        raise ValueError("Phage IDs must be nonempty, nonnull and unique")
    if bool(phages["dataset_id"].isna().to_numpy().any()) or not bool(
        phages["dataset_id"].isin(ds_by_id).to_numpy().all()
    ):
        raise ValueError("Some phages reference a missing dataset")
    if bool(phages["accession"].isna().to_numpy().any()) or bool(
        phages.duplicated(["dataset_id", "accession"]).to_numpy().any()
    ):
        raise ValueError("Phage (dataset_id, accession) keys must be nonnull and unique")
    phages["dataset"] = phages["dataset_id"].map(lambda d: ds_by_id[int(d)])

    lifestyle = df_from_query(
        conn,
        "SELECT DISTINCT ON (phage_id) phage_id, lifestyle FROM phage_lifestyle ORDER BY phage_id, id",
        ["id", "lifestyle"],
    )
    hosts = df_from_query(
        conn,
        'SELECT DISTINCT ON (phage_id) phage_id, "Phylum", "Class", "Order", "Family", "Genus", "Species" '
        "FROM phage_hosts ORDER BY phage_id, id",
        ["id", "host_phylum", "host_class", "host_order", "host_family", "host_genus", "host_species"],
    )

    phages = phages.merge(lifestyle, on="id", how="left", validate="one_to_one")
    phages = phages.merge(hosts, on="id", how="left", validate="one_to_one")
    return phages


def export_feature_counts(conn, phages: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return numeric-ID, accession-only and (dataset, accession) counts."""
    from psycopg2 import sql as pgsql

    catalog = phages[["dataset_id", "dataset"]].drop_duplicates()
    by_dataset_id = {int(row[0]): str(row[1]) for row in catalog.itertuples(index=False, name=None)}
    by_dataset_name = {name.lower(): name for name in by_dataset_id.values()}

    def dataset_name(value: object) -> str:
        token = str(value or "").strip()
        name = by_dataset_id.get(int(token)) if token.isdecimal() else by_dataset_name.get(token.lower())
        if not name:
            raise ValueError(f"Unknown feature dataset label: {token!r}")
        return name

    by_id_frames = []
    for col, sql in FEATURE_SQL_ID.items():
        df = df_from_query(conn, sql, ["id", col])
        if not df["id"].is_unique:
            raise ValueError(f"Duplicate phage IDs in {col}")
        by_id_frames.append(df.set_index("id"))
    by_id = pd.concat(by_id_frames, axis=1).reset_index() if by_id_frames else pd.DataFrame(columns=["id"])

    acc_frames = []
    for col, sql in FEATURE_SQL_ACC.items():
        df = df_from_query(conn, sql, ["accession", col])
        if not df["accession"].is_unique:
            raise ValueError(f"Duplicate accessions in {col}")
        acc_frames.append(df.set_index("accession"))
    by_acc = pd.concat(acc_frames, axis=1).reset_index() if acc_frames else pd.DataFrame(columns=["accession"])

    dataset_frames = []
    for col, sql in FEATURE_SQL_DATASET_ACC.items():
        df = df_from_query(conn, sql, ["accession", "dataset", col])
        df["dataset"] = df["dataset"].map(dataset_name)
        if bool(df.duplicated(["dataset", "accession"]).to_numpy().any()):
            raise ValueError(f"Duplicate (dataset, accession) feature keys in {col}")
        dataset_frames.append(df.set_index(["dataset", "accession"]))

    # The NCBI protein track covers datasets 1..5; the other suffixes map
    # one-to-one to a named catalog dataset. Quoted identifiers preserve case.
    ncbi = cast(pd.DataFrame, phages.loc[phages["dataset_id"].isin(range(1, 6)), ["accession", "dataset"]].copy())
    if bool(ncbi.duplicated(["accession"]).to_numpy().any()):
        raise ValueError("NCBI TMHMM cannot disambiguate repeated accessions across datasets 1..5")
    tmhmm_frames = []
    for sfx in TMHMM_SUFFIXES:
        table = f"phage_protein_tmhmm_{sfx}"
        stmt = pgsql.SQL('SELECT "Phage_Acession_ID", COUNT(*) FROM {} GROUP BY "Phage_Acession_ID"').format(
            pgsql.Identifier(table)
        )
        df = df_from_query(conn, stmt, ["accession", "n_transmembrane"])
        if not df["accession"].is_unique:
            raise ValueError(f"Duplicate accessions in {table}")
        if sfx == "NCBI":
            df = df.merge(ncbi, on="accession", how="left", validate="one_to_one")
            if bool(df["dataset"].isna().to_numpy().any()):
                raise ValueError("NCBI TMHMM contains an accession missing from datasets 1..5")
        else:
            df["dataset"] = dataset_name(sfx)
        tmhmm_frames.append(df.set_index(["dataset", "accession"])[["n_transmembrane"]])
    dataset_frames.append(pd.concat(tmhmm_frames, axis=0).groupby(level=[0, 1]).sum())
    by_dataset_acc = pd.concat(dataset_frames, axis=1).reset_index()
    if bool(by_dataset_acc.duplicated(["dataset", "accession"]).to_numpy().any()):
        raise ValueError("Duplicate dataset/accession feature-count rows")
    return by_id, by_acc, by_dataset_acc


def availability_bitmap(seq_root: Path) -> tuple[pd.DataFrame, dict[str, int]]:
    """Inventory by (dataset, accession); a shared accession must never inherit another dataset's files."""
    masks: dict[tuple[str, str], int] = {}
    inventories: dict[str, int] = {}

    def scan(base: Path, suffixes: tuple[str, ...], kind: str, bit: int) -> None:
        if not base.is_dir():
            raise FileNotFoundError(f"Required {kind} source directory is missing: {base}")
        seen = 0
        for ds_entry in os.scandir(base):
            if not ds_entry.is_dir():
                continue
            for entry in os.scandir(ds_entry.path):
                if not entry.is_file():
                    continue
                for suffix in suffixes:
                    if entry.name.endswith(suffix):
                        key = (ds_entry.name, entry.name[: -len(suffix)])
                        masks[key] = masks.get(key, 0) | bit
                        seen += 1
                        break
        if not seen:
            raise ValueError(f"Required {kind} source contains no files: {base}")
        inventories[kind] = seen

    scan(seq_root / "phage_fasta", (".fasta",), "fasta", AVAIL_FASTA)
    scan(seq_root / "phage_faa", (".faa",), "faa", AVAIL_FAA)
    scan(seq_root / "phage_gff3" / "individual_data", (".gff3", ".gff"), "gff3", AVAIL_GFF3)
    return (
        pd.DataFrame(
            [(dataset, accession, mask) for (dataset, accession), mask in masks.items()],
            columns=["dataset", "accession", "avail_mask"],
        ),
        inventories,
    )


def validate_feature_keys(
    phages: pd.DataFrame,
    by_id: pd.DataFrame,
    by_acc: pd.DataFrame,
    by_dataset_acc: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame | None, dict[str, Any]]:
    """Drop only true catalog orphans, reporting them; reject mismatched dataset labels."""
    unknown_ids = set(by_id["id"].dropna().tolist()) - set(phages["id"].tolist())
    unknown_accessions = set(by_acc["accession"].dropna().tolist()) - set(phages["accession"].tolist())
    if unknown_ids or unknown_accessions:
        raise ValueError(
            f"Feature counts reference uncatalogued phages: {len(unknown_ids)} IDs, "
            f"{len(unknown_accessions)} accession-only keys"
        )
    report: dict[str, Any] = {"discarded_dataset_accession_pairs": 0, "tracks": {}}
    if by_dataset_acc is None or by_dataset_acc.empty:
        return by_dataset_acc, report
    known = pd.MultiIndex.from_frame(cast(pd.DataFrame, phages[["dataset", "accession"]]))
    counted = pd.MultiIndex.from_frame(cast(pd.DataFrame, by_dataset_acc[["dataset", "accession"]]))
    unknown_pairs = counted.difference(known)
    if not len(unknown_pairs):
        return by_dataset_acc, report
    mismatched = set(unknown_pairs.get_level_values("accession")) & set(phages["accession"])
    if mismatched:
        raise ValueError(f"Feature dataset labels conflict with catalog for {len(mismatched)} accessions")
    indexed = by_dataset_acc.set_index(["dataset", "accession"])
    orphan = indexed.loc[unknown_pairs]
    report = {
        "discarded_dataset_accession_pairs": len(unknown_pairs),
        "tracks": {col: int(orphan[col].notna().to_numpy().sum()) for col in COUNT_COLUMNS if col in orphan},
    }
    print(f"      catalog-external feature keys (excluded): {report}", flush=True)
    return indexed.drop(index=unknown_pairs).reset_index(), report


def merge_feature_counts(
    phages: pd.DataFrame, by_id: pd.DataFrame, by_acc: pd.DataFrame, by_dataset_acc: pd.DataFrame
) -> tuple[pd.DataFrame, dict[str, int], int, dict[str, Any]]:
    """Keep accession-only ambiguity as null rather than claiming zero hits."""
    reconciled, orphan_report = validate_feature_keys(phages, by_id, by_acc, by_dataset_acc)
    assert reconciled is not None
    phages = phages.merge(by_id, on="id", how="left", validate="one_to_one")
    phages = phages.merge(by_acc, on="accession", how="left", validate="many_to_one")
    phages = phages.merge(reconciled, on=["dataset", "accession"], how="left", validate="one_to_one")
    for col in COUNT_COLUMNS:
        if col not in phages:
            phages[col] = 0
        phages[col] = phages[col].fillna(0).astype("Int64")
    ambiguous = phages["accession"].duplicated(keep=False)
    for col in ("n_crispr", "n_terminator"):
        phages.loc[ambiguous, col] = pd.NA
    unknown_counts = {col: int(phages[col].isna().to_numpy().sum()) for col in COUNT_COLUMNS}
    return phages, unknown_counts, int(ambiguous.to_numpy().sum() // 2), orphan_report


def validate_wide(phages: pd.DataFrame, expected_rows: int) -> None:
    if len(phages) != expected_rows or not phages["id"].is_unique:
        raise ValueError("A join duplicated or dropped phage rows")
    if bool(phages["dataset"].isna().to_numpy().any()) or bool(phages["accession"].isna().to_numpy().any()):
        raise ValueError("A phage has no dataset or accession")
    if bool(phages["avail_mask"].isna().to_numpy().any()) or not bool(
        phages["avail_mask"].between(0, 7).to_numpy().all()
    ):
        raise ValueError("Invalid file availability bitmap")
    if not bool((phages["avail_mask"] > 0).to_numpy().any()):
        raise ValueError("No catalog accession matched the local file inventory")


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def publish(tmp_dir: Path, out_dir: Path, current: Path) -> None:
    """Publish an immutable version, then atomically replace the current symlink."""
    if out_dir.exists() or out_dir.is_symlink():
        raise FileExistsError(f"Version already exists: {out_dir}; use a new --version")
    if current.exists() and not current.is_symlink():
        raise ValueError(f"Refusing to replace non-symlink current: {current}")
    os.rename(tmp_dir, out_dir)
    candidate = current.with_name(current.name + f".{os.getpid()}.tmp")
    try:
        candidate.symlink_to(out_dir.name)
        os.replace(candidate, current)
    finally:
        candidate.unlink(missing_ok=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--service-env", type=Path, required=True)
    ap.add_argument("--out-root", type=Path, required=True)
    ap.add_argument("--version", required=True)
    ap.add_argument("--seq-root", type=Path, default=Path("/data2/platform/phage_database/phage_sequence"))
    args = ap.parse_args()

    t0 = time.perf_counter()
    env = load_service_env(args.service_env)
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}", args.version) or args.version in {"current", ".", ".."}:
        ap.error("--version must be a simple, non-current directory name")
    if not args.out_root.is_dir():
        ap.error("--out-root must exist")
    out_dir = args.out_root / args.version
    if out_dir.exists() or out_dir.is_symlink():
        raise FileExistsError(f"Version already exists: {out_dir}")
    tmp_dir = Path(tempfile.mkdtemp(prefix=f".{args.version}.", dir=args.out_root))

    print("[1/5] exporting phages + lifestyle + hosts from PostgreSQL…", flush=True)
    conn = connect(env)
    try:
        conn.set_session(readonly=True, isolation_level="REPEATABLE READ")
        phages = export_phages(conn)
        source_rows = len(phages)
        print(f"      phages rows: {source_rows}", flush=True)
        print("[2/5] exporting 7 feature counts…", flush=True)
        by_id, by_acc, by_dataset_acc = export_feature_counts(conn, phages)
    finally:
        conn.close()
    phages, unknown_counts, ambiguous_accessions, orphan_report = merge_feature_counts(
        phages, by_id, by_acc, by_dataset_acc
    )
    count_cols = list(COUNT_COLUMNS)

    print("[3/5] scanning availability bitmap…", flush=True)
    avail, inventories = availability_bitmap(args.seq_root)
    phages = phages.merge(avail, on=["dataset", "accession"], how="left", validate="one_to_one")
    phages["avail_mask"] = phages["avail_mask"].fillna(0).astype("int64")
    validate_wide(phages, source_rows)

    print("[4/5] writing phages_wide.parquet…", flush=True)
    for col in (
        "lifestyle",
        "completeness",
        "taxonomy",
        "host",
        "cluster",
        "subcluster",
        "host_phylum",
        "host_class",
        "host_order",
        "host_family",
        "host_genus",
        "host_species",
    ):
        if col in phages:
            phages[col] = phages[col].fillna("")
    phages["gc_content"] = pd.to_numeric(phages["gc_content"], errors="coerce")
    phages = phages.sort_values(["dataset", "is_mapped", "completeness", "id"], kind="stable")

    wide = tmp_dir / "phages_wide.parquet"
    table = pa.Table.from_pandas(phages, preserve_index=False)
    pq.write_table(table, wide, compression="zstd", row_group_size=100000)

    print("[5/5] validating + manifest + atomic publish…", flush=True)
    reread = pq.read_table(wide, columns=["id", "avail_mask", *count_cols])
    if reread.num_rows != source_rows or len(set(reread.column("id").to_pylist())) != source_rows:
        raise ValueError("Phage ID uniqueness/count mismatch after Parquet write")
    count_totals = {col: sum(int(v) for v in phages[col].tolist() if v is not pd.NA) for col in count_cols}
    for col in count_cols:
        if sum(int(v) for v in reread.column(col).to_pylist() if v is not None) != count_totals[col]:
            raise ValueError(f"Annotation count mismatch after Parquet write: {col}")
    manifest = {
        "format_version": 2,
        "version": args.version,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "phages_rows": source_rows,
        "feature_tracks": count_totals,
        "annotation_unknown_rows": unknown_counts,
        "ambiguous_accessions": ambiguous_accessions,
        "uncatalogued_source_features": orphan_report,
        "feature_tables": "deferred; wide-table annotation counts only",
        "avail_bits": {"fasta": AVAIL_FASTA, "faa": AVAIL_FAA, "gff3": AVAIL_GFF3},
        "file_inventory": inventories,
        "files": {},
        "source": {
            "pg_host": env.get("PGHOST"),
            "pg_database": env.get("PGDATABASE"),
            "seq_root": str(args.seq_root),
            "read_only": True,
        },
        "build_seconds": round(time.perf_counter() - t0, 1),
    }
    manifest["files"][wide.name] = {"bytes": wide.stat().st_size, "sha256": sha256_of(wide)}
    (tmp_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    publish(tmp_dir, out_dir, args.out_root / "current")
    print(
        f"DONE in {manifest['build_seconds']}s -> {out_dir} "
        f"({manifest['files']['phages_wide.parquet']['bytes'] / 1e6:.1f} MB, {len(phages)} rows)",
        flush=True,
    )


if __name__ == "__main__":
    main()
