import inspect
from pathlib import Path

from app.services.deep_think.prompts import (
    _build_native_system_prompt,
    _build_research_source_policy_block,
    _build_system_prompt,
)
from app.services.plans.research_contract import research_contract_prompt


def test_native_and_legacy_prompts_use_source_agnostic_evidence_matrix() -> None:
    for builder in (_build_native_system_prompt, _build_system_prompt):
        assert "_build_research_source_policy_block()" in inspect.getsource(builder)

    prompt = _build_research_source_policy_block()
    assert "RESEARCH SOURCE & PROVENANCE ROUTING" in prompt
    assert "PhageScope is an aggregated catalog" in prompt
    assert "PhagesDB" in prompt
    assert "INPHARED" in prompt
    assert "NCBI RefSeq/GenBank" in prompt
    assert "PubMed/Europe PMC" in prompt
    assert "ICTV" in prompt
    assert "coverage gap" in prompt
    assert "Do not query every source for every task" in prompt
    assert prompt in research_contract_prompt({"objective": "compare sources"})


def test_phage_skill_has_the_same_source_and_provenance_policy() -> None:
    root = Path(__file__).resolve().parents[3]
    skill = (root / "skills/phage-genomics-analyst/SKILL.md").read_text(encoding="utf-8")
    assert "Research Source Selection and Provenance" in skill
    assert "PhageScope V2" in skill
    assert "not proof of complete coverage" in skill
    assert "INPHARED" in skill
    assert "smallest suitable source set" in skill
    assert "silently replacing the study with a PhageScope-only analysis" in skill
