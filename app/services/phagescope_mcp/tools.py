"""Phase-1 read-only PhageScope MCP tools.

Each tool is a research task over the existing PhageScope data service
(``tool_box/tools_impl/phagescope_data/client.py``), never a raw route or SQL.
Results share one envelope: ``data``, ``provenance``, ``limits`` and ``notes``.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from tool_box.tools_impl.phagescope_data import client
from tool_box.tools_impl.phagescope_data.config import PhageScopeDataError

SOURCE = "PhageScope via the GAgent read-only data service"
UNKNOWN_NOTE = "A missing or null field means the value is unknown, not zero or absent."
OVERVIEW_TTL_SECONDS = 600

_FILTER_PROPERTIES: dict[str, Any] = {
    "accession": {"type": "string", "description": "Exact phage accession."},
    "accessions": {"type": "array", "items": {"type": "string"}, "maxItems": 200},
    "phage_ids": {"type": "array", "items": {"type": "integer"}, "maxItems": 200},
    "dataset": {"type": "string", "description": "Source dataset name, e.g. RefSeq, GenBank, MGV (see catalog_overview)."},
    "datasets": {"type": "array", "items": {"type": "string"}, "maxItems": 30},
    "completeness": {"type": "string", "description": "Genome completeness label, e.g. Complete, High-quality."},
    "lifestyle": {"type": "string", "description": "Predicted lifestyle, e.g. virulent or temperate."},
    "taxonomy": {"type": "string", "description": "Phage taxonomy name."},
    "taxonomy_match": {"type": "string", "description": "How to match taxonomy (service default when omitted)."},
    "host": {"type": "string", "description": "Host name, e.g. Salmonella or Escherichia coli."},
    "host_match": {"type": "string", "description": "How to match host (service default when omitted)."},
    "host_rank": {"type": "string", "description": "Host rank used with host_name, e.g. genus or species."},
    "host_name": {"type": "string"},
    "cluster": {"type": "string"},
    "subcluster": {"type": "string"},
    "length_min": {"type": "integer", "minimum": 0},
    "length_max": {"type": "integer", "minimum": 0},
    "gc_min": {"type": "number", "minimum": 0, "maximum": 100},
    "gc_max": {"type": "number", "minimum": 0, "maximum": 100},
    "is_mapped": {"type": "boolean"},
    "is_rep": {"type": "boolean", "description": "Only cluster representatives."},
    "amr_free": {"type": "boolean", "description": "No annotated antimicrobial-resistance genes (columnar layer)."},
    "virulence_free": {"type": "boolean", "description": "No annotated virulence factors (columnar layer)."},
    "min_trna": {"type": "integer", "minimum": 0},
    "has_crispr": {"type": "boolean"},
    "has_anticrispr": {"type": "boolean"},
}

FILTERS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "Phage cohort filters; all given filters are combined with AND.",
    "properties": _FILTER_PROPERTIES,
    "additionalProperties": False,
}

GROUP_BY_DIMENSIONS = (
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
)
FEATURE_TYPES = ("trna", "crispr", "anticrispr", "terminator", "virulence", "amr", "transmembrane")
_PHAGE_REF = {
    "phage_id": {"type": "integer", "minimum": 1, "description": "PhageScope numeric phage id."},
    "accession": {"type": "string", "description": "Phage accession; used when phage_id is omitted."},
}


class ToolInputError(ValueError):
    def __init__(self, message: str, code: str = "invalid_arguments") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ToolSpec:
    name: str
    title: str
    description: str
    input_schema: dict[str, Any]
    run: Callable[[dict[str, Any]], Awaitable[tuple[Any, list[str]]]]


def _obj(properties: dict[str, Any], required: Optional[list[str]] = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": properties, "additionalProperties": False}
    if required:
        schema["required"] = required
    return schema


def _page_props(max_pagesize: int, default: int = 20) -> dict[str, Any]:
    return {
        "page": {"type": "integer", "minimum": 1, "default": 1},
        "pagesize": {"type": "integer", "minimum": 1, "maximum": max_pagesize, "default": default},
    }


def _clamp(value: Any, low: int, high: int, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _phage_ref(args: dict[str, Any]) -> dict[str, Any]:
    if args.get("phage_id") is not None:
        return {"phage_id": int(args["phage_id"])}
    if args.get("accession"):
        return {"accession": str(args["accession"]).strip()}
    raise ToolInputError("Provide phage_id or accession.")


def _filters(args: dict[str, Any], *, required: bool) -> dict[str, Any]:
    filters = args.get("filters") or {}
    if not isinstance(filters, dict):
        raise ToolInputError("filters must be an object.")
    unknown = sorted(set(filters) - set(_FILTER_PROPERTIES))
    if unknown:
        raise ToolInputError(f"Unknown filter keys: {', '.join(unknown)}.")
    cleaned = {k: v for k, v in filters.items() if v is not None and v != "" and v != []}
    if required and not cleaned:
        raise ToolInputError("At least one filter is required; use catalog_overview for whole-catalog numbers.")
    return cleaned


async def _post(path: str, body: dict[str, Any]) -> dict[str, Any]:
    return await client.call_service(path, method="POST", body=body)


async def _get(path: str, query: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    return await client.call_service(path, method="GET", query=query)


# ----------------------------------------------------------------- handlers


async def _catalog_overview(args: dict[str, Any]) -> tuple[Any, list[str]]:
    overview = await _get("stats/overview/")
    datasets = await _get("datasets/")
    return {"overview": overview, "datasets": datasets.get("results", datasets)}, [
        "Counts are from the data service's precomputed overview snapshot."
    ]


async def _search_phages(args: dict[str, Any]) -> tuple[Any, list[str]]:
    body = {
        "filters": _filters(args, required=True),
        "page": _clamp(args.get("page"), 1, 10_000, 1),
        "pagesize": _clamp(args.get("pagesize"), 1, 100, 20),
        "include_total": bool(args.get("include_total", False)),
        "sort_by": str(args.get("sort_by") or "id"),
    }
    data = await _post("v2/phages/query/", body)
    notes = []
    if not body["include_total"]:
        notes.append("count is a lower bound unless include_total=true (exact totals are slower).")
    return data, notes


async def _cohort_report(args: dict[str, Any]) -> tuple[Any, list[str]]:
    body = {
        "filters": _filters(args, required=True),
        "sample_limit": _clamp(args.get("sample_limit"), 1, 20, 5),
        "sort_by": str(args.get("sort_by") or "id"),
    }
    return await _post("v2/phages/cohort_report/", body), []


async def _aggregate_phages(args: dict[str, Any]) -> tuple[Any, list[str]]:
    group_by = args.get("group_by")
    dims = group_by if isinstance(group_by, list) else [group_by]
    if not dims or not 1 <= len(dims) <= 3 or any(d not in GROUP_BY_DIMENSIONS for d in dims):
        raise ToolInputError(f"group_by must be 1-3 of: {', '.join(GROUP_BY_DIMENSIONS)}.")
    body = {
        "group_by": dims if len(dims) > 1 else dims[0],
        "filters": _filters(args, required=False),
        "limit": _clamp(args.get("limit"), 1, 200, 50),
    }
    return await _post("v2/phages/aggregate/", body), [
        "Groups with an unknown value are reported as their own bucket, not folded into zero."
    ]


async def _get_phage(args: dict[str, Any]) -> tuple[Any, list[str]]:
    return await _post("v2/phages/detail/", _phage_ref(args)), []


async def _get_sequence(args: dict[str, Any]) -> tuple[Any, list[str]]:
    ref = _phage_ref(args)
    phage_id = ref.get("phage_id")
    if phage_id is None:
        detail = await _post("v2/phages/detail/", ref)
        phage_id = detail.get("id") or detail.get("phage_id")
        if not phage_id:
            raise ToolInputError("Accession did not resolve to a phage id.", code="not_found")
    max_bases = _clamp(args.get("max_bases"), 100, 200_000, 20_000)
    data = await _get(f"phages/{int(phage_id)}/sequence/", {"max_bases": max_bases})
    return data, [f"Sequence is capped at max_bases={max_bases}; check the truncated flag in data."]


async def _search_proteins(args: dict[str, Any]) -> tuple[Any, list[str]]:
    include_sequence = bool(args.get("include_sequence", False))
    if args.get("protein_ids"):
        ids = args["protein_ids"]
        if not isinstance(ids, list) or not 1 <= len(ids) <= 100:
            raise ToolInputError("protein_ids must be a list of 1-100 ids.")
        body: dict[str, Any] = {"mode": "by_ids", "protein_ids": ids, "include_sequence": include_sequence}
    else:
        body = {
            "mode": "by_phage",
            **_phage_ref(args),
            "include_sequence": include_sequence,
            "page": _clamp(args.get("page"), 1, 10_000, 1),
            "pagesize": _clamp(args.get("pagesize"), 1, 100, 50),
        }
        for key in ("product_query", "function_class"):
            if args.get(key):
                body[key] = str(args[key])
    notes = [] if include_sequence else ["Sequences omitted; set include_sequence=true to include them."]
    return await _post("v2/proteins/query/", body), notes


async def _search_families(args: dict[str, Any]) -> tuple[Any, list[str]]:
    body: dict[str, Any] = {
        "mode": "search_families",
        "page": _clamp(args.get("page"), 1, 10_000, 1),
        "pagesize": _clamp(args.get("pagesize"), 1, 100, 20),
    }
    for key in ("keyword", "protein_id", "family_id", "dominant_class"):
        if args.get(key):
            body[key] = str(args[key])
    if not any(k in body for k in ("keyword", "protein_id", "family_id")):
        raise ToolInputError("Provide keyword, protein_id or family_id.")
    for key in ("min_members", "min_datasets"):
        if args.get(key) is not None:
            body[key] = _clamp(args[key], 1, 10_000_000, 1)
    if args.get("non_singleton_only") is not None:
        body["non_singleton_only"] = bool(args["non_singleton_only"])
    return await _post("v2/proteins/query/", body), []


async def _family_members(args: dict[str, Any]) -> tuple[Any, list[str]]:
    if not args.get("family_id"):
        raise ToolInputError("family_id is required.")
    body: dict[str, Any] = {
        "mode": "family_members",
        "family_id": str(args["family_id"]),
        "include_sequence": bool(args.get("include_sequence", False)),
        "page": _clamp(args.get("page"), 1, 10_000, 1),
        "pagesize": _clamp(args.get("pagesize"), 1, 100, 50),
    }
    for key in ("subfamily_id", "dataset"):
        if args.get(key):
            body[key] = str(args[key])
    return await _post("v2/proteins/query/", body), []


async def _query_features(args: dict[str, Any]) -> tuple[Any, list[str]]:
    feature_type = str(args.get("feature_type") or "").lower()
    if feature_type not in FEATURE_TYPES:
        raise ToolInputError(f"feature_type must be one of: {', '.join(FEATURE_TYPES)}.")
    body: dict[str, Any] = {
        "feature_type": feature_type,
        "page": _clamp(args.get("page"), 1, 10_000, 1),
        "pagesize": _clamp(args.get("pagesize"), 1, 100, 50),
    }
    if args.get("phage_id") is not None or args.get("accession"):
        body.update(_phage_ref(args))
    for key in ("dataset", "keyword"):
        if args.get(key):
            body[key] = str(args[key])
    if not any(k in body for k in ("phage_id", "accession", "dataset", "keyword")):
        raise ToolInputError("Provide phage_id, accession, dataset or keyword to scope the feature query.")
    return await _post("v2/features/query/", body), [
        "No rows for a phage means no annotated feature of this type, which is not proof of absence."
    ]


async def _get_structures(args: dict[str, Any]) -> tuple[Any, list[str]]:
    ids = args.get("protein_ids")
    if not isinstance(ids, list) or not 1 <= len(ids) <= 50:
        raise ToolInputError("protein_ids must be a list of 1-50 ids.")
    return await _post("v2/structures/resolve/", {"protein_ids": ids}), [
        "Structures are ESMFold predictions, not experimental structures."
    ]


TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "catalog_overview",
        "PhageScope catalog overview",
        "Catalog-wide counts (phages, proteins, families, datasets) and the dataset list. Start here.",
        _obj({}),
        _catalog_overview,
    ),
    ToolSpec(
        "search_phages",
        "Search phages",
        "Find phages matching filters (host, taxonomy, lifestyle, dataset, length, GC, AMR/virulence-free...). Paged rows.",
        _obj(
            {
                "filters": FILTERS_SCHEMA,
                **_page_props(100),
                "include_total": {"type": "boolean", "default": False},
                "sort_by": {"type": "string", "enum": ["id", "length_desc", "length_asc"], "default": "id"},
            },
            ["filters"],
        ),
        _search_phages,
    ),
    ToolSpec(
        "cohort_report",
        "Cohort report",
        "Summarise a filtered phage cohort: size, composition breakdowns and a few sample rows.",
        _obj(
            {
                "filters": FILTERS_SCHEMA,
                "sample_limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
                "sort_by": {"type": "string", "enum": ["id", "length_desc", "length_asc"], "default": "id"},
            },
            ["filters"],
        ),
        _cohort_report,
    ),
    ToolSpec(
        "aggregate_phages",
        "Aggregate phages",
        "Count phages grouped by 1-3 dimensions (dataset, lifestyle, host rank...), optionally filtered.",
        _obj(
            {
                "group_by": {
                    "oneOf": [
                        {"type": "string", "enum": list(GROUP_BY_DIMENSIONS)},
                        {
                            "type": "array",
                            "items": {"type": "string", "enum": list(GROUP_BY_DIMENSIONS)},
                            "minItems": 1,
                            "maxItems": 3,
                        },
                    ]
                },
                "filters": FILTERS_SCHEMA,
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
            },
            ["group_by"],
        ),
        _aggregate_phages,
    ),
    ToolSpec(
        "get_phage",
        "Get phage",
        "Full metadata for one phage: taxonomy, host lineage, lifestyle, quality, cluster, dataset.",
        _obj(dict(_PHAGE_REF)),
        _get_phage,
    ),
    ToolSpec(
        "get_sequence",
        "Get genome sequence",
        "Genome nucleotide sequence of one phage, capped at max_bases.",
        _obj({**_PHAGE_REF, "max_bases": {"type": "integer", "minimum": 100, "maximum": 200000, "default": 20000}}),
        _get_sequence,
    ),
    ToolSpec(
        "search_proteins",
        "Search proteins",
        "Proteins of one phage (optionally filtered by product text or function class), or proteins by id.",
        _obj(
            {
                **_PHAGE_REF,
                "protein_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 100},
                "product_query": {"type": "string"},
                "function_class": {"type": "string"},
                "include_sequence": {"type": "boolean", "default": False},
                **_page_props(100, 50),
            }
        ),
        _search_proteins,
    ),
    ToolSpec(
        "search_families",
        "Search protein families",
        "Find protein families by keyword (e.g. endolysin, holin), by member protein_id, or by family_id.",
        _obj(
            {
                "keyword": {"type": "string"},
                "protein_id": {"type": "string"},
                "family_id": {"type": "string"},
                "dominant_class": {"type": "string"},
                "min_members": {"type": "integer", "minimum": 1},
                "min_datasets": {"type": "integer", "minimum": 1},
                "non_singleton_only": {"type": "boolean"},
                **_page_props(100),
            }
        ),
        _search_families,
    ),
    ToolSpec(
        "family_members",
        "Protein family members",
        "Member proteins of one family (optionally one subfamily or dataset). Paged.",
        _obj(
            {
                "family_id": {"type": "string"},
                "subfamily_id": {"type": "string"},
                "dataset": {"type": "string"},
                "include_sequence": {"type": "boolean", "default": False},
                **_page_props(100, 50),
            },
            ["family_id"],
        ),
        _family_members,
    ),
    ToolSpec(
        "query_features",
        "Query genomic features",
        "Annotated features of one type (tRNA, CRISPR, anti-CRISPR, terminator, virulence, AMR, transmembrane).",
        _obj(
            {
                "feature_type": {"type": "string", "enum": list(FEATURE_TYPES)},
                **_PHAGE_REF,
                "dataset": {"type": "string"},
                "keyword": {"type": "string"},
                **_page_props(100, 50),
            },
            ["feature_type"],
        ),
        _query_features,
    ),
    ToolSpec(
        "get_structures",
        "Get protein structures",
        "Resolve predicted (ESMFold) structure availability and links for protein ids.",
        _obj({"protein_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 50}}, ["protein_ids"]),
        _get_structures,
    ),
)

TOOLS_BY_NAME: dict[str, ToolSpec] = {tool.name: tool for tool in TOOLS}

_overview_cache: dict[str, Any] = {"at": 0.0, "value": None}


async def snapshot_info() -> dict[str, Any]:
    """Best-effort data version from the overview snapshot (cached)."""
    now = time.monotonic()
    if _overview_cache["value"] is None or now - _overview_cache["at"] > OVERVIEW_TTL_SECONDS:
        try:
            overview = await _get("stats/overview/")
        except PhageScopeDataError:
            return {}
        keep = {}
        for key in ("snapshot_id", "snapshot_at", "generated_at", "data_version", "computed_at", "version"):
            if overview.get(key) is not None:
                keep[key] = overview[key]
        _overview_cache.update(at=now, value=keep)
    return dict(_overview_cache["value"] or {})


_LIST_KEYS = ("results", "rows", "sample_rows", "groups", "members", "items")


def _main_list(data: Any) -> Optional[list]:
    if isinstance(data, dict):
        for key in _LIST_KEYS:
            if isinstance(data.get(key), list):
                return data[key]
    return None


def build_envelope(tool: str, data: Any, notes: list[str], snapshot: dict[str, Any]) -> dict[str, Any]:
    rows = _main_list(data)
    pagination = {}
    if isinstance(data, dict):
        for key in ("count", "total_exact", "page", "pagesize", "has_more", "next", "previous"):
            if key in data:
                pagination[key] = data[key]
    return {
        "tool": tool,
        "data": data,
        "provenance": {
            "source": SOURCE,
            "snapshot": snapshot or None,
            "retrieved_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        },
        "limits": {"returned_rows": len(rows) if rows is not None else None, "truncated": False, **pagination},
        "notes": [*notes, UNKNOWN_NOTE],
    }


def encode(envelope: dict[str, Any]) -> str:
    return json.dumps(envelope, ensure_ascii=False, default=str, separators=(",", ":"))


def fit_to_budget(envelope: dict[str, Any], max_bytes: int) -> tuple[str, int]:
    """Serialize, dropping trailing rows until the payload fits ``max_bytes``."""
    text = encode(envelope)
    if len(text.encode("utf-8")) <= max_bytes:
        return text, len(text.encode("utf-8"))
    rows = _main_list(envelope.get("data"))
    if rows:
        original = len(rows)
        keep = original
        while keep > 0:
            keep //= 2
            del rows[keep:]
            envelope["limits"].update(returned_rows=keep, truncated=True, rows_before_truncation=original)
            text = encode(envelope)
            if len(text.encode("utf-8")) <= max_bytes:
                envelope["notes"].insert(
                    0, f"Result trimmed to {keep} of {original} rows to stay under {max_bytes} bytes; request a smaller page."
                )
                text = encode(envelope)
                return text, len(text.encode("utf-8"))
    raise ToolInputError(
        f"Result exceeds the {max_bytes}-byte per-call limit; narrow the query or lower pagesize/max_bases.",
        code="result_too_large",
    )
