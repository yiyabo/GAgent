from __future__ import annotations

from app.services.plans.artifact_contracts import infer_artifact_contract
from app.services.resources.resource_registry import resolve_resources


def test_resource_contract_keeps_artifact_aliases_separate() -> None:
    contract = infer_artifact_contract(
        task_name="Read evidence",
        instruction="Use enhanced sampling evidence.",
        metadata={
            "artifact_contract": {
                "requires": ["enhanced_sampling.evidence_md"],
                "publishes": [],
            }
        },
    )

    assert contract == {
        "requires": ["enhanced_sampling.evidence_md"],
        "publishes": [],
    }


def test_resource_contract_accepts_resource_requires_and_resolves_phagescope() -> None:
    contract = infer_artifact_contract(
        task_name="Compute k-mer features",
        instruction="Use resource:phagescope.sequence_corpus for phage_fasta inputs.",
        metadata={
            "artifact_contract": {
                "requires": ["resource:phagescope.sequence_corpus"],
                "publishes": [],
            }
        },
    )

    assert contract["requires"] == []
    assert contract["publishes"] == []
    assert contract["resources"] == ["phagescope.sequence_corpus"]

    resolved, missing = resolve_resources(contract["resources"])
    assert missing == []
    resource = resolved["phagescope.sequence_corpus"]
    assert resource["root"].endswith("/phagescope")
    assert any(path.endswith("/phage_fasta") for path in resource["required_paths"])
    assert any("tarfile.open" in hint for hint in resource["format_hints"])


def test_phagescope_kmer_text_infers_sequence_resource() -> None:
    contract = infer_artifact_contract(
        task_name="Generate 46-mer features",
        instruction="Generate kmer_46.npz from PhageScope phage_fasta genomes.",
        metadata={},
    )

    assert contract.get("resources") == ["phagescope.sequence_corpus"]


def test_phage_embedding_text_infers_sequence_resource() -> None:
    contract = infer_artifact_contract(
        task_name="Generate Genome Embeddings via Pretrained Language Models",
        instruction=(
            "Use a phage-adapted DNA language model such as HyenaDNA or "
            "Nucleotide Transformer to generate fixed-length embeddings for each phage genome."
        ),
        metadata={"artifact_contract": {"publishes": ["phage_ml.genome_embeddings_npy"]}},
    )

    assert contract["publishes"] == ["phage_ml.genome_embeddings_npy"]
    assert contract.get("resources") == ["phagescope.sequence_corpus"]
