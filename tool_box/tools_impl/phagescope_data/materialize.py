"""Materialize untruncated PhageScope genomes, proteins, GFF3 annotations, TSVs, and PDB structures."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import shutil
import uuid
from pathlib import Path
from typing import Any

from tool_box.context import ToolContext
from tool_box.tools_impl.phagescope_artifacts import (
    _attach_local_bundle_artifact_fields,
)

from . import client
from .config import (
    PhageScopeDataError,
    contained,
    fasta_roots_for_dataset,
    pdb_root,
    safe_component,
    safe_filename_stem,
    seq_root,
)

PHAGE_TSV_COLUMNS = (
    "id",
    "Acession_ID",
    "dataset",
    "length",
    "gc_content",
    "completeness",
    "lifestyle",
    "taxonomy",
    "host",
    "Host_phylum",
    "Host_class",
    "Host_order",
    "Host_family",
    "Host_genus",
    "Host_species",
    "cluster",
    "subcluster",
    "is_rep",
    "is_mapped",
)


def resolve_bundle_dir(context: ToolContext | None, output_subdir: str | None = None) -> Path:
    from app.services.session_paths import get_runtime_session_dir
    from app.services.tool_output_resolver import get_tool_output_resolver

    if context and context.work_dir:
        base = Path(context.work_dir).resolve()
    elif context and context.session_id:
        base = Path(get_runtime_session_dir(context.session_id, create=True)).resolve()
    else:
        base = Path(
            get_tool_output_resolver().resolve(session_id=None, tool_name="phagescope_data", create=True)
        ).resolve()

    ps_root = base / "results" / "phagescope"
    ps_root.mkdir(parents=True, exist_ok=True)
    sub = safe_filename_stem(output_subdir) if output_subdir else f"bundle_{uuid.uuid4().hex[:10]}"
    target = contained(ps_root / sub, ps_root)
    target.mkdir(parents=True, exist_ok=True)
    return target


def write_phages_tsv(rows: list[dict[str, Any]], dest: Path) -> Path:
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter="\t", lineterminator="\n")
    writer.writerow(PHAGE_TSV_COLUMNS)
    for r in rows:
        raw_hl = r.get("host_lineage")
        hl: dict[str, Any] = raw_hl if isinstance(raw_hl, dict) else {}
        writer.writerow(
            [
                r.get("id", ""),
                r.get("Acession_ID", ""),
                r.get("dataset", ""),
                r.get("length", ""),
                r.get("gc_content", ""),
                r.get("completeness", ""),
                r.get("lifestyle", ""),
                r.get("taxonomy", ""),
                r.get("host", ""),
                hl.get("Phylum", ""),
                hl.get("Class", ""),
                hl.get("Order", ""),
                hl.get("Family", ""),
                hl.get("Genus", ""),
                hl.get("Species", ""),
                r.get("cluster", ""),
                r.get("subcluster", ""),
                r.get("is_rep", ""),
                r.get("is_mapped", ""),
            ]
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    _ = dest.write_text(buf.getvalue(), encoding="utf-8")
    return dest


def _wrap_fasta_seq(seq: str, width: int = 80) -> str:
    clean = "".join(seq.split())
    return "\n".join(clean[i : i + width] for i in range(0, len(clean), width))


def _format_proteins_faa(proteins: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for p in proteins:
        seq = str(p.get("sequence") or "").strip()
        if not seq:
            continue
        pid = p.get("Protein_id") or p.get("protein_id") or "protein"
        prod = p.get("Protein_product") or p.get("protein_product") or "hypothetical protein"
        pacc = p.get("Phage_Acession_ID") or p.get("phage_accession_id") or ""
        start = p.get("Start_location")
        stop = p.get("Stop_location")
        strand = p.get("Strand") or ""
        loc = f" {start}..{stop}({strand})" if start is not None and stop is not None else ""
        header = f">{pid} {prod} [phage={pacc}]{loc}".strip()
        blocks.append(header + "\n" + _wrap_fasta_seq(seq))
    return ("\n".join(blocks) + "\n") if blocks else ""


async def _fetch_all_phage_proteins_full(
    phage_id: int,
    *,
    timeout: float,
) -> list[dict[str, Any]]:
    all_rows: list[dict[str, Any]] = []
    page = 1
    while page <= 20:
        resp = await client.call_service(
            "v2/proteins/query/",
            method="POST",
            body={
                "mode": "by_phage",
                "phage_id": phage_id,
                "include_sequence": True,
                "full_sequence": True,
                "page": page,
                "pagesize": 1000,
            },
            timeout=timeout,
        )
        batch = resp.get("results") or []
        if not isinstance(batch, list) or not batch:
            break
        all_rows.extend(batch)
        total = int(resp.get("count") or 0)
        if len(all_rows) >= total:
            break
        page += 1
    return all_rows


async def materialize_phage_bundle(
    phages: list[dict[str, Any]],
    *,
    kinds: list[str],
    combine: bool = True,
    per_phage_files: bool = False,
    output_subdir: str | None = None,
    context: ToolContext | None = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    bundle_dir = resolve_bundle_dir(context, output_subdir)
    sr = seq_root()
    saved_files: dict[str, str] = {}
    missing: list[dict[str, str]] = []
    truncated_items: list[str] = []
    diagnostics: list[dict[str, str]] = []

    if "metadata_tsv" in kinds:
        tsv_path = write_phages_tsv(phages, bundle_dir / "metadata.tsv")
        saved_files["metadata.tsv"] = str(tsv_path)

    combined_fasta_chunks: list[str] = []
    combined_faa_chunks: list[str] = []
    fasta_count = 0
    faa_count = 0
    gff_count = 0

    for p in phages:
        pid = int(p["id"])
        acc = str(p.get("Acession_ID") or "")
        ds = str(p.get("dataset") or "")
        stem = safe_filename_stem(acc or f"phage_{pid}")

        if "fasta" in kinds:
            fasta_text: str | None = None
            fasta_truncated = False
            local_unavailable: str | None = None
            if ds and acc:
                dataset_key = safe_component(ds)
                accession_key = safe_component(acc)
                for root_dir, ds_dir in fasta_roots_for_dataset(dataset_key):
                    try:
                        cand = contained(ds_dir / (accession_key + ".fasta"), root_dir)
                    except PhageScopeDataError:
                        # A dataset directory that is a symlink to storage outside
                        # the mounted tree (620b: MetaVR -> /data1/...). Not an
                        # error: the extension mount may still have it.
                        local_unavailable = "dataset_dir_outside_mount"
                        continue
                    if cand.is_file():
                        fasta_text = cand.read_text(encoding="utf-8", errors="replace")
                        break
            if fasta_text is None:
                try:
                    seq_resp = await client.call_service(
                        f"phages/{pid}/sequence/",
                        method="GET",
                        query={"max_bases": 5_000_000},
                        timeout=timeout,
                    )
                    hdr = str(seq_resp.get("header") or f">{acc}")
                    seq_body = str(seq_resp.get("sequence") or "")
                    if seq_body:
                        fasta_text = hdr + "\n" + _wrap_fasta_seq(seq_body) + "\n"
                        fasta_truncated = bool(seq_resp.get("truncated"))
                except PhageScopeDataError as exc:
                    missing.append({"phage": acc, "kind": "fasta", "reason": exc.message})

            if fasta_text and fasta_truncated:
                # Never present a capped preview as the full genome: keep it in a
                # clearly named .preview file and out of the combined FASTA.
                rel = f"fasta/{stem}.fasta.preview"
                fp = contained(bundle_dir / rel, bundle_dir)
                fp.parent.mkdir(parents=True, exist_ok=True)
                _ = fp.write_text(fasta_text, encoding="utf-8")
                saved_files[rel] = str(fp)
                truncated_items.append(f"fasta:{acc}")
                missing.append(
                    {
                        "phage": acc,
                        "kind": "fasta",
                        "reason": "fasta_truncated_no_full_source",
                        "detail": "full genome not reachable from the mounted trees; only a capped preview was returned",
                    }
                )
                diagnostics.append(
                    {
                        "phage": acc,
                        "dataset": ds,
                        "kind": "fasta",
                        "reason": local_unavailable or "local_file_missing",
                    }
                )
            elif fasta_text:
                fasta_count += 1
                if not fasta_text.endswith("\n"):
                    fasta_text += "\n"
                if combine:
                    combined_fasta_chunks.append(fasta_text)
                if per_phage_files or not combine:
                    rel = f"fasta/{stem}.fasta"
                    fp = contained(bundle_dir / rel, bundle_dir)
                    fp.parent.mkdir(parents=True, exist_ok=True)
                    _ = fp.write_text(fasta_text, encoding="utf-8")
                    saved_files[rel] = str(fp)
            elif not any(m.get("phage") == acc and m.get("kind") == "fasta" for m in missing):
                missing.append(
                    {
                        "phage": acc,
                        "kind": "fasta",
                        "reason": local_unavailable or "file_not_found",
                        "detail": f"dataset={ds}",
                    }
                )
                diagnostics.append(
                    {
                        "phage": acc,
                        "dataset": ds,
                        "kind": "fasta",
                        "reason": local_unavailable or "file_not_found",
                    }
                )

        if "faa" in kinds:
            faa_text: str | None = None
            if sr is not None and ds and acc:
                faa_dir = sr / "phage_faa" / safe_component(ds)
                if faa_dir.is_dir():
                    cand_faa = contained(faa_dir / (safe_component(acc) + ".faa"), sr)
                    if cand_faa.is_file():
                        faa_text = cand_faa.read_text(encoding="utf-8", errors="replace")
            if faa_text is None:
                try:
                    prots = await _fetch_all_phage_proteins_full(pid, timeout=timeout)
                    built = _format_proteins_faa(prots)
                    if built:
                        faa_text = built
                except PhageScopeDataError as exc:
                    missing.append({"phage": acc, "kind": "faa", "reason": exc.message})

            if faa_text:
                faa_count += 1
                if not faa_text.endswith("\n"):
                    faa_text += "\n"
                if combine:
                    combined_faa_chunks.append(faa_text)
                if per_phage_files or not combine:
                    rel = f"faa/{stem}.faa"
                    fp = contained(bundle_dir / rel, bundle_dir)
                    fp.parent.mkdir(parents=True, exist_ok=True)
                    _ = fp.write_text(faa_text, encoding="utf-8")
                    saved_files[rel] = str(fp)
            elif not any(m.get("phage") == acc and m.get("kind") == "faa" for m in missing):
                missing.append({"phage": acc, "kind": "faa", "reason": "no_proteins_found"})

        if "gff3" in kinds:
            gff_found = False
            if sr is not None and ds and acc:
                gff_dir = sr / "phage_gff3" / "individual_data" / safe_component(ds)
                if gff_dir.is_dir():
                    for ext in (".gff3", ".gff"):
                        cand_gff = contained(gff_dir / (safe_component(acc) + ext), sr)
                        if cand_gff.is_file():
                            rel = f"gff3/{stem}.gff3"
                            fp = contained(bundle_dir / rel, bundle_dir)
                            fp.parent.mkdir(parents=True, exist_ok=True)
                            _ = shutil.copyfile(cand_gff, fp)
                            saved_files[rel] = str(fp)
                            gff_count += 1
                            gff_found = True
                            break
            if not gff_found:
                missing.append({"phage": acc, "kind": "gff3", "reason": "gff3_not_available_locally"})

    if combine and combined_fasta_chunks:
        comb_fa = bundle_dir / "genomes.fasta"
        _ = comb_fa.write_text("".join(combined_fasta_chunks), encoding="utf-8")
        saved_files["genomes.fasta"] = str(comb_fa)

    if combine and combined_faa_chunks:
        comb_faa = bundle_dir / "proteins.faa"
        _ = comb_faa.write_text("".join(combined_faa_chunks), encoding="utf-8")
        saved_files["proteins.faa"] = str(comb_faa)

    manifest = _write_manifest(
        bundle_dir,
        saved_files,
        extra={
            "bundle_kind": "phages",
            "requested_phages": len(phages),
            "materialized_counts": {"fasta": fasta_count, "faa": faa_count, "gff3": gff_count},
            "truncated_items": truncated_items,
            "missing": missing[:50],
            "diagnostics": diagnostics[:50],
        },
    )
    saved_files["manifest.json"] = str(bundle_dir / "manifest.json")

    result: dict[str, Any] = {
        "success": bool(saved_files),
        "operation": "materialize",
        "target": "phages",
        "requested_phages": len(phages),
        "materialized_counts": {"fasta": fasta_count, "faa": faa_count, "gff3": gff_count},
        "truncated_items": truncated_items,
        "missing": missing[:50],
        "diagnostics": diagnostics[:50],
        "manifest": manifest,
    }
    return attach_artifacts(result, bundle_dir, saved_files, context)


async def materialize_protein_bundle(
    *,
    protein_ids: list[str] | None = None,
    family_id: str | None = None,
    subfamily_id: str | None = None,
    dataset: str | None = None,
    max_items: int = 200,
    include_pdb: bool = False,
    output_subdir: str | None = None,
    context: ToolContext | None = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    bundle_dir = resolve_bundle_dir(context, output_subdir)
    saved_files: dict[str, str] = {}
    missing: list[dict[str, str]] = []

    proteins: list[dict[str, Any]] = []
    if family_id:
        cap = max(1, min(int(max_items), 200))
        resp = await client.call_service(
            "v2/proteins/query/",
            method="POST",
            body={
                "mode": "family_members",
                "family_id": family_id,
                "subfamily_id": subfamily_id,
                "dataset": dataset,
                "include_sequence": True,
                "full_sequence": True,
                "page": 1,
                "pagesize": cap,
            },
            timeout=timeout,
        )
        raw_res = resp.get("results")
        proteins = list(raw_res) if isinstance(raw_res, list) else []
    elif protein_ids:
        clean_ids = [str(x).strip() for x in protein_ids if str(x).strip()][:500]
        resp = await client.call_service(
            "v2/proteins/query/",
            method="POST",
            body={
                "mode": "by_ids",
                "protein_ids": clean_ids,
                "dataset": dataset,
                "include_sequence": True,
                "full_sequence": True,
            },
            timeout=timeout,
        )
        raw_res = resp.get("results")
        proteins = list(raw_res) if isinstance(raw_res, list) else []
    else:
        raise PhageScopeDataError("invalid_parameter", "Provide protein_ids or family_id for protein materialization.")

    faa_text = _format_proteins_faa(proteins)
    if faa_text:
        faa_path = bundle_dir / "proteins.faa"
        _ = faa_path.write_text(faa_text, encoding="utf-8")
        saved_files["proteins.faa"] = str(faa_path)

    if proteins:
        buf = io.StringIO()
        writer = csv.writer(buf, delimiter="\t", lineterminator="\n")
        writer.writerow(["protein_id", "phage_accession", "dataset", "product", "function_class", "seq_aa_len"])
        for p in proteins:
            pid = p.get("Protein_id") or p.get("protein_id") or ""
            pacc = p.get("Phage_Acession_ID") or p.get("phage_accession_id") or ""
            ds = p.get("dataset") or ""
            prod = p.get("Protein_product") or p.get("protein_product") or ""
            fcls = p.get("Protein_function_classification") or p.get("protein_function_class") or ""
            slen = len("".join(str(p.get("sequence") or "").split()))
            writer.writerow([pid, pacc, ds, prod, fcls, slen])
        meta_path = bundle_dir / "proteins_metadata.tsv"
        _ = meta_path.write_text(buf.getvalue(), encoding="utf-8")
        saved_files["proteins_metadata.tsv"] = str(meta_path)

    pdb_copied = 0
    if include_pdb and proteins:
        pr = pdb_root()
        target_pids = [
            str(p.get("Protein_id") or p.get("protein_id"))
            for p in proteins[:50]
            if (p.get("Protein_id") or p.get("protein_id"))
        ]
        st_resp = await client.call_service(
            "v2/structures/resolve/",
            method="POST",
            body={"protein_ids": target_pids},
            timeout=timeout,
        )
        raw_st = st_resp.get("results")
        st_rows = raw_st if isinstance(raw_st, list) else []
        for row in st_rows:
            if not isinstance(row, dict):
                continue
            pid = str(row.get("protein_id") or "")
            rel_p = str(row.get("pdb_rel_path") or "").strip()
            if not row.get("available"):
                missing.append({"protein": pid, "kind": "pdb", "reason": "no_structure_in_catalog"})
                continue
            if pr is None or not rel_p:
                missing.append({"protein": pid, "kind": "pdb", "reason": "pdb_mount_not_available"})
                continue
            src_pdb = contained(pr / rel_p, pr)
            if not src_pdb.is_file():
                missing.append({"protein": pid, "kind": "pdb", "reason": "structure_file_missing"})
                continue
            ext = "." + str(row.get("file_format") or "pdb").lstrip(".")
            out_rel = f"structures/{safe_filename_stem(pid)}{ext}"
            dst_pdb = contained(bundle_dir / out_rel, bundle_dir)
            dst_pdb.parent.mkdir(parents=True, exist_ok=True)
            _ = shutil.copyfile(src_pdb, dst_pdb)
            saved_files[out_rel] = str(dst_pdb)
            pdb_copied += 1

    manifest = _write_manifest(
        bundle_dir,
        saved_files,
        extra={
            "bundle_kind": "proteins",
            "proteins_found": len(proteins),
            "pdb_copied": pdb_copied,
            "missing": missing[:50],
        },
    )
    saved_files["manifest.json"] = str(bundle_dir / "manifest.json")

    result: dict[str, Any] = {
        "success": bool(proteins),
        "operation": "materialize",
        "target": "proteins",
        "proteins_count": len(proteins),
        "pdb_copied": pdb_copied,
        "missing": missing[:50],
        "manifest": manifest,
    }
    return attach_artifacts(result, bundle_dir, saved_files, context)


def _write_manifest(bundle_dir: Path, saved_files: dict[str, str], extra: dict[str, Any]) -> dict[str, Any]:
    records = []
    for rel, raw_p in saved_files.items():
        fp = Path(raw_p)
        if fp.is_file():
            data = fp.read_bytes()
            records.append(
                {
                    "path": rel,
                    "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
    manifest = {**extra, "files": records}
    _ = (bundle_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def attach_artifacts(
    result: dict[str, Any],
    bundle_dir: Path,
    saved_files: dict[str, str],
    context: ToolContext | None,
) -> dict[str, Any]:
    enriched = _attach_local_bundle_artifact_fields(
        result,
        output_dir=bundle_dir,
        saved_files=saved_files,
        session_id=context.session_id if context else None,
        task_id=context.task_id if context else None,
    )
    enriched["output_directory"] = str(bundle_dir)
    enriched["produced_files"] = (
        enriched.get("session_artifact_paths") or enriched.get("artifact_paths") or []
    )
    return enriched
