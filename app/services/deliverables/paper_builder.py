from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple


SECTION_ORDER: Tuple[str, ...] = (
    "abstract",
    "introduction",
    "method",
    "experiment",
    "result",
    "discussion",
    "conclusion",
)

SECTION_TITLES: Dict[str, str] = {
    "abstract": "Abstract",
    "introduction": "Introduction",
    "method": "Method",
    "experiment": "Experiment",
    "result": "Result",
    "discussion": "Discussion",
    "conclusion": "Conclusion",
}

SECTION_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "abstract": ("abstract", "structured abstract", "overview"),
    "introduction": ("introduction", "intro", "background", "motivation"),
    "method": ("methods", "method", "methodology", "approach", "pipeline"),
    "experiment": ("experiments", "experiment", "evaluation", "benchmark", "ablation"),
    "result": ("results", "result", "findings", "finding", "performance"),
    "discussion": ("discussion", "discussion and conclusion", "interpretation", "implications"),
    "conclusion": ("conclusion", "conclusions", "future work", "summary"),
}

PLACEHOLDER_MARKER = "AUTO_PLACEHOLDER"
_TRUTHY_VALUES = {"1", "true", "yes", "on", "y"}


def _env_enabled(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in _TRUTHY_VALUES


@dataclass(frozen=True)
class PaperStatus:
    completed_sections: List[str]
    missing_sections: List[str]
    total_sections: int
    completed_count: int

    def to_dict(self) -> Dict[str, object]:
        return {
            "completed_sections": list(self.completed_sections),
            "missing_sections": list(self.missing_sections),
            "total_sections": self.total_sections,
            "completed_count": self.completed_count,
        }


class PaperBuilder:
    def _legacy_infer_section(self, text: str) -> Optional[str]:
        haystack = str(text or "").strip().lower()
        if not haystack:
            return None
        for section in SECTION_ORDER:
            keywords = SECTION_KEYWORDS.get(section, ())
            if any(keyword and keyword in haystack for keyword in keywords):
                return section
        # Backward-compat fallback for emergency rollback.
        return "introduction"

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        return re.findall(r"[a-z0-9]+", text.lower())

    @staticmethod
    def _contains_phrase(haystack: str, phrase: str) -> bool:
        return bool(re.search(rf"\b{re.escape(phrase)}\b", haystack))

    def infer_section(
        self,
        task_name: Optional[str],
        task_instruction: Optional[str],
        text: Optional[str] = None,
        *,
        explicit_section: Optional[str] = None,
    ) -> Optional[str]:
        # Explicit section from plan/task metadata takes precedence
        if explicit_section is not None:
            section_key = str(explicit_section).strip().lower()
            if section_key in SECTION_ORDER:
                return section_key
        candidates: List[str] = []
        if task_name:
            candidates.append(str(task_name))
        if task_instruction:
            candidates.append(str(task_instruction))
        task_haystack = " ".join(part.strip() for part in candidates if part and str(part).strip()).lower()
        if not task_haystack:
            return None

        if not _env_enabled("PAPER_SECTION_INFER_V2", True):
            return self._legacy_infer_section(task_haystack)

        tokens = set(self._tokenize(task_haystack))
        best_section: Optional[str] = None
        best_score = 0
        for section in SECTION_ORDER:
            keywords = tuple(k.strip().lower() for k in SECTION_KEYWORDS.get(section, ()) if k and k.strip())
            score = 0
            keyword_count = len(keywords)
            for idx, keyword in enumerate(keywords):
                # Earlier keywords are higher-priority anchors.
                weight = (keyword_count - idx) * 10
                if " " in keyword:
                    if self._contains_phrase(task_haystack, keyword):
                        score = max(score, 200 + weight)
                else:
                    if keyword in tokens:
                        score = max(score, 200 + weight)
                    elif self._contains_phrase(task_haystack, keyword):
                        score = max(score, 100 + weight)
            if score > best_score:
                best_score = score
                best_section = section
        return best_section

    def ensure_structure(
        self,
        *,
        paper_dir: Path,
        refs_dir: Path,
        title: str,
    ) -> None:
        sections_dir = paper_dir / "sections"
        sections_dir.mkdir(parents=True, exist_ok=True)
        refs_dir.mkdir(parents=True, exist_ok=True)
        for section in SECTION_ORDER:
            section_path = self._section_path(paper_dir, section)
            if section_path.exists():
                continue
            section_path.write_text(
                self._placeholder_section(section),
                encoding="utf-8",
            )
        main_tex = paper_dir / "main.tex"
        if not main_tex.exists():
            main_tex.write_text(self._main_tex_template(title=title), encoding="utf-8")
        references_path = refs_dir / "references.bib"
        if not references_path.exists():
            references_path.write_text(
                "% references\n",
                encoding="utf-8",
            )

    def update_section(
        self,
        *,
        paper_dir: Path,
        section: str,
        content: str,
    ) -> Path:
        section_key = section.strip().lower()
        if section_key not in SECTION_ORDER:
            raise ValueError(f"Unsupported paper section: {section}")
        section_path = self._section_path(paper_dir, section_key)
        section_path.parent.mkdir(parents=True, exist_ok=True)
        rendered = self._render_section_content(section_key, content or "")
        section_path.write_text(rendered, encoding="utf-8")
        return section_path

    def merge_bib_entries(
        self,
        *,
        refs_dir: Path,
        bib_text: str,
    ) -> Optional[Path]:
        normalized = str(bib_text or "").strip()
        if not normalized:
            return None
        refs_dir.mkdir(parents=True, exist_ok=True)
        references_path = refs_dir / "references.bib"
        existing = references_path.read_text(encoding="utf-8") if references_path.exists() else ""
        existing_keys = set(self._extract_bib_keys(existing))
        new_entries = self._split_bib_entries(normalized)
        if not new_entries:
            return None
        merged_chunks: List[str] = []
        for entry in new_entries:
            keys = self._extract_bib_keys(entry)
            if not keys:
                continue
            key = keys[0]
            if key in existing_keys:
                continue
            existing_keys.add(key)
            merged_chunks.append(entry.strip())
        if not merged_chunks:
            return references_path if references_path.exists() else None
        with references_path.open("a", encoding="utf-8") as handle:
            for chunk in merged_chunks:
                handle.write("\n\n")
                handle.write(chunk)
                handle.write("\n")
        return references_path

    def get_status(self, *, paper_dir: Path) -> PaperStatus:
        completed: List[str] = []
        missing: List[str] = []
        for section in SECTION_ORDER:
            section_path = self._section_path(paper_dir, section)
            if not section_path.exists():
                missing.append(section)
                continue
            text = section_path.read_text(encoding="utf-8")
            if PLACEHOLDER_MARKER in text:
                missing.append(section)
            else:
                completed.append(section)
        return PaperStatus(
            completed_sections=completed,
            missing_sections=missing,
            total_sections=len(SECTION_ORDER),
            completed_count=len(completed),
        )

    def _section_path(self, paper_dir: Path, section: str) -> Path:
        return paper_dir / "sections" / f"{section}.tex"

    def _placeholder_section(self, section: str) -> str:
        title = SECTION_TITLES.get(section, section.title())
        if section == "abstract":
            return (
                f"% {PLACEHOLDER_MARKER}\n"
                "\\begin{abstract}\n"
                f"% TODO: {title}\n"
                "\\end{abstract}\n"
            )
        return (
            f"% {PLACEHOLDER_MARKER}\n"
            f"\\section{{{title}}}\n"
            f"% TODO: {title}\n"
        )

    def _main_tex_template(self, *, title: str) -> str:
        safe_title = self._escape_text(title or "Untitled Project")
        includes = "\n".join([f"\\input{{sections/{name}}}" for name in SECTION_ORDER])
        return (
            "\\documentclass[11pt]{article}\n"
            "\\usepackage[utf8]{inputenc}\n"
            "\\usepackage[T1]{fontenc}\n"
            "\\usepackage{geometry}\n"
            "\\usepackage{hyperref}\n"
            "\\usepackage{graphicx}\n"
            "\\usepackage{booktabs}\n"
            "\\usepackage{longtable}\n"
            "\\usepackage{amsmath}\n"
            "\\usepackage{amssymb}\n"
            "\\usepackage{amsfonts}\n"
            "\\usepackage[numbers]{natbib}\n"
            "\\graphicspath{{figures/}}\n"
            "\\geometry{margin=1in}\n"
            f"\\title{{{safe_title}}}\n"
            "\\author{}\n"
            "\\date{\\today}\n\n"
            "\\begin{document}\n"
            "\\maketitle\n\n"
            f"{includes}\n\n"
            "\\bibliographystyle{plainnat}\n"
            "\\bibliography{../refs/references}\n"
            "\\end{document}\n"
        )

    def _render_section_content(self, section: str, content: str) -> str:
        normalized = content.strip()
        if not normalized:
            return self._placeholder_section(section)
        if section == "abstract":
            if "\\begin{abstract}" in normalized:
                return normalized
            return (
                "\\begin{abstract}\n"
                f"{self._markdown_to_latex(normalized)}\n"
                "\\end{abstract}\n"
            )
        if "\\section{" in normalized:
            return normalized
        title = SECTION_TITLES.get(section, section.title())
        return f"\\section{{{title}}}\n{self._markdown_to_latex(normalized)}\n"

    def _markdown_to_latex(self, text: str) -> str:
        lines = text.replace("\r\n", "\n").split("\n")
        output: List[str] = []
        in_list = False
        in_enum = False
        in_verbatim = False
        table_lines: List[str] = []
        in_table = False

        for line in lines:
            stripped = line.strip()

            # Code blocks
            if stripped.startswith("```"):
                if in_list:
                    output.append("\\end{itemize}")
                    in_list = False
                if in_enum:
                    output.append("\\end{enumerate}")
                    in_enum = False
                if in_table:
                    output.extend(self._render_table(table_lines))
                    table_lines, in_table = [], False
                if in_verbatim:
                    output.append("\\end{verbatim}")
                    in_verbatim = False
                else:
                    output.append("\\begin{verbatim}")
                    in_verbatim = True
                continue

            if in_verbatim:
                output.append(line)
                continue

            # Image: ![caption](path)
            img_match = re.match(r"!\[([^\]]*)\]\(([^)]+)\)", stripped)
            if img_match:
                if in_list:
                    output.append("\\end{itemize}")
                    in_list = False
                if in_enum:
                    output.append("\\end{enumerate}")
                    in_enum = False
                if in_table:
                    output.extend(self._render_table(table_lines))
                    table_lines, in_table = [], False
                caption = img_match.group(1).strip()
                img_path = img_match.group(2).strip()
                img_ref = self._normalize_figure_reference(img_path)
                output.append("\\begin{figure}[h]")
                output.append("\\centering")
                output.append(f"\\includegraphics[width=0.8\\textwidth]{{{img_ref}}}")
                if caption:
                    output.append(f"\\caption{{{self._escape_text(caption)}}}")
                output.append("\\end{figure}")
                continue

            # Table detection: lines starting and ending with |
            if "|" in stripped and stripped.startswith("|") and stripped.endswith("|"):
                if not in_table:
                    if in_list:
                        output.append("\\end{itemize}")
                        in_list = False
                    if in_enum:
                        output.append("\\end{enumerate}")
                        in_enum = False
                    in_table = True
                table_lines.append(stripped)
                continue
            elif in_table:
                output.extend(self._render_table(table_lines))
                table_lines, in_table = [], False

            # Empty line
            if not stripped:
                if in_list:
                    output.append("\\end{itemize}")
                    in_list = False
                if in_enum:
                    output.append("\\end{enumerate}")
                    in_enum = False
                output.append("")
                continue

            # Unordered list
            if stripped.startswith("- "):
                if in_enum:
                    output.append("\\end{enumerate}")
                    in_enum = False
                if not in_list:
                    output.append("\\begin{itemize}")
                    in_list = True
                output.append(f"\\item {self._inline_to_latex(stripped[2:].strip())}")
                continue

            # Numbered list
            num_match = re.match(r"(\d+)\.\s+(.+)", stripped)
            if num_match:
                if in_list:
                    output.append("\\end{itemize}")
                    in_list = False
                if not in_enum:
                    output.append("\\begin{enumerate}")
                    in_enum = True
                output.append(f"\\item {self._inline_to_latex(num_match.group(2).strip())}")
                continue

            if in_list:
                output.append("\\end{itemize}")
                in_list = False
            if in_enum:
                output.append("\\end{enumerate}")
                in_enum = False

            if stripped.startswith("### "):
                output.append(f"\\subsubsection{{{self._escape_text(stripped[4:].strip())}}}")
                continue
            if stripped.startswith("## "):
                output.append(f"\\subsection{{{self._escape_text(stripped[3:].strip())}}}")
                continue
            if stripped.startswith("# "):
                output.append(f"\\subsection{{{self._escape_text(stripped[2:].strip())}}}")
                continue

            output.append(self._inline_to_latex(stripped))

        if in_verbatim:
            output.append("\\end{verbatim}")
        if in_list:
            output.append("\\end{itemize}")
        if in_enum:
            output.append("\\end{enumerate}")
        if in_table:
            output.extend(self._render_table(table_lines))

        return "\n".join(output).strip()

    def _render_table(self, lines: List[str]) -> List[str]:
        """Convert Markdown table lines to LaTeX tabular."""
        if not lines:
            return []
        rows: List[List[str]] = []
        has_header = False
        for line in lines:
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(re.match(r"\s*:?-+:?\s*$", c) for c in cells if c.strip()):
                has_header = True
                continue
            rows.append(cells)
        if not rows:
            return []
        ncols = max(len(r) for r in rows)
        col_spec = "l" * ncols
        output = [
            "\\begin{table}[h]",
            "\\centering",
            f"\\begin{{tabular}}{{{col_spec}}}",
            "\\toprule",
        ]
        for i, row in enumerate(rows):
            while len(row) < ncols:
                row.append("")
            cells = " & ".join(self._escape_text(c) for c in row)
            output.append(f"{cells} \\\\")
            if i == 0 and has_header:
                output.append("\\midrule")
        output.extend([
            "\\bottomrule",
            "\\end{tabular}",
            "\\end{table}",
        ])
        return output

    def _normalize_figure_reference(self, img_path: str) -> str:
        normalized = (img_path or "").strip()
        if not normalized:
            return normalized
        if re.match(r"^[A-Za-z][A-Za-z0-9+.\-]*://", normalized):
            return normalized
        return Path(normalized.split("?", 1)[0].split("#", 1)[0]).name or normalized

    def _inline_to_latex(self, text: str) -> str:
        # Protect math environments from escaping
        math_tokens: List[Tuple[str, str]] = []

        def _save_math(match: re.Match[str]) -> str:
            token = f"\x00MATH{len(math_tokens)}\x00"
            math_tokens.append((token, match.group(0)))
            return token

        replaced = re.sub(r"\$\$.+?\$\$", _save_math, text, flags=re.DOTALL)
        replaced = re.sub(r"\$(?!\$).+?\$", _save_math, replaced)

        # Multi-cite: [@key1; @key2] → \cite{key1, key2}
        def _multi_cite(m: re.Match[str]) -> str:
            keys = [k.strip().lstrip("@") for k in m.group(1).split(";")]
            return "\\cite{" + ", ".join(keys) + "}"

        replaced = re.sub(
            r"\[(@[A-Za-z0-9._:\-]+(?:\s*;\s*@[A-Za-z0-9._:\-]+)+)\]",
            _multi_cite,
            replaced,
        )
        # Single cite: [@key] → \cite{key}
        replaced = re.sub(
            r"\[@([A-Za-z0-9._:\-]+)\]",
            lambda m: f"\\cite{{{m.group(1)}}}",
            replaced,
        )
        replaced = re.sub(
            r"\*\*(.+?)\*\*",
            lambda m: f"\\textbf{{{self._escape_text(m.group(1))}}}",
            replaced,
        )
        replaced = re.sub(
            r"\*(.+?)\*",
            lambda m: f"\\textit{{{self._escape_text(m.group(1))}}}",
            replaced,
        )
        replaced = self._escape_text(replaced, preserve_commands=True)

        # Restore math environments
        for token, original in math_tokens:
            replaced = replaced.replace(token, original)

        return replaced

    def _escape_text(self, text: str, *, preserve_commands: bool = False) -> str:
        if not text:
            return ""
        if preserve_commands:
            tokens: List[Tuple[str, str]] = []

            def _capture(match: re.Match[str]) -> str:
                token = f"\x00LCMD{len(tokens)}\x00"
                tokens.append((token, match.group(0)))
                return token

            protected = re.sub(r"\\[A-Za-z]+\{[^{}]*\}", _capture, text)
            escaped = (
                protected.replace("&", "\\&")
                .replace("%", "\\%")
                .replace("$", "\\$")
                .replace("#", "\\#")
                .replace("_", "\\_")
            )
            for token, cmd in tokens:
                escaped = escaped.replace(token, cmd)
            return escaped
        return (
            text.replace("&", "\\&")
            .replace("%", "\\%")
            .replace("$", "\\$")
            .replace("#", "\\#")
            .replace("_", "\\_")
        )

    def _extract_bib_keys(self, bib_text: str) -> List[str]:
        if not bib_text:
            return []
        return re.findall(r"@\w+\s*\{\s*([^,\s]+)\s*,", bib_text, flags=re.I)

    def _split_bib_entries(self, bib_text: str) -> Sequence[str]:
        chunks: List[str] = []
        for match in re.finditer(r"@\w+\s*\{", bib_text):
            start = match.start()
            end = self._find_entry_end(bib_text, start)
            if end is None:
                continue
            chunks.append(bib_text[start : end + 1].strip())
        return chunks

    def _find_entry_end(self, content: str, start: int) -> Optional[int]:
        depth = 0
        for idx in range(start, len(content)):
            char = content[idx]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return idx
        return None


__all__ = ["PaperBuilder", "PaperStatus", "SECTION_ORDER"]
