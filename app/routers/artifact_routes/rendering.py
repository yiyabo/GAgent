"""Document rendering for artifact previews (LaTeX -> PDF, Markdown/docx -> HTML).

Everything here is a subprocess/HTML transform with no HTTP surface; the
``render_artifact`` / ``get_rendered_file`` endpoints stay in the facade.

``MARKDOWN_AVAILABLE``/``markdown``/``MAMMOTH_AVAILABLE``/``mammoth`` are owned
by the package facade (the optional-dependency try-import lives there and tests
patch the flags/modules on the facade) and are read at call time via
``_facade()``.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, List, Tuple

from .session_dirs import _runtime_root_dir


def _facade() -> Any:
    """Return the package facade module (late-bound, patch-safe)."""
    from .. import artifact_routes as facade

    return facade


# Cache directory for rendered files
def _render_cache_dir() -> Path:
    return _runtime_root_dir() / ".render_cache"


def _iter_render_dependency_files(source_path: Path) -> List[Tuple[str, Path, Path]]:
    src_dir = source_path.parent
    roots: List[Tuple[str, Path]] = [("paper", src_dir)]
    refs_dir = src_dir.parent / "refs"
    if refs_dir.is_dir():
        roots.append(("refs", refs_dir))

    files: List[Tuple[str, Path, Path]] = []
    for label, root in roots:
        for child in sorted(root.rglob("*")):
            if child.is_file():
                files.append((label, root, child))
    return files


def _get_render_cache_path(file_path: Path, extension: str, extra_hash: str = "") -> Path:
    """Get cache path for rendered file based on source file hash.

    For .tex files, also incorporates the paper tree plus sibling refs/
    files so that section edits, staged figure updates, and bibliography
    changes all invalidate the cached PDF.
    """
    file_stat = file_path.stat()
    hash_parts = [f"{file_path.absolute()}:{file_stat.st_size}:{file_stat.st_mtime}"]
    if file_path.suffix.lower() == ".tex":
        for label, root, child in _iter_render_dependency_files(file_path):
            try:
                child_stat = child.stat()
                hash_parts.append(
                    f"{label}:{child.relative_to(root)}:{child_stat.st_size}:{child_stat.st_mtime}"
                )
            except OSError:
                continue
    if extra_hash:
        hash_parts.append(extra_hash)
    file_hash = hashlib.md5(":".join(hash_parts).encode()).hexdigest()[:16]
    cache_name = f"{file_path.stem}_{file_hash}.{extension}"
    return _render_cache_dir() / cache_name


def _rewrite_markdown_image_urls(
    content: str,
    session_id: str,
    source_type: str,
    base_dir: str = "",
) -> str:
    import re as _re
    from urllib.parse import quote

    if source_type == "deliverables":
        endpoint = f"/api/artifacts/sessions/{session_id}/deliverables/file"
    else:
        endpoint = f"/api/artifacts/sessions/{session_id}/file"

    def _replace(match):
        alt = match.group(1)
        src = match.group(2).strip()
        if not src or src.startswith("http://") or src.startswith("https://") or src.startswith("/api/"):
            return match.group(0)
        clean = src.lstrip("/")
        parts = []
        if base_dir:
            parts.append(base_dir)
        parts.append(clean)
        rel_path = "/".join(parts)
        encoded = quote(rel_path, safe="/")
        return f"![{alt}]({endpoint}?path={encoded})"

    return _re.sub(r"!\[(.*?)\]\((.*?)\)", _replace, content)


def _render_markdown_to_html(content: str) -> str:
    """Render Markdown content to HTML."""
    facade = _facade()
    if facade.MARKDOWN_AVAILABLE:
        md = facade.markdown.Markdown(extensions=['tables', 'fenced_code', 'toc'])
        html_body = md.convert(content)
    else:
        # Fallback: basic HTML conversion
        html_body = (
            content
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('\n\n', '</p><p>')
            .replace('\n', '<br>')
        )
        html_body = f'<p>{html_body}</p>'

    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <base href="/">
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            line-height: 1.6;
            max-width: 900px;
            margin: 40px auto;
            padding: 0 20px;
            color: #333;
        }}
        pre {{
            background: #f5f5f5;
            padding: 16px;
            border-radius: 6px;
            overflow-x: auto;
        }}
        code {{
            font-family: 'SF Mono', Monaco, monospace;
            font-size: 0.9em;
            background: #f5f5f5;
            padding: 2px 6px;
            border-radius: 3px;
        }}
        pre code {{
            padding: 0;
            background: none;
        }}
        table {{
            border-collapse: collapse;
            width: 100%;
            margin: 16px 0;
        }}
        th, td {{
            border: 1px solid #ddd;
            padding: 8px 12px;
            text-align: left;
        }}
        th {{
            background: #f5f5f5;
            font-weight: 600;
        }}
        img {{
            max-width: 100%;
            height: auto;
        }}
        h1, h2, h3, h4 {{
            color: #1a1a1a;
            margin-top: 24px;
            margin-bottom: 16px;
        }}
        blockquote {{
            border-left: 4px solid #ddd;
            margin: 0;
            padding-left: 16px;
            color: #666;
        }}
    </style>
</head>
<body>
{html_body}
</body>
</html>"""


