"""
Document Reader - local parsing (no external upload)

PDF: PyPDF2 (<=50MB)
Image: Pillow (+ optional pytesseract OCR)
Text/Markdown/CSV/JSON/YAML: plain UTF-8 read (<=10MB)
"""

import logging
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


async def read_pdf(file_path: str) -> Dict[str, Any]:
    """Read PDF locally with PyPDF2."""
    try:
        import PyPDF2
    except ImportError:
        return {"success": False, "error": "Missing PyPDF2, please run: pip install PyPDF2"}

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
            reader = PyPDF2.PdfReader(f)
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


async def read_text_like(file_path: str) -> Dict[str, Any]:
    """Read text/markdown/csv/json/yaml and other small text files."""
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
    text_exts = {
        ".txt",
        ".md",
        ".csv",
        ".tsv",
        ".json",
        ".yaml",
        ".yml",
        ".log",
        ".ini",
        ".cfg",
        ".config",
    }
    if suffix and suffix not in text_exts:
        # Allow reading unknown small text files as fallback
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
    """Return (kind, suffix) where kind in pdf/image/text."""
    suffix = Path(file_path).suffix.lower()
    if suffix == ".pdf":
        return "pdf", suffix
    if suffix in {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff"}:
        return "image", suffix
    return "text", suffix


async def document_reader_handler(
    operation: str,
    file_path: str,
    use_ocr: bool = False,
) -> Dict[str, Any]:
    # Normalize path early and handle directory listing
    abs_path = Path(file_path).expanduser()
    if not abs_path.is_absolute():
        abs_path = Path.cwd() / abs_path
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
                "summary": f"Path is a directory containing {len(entries)} items. Use claude_code for recursive analysis, or specify a file path.",
            }
        except Exception as e:
            logger.error("Failed to list directory: %s", e)
            return {"success": False, "error": f"Failed to read directory: {e}"}
    # For downstream handlers, use normalized absolute path
    file_path = str(abs_path)

    try:
        if operation == "read_pdf":
            kind, _ = _detect_type(file_path)
            if kind == "pdf":
                return await read_pdf(file_path)
            if kind == "image":
                # Frontend/LLM misused read_pdf but passed an image, try reading as image
                return await read_image(file_path, use_ocr=use_ocr)
            # Other text types, try text reading
            return await read_text_like(file_path)
        if operation == "read_image":
            return await read_image(file_path, use_ocr=use_ocr)
        if operation == "read_text":
            return await read_text_like(file_path)
        if operation in {"read_any", "read_file", "auto"}:
            kind, _ = _detect_type(file_path)
            if kind == "pdf":
                return await read_pdf(file_path)
            if kind == "image":
                return await read_image(file_path, use_ocr=use_ocr)
            return await read_text_like(file_path)
        return {"success": False, "error": f"Unsupported operation: {operation}"}
    except Exception as e:
        logger.error("Document reading failed: %s", e)
        return {"success": False, "error": f"Error processing request: {e}"}


document_reader_tool = {
    "name": "document_reader",
    "description": "Extract content from files locally. Supports: PDF text extraction, image metadata (dimensions, format, size), text/Markdown/CSV/JSON files. For visual understanding like OCR, describing figures, or reading equations, use vision_reader instead.",
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
    "tags": ["document", "pdf", "image", "ocr", "text", "markdown", "csv", "json"],
    "examples": [
        "Read PDF file and extract text content",
        "Recognize text in images",
        "Read Markdown/text/CSV/JSON files",
        "Auto-detect file type and read content",
    ],
}
