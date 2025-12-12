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
        return {"success": False, "error": "缺少 PyPDF2，请先 pip install PyPDF2"}

    abs_path = Path(file_path).expanduser().resolve()
    if not abs_path.exists():
        return {"success": False, "error": f"文件不存在: {file_path}"}
    if abs_path.suffix.lower() != ".pdf":
        return {"success": False, "error": f"不是PDF文件: {file_path}"}

    try:
        size_bytes = abs_path.stat().st_size
    except OSError:
        size_bytes = 0
    if size_bytes > 50 * 1024 * 1024:
        return {"success": False, "error": f"PDF文件过大（>{size_bytes/1024/1024:.2f}MB），上限50MB"}

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
                    text_parts.append(f"--- 第 {i+1} 页 ---\n{txt}")
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
            "summary": f"成功读取PDF，{page_count}页，提取{len(full_text)}字符",
        }
    except Exception as e:
        logger.error("读取PDF失败: %s", e)
        return {"success": False, "error": f"读取PDF失败: {e}"}


async def read_image(file_path: str, use_ocr: bool = False) -> Dict[str, Any]:
    """Read image locally with Pillow; optional OCR via pytesseract."""
    try:
        from PIL import Image
    except ImportError:
        return {"success": False, "error": "缺少 Pillow，请先 pip install Pillow"}

    abs_path = Path(file_path).expanduser().resolve()
    if not abs_path.exists():
        return {"success": False, "error": f"文件不存在: {file_path}"}

    supported = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff"}
    if abs_path.suffix.lower() not in supported:
        return {"success": False, "error": f"不支持的图片格式: {abs_path.suffix}"}

    try:
        size_bytes = abs_path.stat().st_size
    except OSError:
        size_bytes = 0
    if size_bytes > 20 * 1024 * 1024:
        return {"success": False, "error": f"图片文件过大（>{size_bytes/1024/1024:.2f}MB），上限20MB"}

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
            "summary": f"读取图片 {abs_path.name} 成功，尺寸 {img.width}x{img.height}, 格式 {img.format}",
        }

        if use_ocr:
            try:
                import pytesseract

                text = pytesseract.image_to_string(img, lang="chi_sim+eng")
                result["ocr_text"] = text
                result["ocr_enabled"] = True
                result["summary"] += f"，OCR提取{len(text)}字符"
            except ImportError:
                result["ocr_enabled"] = False
                result["ocr_error"] = "缺少 pytesseract，无法OCR"
            except Exception as ocr_err:
                result["ocr_enabled"] = False
                result["ocr_error"] = f"OCR失败: {ocr_err}"

        return result
    except Exception as e:
        logger.error("读取图片失败: %s", e)
        return {"success": False, "error": f"读取图片失败: {e}"}


async def analyze_image_with_llm(file_path: str, prompt: Optional[str] = None) -> Dict[str, Any]:
    """Placeholder: local reader does not analyze with LLM."""
    return {
        "success": False,
        "error": "analyze_image 未启用（本地模式）",
    }


async def read_text_like(file_path: str) -> Dict[str, Any]:
    """Read text/markdown/csv/json/yaml and other small text files."""
    abs_path = Path(file_path).expanduser().resolve()
    if not abs_path.exists():
        return {"success": False, "error": f"文件不存在: {file_path}"}

    try:
        size_bytes = abs_path.stat().st_size
    except OSError:
        size_bytes = 0
    if size_bytes > 10 * 1024 * 1024:
        return {"success": False, "error": f"文本文件过大（>{size_bytes/1024/1024:.2f}MB），上限10MB"}

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
        logger.error("读取文本类文件失败: %s", e)
        return {"success": False, "error": f"读取文本失败: {e}"}

    return {
        "success": True,
        "file_path": str(abs_path),
        "file_name": abs_path.name,
        "file_size": f"{size_bytes/1024:.2f} KB" if size_bytes else None,
        "format": suffix.lstrip(".") if suffix else "text",
        "text": content,
        "text_length": len(content),
        "summary": f"成功读取文本文件，提取{len(content)}字符",
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
    prompt: Optional[str] = None,
) -> Dict[str, Any]:
    try:
        if operation == "read_pdf":
            kind, _ = _detect_type(file_path)
            if kind == "pdf":
                return await read_pdf(file_path)
            if kind == "image":
                # 前端/LLM 误用 read_pdf 但传了图片，尝试读取图片
                return await read_image(file_path, use_ocr=use_ocr)
            # 其他文本类，尝试文本读取
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
        if operation == "analyze_image":
            return await analyze_image_with_llm(file_path, prompt=prompt)
        return {"success": False, "error": f"不支持的操作: {operation}"}
    except Exception as e:
        logger.error("文档读取处理失败: %s", e)
        return {"success": False, "error": f"处理请求时出错: {e}"}


document_reader_tool = {
    "name": "document_reader",
    "description": "读取和分析文档/图片：PDF、图片(OCR可选)、文本/Markdown/CSV/JSON等（本地解析，无外部上传）",
    "category": "document_processing",
    "parameters_schema": {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["read_pdf", "read_image", "read_text", "read_any", "analyze_image"],
                "description": "操作类型：read_pdf, read_image, read_text, read_any（自动判断），analyze_image（占位）",
            },
            "file_path": {
                "type": "string",
                "description": "文件路径（绝对或相对）",
            },
            "use_ocr": {
                "type": "boolean",
                "description": "是否对图片执行OCR（仅 read_image / read_any 命中图片时生效）",
                "default": False,
            },
            "prompt": {
                "type": "string",
                "description": "分析提示词（仅 analyze_image，占位）",
            },
        },
        "required": ["operation", "file_path"],
    },
    "handler": document_reader_handler,
    "tags": ["document", "pdf", "image", "ocr", "text", "markdown", "csv", "json"],
    "examples": [
        "读取PDF文件并提取文本内容",
        "识别图片中的文字信息",
        "读取Markdown/文本/CSV/JSON文件",
        "自动判断文件类型并读取内容",
    ],
}
