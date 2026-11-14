#!/usr/bin/env python3
"""Compare bounded protein-family retrieval modes and create an unlabeled review sheet.

Run on the GAgent host as phage_ro. This reads PostgreSQL and the immutable
FTS5 snapshot; it never calls a model or writes to PhageScope's source data.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.phagescope_data_service import db_queries as dq
from app.services.phagescope_data_service.family_fts_engine import FamilyFTSEngine

DEFAULT_QUERIES = (
    Path(__file__).resolve().parent.parent
    / "deploy/phagescope_data_service/family_search_eval_queries.json"
)
MODES = ("literal", "fts", "synonyms")


def load_queries(path: Path) -> list[dict[str, str]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Could not read the family search query list") from exc
    if data.get("format_version") != 1 or not isinstance(data.get("queries"), list):
        raise ValueError("Expected a version-1 query list")
    queries = data["queries"]
    if not 1 <= len(queries) <= 100:
        raise ValueError("Query list must have 1..100 terms")
    seen: set[str] = set()
    for item in queries:
        if not isinstance(item, dict) or not all(
            isinstance(item.get(key), str) for key in ("term", "group", "kind")
        ):
            raise ValueError("Each query needs term, group and kind strings")
        term = item["term"].strip()
        if not term or len(term) > 120 or term.casefold() in seen:
            raise ValueError(
                "Queries must be nonempty, unique and at most 120 characters"
            )
        seen.add(term.casefold())
    return queries


def _read_env(path: Path) -> dict[str, str]:
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


def _preview(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "family_id": str(row.get("family_id") or ""),
        "consensus_product": str(row.get("consensus_product") or "")[:350]
        .replace("\n", " ")
        .replace("\t", " "),
        "dominant_class": str(row.get("dominant_class") or "")[:120],
        "member_count": row.get("member_count"),
        "match_kind": row.get("match_kind"),
    }


def evaluate(
    conn: Any, engine: Any, queries: list[dict[str, str]], *, top_k: int = 5
) -> list[dict[str, Any]]:
    """Return observed top-k retrieval and single-call times, without relevance judgments."""
    if not 1 <= top_k <= 20:
        raise ValueError("top_k must be 1..20")
    output = []
    for item in queries:
        record: dict[str, Any] = {**item, "modes": {}}
        for mode in MODES:
            start = time.perf_counter()
            try:
                result = (
                    dq.search_protein_families(
                        conn, keyword=item["term"], pagesize=top_k
                    )
                    if mode == "literal"
                    else engine.search(item["term"], mode=mode, pagesize=top_k)
                )
                record["modes"][mode] = {
                    "elapsed_ms": round((time.perf_counter() - start) * 1000, 1),
                    "has_more": bool(result.get("has_more")),
                    "results": [_preview(row) for row in result["results"]],
                    "expanded_terms": result.get("expanded_terms", []),
                }
            except (
                Exception
            ) as exc:  # record a failed query; do not invent an empty result
                record["modes"][mode] = {
                    "elapsed_ms": round((time.perf_counter() - start) * 1000, 1),
                    "error": type(exc).__name__,
                }
        output.append(record)
        print("query", item["term"], "complete", flush=True)
    return output


def _write_private(path: Path, content: str) -> None:
    with path.open("x", encoding="utf-8") as stream:
        os.chmod(path, 0o600)
        stream.write(content)


def save_artifacts(
    directory: Path, records: list[dict[str, Any]], *, metadata: dict[str, Any]
) -> None:
    directory.mkdir(mode=0o700, parents=False, exist_ok=False)
    _write_private(
        directory / "results.json",
        json.dumps(
            {"metadata": metadata, "queries": records}, ensure_ascii=False, indent=2
        )
        + "\n",
    )
    columns: tuple[str, ...] = (
        "query",
        "group",
        "kind",
        "family_id",
        "consensus_product",
        "dominant_class",
        "member_count",
        "literal_rank",
        "fts_rank",
        "synonyms_rank",
        "synonyms_match_kind",
        "expert_label",
        "expert_note",
    )
    review_path = directory / "review_candidates.tsv"
    with review_path.open("x", encoding="utf-8", newline="") as stream:
        os.chmod(review_path, 0o600)
        writer: csv.DictWriter[str] = csv.DictWriter(
            stream, fieldnames=list(columns), delimiter="\t"
        )
        writer.writeheader()
        for record in records:
            by_id: dict[str, dict[str, Any]] = {}
            for mode in MODES:
                for rank, row in enumerate(record["modes"][mode].get("results", []), 1):
                    fid = row["family_id"]
                    entry = by_id.setdefault(
                        fid,
                        {
                            "query": record["term"],
                            "group": record["group"],
                            "kind": record["kind"],
                            **{
                                key: row[key]
                                for key in (
                                    "family_id",
                                    "consensus_product",
                                    "dominant_class",
                                    "member_count",
                                )
                            },
                            "expert_label": "",
                            "expert_note": "",
                        },
                    )
                    entry[f"{mode}_rank"] = rank
                    if mode == "synonyms":
                        entry["synonyms_match_kind"] = row["match_kind"]
            for entry in by_id.values():
                writer.writerow(entry)

    summary = [
        "# PhageScope family search: candidate retrieval review",
        "",
        f"Generated: {metadata['checked_at']} | snapshot: {metadata['snapshot_version']} ({metadata['snapshot_built_at']})",
        f"Query list SHA-256: `{metadata['query_sha256']}` | top-k: {metadata['top_k']}",
        "",
        "These are observed retrieval sets, not expert relevance labels. The TSV leaves expert_label and expert_note empty.",
        "Top-k overlap is not corpus-level recall. Times are one-off host measurements, not latency guarantees.",
        "The indexed word/phrase mode and PostgreSQL substring mode have different matching semantics.",
        "",
        "| Query | Kind | Literal top-k | FTS top-k | Synonyms top-k | Alias rows in synonyms top-k | Literal ∩ FTS |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for record in records:
        modes = record["modes"]
        sets = {
            mode: {row["family_id"] for row in modes[mode].get("results", [])}
            for mode in MODES
        }
        shown = {
            mode: (
                f"error: {modes[mode]['error']}"
                if "error" in modes[mode]
                else str(len(sets[mode]))
            )
            for mode in MODES
        }
        aliases = (
            "—"
            if "error" in modes["synonyms"]
            else str(
                sum(
                    row.get("match_kind") == "alias"
                    for row in modes["synonyms"]["results"]
                )
            )
        )
        overlap = (
            "—"
            if "error" in modes["literal"] or "error" in modes["fts"]
            else str(len(sets["literal"] & sets["fts"]))
        )
        summary.append(
            f"| {record['term']} | {record['kind']} | {shown['literal']} | {shown['fts']} | "
            f"{shown['synonyms']} | {aliases} | {overlap} |"
        )
    summary.extend(
        [
            "",
            "Review labels (fill in TSV after expert inspection): same_function, related, different, insufficient_evidence.",
            "Rows whose descriptions suggest a structural domain or generic motif deserve special scrutiny; an alias match alone is not function proof.",
            "For future precision/recall estimates, freeze expert labels and evaluate a separate query set.",
            "",
        ]
    )
    _write_private(directory / "summary.md", "\n".join(summary))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service-env", type=Path, required=True)
    parser.add_argument("--index-root", type=Path, required=True)
    parser.add_argument("--queries", type=Path, default=DEFAULT_QUERIES)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()
    if args.out_dir.exists():
        parser.error("--out-dir must name a new directory")
    queries = load_queries(args.queries)
    env = _read_env(args.service_env)
    engine = FamilyFTSEngine(args.index_root)
    if not engine.available:
        raise ValueError("a validated family index is required")
    import psycopg2  # host-only dependency, never bundled credentials

    conn = psycopg2.connect(
        host=env["PGHOST"],
        port=env["PGPORT"],
        dbname=env["PGDATABASE"],
        user=env["PGUSER_RO"],
        password=env["PGPASSWORD_RO"],
        options="-c default_transaction_read_only=on -c statement_timeout=10000",
    )
    try:
        conn.autocommit = True
        records = evaluate(conn, engine, queries, top_k=args.top_k)
    finally:
        conn.close()
    metadata = {
        "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "snapshot_version": engine.manifest["version"],
        "snapshot_built_at": engine.manifest["built_at"],
        "source_role": "phage_ro",
        "top_k": args.top_k,
        "query_sha256": hashlib.sha256(args.queries.read_bytes()).hexdigest(),
        "mode_meaning": {
            "literal": "PostgreSQL product substring",
            "fts": "FTS5 token/phrase",
            "synonyms": "curated alias expansion",
        },
    }
    save_artifacts(args.out_dir, records, metadata=metadata)
    print("saved", args.out_dir, "queries", len(records), flush=True)


if __name__ == "__main__":
    main()
