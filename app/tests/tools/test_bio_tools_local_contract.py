"""Keep the advertised local bio-tool contract executable without optional arguments."""

from __future__ import annotations

import asyncio
import importlib
import shlex
from pathlib import Path

import pytest

bio = importlib.import_module("tool_box.bio_tools.bio_tools_handler")


def _example_value(name: str):
    if name in {"count", "threads", "cores", "quality", "min_length"}:
        return 1
    if name == "type":
        return "nucl"
    if name in bio.INPUT_PATH_PARAM_KEYS:
        return "/tmp/bio_input.fa"
    if name in bio.OUTPUT_PATH_PARAM_KEYS or name == "db":
        return "bio_output"
    return "value"


def test_every_documented_operation_renders_with_only_required_parameters(tmp_path: Path) -> None:
    for tool_name, tool in bio.get_tools_config().items():
        for operation, spec in tool["operations"].items():
            raw = spec.get("extra_params", {})
            schema = raw if isinstance(raw, dict) else {name: {"required": True} for name in raw}
            required = {name: _example_value(name) for name, param in schema.items() if param.get("required")}
            argv = bio._build_docker_command_args(
                tool_name,
                operation,
                input_file="/tmp/bio_input.fa" if spec.get("requires_input") else None,
                output_file="bio_output" if spec.get("requires_output") else None,
                extra_params=required,
                work_dir_override=str(tmp_path),
                validate_input_exists=False,
                validate_database=False,
            )
            assert argv[:2] == ["docker", "run"], (tool_name, operation)


def test_optional_defaults_and_help_example_are_consistent() -> None:
    assert bio._validate_and_normalize_operation_params("samtools", "view", {"output": "a.bam"}) == {
        "output": "a.bam",
        "threads": 4,
    }
    assert (
        bio._validate_and_normalize_operation_params("samtools", "view", {"output": "a.bam", "threads": 2})["threads"]
        == "2"
    )
    help_result = asyncio.run(bio.bio_tools_handler("samtools", "help"))
    example = help_result["operations"]["view"]["example_call"]["params"]
    assert example == {"output": "..."}
    assert help_result["operations"]["view"]["params"]["threads"]["default"] == 4


def test_seqkit_exact_and_regex_operations_are_distinct() -> None:
    config = bio.get_tools_config()["seqkit"]["operations"]
    assert " -r " not in config["grep"]["command"]
    assert " -r " in config["grep_regex"]["command"]


def test_index_tools_write_to_work_directory_and_extra_inputs_are_mounted(tmp_path: Path) -> None:
    input_dir = tmp_path / "inputs"
    input_dir.mkdir()
    reference = input_dir / "ref.fa"
    reference.write_text(">a\nACGT\n", encoding="utf-8")
    bam = input_dir / "sorted.bam"
    bam.write_bytes(b"bam")
    reads_dir = tmp_path / "reads"
    reads_dir.mkdir()
    reads = reads_dir / "reads.fq"
    reads.write_text("@r\nACGT\n+\nIIII\n", encoding="utf-8")
    work = tmp_path / "work"

    sam = shlex.join(
        bio._build_docker_command_args(
            "samtools",
            "index",
            input_file=str(bam),
            work_dir_override=str(work),
        )
    )
    assert "samtools index /input/sorted.bam /work/sorted.bam.bai" in sam
    bwa = shlex.join(
        bio._build_docker_command_args(
            "bwa",
            "index",
            input_file=str(reference),
            work_dir_override=str(work),
        )
    )
    assert "bwa index -p /work/ref /input/ref.fa" in bwa

    alignment = shlex.join(
        bio._build_docker_command_args(
            "minimap2",
            "map",
            input_file=str(reads),
            work_dir_override=str(work),
            extra_params={"preset": "sr", "reference": str(reference), "query": str(reads), "output": "a.sam"},
        )
    )
    assert str(reference) not in alignment
    assert "/param_inputs/reference_0/ref.fa" in alignment
    assert "/input/reads.fq" in alignment


