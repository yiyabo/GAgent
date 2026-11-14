"""Concrete async handler for the phagescope_data tool."""

from __future__ import annotations

from typing import Any

from tool_box.context import ToolContext

from . import client
from .config import (
    PhageScopeDataError,
    local_mounts_status,
    phagescope_data_enabled,
    service_endpoint,
)
from .materialize import (
    attach_artifacts,
    materialize_phage_bundle,
    materialize_protein_bundle,
    resolve_bundle_dir,
    write_phages_tsv,
)
from .schema import OPERATIONS

_PHAGE_FILTER_KEYS = (
    "phage_ids",
    "accession",
    "accessions",
    "dataset",
    "datasets",
    "completeness",
    "lifestyle",
    "taxonomy",
    "taxonomy_match",
    "host",
    "host_match",
    "host_rank",
    "host_name",
    "cluster",
    "subcluster",
    "is_rep",
    "is_mapped",
    "length_min",
    "length_max",
    "gc_min",
    "gc_max",
    # Phase 2 agent-only predicates (served by the acceleration layer)
    "amr_free",
    "virulence_free",
    "min_trna",
    "has_crispr",
    "has_anticrispr",
    "require_files",
)


def _int_arg(value: Any, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise PhageScopeDataError("invalid_parameter", f"Expected integer argument, got {value!r}.") from exc


def _extract_phage_filters(kwargs: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in _PHAGE_FILTER_KEYS:
        val = kwargs.get(k)
        if val is not None and val != "" and val != []:
            out[k] = val
    if kwargs.get("phage_id") is not None and "phage_ids" not in out:
        out["phage_ids"] = [_int_arg(kwargs["phage_id"], 0)]
    return out


async def phagescope_data_handler(
    operation: str = "overview",
    timeout: float = 45.0,
    tool_context: ToolContext | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    if not phagescope_data_enabled():
        return {
            "success": False,
            "tool": "phagescope_data",
            "error_code": "tool_disabled",
            "error": "phagescope_data is not enabled on this deployment (set PHAGESCOPE_DATA_ENABLED=1).",
        }

    op = str(operation or "").strip().lower()
    if op not in OPERATIONS:
        return {
            "success": False,
            "tool": "phagescope_data",
            "operation": op,
            "error_code": "invalid_operation",
            "error": f"Unsupported operation {op!r}. Valid operations: {', '.join(OPERATIONS)}.",
        }

    try:
        if op == "overview":
            url, key = service_endpoint()
            mounts = local_mounts_status()
            overview = await client.call_service("stats/overview/", method="GET", timeout=timeout)
            health = await client.call_service("health/", method="GET", timeout=timeout)
            return {
                "success": True,
                "tool": "phagescope_data",
                "operation": "overview",
                "db_status": health.get("status", "ok"),
                "service_configured": bool(url and key),
                "mounts": mounts,
                "overview": overview,
            }

        if op == "datasets":
            mounts = local_mounts_status()
            ds_resp = await client.call_service("datasets/", method="GET", timeout=timeout)
            ov_resp = await client.call_service("stats/overview/", method="GET", timeout=timeout)
            raw_by_src = ov_resp.get("annotated_by_source")
            by_source: dict[str, Any] = raw_by_src if isinstance(raw_by_src, dict) else {}
            raw_ds = ds_resp.get("results")
            ds_rows = raw_ds if isinstance(raw_ds, list) else []
            enriched = []
            for row in ds_rows:
                if not isinstance(row, dict):
                    continue
                name = str(row.get("name") or "")
                enriched.append(
                    {
                        "id": row.get("id"),
                        "name": name,
                        "annotated_genomes": by_source.get(name),
                    }
                )
            return {
                "success": True,
                "tool": "phagescope_data",
                "operation": "datasets",
                "count": len(enriched),
                "datasets": enriched,
                "mounts": mounts,
            }

        if op == "get_phage":
            pid = kwargs.get("phage_id")
            acc = kwargs.get("accession")
            if pid is None and not acc:
                raise PhageScopeDataError("invalid_parameter", "get_phage requires phage_id or accession.")
            detail = await client.call_service(
                "v2/phages/detail/",
                method="POST",
                body={"phage_id": pid, "accession": acc},
                timeout=timeout,
            )
            return {
                "success": True,
                "tool": "phagescope_data",
                "operation": "get_phage",
                "phage": detail,
            }

        if op == "search_phages":
            filters = _extract_phage_filters(kwargs)
            save_tsv = bool(kwargs.get("save_tsv", False))
            page = _int_arg(kwargs.get("page"), 1)
            if save_tsv:
                pagesize = min(max(_int_arg(kwargs.get("max_items"), 500), 1), 2000)
            else:
                pagesize = min(max(_int_arg(kwargs.get("pagesize"), 25), 1), 500)
            resp = await client.call_service(
                "v2/phages/query/",
                method="POST",
                body={
                    "filters": filters,
                    "page": page,
                    "pagesize": pagesize,
                    "export_mode": save_tsv,
                    "include_total": bool(kwargs.get("include_total", False)),
                    "include_host_ranks": True,
                    "sort_by": kwargs.get("sort_by") or "id",
                },
                timeout=timeout,
            )
            raw_rows = resp.get("results")
            rows = list(raw_rows) if isinstance(raw_rows, list) else []
            out: dict[str, Any] = {
                "success": True,
                "tool": "phagescope_data",
                "operation": "search_phages",
                "filters": filters,
                "count": resp.get("count", len(rows)),
                "total_exact": resp.get("total_exact", False),
                "page": resp.get("page", page),
                "pagesize": resp.get("pagesize", pagesize),
                "has_more": resp.get("has_more", False),
                "returned": len(rows),
                "results": rows[:50] if save_tsv else rows,
            }
            if save_tsv and rows:
                bdir = resolve_bundle_dir(tool_context, kwargs.get("output_subdir"))
                tsv_path = write_phages_tsv(rows, bdir / "phages_cohort.tsv")
                saved = {"phages_cohort.tsv": str(tsv_path)}
                out = attach_artifacts(out, bdir, saved, tool_context)
                out["saved_rows"] = len(rows)
            return out

        if op == "cohort_report":
            filters = _extract_phage_filters(kwargs)
            sample_limit = min(max(_int_arg(kwargs.get("sample_size"), 5), 1), 20)
            resp = await client.call_service(
                "v2/phages/cohort_report/",
                method="POST",
                body={
                    "filters": filters,
                    "sample_limit": sample_limit,
                    "sort_by": kwargs.get("sort_by") or "id",
                },
                timeout=timeout,
            )
            out: dict[str, Any] = {
                "success": True,
                "tool": "phagescope_data",
                "operation": "cohort_report",
                "filters": filters,
                **resp,
            }
            if kwargs.get("save_tsv"):
                # Persist the full cohort table next to the report so the model
                # never has to re-page through results just to export them.
                export = await client.call_service(
                    "v2/phages/query/",
                    method="POST",
                    body={
                        "filters": filters,
                        "page": 1,
                        "pagesize": min(max(_int_arg(kwargs.get("max_items"), 2000), 1), 2000),
                        "export_mode": True,
                        "include_total": False,
                        "include_host_ranks": True,
                        "sort_by": kwargs.get("sort_by") or "id",
                    },
                    timeout=timeout,
                )
                raw_rows = export.get("results")
                rows = list(raw_rows) if isinstance(raw_rows, list) else []
                if rows:
                    bdir = resolve_bundle_dir(tool_context, kwargs.get("output_subdir"))
                    tsv_path = write_phages_tsv(rows, bdir / "phages_cohort.tsv")
                    saved = {"phages_cohort.tsv": str(tsv_path)}
                    out = attach_artifacts(out, bdir, saved, tool_context)
                    out["saved_rows"] = len(rows)
            return out

        if op == "aggregate":
            group_by = kwargs.get("group_by")
            if isinstance(group_by, str):
                group_by = group_by.strip()
            if not group_by:
                raise PhageScopeDataError("invalid_parameter", "aggregate requires group_by.")
            filters = _extract_phage_filters(kwargs)
            limit = min(max(_int_arg(kwargs.get("max_items") or kwargs.get("pagesize"), 50), 1), 100)
            resp = await client.call_service(
                "v2/phages/aggregate/",
                method="POST",
                body={"group_by": group_by, "filters": filters, "limit": limit},
                timeout=timeout,
            )
            return {
                "success": True,
                "tool": "phagescope_data",
                "operation": "aggregate",
                "filters": filters,
                **resp,
            }

        if op == "search_proteins":
            page = _int_arg(kwargs.get("page"), 1)
            pagesize = min(max(_int_arg(kwargs.get("pagesize"), 25), 1), 200)
            inc_seq = bool(kwargs.get("include_sequence", False))
            raw_pids = kwargs.get("protein_ids")
            if raw_pids or kwargs.get("protein_id"):
                pids = list(raw_pids) if isinstance(raw_pids, list) else []
                if kwargs.get("protein_id"):
                    pids.append(str(kwargs["protein_id"]))
                resp = await client.call_service(
                    "v2/proteins/query/",
                    method="POST",
                    body={
                        "mode": "by_ids",
                        "protein_ids": pids,
                        "dataset": kwargs.get("dataset"),
                        "include_sequence": inc_seq,
                    },
                    timeout=timeout,
                )
                return {
                    "success": True,
                    "tool": "phagescope_data",
                    "operation": "search_proteins",
                    "mode": "by_ids",
                    **resp,
                }
            if kwargs.get("phage_id") is not None or kwargs.get("accession"):
                resp = await client.call_service(
                    "v2/proteins/query/",
                    method="POST",
                    body={
                        "mode": "by_phage",
                        "phage_id": kwargs.get("phage_id"),
                        "accession": kwargs.get("accession"),
                        "dataset": kwargs.get("dataset"),
                        "product_query": kwargs.get("product_query"),
                        "function_class": kwargs.get("function_class"),
                        "include_sequence": inc_seq,
                        "page": page,
                        "pagesize": pagesize,
                    },
                    timeout=timeout,
                )
                return {
                    "success": True,
                    "tool": "phagescope_data",
                    "operation": "search_proteins",
                    "mode": "by_phage",
                    **resp,
                }
            fam_kw = kwargs.get("family_query") or kwargs.get("product_query") or kwargs.get("keyword")
            resp = await client.call_service(
                "v2/proteins/query/",
                method="POST",
                body={
                    "mode": "search_families",
                    "family_id": kwargs.get("family_id"),
                    "keyword": fam_kw,
                    "family_search_mode": kwargs.get("family_search_mode", "auto"),
                    "dominant_class": kwargs.get("function_class"),
                    "min_members": kwargs.get("min_members"),
                    "min_datasets": kwargs.get("min_datasets"),
                    "page": page,
                    "pagesize": min(pagesize, 100),
                },
                timeout=timeout,
            )
            return {
                "success": True,
                "tool": "phagescope_data",
                "operation": "search_proteins",
                "mode": "search_families",
                **resp,
            }

        if op == "family_members":
            fid = str(kwargs.get("family_id") or "").strip()
            if not fid and kwargs.get("protein_id"):
                fam_info = await client.call_service(
                    "v2/proteins/query/",
                    method="POST",
                    body={"mode": "search_families", "protein_id": kwargs["protein_id"]},
                    timeout=timeout,
                )
                fid = str(fam_info.get("family_id") or "")
            if not fid:
                raise PhageScopeDataError("invalid_parameter", "family_members requires family_id (or protein_id).")
            fam_summary = await client.call_service(
                "v2/proteins/query/",
                method="POST",
                body={"mode": "search_families", "family_id": fid},
                timeout=timeout,
            )
            members = await client.call_service(
                "v2/proteins/query/",
                method="POST",
                body={
                    "mode": "family_members",
                    "family_id": fid,
                    "subfamily_id": kwargs.get("subfamily_id"),
                    "dataset": kwargs.get("dataset"),
                    "include_sequence": bool(kwargs.get("include_sequence", False)),
                    "page": _int_arg(kwargs.get("page"), 1),
                    "pagesize": min(max(_int_arg(kwargs.get("pagesize"), 25), 1), 200),
                },
                timeout=timeout,
            )
            return {
                "success": True,
                "tool": "phagescope_data",
                "operation": "family_members",
                "family": fam_summary,
                **members,
            }

        if op == "features":
            ftype = str(kwargs.get("feature_type") or "").strip()
            if not ftype:
                raise PhageScopeDataError("invalid_parameter", "features requires feature_type.")
            resp = await client.call_service(
                "v2/features/query/",
                method="POST",
                body={
                    "feature_type": ftype,
                    "phage_id": kwargs.get("phage_id"),
                    "accession": kwargs.get("accession"),
                    "dataset": kwargs.get("dataset"),
                    "keyword": kwargs.get("keyword"),
                    "page": _int_arg(kwargs.get("page"), 1),
                    "pagesize": min(max(_int_arg(kwargs.get("pagesize"), 50), 1), 200),
                },
                timeout=timeout,
            )
            return {
                "success": True,
                "tool": "phagescope_data",
                "operation": "features",
                "feature_type": ftype,
                **resp,
            }

        if op == "structures":
            raw_pids = kwargs.get("protein_ids")
            pids = [str(x) for x in raw_pids] if isinstance(raw_pids, list) else []
            if kwargs.get("protein_id"):
                pids.append(str(kwargs["protein_id"]))
            if not pids:
                raise PhageScopeDataError("invalid_parameter", "structures requires protein_id or protein_ids.")
            if kwargs.get("materialize_pdb"):
                mat = await materialize_protein_bundle(
                    protein_ids=pids[:50],
                    include_pdb=True,
                    output_subdir=kwargs.get("output_subdir"),
                    context=tool_context,
                    timeout=timeout,
                )
                mat["tool"] = "phagescope_data"
                mat["operation"] = "structures"
                return mat
            resp = await client.call_service(
                "v2/structures/resolve/",
                method="POST",
                body={"protein_ids": pids[:50]},
                timeout=timeout,
            )
            raw_st = resp.get("results")
            st_rows = raw_st if isinstance(raw_st, list) else []
            rows = [{k: v for k, v in r.items() if k != "pdb_rel_path"} for r in st_rows if isinstance(r, dict)]
            return {
                "success": True,
                "tool": "phagescope_data",
                "operation": "structures",
                "count": len(rows),
                "results": rows,
            }

        raw_kinds = kwargs.get("kinds")
        has_protein_selector = bool(kwargs.get("protein_ids") or kwargs.get("protein_id") or kwargs.get("family_id"))
        if has_protein_selector and not (
            kwargs.get("phage_id") or kwargs.get("phage_ids") or kwargs.get("accession") or kwargs.get("accessions")
        ):
            raw_pids = kwargs.get("protein_ids")
            pids = [str(x) for x in raw_pids] if isinstance(raw_pids, list) else []
            if kwargs.get("protein_id"):
                pids.append(str(kwargs["protein_id"]))
            kinds_list = [str(k) for k in raw_kinds] if isinstance(raw_kinds, list) and raw_kinds else ["faa"]
            mat_p = await materialize_protein_bundle(
                protein_ids=pids or None,
                family_id=kwargs.get("family_id"),
                subfamily_id=kwargs.get("subfamily_id"),
                dataset=kwargs.get("dataset"),
                max_items=min(max(_int_arg(kwargs.get("max_items"), 100), 1), 200),
                include_pdb=("pdb" in kinds_list or bool(kwargs.get("materialize_pdb"))),
                output_subdir=kwargs.get("output_subdir"),
                context=tool_context,
                timeout=timeout,
            )
            mat_p["tool"] = "phagescope_data"
            return mat_p

        filters = _extract_phage_filters(kwargs)
        if not filters:
            raise PhageScopeDataError(
                "missing_filter",
                "materialize requires phage_id/phage_ids/accession/accessions, phage filters, or protein_ids/family_id.",
            )
        cap = min(max(_int_arg(kwargs.get("max_items"), 50), 1), 200)
        q_resp = await client.call_service(
            "v2/phages/query/",
            method="POST",
            body={
                "filters": filters,
                "page": 1,
                "pagesize": cap,
                "include_total": False,
                "include_host_ranks": True,
                "sort_by": kwargs.get("sort_by") or "id",
            },
            timeout=timeout,
        )
        raw_phages = q_resp.get("results")
        phages = [r for r in raw_phages if isinstance(r, dict)] if isinstance(raw_phages, list) else []
        if not phages:
            return {
                "success": False,
                "tool": "phagescope_data",
                "operation": "materialize",
                "error_code": "not_found",
                "error": "No phages matched the requested identifiers or filters.",
            }
        kinds_list = (
            [str(k) for k in raw_kinds] if isinstance(raw_kinds, list) and raw_kinds else ["fasta", "metadata_tsv"]
        )
        # Advisory snapshot only: a missing file can still be supplied by the
        # live SQL sequence/protein fallback, so the materializer decides.
        precheck: dict[str, Any] | None = None
        wanted = set(kinds_list) & {"fasta", "faa", "gff3"}
        if wanted:
            try:
                availability = await client.call_service(
                    "v2/phages/availability/",
                    method="POST",
                    body={"phage_ids": [int(p["id"]) for p in phages]},
                    timeout=timeout,
                )
                found = {int(row["id"]): int(row["avail_mask"]) for row in availability.get("results", [])}
                missing_snapshot = [
                    {"phage": str(p.get("Acession_ID") or ""), "kind": kind}
                    for p in phages
                    for kind, bit in (("fasta", 1), ("faa", 2), ("gff3", 4))
                    if kind in wanted and not (found.get(int(p["id"]), 0) & bit)
                ]
                precheck = {
                    "status": "snapshot_advisory",
                    "checked": len(found),
                    "missing_snapshot": missing_snapshot[:50],
                    "missing_snapshot_count": len(missing_snapshot),
                }
            except (PhageScopeDataError, ValueError, TypeError) as exc:
                precheck = {"status": "unavailable", "reason": getattr(exc, "code", "invalid_snapshot")}
        mat_g = await materialize_phage_bundle(
            phages,
            kinds=kinds_list,
            combine=bool(kwargs.get("combine", True)),
            per_phage_files=bool(kwargs.get("per_phage_files", False)),
            output_subdir=kwargs.get("output_subdir"),
            context=tool_context,
            timeout=timeout,
        )
        mat_g["tool"] = "phagescope_data"
        if precheck is not None:
            mat_g["availability_precheck"] = precheck
        return mat_g

    except PhageScopeDataError as exc:
        return {
            "success": False,
            "tool": "phagescope_data",
            "operation": op,
            "status_code": exc.status,
            "error_code": exc.code,
            "error": exc.message,
        }
