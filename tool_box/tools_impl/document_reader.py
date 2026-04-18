"""
Document Reader - local parsing (no external upload)

PDF: PyPDF2 (<=50MB)
Image: Pillow (+ optional pytesseract OCR)
DOCX: Office Open XML parsing (no external service)
Text/Markdown/CSV/JSON/YAML: plain UTF-8 read (<=10MB)
"""

import logging
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
import zipfile
from xml.etree import ElementTree as ET

from tool_box.context import ToolContext
from tool_box.path_resolution import resolve_tool_path

logger = logging.getLogger(__name__)


def _extract_docx_text(xml_bytes: bytes) -> str:
    """Extract visible text from DOCX document.xml."""
    root = ET.fromstring(xml_bytes)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}

    paragraphs = []
    for para in root.findall(".//w:p", ns):
        parts = []
        for text_node in para.findall(".//w:t", ns):
            if text_node.text:
                parts.append(text_node.text)
        if parts:
            paragraphs.append("".join(parts))

    if paragraphs:
        return "\n\n".join(paragraphs)

    # Fallback: flatten all text nodes if paragraph structure is unusual.
    flat_parts = []
    for text_node in root.findall(".//w:t", ns):
        if text_node.text:
            flat_parts.append(text_node.text)
    return "\n".join(flat_parts)


async def read_docx(file_path: str) -> Dict[str, Any]:
    """Read DOCX locally by parsing Office Open XML."""
    abs_path = Path(file_path).expanduser().resolve()
    if not abs_path.exists():
        return {"success": False, "error": f"File not found: {file_path}"}
    if abs_path.suffix.lower() != ".docx":
        return {"success": False, "error": f"Not a DOCX file: {file_path}"}

    try:
        size_bytes = abs_path.stat().st_size
    except OSError:
        size_bytes = 0
    if size_bytes > 20 * 1024 * 1024:
        return {"success": False, "error": f"DOCX file too large (>{size_bytes/1024/1024:.2f}MB), limit is 20MB"}

    try:
        with zipfile.ZipFile(abs_path, "r") as archive:
            if "word/document.xml" not in archive.namelist():
                return {"success": False, "error": "Invalid DOCX: missing word/document.xml"}
            xml_bytes = archive.read("word/document.xml")
    except zipfile.BadZipFile:
        return {"success": False, "error": "Invalid DOCX: corrupted ZIP container"}
    except Exception as exc:
        logger.error("Failed to open DOCX: %s", exc)
        return {"success": False, "error": f"Failed to open DOCX: {exc}"}

    try:
        text = _extract_docx_text(xml_bytes)
    except Exception as exc:
        logger.error("Failed to parse DOCX XML: %s", exc)
        return {"success": False, "error": f"Failed to parse DOCX content: {exc}"}

    return {
        "success": True,
        "file_path": str(abs_path),
        "file_name": abs_path.name,
        "file_size": f"{size_bytes/1024:.2f} KB" if size_bytes else None,
        "format": "docx",
        "text": text,
        "text_length": len(text),
        "summary": f"Successfully read DOCX file, extracted {len(text)} characters",
    }


async def read_pdf(file_path: str) -> Dict[str, Any]:
    """Read PDF locally with pypdf."""
    try:
        import pypdf
    except ImportError:
        return {"success": False, "error": "Missing pypdf, please run: pip install pypdf"}

    abs_path = Path(file_path).expanduser().resolve()
    if not abs_path.exists():
        return {"success": False, "error": f"File not found: {file_path}"}
    if abs_path.suffix.lower() != ".pdf":
        return {"success": False, "error": f"Not a PDF file: {file_path}"}

    try:
        size_bytes = abs_path.stat().st_size
    except OSError:
        size_bytes = 0
    if size_bytes > 50 * 1024 * 1024:
        return {"success": False, "error": f"PDF file too large (>{size_bytes/1024/1024:.2f}MB), limit is 50MB"}

    text_parts = []
    metadata = {}
    try:
        with abs_path.open("rb") as f:
            reader = pypdf.PdfReader(f)
            if reader.metadata:
                metadata = {
                    "title": reader.metadata.get("/Title", ""),
                    "author": reader.metadata.get("/Author", ""),
                    "subject": reader.metadata.get("/Subject", ""),
                    "creator": reader.metadata.get("/Creator", ""),
                }
            page_count = len(reader.pages)
            for i in range(page_count):
                page = reader.pages[i]
                try:
                    txt = page.extract_text() or ""
                except Exception:
                    txt = ""
                if txt.strip():
                    text_parts.append(f"--- Page {i+1} ---\n{txt}")
        full_text = "\n\n".join(text_parts)
        return {
            "success": True,
            "file_path": str(abs_path),
            "file_name": abs_path.name,
            "file_size": f"{size_bytes/1024:.2f} KB" if size_bytes else None,
            "page_count": page_count,
            "metadata": metadata,
            "text": full_text,
            "text_length": len(full_text),
            "summary": f"Successfully read PDF, {page_count} pages, extracted {len(full_text)} characters",
        }
    except Exception as e:
        logger.error("Failed to read PDF: %s", e)
        return {"success": False, "error": f"Failed to read PDF: {e}"}