def _render_docx_to_html(source_path: Path) -> str:
    facade = _facade()
    if not facade.MAMMOTH_AVAILABLE:
        raise RuntimeError("mammoth is not installed")
    with open(source_path, "rb") as f:
        result = facade.mammoth.convert_to_html(f)
    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'">
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            line-height: 1.6;
            max-width: 900px;
            margin: 40px auto;
            padding: 0 20px;
            color: #333;
        }}
        table {{
            border-collapse: collapse;
            width: 100%;
            margin: 16px 0;
        }}
        th, td {{
            border: 1px solid #ddd;
            padding: 8px 12px;
            text-align: left;
        }}
        th {{
            background: #f5f5f5;
            font-weight: 600;
        }}
        img {{
            max-width: 100%;
            height: auto;
        }}
        h1, h2, h3, h4 {{
            color: #1a1a1a;
            margin-top: 24px;
            margin-bottom: 16px;
        }}
    </style>
</head>
<body>
{result.value}
</body>
</html>"""


def _render_latex_to_pdf(source_path: Path, output_path: Path) -> bool:
    """Render LaTeX file to PDF using pdflatex or xelatex."""
    # Try xelatex first (better Unicode support), then pdflatex
    latex_cmds = ['xelatex', 'pdflatex']
    latex_cmd = None

    for cmd in latex_cmds:
        if shutil.which(cmd):
            latex_cmd = cmd
            break

    if not latex_cmd:
        return False

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            src_dir = source_path.parent

            # Copy the entire directory containing the .tex file
            # (includes sections/ subdirectory for paper projects)
            work_dir = tmpdir_path / src_dir.name
            shutil.copytree(src_dir, work_dir)

            # Copy sibling refs/ if present for bibliography resolution.
            refs_dir = src_dir.parent / "refs"
            if refs_dir.is_dir():
                shutil.copytree(refs_dir, tmpdir_path / "refs")

            temp_tex = work_dir / source_path.name
            tex_stem = temp_tex.stem

            # Compile: latex → bibtex → latex × 2  (full bibliography flow)
            # Step 1: first latex pass
            result = subprocess.run(
                [latex_cmd, '-interaction=nonstopmode', '-halt-on-error', source_path.name],
                cwd=str(work_dir),
                capture_output=True,
                text=True,
                timeout=120,
            )

            # Step 2: run bibtex if .aux exists (needed for \cite → references)
            aux_file = work_dir / f"{tex_stem}.aux"
            bibtex_cmd = shutil.which('bibtex')
            if bibtex_cmd and aux_file.exists():
                subprocess.run(
                    [bibtex_cmd, tex_stem],
                    cwd=str(work_dir),
                    capture_output=True,
                    text=True,
                    timeout=30,
                )

            # Steps 3-4: two more latex passes to resolve citations & refs
            for _ in range(2):
                result = subprocess.run(
                    [latex_cmd, '-interaction=nonstopmode', '-halt-on-error', source_path.name],
                    cwd=str(work_dir),
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if result.returncode != 0:
                    error_match = re.search(r'! (.*?)(?:\n|$)', result.stderr or result.stdout, re.DOTALL)
                    error_msg = error_match.group(1).strip() if error_match else 'LaTeX compilation failed'
                    print(f"LaTeX error: {error_msg}")

            # Move output PDF to cache location
            output_pdf = work_dir / f"{tex_stem}.pdf"
            if output_pdf.exists():
                output_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(output_pdf), str(output_path))
                return True

    except subprocess.TimeoutExpired:
        print("LaTeX compilation timed out")
    except Exception as e:
        print(f"LaTeX compilation error: {e}")

    return False
