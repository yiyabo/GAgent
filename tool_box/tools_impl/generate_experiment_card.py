from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from app.services.paper_replication import (
    Assay,
    ExperimentCard,
    discover_experiments,
    load_experiment_card,
    save_experiment_card,
)
from .document_reader import read_pdf

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
_UPLOADS_ROOT = _PROJECT_ROOT / "data" / "uploads"


def _slugify(name: str) -> str:
    """Convert filename stem to a safe experiment id."""
    cleaned = []
    for ch in name.lower():
        if ch.isalnum():
            cleaned.append(ch)
        else:
            cleaned.append("_")
    slug = "_".join(filter(None, "".join(cleaned).split("_")))
    return slug or "experiment"


def _find_latest_pdf(root: Path) -> Optional[Path]:
    """Find the most recently modified PDF under a root directory."""
    if not root.exists():
        return None
    newest: Optional[Path] = None
    newest_mtime: float = -1
    for candidate in root.rglob("*.pdf"):
        try:
            mtime = candidate.stat().st_mtime
        except OSError:
            continue
        if mtime > newest_mtime:
            newest = candidate
            newest_mtime = mtime
    return newest


def _find_existing_card_for_pdf(pdf_path: Path) -> Optional[Tuple[str, ExperimentCard, Path]]:
    """Return existing (experiment_id, card, path) if any card references this pdf_path."""
    for exp_id in discover_experiments():
        try:
            card = load_experiment_card(exp_id)
            if Path(card.paper.get("pdf_path", "")).resolve() == pdf_path.resolve():
                card_path = _PROJECT_ROOT / "data" / exp_id / "card.yaml"
                return exp_id, card, card_path
        except Exception:
            continue
    return None


