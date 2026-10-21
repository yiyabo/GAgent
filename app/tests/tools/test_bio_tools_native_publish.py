"""A native bio_tools run's files are copied into the session that asked for them.

The run directory is under ``runtime/bio_tools/_native``, which the file tool refuses, so the agent could not
read, copy or submit what the tool had just produced.
"""

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from tool_box.bio_tools import native_publish
from tool_box.context import ToolContext

# ``tool_box.bio_tools`` re-exports a function called ``bio_tools_handler``, which hides the module of the same name.
bio_tools_handler = importlib.import_module("tool_box.bio_tools.bio_tools_handler")


def _run(tmp_path: Path, files: dict, **extra):
    """A finished native run with real files in its work directory."""
    work = tmp_path / "runtime" / "bio_tools" / "_native" / "runs" / "abc123def456" / "work"
    produced = []
    for rel, text in files.items():
        path = work / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        produced.append({"path": rel, "size": len(text)})
    result = {
        "success": True,
        "tool": "samtools",
        "operation": "index",
        "run_id": "abc123def456",
        "work_dir": str(work),
        "produced_files": produced,
    }
    result.update(extra)
    return result


def test_files_are_copied_into_the_session_and_named_in_the_result(tmp_path):
    session = tmp_path / "runtime" / "session_s1"
    session.mkdir(parents=True)
    result = _run(
        tmp_path,
        {"aligned.sorted.bam": "bam", "aligned.sorted.bam.bai": "bai", "sub/stats.txt": "stats"},
        output_path=str(tmp_path / "runtime" / "bio_tools" / "_native" / "runs" / "abc123def456" / "work" / "aligned.sorted.bam"),
    )
    published = native_publish.publish_run_outputs(result, session)

    dest = session / "results" / "bio_tools" / "samtools_index_abc123def456"
    assert (dest / "aligned.sorted.bam.bai").read_text() == "bai"
    assert (dest / "sub" / "stats.txt").read_text() == "stats"
    assert sorted(published["artifact_paths"]) == sorted(
        str(dest / name) for name in ("aligned.sorted.bam", "aligned.sorted.bam.bai", "sub/stats.txt")
    )
    assert published["session_output_dir"] == str(dest)
    assert published["session_output_path"] == str(dest / "aligned.sorted.bam")
    assert all(Path(item["path"]).is_file() and item["size"] > 0 for item in published["session_outputs"])
    assert all("session_path" in entry for entry in published["produced_files"])
    assert "artifact_paths" not in result, "the input result is not modified"


def test_a_failed_run_publishes_nothing(tmp_path):
    session = tmp_path / "runtime" / "session_s1"
    session.mkdir(parents=True)
    result = _run(tmp_path, {"a.txt": "x"}, success=False)
    assert native_publish.publish_run_outputs(result, session) is result
    assert not (session / "results").exists()


def test_a_run_with_no_files_or_no_session_is_left_alone(tmp_path):
    session = tmp_path / "runtime" / "session_s1"
    session.mkdir(parents=True)
    empty = {"success": True, "tool": "seqkit", "operation": "stats", "run_id": "r1", "work_dir": str(tmp_path)}
    assert native_publish.publish_run_outputs(empty, session) is empty
    with_files = _run(tmp_path, {"a.txt": "x"})
    assert native_publish.publish_run_outputs(with_files, None) is with_files


def test_unsafe_entries_are_skipped_not_followed(tmp_path):
    session = tmp_path / "runtime" / "session_s1"
    session.mkdir(parents=True)
    secret = tmp_path / "secret.txt"
    secret.write_text("secret")
    result = _run(tmp_path, {"ok.txt": "ok"})
    result["produced_files"] += [
        {"path": "../../../../../secret.txt", "size": 6},
        {"path": str(secret), "size": 6},
    ]
    published = native_publish.publish_run_outputs(result, session)
    assert [item["path"].rsplit("/", 1)[-1] for item in published["session_outputs"]] == ["ok.txt"]
    assert {item["reason"] for item in published["session_outputs_skipped"]} == {"unsafe_path"}
    assert not any(p.name == "secret.txt" for p in (session / "results").rglob("*"))


