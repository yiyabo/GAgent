"""The report node: one model call over compact evidence from the results wired into it.

The model sees text the engine extracted, never the files themselves: tables and text up to a size budget,
sequence files as counts and lengths, hit tables as statistics plus the best hits, folders as a listing plus
their small summary tables. Each item also carries the command that produced it and a note on how to read
it (what an empty file means, the tool's thresholds). The prompt tells the model to use only that evidence
and to say when something was cut, so the report cannot quote numbers no tool produced.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

TOTAL_BUDGET_CHARS = 120_000
MIN_ITEM_CHARS = 4_000
MAX_DIR_LISTING = 80
MAX_DIR_TABLES = 6
DIR_TABLE_MAX_BYTES = 40_000
SUMMARY_HINTS = ("summary", "prediction", "quality", "score", "result", "classification", "taxonomy")
MODEL_TIMEOUT_SECONDS = 300
MAX_REPORT_TOKENS = 6000
MAX_COMMAND_CHARS = 600
SIGNIFICANT_EVALUE = 1e-5
TOP_HITS = 25
_PATH = re.compile(r"(?:/[^\s/'\"]+)+/([^\s/'\"]+)")


def _read_text(path: Path, limit: int) -> Tuple[str, bool]:
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        text = handle.read(limit + 1)
    return (text[:limit], True) if len(text) > limit else (text, False)


def _fasta_summary(path: Path, nucleotide: bool) -> str:
    count = total = gc = 0
    lengths: List[int] = []
    headers: List[str] = []
    current = 0
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if line.startswith(">"):
                if count:
                    lengths.append(current)
                count += 1
                current = 0
                if len(headers) < 8:
                    headers.append(line[1:].strip()[:160])
                continue
            sequence = line.strip()
            current += len(sequence)
            total += len(sequence)
            if nucleotide:
                upper = sequence.upper()
                gc += upper.count("G") + upper.count("C")
    if count:
        lengths.append(current)
    if not count:
        return "empty (no FASTA records)"
    lines = [
        f"records: {count}",
        f"total length: {total}",
        f"min / max / mean length: {min(lengths)} / {max(lengths)} / {total / count:.1f}",
    ]
    if nucleotide and total:
        lines.append(f"GC: {100.0 * gc / total:.2f}%")
    lines.append("first headers:")
    lines.extend(f"  >{header}" for header in headers)
    return "\n".join(lines)


def compact_command(command: Optional[str]) -> str:
    """The command with every absolute path cut to its last part: what ran and with which options."""
    text = _PATH.sub(r"\1", str(command or "").strip())
    return text[:MAX_COMMAND_CHARS]


def _hits_summary(path: Path, limit: int) -> Tuple[str, bool]:
    """BLAST-style tabular hits as counts and the best hit of each query, so a long table is not cut blind."""
    rows = []
    other_lines = 0
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if line.startswith("#") or not line.strip():
                continue
            parts = line.rstrip("\n").split("\t")
            try:
                evalue, bits = float(parts[10]), float(parts[11])
            except (IndexError, ValueError):
                other_lines += 1
                continue
            rows.append((parts[0], parts[1], evalue, bits, "\t".join(parts[:12])))
    if not rows:
        # Not the 12-column format after all: show the text rather than claim there are no hits.
        return _read_text(path, limit) if other_lines else ("empty: no hits", False)
    significant = [row for row in rows if row[2] <= SIGNIFICANT_EVALUE]
    best: Dict[str, Tuple[str, str, float, float, str]] = {}
    for row in rows:
        if row[0] not in best or row[3] > best[row[0]][3]:
            best[row[0]] = row
    top = sorted(best.values(), key=lambda row: -row[3])[:TOP_HITS]
    lines = [
        "columns: query, subject, identity %, alignment length, mismatches, gap opens, query start, query end, "
        "subject start, subject end, E-value, bit score",
        f"hit rows: {len(rows)}; queries with a hit: {len(best)}; subjects hit: {len({row[1] for row in rows})}",
        f"rows with E-value <= {SIGNIFICANT_EVALUE:g}: {len(significant)}; "
        f"queries with such a hit: {len({row[0] for row in significant})}",
        f"best hit of each query, the {len(top)} with the highest bit score:",
        *(row[4] for row in top),
    ]
    text = "\n".join(lines)
    return (text[:limit], True) if len(text) > limit else (text, False)


def _directory_summary(path: Path, limit: int) -> Tuple[str, bool]:
    files = sorted(item for item in path.rglob("*") if item.is_file())
    listing = [f"{item.relative_to(path)} ({item.stat().st_size} bytes)" for item in files[:MAX_DIR_LISTING]]
    parts = [f"{len(files)} files" + (f", first {MAX_DIR_LISTING} listed" if len(files) > MAX_DIR_LISTING else "")]
    parts.extend(listing)
    tables = [
        item
        for item in files
        if item.suffix.lower() in {".tsv", ".txt", ".csv"}
        and item.stat().st_size <= DIR_TABLE_MAX_BYTES
        and any(hint in item.name.lower() for hint in SUMMARY_HINTS)
    ][:MAX_DIR_TABLES]
    truncated = len(files) > MAX_DIR_LISTING
    remaining = max(0, limit - sum(len(line) + 1 for line in parts))
    for table in tables:
        if remaining <= 200:
            truncated = True
            break
        text, cut = _read_text(table, min(remaining, DIR_TABLE_MAX_BYTES))
        parts.append(f"\n--- {table.relative_to(path)} ---\n{text}")
        remaining -= len(text) + 50
        truncated = truncated or cut
    return "\n".join(parts), truncated


def describe(item: Dict[str, Any], limit: int) -> Tuple[str, bool]:
    """Evidence text for one connected result and whether it was cut."""
    if item.get("absent"):
        return "not produced: the tool finished without writing this optional output", False
    path = Path(str(item.get("path") or ""))
    data_type = item.get("type")
    kind = item.get("kind")
    if kind == "prefix" or data_type in {"blast_db_nt", "blast_db_aa"}:
        return f"BLAST database at {path.name} (not readable as text)", False
    if not path.exists():
        return "the file is missing", False
    if kind == "dir" or path.is_dir():
        return _directory_summary(path, limit)
    if path.stat().st_size == 0:
        return "empty file (0 bytes)", False
    if data_type in {"fasta_nt", "fasta_aa"}:
        return _fasta_summary(path, nucleotide=data_type == "fasta_nt"), False
    if item.get("digest") == "blast_tab":
        return _hits_summary(path, limit)
    return _read_text(path, limit)


def build_evidence(evidence: List[Dict[str, Any]]) -> Tuple[str, bool]:
    per_item = max(MIN_ITEM_CHARS, TOTAL_BUDGET_CHARS // max(1, len(evidence)))
    blocks: List[str] = []
    any_cut = False
    for index, item in enumerate(evidence, start=1):
        text, cut = describe(item, per_item)
        any_cut = any_cut or cut
        lines = [
            f"## Evidence {index}: {item.get('label')} — {item.get('port_title')} "
            f"[{item.get('node_type')} / {item.get('type')}]"
        ]
        if item.get("path"):
            lines.append(f"file: {Path(str(item['path'])).name}")
        command = compact_command(item.get("command"))
        if command:
            lines.append(f"command: {command}")
        if item.get("reading"):
            lines.append(f"how to read: {item['reading']}")
        if cut:
            lines.append("(truncated: only the beginning is shown)")
        blocks.append("\n".join(lines) + f"\n```\n{text}\n```")
    return "\n\n".join(blocks), any_cut


def build_prompt(evidence_text: str, instructions: str, language: str) -> str:
    language_name = "Chinese (简体中文)" if language == "zh" else "English"
    return (
        "You are writing the final report of a bioinformatics pipeline that has already run. The evidence "
        "below was extracted from the output files of its tools. Rules:\n"
        "- Use only the evidence. Quote numbers exactly as they appear. Never invent values, tools or files.\n"
        "- Read each item with its 'how to read' note and its command: they give the columns, the thresholds "
        "and what an empty output means.\n"
        "- An empty or not-produced output is a result: say what it means according to its note. Call something "
        "missing only when the file is missing, and suggest a rerun only when a tool failed.\n"
        "- If evidence is truncated, say so instead of guessing what the rest contained.\n"
        "- Say which tool each finding comes from; point out where tools agree or disagree, and when they differ "
        "only because of different thresholds, say that.\n"
        "- Background knowledge (what is known about a well-studied organism) may be used only to compare with the "
        "evidence, labelled as background. Do not add taxonomy, morphology, gene functions or other facts that are "
        "not in the evidence; when unsure, leave them out.\n"
        "- Separate what the data shows from your interpretation, and mark uncertain conclusions.\n"
        f"- Write in {language_name}, as Markdown, starting with a short summary and a table of key numbers.\n\n"
        f"What the user wants the report to cover:\n{instructions.strip() or 'A clear summary of the results.'}\n\n"
        "<evidence>\n"
        f"{evidence_text}\n"
        "</evidence>"
    )


async def _call_model(prompt: str, session_id: str, run_id: str) -> str:
    from app.llm import LLMClient, clear_usage_context, set_usage_context, stream_chat_collect_async

    token = set_usage_context(session_id=session_id, parent_run_id=run_id, phase="pipeline", call_purpose="pipeline_report")
    try:
        client = LLMClient(timeout=MODEL_TIMEOUT_SECONDS, retries=1)
        return await asyncio.wait_for(
            stream_chat_collect_async(client, prompt, max_tokens=MAX_REPORT_TOKENS, enable_thinking=False),
            timeout=MODEL_TIMEOUT_SECONDS + 30,
        )
    finally:
        clear_usage_context(token)


async def write_report(
    *,
    target: Path,
    evidence: List[Dict[str, Any]],
    instructions: str,
    language: str,
    session_id: str,
    run_id: str,
) -> Dict[str, Any]:
    evidence_text, truncated = await asyncio.to_thread(build_evidence, evidence)
    prompt = build_prompt(evidence_text, instructions, language)
    text = (await _call_model(prompt, session_id, run_id)).strip()
    if not text:
        from .engine import NodeFailure

        raise NodeFailure("the model returned an empty report")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text + "\n", encoding="utf-8")
    return {
        "evidence_items": len(evidence),
        "evidence_chars": len(evidence_text),
        "evidence_truncated": truncated,
        "report_chars": len(text),
    }
