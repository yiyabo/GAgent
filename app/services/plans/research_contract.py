"""Persistent, owner-scoped research context shared by Plan creation and execution."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

RESEARCH_SOURCE_POLICY = (
    "=== RESEARCH SOURCE & PROVENANCE ROUTING ===\n"
    "- For research Plans and evidence-sensitive analysis, build an evidence matrix: question/claim, authoritative source, "
    "query or accession, retrieval date/version, exact artifact path, independent cross-check, and limitations.\n"
    "- PhageScope is an aggregated catalog and discovery/materialization source, not the default universal truth or proof of coverage. "
    "Verify decisive accession, sequence, taxonomy, host and time-dependent claims against an authoritative or primary source when available.\n"
    "- Route by question: NCBI RefSeq/GenBank (`ncbi_search`/`sequence_fetch`) for canonical sequences and metadata; "
    "PhagesDB for isolated phage records; INPHARED for phage-host metadata; PubMed/Europe PMC for primary papers and methods; "
    "ICTV for current taxonomy. Use web_search to locate authoritative records/endpoints and cite their URLs; do not imply a direct bulk connector exists when it does not.\n"
    "- User-provided immutable data defines the requested scope; preserve exact inputs and provenance.\n"
    "- Do not query every source for every task. Select the smallest suitable source set. If a required source is unavailable, "
    "record an explicit coverage gap instead of silently narrowing the study to PhageScope.\n"
    "- When sources disagree, report the source, version/date and exact difference; do not resolve conflicts by majority vote.\n\n"
)
CONTRACT_VERSION = 1
MAX_MESSAGES = 24
MAX_MESSAGE_CHARS = 20_000
MAX_OBJECTIVE_CHARS = 50_000


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _messages(values: Any) -> list[dict[str, str]]:
    if not isinstance(values, list):
        return []
    selected = [item for item in values if isinstance(item, Mapping)][-MAX_MESSAGES:]
    result: list[dict[str, str]] = []
    for item in selected:
        role = str(item.get("role") or "").strip()
        content = _text(item.get("content"), MAX_MESSAGE_CHARS)
        if role in {"user", "assistant"} and content:
            result.append({"role": role, "content": content})
    return result


def build_research_contract(
    session_context: Mapping[str, Any] | None,
    *,
    description: str | None = None,
    assumptions: Any = None,
) -> dict[str, Any]:
    """Capture user intent and evidence without replacing it with an Execute command."""
    context = session_context or {}
    objective = _text(context.get("user_message"), MAX_OBJECTIVE_CHARS) or _text(
        description, MAX_OBJECTIVE_CHARS
    )
    evidence = _messages(context.get("chat_history"))
    if objective and not any(
        item["role"] == "user" and item["content"] == objective for item in evidence
    ):
        evidence.append({"role": "user", "content": objective})
    return {
        "version": CONTRACT_VERSION,
        "objective": objective,
        "requirements": _text(description, MAX_OBJECTIVE_CHARS),
        "evidence": evidence[-MAX_MESSAGES:],
        "assumptions": list(assumptions) if isinstance(assumptions, list) else [],
        "source_preferences": list(context.get("source_preferences") or [])
        if isinstance(context.get("source_preferences"), list)
        else [],
        "deliverables": list(context.get("deliverables") or [])
        if isinstance(context.get("deliverables"), list)
        else [],
    }


def research_contract_prompt(contract: Mapping[str, Any]) -> str:
    """Render the persisted contract; this path deliberately has no 360/500-char cuts."""
    lines = ["=== PERSISTED RESEARCH CONTRACT ==="]
    objective = str(contract.get("objective") or "").strip()
    requirements = str(contract.get("requirements") or "").strip()
    if objective:
        lines.extend(["ORIGINAL OBJECTIVE:", objective])
    if requirements and requirements != objective:
        lines.extend(["PLAN REQUIREMENTS:", requirements])
    for field, heading in (
        ("deliverables", "REQUIRED DELIVERABLES"),
        ("source_preferences", "SOURCE PREFERENCES"),
        ("assumptions", "ASSUMPTIONS (not user-confirmed facts)"),
    ):
        values = contract.get(field)
        if isinstance(values, list) and values:
            lines.append(f"{heading}:")
            lines.extend(f"- {item}" for item in values)
    evidence = contract.get("evidence")
    if isinstance(evidence, list) and evidence:
        lines.append("USER/ASSISTANT EVIDENCE (preserve scientific constraints and citations):")
        for item in evidence:
            if isinstance(item, Mapping):
                lines.append(f"[{item.get('role', 'unknown')}] {item.get('content', '')}")
    lines.append(
        "Use this contract together with task instructions. Do not silently reduce the objective, "
        "deliverables, validation depth, or source requirements. Mark uncertainty explicitly."
    )
    return "\n".join(lines) + "\n\n" + RESEARCH_SOURCE_POLICY


def with_research_contract(
    session_context: Mapping[str, Any] | None, contract: Mapping[str, Any]
) -> dict[str, Any]:
    result = dict(session_context or {})
    result["research_contract"] = dict(contract)
    result["research_contract_prompt"] = research_contract_prompt(contract)
    objective = str(contract.get("objective") or "").strip()
    if objective:
        result["user_message"] = objective
    result["chat_history"] = list(contract.get("evidence") or [])
    return result


def merge_owner_history(
    contract: Mapping[str, Any], history: list[dict[str, Any]]
) -> dict[str, Any]:
    """Add later approved revisions/evidence while keeping the original contract."""
    merged = dict(contract)
    evidence = _messages(list(contract.get("evidence") or []) + _messages(history))
    unique: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in evidence:
        key = (item["role"], item["content"])
        if key not in seen:
            seen.add(key)
            unique.append(item)
    merged["evidence"] = unique[-MAX_MESSAGES:]
    return dict(merged)
