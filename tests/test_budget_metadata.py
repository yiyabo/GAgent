from typing import Dict, Any, List

from app.services.context_budget import apply_budget


def test_budget_metadata_per_section_reason_and_ordering():
    sections = [
        {"task_id": 2, "name": "A", "short_name": "A", "kind": "dep:requires", "content": "ABCDE"},
        {"task_id": 3, "name": "B", "short_name": "B", "kind": "dep:refers",   "content": "WXYZ"},
    ]
    bundle = {"task_id": 1, "sections": sections, "combined": ""}
    out = apply_budget(bundle, per_section_max=3)

    s0, s1 = out["sections"][0], out["sections"][1]

    # Sorted by priority: requires first, refers second
    assert s0["kind"] == "dep:requires" and s0["budget"]["group"] == 0 and s0["budget"]["index"] == 0
    assert s1["kind"] == "dep:refers"   and s1["budget"]["group"] == 1 and s1["budget"]["index"] == 1

    # Per-section cap applied; no total budget
    assert s0["budget"]["allowed_by_per_section"] == 3
    assert s1["budget"]["allowed_by_per_section"] == 3
    assert s0["budget"]["allowed_by_total"] == len("ABCDE")
    assert s1["budget"]["allowed_by_total"] == len("WXYZ")

    # Effective allow == 3, both truncated by per_section
    assert s0["budget"]["allowed"] == 3 and s0["budget"]["truncated"] is True and s0["budget"]["truncated_reason"] == "per_section"
    assert s1["budget"]["allowed"] == 3 and s1["budget"]["truncated"] is True and s1["budget"]["truncated_reason"] == "per_section"


def test_budget_metadata_total_vs_both_reasons():
    # Equal-length sections to observe per/total interplay
    sections = [
        {"task_id": 10, "name": "R", "short_name": "R", "kind": "dep:requires", "content": "abcd"},
        {"task_id": 11, "name": "F", "short_name": "F", "kind": "dep:refers",   "content": "efgh"},
    ]
    bundle = {"task_id": 9, "sections": sections, "combined": ""}
    out = apply_budget(bundle, max_chars=5, per_section_max=3)

    s0, s1 = out["sections"][0], out["sections"][1]
    # First section: per_section applied but total didn't bind yet
    assert s0["budget"]["truncated"] is True
    assert s0["budget"]["truncated_reason"] == "per_section"
    # Second section: both per_section and total bind (remaining was reduced)
    assert s1["budget"]["truncated"] is True
    assert s1["budget"]["truncated_reason"] == "both"


def test_budget_metadata_total_only_reason():
    sections = [
        {"task_id": 1, "name": "A", "short_name": "A", "kind": "dep:requires", "content": "abcd"},
        {"task_id": 2, "name": "B", "short_name": "B", "kind": "dep:refers",   "content": "efgh"},
    ]
    bundle = {"task_id": 0, "sections": sections, "combined": ""}
    out = apply_budget(bundle, max_chars=3)

    s0, s1 = out["sections"][0], out["sections"][1]
    assert s0["budget"]["truncated"] is True and s0["budget"]["truncated_reason"] == "total"
    assert s1["budget"]["truncated"] is True and s1["budget"]["truncated_reason"] == "total"


def test_budget_metadata_no_truncation_reason_none():
    sections = [
        {"task_id": 7, "name": "A", "short_name": "A", "kind": "dep:requires", "content": "hi"},
        {"task_id": 8, "name": "B", "short_name": "B", "kind": "dep:refers",   "content": "ok"},
    ]
    bundle = {"task_id": 6, "sections": sections, "combined": ""}
    out = apply_budget(bundle)  # no caps -> unchanged bundle

    # When no caps provided, function returns bundle unchanged; no 'budget' metadata expected
    for s in out["sections"]:
        assert "budget" not in s
