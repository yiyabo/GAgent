from __future__ import annotations

from app.services.plans.artifact_contracts import (
    ArtifactContractProvenance,
    extend_contract_with_runtime_candidates,
    infer_artifact_contract,
    resolve_artifact_contract_with_provenance,
)


def test_explicit_contract_is_authoritative() -> None:
    resolved = resolve_artifact_contract_with_provenance(
        task_name="Consumer task",
        instruction="Use the provided references.",
        metadata={
            "artifact_contract": {
                "requires": ["ai_dl.references_bib"],
                "publishes": ["ai_dl.evidence_md"],
            }
        },
    )

    assert resolved.explicit_requires == ["ai_dl.references_bib"]
    assert resolved.explicit_publishes == ["ai_dl.evidence_md"]
    assert resolved.inferred_requires == []
    assert resolved.inferred_publishes == []
    assert resolved.contract_source == "explicit"
    assert resolved.has_explicit is True
    assert resolved.requires() == ["ai_dl.references_bib"]
    assert resolved.publishes() == ["ai_dl.evidence_md"]


def test_inferred_contract_marked_as_fallback() -> None:
    resolved = resolve_artifact_contract_with_provenance(
        task_name="AI evidence task",
        instruction="Assemble findings into ai_dl_evidence.md",
        metadata={
            "paper_context_paths": ["data/ai_references.bib"],
            "acceptance_criteria": {
                "checks": [{"path": "ai_dl/evidence.md"}],
            },
        },
    )

    assert resolved.explicit_requires == []
    assert resolved.explicit_publishes == []
    assert "ai_dl.references_bib" in resolved.inferred_requires
    assert "ai_dl.evidence_md" in resolved.inferred_publishes
    assert resolved.contract_source == "inferred"
    assert resolved.has_explicit is False


def test_mixed_contract_source_when_explicit_and_inferred_present() -> None:
    resolved = resolve_artifact_contract_with_provenance(
        task_name="AI evidence task",
        instruction="Use ai_references.bib and produce evidence.",
        metadata={
            "artifact_contract": {"publishes": ["ai_dl.evidence_md"]},
            "acceptance_criteria": {
                "checks": [{"path": "ai_dl/structured_evidence.json"}],
            },
        },
    )

    assert resolved.explicit_publishes == ["ai_dl.evidence_md"]
    assert "ai_dl.structured_evidence_json" in resolved.inferred_publishes
    assert resolved.contract_source == "mixed"
    assert resolved.has_explicit is True


def test_empty_metadata_yields_none_source() -> None:
    resolved = resolve_artifact_contract_with_provenance(
        task_name="Generic task",
        instruction="Do something unrelated to known aliases.",
        metadata=None,
    )

    assert resolved.requires() == []
    assert resolved.publishes() == []
    assert resolved.contract_source == "none"
    assert resolved.has_explicit is False


def test_explicit_alias_not_duplicated_as_inferred() -> None:
    resolved = resolve_artifact_contract_with_provenance(
        task_name="AI consumer",
        instruction="Pull from data/ai_references.bib",
        metadata={
            "artifact_contract": {"requires": ["ai_dl.references_bib"]},
            "paper_context_paths": ["data/ai_references.bib"],
        },
    )

    assert resolved.explicit_requires == ["ai_dl.references_bib"]
    assert resolved.inferred_requires == []
    assert resolved.requires() == ["ai_dl.references_bib"]


def test_infer_artifact_contract_compat_wrapper_matches_combined() -> None:
    metadata = {
        "artifact_contract": {"requires": ["ai_dl.references_bib"]},
        "acceptance_criteria": {
            "checks": [{"path": "ai_dl/evidence.md"}],
        },
    }
    legacy = infer_artifact_contract(
        task_name="Task",
        instruction="",
        metadata=metadata,
    )
    resolved = resolve_artifact_contract_with_provenance(
        task_name="Task",
        instruction="",
        metadata=metadata,
    )

    assert legacy == resolved.as_contract_dict()
    assert legacy["requires"] == ["ai_dl.references_bib"]
    assert legacy["publishes"] == ["general.evidence_md"]


def test_runtime_candidates_extend_publishes_and_flip_source() -> None:
    provenance = ArtifactContractProvenance(
        explicit_requires=["ai_dl.references_bib"],
    )

    extended = extend_contract_with_runtime_candidates(
        provenance,
        task_name="AI consumer",
        instruction="Downstream consumer",
        candidate_paths=["/tmp/plan1/task2/run_a/ai_dl/evidence.md"],
    )

    assert extended.runtime_publishes == ["ai_dl.evidence_md"]
    assert extended.publishes() == ["ai_dl.evidence_md"]
    assert extended.contract_source == "mixed"


def test_runtime_only_contract_source() -> None:
    provenance = ArtifactContractProvenance()
    extended = extend_contract_with_runtime_candidates(
        provenance,
        task_name="AI consumer",
        instruction="Downstream consumer",
        candidate_paths=["/tmp/ai_dl/evidence.md"],
    )

    assert extended.runtime_publishes == ["ai_dl.evidence_md"]
    assert extended.contract_source == "runtime"
    assert extended.has_explicit is False


def test_invalid_explicit_alias_is_dropped() -> None:
    resolved = resolve_artifact_contract_with_provenance(
        task_name="Task",
        instruction="",
        metadata={"artifact_contract": {"requires": ["not_a_real_alias", "ai_dl.references_bib"]}},
    )

    assert resolved.explicit_requires == ["ai_dl.references_bib"]
