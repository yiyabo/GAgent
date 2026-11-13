"""Phase 2: publish integrity, read-only Parquet filtering, and service parity."""

from __future__ import annotations

import contextlib
import hashlib
import json
import runpy
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq
import pytest

from app.ops import sandbox as sandbox_ops
from app.services.phagescope_data_service import accel_engine as ae
from app.services.phagescope_data_service import server as svc
from app.tests.tools.test_phagescope_data import _MockConn
from scripts import build_phagescope_accel as build
from tool_box.tools_impl.execute_code import config as code_config
from tool_box.tools_impl.execute_code import fs_guard_runtime, fs_policy, stub_gen
from tool_box.tools_impl.phagescope_data import handler as ps_handler
from tool_box.tools_impl.phagescope_data.config import PhageScopeDataError


@pytest.fixture
def accel_dir(tmp_path: Path) -> Path:
    root = tmp_path / "v2"
    root.mkdir()
    rows = [
        {
            "id": 13,
            "accession": "NC_001416.1",
            "dataset_id": 2,
            "dataset": "RefSeq",
            "length": 48502,
            "gc_content": 49.86,
            "host": "Escherichia coli",
            "completeness": "Complete",
            "taxonomy": "Drexlerviridae",
            "cluster": "Cluster_1",
            "subcluster": "Subcluster_1",
            "is_mapped": False,
            "is_rep": True,
            "lifestyle": "temperate",
            "host_phylum": "Pseudomonadota",
            "host_class": "Gammaproteobacteria",
            "host_order": "Enterobacterales",
            "host_family": "Enterobacteriaceae",
            "host_genus": "Escherichia",
            "host_species": "Escherichia coli",
            "avail_mask": 7,
            "n_amr": 0,
            "n_virulence": 0,
            "n_trna": 2,
            "n_crispr": 0,
            "n_anticrispr": 0,
            "n_terminator": 0,
            "n_transmembrane": 1,
        },
        {
            "id": 99,
            "accession": "MetaVR_jumbo_01",
            "dataset_id": 27,
            "dataset": "MetaVR",
            "length": 260000,
            "gc_content": 41.2,
            "host": "Pseudomonas aeruginosa",
            "completeness": "Complete",
            "taxonomy": "Caudoviricetes",
            "cluster": "Cluster_9",
            "subcluster": "Subcluster_9",
            "is_mapped": False,
            "is_rep": True,
            "lifestyle": "virulent",
            "host_phylum": "Pseudomonadota",
            "host_class": "Gammaproteobacteria",
            "host_order": "Pseudomonadales",
            "host_family": "Pseudomonadaceae",
            "host_genus": "Pseudomonas",
            "host_species": "Pseudomonas aeruginosa",
            "avail_mask": 1,
            "n_amr": 1,
            "n_virulence": 0,
            "n_trna": 0,
            "n_crispr": 1,
            "n_anticrispr": 0,
            "n_terminator": 0,
            "n_transmembrane": 0,
        },
        {
            "id": 101,
            "accession": "MAPPED_01",
            "dataset_id": 27,
            "dataset": "MetaVR",
            "length": 9000,
            "gc_content": 38.0,
            "host": "",
            "completeness": "Unknown",
            "taxonomy": "",
            "cluster": "",
            "subcluster": "",
            "is_mapped": True,
            "is_rep": False,
            "lifestyle": "",
            "host_phylum": "",
            "host_class": "",
            "host_order": "",
            "host_family": "",
            "host_genus": "",
            "host_species": "",
            "avail_mask": 0,
            "n_amr": 0,
            "n_virulence": 0,
            "n_trna": 0,
            "n_crispr": 0,
            "n_anticrispr": 0,
            "n_terminator": 0,
            "n_transmembrane": 0,
        },
    ]
    wide = root / "phages_wide.parquet"
    pq.write_table(pa.Table.from_pylist(rows), wide)
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "format_version": 2,
                "phages_rows": 3,
                "feature_tracks": {"n_amr": 1},
                "files": {
                    wide.name: {
                        "bytes": wide.stat().st_size,
                        "sha256": hashlib.sha256(wide.read_bytes()).hexdigest(),
                    }
                }
            }
        )
    )
    return root


def test_accel_filters_include_mapped_when_selected_and_require_all_files(accel_dir: Path) -> None:
    engine = ae.AccelEngine(accel_dir)
    assert engine.available
    assert engine.filter_ids({"min_trna": 0}) == ([13, 99, 101], 3)
    assert engine.filter_ids({"is_mapped": True, "amr_free": True}) == ([101], 1)
    assert engine.filter_ids({"require_files": ["fasta", "faa", "gff3"]}) == ([13], 1)
    assert engine.filter_ids({"dataset": "refseq", "datasets": ["metavr"], "has_crispr": True}) == ([99], 1)
    assert engine.filter_ids({"host": "PSEUDOMONAS", "host_match": "contains", "amr_free": False}) == ([99], 1)
    assert engine.aggregate({}, ["dataset", "completeness"])["groups"][0]["count"] == 1
    assert engine.cohort_report({"min_trna": 0})["count"] == 3
    assert engine.cohort_report({})["count"] == 2


