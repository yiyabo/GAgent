"""Native (host + Slurm) execution of bio_tools: request validation, the runner, and the handler modes.

The runner is exercised against fake ``sbatch``/``squeue``/``sacct``/``scancel`` shims, so the whole
hand-off (request -> script -> status -> result) runs without Slurm or Lmod.
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import json
import os
import re
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

import pytest

from tool_box.bio_tools import native_client, native_exec, native_runner

handler = importlib.import_module("tool_box.bio_tools.bio_tools_handler")
BIO_DIR = Path(native_exec.__file__).parent
REAL_CONFIG = native_exec.load_tools_config(str(BIO_DIR / "tools_config.json"))


# --------------------------------------------------------------------------- the files the runner ships as

@pytest.mark.parametrize("name", ["native_exec.py", "native_runner.py"])
def test_host_side_files_stay_python38_and_standalone(name: str) -> None:
    source = (BIO_DIR / name).read_text(encoding="utf-8")
    ast.parse(source, feature_version=(3, 8))
    # The runner is installed as loose files on a host with an old system Python.
    probe = "import native_exec" if name == "native_exec.py" else "import native_runner"
    done = subprocess.run(
        [sys.executable, "-c", probe], cwd=str(BIO_DIR), capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": ""},
    )
    assert done.returncode == 0, done.stderr


# --------------------------------------------------------------------------- build_job

@pytest.fixture()
def dirs(tmp_path: Path) -> Dict[str, Path]:
    read_root = tmp_path / "runtime"
    work = read_root / "bio_tools" / "_native" / "runs" / "aaaaaaaaaaaa" / "work"
    work.mkdir(parents=True)
    (read_root / "session_x").mkdir()
    query = read_root / "session_x" / "q.fasta"
    query.write_text(">a\nACGT\n")
    (read_root / "session_x" / "db").mkdir()
    return {"read_root": read_root, "work": work, "query": query, "tmp": tmp_path}


def _job(dirs, tool, op, **kw):
    kw.setdefault("input_file", None)
    kw.setdefault("output_file", None)
    kw.setdefault("params", {})
    kw.setdefault("timeout", None)
    return native_exec.build_job(
        REAL_CONFIG, tool=tool, operation=op, work_dir=str(dirs["work"]), read_roots=[str(dirs["read_root"])], **kw
    )


def test_blastn_renders_host_paths_and_loads_the_module(dirs) -> None:
    job = _job(dirs, "blast", "blastn", input_file=str(dirs["query"]),
               params={"db": str(dirs["read_root"] / "session_x" / "db" / "viral"), "output": "hits.tsv"})
    work = os.path.realpath(dirs["work"])
    assert job["command"] == (
        f"blastn -query {os.path.realpath(dirs['query'])} "
        f"-db {os.path.realpath(dirs['read_root'])}/session_x/db/viral -out {work}/hits.tsv -outfmt 6"
    )
    assert job["modules"] == ["BLAST+/2.12.0"]
    assert job["output_path"] == f"{work}/hits.tsv"
    assert "/work" not in job["command"].replace(work, "")


def test_makeblastdb_reports_the_prefix_a_later_step_should_use(dirs) -> None:
    job = _job(dirs, "blast", "makeblastdb", input_file=str(dirs["query"]), params={"type": "nucl", "db": "mydb"})
    assert job["output_path"] == f"{os.path.realpath(dirs['work'])}/mydb"
    assert f"-out {os.path.realpath(dirs['work'])}/mydb" in job["command"]


def test_samtools_threads_are_clamped_to_the_tool_limit(dirs) -> None:
    job = _job(dirs, "samtools", "sort", input_file=str(dirs["query"]), params={"threads": "999", "output": "s.bam"})
    assert job["cpus"] == native_exec.DEFAULT_MAX_CPUS
    assert "-@ 32 " in job["command"]


def test_minimap2_takes_two_input_paths_and_a_free_text_preset(dirs) -> None:
    job = _job(dirs, "minimap2", "map", input_file=str(dirs["query"]),
               params={"reference": str(dirs["query"]), "query": str(dirs["query"]), "preset": "map-ont",
                       "output": "o.sam"})
    assert "-ax map-ont " in job["command"] and job["command"].endswith("/o.sam")


@pytest.mark.parametrize(
    "tool,op,kwargs,code",
    [
        ("blast", "blastn", {"input_file": "/etc/passwd", "params": {"db": "/x/db", "output": "o"}}, "path_not_allowed"),
        ("blast", "blastn", {"params": {"db": "/x", "output": "o"}}, "missing_required_params"),
        ("samtools", "sort", {"input_file": "{q}", "params": {"output": "../escape.bam", "threads": "2"}}, "invalid_parameter"),
        ("samtools", "sort", {"input_file": "{q}", "params": {"output": "/tmp/escape.bam", "threads": "2"}}, "invalid_parameter"),
        ("samtools", "sort", {"input_file": "{q}", "params": {"output": "a; rm -rf /", "threads": "2"}}, "invalid_parameter"),
        ("samtools", "sort", {"input_file": "{q}", "params": {"output": "o.bam", "evil": "1", "threads": "2"}}, "invalid_parameter"),
        ("samtools", "sort", {"input_file": "{q}", "params": {"output": "o.bam", "threads": "x"}}, "invalid_parameter"),
        ("nextflow", "clean", {}, "not_native"),
        ("nope", "x", {}, "unknown_tool"),
        ("samtools", "sort", {"input_file": "relative/q.bam", "params": {"output": "o.bam", "threads": "2"}}, "invalid_parameter"),
    ],
)
def test_requests_that_must_not_run_are_refused(dirs, tool, op, kwargs, code) -> None:
    kwargs = json.loads(json.dumps(kwargs).replace("{q}", str(dirs["query"])))
    with pytest.raises(native_exec.NativeError) as err:
        _job(dirs, tool, op, **kwargs)
    assert err.value.code == code


def test_a_symlink_out_of_the_allowed_roots_is_not_followed(dirs) -> None:
    secret = dirs["tmp"] / "secret.txt"
    secret.write_text("x")
    link = dirs["read_root"] / "session_x" / "link.fasta"
    link.symlink_to(secret)
    with pytest.raises(native_exec.NativeError) as err:
        _job(dirs, "samtools", "sort", input_file=str(link), params={"output": "o.bam", "threads": "2"})
    assert err.value.code == "path_not_allowed"


def test_script_loads_modules_and_records_the_exit_code(dirs) -> None:
    job = _job(dirs, "samtools", "sort", input_file=str(dirs["query"]), params={"output": "s.bam", "threads": "2"})
    script = native_exec.render_script(job, run_dir="/runs/x")
    assert script.startswith("#!/bin/bash -l")
    assert "module load SAMtools/1.15.1" in script
    assert "umask 000" in script and "trap 'echo $? > /runs/x/exit_code' EXIT" in script


def test_script_exports_cpus_and_activates_the_conda_env_from_the_configured_dir(dirs) -> None:
    job = _job(dirs, "seqkit", "stats", input_file=str(dirs["query"]))
    script = native_exec.render_script(
        job, run_dir="/runs/x", conda_base="/opt/conda", conda_envs_dir="/data/envs"
    )
    lines = script.splitlines()
    assert "export GAGENT_CPUS=2 OMP_NUM_THREADS=2" in lines
    assert lines.index("export CONDA_ENVS_PATH=/data/envs") < next(
        i for i, line in enumerate(lines) if "conda activate seqkit" in line
    )
    assert lines.index("unset PYTHONPATH PYTHONHOME PYTHONSTARTUP PERL5LIB") < next(
        i for i, line in enumerate(lines) if "conda activate seqkit" in line
    )
    assert native_exec.render_script(job, run_dir="/r", conda_base="/opt/conda").count("CONDA_ENVS_PATH") == 0


def test_a_system_program_is_checked_before_the_command_runs(dirs) -> None:
    job = _job(dirs, "prodigal", "meta", input_file=str(dirs["query"]), params={"protein": "p.faa"})
    script = native_exec.render_script(job, run_dir="/runs/x")
    assert "command -v prodigal" in script and "exit 95" in script
    assert script.index("command -v prodigal") < script.index("prodigal -i")


def test_native_command_passes_the_cpu_allocation_where_docker_relies_on_defaults(dirs) -> None:
    job = _job(dirs, "flye", "ont", input_file=str(dirs["query"]), params={"output": "asm"})
    assert "--threads $GAGENT_CPUS" in job["command"]
    assert job["cpus"] == 16
    docker = REAL_CONFIG["flye"]["operations"]["ont"]["command"]
    assert "GAGENT_CPUS" not in docker


def test_a_database_tool_says_so_when_its_database_is_not_installed(dirs) -> None:
    with pytest.raises(native_exec.NativeError) as err:
        _job(dirs, "genomad", "end_to_end", input_file=str(dirs["query"]), params={"output": "o"})
    assert err.value.code == "database_unavailable"


def test_a_bad_path_tools_entry_is_a_config_error() -> None:
    with pytest.raises(native_exec.NativeError) as err:
        native_exec.validate_native_spec("x", {"path_tools": ["ok", "bad name; rm"]})
    assert err.value.code == "bad_config"


def test_native_commands_use_the_flags_the_installed_versions_accept() -> None:
    """Found by running each tool on 620b; the Docker templates for these were wrong or silent on failure."""
    ops = {(t, o): REAL_CONFIG[t]["operations"][o]["native_command"]
           for t, o in (("checkm", "lineage_wf"), ("checkm", "qa"), ("vibrant", "run"), ("concoct", "bin"))}
    assert "--tab_table" in ops[("checkm", "lineage_wf")] and "--tabulated" not in ops[("checkm", "lineage_wf")]
    assert "--tab_table" in ops[("checkm", "qa")]
    assert ops[("checkm", "lineage_wf")].startswith("CHECKM_DATA_PATH=/work/database ")
    # VIBRANT's -o is an ORF count, the output folder is -folder; it also exits 0 on failure.
    assert "-folder /work/{output}" in ops[("vibrant", "run")] and " -o " not in ops[("vibrant", "run")]
    assert "test -n" in ops[("vibrant", "run")]
    gtdbtk = REAL_CONFIG["gtdbtk"]["operations"]["classify_wf"]["native_command"]
    assert "--skip_ani_screen" in gtdbtk and gtdbtk.startswith("GTDBTK_DATA_PATH=/work/database ")  # 2.3.2 refuses to run without one of --skip_ani_screen/--mash_db
    assert "--composition_file {contigs}" in ops[("concoct", "bin")] and "--contigs_file" not in ops[("concoct", "bin")]


def test_every_native_operation_in_the_shipped_config_renders(dirs) -> None:
    """Every operation that opts into native execution must produce a command from plausible values."""
    database = dirs["tmp"] / "db"
    database.mkdir()
    for name in REAL_CONFIG:
        spec = native_exec.native_spec(REAL_CONFIG, name)
        if not spec:
            continue
        for op_name, op in REAL_CONFIG[name]["operations"].items():
            if not native_exec.supports_native(REAL_CONFIG, name, op_name):
                continue
            params: Dict[str, Any] = {}
            template = op.get("native_command") or op["command"]
            written = set(re.findall(r"/work/\{(\w+)\}", template))
            for key, meta in (op.get("extra_params") or {}).items():
                if key in native_exec.INPUT_PATH_PARAM_KEYS and key not in written:
                    params[key] = str(dirs["query"])
                elif meta.get("type") == "integer":
                    params[key] = 2
                elif key in ("fraction", "min_seq_id", "min_score", "quality"):
                    params[key] = "0.5"
                else:
                    params[key] = "x1"
            job = native_exec.build_job(
                REAL_CONFIG, tool=name, operation=op_name, input_file=str(dirs["query"]), output_file=None,
                params=params, timeout=None, work_dir=str(dirs["work"]), read_roots=[str(dirs["read_root"])],
                database_path=str(database),
            )
            assert job["command"].strip(), (name, op_name)
            assert "{" not in job["command"] and "/work/" not in job["command"].replace(job["work_dir"], ""), (
                name, op_name, job["command"])
            native_exec.render_script(job, run_dir="/runs/x", conda_base="/opt/conda", conda_envs_dir="/envs")


def test_every_native_block_in_the_shipped_config_is_valid() -> None:
    names = [t for t in REAL_CONFIG if native_exec.native_spec(REAL_CONFIG, t)]
    assert {"blast", "samtools", "minimap2"} <= set(names)
    for name in names:
        native_exec.validate_native_spec(name, native_exec.native_spec(REAL_CONFIG, name))


# --------------------------------------------------------------------------- runner + client with fake Slurm

FAKE_SBATCH = """#!/bin/bash
d="$FAKE_SLURM_DIR"
n=$(( $(cat "$d/next" 2>/dev/null || echo 4000) + 1 )); echo $n > "$d/next"
out=; err=; chd=.
for a in "$@"; do case "$a" in --output=*) out=${a#--output=};; --error=*) err=${a#--error=};; --chdir=*) chd=${a#--chdir=};; esac; done
script="${@: -1}"
echo "$*" > "$d/sbatch.$n.args"
if [ -f "$d/hold" ]; then echo RUNNING > "$d/state.$n"
else (cd "$chd" && bash "$script" > "$out" 2> "$err") && echo COMPLETED > "$d/state.$n" || echo FAILED > "$d/state.$n"; fi
echo $n
"""
FAKE_SQUEUE = """#!/bin/bash
d="$FAKE_SLURM_DIR"; ids=
while [ $# -gt 0 ]; do [ "$1" = "-j" ] && ids="$2"; shift; done
for id in ${ids//,/ }; do [ "$(cat "$d/state.$id" 2>/dev/null)" = RUNNING ] && echo "$id|RUNNING"; done; exit 0
"""
FAKE_SACCT = """#!/bin/bash
d="$FAKE_SLURM_DIR"; id=
while [ $# -gt 0 ]; do [ "$1" = "-j" ] && id="$2"; shift; done
cat "$d/state.$id" 2>/dev/null; exit 0
"""
FAKE_SCANCEL = """#!/bin/bash
d="$FAKE_SLURM_DIR"; echo CANCELLED > "$d/state.$1"; echo "$1" >> "$d/scancel.log"
"""

TEST_CONFIG = {
    "fake": {
        "image": "none", "description": "t", "category": "t",
        "native": {"modules": [], "path_tools": ["cat"], "cpus": 1, "mem_mb": 64},
        "operations": {
            "copy": {"command": "cat {input} > /work/{output}", "requires_input": True,
                     "extra_params": {"output": {"type": "string", "required": False}}},
            "fail": {"command": "echo boom >&2; exit 3", "requires_input": False},
        },
    }
}


@pytest.fixture()
def slurm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    bin_dir = tmp_path / "fakebin"
    state = tmp_path / "slurmstate"
    bin_dir.mkdir()
    state.mkdir()
    for name, body in (("sbatch", FAKE_SBATCH), ("squeue", FAKE_SQUEUE), ("sacct", FAKE_SACCT), ("scancel", FAKE_SCANCEL)):
        path = bin_dir / name
        path.write_text(body)
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("FAKE_SLURM_DIR", str(state))
    monkeypatch.setenv("BIO_TOOLS_NATIVE_POLL_INTERVAL", "0.05")
    return {"bin": bin_dir, "state": state}


class RunnerThread:
    def __init__(self, runner: native_runner.Runner) -> None:
        self.runner, self.stop = runner, [False]
        runner.ensure_dirs()
        self.thread = threading.Thread(target=lambda: runner.serve(0.05, self.stop), daemon=True)

    def __enter__(self) -> "RunnerThread":
        self.thread.start()
        deadline = time.time() + 5
        while time.time() < deadline and not os.path.exists(native_exec.heartbeat_path(self.runner.runtime_dir)):
            time.sleep(0.02)
        return self

    def __exit__(self, *exc: Any) -> None:
        self.stop[0] = True
        self.thread.join(timeout=5)


@pytest.fixture()
def world(tmp_path: Path, slurm, monkeypatch: pytest.MonkeyPatch):
    runtime = tmp_path / "runtime"
    bio = runtime / "bio_tools"
    bio.mkdir(parents=True)
    config_path = tmp_path / "tools_config.json"
    config_path.write_text(json.dumps(TEST_CONFIG))
    runner = native_runner.Runner(
        str(bio), str(config_path), [str(runtime)],
        sbatch=str(slurm["bin"] / "sbatch"), squeue=str(slurm["bin"] / "squeue"),
        sacct=str(slurm["bin"] / "sacct"), scancel=str(slurm["bin"] / "scancel"),
    )
    return {"runtime": runtime, "bio": bio, "runner": runner, "config": TEST_CONFIG, "slurm": slurm}


def _run(world, operation="copy", input_file=None, params=None, **kw):
    mapping = native_client.PathMap([(str(world["runtime"]), str(world["runtime"]))])
    return asyncio.run(native_client.run_native_bio_tool(
        config=world["config"], runtime_dir=str(world["bio"]), container_runtime_root=str(world["runtime"]),
        tool_name="fake", operation=operation, input_file=input_file, output_file=kw.pop("output_file", None),
        params=params or {}, timeout=kw.pop("timeout", None), path_map=mapping, **kw,
    ))


def test_a_run_goes_request_to_slurm_to_result(world) -> None:
    source = world["runtime"] / "session_a" / "in.txt"
    source.parent.mkdir()
    source.write_text("hello native\n")
    logs: List[str] = []
    with RunnerThread(world["runner"]):
        result = _run(world, input_file=str(source), params={"output": "out.txt"},
                      log_callback=lambda line, stream: logs.append(line))
    assert result["success"] is True, result
    assert result["execution_mode"] == "native" and result["state"] == "succeeded"
    assert Path(result["output_path"]).read_text() == "hello native\n"
    assert result["exit_code"] == 0 and result["slurm_job_id"] == "4001"
    run_dir = Path(result["work_dir"]).parent
    args = (world["slurm"]["state"] / "sbatch.4001.args").read_text()
    assert "--cpus-per-task=1" in args and "--mem=64M" in args and "--export=NONE" in args
    # The last argument is the script itself, with nothing in front of it that sbatch would take for the script.
    assert args.split()[-1] == str(run_dir / "job.sh") or args.split()[-1] == os.path.realpath(run_dir / "job.sh")
    assert " bash " not in args
    assert f"--chdir={os.path.realpath(run_dir / 'work')}" in args
    assert (run_dir / "job.sh").read_text().startswith("#!/bin/bash -l")
    assert not any(p.suffix == ".tmp" for p in run_dir.iterdir())


def test_a_failing_tool_is_reported_as_failed_with_its_stderr(world) -> None:
    with RunnerThread(world["runner"]):
        result = _run(world, operation="fail")
    assert result["success"] is False and result["state"] == "failed"
    assert result["exit_code"] == 3 and "boom" in result["stderr"]


def test_the_runner_refuses_a_request_the_app_side_would_have_let_through(world) -> None:
    """The runner re-validates: a hand-written request cannot read outside its roots."""
    run_id = "bbbbbbbbbbbb"
    run_dir = Path(native_exec.runs_root(str(world["bio"]))) / run_id
    run_dir.mkdir(parents=True)
    native_exec.write_json_atomic(str(run_dir / "request.json"), {
        "version": 1, "run_id": run_id, "tool": "fake", "operation": "copy",
        "input_file": "/etc/passwd", "params": {"output": "o"},
    })
    with RunnerThread(world["runner"]):
        deadline = time.time() + 5
        while time.time() < deadline and not (run_dir / "status.json").exists():
            time.sleep(0.02)
    status = native_exec.read_json(str(run_dir / "status.json"))
    assert status["state"] == "rejected" and status["error_code"] == "path_not_allowed"
    assert not list(world["slurm"]["state"].glob("sbatch.*"))


def test_the_runner_ignores_a_request_whose_id_does_not_match_its_directory(world) -> None:
    run_dir = Path(native_exec.runs_root(str(world["bio"]))) / "cccccccccccc"
    run_dir.mkdir(parents=True)
    native_exec.write_json_atomic(str(run_dir / "request.json"), {
        "version": 1, "run_id": "dddddddddddd", "tool": "fake", "operation": "fail", "params": {},
    })
    with RunnerThread(world["runner"]):
        time.sleep(0.4)
    assert native_exec.read_json(str(run_dir / "status.json"))["state"] == "rejected"


def test_without_a_runner_the_app_says_so_and_allows_a_fallback(world) -> None:
    result = _run(world, operation="fail")
    assert result["success"] is False
    assert result["error_code"] == "native_runner_unavailable" and result["fallback_ok"] is True


def test_a_stale_heartbeat_counts_as_no_runner(world) -> None:
    world["runner"].ensure_dirs()
    native_exec.write_json_atomic(native_exec.heartbeat_path(str(world["bio"])), {"ts": time.time() - 600})
    assert "has not reported" in (native_client.runner_unavailable_reason(str(world["bio"])) or "")


def test_cancelling_the_app_side_wait_cancels_the_slurm_job(world) -> None:
    (world["slurm"]["state"] / "hold").write_text("")

    async def scenario() -> None:
        mapping = native_client.PathMap([(str(world["runtime"]), str(world["runtime"]))])
        task = asyncio.ensure_future(native_client.run_native_bio_tool(
            config=world["config"], runtime_dir=str(world["bio"]), container_runtime_root=str(world["runtime"]),
            tool_name="fake", operation="fail", input_file=None, output_file=None, params={}, timeout=None,
            path_map=mapping))
        await asyncio.sleep(1.0)  # submitted and running
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.8)

    with RunnerThread(world["runner"]):
        asyncio.run(scenario())
    assert (world["slurm"]["state"] / "scancel.log").read_text().strip() == "4001"
    runs = list(Path(native_exec.runs_root(str(world["bio"]))).iterdir())
    assert native_exec.read_json(str(runs[0] / "status.json"))["state"] == "cancelled"


def test_the_runner_resumes_runs_that_were_submitted_before_it_restarted(world) -> None:
    (world["slurm"]["state"] / "hold").write_text("")
    with RunnerThread(world["runner"]):
        async def submit_only() -> str:
            mapping = native_client.PathMap([(str(world["runtime"]), str(world["runtime"]))])
            task = asyncio.ensure_future(native_client.run_native_bio_tool(
                config=world["config"], runtime_dir=str(world["bio"]),
                container_runtime_root=str(world["runtime"]), tool_name="fake", operation="fail",
                input_file=None, output_file=None, params={}, timeout=None, path_map=mapping))
            await asyncio.sleep(0.8)
            run_id = next(Path(native_exec.runs_root(str(world["bio"]))).iterdir()).name
            task.cancel()
            return run_id
        run_id = asyncio.run(submit_only())
    # A new runner process finds the unfinished run in status.json and keeps tracking it.
    fresh = native_runner.Runner(
        str(world["bio"]), str(world["runner"].config_path), [str(world["runtime"])],
        sbatch=world["runner"].bins["sbatch"], squeue=world["runner"].bins["squeue"],
        sacct=world["runner"].bins["sacct"], scancel=world["runner"].bins["scancel"])
    fresh.ensure_dirs()
    fresh.recover()
    assert run_id in fresh.active or native_exec.read_json(
        os.path.join(native_exec.runs_root(str(world["bio"])), run_id, "status.json"))["state"] == "cancelled"


# --------------------------------------------------------------------------- path map

def test_path_map_translates_container_paths_both_ways() -> None:
    pm = native_client.PathMap([("/app/runtime", "/home/u/gagent/runtime"), ("/data", "/home/u/gagent/refdata")])
    assert pm.to_host("/app/runtime/session_1/a.fa") == "/home/u/gagent/runtime/session_1/a.fa"
    assert pm.to_host("/data/ref.fa") == "/home/u/gagent/refdata/ref.fa"
    assert pm.to_host("/home/u/gagent/runtime/x") == "/home/u/gagent/runtime/x"
    assert pm.to_container("/home/u/gagent/runtime/bio_tools/_native/runs/r/work/o") == (
        "/app/runtime/bio_tools/_native/runs/r/work/o")
    with pytest.raises(native_exec.NativeError):
        pm.to_host("/etc/passwd")
    with pytest.raises(native_exec.NativeError):
        pm.to_host("/app/runtimeevil/x")


# --------------------------------------------------------------------------- handler modes

def _patch_modes(monkeypatch, native_result):
    calls: List[str] = []

    async def fake_native(**kwargs):
        calls.append("native")
        return dict(native_result)

    async def fake_local(**kwargs):
        calls.append("local")
        return {"success": True, "execution_mode": "local", "execution_host": "local"}

    monkeypatch.setattr(handler, "_execute_native_bio_tool", fake_native)
    monkeypatch.setattr(handler, "_execute_local_bio_tool", fake_local)
    return calls


def _call(**kwargs):
    return asyncio.run(handler._execute_bio_tool_once(
        tool_name=kwargs.get("tool", "samtools"), operation=kwargs.get("op", "sort"), input_file="/x/in.bam",
        output_file=None, params={"output": "o.bam"}, timeout=None))


def test_native_mode_never_falls_back_to_docker(monkeypatch) -> None:
    monkeypatch.setenv("BIO_TOOLS_EXECUTION_MODE", "native")
    calls = _patch_modes(monkeypatch, {"success": False, "error": "no runner", "fallback_ok": True})
    result = _call()
    assert result["success"] is False and calls == ["native"]


def test_native_first_falls_back_to_docker_only_when_native_cannot_take_the_run(monkeypatch) -> None:
    monkeypatch.setenv("BIO_TOOLS_EXECUTION_MODE", "native_first")
    calls = _patch_modes(monkeypatch, {"success": False, "error": "no runner", "fallback_ok": True})
    assert _call()["execution_mode"] == "local" and calls == ["native", "local"]
    calls = _patch_modes(monkeypatch, {"success": False, "error_code": "not_native", "error": "x"})
    assert _call()["execution_mode"] == "local" and calls == ["native", "local"]


def test_native_first_reports_a_native_failure_instead_of_rerunning_it_in_docker(monkeypatch) -> None:
    monkeypatch.setenv("BIO_TOOLS_EXECUTION_MODE", "native_first")
    calls = _patch_modes(monkeypatch, {"success": False, "error": "samtools failed", "state": "failed"})
    result = _call()
    assert result["success"] is False and calls == ["native"]


def test_list_and_help_say_which_operations_are_native() -> None:
    listing = asyncio.run(handler.bio_tools_handler(tool_name="list", operation="list"))
    by_name = {t["name"]: t for t in listing["tools"]}
    assert "blastn" in by_name["blast"]["native_operations"]
    assert "native_operations" not in by_name["nextflow"]
    helped = asyncio.run(handler.bio_tools_handler(tool_name="samtools", operation="help"))
    assert "sort" in helped["native_operations"]


# --------------------------------------------------------------------------- what the model is offered

def _caps_config() -> Dict[str, Any]:
    return {
        "dbtool": {"native": {"conda_env": "dbenv", "database": {"subdir": "dbtool"}}, "operations": {
            "scan": {"command": "dbtool {input} /work/database"},
            "check": {"command": "dbtool check {input}"},
        }},
        "sys": {"native": {"path_tools": ["prodigal"]}, "operations": {"run": {"command": "prodigal -i {input}"}}},
        "dockeronly": {"operations": {"run": {"command": "x {input}"}}},
    }


def test_capabilities_say_why_an_operation_cannot_run(tmp_path: Path) -> None:
    envs = tmp_path / "envs"
    (envs / "dbenv" / "conda-meta").mkdir(parents=True)
    config = _caps_config()
    ready = native_exec.compute_capabilities(
        config, database_for=lambda tool: None, conda_envs_dir=str(envs),
        which=lambda program: "/usr/bin/" + program,
    )
    assert ready == {"dbtool": {"scan": "database_unavailable", "check": None}, "sys": {"run": None}}
    missing = native_exec.compute_capabilities(
        config, database_for=lambda tool: "/db", conda_envs_dir=str(tmp_path / "nowhere"), which=lambda program: None,
    )
    assert missing == {"dbtool": {"scan": "environment_not_installed", "check": "environment_not_installed"},
                       "sys": {"run": "program_not_installed"}}


def test_the_runner_reports_its_capabilities_in_the_heartbeat(world) -> None:
    world["runner"].ensure_dirs()
    world["runner"].heartbeat(force=True)
    caps = native_exec.read_capabilities(str(world["bio"]))
    assert caps == {"fake": {op: None for op in world["config"]["fake"]["operations"]}}


def _native_mode(monkeypatch, tmp_path: Path, capabilities) -> None:
    runtime = tmp_path / "bio"
    runtime.mkdir(exist_ok=True)
    (runtime / "_native").mkdir(exist_ok=True)
    if capabilities is not None:
        (runtime / "_native" / "runner.json").write_text(json.dumps({"ts": time.time(), "capabilities": capabilities}))
    monkeypatch.setenv("BIO_TOOLS_EXECUTION_MODE", "native")
    monkeypatch.setattr(handler, "_native_runtime_dir", lambda: str(runtime))


def test_native_mode_lists_only_what_the_server_can_run(monkeypatch, tmp_path: Path) -> None:
    caps = {"genomad": {"end_to_end": "database_unavailable", "annotate": "database_unavailable",
                        "find_proviruses": "database_unavailable"},
            "seqkit": {"stats": None, "grep": None, "seq": None, "head": None}}
    _native_mode(monkeypatch, tmp_path, caps)
    listing = asyncio.run(handler.bio_tools_handler(tool_name="list", operation="list"))
    names = {t["name"] for t in listing["tools"]}
    assert "seqkit" in names and "nextflow" not in names and "dorado" not in names
    assert "genomad" not in names
    assert listing["unavailable_tools"]["genomad"]["end_to_end"] == "database_unavailable"
    blast = next(t for t in listing["tools"] if t["name"] == "blast")
    assert blast["operations"] == blast["native_operations"]


def test_native_mode_help_hides_operations_that_cannot_run(monkeypatch, tmp_path: Path) -> None:
    caps = {"checkv": {"end_to_end": "database_unavailable", "completeness": "database_unavailable",
                       "complete_genomes": None}}
    _native_mode(monkeypatch, tmp_path, caps)
    helped = asyncio.run(handler.bio_tools_handler(tool_name="checkv", operation="help"))
    assert list(helped["operations"]) == ["complete_genomes"]
    assert helped["unavailable_operations"]["end_to_end"] == "database_unavailable"
    assert "image" not in helped
    gone = asyncio.run(handler.bio_tools_handler(
        tool_name="genomad", operation="help"))
    assert gone["success"] is True  # no report for genomad: shown, the call itself will say why not
    caps = {"genomad": {"end_to_end": "database_unavailable", "annotate": "database_unavailable",
                        "find_proviruses": "database_unavailable"}}
    _native_mode(monkeypatch, tmp_path, caps)
    refused = asyncio.run(handler.bio_tools_handler(tool_name="genomad", operation="help"))
    assert refused["success"] is False and refused["error_code"] == "tool_unavailable"
    assert "genomad" not in refused["available_tools"]


def test_native_mode_without_a_runner_report_still_offers_the_native_tools(monkeypatch, tmp_path: Path) -> None:
    _native_mode(monkeypatch, tmp_path, None)
    listing = asyncio.run(handler.bio_tools_handler(tool_name="list", operation="list"))
    names = {t["name"] for t in listing["tools"]}
    assert "genomad" in names and "nextflow" not in names


def test_a_native_only_tool_exists_only_where_native_execution_is_on(monkeypatch) -> None:
    assert REAL_CONFIG["phabox2"]["native_only"] is True and not REAL_CONFIG["phabox2"]["image"]
    monkeypatch.delenv("BIO_TOOLS_EXECUTION_MODE", raising=False)
    names = {t["name"] for t in asyncio.run(handler.bio_tools_handler(tool_name="list", operation="list"))["tools"]}
    assert "phabox2" not in names
    refused = asyncio.run(handler.bio_tools_handler(tool_name="phabox2", operation="help"))
    assert refused["success"] is False and "phabox2" not in refused["available_tools"]
    monkeypatch.setenv("BIO_TOOLS_EXECUTION_MODE", "native_first")
    names = {t["name"] for t in asyncio.run(handler.bio_tools_handler(tool_name="list", operation="list"))["tools"]}
    assert "phabox2" in names


def test_other_modes_list_every_tool(monkeypatch) -> None:
    monkeypatch.delenv("BIO_TOOLS_EXECUTION_MODE", raising=False)
    listing = asyncio.run(handler.bio_tools_handler(tool_name="list", operation="list"))
    assert {"nextflow", "dorado", "snakemake"} <= {t["name"] for t in listing["tools"]}
    assert "unavailable_tools" not in listing


def test_the_schema_and_prompt_catalogs_follow_native_mode() -> None:
    code = (
        "from app.services import tool_schemas as t\n"
        "from app.services.deep_think import prompts as p\n"
        "print(len(t._BIO_TOOL_NAMES), len(p._BIO_TOOLS_CATALOG), 'nextflow' in t._BIO_TOOL_NAMES, "
        "'dorado' in p._BIO_TOOLS_CATALOG_TEXT, 'genomad' in t._BIO_TOOL_NAMES)\n"
    )
    root = str(BIO_DIR.parents[1])
    for mode, expected in (("native", "30 30 False False True"), ("", "32 32 True True True"), ("native_first", "33 33 True True True")):
        done = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True,
                              env={**os.environ, "BIO_TOOLS_EXECUTION_MODE": mode})
        assert done.stdout.strip().splitlines()[-1] == expected, done.stderr[-500:]