async def generate_experiment_card_handler(
    experiment_id: Optional[str] = None,
    pdf_path: Optional[str] = None,
    code_root: Optional[str] = None,
    notes: Optional[str] = None,
    overwrite: bool = False,
    uploads_root: Optional[str] = None,  # optional override for testing
) -> Dict[str, Any]:
    """Generate and persist an ExperimentCard from a paper PDF.

    If pdf_path is not provided, the handler will look for the latest uploaded PDF
    under data/uploads (or uploads_root override). If experiment_id is not provided,
    it is derived from the PDF filename stem.
    """
    tool_name = "generate_experiment_card"

    # Resolve uploads root for auto-discovery
    uploads_dir = Path(uploads_root).expanduser() if uploads_root else _UPLOADS_ROOT
    uploads_dir = uploads_dir.resolve()

    # Resolve PDF path (explicit > derived from experiment_id > latest upload)
    abs_pdf: Optional[Path] = None
    if pdf_path:
        try:
            abs_pdf = Path(pdf_path).expanduser().resolve()
        except Exception:
            return {
                "tool": tool_name,
                "success": False,
                "error": f"Invalid pdf_path: {pdf_path}",
                "code": "invalid_path",
            }
    elif experiment_id:
        candidate = _PROJECT_ROOT / "data" / experiment_id / "paper.pdf"
        if candidate.exists():
            abs_pdf = candidate.resolve()
    if abs_pdf is None:
        abs_pdf = _find_latest_pdf(uploads_dir)
        if abs_pdf:
            logger.info("Auto-selected latest uploaded PDF: %s", abs_pdf)

    if abs_pdf is None:
        return {
            "tool": tool_name,
            "success": False,
            "error": "pdf_path not provided and no PDF found under uploads.",
            "code": "not_found",
        }
    if not abs_pdf.exists():
        return {
            "tool": tool_name,
            "success": False,
            "error": f"PDF not found at {abs_pdf}",
            "code": "not_found",
        }

    # If a card already exists for this PDF and overwrite is False, reuse it
    existing = _find_existing_card_for_pdf(abs_pdf)
    if existing and not overwrite:
        exp_id, card, card_path = existing
        return {
            "tool": tool_name,
            "success": True,
            "experiment_id": exp_id,
            "card_path": str(card_path),
            "card": card.to_dict(),
            "metadata": {
                "pdf_pages": pdf_result.get("page_count"),
                "pdf_file": pdf_result.get("file_name"),
                "reused": True,
            },
        }

    # Derive experiment_id if missing (title slug + short hash of pdf path)
    base_slug = _slugify(abs_pdf.stem)
    if experiment_id:
        exp_id = experiment_id
    else:
        digest = hashlib.md5(str(abs_pdf).encode("utf-8")).hexdigest()[:8]
        exp_id = f"{base_slug}_{digest}"

    pdf_result = await read_pdf(str(abs_pdf))
    if not pdf_result.get("success"):
        return {
            "tool": tool_name,
            "success": False,
            "error": pdf_result.get("error", "Failed to read PDF"),
            "details": pdf_result,
            "code": "read_failed",
        }

    metadata = pdf_result.get("metadata") or {}
    def _to_str(value: Optional[Any]) -> Optional[str]:
        if value is None:
            return None
        try:
            return str(value)
        except Exception:
            return None

    title = _to_str(metadata.get("title")) or abs_pdf.stem
    venue = _to_str(metadata.get("subject"))
    year = _to_str(metadata.get("year")) or None
    doi = _to_str(metadata.get("doi"))
    description = f"Reproduce results from paper '{title}'."
    if pdf_result.get("text"):
        excerpt = str(pdf_result["text"]).strip()
        if excerpt:
            description += f" Key excerpt: {excerpt[:400]}..."

    details: Dict[str, Any] = {"pdf_path": str(abs_pdf)}
    if code_root:
        details["code_root"] = str(Path(code_root).expanduser())

    notes_list = []
    if notes:
        notes_list = [line for line in str(notes).splitlines() if line.strip()]

    card = ExperimentCard(
        paper={
            "title": title,
            "venue": venue,
            "year": year,
            "doi": doi,
            "pdf_path": str(abs_pdf),
        },
        experiment={
            "id": exp_id,
            "type": "paper_reproduction",
            "name": title,
            "goal": description,
        },
        task={
            "description": description,
            "input_modality": ["paper"],
            "output_modality": ["reproduction_report"],
        },
        assay=Assay(
            type="paper_reproduction",
            description=description,
            details=details,
        ),
        metrics=[],
        artifacts={},
        constraints={},
        notes=notes_list,
    )

    try:
        card_path = save_experiment_card(exp_id, card, overwrite=overwrite)
    except FileExistsError as exc:
        return {
            "tool": tool_name,
            "success": False,
            "error": str(exc),
            "code": "already_exists",
        }
    except Exception as exc:
        logger.exception("Failed to save experiment card: %s", exc)
        return {
            "tool": tool_name,
            "success": False,
            "error": f"Failed to save experiment card: {exc}",
            "code": "save_failed",
        }

    return {
        "tool": tool_name,
        "success": True,
        "experiment_id": exp_id,
        "card_path": str(card_path),
        "card": card.to_dict(),
        "metadata": {
            "pdf_pages": pdf_result.get("page_count"),
            "pdf_file": pdf_result.get("file_name"),
        },
    }


generate_experiment_card_tool: Dict[str, Any] = {
    "name": "generate_experiment_card",
    "description": (
        "Read a paper PDF and generate an ExperimentCard at data/<experiment_id>/card.yaml. "
        "If pdf_path is omitted, the latest uploaded PDF under data/uploads is used; "
        "if experiment_id is omitted, it is derived from the PDF filename."
    ),
    "category": "paper_replication",
    "parameters_schema": {
        "type": "object",
        "properties": {
            "experiment_id": {
                "type": "string",
                "description": "Identifier for the experiment (data/<experiment_id>). If omitted, derived from PDF name.",
            },
            "pdf_path": {
                "type": "string",
                "description": "Path to the source paper PDF. If omitted, use the latest uploaded PDF.",
            },
            "code_root": {
                "type": "string",
                "description": "Optional code repository or root directory to include in the card.",
            },
            "notes": {
                "type": "string",
                "description": "Optional notes to append to the card.",
            },
            "overwrite": {
                "type": "boolean",
                "description": "Whether to overwrite an existing card.yaml if present.",
                "default": False,
            },
        },
        "required": [],
    },
    "handler": generate_experiment_card_handler,
    "tags": ["paper", "replication", "metadata", "card"],
    "examples": [
        "Generate a card using the latest uploaded PDF and save to data/<derived-id>/card.yaml.",
        "Generate a card for experiment_2 using data/experiment_2/paper.pdf.",
    ],
}