def test_a_symlink_in_the_work_dir_is_not_copied(tmp_path):
    session = tmp_path / "runtime" / "session_s1"
    session.mkdir(parents=True)
    secret = tmp_path / "secret.txt"
    secret.write_text("secret")
    result = _run(tmp_path, {"ok.txt": "ok"})
    link = Path(result["work_dir"]) / "link.txt"
    link.symlink_to(secret)
    result["produced_files"].append({"path": "link.txt", "size": 6})
    published = native_publish.publish_run_outputs(result, session)
    assert [item["path"].rsplit("/", 1)[-1] for item in published["session_outputs"]] == ["ok.txt"]


def test_size_caps_skip_and_say_so(tmp_path, monkeypatch):
    session = tmp_path / "runtime" / "session_s1"
    session.mkdir(parents=True)
    monkeypatch.setattr(native_publish, "MAX_FILE_BYTES", 5)
    result = _run(tmp_path, {"small.txt": "tiny", "big.txt": "far too large"})
    published = native_publish.publish_run_outputs(result, session)
    assert [item["path"].rsplit("/", 1)[-1] for item in published["session_outputs"]] == ["small.txt"]
    assert published["session_outputs_skipped"] == [{"path": "big.txt", "reason": "too_large"}]

    monkeypatch.setattr(native_publish, "MAX_FILE_BYTES", 1000)
    monkeypatch.setattr(native_publish, "MAX_TOTAL_BYTES", 6)
    result = _run(tmp_path, {"a.txt": "1234", "b.txt": "5678"})
    published = native_publish.publish_run_outputs(result, session)
    assert len(published["session_outputs"]) == 1
    assert published["session_outputs_skipped"][0]["reason"] == "too_large"


def test_odd_tool_names_cannot_leave_the_publish_directory(tmp_path):
    session = tmp_path / "runtime" / "session_s1"
    session.mkdir(parents=True)
    result = _run(tmp_path, {"a.txt": "x"}, tool="../../evil", operation="a/b", run_id="../x")
    published = native_publish.publish_run_outputs(result, session)
    target = Path(published["session_outputs"][0]["path"]).resolve()
    assert target.is_relative_to((session / "results" / "bio_tools").resolve())


def test_a_copy_error_is_reported_not_raised(tmp_path, monkeypatch):
    session = tmp_path / "runtime" / "session_s1"
    session.mkdir(parents=True)

    def boom(src, dst):
        raise PermissionError("denied")

    monkeypatch.setattr(native_publish.shutil, "copyfile", boom)
    published = native_publish.publish_run_outputs(_run(tmp_path, {"a.txt": "x"}), session)
    assert published["session_outputs_skipped"] == [{"path": "a.txt", "reason": "copy_failed"}]
    assert "artifact_paths" not in published


@pytest.fixture()
def _runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_RUNTIME_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setenv("BIO_TOOLS_EXECUTION_MODE", "native")
    return tmp_path


def test_a_native_run_through_the_handler_lands_in_the_calling_session(_runtime, monkeypatch):
    native = _run(_runtime, {"proteins.faa": ">p\nMKV\n", "genes.fna": ">g\nATG\n"})

    async def fake_native(**kwargs):
        return dict(native)

    monkeypatch.setattr(bio_tools_handler, "_execute_native_bio_tool", fake_native)
    ctx = ToolContext(session_id="handler-session")
    result = asyncio.run(
        bio_tools_handler._execute_bio_tool_once(
            tool_name="samtools", operation="index", input_file=None, output_file=None,
            params={}, timeout=None, tool_context=ctx,
        )
    )
    assert len(result["artifact_paths"]) == 2
    for path in result["artifact_paths"]:
        assert "/session_handler-session/results/bio_tools/" in path
        assert Path(path).is_file()


