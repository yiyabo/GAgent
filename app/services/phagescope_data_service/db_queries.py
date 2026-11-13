"""Read-only SQL and file query helpers for the GAgent PhageScope V2 Data Service.

Every SQL query is assembled exclusively from closed constant string tables and
executed with `%s` parameter tuples inside read-only transactions
(`default_transaction_read_only=on`, `statement_timeout=10000`). User input is
never interpolated into SQL text or identifier names.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Any

DATASET_PROTEIN_SUFFIX: dict[str, str] = {
    "1": "NCBI",
    "2": "NCBI",
    "3": "NCBI",
    "4": "NCBI",
    "5": "NCBI",
    "genbank": "NCBI",
    "refseq": "NCBI",
    "ddbj": "NCBI",
    "embl": "NCBI",
    "tpg": "NCBI",
    "6": "PhagesDB",
    "phagesdb": "PhagesDB",
    "7": "GPD",
    "gpd": "GPD",
    "8": "GVD",
    "gvd": "GVD",
    "9": "MGV",
    "mgv": "MGV",
    "10": "TemPhD",
    "temphd": "TemPhD",
    "11": "CHVD",
    "chvd": "CHVD",
    "12": "IGVD",
    "igvd": "IGVD",
    "13": "IMG_VR",
    "img_vr": "IMG_VR",
    "img vr": "IMG_VR",
    "14": "GOV2",
    "gov2": "GOV2",
    "15": "STV",
    "stv": "STV",
    "16": "GSV",
    "gsv": "GSV",
    "17": "UHGV",
    "uhgv": "UHGV",
    "18": "HPGC",
    "hpgc": "HPGC",
    "19": "URPC",
    "urpc": "URPC",
    "20": "ELGV",
    "elgv": "ELGV",
    "21": "OVD",
    "ovd": "OVD",
    "22": "VMGC",
    "vmgc": "VMGC",
    "23": "OPD",
    "opd": "OPD",
    "24": "BAPS",
    "baps": "BAPS",
    "25": "SVD",
    "svd": "SVD",
    "26": "TYMEFLIES_Viral",
    "tymeflies_viral": "TYMEFLIES_Viral",
    "27": "MetaVR",
    "metavr": "MetaVR",
}

CANONICAL_DATASETS: dict[str, str] = {
    "genbank": "Genbank",
    "refseq": "RefSeq",
    "ddbj": "DDBJ",
    "embl": "EMBL",
    "tpg": "tpg",
    "phagesdb": "PhagesDB",
    "gpd": "GPD",
    "gvd": "GVD",
    "mgv": "MGV",
    "temphd": "TemPhD",
    "chvd": "CHVD",
    "igvd": "IGVD",
    "img_vr": "IMG_VR",
    "img vr": "IMG_VR",
    "gov2": "GOV2",
    "stv": "STV",
    "gsv": "GSV",
    "uhgv": "UHGV",
    "hpgc": "HPGC",
    "urpc": "URPC",
    "elgv": "ELGV",
    "ovd": "OVD",
    "vmgc": "VMGC",
    "opd": "OPD",
    "baps": "BAPS",
    "svd": "SVD",
    "tymeflies_viral": "TYMEFLIES_Viral",
    "metavr": "MetaVR",
}

PROTEIN_SUFFIXES = (
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

# Closed static table-to-SQL maps so dynamic user strings never enter SQL text.
_PROTEIN_COUNT_BY_PHAGE_SQL: dict[str, str] = {
    s: " ".join(('SELECT COUNT(*) FROM', '"' + "phage_protein_" + s + '"', 'WHERE "Phage_Acession_ID" = %s'))
    for s in PROTEIN_SUFFIXES
}
_PROTEIN_SELECT_HEAD: dict[tuple[str, bool], str] = {
    (s, inc): " ".join(
        (
            'SELECT "Protein_id", "Phage_Acession_ID", "Start_location", "Stop_location",',
            '"Strand", "Protein_product", "Protein_function_classification", dataset,',
            "prosequence" if inc else "NULL",
            "FROM",
            '"' + "phage_protein_" + s + '"',
        )
    )
    for s in PROTEIN_SUFFIXES
    for inc in (False, True)
}
_PROTEIN_COUNT_HEAD: dict[str, str] = {
    s: " ".join(("SELECT COUNT(*) FROM", '"' + "phage_protein_" + s + '"'))
    for s in PROTEIN_SUFFIXES
}
_TMHMM_COUNT_SQL: dict[str, str] = {
    s: " ".join(('SELECT COUNT(*) FROM', '"' + "phage_protein_tmhmm_" + s + '"', 'WHERE "Phage_Acession_ID" = %s'))
    for s in PROTEIN_SUFFIXES
}
_TMHMM_SELECT_SQL: dict[str, str] = {
    s: " ".join(
        (
            'SELECT "Protein_id", "Phage_Acession_ID", "predictedTMHsNumber" FROM',
            '"' + "phage_protein_tmhmm_" + s + '"',
            'WHERE "Phage_Acession_ID" = %s ORDER BY id ASC LIMIT %s OFFSET %s',
        )
    )
    for s in PROTEIN_SUFFIXES
}

HOST_RANK_EQ_CLAUSE: dict[str, str] = {
    "Phylum": 'ph."Phylum" = %s',
    "Class": 'ph."Class" = %s',
    "Order": 'ph."Order" = %s',
    "Family": 'ph."Family" = %s',
    "Genus": 'ph."Genus" = %s',
    "Species": 'ph."Species" = %s',
}
HOST_TABLE_EQ_CLAUSE: dict[str, str] = {
    "Phylum": '"Phylum" = %s',
    "Class": '"Class" = %s',
    "Order": '"Order" = %s',
    "Family": '"Family" = %s',
    "Genus": '"Genus" = %s',
    "Species": '"Species" = %s',
}
HOST_TABLE_NONEMPTY_CLAUSE: dict[str, str] = {
    "Phylum": '"Phylum" IS NOT NULL AND "Phylum" <> \'\'',
    "Class": '"Class" IS NOT NULL AND "Class" <> \'\'',
    "Order": '"Order" IS NOT NULL AND "Order" <> \'\'',
    "Family": '"Family" IS NOT NULL AND "Family" <> \'\'',
    "Genus": '"Genus" IS NOT NULL AND "Genus" <> \'\'',
    "Species": '"Species" IS NOT NULL AND "Species" <> \'\'',
}
GROUP_BY_EXPR: dict[str, str] = {
    "dataset": 'p."Data_Sets_id"',
    "completeness": "p.completeness",
    "lifestyle": "pl.lifestyle",
    "taxonomy": "p.taxonomy",
    "cluster": "p.cluster",
    "host_phylum": 'ph."Phylum"',
    "host_class": 'ph."Class"',
    "host_order": 'ph."Order"',
    "host_family": 'ph."Family"',
    "host_genus": 'ph."Genus"',
    "host_species": 'ph."Species"',
}
ORDER_BY_SQL: dict[str, str] = {
    "id": "p.id ASC",
    "length_desc": "p.length DESC NULLS LAST, p.id DESC",
    "length_asc": "p.length ASC NULLS LAST, p.id ASC",
}
# Jumbo phages exceed the old 200 kb gateway cap; the caller still chooses
# ``max_bases`` and this is only the ceiling.
MAX_SEQUENCE_BASES = 5_000_000
FEATURE_TYPES = frozenset(
    {"trna", "crispr", "anticrispr", "terminator", "virulence", "amr", "transmembrane"}
)

_PHAGE_SELECT_PREFIX = (
    'SELECT p.id, p."Acession_ID", p."Data_Sets_id", p.length, p.gc_content, '
    "p.host, p.completeness, p.taxonomy, p.cluster, p.subcluster, p.is_mapped, p.is_rep "
    "FROM phages p"
)


class ServiceQueryError(Exception):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _compose_sql(*segments: str) -> str:
    return " ".join(s for s in segments if s)


def canonical_dataset_name(raw: Any) -> str | None:
    if raw is None:
        return None
    return CANONICAL_DATASETS.get(str(raw).strip().lower())


def protein_suffix(dataset: Any) -> str | None:
    if dataset is None:
        return None
    text = str(dataset).strip()
    suffix = DATASET_PROTEIN_SUFFIX.get(text) or DATASET_PROTEIN_SUFFIX.get(text.lower())
    return suffix if suffix in PROTEIN_SUFFIXES else None


def contained_path(path: Path, root: Path) -> Path:
    root_real = os.path.realpath(str(root))
    path_real = os.path.realpath(str(path))
    if path_real != root_real and not path_real.startswith(root_real + os.sep):
        raise ServiceQueryError("path_rejected", "Path escaped allowed root.", 400)
    return Path(path_real)


def safe_component(value: Any) -> str:
    text = "" if value is None else str(value).strip()
    if not text or "/" in text or "\\" in text or text in {".", ".."} or ".." in text:
        raise ServiceQueryError("path_rejected", "Unsafe path component.", 400)
    return text


def verify_readonly_connection(conn: Any) -> str | None:
    """Return None if the PostgreSQL role satisfies read-only invariants, else reason."""
    with conn.cursor() as cur:
        cur.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
        row = cur.fetchone()
        if not row or bool(row[0]):
            return "database role is a superuser"
        cur.execute("SHOW default_transaction_read_only")
        ro = str(cur.fetchone()[0] or "").strip().lower()
        if ro != "on":
            return f"default_transaction_read_only is {ro!r}, expected 'on'"
        for priv in ("INSERT", "UPDATE", "DELETE"):
            cur.execute("SELECT has_table_privilege(current_user, 'phages', %s)", (priv,))
            if bool(cur.fetchone()[0]):
                return f"role has {priv} privilege on phages"
    return None


def fetch_datasets(conn: Any) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute("SELECT id, name FROM datasets ORDER BY id")
        return [{"id": int(r[0]), "name": r[1]} for r in cur.fetchall()]


def _dataset_maps(conn: Any) -> tuple[dict[int, str], dict[str, int]]:
    rows = fetch_datasets(conn)
    by_id = {r["id"]: str(r["name"]) for r in rows if r.get("name")}
    by_lower = {str(r["name"]).lower(): r["id"] for r in rows if r.get("name")}
    return by_id, by_lower


def _enrich_phage_rows(
    conn: Any,
    rows: list[tuple[Any, ...]],
    ds_by_id: dict[int, str],
    *,
    include_host_ranks: bool = False,
) -> list[dict[str, Any]]:
    if not rows:
        return []
    phage_ids = [int(r[0]) for r in rows]
    lifestyles: dict[int, tuple[str, str]] = {}
    host_ranks: dict[int, dict[str, Any]] = {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT phage_id, lifestyle, source FROM phage_lifestyle WHERE phage_id = ANY(%s)",
            (phage_ids,),
        )
        for pid, ls, src in cur.fetchall():
            if pid is not None and int(pid) not in lifestyles:
                lifestyles[int(pid)] = (ls or "-", src or "")
        if include_host_ranks:
            cur.execute(
                'SELECT phage_id, host_source, "Phylum", "Class", "Order", "Family", "Genus", "Species" '
                "FROM phage_hosts WHERE phage_id = ANY(%s)",
                (phage_ids,),
            )
            for pid, hsrc, phy, cls, ord_, fam, gen, sp in cur.fetchall():
                if pid is not None and int(pid) not in host_ranks:
                    host_ranks[int(pid)] = {
                        "host_source": hsrc or "",
                        "Phylum": phy or "",
                        "Class": cls or "",
                        "Order": ord_ or "",
                        "Family": fam or "",
                        "Genus": gen or "",
                        "Species": sp or "",
                    }

    results: list[dict[str, Any]] = []
    for row in rows:
        pid, acc, ds_id, length, gc, host, comp, tax, cluster, subcluster, is_mapped, is_rep = row
        is_mapped_bool = bool(is_mapped)
        ls_val = "-" if is_mapped_bool else lifestyles.get(int(pid), ("-", ""))[0]
        item: dict[str, Any] = {
            "id": int(pid),
            "Acession_ID": acc,
            "dataset": ds_by_id.get(int(ds_id)) if ds_id is not None else None,
            "length": length,
            "gc_content": gc,
            "host": host,
            "completeness": comp,
            "taxonomy": tax,
            "cluster": cluster,
            "subcluster": subcluster,
            "is_mapped": is_mapped_bool,
            "lifestyle": ls_val,
        }
        if include_host_ranks:
            item["is_rep"] = bool(is_rep)
            item["host_lineage"] = host_ranks.get(int(pid), {})
        results.append(item)
    return results


def _build_phage_where(
    filters: dict[str, Any],
    ds_by_lower: dict[str, int],
) -> tuple[list[str], list[Any], list[str]]:
    joins: list[str] = []
    clauses: list[str] = []
    params: list[Any] = []

    phage_ids = filters.get("phage_ids")
    if phage_ids:
        if not isinstance(phage_ids, list) or len(phage_ids) > 500:
            raise ServiceQueryError("invalid_parameter", "phage_ids must be a list of 1..500 integers.")
        try:
            clean_ids = [int(x) for x in phage_ids]
        except (TypeError, ValueError) as exc:
            raise ServiceQueryError("invalid_parameter", "phage_ids must contain integers.") from exc
        clauses.append("p.id = ANY(%s)")
        params.append(clean_ids)

    acc = filters.get("accession")
    accs = filters.get("accessions")
    if acc:
        clauses.append('p."Acession_ID" = %s')
        params.append(str(acc).strip())
    elif accs:
        if not isinstance(accs, list) or not 1 <= len(accs) <= 500:
            raise ServiceQueryError("invalid_parameter", "accessions must be a list of 1..500 strings.")
        clean_accs = [str(a).strip() for a in accs if str(a).strip()]
        if not clean_accs:
            raise ServiceQueryError("invalid_parameter", "accessions must not be empty.")
        clauses.append('p."Acession_ID" = ANY(%s)')
        params.append(clean_accs)

    ds_names: list[str] = []
    if filters.get("dataset"):
        ds_names.append(str(filters["dataset"]).strip())
    if filters.get("datasets"):
        if not isinstance(filters["datasets"], list):
            raise ServiceQueryError("invalid_parameter", "datasets must be a list of dataset names.")
        ds_names.extend(str(d).strip() for d in filters["datasets"] if str(d).strip())
    if ds_names:
        ds_ids: list[int] = []
        for name in ds_names:
            did = ds_by_lower.get(name.lower())
            if did is None:
                canonical = canonical_dataset_name(name)
                did = ds_by_lower.get(canonical.lower()) if canonical else None
            if did is None:
                raise ServiceQueryError("invalid_parameter", f"Unknown dataset name: {name!r}.")
            if did not in ds_ids:
                ds_ids.append(did)
        if len(ds_ids) == 1:
            clauses.append('p."Data_Sets_id" = %s')
            params.append(ds_ids[0])
        else:
            clauses.append('p."Data_Sets_id" = ANY(%s)')
            params.append(ds_ids)

    comp = filters.get("completeness")
    if comp:
        if isinstance(comp, list):
            clean_c = [str(c).strip() for c in comp if str(c).strip()]
            clauses.append("p.completeness = ANY(%s)")
            params.append(clean_c)
        else:
            clauses.append("p.completeness = %s")
            params.append(str(comp).strip())

    tax = filters.get("taxonomy")
    if tax:
        mode = str(filters.get("taxonomy_match") or "prefix").strip().lower()
        if mode == "contains":
            clauses.append("p.taxonomy ILIKE %s")
            params.append(f"%{str(tax).strip()}%")
        else:
            clauses.append("p.taxonomy LIKE %s")
            params.append(f"{str(tax).strip()}%")

    host = filters.get("host")
    if host:
        mode = str(filters.get("host_match") or "prefix").strip().lower()
        if mode == "contains":
            clauses.append("p.host ILIKE %s")
            params.append(f"%{str(host).strip()}%")
        else:
            clauses.append("p.host LIKE %s")
            params.append(f"{str(host).strip()}%")

    host_rank = filters.get("host_rank")
    host_name = filters.get("host_name")
    if host_rank or host_name:
        rank_clause = HOST_RANK_EQ_CLAUSE.get(str(host_rank or ""))
        if rank_clause is None:
            raise ServiceQueryError(
                "invalid_parameter",
                f"host_rank must be one of {', '.join(HOST_RANK_EQ_CLAUSE)}.",
            )
        if not host_name or not str(host_name).strip():
            raise ServiceQueryError("invalid_parameter", "host_name is required with host_rank.")
        joins.append("JOIN phage_hosts ph ON ph.phage_id = p.id")
        clauses.append(rank_clause)
        params.append(str(host_name).strip())

    if filters.get("cluster"):
        clauses.append("p.cluster LIKE %s")
        params.append(f"{str(filters['cluster']).strip()}%")
    if filters.get("subcluster"):
        clauses.append("p.subcluster LIKE %s")
        params.append(f"{str(filters['subcluster']).strip()}%")

    if filters.get("is_rep") is not None:
        clauses.append("p.is_rep = %s")
        params.append(bool(filters["is_rep"]))
    if filters.get("is_mapped") is not None:
        clauses.append("p.is_mapped = %s")
        params.append(bool(filters["is_mapped"]))

    try:
        if filters.get("length_min") is not None and filters.get("length_min") != "":
            clauses.append("p.length >= %s")
            params.append(int(filters["length_min"]))
        if filters.get("length_max") is not None and filters.get("length_max") != "":
            clauses.append("p.length <= %s")
            params.append(int(filters["length_max"]))
        if filters.get("gc_min") is not None and filters.get("gc_min") != "":
            clauses.append("NULLIF(p.gc_content, '')::double precision >= %s")
            params.append(float(filters["gc_min"]))
        if filters.get("gc_max") is not None and filters.get("gc_max") != "":
            clauses.append("NULLIF(p.gc_content, '')::double precision <= %s")
            params.append(float(filters["gc_max"]))
    except (TypeError, ValueError) as exc:
        raise ServiceQueryError("invalid_parameter", "length and gc filters must be numeric.") from exc

    lifestyle = filters.get("lifestyle")
    if lifestyle:
        joins.append("JOIN phage_lifestyle pl ON pl.phage_id = p.id")
        clauses.append("pl.lifestyle = %s")
        params.append(str(lifestyle).strip())
        clauses.append("p.is_mapped = FALSE")

    if not clauses:
        raise ServiceQueryError("missing_filter", "At least one filter is required.", 400)

    return joins, params, clauses


def query_phages(
    conn: Any,
    filters: dict[str, Any],
    *,
    page: int = 1,
    pagesize: int = 20,
    max_pagesize: int = 500,
    include_total: bool = False,
    include_host_ranks: bool = False,
    sort_by: str = "id",
) -> dict[str, Any]:
    page = max(1, int(page))
    pagesize = max(1, min(int(pagesize), max_pagesize))
    ds_by_id, ds_by_lower = _dataset_maps(conn)
    joins, params, clauses = _build_phage_where(filters, ds_by_lower)

    order_sql = ORDER_BY_SQL.get(sort_by, ORDER_BY_SQL["id"])
    join_sql = " ".join(joins)
    where_sql = " AND ".join(clauses)
    offset = (page - 1) * pagesize

    stmt = _compose_sql(
        _PHAGE_SELECT_PREFIX,
        join_sql,
        "WHERE",
        where_sql,
        "ORDER BY",
        order_sql,
        "LIMIT %s OFFSET %s",
    )
    with conn.cursor() as cur:
        cur.execute(stmt, [*params, pagesize + 1, offset])
        raw_rows = cur.fetchall()

        if (
            not raw_rows
            and page == 1
            and filters.get("accession")
            and "." not in str(filters["accession"])
            and len(clauses) == 1
        ):
            prefix = str(filters["accession"]).strip() + ".%"
            fallback_stmt = _compose_sql(
                _PHAGE_SELECT_PREFIX,
                'WHERE p."Acession_ID" LIKE %s ORDER BY p.id ASC LIMIT %s',
            )
            cur.execute(fallback_stmt, (prefix, pagesize + 1))
            raw_rows = cur.fetchall()

        has_more = len(raw_rows) > pagesize
        trimmed = raw_rows[:pagesize]
        total_count: int | None = None
        if include_total:
            cnt_stmt = _compose_sql("SELECT COUNT(*) FROM phages p", join_sql, "WHERE", where_sql)
            cur.execute(cnt_stmt, params)
            total_count = int(cur.fetchone()[0])

    enriched = _enrich_phage_rows(conn, trimmed, ds_by_id, include_host_ranks=include_host_ranks)
    return {
        "count": total_count if total_count is not None else (offset + len(enriched) + (1 if has_more else 0)),
        "total_exact": include_total,
        "page": page,
        "pagesize": pagesize,
        "has_more": has_more,
        "results": enriched,
    }


def get_phage_detail(
    conn: Any,
    *,
    phage_id: int | None = None,
    accession: str | None = None,
    include_host_ranks: bool = False,
) -> dict[str, Any]:
    ds_by_id, _ = _dataset_maps(conn)
    rows: list[Any] = []
    with conn.cursor() as cur:
        if phage_id is not None:
            cur.execute(_compose_sql(_PHAGE_SELECT_PREFIX, "WHERE p.id = %s"), (int(phage_id),))
            r = cur.fetchone()
            if r is not None:
                rows = [r]
        elif accession:
            acc = str(accession).strip()
            cur.execute(
                _compose_sql(_PHAGE_SELECT_PREFIX, 'WHERE p."Acession_ID" = %s ORDER BY p.id ASC LIMIT 1'),
                (acc,),
            )
            row_try = cur.fetchone()
            if row_try is None and "." not in acc:
                cur.execute(
                    _compose_sql(_PHAGE_SELECT_PREFIX, 'WHERE p."Acession_ID" LIKE %s ORDER BY p.id ASC LIMIT 1'),
                    (acc + ".%",),
                )
                row_try = cur.fetchone()
            if row_try is not None:
                rows = [row_try]
        else:
            raise ServiceQueryError("invalid_parameter", "Provide phage_id or accession.")

        if not rows:
            raise ServiceQueryError("not_found", "Phage was not found.", 404)

        item = _enrich_phage_rows(conn, rows, ds_by_id, include_host_ranks=include_host_ranks)[0]
        sfx = protein_suffix(rows[0][2]) or protein_suffix(item.get("dataset"))
        if sfx and sfx in _PROTEIN_COUNT_BY_PHAGE_SQL:
            cur.execute(_PROTEIN_COUNT_BY_PHAGE_SQL[sfx], (item["Acession_ID"],))
            item["protein_count"] = int(cur.fetchone()[0])
        else:
            item["protein_count"] = 0
    return item


def aggregate_phages(
    conn: Any,
    *,
    group_by: str,
    filters: dict[str, Any] | None = None,
    overview: dict[str, Any] | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    expr = GROUP_BY_EXPR.get(group_by)
    if expr is None:
        raise ServiceQueryError(
            "invalid_parameter",
            f"group_by must be one of {', '.join(sorted(GROUP_BY_EXPR))}.",
        )
    limit = max(1, min(int(limit), 100))
    filters = filters or {}

    if not filters and overview:
        if group_by == "completeness" and "completeness" in overview:
            items = [{"key": k, "count": v} for k, v in sorted(overview["completeness"].items(), key=lambda x: -x[1])]
            return {"group_by": group_by, "source": "overview_snapshot", "groups": items[:limit]}
        if group_by == "lifestyle" and "lifestyle" in overview:
            items = [{"key": k, "count": v} for k, v in sorted(overview["lifestyle"].items(), key=lambda x: -x[1])]
            return {"group_by": group_by, "source": "overview_snapshot", "groups": items[:limit]}
        if group_by == "dataset" and "annotated_by_source" in overview:
            items = [{"key": k, "count": v} for k, v in sorted(overview["annotated_by_source"].items(), key=lambda x: -x[1])]
            return {"group_by": group_by, "source": "overview_snapshot", "groups": items[:limit]}

    ds_by_id, ds_by_lower = _dataset_maps(conn)
    if filters:
        joins, params, clauses = _build_phage_where(filters, ds_by_lower)
    else:
        joins, params, clauses = [], [], ["p.is_mapped = FALSE"]

    if group_by == "lifestyle" and not any("phage_lifestyle" in j for j in joins):
        joins.append("JOIN phage_lifestyle pl ON pl.phage_id = p.id")
        clauses.append("p.is_mapped = FALSE")
    if group_by.startswith("host_") and not any("phage_hosts" in j for j in joins):
        joins.append("JOIN phage_hosts ph ON ph.phage_id = p.id")

    clauses.append(_compose_sql(expr, "IS NOT NULL"))
    stmt = _compose_sql(
        "SELECT",
        expr,
        "AS grp, COUNT(*) AS cnt, MIN(p.length), ROUND(AVG(p.length))::bigint, MAX(p.length) FROM phages p",
        " ".join(joins),
        "WHERE",
        " AND ".join(clauses),
        "GROUP BY",
        expr,
        "ORDER BY cnt DESC LIMIT %s",
    )
    with conn.cursor() as cur:
        cur.execute(stmt, [*params, limit])
        rows = cur.fetchall()

    groups: list[dict[str, Any]] = []
    for grp, cnt, min_len, avg_len, max_len in rows:
        key_val = ds_by_id.get(int(grp), str(grp)) if group_by == "dataset" and grp is not None else (str(grp) or "-")
        if key_val == "":
            continue
        groups.append(
            {
                "key": key_val,
                "count": int(cnt),
                "min_length": int(min_len) if min_len is not None else None,
                "avg_length": int(avg_len) if avg_len is not None else None,
                "max_length": int(max_len) if max_len is not None else None,
            }
        )
    return {"group_by": group_by, "source": "postgresql_live", "groups": groups}


def _format_protein_row(
    row: tuple[Any, ...],
    *,
    include_sequence: bool = False,
    full_sequence: bool = False,
) -> dict[str, Any]:
    pid, pacc, start, stop, strand, product, func_cls, ds, seq = row
    out: dict[str, Any] = {
        "Protein_id": pid,
        "Phage_Acession_ID": pacc,
        "Start_location": start,
        "Stop_location": stop,
        "Strand": strand,
        "Protein_product": product,
        "Protein_function_classification": func_cls,
        "dataset": ds,
    }
    if include_sequence or full_sequence:
        raw_seq = seq or ""
        if full_sequence:
            out["sequence"] = raw_seq
            out["truncated"] = False
        else:
            out["sequence"] = raw_seq[:2000]
            out["truncated"] = len(raw_seq) > 2000
    return out


def query_phage_proteins(
    conn: Any,
    *,
    phage_id: int | None = None,
    accession: str | None = None,
    dataset: str | None = None,
    product_query: str | None = None,
    function_class: str | None = None,
    include_sequence: bool = False,
    full_sequence: bool = False,
    page: int = 1,
    pagesize: int = 50,
    max_pagesize: int = 1000,
) -> dict[str, Any]:
    if phage_id is not None or not dataset:
        phage = get_phage_detail(conn, phage_id=phage_id, accession=accession)
        acc = phage["Acession_ID"]
        ds = phage["dataset"]
    else:
        acc = str(accession or "").strip()
        ds = dataset
    if not acc:
        raise ServiceQueryError("invalid_parameter", "Provide phage_id or accession.")
    sfx = protein_suffix(ds)
    if not sfx:
        raise ServiceQueryError("not_found", f"No protein table mapped for dataset {ds!r}.", 404)

    page = max(1, int(page))
    pagesize = max(1, min(int(pagesize), max_pagesize))
    offset = (page - 1) * pagesize

    clauses = ['"Phage_Acession_ID" = %s']
    params: list[Any] = [acc]
    if product_query:
        clauses.append('"Protein_product" ILIKE %s')
        params.append(f"%{str(product_query).strip()}%")
    if function_class:
        clauses.append('"Protein_function_classification" ILIKE %s')
        params.append(f"%{str(function_class).strip()}%")

    where_sql = " AND ".join(clauses)
    want_seq = bool(include_sequence or full_sequence)
    cnt_stmt = _compose_sql(_PROTEIN_COUNT_HEAD[sfx], "WHERE", where_sql)
    sel_stmt = _compose_sql(
        _PROTEIN_SELECT_HEAD[(sfx, want_seq)],
        "WHERE",
        where_sql,
        'ORDER BY "Start_location" ASC NULLS LAST, id ASC LIMIT %s OFFSET %s',
    )
    with conn.cursor() as cur:
        cur.execute(cnt_stmt, params)
        total = int(cur.fetchone()[0])
        cur.execute(sel_stmt, [*params, pagesize, offset])
        rows = [
            _format_protein_row(r, include_sequence=include_sequence, full_sequence=full_sequence)
            for r in cur.fetchall()
        ]
    return {
        "count": total,
        "page": page,
        "pagesize": pagesize,
        "phage_accession": acc,
        "dataset": ds,
        "results": rows,
    }


def fetch_proteins_by_ids(
    conn: Any,
    protein_ids: list[str],
    *,
    dataset_hint: str | None = None,
    include_sequence: bool = False,
    full_sequence: bool = False,
) -> list[dict[str, Any]]:
    if not protein_ids or len(protein_ids) > 500:
        raise ServiceQueryError("invalid_parameter", "protein_ids must contain 1..500 IDs.")
    clean = [str(p).strip() for p in protein_ids if str(p).strip()]
    by_sfx: dict[str, list[str]] = {}
    want_seq = bool(include_sequence or full_sequence)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT protein_id, dataset FROM phage_protein_family_member WHERE protein_id = ANY(%s)",
            (clean,),
        )
        mapped = {str(pid): str(ds) for pid, ds in cur.fetchall()}
        for pid in clean:
            ds = mapped.get(pid) or dataset_hint
            sfx = protein_suffix(ds) if ds else None
            if sfx:
                by_sfx.setdefault(sfx, []).append(pid)

        found: dict[str, dict[str, Any]] = {}
        for sfx, ids in by_sfx.items():
            stmt = _compose_sql(_PROTEIN_SELECT_HEAD[(sfx, want_seq)], 'WHERE "Protein_id" = ANY(%s)')
            cur.execute(stmt, (ids,))
            for r in cur.fetchall():
                item = _format_protein_row(
                    r, include_sequence=include_sequence, full_sequence=full_sequence
                )
                found[str(item["Protein_id"])] = item
    return [found[pid] for pid in clean if pid in found]


def search_protein_families(
    conn: Any,
    *,
    protein_id: str | None = None,
    family_id: str | None = None,
    keyword: str | None = None,
    dominant_class: str | None = None,
    min_members: int | None = None,
    min_datasets: int | None = None,
    non_singleton_only: bool = True,
    page: int = 1,
    pagesize: int = 20,
) -> dict[str, Any]:
    page = max(1, int(page))
    pagesize = max(1, min(int(pagesize), 100))
    offset = (page - 1) * pagesize

    with conn.cursor() as cur:
        if protein_id:
            cur.execute(
                "SELECT family_id, subfamily_id FROM phage_protein_family_member WHERE protein_id = %s",
                (str(protein_id).strip(),),
            )
            m = cur.fetchone()
            if not m:
                raise ServiceQueryError(
                    "not_found",
                    "This protein is not in the clustering, or the family id does not exist.",
                    404,
                )
            family_id = str(m[0])

        if family_id and not keyword and not dominant_class:
            cur.execute(
                "SELECT family_id, member_count, db_member_count, dominant_class, "
                "consensus_product, subfamily_count, representative_protein_id, dataset_count "
                "FROM phage_protein_family WHERE family_id = %s",
                (str(family_id).strip(),),
            )
            row = cur.fetchone()
            if not row:
                raise ServiceQueryError("not_found", "Protein family was not found.", 404)
            return {
                "family_id": row[0],
                "member_count": row[1],
                "db_member_count": row[2],
                "dominant_class": row[3] or "",
                "consensus_product": row[4] or "",
                "subfamily_count": row[5],
                "representative_protein_id": row[6] or "",
                "dataset_count": row[7],
            }

        clauses: list[str] = []
        params: list[Any] = []
        if non_singleton_only:
            clauses.append("NOT is_singleton")
        if keyword:
            clauses.append("consensus_product ILIKE %s")
            params.append(f"%{str(keyword).strip()}%")
        if dominant_class:
            clauses.append("dominant_class ILIKE %s")
            params.append(f"%{str(dominant_class).strip()}%")
        if min_members is not None:
            clauses.append("member_count >= %s")
            params.append(int(min_members))
        if min_datasets is not None:
            clauses.append("dataset_count >= %s")
            params.append(int(min_datasets))
        if not clauses:
            clauses.append("NOT is_singleton")

        stmt = _compose_sql(
            "SELECT family_id, member_count, db_member_count, dominant_class,",
            "consensus_product, subfamily_count, representative_protein_id, dataset_count",
            "FROM phage_protein_family WHERE",
            " AND ".join(clauses),
            "ORDER BY member_count DESC, family_id DESC LIMIT %s OFFSET %s",
        )
        cur.execute(stmt, [*params, pagesize + 1, offset])
        raw = cur.fetchall()
        has_more = len(raw) > pagesize
        results = [
            {
                "family_id": r[0],
                "member_count": r[1],
                "db_member_count": r[2],
                "dominant_class": r[3] or "",
                "consensus_product": r[4] or "",
                "subfamily_count": r[5],
                "representative_protein_id": r[6] or "",
                "dataset_count": r[7],
            }
            for r in raw[:pagesize]
        ]
    return {"page": page, "pagesize": pagesize, "has_more": has_more, "results": results}


def list_family_members(
    conn: Any,
    *,
    family_id: str,
    subfamily_id: str | None = None,
    dataset: str | None = None,
    include_sequence: bool = False,
    full_sequence: bool = False,
    page: int = 1,
    pagesize: int = 20,
    max_pagesize: int = 200,
) -> dict[str, Any]:
    fid = str(family_id or "").strip()
    if not fid:
        raise ServiceQueryError("invalid_parameter", "family_id is required.")
    page = max(1, int(page))
    pagesize = max(1, min(int(pagesize), max_pagesize))
    offset = (page - 1) * pagesize

    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM phage_protein_family WHERE family_id = %s", (fid,))
        if not cur.fetchone():
            raise ServiceQueryError("not_found", "Protein family was not found.", 404)

        clauses = ["family_id = %s"]
        params: list[Any] = [fid]
        if subfamily_id:
            clauses.append("subfamily_id = %s")
            params.append(str(subfamily_id).strip())
        if dataset:
            canon = canonical_dataset_name(dataset) or str(dataset).strip()
            clauses.append("dataset = %s")
            params.append(canon)
        where_sql = " AND ".join(clauses)
        cnt_stmt = _compose_sql("SELECT COUNT(*) FROM phage_protein_family_member WHERE", where_sql)
        sel_stmt = _compose_sql(
            "SELECT protein_id, family_id, subfamily_id, dataset, phage_accession_id,",
            "protein_product, protein_function_class FROM phage_protein_family_member WHERE",
            where_sql,
            "ORDER BY protein_id ASC LIMIT %s OFFSET %s",
        )
        cur.execute(cnt_stmt, params)
        total = int(cur.fetchone()[0])
        cur.execute(sel_stmt, [*params, pagesize, offset])
        members = [
            {
                "protein_id": r[0],
                "family_id": r[1],
                "subfamily_id": r[2] or "",
                "dataset": r[3] or "",
                "phage_accession_id": r[4] or "",
                "protein_product": r[5] or "",
                "protein_function_class": r[6] or "",
            }
            for r in cur.fetchall()
        ]

    if members and (include_sequence or full_sequence):
        seq_rows = fetch_proteins_by_ids(
            conn,
            [m["protein_id"] for m in members],
            include_sequence=include_sequence,
            full_sequence=full_sequence,
        )
        seq_map = {r["Protein_id"]: r for r in seq_rows}
        for m in members:
            sr = seq_map.get(m["protein_id"])
            if sr:
                m["sequence"] = sr.get("sequence", "")
                m["truncated"] = sr.get("truncated", False)

    return {"count": total, "page": page, "pagesize": pagesize, "results": members}


def query_hosts(
    conn: Any,
    *,
    rank: str,
    name: str | None = None,
    page: int = 1,
    pagesize: int = 20,
) -> dict[str, Any]:
    if rank not in HOST_TABLE_EQ_CLAUSE:
        raise ServiceQueryError(
            "invalid_parameter",
            f"rank must be one of {', '.join(HOST_TABLE_EQ_CLAUSE)}.",
        )
    page = max(1, int(page))
    pagesize = max(1, min(int(pagesize), 100))
    offset = (page - 1) * pagesize
    params: list[Any] = []
    if name:
        where_sql = HOST_TABLE_EQ_CLAUSE[rank]
        params.append(str(name).strip())
    else:
        where_sql = HOST_TABLE_NONEMPTY_CLAUSE[rank]

    cnt_stmt = _compose_sql("SELECT COUNT(*) FROM phage_hosts WHERE", where_sql)
    sel_stmt = _compose_sql(
        'SELECT id, accesion_id, host, host_source, "Phylum", "Class", "Order", "Family", "Genus", "Species"',
        "FROM phage_hosts WHERE",
        where_sql,
        "ORDER BY id ASC LIMIT %s OFFSET %s",
    )
    with conn.cursor() as cur:
        cur.execute(cnt_stmt, params)
        total = int(cur.fetchone()[0])
        cur.execute(sel_stmt, [*params, pagesize, offset])
        results = [
            {
                "id": int(r[0]),
                "accesion_id": r[1],
                "host": r[2],
                "host_source": r[3],
                "Phylum": r[4],
                "Class": r[5],
                "Order": r[6],
                "Family": r[7],
                "Genus": r[8],
                "Species": r[9],
            }
            for r in cur.fetchall()
        ]
    return {"count": total, "page": page, "pagesize": pagesize, "results": results}


def _scan_terminator_tsv(
    csv_download_root: Path | None,
    dataset: str | None,
    accession: str,
    page: int,
    pagesize: int,
) -> dict[str, Any] | None:
    if csv_download_root is None or not dataset:
        return None
    term_dir = csv_download_root / "Transcription_terminators_meta_data"
    if not term_dir.is_dir():
        return None
    ds_low = dataset.lower()
    target_file: Path | None = None
    for entry in os.scandir(term_dir):
        if entry.is_file() and entry.name.lower().startswith(ds_low + "_"):
            target_file = contained_path(Path(entry.path), csv_download_root)
            break
    if target_file is None:
        return None
    matches: list[dict[str, Any]] = []
    with target_file.open("r", encoding="utf-8", errors="replace") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        for row in reader:
            pid = row.get("Phage_ID") or row.get("Phage_id") or ""
            if pid == accession:
                matches.append(
                    {
                        "Phage_id": pid,
                        "Terminator_Id": row.get("Terminator_Id") or row.get("Terminator_ID") or "",
                        "Start": row.get("Start") or "",
                        "Stop": row.get("Stop") or "",
                        "Sense": row.get("Sense") or row.get("Strand") or "",
                        "Confidence": row.get("Confidence") or "",
                    }
                )
    offset = (page - 1) * pagesize
    return {
        "count": len(matches),
        "page": page,
        "pagesize": pagesize,
        "results": matches[offset : offset + pagesize],
    }


def query_features(
    conn: Any,
    *,
    feature_type: str,
    phage_id: int | None = None,
    accession: str | None = None,
    dataset: str | None = None,
    keyword: str | None = None,
    csv_download_root: Path | None = None,
    page: int = 1,
    pagesize: int = 50,
) -> dict[str, Any]:
    ftype = str(feature_type or "").strip().lower()
    if ftype not in FEATURE_TYPES:
        raise ServiceQueryError(
            "invalid_parameter",
            f"type must be one of {', '.join(sorted(FEATURE_TYPES))}.",
        )
    page = max(1, int(page))
    pagesize = max(1, min(int(pagesize), 200))
    offset = (page - 1) * pagesize

    if phage_id is not None or accession:
        resolved = get_phage_detail(conn, phage_id=phage_id, accession=accession)
        accession = resolved["Acession_ID"]
        dataset = resolved["dataset"]
        phage_id = resolved["id"]

    if not accession and not keyword and not dataset:
        raise ServiceQueryError(
            "missing_filter",
            "Provide phage_id/accession, keyword, or dataset for feature query.",
        )

    if ftype == "terminator" and accession:
        fast_tsv = _scan_terminator_tsv(csv_download_root, dataset, accession, page, pagesize)
        if fast_tsv is not None:
            return fast_tsv

    with conn.cursor() as cur:
        if ftype == "trna":
            clauses: list[str] = []
            params: list[Any] = []
            if phage_id is not None:
                clauses.append("phage_id = %s")
                params.append(int(phage_id))
            elif accession:
                clauses.append("phage_accid = %s")
                params.append(accession)
            if keyword:
                clauses.append("trnatype ILIKE %s")
                params.append(f"%{str(keyword).strip()}%")
            where_sql = " AND ".join(clauses)
            cur.execute(_compose_sql("SELECT COUNT(*) FROM phage_trna WHERE", where_sql), params)
            total = int(cur.fetchone()[0])
            cur.execute(
                _compose_sql(
                    'SELECT trna_id, trnatype, start, "end", strand, length, phage_accid',
                    "FROM phage_trna WHERE",
                    where_sql,
                    "ORDER BY id ASC LIMIT %s OFFSET %s",
                ),
                [*params, pagesize, offset],
            )
            rows = [
                {
                    "trna_id": r[0],
                    "trnatype": r[1],
                    "start": r[2],
                    "end": r[3],
                    "strand": r[4],
                    "length": r[5],
                    "phage_accid": r[6],
                }
                for r in cur.fetchall()
            ]
            return {"count": total, "page": page, "pagesize": pagesize, "results": rows}

        if ftype == "crispr":
            if not accession:
                raise ServiceQueryError("invalid_parameter", "crispr requires phage_id or accession.")
            cur.execute('SELECT COUNT(*) FROM phage_crispr WHERE "Phage_id" = %s', (accession,))
            total = int(cur.fetchone()[0])
            cur.execute(
                'SELECT "Phage_id", "CRISPR_Id", "CRISPR_Start", "CRISPR_End", "CRISPR_Length", "Evidence_Level" '
                'FROM phage_crispr WHERE "Phage_id" = %s ORDER BY id ASC LIMIT %s OFFSET %s',
                (accession, pagesize, offset),
            )
            rows = [
                {
                    "Phage_id": r[0],
                    "CRISPR_Id": r[1],
                    "CRISPR_Start": r[2],
                    "CRISPR_End": r[3],
                    "CRISPR_Length": r[4],
                    "Evidence_Level": r[5],
                }
                for r in cur.fetchall()
            ]
            return {"count": total, "page": page, "pagesize": pagesize, "results": rows}

        if ftype == "anticrispr":
            clauses, params = [], []
            if accession:
                clauses.append('"Phage_Acession_ID" = %s')
                params.append(accession)
            if dataset and not accession:
                canon = canonical_dataset_name(dataset) or str(dataset).strip()
                clauses.append("dataset = %s")
                params.append(canon)
            if keyword:
                clauses.append('"Source" ILIKE %s')
                params.append(f"%{str(keyword).strip()}%")
            where_sql = " AND ".join(clauses)
            cur.execute(_compose_sql("SELECT COUNT(*) FROM phage_protein_anticrispr WHERE", where_sql), params)
            total = int(cur.fetchone()[0])
            cur.execute(
                _compose_sql(
                    'SELECT "Protein_id", "Source", "Phage_Acession_ID", dataset',
                    "FROM phage_protein_anticrispr WHERE",
                    where_sql,
                    "ORDER BY id ASC LIMIT %s OFFSET %s",
                ),
                [*params, pagesize, offset],
            )
            rows = [
                {"Protein_id": r[0], "Source": r[1], "Phage_Acession_ID": r[2], "dataset": r[3]}
                for r in cur.fetchall()
            ]
            return {"count": total, "page": page, "pagesize": pagesize, "results": rows}

        if ftype == "terminator":
            if not accession:
                raise ServiceQueryError("invalid_parameter", "terminator requires phage_id or accession.")
            cur.execute('SELECT COUNT(*) FROM phage_terminators WHERE "Phage_id" = %s', (accession,))
            total = int(cur.fetchone()[0])
            cur.execute(
                'SELECT "Phage_id", "Terminator_Id", "Start", "Stop", "Sense", "Confidence" '
                'FROM phage_terminators WHERE "Phage_id" = %s ORDER BY id ASC LIMIT %s OFFSET %s',
                (accession, pagesize, offset),
            )
            rows = [
                {
                    "Phage_id": r[0],
                    "Terminator_Id": r[1],
                    "Start": r[2],
                    "Stop": r[3],
                    "Sense": r[4],
                    "Confidence": r[5],
                }
                for r in cur.fetchall()
            ]
            return {"count": total, "page": page, "pagesize": pagesize, "results": rows}

        if ftype == "virulence":
            clauses, params = [], []
            if accession:
                clauses.append('"Phage_Acession_ID" = %s')
                params.append(accession)
            if dataset and not accession:
                canon = canonical_dataset_name(dataset) or str(dataset).strip()
                clauses.append("dataset = %s")
                params.append(canon)
            if keyword:
                clauses.append('"Aligned_Protein_in_VFDB" ILIKE %s')
                params.append(f"%{str(keyword).strip()}%")
            where_sql = " AND ".join(clauses)
            cur.execute(_compose_sql("SELECT COUNT(*) FROM phage_virulent_factor WHERE", where_sql), params)
            total = int(cur.fetchone()[0])
            cur.execute(
                _compose_sql(
                    'SELECT "Protein_id", "Aligned_Protein_in_VFDB", "Phage_Acession_ID", dataset',
                    "FROM phage_virulent_factor WHERE",
                    where_sql,
                    "ORDER BY id ASC LIMIT %s OFFSET %s",
                ),
                [*params, pagesize, offset],
            )
            rows = [
                {
                    "Protein_id": r[0],
                    "Aligned_Protein_in_VFDB": r[1],
                    "Phage_Acession_ID": r[2],
                    "dataset": r[3],
                }
                for r in cur.fetchall()
            ]
            return {"count": total, "page": page, "pagesize": pagesize, "results": rows}

        if ftype == "amr":
            clauses, params = [], []
            if accession:
                clauses.append('"Phage_Acession_ID" = %s')
                params.append(accession)
            if dataset and not accession:
                canon = canonical_dataset_name(dataset) or str(dataset).strip()
                clauses.append("dataset = %s")
                params.append(canon)
            if keyword:
                clauses.append('"Aligned_Protein_in_CARD" ILIKE %s')
                params.append(f"%{str(keyword).strip()}%")
            where_sql = " AND ".join(clauses)
            cur.execute(
                _compose_sql("SELECT COUNT(*) FROM phage_antimicrobial_resistance_gene WHERE", where_sql),
                params,
            )
            total = int(cur.fetchone()[0])
            cur.execute(
                _compose_sql(
                    'SELECT "Protein_id", "Aligned_Protein_in_CARD", "Phage_Acession_ID", dataset',
                    "FROM phage_antimicrobial_resistance_gene WHERE",
                    where_sql,
                    "ORDER BY id ASC LIMIT %s OFFSET %s",
                ),
                [*params, pagesize, offset],
            )
            rows = [
                {
                    "Protein_id": r[0],
                    "Aligned_Protein_in_CARD": r[1],
                    "Phage_Acession_ID": r[2],
                    "dataset": r[3],
                }
                for r in cur.fetchall()
            ]
            return {"count": total, "page": page, "pagesize": pagesize, "results": rows}

        if not accession or not dataset:
            raise ServiceQueryError("invalid_parameter", "transmembrane requires phage_id or accession.")
        sfx = protein_suffix(dataset)
        if not sfx:
            raise ServiceQueryError("not_found", f"No TMHMM table for dataset {dataset!r}.", 404)
        cur.execute(_TMHMM_COUNT_SQL[sfx], (accession,))
        total = int(cur.fetchone()[0])
        cur.execute(_TMHMM_SELECT_SQL[sfx], (accession, pagesize, offset))
        rows = [
            {"Protein_id": r[0], "Phage_Acession_ID": r[1], "predictedTMHsNumber": r[2]}
            for r in cur.fetchall()
        ]
        return {"count": total, "page": page, "pagesize": pagesize, "results": rows}


def resolve_structures(
    conn: Any,
    protein_ids: list[str],
    *,
    pdb_host_prefix: str = "/data4/back_up_platform/esmfold_pdb/",
) -> list[dict[str, Any]]:
    if not protein_ids or len(protein_ids) > 50:
        raise ServiceQueryError("invalid_parameter", "protein_ids must contain 1..50 identifiers.")
    clean = [str(p).strip() for p in protein_ids if str(p).strip()]
    with conn.cursor() as cur:
        cur.execute(
            "SELECT m.protein_id, m.asset_id, m.confidence_label, m.match_type, "
            "m.identity, m.qcov, m.tcov, "
            "a.source, a.method, a.plddt_mean, a.resolution_a, a.file_format, a.file_path "
            "FROM protein_structure_map m "
            "JOIN structure_asset a ON a.asset_id = m.asset_id "
            "WHERE m.is_primary = TRUE AND m.protein_id = ANY(%s)",
            (clean,),
        )
        found: dict[str, dict[str, Any]] = {}
        for r in cur.fetchall():
            pid = str(r[0])
            raw_fp = str(r[12] or "")
            rel_fp = ""
            if pdb_host_prefix and raw_fp.startswith(pdb_host_prefix):
                rel_fp = raw_fp[len(pdb_host_prefix) :].lstrip("/")
            found[pid] = {
                "protein_id": pid,
                "available": True,
                "asset_id": r[1],
                "confidence_label": r[2] or "",
                "match_type": r[3] or "",
                "identity": r[4],
                "qcov": r[5],
                "tcov": r[6],
                "source": r[7] or "",
                "method": r[8] or "",
                "plddt_mean": r[9],
                "resolution_a": r[10],
                "file_format": r[11] or "pdb",
                "pdb_rel_path": rel_fp,
            }
    return [found.get(pid, {"protein_id": pid, "available": False}) for pid in clean]


def read_phage_sequence_preview(
    conn: Any,
    *,
    phage_id: int,
    fasta_root: Path | None,
    fasta_ext_root: Path | None = None,
    max_bases: int = 20000,
) -> dict[str, Any]:
    if fasta_root is None and fasta_ext_root is None:
        raise ServiceQueryError("unavailable", "PHAGE_FASTA_ROOT is not configured.", 503)
    phage = get_phage_detail(conn, phage_id=phage_id)
    ds_name = phage.get("dataset") or ""
    acc = phage["Acession_ID"]
    filename = safe_component(acc) + ".fasta"
    dataset_key = safe_component(ds_name)
    candidates: list[tuple[Path, Path]] = []
    if fasta_root is not None:
        candidates.append((fasta_root, fasta_root / dataset_key))
    if fasta_ext_root is not None:
        # Datasets whose catalog directory is a symlink to storage outside the
        # main tree (620b: MetaVR -> /data1/.../phage_fasta_ext/MetaVR).
        candidates.append((fasta_ext_root, fasta_ext_root / dataset_key))
    path: Path | None = None
    for root, directory in candidates:
        try:
            candidate = contained_path(directory / filename, root)
        except ServiceQueryError:
            continue
        if candidate.is_file():
            path = candidate
            break
    if path is None:
        raise ServiceQueryError("not_found", "Sequence file was not found.", 404)
    limit = max(1, min(int(max_bases), MAX_SEQUENCE_BASES))
    header = ""
    chunks: list[str] = []
    remaining = limit
    truncated = False
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.startswith(">"):
                if header:
                    truncated = True
                    break
                header = line.strip()
                continue
            text = "".join(line.split())
            if len(text) <= remaining:
                chunks.append(text)
                remaining -= len(text)
            else:
                chunks.append(text[:remaining])
                truncated = True
                break
    return {
        "Acession_ID": acc,
        "dataset": ds_name,
        "header": header,
        "sequence": "".join(chunks),
        "truncated": truncated,
    }
