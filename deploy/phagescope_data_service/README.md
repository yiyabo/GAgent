# GAgent PhageScope V2 Read-Only Data Service

Self-contained read-only service owned by GAgent (`/data3/platform/gagent/phagescope-service/`).
Requires **zero modifications** to PhageScope's website codebase or PostgreSQL tables.

## Safety Guarantees

1. Connects strictly as `phage_ro` (`NOSUPERUSER`, `default_transaction_read_only=on`, `statement_timeout=10000ms`, max 4 pooled connections).
2. Startup self-check verifies `INSERT`, `UPDATE`, and `DELETE` privileges on `phages` are all `False`; exits immediately otherwise.
3. Binds `127.0.0.1:18326` on the host and requires constant-time `X-API-Key` validation. `gagent-forwarder-1` exposes `18326` only on the internal Docker bridge (`FORWARD_BIND`).
4. Serves both legacy `/agent-api/v1/data/*` GET endpoints (`phagescope(action="gateway")`) and research-grade `/agent-api/v1/data/v2/*` POST endpoints (`phagescope_data`).

## Private Parquet snapshot (Phase 2, opt-in)

`app/services/phagescope_data_service/{server.py,db_queries.py,accel_engine.py}` are the host service sources; install all three together. `scripts/build_phagescope_accel.py` reads PostgreSQL as `phage_ro` in a repeatable-read, read-only transaction and scans only filenames from the sequence trees. Build with a **new version name** (for example `v2`); it refuses existing versions and updates `current` with an atomic symlink replacement only after row-count, uniqueness, count-sum, and SHA-256 checks. The earlier exploratory `v1` is not a Phase 2 release artifact. A failed build leaves `current` at its previous target.

The version contains `phages_wide.parquet` (seven per-phage annotation-count columns and dataset-scoped FASTA/FAA/GFF3 availability bits) plus `manifest.json` with `format_version=2`. In the source catalog, 22,720 accessions occur in both IMG_VR and MetaVR. AMR, VFDB, anti-CRISPR, and transmembrane counts are joined by dataset and accession (or numeric phage ID for tRNA). The CRISPR and terminator sources have no dataset key, so both counts are **null/unknown** for those duplicated accessions; predicates must never treat unknown as zero. The manifest records `annotation_unknown_rows`, `ambiguous_accessions`, and `uncatalogued_source_features` (feature rows with no catalog accession, excluded from the snapshot with per-track counts). If a feature row names an accession present under a different dataset, publication fails rather than recording a false zero. Feature-level `features/*.parquet` is deferred: this release exposes catalog-level counts only. A zero annotated CARD/VFDB hit is **not** evidence that a phage is clinically safe. The availability bitmap describes files at build time; the materializer checks real files and can fall back to the database, and a capped preview is never labeled a full genome.

Until a validated v2 build is published, leave host `service.env` without `PHAGE_ACCEL_ROOT` and leave app `deploy/.env` without `PHAGESCOPE_ACCEL_HOST_DIR`. Normal catalog queries continue through PostgreSQL. After validating live SQL parity and file samples, set `PHAGE_ACCEL_ROOT=/data3/platform/gagent/phagescope-accel/current` in the host service environment and `PHAGESCOPE_ACCEL_HOST_DIR=/data3/platform/gagent/phagescope-accel` in the app environment. Back up both configuration files with mode 0600, restart the host service and deploy the app through `gagentctl` after `gate`. New filters/multi-dimensional groups need the validated build; ordinary queries keep their SQL behavior. `execute_code` receives only the GAgent-owned Parquet root as a read-only bind (also allowlisted by the Docker proxy) and exposes `phagescope_dataset` / bounded `phagescope_dataframe` helpers through generated `gagent_tools` stubs. Roll back by removing these two environment variables, restoring the backed-up service files, and restarting normally; keep the immutable Parquet versions for audit.

## Private protein-family FTS5 snapshot (Phase 3)

`app/services/phagescope_data_service/family_fts_engine.py` joins the three host service modules above. `scripts/build_phagescope_family_fts.py` uses only `phage_ro` in a read-only repeatable-read transaction. It streams families with a nonblank `consensus_product` or `dominant_class` into a GAgent-owned SQLite FTS5 database; the entire 12.61M-family source count and the indexed subset count are reconciled in the manifest. The snapshot holds family search metadata and text, with no protein sequences. The `family_synonyms.json` file is a versioned, small alias list under `deploy/phagescope_data_service/`; aliases aid retrieval and do not establish biological function. The builder validates FTS5 and SQLite integrity, row counts, read-only reopen, and SHA-256 before switching `current` atomically. A failed build leaves `current` intact.

On 620b, copy the builder and synonym file to a GAgent-owned staging directory, then build a **new** version (the command requires an existing output directory):

```bash
python3 build_phagescope_family_fts.py \
  --service-env /data3/platform/gagent/phagescope-service/service.env \
  --synonyms /data3/platform/gagent/phagescope-family-search/family_synonyms.json \
  --out-root /data3/platform/gagent/phagescope-family-search \
  --version v3
```

After checking the manifest and sample queries, install `family_fts_engine.py` beside `server.py`, `db_queries.py`, and `accel_engine.py` in the GAgent host service; add `PHAGE_FAMILY_INDEX_ROOT=/data3/platform/gagent/phagescope-family-search/current` to its `service.env` and restart. No app mount or PostgreSQL change is needed. With the index configured, a family keyword defaults to indexed word/phrase matching across product and dominant class (`family_search_mode=auto`). `family_search_mode=synonyms` searches the curated aliases and marks `match_kind=literal|alias`; `family_search_mode=literal` preserves the original PostgreSQL substring query. `family_search_mode=fts` requires the index. Indexed results report `source`, `search_mode`, and `index_built_at`; the snapshot can lag a live database update. Direct `family_id`/`protein_id` queries, members, and searches without a keyword still use read-only PostgreSQL. A configured but invalid index yields `503 family_index_unavailable` for indexed requests. Roll back by removing the family root environment variable, restoring the previous host service modules, and restarting; keep immutable versions for audit.
