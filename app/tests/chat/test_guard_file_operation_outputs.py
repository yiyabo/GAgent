"""A deliverable made with file_operations, or published with deliverable_submit, counts for acceptance.

Acceptance only knew the files named under artifact_paths / produced_files / output_path and the like.
file_operations reports a write as ``path`` and a copy or move as ``destination``, and deliverable_submit
lists ``deliverable_submit.artifacts``, so a protein file the agent copied to ``results/x.fasta`` and then
submitted was invisible and the run ended with "delivery acceptance failed: data".
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.deep_think.guards import _extract_guard_candidates, _verify_guard_path


# Candidate extraction only reads strings; a pytest tmp_path under /tmp would be filtered as scratch on Linux.
ROOT = "/app/runtime/session_guard_test"


def _agent():
    return SimpleNamespace(request_profile={}, _acceptance_spec=None)


def _result(tool, params, inner, **outer):
    payload = {"success": True, "tool": tool, "result": {"tool": tool, "success": True, **inner}, **outer}
    return {"tool_name": tool, "tool_params": params, "tool_result": payload}


def test_a_copy_names_its_destination():
    destination = f"{ROOT}/results/genome_small_proteins.fasta"
    item = _result(
        "file_operations", {"operation": "copy"},
        {"operation": "copy", "source": f"{ROOT}/results/a.faa", "destination": destination},
    )
    assert _extract_guard_candidates(_agent(), [item]) == [destination]


def test_a_move_names_its_destination():
    destination = f"{ROOT}/results/moved.csv"
    item = _result("file_operations", {"operation": "move"}, {"operation": "move", "source": "x", "destination": destination})
    assert _extract_guard_candidates(_agent(), [item]) == [destination]


def test_a_write_names_its_path():
    path = f"{ROOT}/results/table.csv"
    item = _result("file_operations", {"operation": "write"}, {"operation": "write", "path": path, "size": 10})
    assert _extract_guard_candidates(_agent(), [item]) == [path]


def test_a_submission_names_the_published_files():
    path = f"{ROOT}/results/genome_small_proteins.fasta"
    item = _result(
        "deliverable_submit", {},
        {"deliverable_submit": {"publish": True, "artifacts": [{"path": path, "module": "refs", "reason": "proteins"}]}},
    )
    assert _extract_guard_candidates(_agent(), [item]) == [path]


@pytest.mark.parametrize("operation", ["read", "list", "exists", "profile"])
def test_reading_a_file_is_not_producing_it(operation):
    path = f"{ROOT}/results/table.csv"
    item = _result("file_operations", {"operation": operation}, {"operation": operation, "path": path, "destination": path})
    assert _extract_guard_candidates(_agent(), [item]) == []


def test_a_failed_copy_is_not_a_deliverable():
    destination = f"{ROOT}/results/x.fasta"
    item = {
        "tool_name": "file_operations", "tool_params": {"operation": "copy"},
        "tool_result": {"success": False, "result": {"operation": "copy", "destination": destination, "success": False}},
    }
    assert _extract_guard_candidates(_agent(), [item]) == []


def test_scratch_and_non_deliverable_files_still_do_not_count():
    scratch = f"{ROOT}/scratch/notes.txt"
    tmp = "/tmp/out.csv"
    items = [
        _result("file_operations", {"operation": "write"}, {"operation": "write", "path": scratch}),
        _result("file_operations", {"operation": "write"}, {"operation": "write", "path": tmp}),
        _result("file_operations", {"operation": "write"}, {"operation": "write", "path": f"{ROOT}/results/x.bin"}),
    ]
    assert _extract_guard_candidates(_agent(), items) == []


def test_a_terminal_write_has_no_path_to_harvest():
    item = _result("terminal_session", {"operation": "write"}, {"operation": "write", "terminal_id": "t1", "bytes_sent": 12})
    assert _extract_guard_candidates(_agent(), [item]) == []


def test_the_copied_file_satisfies_the_acceptance_check(tmp_path):
    """S05: the protein file was copied to results/*.fasta; only a .faa from the tool was visible."""
    from app.services.deep_think.acceptance import acceptance_missing
    from app.services.plans.output_spec import OutputSpec, RequiredOutput

    (tmp_path / "results").mkdir()
    copied = tmp_path / "results" / "genome_small_proteins.fasta"
    copied.write_text(">p1\nMKV\n")
    agent = _agent()
    spec = OutputSpec(required_outputs=[RequiredOutput(kind="data", extensions=[".fasta"])])
    agent._acceptance_spec = spec
    agent._output_verification = None
    item = _result("file_operations", {"operation": "copy"}, {"operation": "copy", "destination": str(copied)})
    produced = [_verify_guard_path(agent, c) for c in _extract_guard_candidates(agent, [item])]
    assert produced == [str(copied)]
    assert acceptance_missing(agent, [], produced, spec) == []
    assert acceptance_missing(agent, [], [], spec)
