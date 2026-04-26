import asyncio
import csv
from pathlib import Path

from tool_box.tools_impl.code_executor import _build_qwen_container_mounts
from tool_box.tools_impl.phagescope_research import phagescope_research_handler


def test_phagescope_research_available_to_plan_executor() -> None:
    from app.services.tool_schemas import EXECUTOR_AVAILABLE_TOOLS, build_executor_tool_schemas

    assert "phagescope_research" in EXECUTOR_AVAILABLE_TOOLS
    executor_tool_names = {
        schema.get("function", {}).get("name")
        for schema in build_executor_tool_schemas()
    }
    assert "phagescope_research" in executor_tool_names


def _write_metadata(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "Phage_ID": "p1",
            "Length": "1000",
            "GC_content": "45.1",
            "Taxonomy": "Caudoviricetes",
            "Completeness": "High-quality",
            "Lifestyle": "Virulent",
            "Cluster": "c1",
            "Subcluster": "s1",
            "Phage_source": "TEST",
            "Host": "Escherichia coli",
        },
        {
            "Phage_ID": "p2",
            "Length": "1200",
            "GC_content": "46.2",
            "Taxonomy": "Caudoviricetes",
            "Completeness": "Medium-quality",
            "Lifestyle": "Temperate",
            "Cluster": "c1",
            "Subcluster": "s1",
            "Phage_source": "TEST",
            "Host": "Escherichia albertii",
        },
        {
            "Phage_ID": "p3",
            "Length": "1300",
            "GC_content": "47.3",
            "Taxonomy": "Caudoviricetes",
            "Completeness": "High-quality",
            "Lifestyle": "",
            "Cluster": "c2",
            "Subcluster": "s2",
            "Phage_source": "TEST",
            "Host": "Escherichia fergusonii",
        },
        {
            "Phage_ID": "p4",
            "Length": "900",
            "GC_content": "44.0",
            "Taxonomy": "Caudoviricetes",
            "Completeness": "Low-quality",
            "Lifestyle": "Virulent",
            "Cluster": "c3",
            "Subcluster": "s3",
            "Phage_source": "TEST",
            "Host": "Salmonella enterica",
        },
        {
            "Phage_ID": "p5",
            "Length": "1100",
            "GC_content": "48.0",
            "Taxonomy": "Caudoviricetes",
            "Completeness": "High-quality",
            "Lifestyle": "Virulent",
            "Cluster": "c4",
            "Subcluster": "s4",
            "Phage_source": "TEST",
            "Host": "unknown",
        },
    ]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]), delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def test_phagescope_research_audit_and_prepare_metadata_table(tmp_path: Path) -> None:
    data_dir = tmp_path / "phagescope"
    _write_metadata(data_dir / "meta_data" / "test_phage_meta_data.tsv")
    output_dir = tmp_path / "out"

    audit = asyncio.run(
        phagescope_research_handler(
            action="audit",
            data_dir=str(data_dir),
            top_n=3,
        )
    )

    assert audit["success"] is True
    assert audit["metadata_rows"] == 5
    assert audit["unique_phage_ids"] == 5
    assert audit["code_executor_add_dirs"] == [str(data_dir), str(data_dir.resolve())]

    prepared = asyncio.run(
        phagescope_research_handler(
            action="prepare_metadata_table",
            data_dir=str(data_dir),
            output_dir=str(output_dir),
            label_level="genus",
            min_label_count=2,
            split_group="subcluster",
        )
    )

    assert prepared["success"] is True
    assert prepared["rows_written"] == 3
    assert prepared["labels_kept"] == 1
    assert prepared["split_group"] == "subcluster"
    table_path = Path(prepared["output_table"])
    assert table_path.exists()
    assert Path(prepared["canonical_output_table"]).name == "curated_metadata.tsv"
    assert Path(prepared["canonical_output_table"]).exists()
    assert Path(prepared["canonical_label_counts_path"]).name == "label_counts.tsv"
    assert Path(prepared["canonical_label_counts_path"]).exists()
    assert Path(prepared["canonical_summary_path"]).name == "metadata_summary.json"
    assert Path(prepared["canonical_summary_path"]).exists()
    assert str(Path(prepared["canonical_output_table"])) in prepared["artifact_paths"]
    with table_path.open("r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))
    assert {row["Host_label"] for row in rows} == {"Escherichia"}
    assert all(row["Split"] in {"train", "val", "test"} for row in rows)


def test_qwen_mounts_keep_symlink_alias_and_resolved_target(tmp_path: Path) -> None:
    parent = tmp_path / "repo"
    real_data = tmp_path / "real_phagescope"
    work_dir = tmp_path / "work"
    parent.mkdir()
    real_data.mkdir()
    work_dir.mkdir()
    phagescope_link = parent / "phagescope"
    phagescope_link.symlink_to(real_data, target_is_directory=True)

    mounts = _build_qwen_container_mounts(
        task_work_dir=work_dir,
        session_dir=work_dir,
        allowed_dirs=[str(parent), str(phagescope_link), str(real_data)],
    )

    assert (str(phagescope_link), str(phagescope_link)) in mounts
    assert (str(real_data.resolve()), str(real_data.resolve())) in mounts