def test_checkv_preflight_distinguishes_incomplete_db_from_prior_stage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    database = tmp_path / "checkv_db"
    (database / "genome_db").mkdir(parents=True)
    (database / "genome_db" / "checkv_reps.fna").write_text(">a\nACGT\n", encoding="utf-8")
    monkeypatch.setenv("BIO_TOOLS_CHECKV_DB_PATH", str(database))
    with pytest.raises(bio.DatabaseUnavailableError, match="checkv_reps.dmnd"):
        bio._resolve_database_mount("checkv", validate=True, operation="completeness")

    work = tmp_path / "work"
    input_file = tmp_path / "a.fa"
    input_file.write_text(">a\nACGT\n", encoding="utf-8")
    with pytest.raises(ValueError, match="prior CheckV completeness"):
        bio._build_docker_command_args(
            "checkv",
            "complete_genomes",
            input_file=str(input_file),
            extra_params={"output": "complete"},
            work_dir_override=str(work),
        )
    (work / "complete").mkdir(parents=True)
    (work / "complete" / "completeness.tsv").write_text("header\n", encoding="utf-8")
    argv = bio._build_docker_command_args(
        "checkv",
        "complete_genomes",
        input_file=str(input_file),
        extra_params={"output": "complete"},
        work_dir_override=str(work),
    )
    assert "checkv complete_genomes /input/a.fa /work/complete" in shlex.join(argv)


def test_local_docker_mounts_use_the_host_runtime_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    container_root = tmp_path / "container_runtime"
    host_root = tmp_path / "host_runtime"
    input_dir = container_root / "session_a"
    input_dir.mkdir(parents=True)
    source = input_dir / "a.fa"
    source.write_text(">a\nACGT\n", encoding="utf-8")
    monkeypatch.setenv("APP_RUNTIME_ROOT", str(container_root))
    monkeypatch.setenv("HOST_RUNTIME_ROOT", str(host_root))
    monkeypatch.setattr(bio, "RUNTIME_BASE_DIR", str(container_root / "bio_tools"))

    argv = bio._build_docker_command_args("seqkit", "stats", input_file=str(source))
    assert f"{host_root / 'bio_tools' / 'seqkit'}:/work" in argv
    assert f"{host_root / 'session_a'}:/input:ro" in argv


def test_bio_tools_default_workdir_follows_app_runtime_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("APP_RUNTIME_ROOT", str(tmp_path / "shared_runtime"))
    assert bio._default_runtime_base_dir() == str(tmp_path / "shared_runtime" / "bio_tools")


@pytest.mark.parametrize(
    ("tool_name", "operation", "params", "made_files", "expected_output"),
    [
        ("samtools", "view", {"output": "reads.bam", "threads": 1}, ["reads.bam"], "reads.bam"),
        ("blast", "makeblastdb", {"type": "nucl", "db": "testdb"}, ["testdb.nsq", "testdb.nhr"], None),
    ],
)
def test_local_run_reports_files_it_created(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tool_name: str,
    operation: str,
    params: dict,
    made_files: list[str],
    expected_output: str | None,
) -> None:
    monkeypatch.setattr(bio, "RUNTIME_BASE_DIR", str(tmp_path))
    source = tmp_path / "source.fa"
    source.write_text(">a\nACGT\n", encoding="utf-8")

    async def fake_execute(*args, **kwargs):
        tool_dir = tmp_path / tool_name
        tool_dir.mkdir(exist_ok=True)
        for name in made_files:
            (tool_dir / name).write_text("data", encoding="utf-8")
        return {"success": True, "exit_code": 0}

    monkeypatch.setattr(bio, "execute_docker_command", fake_execute)
    result = asyncio.run(
        bio._execute_local_bio_tool(
            tool_name=tool_name,
            operation=operation,
            input_file=str(source),
            output_file=None,
            params=params,
            timeout=5,
        )
    )
    assert result["success"] is True
    assert {Path(path).name for path in result["produced_files"]} == set(made_files)
    if expected_output:
        assert Path(result["output_path"]).name == expected_output
    else:
        assert "output_path" not in result
