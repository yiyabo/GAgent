"""Deliverable publish policy: extension tables, blocked paths, classification.

Extracted from ``publisher.py`` (clusters "扩展名/屏蔽策略常量 12 组" and
"路径政策") per ``design/2026-09-24-backend-godfiles-refactor-plan.md`` §4.3.
This sibling owns the extension whitelists, the blocked segment/dir/filename
tables and every predicate that decides whether a source file may enter a
deliverable module.

Compatibility contract (``deep_think/gating.py`` + ``phagescope.py`` pattern):
the ``publisher`` facade re-exports every name defined here, so
``app/services/artifacts/projector.py`` and every other import site keep
working unchanged.  ``MANUSCRIPT_PDF_STEMS`` stays in manual sync with
``scripts/archive/repair_deliverable_pdf_layout.py``.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import List, Optional

CODE_EXTS = {
    ".py",
    ".ipynb",
    ".r",
    ".jl",
    ".sh",
    ".bash",
    ".zsh",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".java",
    ".cpp",
    ".c",
    ".rs",
    ".go",
    ".sql",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
}

TABULAR_EXTS = {
    ".csv",
    ".tsv",
    ".xlsx",
    ".xls",
    ".jsonl",
    ".parquet",
    ".json",
    ".fasta",
    ".fa",
    ".fna",
    ".faa",
    ".out",
    ".nwk",
    ".newick",
    ".graphml",
    ".sqlite",
    ".db",
}

IMAGE_EXTS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".svg",
    ".gif",
    ".bmp",
    ".tiff",
    ".webp",
}

DOC_EXTS = {
    ".md",
    ".markdown",
    ".txt",
    ".docx",
    ".rtf",
}

PAPER_EXTS = {
    ".tex",
    ".pdf",
    ".cls",
    ".sty",
    ".bst",
}

REF_EXTS = {
    ".bib",
}

# PDFs next to manuscript sources are usually downloaded references; keep only obvious build outputs in paper/
MANUSCRIPT_PDF_STEMS = {
    "main",
    "manuscript",
    "paper",
    "submission",
    "preprint",
}

DOC_ALLOWED_STEMS = {
    "abstract",
    "introduction",
    "method",
    "methods",
    "experiment",
    "result",
    "results",
    "discussion",
    "conclusion",
    "reference",
    "references",
    "report",
    "analysis",
    "evidence_coverage",
    "release_summary",
    "study_matrix",
    "survey",
    "summary",
}

SOURCE_OWNERSHIP_MAP = ".source_owners.json"

MAX_PATH_CANDIDATE_LENGTH = 1024

NOISE_PATH_SEGMENTS = {
    "/tool_outputs/",
    "/information_sessions/",
}

NOISE_FILENAMES = {
    "manifest.json",
    "preview.json",
    "result.json",
}

BLOCKED_SOURCE_SEGMENTS = {
    "node_modules",
    ".git",
    "__MACOSX",
    "__pycache__",
    ".venv",
    "venv",
    ".pytest_cache",
    ".mypy_cache",
    ".tox",
    ".eggs",
    "dist",
    ".cursor",
    ".codex",
    ".claude",
}

BLOCKED_PROJECT_DIRS = {
    "app",
    "tool_box",
    "execute_memory",
    "web-ui",
    "scripts",
    "docker",
    ".github",
}

BLOCKED_SOURCE_FILENAMES = {
    ".DS_Store",
    "Thumbs.db",
}

_CC_RUN_ARTIFACT_RE = re.compile(r"^run_\d{8}_\d{6}_")

# Agent-explicit deliverables (only deliverable_submit + manuscript tools publish)
DELIVERABLE_SUBMIT_KEY = "deliverable_submit"

CC_INTERMEDIATE_SCRIPT_EXTS = {".py", ".sh", ".bash", ".r", ".jl"}


class _PolicyMethods:
    """Publish-policy predicates mixed into ``DeliverablePublisher``."""

    def _paper_file_is_substantive(self, file_path: Path) -> bool:
        if not file_path.exists() or not file_path.is_file():
            return False
        rel = str(file_path).replace("\\", "/")
        if rel.endswith("/paper/main.tex"):
            sections_dir = file_path.parent / "sections"
            if not sections_dir.exists():
                return False
            for section_file in sections_dir.glob("*.tex"):
                try:
                    if self._paper_builder.is_substantive_section_text(
                        section_file.read_text(encoding="utf-8")
                    ):
                        return True
                except Exception:
                    continue
            return False
        if file_path.suffix.lower() == ".tex" and file_path.parent.name == "sections":
            try:
                return self._paper_builder.is_substantive_section_text(
                    file_path.read_text(encoding="utf-8")
                )
            except Exception:
                return False
        return True

    @staticmethod
    def _refs_file_is_substantive(file_path: Path) -> bool:
        if not file_path.exists() or not file_path.is_file():
            return False
        if file_path.suffix.lower() != ".bib":
            return True
        try:
            text = file_path.read_text(encoding="utf-8")
        except Exception:
            return False
        return bool(re.search(r"@\w+\s*\{", text))

    def _source_path_is_blocked(self, source_path: str) -> bool:
        """Check if a source_path points to project infrastructure (not research output)."""
        normalized = source_path.replace("\\", "/").strip("/")
        for blocked_dir in BLOCKED_PROJECT_DIRS:
            if normalized.startswith(blocked_dir + "/") or normalized == blocked_dir:
                return True
        if normalized.startswith("runtime/") and "/deliverables/" not in normalized:
            parts = normalized.split("/")
            if len(parts) >= 2 and not parts[1].startswith("session_"):
                return True
        return False

    def _file_belongs_in_deliverables(self, file_path: Path, module: str) -> bool:
        """Heuristic check for orphan files with no source tracking."""
        if module == "docs":
            return True
        if module == "paper":
            return self._paper_file_is_substantive(file_path)
        if module == "refs":
            return self._refs_file_is_substantive(file_path)
        if module == "image_tabular":
            return file_path.suffix.lower() in (IMAGE_EXTS | TABULAR_EXTS | {".pdf"})
        if module == "code":
            name_lower = file_path.name.lower()
            agent_infra_names = {
                "action_execution.py", "action_handlers.py", "agent.py",
                "agent_routes.py", "artifact_routes.py", "chat_routes.py",
                "plan_routes.py", "stream.py", "llm.py", "settings.py",
                "deep_think_agent.py", "plan_executor.py", "plan_decomposer.py",
                "tool_schemas.py", "tool_executor.py", "publisher.py",
                "session_paths.py", "database.py", "database_config.py",
                "database_pool.py", "middleware.py",
            }
            if name_lower in agent_infra_names:
                return False
            return True
        return True

    def _resolve_path(self, value: str, *, session_dir: Path) -> Optional[Path]:
        raw = str(value or "").strip()
        if not raw:
            return None
        if "\n" in raw or "\r" in raw:
            return None
        if len(raw) > MAX_PATH_CANDIDATE_LENGTH:
            return None
        if raw.startswith("http://") or raw.startswith("https://"):
            return None
        if raw.startswith("~"):
            return None

        path = Path(raw)
        candidates: List[Path] = []
        if path.is_absolute():
            candidates.append(path)
        else:
            # Prioritize unified raw_files/ path structure
            raw_files_candidate = session_dir / "raw_files" / path
            candidates.append(raw_files_candidate)
            candidates.append(session_dir / path)
            candidates.append(self._project_root / path)

        for item in candidates:
            try:
                resolved = item.resolve()
            except Exception:
                continue
            if not resolved.exists():
                continue
            if not self._is_allowed_source(item):
                continue
            return resolved
        return None

    def _is_allowed_source(self, path: Path) -> bool:
        if path.name in BLOCKED_SOURCE_FILENAMES:
            return False
        if path.name.startswith("._"):
            return False
        path_parts = set(Path(os.path.abspath(str(path))).parts)
        if path_parts & BLOCKED_SOURCE_SEGMENTS:
            return False

        lexical_abs = Path(os.path.abspath(str(path)))
        if self._is_within(lexical_abs, self._project_root):
            if self._is_in_blocked_project_dir(lexical_abs):
                return False
            return True
        try:
            resolved = path.resolve()
        except Exception:
            return False
        if self._is_within(resolved, self._project_root):
            if self._is_in_blocked_project_dir(resolved):
                return False
            return True
        return False

    def _is_in_blocked_project_dir(self, abs_path: Path) -> bool:
        """Check if an absolute path falls inside a blocked project directory."""
        for blocked_dir in BLOCKED_PROJECT_DIRS:
            blocked_abs = (self._project_root / blocked_dir).resolve()
            if self._is_within(abs_path, blocked_abs):
                return True
        return False

    @staticmethod
    def _is_within(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    def _classify_module(self, path: Path) -> Optional[str]:
        path_lower = str(path).lower()
        suffix = path.suffix.lower()
        file_stem = path.stem.lower()

        # Reference materials take priority over generic /paper/ path match
        if "/refs/" in path_lower or suffix in REF_EXTS or "references" in path.name.lower():
            return "refs"
        if "/reference_paper" in path_lower and suffix == ".pdf":
            return "refs"
        # Manuscript LaTeX (and class/style) always live under paper/
        if suffix in {".tex", ".cls", ".sty", ".bst"}:
            return "paper"
        # Manuscript markdown files (draft, final, etc.)
        if suffix == ".md" and any(keyword in file_stem for keyword in ("manuscript", "draft", "paper", "submission")):
            return "paper"
        # PDFs under paper/ are almost always downloaded papers, not the compiled manuscript
        if suffix == ".pdf" and "/paper/" in path_lower:
            if file_stem in MANUSCRIPT_PDF_STEMS:
                return "paper"
            return "refs"
        if "/paper/" in path_lower:
            return "paper"
        if "/code/" in path_lower or suffix in CODE_EXTS:
            return "code"
        if suffix in IMAGE_EXTS or suffix in TABULAR_EXTS:
            return "image_tabular"
        if suffix == ".pdf":
            # PDFs with chart/plot/figure/visualization keywords are image_tabular
            if any(token in path_lower for token in ("/fig", "/figure", "/table", "/plot", "/chart", "pcoa", "pca", "visualization")):
                return "image_tabular"
            # PDFs from literature_pipeline are downloaded references, not our paper
            if any(token in path_lower for token in (
                "/literature_pipeline/",
                "/review_pack",
                "/reference_paper",
            )):
                return "refs"
            loose_ref_tokens = (
                "/refs/",
                "/references/",
                "/downloads/",
                "/literature/",
                "/citation",
                "/bibliography",
                "/preprint",
                "/arxiv",
                "/supplement",
                "/supplementary",
            )
            if any(t in path_lower for t in loose_ref_tokens):
                return "refs"
            return None
        if "/docs/" in path_lower or suffix in DOC_EXTS:
            if any(keyword in file_stem for keyword in DOC_ALLOWED_STEMS):
                return "docs"
            return None
        if any(keyword in file_stem for keyword in DOC_ALLOWED_STEMS) and suffix in {".md", ".txt"}:
            return "docs"
        return None

    def _should_publish_file(self, module: str, source_path: Path) -> bool:
        if module not in self._settings.modules:
            return False
        if self._is_noise_artifact_file(source_path):
            return False
        if self._is_cc_intermediate_artifact(source_path):
            return False
        if module == "docs":
            return self._is_allowed_doc_file(source_path)
        if module == "paper":
            return self._paper_file_is_substantive(source_path)
        if module == "refs":
            return self._refs_file_is_substantive(source_path)
        if module == "code":
            return self._is_allowed_code_file(source_path)
        return True

    @staticmethod
    def _is_allowed_code_file(path: Path) -> bool:
        """Only actual code files belong in the code module; raw JSON/YAML data files do not."""
        return path.suffix.lower() in CODE_EXTS

    def _is_noise_artifact_file(self, source_path: Path) -> bool:
        file_name = source_path.name.lower()
        if file_name not in NOISE_FILENAMES:
            return False
        normalized = "/" + str(source_path).replace("\\", "/").lower()
        return any(segment in normalized for segment in NOISE_PATH_SEGMENTS)

    @staticmethod
    def _is_cc_intermediate_artifact(source_path: Path) -> bool:
        """CC auto-generated one-off scripts (run_YYYYMMDD_HHMMSS_*) are working artifacts, not deliverables."""
        if source_path.suffix.lower() not in CC_INTERMEDIATE_SCRIPT_EXTS:
            return False
        return bool(_CC_RUN_ARTIFACT_RE.match(source_path.name))

    @staticmethod
    def _is_allowed_doc_file(path: Path) -> bool:
        if path.suffix.lower() not in DOC_EXTS:
            return False
        stem = path.stem.lower()
        return any(keyword in stem for keyword in DOC_ALLOWED_STEMS)


__all__ = [
    "CODE_EXTS",
    "TABULAR_EXTS",
    "IMAGE_EXTS",
    "DOC_EXTS",
    "PAPER_EXTS",
    "REF_EXTS",
    "MANUSCRIPT_PDF_STEMS",
    "DOC_ALLOWED_STEMS",
    "SOURCE_OWNERSHIP_MAP",
    "MAX_PATH_CANDIDATE_LENGTH",
    "NOISE_PATH_SEGMENTS",
    "NOISE_FILENAMES",
    "BLOCKED_SOURCE_SEGMENTS",
    "BLOCKED_PROJECT_DIRS",
    "BLOCKED_SOURCE_FILENAMES",
    "DELIVERABLE_SUBMIT_KEY",
    "CC_INTERMEDIATE_SCRIPT_EXTS",
]