def test_accel_rejects_invalid_or_ambiguous_filters(accel_dir: Path) -> None:
    engine = ae.AccelEngine(accel_dir)
    with pytest.raises(ValueError, match="host_rank"):
        engine.filter_ids({"amr_free": True, "host_rank": "Genus", "host_name": "Pseudomonas"})
    with pytest.raises(ValueError, match="Unknown dataset"):
        engine.filter_ids({"min_trna": 0, "dataset": "nonexistent"})
    with pytest.raises(ValueError, match="require_files"):
        engine.filter_ids({"require_files": ["fasta", "secret"]})
    with pytest.raises(ValueError, match="group_by"):
        engine.aggregate({}, ["dataset", "dataset"])


def test_service_accel_query_preserves_sql_rows_paging_and_unavailable(accel_dir: Path) -> None:
    @contextlib.contextmanager
    def provider():
        yield _MockConn()

    state = svc.DataServiceState(api_key="test", conn_provider=provider, overview={}, accel_root=accel_dir)
    headers = {"X-API-Key": "test"}
    endpoint = "/agent-api/v1/data/v2/phages/query/"
    status, body = svc.dispatch_request(
        state,
        "POST",
        endpoint,
        headers,
        json.dumps(
            {
                "filters": {"require_files": ["fasta", "faa"]},
                "include_total": True,
                "pagesize": 1,
            }
        ).encode(),
    )
    assert status == 200
    assert body["count"] == 1 and body["total_exact"] is True and not body["has_more"]
    assert body["results"][0]["id"] == 13
    assert "host_lineage" in body["results"][0]
    assert "host_ranks" not in body["results"][0]
    (accel_dir / "manifest.json").write_text("{}")
    unavailable = svc.DataServiceState(api_key="test", conn_provider=provider, overview={}, accel_root=accel_dir)
    status, body = svc.dispatch_request(
        unavailable,
        "POST",
        endpoint,
        headers,
        json.dumps(
            {
                "filters": {"require_files": ["fasta"]},
            }
        ).encode(),
    )
    assert status == 503 and body["error"]["code"] == "accel_unavailable"
    status, body = svc.dispatch_request(
        unavailable,
        "POST",
        endpoint,
        headers,
        json.dumps(
            {
                "filters": {"phage_ids": [13]},
            }
        ).encode(),
    )
    assert status == 200 and body["results"][0]["id"] == 13


