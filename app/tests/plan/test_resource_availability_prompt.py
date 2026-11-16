"""Planners must only declare data resources that exist on this deployment."""

from __future__ import annotations

from pathlib import Path

from app.services.resources.resource_registry import (
    ResourceSpec,
    available_resource_prompt_lines,
    get_resource_spec,
    resolve_resource,
    resource_availability,
)


def test_620b_read_only_mount_is_a_candidate_root() -> None:
    spec = get_resource_spec("phagescope.sequence_corpus")
    assert spec is not None
    assert "/phagescope/phage_sequence" in spec.candidate_roots


def test_mounted_corpus_resolves_with_its_required_subpath(tmp_path, monkeypatch) -> None:
    root = tmp_path / "phage_sequence"
    (root / "phage_fasta" / "RefSeq").mkdir(parents=True)
    monkeypatch.setenv("PHAGESCOPE_DATA_DIR", str(root))

    resolved = resolve_resource("phagescope.sequence_corpus")
    assert resolved is not None
    assert resolved.resolved_root == str(root.resolve())
    info = resource_availability("phagescope.sequence_corpus")
    assert info["available"] is True
    assert info["root"] == str(root.resolve())
    assert Path(info["required_paths"][0]).name == "phage_fasta"


def test_unmounted_corpus_is_reported_unavailable(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PHAGESCOPE_DATA_DIR", str(tmp_path / "missing"))
    monkeypatch.setattr(
        "app.services.resources.resource_registry._REGISTRY",
        {
            "phagescope.sequence_corpus": ResourceSpec(
                id="phagescope.sequence_corpus",
                name="PhageScope sequence corpus",
                candidate_roots=(str(tmp_path / "also-missing"),),
                required_subpaths=("phage_fasta",),
                description="Local corpus",
            )
        },
    )

    assert resolve_resource("phagescope.sequence_corpus") is None
    lines = available_resource_prompt_lines()
    assert len(lines) == 1
    assert "UNAVAILABLE" in lines[0]
    assert "Do NOT list it in required_resources" in lines[0]
    assert "phagescope_data" in lines[0]


def test_available_resources_are_advertised_with_their_root(tmp_path, monkeypatch) -> None:
    root = tmp_path / "phage_sequence"
    (root / "phage_fasta").mkdir(parents=True)
    monkeypatch.setenv("PHAGESCOPE_DATA_DIR", str(root))

    lines = available_resource_prompt_lines()
    assert any("AVAILABLE" in line and str(root.resolve()) in line for line in lines)
    assert not any("UNAVAILABLE" in line for line in lines)