async def read_image(file_path: str, use_ocr: bool = False) -> Dict[str, Any]:
    """Read image locally with Pillow; optional OCR via pytesseract."""
    try:
        from PIL import Image
    except ImportError:
        return {"success": False, "error": "Missing Pillow, please run: pip install Pillow"}

    abs_path = Path(file_path).expanduser().resolve()
    if not abs_path.exists():
        return {"success": False, "error": f"File not found: {file_path}"}

    supported = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff"}
    if abs_path.suffix.lower() not in supported:
        return {"success": False, "error": f"Unsupported image format: {abs_path.suffix}"}

    try:
        size_bytes = abs_path.stat().st_size
    except OSError:
        size_bytes = 0
    if size_bytes > 20 * 1024 * 1024:
        return {"success": False, "error": f"Image file too large (>{size_bytes/1024/1024:.2f}MB), limit is 20MB"}

    try:
        img = Image.open(abs_path)
        info = {
            "format": img.format,
            "mode": img.mode,
            "width": img.width,
            "height": img.height,
            "size": img.size,
        }
        result = {
            "success": True,
            "file_path": str(abs_path),
            "file_name": abs_path.name,
            "file_size": f"{size_bytes/1024:.2f} KB" if size_bytes else None,
            "image_info": info,
            "summary": f"Successfully read image {abs_path.name}, size {img.width}x{img.height}, format {img.format}",
        }

        if use_ocr:
            try:
                import pytesseract

                text = pytesseract.image_to_string(img, lang="chi_sim+eng")
                result["ocr_text"] = text
                result["ocr_enabled"] = True
                result["summary"] += f", OCR extracted {len(text)} characters"
            except ImportError:
                result["ocr_enabled"] = False
                result["ocr_error"] = "Missing pytesseract, cannot perform OCR"
            except Exception as ocr_err:
                result["ocr_enabled"] = False
                result["ocr_error"] = f"OCR failed: {ocr_err}"

        return result
    except Exception as e:
        logger.error("Failed to read image: %s", e)
        return {"success": False, "error": f"Failed to read image: {e}"}


def _read_csv_tsv_preview(abs_path: Path, suffix: str) -> Dict[str, Any]:
    """
    Return the first chunk of a CSV/TSV as plain text so the agent can inspect headers
    and sample rows without a second tool call. Full stats/plots still need code_executor.
    """
    try:
        size_bytes = abs_path.stat().st_size
    except OSError:
        size_bytes = 0
    if size_bytes > 10 * 1024 * 1024:
        return {
            "success": False,
            "error": f"Tabular file too large (>{size_bytes/1024/1024:.2f}MB), limit is 10MB",
        }

    max_lines = 150
    max_bytes = 400_000
    lines: list[str] = []
    total_bytes = 0
    try:
        with abs_path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if len(lines) >= max_lines:
                    break
                raw = line.encode("utf-8", errors="replace")
                if total_bytes + len(raw) > max_bytes:
                    take = max_bytes - total_bytes
                    if take > 0:
                        lines.append(raw[:take].decode("utf-8", errors="replace").rstrip("\n\r"))
                    break
                lines.append(line.rstrip("\n\r"))
                total_bytes += len(raw)
    except Exception as exc:
        logger.error("Failed to preview tabular file: %s", exc)
        return {"success": False, "error": f"Failed to read tabular preview: {exc}"}

    text = "\n".join(lines)
    return {
        "success": True,
        "file_path": str(abs_path),
        "file_name": abs_path.name,
        "file_size_kb": f"{size_bytes/1024:.2f} KB" if size_bytes else None,
        "format": suffix.lstrip(".") or "tabular",
        "text": text,
        "text_length": len(text),
        "preview_only": True,
        "tabular_preview": True,
        "preview_line_count": len(lines),
        "summary": (
            f"Tabular preview ({suffix}): first {len(lines)} line(s), {len(text)} characters. "
            "For row counts, filtering, aggregation, or plots, use code_executor."
        ),
    }


