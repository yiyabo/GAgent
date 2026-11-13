"""PyArrow columnar acceleration engine for the PhageScope V2 data service (Phase 2).

Reads the GAgent-private ``phages_wide.parquet`` built by
``scripts/build_phagescope_accel.py`` and answers catalog queries with
``pyarrow.dataset`` + ``pyarrow.compute`` instead of PostgreSQL:

- ``search_phages`` with the full filter set PLUS agent-only predicates the SQL
  path cannot express cheaply (``amr_free``, ``virulence_free``, ``min_trna``,
  ``has_crispr``, ``has_anticrispr``, ``require_files``);
- multi-dimensional ``aggregate`` (up to 3 group-by columns with count and
  length stats) versus the single-dimension SQL GROUP BY;
- ``cohort_report`` computed in one in-process pass (no 5-6 SQL round-trips).

Every result is shaped to be interchangeable with the SQL implementation in
``db_queries.py`` so callers can fall back transparently when the acceleration
layer is not built. The Parquet file is opened read-only; nothing here writes
to the production database or to the PhageScope website.
"""

from __future__ import annotations

# pyright: reportAttributeAccessIssue=false
# pyarrow.compute / pyarrow.dataset are C-extension modules whose functions
# pyright cannot enumerate (stub is incomplete). Every compute function used
# here is verified present at runtime (pyarrow 17 on the 620b host, 26 in the
# app image), including bit_wise_and used for the avail_mask require_files test.
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds

try:
    from . import db_queries as dq
except ImportError:  # host service is also run as a standalone script
    import db_queries as dq

AVAIL_FASTA, AVAIL_FAA, AVAIL_GFF3 = 1, 2, 4

_GROUP_BY_COLUMNS = {
    "dataset",
    "completeness",
    "lifestyle",
    "taxonomy",
    "cluster",
    "host_phylum",
    "host_class",
    "host_order",
    "host_family",
    "host_genus",
    "host_species",
}
_SORT_COLUMNS = {"id": "id", "length_desc": "length", "length_asc": "length"}
_FEATURE_COUNT_COLUMNS = (
    "n_trna",
    "n_crispr",
    "n_terminator",
    "n_anticrispr",
    "n_virulence",
    "n_amr",
    "n_transmembrane",
)


def accel_root_from_env() -> Path | None:
    raw = os.environ.get("PHAGE_ACCEL_ROOT", "").strip()
    return Path(raw) if raw else None