def test_without_a_session_the_native_result_is_returned_untouched(_runtime, monkeypatch):
    native = _run(_runtime, {"proteins.faa": ">p\nMKV\n"})

    async def fake_native(**kwargs):
        return dict(native)

    monkeypatch.setattr(bio_tools_handler, "_execute_native_bio_tool", fake_native)
    result = asyncio.run(
        bio_tools_handler._execute_bio_tool_once(
            tool_name="samtools", operation="index", input_file=None, output_file=None,
            params={}, timeout=None, tool_context=None,
        )
    )
    assert "artifact_paths" not in result


def test_a_background_job_carries_the_session_to_the_native_run(monkeypatch):
    seen = {}

    async def fake_once(**kwargs):
        seen["tool_context"] = kwargs.get("tool_context")
        return {"success": False, "error": "stop here"}

    class Manager:
        def __getattr__(self, name):
            return lambda *a, **k: None

    monkeypatch.setattr(bio_tools_handler, "_execute_bio_tool_once", fake_once)
    monkeypatch.setattr(bio_tools_handler, "_get_plan_job_manager", lambda: Manager())
    bio_tools_handler._run_background_bio_tools_job(
        job_id="bio_x", tool_name="bakta", operation="annotate", input_file=None, output_file=None,
        params={}, timeout=None, session_id="bg-session",
    )
    assert seen["tool_context"].session_id == "bg-session"
    seen.clear()
    bio_tools_handler._run_background_bio_tools_job(
        job_id="bio_y", tool_name="bakta", operation="annotate", input_file=None, output_file=None,
        params={}, timeout=None,
    )
    assert seen["tool_context"] is None


def test_artifact_paths_reach_delivery_acceptance(tmp_path):
    """The key the publisher adds is one the DeepThink extractor reads."""
    from app.services.deep_think import dispatch

    agent = SimpleNamespace(
        _looks_like_artifact_path=dispatch._looks_like_artifact_path,
        _is_internal_artifact_path=dispatch._is_internal_artifact_path,
    )
    session = tmp_path / "runtime" / "session_s1"
    session.mkdir(parents=True)
    published = native_publish.publish_run_outputs(_run(tmp_path, {"proteins.faa": ">p\nMKV\n"}), session)
    assert dispatch._extract_explicit_artifact_paths(agent, published) == published["artifact_paths"]


# --------------------------------------------------------------------------- sidecars of the input

def test_an_index_lands_next_to_its_input_in_the_session(tmp_path):
    session = tmp_path / "runtime" / "session_s1"
    (session / "results").mkdir(parents=True)
    bam = session / "results" / "aligned.sorted.bam"
    bam.write_bytes(b"bam")
    result = _run(tmp_path, {"aligned.sorted.bam.bai": "bai"})
    published = native_publish.publish_run_outputs(result, session, input_file=str(bam))

    assert (session / "results" / "aligned.sorted.bam.bai").read_text() == "bai"
    assert published["artifact_paths"] == [str(session / "results" / "aligned.sorted.bam.bai")]
    assert not (session / "results" / "bio_tools").exists(), "one copy, not a second one in the run folder"
    assert "session_output_dir" not in published
    assert published["session_outputs"][0]["beside_input"] is True


def test_every_file_a_bwa_index_writes_goes_next_to_the_reference(tmp_path):
    session = tmp_path / "runtime" / "session_s1"
    (session / "uploads").mkdir(parents=True)
    ref = session / "uploads" / "ref.fa"
    ref.write_text(">r\nACGT\n")
    names = ["ref.fa.amb", "ref.fa.ann", "ref.fa.bwt", "ref.fa.pac", "ref.fa.sa"]
    result = _run(tmp_path, {name: name for name in names}, tool="bwa")
    published = native_publish.publish_run_outputs(result, session, input_file=str(ref))

    assert sorted(p.name for p in (session / "uploads").iterdir()) == sorted(names + ["ref.fa"])
    assert sorted(Path(p).name for p in published["artifact_paths"]) == sorted(names)