async def read_text_like(file_path: str) -> Dict[str, Any]:
    """Read text/markdown and other small text files. Rejects structured data formats."""
    abs_path = Path(file_path).expanduser().resolve()
    if not abs_path.exists():
        return {"success": False, "error": f"File not found: {file_path}"}

    try:
        size_bytes = abs_path.stat().st_size
    except OSError:
        size_bytes = 0
    if size_bytes > 10 * 1024 * 1024:
        return {"success": False, "error": f"Text file too large (>{size_bytes/1024/1024:.2f}MB), limit is 10MB"}

    suffix = abs_path.suffix.lower()

    # Tabular/structured text: return an automatic preview so the agent is not
    # blocked.  Heavy analysis (aggregation, filtering, plots) still belongs in
    # code_executor, but giving a preview prevents unnecessary tool failures.
    if suffix in {".csv", ".tsv"}:
        return _read_csv_tsv_preview(abs_path, suffix)

    if suffix in {".json", ".yaml", ".yml"}:
        return _read_csv_tsv_preview(abs_path, suffix)

    binary_data_exts = {".xlsx", ".xls", ".parquet"}
    if suffix in binary_data_exts:
        return {
            "success": False,
            "error": (
                f"Binary data file ({suffix}) detected. For Excel/Parquet, use "
                f"`code_executor` for analysis. `document_reader` handles text-based formats only."
            ),
            "suggestion": f"Use code_executor with a task like: 'Read and analyze the data in {file_path}'",
        }
    
    # Allowed text formats
    text_exts = {
        ".txt",
        ".md",
        ".log",
        ".ini",
        ".cfg",
        ".config",
        ".yaml",
        ".yml",
    }
    if suffix and suffix not in text_exts:
        # Allow reading unknown small text files as fallback, but warn
        pass

    try:
        content = abs_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        logger.error("Failed to read text file: %s", e)
        return {"success": False, "error": f"Failed to read text: {e}"}

    return {
        "success": True,
        "file_path": str(abs_path),
        "file_name": abs_path.name,
        "file_size": f"{size_bytes/1024:.2f} KB" if size_bytes else None,
        "format": suffix.lstrip(".") if suffix else "text",
        "text": content,
        "text_length": len(content),
        "summary": f"Successfully read text file, extracted {len(content)} characters",
    }