def test_service_accel_aggregate_cohort_and_sql_fallback(accel_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The separate query test covers SQL enrichment; this fixture checks route
    # selection and that cohort samples follow the Parquet-selected IDs.
    monkeypatch.setattr(
        svc, "_accel_sample_rows",
        lambda state, ids, **kwargs: [{"id": pid, "host_lineage": {}} for pid in ids],
    )
    @contextlib.contextmanager
    def provider():
        yield _MockConn()

    state = svc.DataServiceState(api_key="test", conn_provider=provider, overview={}, accel_root=accel_dir)
    headers = {"X-API-Key": "test"}

    def post(endpoint: str, body: dict):
        return svc.dispatch_request(state, "POST", "/agent-api/v1/data/v2/phages/" + endpoint + "/", headers,
                                    json.dumps(body).encode())

    status, grouped = post("aggregate", {"filters": {"require_files": ["fasta"]},
                                         "group_by": ["dataset", "completeness"]})
    assert status == 200 and grouped["source"] == "accel_parquet"
    assert {(g["key"][0], g["count"]) for g in grouped["groups"]} == {("MetaVR", 1), ("RefSeq", 1)}
    status, report = post("cohort_report", {"filters": {"require_files": ["fasta"]}, "sample_limit": 2})
    assert status == 200 and report["count"] == 2
    assert [row["id"] for row in report["sample_rows"]] == [13, 99]
    assert all("host_lineage" in row for row in report["sample_rows"])
    status, legacy = post("aggregate", {"filters": {"dataset": "MetaVR"}, "group_by": "host_genus"})
    assert status == 200 and legacy["source"] == "postgresql_live"


def test_availability_route_is_bounded_and_reports_snapshot(accel_dir: Path) -> None:
    @contextlib.contextmanager
    def provider():
        yield _MockConn()

    state = svc.DataServiceState(api_key="test", conn_provider=provider, overview={}, accel_root=accel_dir)
    url = "/agent-api/v1/data/v2/phages/availability/"
    status, body = svc.dispatch_request(state, "POST", url, {"X-API-Key": "test"}, b'{"phage_ids":[13,99]}')
    assert status == 200
    assert {row["id"]: row["avail_mask"] for row in body["results"]} == {13: 7, 99: 1}
    status, body = svc.dispatch_request(state, "POST", url, {"X-API-Key": "test"}, b'{"phage_ids":[]}')
    assert status == 400 and body["error"]["code"] == "invalid_parameter"


@pytest.mark.asyncio
@pytest.mark.parametrize("unavailable", [False, True])
async def test_materialize_precheck_is_advisory(monkeypatch: pytest.MonkeyPatch, unavailable: bool) -> None:
    monkeypatch.setenv("PHAGESCOPE_DATA_ENABLED", "1")

    async def fake_service(path, **kwargs):
        if path == "v2/phages/query/":
            return {"results": [{"id": 13, "Acession_ID": "x", "dataset": "RefSeq"}]}
        if unavailable:
            raise PhageScopeDataError("accel_unavailable", "No snapshot", 503)
        return {"results": [{"id": 13, "avail_mask": 1}]}

    async def fake_materialize(phages, **kwargs):
        return {"success": True, "materialized_counts": {"fasta": 1, "faa": 1}}

    monkeypatch.setattr(ps_handler.client, "call_service", fake_service)
    monkeypatch.setattr(ps_handler, "materialize_phage_bundle", fake_materialize)
    result = await ps_handler.phagescope_data_handler(
        operation="materialize",
        phage_id=13,
        kinds=["fasta", "faa"],
    )
    assert result["success"] is True
    precheck = result["availability_precheck"]
    if unavailable:
        assert precheck == {"status": "unavailable", "reason": "accel_unavailable"}
    else:
        assert precheck["status"] == "snapshot_advisory"
        assert precheck["missing_snapshot"] == [{"phage": "x", "kind": "faa"}]


def test_code_mode_dataframe_and_read_only_mount(accel_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = stub_gen.generate_stub_module(["phagescope_data"])
    source_file = accel_dir.parent / "gagent_tools.py"
    source_file.write_text(source, encoding="utf-8")
    ns = runpy.run_path(str(source_file))
    import pyarrow.dataset as ds

    table = ns["phagescope_dataframe"](
        columns=["id", "dataset"],
        filter_expr=ds.field("dataset") == "RefSeq",
        root=str(accel_dir),
    )
    assert table.to_dict("records") == [{"id": 13, "dataset": "RefSeq"}]
    with pytest.raises(ValueError, match="limit"):
        ns["phagescope_dataframe"](limit=100001, root=str(accel_dir))
    assert "def phagescope_dataframe" not in stub_gen.generate_stub_module([])

    host_root = accel_dir.parent
    (host_root / "current").symlink_to("v2")
    monkeypatch.setenv("PHAGESCOPE_DATA_ENABLED", "1")
    monkeypatch.setenv("PHAGESCOPE_ACCEL_HOST_DIR", str(host_root))
    monkeypatch.setattr(code_config, "PHAGESCOPE_ACCEL_CONTAINER_DIR", str(host_root))
    assert code_config.phagescope_accel_host_dir() == str(host_root)
    work = host_root.parent / "session"
    work.mkdir()
    policy = fs_policy.build_fs_policy(session_id="", child_cwd=work, scratch_dir=work, kernel_dir=None)
    policy["deny_roots"] = [str(host_root)]
    guard = fs_guard_runtime._Policy(policy)
    guard.check(host_root / "current" / "phages_wide.parquet", False)
    with pytest.raises(PermissionError):
        guard.check(host_root / "current" / "manifest.json", True)
    spec = sandbox_ops.SandboxRunSpec(
        image="gagent-sandbox-python:latest",
        kind="python",
        host_ro_mounts=[(str(host_root), "/phagescope/accel")],
    )
    argv = sandbox_ops.build_sandbox_run_args(spec)
    assert f"{host_root}:/phagescope/accel:ro" in argv
    assert sandbox_ops.as_run_kwargs(spec)["volumes"][str(host_root)]["mode"] == "ro"
    monkeypatch.setenv("PHAGESCOPE_DATA_ENABLED", "0")
    assert code_config.phagescope_accel_host_dir() is None


def test_availability_is_dataset_scoped_and_missing_source_blocks_publish(tmp_path: Path) -> None:
    for kind, filename in (
        ("phage_fasta", "x.fasta"),
        ("phage_faa", "x.faa"),
        ("phage_gff3/individual_data", "x.gff3"),
    ):
        folder = tmp_path / kind / "RefSeq"
        folder.mkdir(parents=True)
        (folder / filename).write_text("x")
    available, totals = build.availability_bitmap(tmp_path)
    assert totals == {"fasta": 1, "faa": 1, "gff3": 1}
    phages = pd.DataFrame(
        [{"id": 1, "dataset": "RefSeq", "accession": "x"}, {"id": 2, "dataset": "MetaVR", "accession": "x"}]
    )
    joined = phages.merge(available, on=["dataset", "accession"], how="left", validate="one_to_one")
    assert joined["avail_mask"].fillna(0).tolist() == [7, 0]
    (tmp_path / "phage_faa" / "RefSeq" / "x.faa").unlink()
    with pytest.raises(ValueError, match="no files"):
        build.availability_bitmap(tmp_path)


def test_feature_key_validation_rejects_orphans_and_preserves_join_cardinality() -> None:
    phages = pd.DataFrame([{"id": 1, "accession": "a"}, {"id": 2, "accession": "b"}])
    by_id = pd.DataFrame([{"id": 1, "n_trna": 2}])
    by_acc = pd.DataFrame([{"accession": "a", "n_amr": 0}])
    build.validate_feature_keys(phages, by_id, by_acc)
    assert len(phages.merge(by_id, on="id", how="left", validate="one_to_one")
               .merge(by_acc, on="accession", how="left", validate="one_to_one")) == 2
    with pytest.raises(ValueError, match="uncatalogued phages"):
        build.validate_feature_keys(phages, by_id, pd.DataFrame([{"accession": "orphan", "n_amr": 1}]))


def test_duplicate_accessions_keep_unknown_annotation_counts() -> None:
    catalog = pd.DataFrame([
        {"id": 1, "dataset": "IMG_VR", "accession": "same"},
        {"id": 2, "dataset": "MetaVR", "accession": "same"},
        {"id": 3, "dataset": "IMG_VR", "accession": "unique"},
    ])
    by_id = pd.DataFrame([{"id": 1, "n_trna": 2}])
    by_acc = pd.DataFrame([{"accession": "same", "n_crispr": 1, "n_terminator": 3}])
    by_dataset_acc = pd.DataFrame([
        {"dataset": "IMG_VR", "accession": "same", "n_amr": 1},
        {"dataset": "MetaVR", "accession": "same", "n_amr": 0},
    ])
    merged, unknown, n_ambiguous, orphan_report = build.merge_feature_counts(catalog, by_id, by_acc, by_dataset_acc)
    assert orphan_report["discarded_dataset_accession_pairs"] == 0
    assert len(merged) == 3 and n_ambiguous == 1
    assert merged["n_amr"].tolist() == [1, 0, 0]
    assert pd.isna(merged.loc[0, "n_crispr"]) and pd.isna(merged.loc[1, "n_crispr"])
    assert merged.loc[2, "n_crispr"] == 0 and unknown["n_crispr"] == 2
    table = ds.dataset(pa.Table.from_pandas(merged, preserve_index=False))
    assert table.to_table(filter=ds.field("n_crispr") == 0).column("id").to_pylist() == [3]
    assert table.to_table(filter=ds.field("n_amr") == 0).column("id").to_pylist() == [2, 3]


def test_orphan_feature_pairs_are_reported_but_conflicting_dataset_fails() -> None:
    catalog = pd.DataFrame([{"id": 1, "dataset": "IMG_VR", "accession": "in_catalog"}])
    empty_id = pd.DataFrame(columns=["id"])
    empty_acc = pd.DataFrame(columns=["accession"])
    orphan = pd.DataFrame([
        {"dataset": "IMG_VR", "accession": "in_catalog", "n_anticrispr": 3},
        {"dataset": "MetaVR", "accession": "outside_catalog", "n_anticrispr": 7},
    ])
    merged, unknown, _, report = build.merge_feature_counts(catalog, empty_id, empty_acc, orphan)
    assert merged.loc[0, "n_anticrispr"] == 3 and unknown["n_anticrispr"] == 0
    assert report == {"discarded_dataset_accession_pairs": 1, "tracks": {"n_anticrispr": 1}}
    conflicting = pd.DataFrame([{"dataset": "MetaVR", "accession": "in_catalog", "n_anticrispr": 7}])
    with pytest.raises(ValueError, match="dataset labels conflict"):
        build.merge_feature_counts(catalog, empty_id, empty_acc, conflicting)


def test_atomic_publish_keeps_old_version_on_error(tmp_path: Path) -> None:
    old = tmp_path / "v1"
    old.mkdir()
    current = tmp_path / "current"
    current.symlink_to("v1")
    staging = tmp_path / ".v2.tmp"
    staging.mkdir()
    build.publish(staging, tmp_path / "v2", current)
    assert current.resolve() == (tmp_path / "v2").resolve()
    assert old.is_dir()
    duplicate = tmp_path / ".v2.other"
    duplicate.mkdir()
    with pytest.raises(FileExistsError):
        build.publish(duplicate, tmp_path / "v2", current)
    assert current.resolve() == (tmp_path / "v2").resolve()
    assert duplicate.is_dir()
