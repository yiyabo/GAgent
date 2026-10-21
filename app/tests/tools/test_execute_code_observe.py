"""execute_code reports every file a cell wrote, as absolute paths.

The snapshot diff only sees ``ALLOWED_EXTENSIONS``; the kernel's own list sees every extension. ``observe``
used to replace the second with the first, so a cell that wrote ``results/x.bam`` and ``results/x.bam.bai``
reported only the BAM and delivery acceptance, which looks for the index, failed on a file that existed.
A kernel list that was relative was also useless downstream: readers root a relative ``produced_files``
entry at ``/``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.services.plans.output_spec import OutputSpec, RequiredOutput, validate_output_spec
from tool_box.context import ToolContext
from tool_box.tools_impl.execute_code import artifacts, kernel as kernel_module
from tool_box.tools_impl.execute_code.tool import execute_code_handler


def _write(root: Path, rel: str, text: str = "x") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_a_non_allowed_file_beside_an_allowed_one_is_still_reported(tmp_path):
    before = artifacts.snapshot(str(tmp_path))
    bam = _write(tmp_path, "results/aligned.sorted.bam")
    _write(tmp_path, "results/aligned.sorted.bam.bai")
    _write(tmp_path, "results/aligned.sorted.stats.txt")
    kernel_listed = [
        "results/aligned.sorted.bam.bai",
        "results/aligned.sorted.bam",
        "results/aligned.sorted.stats.txt",
    ]
    result = artifacts.observe({"success": True, "produced_files": kernel_listed}, before, str(tmp_path))

    produced = result["produced_files"]
    root = tmp_path.resolve()
    assert str(root / "results/aligned.sorted.bam.bai") in produced
    assert str(root / "results/aligned.sorted.bam") in produced
    assert len(produced) == len(set(produced)) == 3
    assert all(Path(p).is_absolute() for p in produced)
    assert bam.exists()


def test_a_cell_that_only_wrote_unlisted_extensions_reports_absolute_paths(tmp_path):
    before = artifacts.snapshot(str(tmp_path))
    _write(tmp_path, "results/genes.fna")
    _write(tmp_path, "results/proteins.faa")
    result = artifacts.observe(
        {"produced_files": ["results/proteins.faa", "results/genes.fna"]}, before, str(tmp_path)
    )
    assert sorted(result["produced_files"]) == sorted(
        [str(tmp_path.resolve() / "results/proteins.faa"), str(tmp_path.resolve() / "results/genes.fna")]
    )
    assert result["artifact_observation"] == "working_directory_file_changes"


def test_a_cell_that_wrote_nothing_is_left_alone(tmp_path):
    before = artifacts.snapshot(str(tmp_path))
    result = artifacts.observe({"success": True, "output": "hi"}, before, str(tmp_path))
    assert "produced_files" not in result and "artifact_observation" not in result


def test_without_a_workspace_the_kernel_list_is_not_touched():
    result = artifacts.observe({"produced_files": ["x.txt"]}, {}, "")
    assert result == {"produced_files": ["x.txt"]}


def test_an_absolute_kernel_entry_is_kept_as_it_is(tmp_path):
    outside = str(tmp_path.resolve() / "elsewhere" / "a.faa")
    result = artifacts.observe({"produced_files": [outside, "", None, 7]}, {}, str(tmp_path))
    assert result["produced_files"] == [outside]


def _spec(*outputs: RequiredOutput) -> OutputSpec:
    return OutputSpec(required_outputs=list(outputs))


def test_a_data_output_without_extensions_accepts_what_the_bio_tools_write(tmp_path):
    files = []
    for name in ("proteins.faa", "genes.fna", "reads.fastq", "calls.vcf", "aligned.bam", "aligned.bam.bai", "ann.gff3"):
        files.append(str(_write(tmp_path, f"results/{name}", "content")))
    for name in ("proteins.faa", "aligned.bam.bai", "ann.gff3"):
        report = validate_output_spec(
            _spec(RequiredOutput(kind="data")), [str(tmp_path / "results" / name)], base_dir=tmp_path
        )
        assert report["status"] == "passed", (name, report)
    report = validate_output_spec(_spec(RequiredOutput(kind="data", min_count=len(files))), files, base_dir=tmp_path)
    assert report["status"] == "passed", report


def test_a_data_output_still_refuses_an_image(tmp_path):
    png = _write(tmp_path, "plot.png", "not really a png")
    report = validate_output_spec(_spec(RequiredOutput(kind="data")), [str(png)], base_dir=tmp_path)
    assert report["status"] == "failed"


def test_a_declared_index_is_satisfied_by_the_merged_list(tmp_path):
    """The S09 case end to end: spec asks for a BAM and its .bai, the cell wrote both by copying."""
    before = artifacts.snapshot(str(tmp_path))
    _write(tmp_path, "results/aligned.sorted.bam", "bam")
    _write(tmp_path, "results/aligned.sorted.bam.bai", "bai")
    observed = artifacts.observe(
        {"produced_files": ["results/aligned.sorted.bam.bai", "results/aligned.sorted.bam"]},
        before,
        str(tmp_path),
    )["produced_files"]
    spec = _spec(
        RequiredOutput(kind="data", extensions=[".bam"]),
        RequiredOutput(kind="data", extensions=[".bai"]),
    )
    assert validate_output_spec(spec, observed, base_dir=tmp_path)["status"] == "passed"


@pytest.fixture()
def _code_mode(monkeypatch):
    monkeypatch.setenv("CODE_MODE_ENABLED", "1")
    yield
    kernel_module.shutdown_all_kernels()


@pytest.mark.timeout(90)
def test_the_handler_reports_files_of_every_extension(tmp_path, _code_mode):
    ctx = ToolContext(session_id="observe-test", work_dir=str(tmp_path))
    code = (
        "import os\n"
        "os.makedirs('results', exist_ok=True)\n"
        "for name in ('proteins.faa', 'genes.fna', 'aligned.bam', 'aligned.bam.bai', 'stats.txt'):\n"
        "    open(os.path.join('results', name), 'w').write('x')\n"
    )
    result = asyncio.run(execute_code_handler(code=code, tool_context=ctx))
    assert result["success"] is True, result
    produced = result["produced_files"]
    root = tmp_path.resolve()
    for name in ("proteins.faa", "genes.fna", "aligned.bam", "aligned.bam.bai", "stats.txt"):
        assert str(root / "results" / name) in produced, (name, produced)
    assert all(Path(p).is_absolute() for p in produced)
