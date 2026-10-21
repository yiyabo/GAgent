"""Kernel filesystem/process guard: a cell gets the session boundary file_operations already enforces.

Three levels, because an audit hook cannot be removed from the interpreter that installs it:
  * the policy table is exercised in-process through ``_Policy.check`` (the hook is never installed here);
  * the hook itself runs in a throwaway interpreter;
  * the real kernel path (run_cell -> spawned runner) proves the wiring and the failure messages.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

from tool_box.context import ToolContext
from tool_box.tools_impl.execute_code import fs_guard_runtime, fs_policy
from tool_box.tools_impl.execute_code import kernel as kernel_module

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX paths and /proc")

GUARD_FILE = Path(fs_guard_runtime.__file__)


@pytest.fixture()
def project(tmp_path, monkeypatch):
    """A fake project: two sessions, the SQLite dir, a .env, source code, native bio_tools runs."""
    root = tmp_path / "proj"
    runtime = root / "runtime"
    for name in ("session_a", "session_b"):
        (runtime / name / "uploads").mkdir(parents=True)
    (runtime / "session_a" / "results").mkdir()
    (runtime / "session_a" / "uploads" / "mine.txt").write_text("mine", encoding="utf-8")
    (runtime / "session_b" / "uploads" / "theirs.txt").write_text("theirs", encoding="utf-8")
    (root / "data" / "databases").mkdir(parents=True)
    (root / "data" / "databases" / "plans.db").write_text("db", encoding="utf-8")
    (root / ".env").write_text("API_KEY=nope", encoding="utf-8")
    (root / "app").mkdir()
    (root / "app" / "main.py").write_text("print('app')", encoding="utf-8")
    run = runtime / "bio_tools" / "_native" / "runs" / "abc123def456" / "work"
    run.mkdir(parents=True)
    (run / "hits.tsv").write_text("hit", encoding="utf-8")
    monkeypatch.setenv("APP_RUNTIME_ROOT", str(runtime))
    # The temp dir is writable for cells. Keep it away from the fake project, which pytest would
    # otherwise place inside it, so "writes elsewhere are refused" is not masked by that allowance.
    scratch = tmp_path / "tmpdir"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    monkeypatch.setenv("TMPDIR", str(scratch))
    monkeypatch.delenv("CODE_MODE_ALLOW_SUBPROCESS", raising=False)
    monkeypatch.delenv("CODE_MODE_FS_GUARD", raising=False)
    return root


def _policy(project: Path, session: str = "a", **overrides) -> dict:
    runtime = project / "runtime"
    work = runtime / f"session_{session}"
    scratch = work / "scratch" / "code_mode"
    data = fs_policy.build_fs_policy(
        session_id=session,
        child_cwd=work,
        scratch_dir=scratch,
        kernel_dir=scratch / "kernels" / "k1",
    )
    data.update(overrides)
    return data


def _check(project: Path, raw, *, write=False, listing=False, session="a") -> bool:
    """True when the guard lets the access through."""
    guard = fs_guard_runtime._Policy(_policy(project, session))
    try:
        guard.check(raw, write, listing=listing)
    except PermissionError:
        return False
    return True


# ---------------------------------------------------------------------------
# Level 1: the policy table
# ---------------------------------------------------------------------------


def test_own_session_is_open_for_reading_and_writing(project):
    mine = project / "runtime" / "session_a" / "uploads" / "mine.txt"
    assert _check(project, mine)
    assert _check(project, mine, write=True)
    assert _check(project, project / "runtime" / "session_a" / "results" / "new.csv", write=True)
    assert _check(project, project / "runtime" / "session_a", listing=True)


@pytest.mark.parametrize(
    "relative",
    [
        "runtime/session_b/uploads/theirs.txt",
        "runtime/session_b",
        "data/databases/plans.db",
        "data",
        ".env",
    ],
)
def test_other_sessions_and_app_state_are_closed(project, relative):
    target = project / relative
    assert not _check(project, target)
    assert not _check(project, target, listing=True)
    assert not _check(project, target, write=True)


def test_dotdot_and_symlinks_cannot_reach_a_closed_path(project):
    session = project / "runtime" / "session_a"
    sneaky = session / "uploads" / ".." / ".." / "session_b" / "uploads" / "theirs.txt"
    assert not _check(project, sneaky)

    link = session / "uploads" / "to_theirs.txt"
    link.symlink_to(project / "runtime" / "session_b" / "uploads" / "theirs.txt")
    assert not _check(project, link)

    passwd_link = session / "uploads" / "passwd"
    passwd_link.symlink_to("/etc/passwd")
    assert not _check(project, passwd_link)


def test_host_paths_file_operations_refuses_are_refused(project):
    assert not _check(project, "/etc/passwd")
    assert not _check(project, "/etc/shadow")
    assert not _check(project, "/root/.bashrc")
    assert not _check(project, "/var/log/syslog")


def test_what_libraries_read_at_import_time_stays_readable(project):
    assert _check(project, os.__file__)
    assert _check(project, sys.executable)
    for name in ("/etc/hosts", "/etc/localtime", "/etc/ssl/certs", "/dev/null", "/dev/urandom"):
        assert _check(project, name), name


@pytest.mark.skipif(not Path("/proc/1/environ").exists(), reason="needs /proc")
def test_other_processes_proc_entries_are_closed_but_own_is_open(project):
    assert not _check(project, "/proc/1/environ")
    assert not _check(project, f"/proc/{os.getppid()}/environ")
    assert not _check(project, "/proc/1/cwd/data")
    assert _check(project, "/proc/self/environ")
    assert _check(project, f"/proc/{os.getpid()}/status")
    assert _check(project, "/proc/cpuinfo") or not Path("/proc/cpuinfo").exists()


def test_writes_outside_the_workspace_are_closed(project, tmp_path):
    assert not _check(project, project / "app" / "main.py", write=True)
    assert not _check(project, project / "app" / "new.py", write=True)
    assert not _check(project, project / "runtime" / "session_b" / "x", write=True)
    assert not _check(project, "/etc/cron.d/x", write=True)
    assert not _check(project, Path.home() / "planted", write=True)


def test_scratch_space_libraries_need_stays_writable(project):
    assert _check(project, os.path.join(tempfile.gettempdir(), "x.tmp"), write=True)
    assert _check(project, "/dev/null", write=True)


def test_native_runs_open_by_exact_path_but_cannot_be_listed(project):
    runs = project / "runtime" / "bio_tools" / "_native" / "runs"
    hits = runs / "abc123def456" / "work" / "hits.tsv"
    assert _check(project, hits)
    assert _check(project, runs / "abc123def456" / "work", listing=True)
    assert not _check(project, runs, listing=True)
    assert not _check(project, project / "runtime" / "bio_tools" / "other.txt")


def test_sqlite_of_the_app_is_closed_and_a_local_database_is_open(project):
    guard = fs_guard_runtime._Policy(_policy(project))
    with pytest.raises(PermissionError):
        guard.check(str(project / "data" / "databases" / "plans.db"), False)
    guard.check(str(project / "runtime" / "session_a" / "local.db"), False)


def test_policy_names_the_running_sessions_roots(project):
    data = _policy(project, "a")
    resolved_session = str((project / "runtime" / "session_a").resolve())
    assert resolved_session in data["rw_roots"]
    assert str((project / "runtime" / "session_b").resolve()) not in data["rw_roots"]
    assert str((project / "runtime").resolve()) in data["deny_roots"]
    assert str((project / ".env").resolve()) in data["deny_roots"]
    assert data["allow_subprocess"] is False


def test_operator_switches(project, monkeypatch):
    assert fs_policy.fs_guard_enabled() is True
    monkeypatch.setenv("CODE_MODE_FS_GUARD", "0")
    assert fs_policy.fs_guard_enabled() is False
    monkeypatch.setenv("CODE_MODE_ALLOW_SUBPROCESS", "1")
    assert _policy(project)["allow_subprocess"] is True


# ---------------------------------------------------------------------------
# Level 2: the hook, in a throwaway interpreter
# ---------------------------------------------------------------------------

_HOOK_PROBE = textwrap.dedent(
    """
    import importlib.util, json, os, sys
    spec = importlib.util.spec_from_file_location("gagent_fs_guard", sys.argv[1])
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    guard.install(sys.argv[2])
    results = {}

    def attempt(name, fn):
        try:
            fn()
            results[name] = "ok"
        except PermissionError:
            results[name] = "denied"
        except Exception as exc:
            results[name] = "other:" + type(exc).__name__

    exec(sys.argv[3])
    print(json.dumps(results))
    """
)


def _hook(project: Path, body: str, **overrides) -> dict:
    policy = _policy(project, **overrides)
    work = project / "runtime" / "session_a"
    out = subprocess.run(
        [sys.executable, "-c", _HOOK_PROBE, str(GUARD_FILE), json.dumps(policy), textwrap.dedent(body)],
        cwd=str(work),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_hook_blocks_the_calls_a_model_reaches_for(project):
    theirs = str(project / "runtime" / "session_b" / "uploads" / "theirs.txt")
    body = f"""
    import glob, os, pathlib, shutil, sqlite3
    attempt("open_other", lambda: open({theirs!r}).read())
    attempt("open_etc", lambda: open("/etc/passwd").read())
    attempt("pathlib_other", lambda: pathlib.Path({theirs!r}).read_text())
    attempt("listdir_other", lambda: os.listdir({str(project / "runtime" / "session_b")!r}))
    attempt("scandir_data", lambda: list(os.scandir({str(project / "data")!r})))
    attempt("os_open_etc", lambda: os.open("/etc/passwd", os.O_RDONLY))
    attempt("copy_other", lambda: shutil.copy({theirs!r}, "stolen.txt"))
    attempt("write_source", lambda: open({str(project / "app" / "main.py")!r}, "w"))
    attempt("sqlite_app", lambda: sqlite3.connect({str(project / "data" / "databases" / "plans.db")!r}))
    attempt("own_write", lambda: open("out.txt", "w").write("x"))
    attempt("own_read", lambda: open("out.txt").read())
    attempt("own_mkdir", lambda: os.makedirs("results/deep", exist_ok=True))
    attempt("own_rename", lambda: os.rename("out.txt", "results/out.txt"))
    attempt("own_remove", lambda: os.remove("results/out.txt"))
    attempt("glob_own", lambda: glob.glob("*"))
    """
    results = _hook(project, body)
    for name in (
        "open_other",
        "open_etc",
        "pathlib_other",
        "listdir_other",
        "scandir_data",
        "os_open_etc",
        "copy_other",
        "write_source",
        "sqlite_app",
    ):
        assert results[name] == "denied", (name, results)
    for name in ("own_write", "own_read", "own_mkdir", "own_rename", "own_remove", "glob_own"):
        assert results[name] == "ok", (name, results)
    assert not (project / "runtime" / "session_a" / "stolen.txt").exists()


def test_hook_blocks_starting_programs_but_not_forking(project):
    body = """
    import os, subprocess
    attempt("popen", lambda: subprocess.Popen(["true"]))
    attempt("run", lambda: subprocess.run(["cat", "/etc/passwd"]))
    attempt("check_output", lambda: subprocess.check_output("id", shell=True))
    attempt("system", lambda: os.system("id"))
    attempt("execv", lambda: os.execv("/bin/sh", ["sh", "-c", "id"]))
    attempt("posix_spawn", lambda: os.posix_spawn("/bin/sh", ["sh", "-c", "id"], os.environ))

    def forked():
        pid = os.fork()
        if pid == 0:
            try:
                open("/etc/passwd").read()
                os._exit(3)
            except PermissionError:
                os._exit(0)
        _, status = os.waitpid(pid, 0)
        assert os.WEXITSTATUS(status) == 0, "the forked child lost the guard"

    attempt("fork_child_keeps_guard", forked)
    """
    results = _hook(project, body)
    for name in ("popen", "run", "check_output", "system", "execv", "posix_spawn"):
        assert results[name] == "denied", (name, results)
    assert results["fork_child_keeps_guard"] == "ok", results


def test_hook_lets_an_operator_allow_subprocess(project):
    results = _hook(
        project,
        """
        import subprocess
        attempt("popen", lambda: subprocess.run(["true"], check=True))
        """,
        allow_subprocess=True,
    )
    assert results["popen"] == "ok"


def test_hook_blocks_libc_through_ctypes(project):
    results = _hook(
        project,
        """
        import ctypes
        attempt("cdll_none", lambda: ctypes.CDLL(None))
        attempt("libc", lambda: ctypes.CDLL("libc.so.6"))
        """,
    )
    assert results["cdll_none"] == "denied", results
    if results["libc"] != "other:OSError":
        assert results["libc"] == "denied", results


def test_a_broken_policy_value_does_not_crash_the_hook(project):
    results = _hook(
        project,
        """
        attempt("bytes_path", lambda: open(b"out2.txt", "wb").close())
        attempt("fd", lambda: os.close(os.open(".", os.O_RDONLY)))
        """,
    )
    assert results == {"bytes_path": "ok", "fd": "ok"}, results


# ---------------------------------------------------------------------------
# Level 3: the real kernel
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=False)
def _clean_kernels():
    yield
    kernel_module.shutdown_all_kernels()


@pytest.fixture()
def ctx(project, _clean_kernels):
    work = project / "runtime" / "session_a"
    return ToolContext(session_id="a", work_dir=str(work))


def _run(code: str, ctx: ToolContext):
    return kernel_module.run_cell(
        textwrap.dedent(code),
        session_id=str(ctx.session_id),
        work_dir=ctx.work_dir,
        reset=False,
        tool_context=ctx,
    )


@pytest.mark.timeout(120)
def test_kernel_cell_cannot_read_what_file_operations_refuses(ctx, project, monkeypatch):
    monkeypatch.setenv("PLANTED_PROVIDER_API_KEY", "sk-planted-secret-value")
    theirs = project / "runtime" / "session_b" / "uploads" / "theirs.txt"
    result = _run(
        f"""
        import os
        for label, path in [
            ("passwd", "/etc/passwd"),
            ("other_session", {str(theirs)!r}),
            ("app_db", {str(project / "data" / "databases" / "plans.db")!r}),
            ("dotenv", {str(project / ".env")!r}),
            ("parent_environ", "/proc/%d/environ" % os.getppid()),
        ]:
            if label == "parent_environ" and not os.path.exists(path):
                print(label, "DENIED", "no /proc on this platform")
                continue
            try:
                print(label, "READ", open(path, "rb").read()[:20])
            except PermissionError as exc:
                print(label, "DENIED", exc.errno)
        print("own", os.path.exists("/proc/self/environ") is False or bool(open("/proc/self/environ", "rb").read()))
        """,
        ctx,
    )
    assert result["status"] == "success", result
    out = result["output"]
    for label in ("passwd", "other_session", "app_db", "dotenv", "parent_environ"):
        assert f"{label} DENIED" in out, out
        assert f"{label} READ" not in out
    assert "sk-planted-secret-value" not in out
    assert "own True" in out


@pytest.mark.timeout(120)
def test_kernel_cell_cannot_start_a_program_to_get_around_the_guard(ctx):
    result = _run(
        """
        import os, subprocess
        for label, call in [
            ("run", lambda: subprocess.run(["cat", "/etc/passwd"], capture_output=True)),
            ("shell", lambda: subprocess.check_output("cat /etc/passwd", shell=True)),
            ("system", lambda: os.system("cat /etc/passwd")),
        ]:
            try:
                call()
                print(label, "STARTED")
            except PermissionError as exc:
                print(label, "DENIED", "does not start other programs" in str(exc))
        """,
        ctx,
    )
    assert result["status"] == "success", result
    out = result["output"]
    assert "STARTED" not in out, out
    assert out.count("DENIED True") == 3, out


_WORKER_PROBE = textwrap.dedent(
    """
    def probe(_):
        try:
            open("/etc/passwd").read()
            return "READ"
        except PermissionError:
            return "DENIED"
    """
)


@pytest.mark.timeout(180)
def test_forked_workers_keep_the_guard(ctx, project, monkeypatch):
    monkeypatch.setenv("CODE_MODE_CELL_TIMEOUT_SECONDS", "60")
    (project / "runtime" / "session_a" / "probe_worker.py").write_text(_WORKER_PROBE, encoding="utf-8")
    result = _run(
        """
        import multiprocessing as mp, os, sys
        sys.path.insert(0, os.getcwd())
        import probe_worker
        with mp.get_context("fork").Pool(1) as pool:
            print("fork", pool.map(probe_worker.probe, [0]))
        """,
        ctx,
    )
    assert result["status"] == "success", result
    assert "fork ['DENIED']" in result["output"], result["output"]


@pytest.mark.timeout(180)
def test_joblib_workers_run_under_the_same_guard(ctx, project, monkeypatch):
    """loky starts fresh interpreters through a path the audit hook cannot see; sitecustomize guards them."""
    pytest.importorskip("joblib")
    monkeypatch.setenv("CODE_MODE_CELL_TIMEOUT_SECONDS", "60")
    (project / "runtime" / "session_a" / "probe_worker.py").write_text(_WORKER_PROBE, encoding="utf-8")
    result = _run(
        """
        import os, sys
        sys.path.insert(0, os.getcwd())
        import probe_worker
        from joblib import Parallel, delayed

        print("loky", Parallel(n_jobs=2)(delayed(probe_worker.probe)(i) for i in range(2)))
        """,
        ctx,
    )
    assert result["status"] == "success", result
    assert "loky ['DENIED', 'DENIED']" in result["output"], result["output"]


@pytest.mark.timeout(120)
def test_kernel_cell_keeps_working_inside_its_workspace(ctx, project):
    result = _run(
        """
        import csv, json, os, pathlib, shutil, tempfile, sqlite3, zipfile
        os.makedirs("results", exist_ok=True)
        with open("results/table.csv", "w", newline="") as handle:
            csv.writer(handle).writerows([["a", "b"], [1, 2]])
        shutil.copy("results/table.csv", "copy.csv")
        with tempfile.NamedTemporaryFile("w", suffix=".txt") as tmp:
            tmp.write("scratch")
        db = sqlite3.connect("local.db")
        db.execute("create table t(x)")
        db.commit()
        db.close()
        with zipfile.ZipFile("z.zip", "w") as archive:
            archive.write("copy.csv")
        print(sorted(p.name for p in pathlib.Path(".").iterdir() if p.is_file()))
        print(open("/etc/hosts").read()[:1] != "?")
        """,
        ctx,
    )
    assert result["status"] == "success", result
    assert "copy.csv" in result["output"] and "local.db" in result["output"]
    assert (project / "runtime" / "session_a" / "results" / "table.csv").exists()


@pytest.mark.timeout(120)
def test_kernel_plotting_stack_still_works_under_the_guard(ctx, project):
    pytest.importorskip("matplotlib")
    pytest.importorskip("numpy")
    result = _run(
        """
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
        plt.plot(np.arange(5), np.arange(5) ** 2)
        plt.savefig("results/chart.png")
        print("saved")
        """,
        ctx,
    )
    assert result["status"] == "success", result
    assert "saved" in result["output"]
    assert "not a writable directory" not in (result.get("stderr") or "") + result["output"]
    assert (project / "runtime" / "session_a" / "results" / "chart.png").exists()


@pytest.mark.timeout(120)
def test_guard_denial_does_not_kill_the_kernel(ctx):
    denied = _run("open('/etc/passwd').read()", ctx)
    assert denied["status"] == "error"
    assert "PermissionError" in denied["error"] or "cannot read" in denied["error"]
    followup = _run("print('still here')", ctx)
    assert followup["status"] == "success"
    assert followup["kernel"]["reused"] is True


@pytest.mark.timeout(120)
def test_operator_switch_turns_the_guard_off(ctx, monkeypatch):
    monkeypatch.setenv("CODE_MODE_FS_GUARD", "0")
    result = _run("print(len(open('/etc/hosts').read()) >= 0)", ctx)
    assert result["status"] == "success", result
    assert "True" in result["output"]
    # an unguarded kernel is the only one allowed to start a program
    again = _run("import subprocess; print(subprocess.run(['true']).returncode)", ctx)
    assert again["status"] == "success", again
    assert "0" in again["output"]


@pytest.mark.timeout(120)
def test_a_guard_that_cannot_install_ends_the_kernel_instead_of_running_open(ctx, monkeypatch):
    monkeypatch.setattr(
        fs_policy, "build_fs_policy_json", lambda **kwargs: "{not json"
    )
    monkeypatch.setattr(kernel_module, "build_fs_policy_json", lambda **kwargs: "{not json")
    result = _run("print('must not run')", ctx)
    assert result["status"] != "success", result
    assert "must not run" not in (result.get("output") or "")


# ---------------------------------------------------------------------------
# Project continuation: recall points a new session at an earlier session's output
# ---------------------------------------------------------------------------


@pytest.fixture()
def project_db(isolated_app_env, monkeypatch, tmp_path):
    """Sessions a and b share a project and an owner; c is another project; d is another owner."""
    from app.database import get_db, init_db
    from app.services.session_paths import get_runtime_session_dir

    monkeypatch.delenv("CODE_MODE_FS_GUARD", raising=False)
    monkeypatch.delenv("CODE_MODE_ALLOW_SUBPROCESS", raising=False)
    scratch = tmp_path / "tmpdir"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    monkeypatch.setenv("TMPDIR", str(scratch))
    init_db()
    rows = [("a", "owner-1", 71), ("b", "owner-1", 71), ("c", "owner-1", 72), ("d", "owner-2", 71), ("e", "owner-1", None)]
    with get_db() as con:
        for sid, owner, project_id in rows:
            con.execute(
                "INSERT INTO chat_sessions(id, owner_id, project_id, name) VALUES(?,?,?,?)",
                (sid, owner, project_id, sid),
            )
        con.commit()
    dirs = {sid: get_runtime_session_dir(sid, create=True) for sid, _, _ in rows}
    for sid, path in dirs.items():
        (path / "results").mkdir(exist_ok=True)
        (path / "results" / "clean.csv").write_text(f"from-{sid}", encoding="utf-8")
    return dirs


def test_siblings_in_the_same_project_are_recalled_read_only(project_db):
    from app.repository.context_recall import project_session_ids

    assert project_session_ids("a") == ["b"]
    assert project_session_ids("e") == []  # a projectless session never shares

    policy = fs_policy.build_fs_policy(
        session_id="a",
        child_cwd=project_db["a"],
        scratch_dir=project_db["a"] / "scratch" / "code_mode",
        kernel_dir=None,
    )
    guard = fs_guard_runtime._Policy(policy)
    own, sibling = project_db["a"] / "results" / "clean.csv", project_db["b"] / "results" / "clean.csv"
    guard.check(str(own), False)
    guard.check(str(sibling), False)
    guard.check(str(sibling.parent), False, listing=True)
    for write_target in (sibling, project_db["b"] / "results" / "new.csv"):
        with pytest.raises(PermissionError):
            guard.check(str(write_target), True)
    for closed in ("c", "d", "e"):
        with pytest.raises(PermissionError):
            guard.check(str(project_db[closed] / "results" / "clean.csv"), False)


@pytest.mark.timeout(120)
def test_kernel_reads_a_recalled_sibling_output_but_not_another_projects(project_db, _clean_kernels):
    ctx = ToolContext(session_id="a", work_dir=str(project_db["a"]))
    result = _run(
        f"""
        for label, path in [("sibling", {str(project_db["b"] / "results" / "clean.csv")!r}),
                            ("other_project", {str(project_db["c"] / "results" / "clean.csv")!r}),
                            ("other_owner", {str(project_db["d"] / "results" / "clean.csv")!r})]:
            try:
                print(label, "READ", open(path).read())
            except PermissionError:
                print(label, "DENIED")
        try:
            open({str(project_db["b"] / "results" / "planted.csv")!r}, "w")
            print("sibling_write STARTED")
        except PermissionError:
            print("sibling_write DENIED")
        """,
        ctx,
    )
    assert result["status"] == "success", result
    out = result["output"]
    assert "sibling READ from-b" in out, out
    assert "other_project DENIED" in out and "other_owner DENIED" in out, out
    assert "sibling_write DENIED" in out, out
    assert not (project_db["b"] / "results" / "planted.csv").exists()