def test_only_files_named_after_the_input_go_beside_it(tmp_path):
    session = tmp_path / "runtime" / "session_s1"
    (session / "uploads").mkdir(parents=True)
    bam = session / "uploads" / "x.bam"
    bam.write_bytes(b"bam")
    result = _run(tmp_path, {"x.bam.bai": "bai", "x.bamx": "other", "stats.txt": "s", "sub/x.bam.bai": "nested"})
    published = native_publish.publish_run_outputs(result, session, input_file=str(bam))

    assert (session / "uploads" / "x.bam.bai").read_text() == "bai"
    assert not (session / "uploads" / "x.bamx").exists() and not (session / "uploads" / "stats.txt").exists()
    dest = session / "results" / "bio_tools" / "samtools_index_abc123def456"
    assert (dest / "x.bamx").is_file() and (dest / "stats.txt").is_file() and (dest / "sub" / "x.bam.bai").is_file()
    assert published["session_output_dir"] == str(dest)


def test_a_stale_index_is_replaced(tmp_path):
    session = tmp_path / "runtime" / "session_s1"
    (session / "results").mkdir(parents=True)
    bam = session / "results" / "a.bam"
    bam.write_bytes(b"bam")
    (session / "results" / "a.bam.bai").write_text("old")
    native_publish.publish_run_outputs(_run(tmp_path, {"a.bam.bai": "new"}), session, input_file=str(bam))
    assert (session / "results" / "a.bam.bai").read_text() == "new"


@pytest.mark.parametrize("where", ["outside", "relative", "none"])
def test_an_input_outside_the_session_gets_the_usual_copy(tmp_path, where):
    session = tmp_path / "runtime" / "session_s1"
    session.mkdir(parents=True)
    other = tmp_path / "runtime" / "session_other"
    other.mkdir()
    (other / "a.bam").write_bytes(b"bam")
    input_file = {"outside": str(other / "a.bam"), "relative": "results/a.bam", "none": None}[where]
    published = native_publish.publish_run_outputs(
        _run(tmp_path, {"a.bam.bai": "bai"}), session, input_file=input_file
    )
    dest = session / "results" / "bio_tools" / "samtools_index_abc123def456"
    assert (dest / "a.bam.bai").is_file()
    assert not (other / "a.bam.bai").exists()
    assert published["session_output_dir"] == str(dest)


def test_a_requested_location_wins_over_the_input_directory(tmp_path):
    from tool_box.bio_tools import output_paths

    session = tmp_path / "runtime" / "session_s1"
    (session / "results").mkdir(parents=True)
    bam = session / "results" / "a.bam"
    bam.write_bytes(b"bam")
    wanted = session / "results" / "indexes" / "a.bam.bai"
    requests = [
        output_paths.OutputRequest(key="output_file", requested=str(wanted), relative="a.bam.bai", target=str(wanted))
    ]
    native_publish.publish_run_outputs(_run(tmp_path, {"a.bam.bai": "bai"}), session, requests, input_file=str(bam))
    assert wanted.read_text() == "bai"
    assert not (session / "results" / "a.bam.bai").exists()


def test_the_handler_hands_the_input_to_the_publisher(_runtime, monkeypatch):
    session = _runtime / "runtime" / "session_handler-session"
    (session / "results").mkdir(parents=True)
    bam = session / "results" / "aligned.sorted.bam"
    bam.write_bytes(b"bam")
    native = _run(_runtime, {"aligned.sorted.bam.bai": "bai"})

    async def fake_native(**kwargs):
        return dict(native)

    monkeypatch.setattr(bio_tools_handler, "_execute_native_bio_tool", fake_native)
    result = asyncio.run(
        bio_tools_handler._execute_bio_tool_once(
            tool_name="samtools", operation="index", input_file=str(bam), output_file=None,
            params={}, timeout=None, tool_context=ToolContext(session_id="handler-session"),
        )
    )
    assert result["artifact_paths"] == [str(session / "results" / "aligned.sorted.bam.bai")]
    assert (session / "results" / "aligned.sorted.bam.bai").read_text() == "bai"
