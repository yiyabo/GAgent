"""An output location inside the calling session is accepted by a native bio_tools run.

The run writes only inside its own directory, so ``/app/runtime/session_x/results/proteins.faa`` used to be refused
("protein must stay inside the run directory"). The tool was then reported as failed and the answer came from an
improvised fallback (S05, S16, S09, S11, S17, S20 in the evaluation).
"""

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path

import pytest

from tool_box.bio_tools import native_publish, output_paths
from tool_box.context import ToolContext

bio_tools_handler = importlib.import_module("tool_box.bio_tools.bio_tools_handler")


@pytest.fixture()
def session(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_RUNTIME_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setenv("BIO_TOOLS_EXECUTION_MODE", "native")
    from app.services.session_paths import get_runtime_session_dir

    root = get_runtime_session_dir("own", create=True)
    (root / "results").mkdir(parents=True, exist_ok=True)
    (root / "uploads").mkdir(parents=True, exist_ok=True)
    return {"root": root, "tmp": tmp_path}


def _run(tmp_path: Path, files: dict, **extra):
    work = tmp_path / "runtime" / "bio_tools" / "_native" / "runs" / "abc123def456" / "work"
    produced = []
    for rel, text in files.items():
        path = work / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        produced.append({"path": rel, "size": len(text)})
    result = {
        "success": True, "tool": "prodigal", "operation": "predict", "run_id": "abc123def456",
        "work_dir": str(work), "produced_files": produced,
    }
    result.update(extra)
    return result


def test_session_locations_become_plain_names(session):
    root = session["root"]
    out_file, params, requests = output_paths.plan_session_outputs(
        str(root / "results" / "out.txt"),
        {"protein": str(root / "results" / "proteins.faa"), "nucleotide": str(root / "scratch" / "genes.fna"), "mode": "single"},
        root,
    )
    assert out_file == "out.txt"
    assert params == {"protein": "proteins.faa", "nucleotide": "genes.fna", "mode": "single"}
    assert {r.key: r.target for r in requests} == {
        "output_file": str(root / "results" / "out.txt"),
        "protein": str(root / "results" / "proteins.faa"),
        "nucleotide": str(root / "scratch" / "genes.fna"),
    }


@pytest.mark.parametrize(
    "value",
    [
        "/etc/passwd",
        "/tmp/elsewhere.faa",
        "/work/inside.faa",
        "relative/name.faa",
        "plain.faa",
        "../escape.faa",
    ],
)
def test_other_values_are_left_for_the_runner_to_judge(session, value):
    assert output_paths.plan_session_outputs(None, {"protein": value}, session["root"]) == (None, {"protein": value}, [])


def test_the_users_uploads_and_other_sessions_are_never_a_target(session):
    root = session["root"]
    other = root.parent / "session_other" / "results" / "x.faa"
    for value in (str(root / "uploads" / "genome.fasta"), str(other), str(root)):
        assert output_paths.plan_session_outputs(None, {"output": value}, root)[2] == []


def test_a_symlink_out_of_the_session_is_not_followed(session):
    root = session["root"]
    link = root / "results" / "link"
    link.symlink_to(session["tmp"], target_is_directory=True)
    assert output_paths.plan_session_outputs(None, {"output_dir": str(link / "x")}, root)[2] == []


def test_without_a_session_nothing_is_rewritten(session):
    value = str(session["root"] / "results" / "p.faa")
    assert output_paths.plan_session_outputs(value, {"protein": value}, None) == (value, {"protein": value}, [])


def test_two_locations_with_one_file_name_get_two_run_names(session):
    root = session["root"]
    _, params, requests = output_paths.plan_session_outputs(
        None, {"protein": str(root / "results" / "a" / "x.faa"), "nucleotide": str(root / "results" / "b" / "x.faa")}, root
    )
    assert len({params["protein"], params["nucleotide"]}) == 2
    assert len({r.relative for r in requests}) == 2


def test_a_run_file_maps_to_its_requested_place_only(session):
    root = session["root"]
    requests = [
        output_paths.OutputRequest("protein", str(root / "results" / "p.faa"), "p.faa", str(root / "results" / "p.faa")),
        output_paths.OutputRequest("output_dir", str(root / "results" / "d"), "d", str(root / "results" / "d")),
        output_paths.OutputRequest("index_prefix", str(root / "results" / "ref"), "ref", str(root / "results" / "ref")),
    ]
    assert output_paths.target_for("p.faa", requests) == str(root / "results" / "p.faa")
    assert output_paths.target_for("d/sub/x.tsv", requests) == str(root / "results" / "d" / "sub" / "x.tsv")
    assert output_paths.target_for("ref.bai", requests) == str(root / "results" / "ref.bai")
    assert output_paths.target_for("p.faa2", requests) is None
    assert output_paths.target_for("dd/x", requests) is None
    assert output_paths.target_for("other.txt", requests) is None


def test_requested_files_are_copied_where_they_were_asked_for_and_the_rest_to_the_default_place(session):
    root = session["root"]
    target = root / "results" / "proteins.faa"
    requests = [output_paths.OutputRequest("protein", str(target), "proteins.faa", str(target))]
    result = _run(session["tmp"], {"proteins.faa": ">p\nMKV\n", "stderr.log": "log"})
    published = native_publish.publish_run_outputs(result, root, requests)

    assert target.read_text(encoding="utf-8") == ">p\nMKV\n"
    default = root / "results" / "bio_tools" / "prodigal_predict_abc123def456" / "stderr.log"
    assert default.is_file()
    assert not (root / "results" / "bio_tools" / "prodigal_predict_abc123def456" / "proteins.faa").exists()
    assert set(published["artifact_paths"]) == {str(target), str(default)}
    assert published["requested_outputs"] == [
        {"parameter": "protein", "requested": str(target), "run_name": "proteins.faa"}
    ]
    assert "copied to the requested location" in published["output_path_note"]


def test_when_everything_was_requested_there_is_no_default_directory(session):
    root = session["root"]
    target = root / "results" / "proteins.faa"
    requests = [output_paths.OutputRequest("protein", str(target), "proteins.faa", str(target))]
    published = native_publish.publish_run_outputs(_run(session["tmp"], {"proteins.faa": "x"}), root, requests)
    assert "session_output_dir" not in published
    assert not (root / "results" / "bio_tools").exists()


def test_a_requested_directory_receives_its_files(session):
    root = session["root"]
    target = root / "results" / "annot"
    requests = [output_paths.OutputRequest("output_dir", str(target), "annot", str(target))]
    result = _run(session["tmp"], {"annot/a.gff3": "1", "annot/deep/b.tsv": "2"})
    native_publish.publish_run_outputs(result, root, requests)
    assert (target / "a.gff3").read_text() == "1"
    assert (target / "deep" / "b.tsv").read_text() == "2"


def test_publishing_without_requests_is_unchanged(session):
    root = session["root"]
    published = native_publish.publish_run_outputs(_run(session["tmp"], {"a.txt": "x"}), root)
    assert "requested_outputs" not in published
    assert published["session_output_dir"].endswith("prodigal_predict_abc123def456")


def _call_once(session, monkeypatch, tool_context, **kwargs):
    seen = {}
    native = _run(session["tmp"], {"proteins.faa": ">p\nMKV\n"})

    async def fake_native(**inner):
        seen.update(inner)
        return dict(native)

    monkeypatch.setattr(bio_tools_handler, "_execute_native_bio_tool", fake_native)
    result = asyncio.run(
        bio_tools_handler._execute_bio_tool_once(
            tool_name="prodigal", operation="predict", input_file=None, timeout=None, tool_context=tool_context, **kwargs
        )
    )
    return seen, result


def test_the_handler_runs_under_a_plain_name_and_puts_the_file_where_the_model_asked(session, monkeypatch):
    target = session["root"] / "results" / "proteins.faa"
    seen, result = _call_once(
        session, monkeypatch, ToolContext(session_id="own"), output_file=None, params={"protein": str(target)}
    )
    assert seen["params"] == {"protein": "proteins.faa"}
    assert result["artifact_paths"] == [str(target)]
    assert target.read_text(encoding="utf-8") == ">p\nMKV\n"


def test_the_handler_leaves_a_call_without_a_session_alone(session, monkeypatch):
    value = str(session["root"] / "results" / "proteins.faa")
    seen, result = _call_once(session, monkeypatch, None, output_file=None, params={"protein": value})
    assert seen["params"] == {"protein": value}
    assert "artifact_paths" not in result