def _detect_type(file_path: str) -> Tuple[str, str]:
    """Return (kind, suffix) where kind in pdf/image/docx/text."""
    suffix = Path(file_path).suffix.lower()
    if suffix == ".pdf":
        return "pdf", suffix
    if suffix in {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff"}:
        return "image", suffix
    if suffix == ".docx":
        return "docx", suffix
    return "text", suffix


async def document_reader_handler(
    operation: str,
    file_path: str,
    use_ocr: bool = False,
    tool_context: Optional[ToolContext] = None,
) -> Dict[str, Any]:
    import glob as glob_module
    
    # Check if path contains glob patterns (*, ?, [])
    if any(c in file_path for c in ['*', '?', '[']):
        # Expand glob pattern
        expanded_path = resolve_tool_path(file_path, tool_context=tool_context)
        
        matched_files = sorted(glob_module.glob(str(expanded_path)))
        
        if not matched_files:
            # No files matched the pattern
            parent_dir = Path(file_path).parent
            if parent_dir.exists():
                # List available files in the directory to help
                try:
                    available = [f.name for f in Path(parent_dir).iterdir() if f.is_file()]
                    return {
                        "success": False,
                        "error": f"No files matched pattern: {file_path}",
                        "suggestion": f"Available files in {parent_dir}: {available[:10]}{'...' if len(available) > 10 else ''}",
                        "hint": "Please specify exact file names instead of wildcard patterns.",
                    }
                except Exception:
                    pass
            return {
                "success": False,
                "error": f"No files matched pattern: {file_path}",
                "hint": "Please specify exact file names instead of wildcard patterns.",
            }
        
        if len(matched_files) == 1:
            # Single match - process normally
            file_path = matched_files[0]
        else:
            # Multiple matches - process each file and combine results
            results = []
            for matched_file in matched_files[:10]:  # Limit to 10 files
                try:
                    result = await document_reader_handler(
                        operation,
                        matched_file,
                        use_ocr,
                        tool_context=tool_context,
                    )
                    results.append({
                        "file": matched_file,
                        "success": result.get("success", False),
                        "summary": result.get("summary", ""),
                        "text_length": result.get("text_length", 0),
                        "error": result.get("error"),
                    })
                except Exception as e:
                    results.append({
                        "file": matched_file,
                        "success": False,
                        "error": str(e),
                    })
            
            return {
                "success": all(r.get("success") for r in results),
                "is_glob_result": True,
                "pattern": file_path,
                "matched_count": len(matched_files),
                "processed_count": len(results),
                "results": results,
                "summary": f"Processed {len(results)} files matching pattern. {sum(1 for r in results if r.get('success'))} succeeded.",
                "hint": "For detailed content of each file, call document_reader with specific file paths.",
            }
    
    # Normalize path early and handle directory listing
    abs_path = resolve_tool_path(file_path, tool_context=tool_context)
    if abs_path.is_dir():
        try:
            entries = []
            for child in sorted(abs_path.iterdir()):
                kind = "dir" if child.is_dir() else child.suffix.lstrip(".") or "file"
                entries.append({"name": child.name, "kind": kind})
            return {
                "success": True,
                "is_directory": True,
                "file_path": str(abs_path),
                "entries": entries,
                "summary": f"Path is a directory containing {len(entries)} items. Use code_executor for recursive analysis, or specify a file path.",
            }
        except Exception as e:
            logger.error("Failed to list directory: %s", e)
            return {"success": False, "error": f"Failed to read directory: {e}"}
    # For downstream handlers, use normalized absolute path
    file_path = str(abs_path)

    # Normalize operation aliases
    operation_aliases = {
        "extract_text": "read_any",
        "read": "read_any",
        "parse": "read_any",
        "extract": "read_any",
        "read_file": "read_any",
        "auto": "read_any",
    }
    operation = operation_aliases.get(operation, operation)

    try:
        if operation == "read_pdf":
            kind, _ = _detect_type(file_path)
            if kind == "pdf":
                return await read_pdf(file_path)
            if kind == "image":
                # Frontend/LLM misused read_pdf but passed an image, try reading as image
                return await read_image(file_path, use_ocr=use_ocr)
            if kind == "docx":
                return await read_docx(file_path)
            # Other text types, try text reading
            return await read_text_like(file_path)
        if operation == "read_image":
            return await read_image(file_path, use_ocr=use_ocr)
        if operation == "read_text":
            kind, _ = _detect_type(file_path)
            if kind == "docx":
                return await read_docx(file_path)
            return await read_text_like(file_path)
        if operation == "read_any":
            kind, _ = _detect_type(file_path)
            if kind == "pdf":
                return await read_pdf(file_path)
            if kind == "image":
                return await read_image(file_path, use_ocr=use_ocr)
            if kind == "docx":
                return await read_docx(file_path)
            return await read_text_like(file_path)
        
        # Unsupported operation - provide helpful error message
        supported_ops = ["read_pdf", "read_image", "read_text", "read_any", "extract_text", "read", "parse", "extract"]
        return {
            "success": False, 
            "error": f"Unsupported operation: {operation}. Supported operations: {', '.join(supported_ops)}"
        }
    except Exception as e:
        logger.error("Document reading failed: %s", e)
        return {"success": False, "error": f"Error processing request: {e}"}


document_reader_tool = {
    "name": "document_reader",
    "description": (
        "Extract content from files locally: PDF, DOCX, images, plain text/Markdown. "
        "For .csv/.tsv/.json/.yaml, returns an automatic text preview (first ~150 lines) "
        "so the agent can read headers and samples; use code_executor for aggregation, "
        "filtering, or plots. For Excel/Parquet, use code_executor. For OCR/figures, use vision_reader."
    ),
    "category": "document_processing",
    "parameters_schema": {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["read_pdf", "read_image", "read_text", "read_any"],
                "description": "Operation type: read_pdf, read_image, read_text, read_any (auto-detect)",
            },
            "file_path": {
                "type": "string",
                "description": "File path (absolute or relative)",
            },
            "use_ocr": {
                "type": "boolean",
                "description": "Whether to perform OCR on images (only effective for read_image / read_any when image is detected)",
                "default": False,
            },
        },
        "required": ["operation", "file_path"],
    },
    "handler": document_reader_handler,
    "tags": ["document", "pdf", "docx", "image", "ocr", "text", "markdown", "csv", "json"],
    "examples": [
        "Read PDF file and extract text content",
        "Read DOCX file and extract text content",
        "Recognize text in images",
        "Read Markdown/text/CSV/JSON files",
        "Auto-detect file type and read content",
    ],
}