class AccelEngine:
    """Read-only PyArrow engine over phages_wide.parquet."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self._dataset: ds.Dataset | None = None
        self._known_datasets: dict[str, str] | None = None

    @property
    def available(self) -> bool:
        wide = self.root / "phages_wide.parquet"
        manifest_path = self.root / "manifest.json"
        if not wide.is_file() or not manifest_path.is_file():
            return False
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            info = manifest["files"][wide.name]
            if manifest.get("format_version") != 2 or not isinstance(manifest.get("feature_tracks"), dict):
                return False
            if wide.stat().st_size != info["bytes"] or not info.get("sha256"):
                return False
            import pyarrow.parquet as pq

            meta = pq.ParquetFile(wide).metadata
            if meta.num_rows != manifest["phages_rows"]:
                return False
            if not {"id", "dataset", "accession", "avail_mask", *_FEATURE_COUNT_COLUMNS}.issubset(meta.schema.names):
                return False
            digest = hashlib.sha256()
            with wide.open("rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    digest.update(chunk)
            return digest.hexdigest() == info["sha256"]
        except (OSError, KeyError, TypeError, ValueError):
            return False

    @property
    def dataset(self) -> ds.Dataset:
        if self._dataset is None:
            self._dataset = ds.dataset(str(self.root / "phages_wide.parquet"), format="parquet")
        return self._dataset

    @property
    def known_datasets(self) -> dict[str, str]:
        """Cache the stable dataset names for this immutable snapshot."""
        if self._known_datasets is None:
            names = self.dataset.to_table(columns=["dataset"]).column("dataset").unique().to_pylist()
            self._known_datasets = {str(name).lower(): str(name) for name in names}
        return self._known_datasets

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    def _predicate(self, filters: dict[str, Any], *, default_annotated: bool = False) -> pc.Expression:
        exprs: list[pc.Expression] = []
        if not isinstance(filters, dict):
            raise ValueError("filters must be an object")
        if filters.get("host_rank") or filters.get("host_name"):
            raise ValueError("host_rank/host_name with accelerated filters is not supported")
        for key in ("amr_free", "virulence_free", "has_crispr", "has_anticrispr", "is_mapped", "is_rep"):
            if key in filters and not isinstance(filters[key], bool):
                raise ValueError(f"{key} must be a boolean")
        if "min_trna" in filters and (
            isinstance(filters["min_trna"], bool) or not isinstance(filters["min_trna"], int) or filters["min_trna"] < 0
        ):
            raise ValueError("min_trna must be a nonnegative integer")
        if filters.get("accession") and filters.get("accessions"):
            raise ValueError("Specify accession or accessions, not both")

        def add(e: pc.Expression) -> None:
            exprs.append(e)

        is_mapped = filters.get("is_mapped")
        if is_mapped is not None:
            add(pc.field("is_mapped") == bool(is_mapped))
        elif default_annotated and not filters:
            add(pc.field("is_mapped") == False)  # noqa: E712 - SQL default for unfiltered aggregates

        if filters.get("phage_ids"):
            ids = filters["phage_ids"]
            if not isinstance(ids, list) or len(ids) > 500:
                raise ValueError("phage_ids must be a list of 1..500 integers")
            add(pc.field("id").isin([int(x) for x in ids]))
        if filters.get("accession"):
            add(pc.field("accession") == str(filters["accession"]))
        if filters.get("accessions"):
            accessions = filters["accessions"]
            if not isinstance(accessions, list) or not 1 <= len(accessions) <= 500:
                raise ValueError("accessions must be a list of 1..500 strings")
            add(pc.field("accession").isin([str(a).strip() for a in accessions]))
        names = [str(filters[k]).strip() for k in ("dataset",) if filters.get(k)]
        if filters.get("datasets"):
            if not isinstance(filters["datasets"], list):
                raise ValueError("datasets must be a list")
            names.extend(str(d).strip() for d in filters["datasets"] if str(d).strip())
        if names:
            known = self.known_datasets
            # Match the SQL service's canonical spelling for dataset aliases.
            canonical = []
            for name in names:
                resolved = known.get(name.lower()) or known.get(dq.CANONICAL_DATASETS.get(name.lower(), "").lower())
                if resolved is None:
                    raise ValueError(f"Unknown dataset name: {name!r}.")
                canonical.append(resolved)
            add(pc.field("dataset").isin(canonical))
        if filters.get("completeness"):
            comp = filters["completeness"]
            values = comp if isinstance(comp, list) else [comp]
            add(pc.field("completeness").isin([str(c) for c in values]))
        if filters.get("lifestyle"):
            add(pc.field("lifestyle") == str(filters["lifestyle"]))
            add(pc.field("is_mapped") == False)  # noqa: E712 - lifestyle SQL excludes mapped rows
        if filters.get("taxonomy"):
            tax = str(filters["taxonomy"])
            if filters.get("taxonomy_match") == "contains":
                add(pc.match_substring(pc.field("taxonomy"), tax, ignore_case=True))
            else:
                add(pc.starts_with(pc.field("taxonomy"), tax))
        if filters.get("host"):
            host = str(filters["host"])
            if filters.get("host_match") == "contains":
                add(pc.match_substring(pc.field("host"), host, ignore_case=True))
            else:
                add(pc.starts_with(pc.field("host"), host))
        if filters.get("cluster"):
            add(pc.starts_with(pc.field("cluster"), str(filters["cluster"])))
        if filters.get("subcluster"):
            add(pc.starts_with(pc.field("subcluster"), str(filters["subcluster"])))
        if filters.get("is_rep") is not None:
            add(pc.field("is_rep") == bool(filters["is_rep"]))
        for key in ("length_min", "length_max"):
            if filters.get(key) is not None:
                val = int(filters[key])
                add(pc.field("length") >= val if key.endswith("min") else pc.field("length") <= val)
        for key, op in (("gc_min", ">="), ("gc_max", "<=")):
            if filters.get(key) is not None:
                val = float(filters[key])
                add(pc.field("gc_content") >= val if op == ">=" else pc.field("gc_content") <= val)

        # agent-only predicates (not available on the SQL path)
        if filters.get("amr_free"):
            add(pc.field("n_amr") == 0)
        if filters.get("virulence_free"):
            add(pc.field("n_virulence") == 0)
        if filters.get("min_trna") is not None:
            add(pc.field("n_trna") >= int(filters["min_trna"]))
        if filters.get("has_crispr"):
            add(pc.field("n_crispr") > 0)
        if filters.get("has_anticrispr"):
            add(pc.field("n_anticrispr") > 0)
        if filters.get("require_files"):
            mask = 0
            if not isinstance(filters["require_files"], list) or not filters["require_files"]:
                raise ValueError("require_files must be a nonempty list")
            for kind in filters["require_files"]:
                if kind not in {"fasta", "faa", "gff3"}:
                    raise ValueError(f"Invalid require_files kind: {kind!r}")
                mask |= {"fasta": AVAIL_FASTA, "faa": AVAIL_FAA, "gff3": AVAIL_GFF3}[kind]
            add(pc.bit_wise_and(pc.field("avail_mask"), mask) == mask)

        if not exprs:
            return pc.scalar(True)
        out = exprs[0]
        for e in exprs[1:]:
            out = out & e
        return out

    @staticmethod
    def requires_accel(filters: dict[str, Any], group_by: Any = None) -> bool:
        """True when the request can ONLY be served by the columnar layer."""
        if any(
            filters.get(k) is not None and filters.get(k) != [] and filters.get(k) is not False
            for k in (
                "amr_free",
                "virulence_free",
                "min_trna",
                "has_crispr",
                "has_anticrispr",
                "require_files",
            )
        ):
            return True
        return isinstance(group_by, (list, tuple)) and len(group_by) > 1

    # ------------------------------------------------------------------
    # queries
    # ------------------------------------------------------------------
    def aggregate(
        self,
        filters: dict[str, Any],
        group_by: Any,
        *,
        limit: int = 50,
    ) -> dict[str, Any]:
        cols = [group_by] if isinstance(group_by, str) else list(group_by or [])
        if not cols or len(cols) > 3 or any(c not in _GROUP_BY_COLUMNS for c in cols) or len(set(cols)) != len(cols):
            raise ValueError(f"group_by must be 1-3 of {sorted(_GROUP_BY_COLUMNS)}")
        pred = self._predicate(filters, default_annotated=True)
        if "lifestyle" in cols:
            pred = pred & (pc.field("is_mapped") == False)  # noqa: E712 - SQL lifestyle join excludes mapped
        table = self.dataset.to_table(filter=pred, columns=cols + ["id", "length"])
        grouped = table.group_by(cols).aggregate(
            [
                ("id", "count"),
                ("length", "min"),
                ("length", "mean"),
                ("length", "max"),
            ]
        )
        groups = []
        for row in grouped.to_pylist():
            if any(row[c] in (None, "") for c in cols):
                continue
            key = row[cols[0]] if len(cols) == 1 else [row[c] for c in cols]
            groups.append(
                {
                    "key": key if key is not None else "-",
                    "count": int(row["id_count"]),
                    "min_length": row["length_min"],
                    "avg_length": int(round(row["length_mean"])) if row["length_mean"] is not None else None,
                    "max_length": row["length_max"],
                }
            )
        groups.sort(key=lambda g: -g["count"])
        return {
            "group_by": cols if len(cols) > 1 else cols[0],
            "source": "accel_parquet",
            "groups": groups[: max(1, min(limit, 100))],
        }

    def availability(self, phage_ids: list[int]) -> list[dict[str, Any]]:
        """Snapshot file inventory for a bounded materialization precheck."""
        table = self.dataset.to_table(
            filter=pc.field("id").isin(phage_ids),
            columns=["id", "dataset", "accession", "avail_mask"],
        )
        return table.to_pylist()

    def filter_ids(
        self,
        filters: dict[str, Any],
        *,
        sort_by: str = "id",
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[int], int]:
        """Return (ordered phage ids for one page, total match count).

        Used for the hybrid path: accel performs the (possibly agent-only)
        filtering, then SQL enriches just those ids so the response keeps the
        exact SQL field shape (Acession_ID, protein_count, host_ranks, ...).
        """
        pred = self._predicate(filters)
        sort_col = _SORT_COLUMNS.get(sort_by, "id")
        columns = ["id"] if sort_col == "id" else ["id", sort_col]
        table = self.dataset.to_table(filter=pred, columns=columns)
        total = table.num_rows
        table = table.sort_by(
            [
                (sort_col, "descending" if sort_by == "length_desc" else "ascending"),
                ("id", "descending" if sort_by == "length_desc" else "ascending"),
            ]
            if sort_col != "id"
            else [("id", "ascending")]
        )
        window = table.slice(max(0, int(offset)), max(1, int(limit)))
        return [int(x) for x in window.column("id").to_pylist()], int(total)

    def cohort_report(
        self,
        filters: dict[str, Any],
        *,
        sample_limit: int = 5,
        sort_by: str = "id",
    ) -> dict[str, Any]:
        pred = self._predicate(filters, default_annotated=True)
        table = self.dataset.to_table(filter=pred)
        n = table.num_rows
        length_col = table.column("length") if n else pa.array([])
        minimum = pc.min(length_col).as_py() if n else None
        average = pc.mean(length_col).as_py() if n else None
        maximum = pc.max(length_col).as_py() if n else None
        stats = {
            "min": int(minimum) if minimum is not None else None,
            "avg": int(round(average)) if average is not None else None,
            "max": int(maximum) if maximum is not None else None,
        }
        facets = {
            "by_dataset": self.aggregate(filters, "dataset", limit=30)["groups"],
            "by_completeness": self.aggregate(filters, "completeness", limit=8)["groups"],
            "by_lifestyle": self.aggregate(filters, "lifestyle", limit=4)["groups"],
            "top_host_genera": self.aggregate(filters, "host_genus", limit=10)["groups"],
        }
        sort_col = _SORT_COLUMNS.get(sort_by, "id")
        sort_keys = [(sort_col, "descending" if sort_by == "length_desc" else "ascending")]
        if sort_col != "id":
            sort_keys.append(("id", "descending" if sort_by == "length_desc" else "ascending"))
        ordered = table.sort_by(sort_keys)
        sample = ordered.slice(0, max(1, min(int(sample_limit), 20))).column("id").to_pylist()
        return {
            "count": int(n),
            "length": stats,
            "facets": facets,
            "sample_ids": [int(pid) for pid in sample],
            "source": "accel_parquet",
        }
