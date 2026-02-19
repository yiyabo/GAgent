"""Vision Reader Tool Implementation

This module provides vision-based reading capabilities backed by a multimodal
model (e.g., qwen3-vl-plus). It is exposed as a text-only tool to the main
agent: all outputs are English text / JSON that can be consumed by a
text-only LLM such as Qwen3-Max.

The tool supports operations like:
- ocr_page: extract all readable text (including equations and labels)
- read_equation_image: read and transcribe equations from an image
- describe_figure: describe the content and trends of a scientific figure

NOTE: This implementation assumes an OpenAI-compatible chat API that accepts
image URLs via the "image_url" content type. The actual vision backend can be
configured via environment variables or settings.
"""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import os
from pathlib import Path
from typing import Any, Dict, Optional

import aiohttp

from app.services.foundation.settings import get_settings

logger = logging.getLogger(__name__)


async def _call_qwen_vision_api(prompt: str, file_path: str) -> str:
    """Call a Qwen vision-capable model (e.g., qwen3-vl-plus) with an image or PDF.

    The API is assumed to be OpenAI-compatible chat completions with
    `messages: [{role: "user", content: [{type: "text"}, {type: "image_url"}]}]`.

    Environment / settings used:
    - QWEN_VL_API_KEY (fallback: QWEN_API_KEY, settings.qwen_api_key)
    - QWEN_VL_API_URL (fallback: QWEN_API_URL, settings.qwen_api_url)
    - QWEN_VL_MODEL   (fallback: QWEN_MODEL, settings.qwen_model)
    """

    settings = get_settings()

    # Check API key from multiple sources
    api_key_from_env = os.getenv("QWEN_VL_API_KEY") or os.getenv("QWEN_API_KEY")
    api_key_from_settings = getattr(settings, "qwen_api_key", None)
    
    api_key = api_key_from_env or api_key_from_settings
    
    if not api_key:
        env_keys = [k for k in os.environ.keys() if 'QWEN' in k.upper()]
        raise RuntimeError(
            f"Qwen vision API key is not configured. "
            f"Expected: QWEN_VL_API_KEY or QWEN_API_KEY. "
            f"Found QWEN-related env vars: {env_keys}. "
            f"Settings qwen_api_key: {'set' if api_key_from_settings else 'not set'}"
        )
    
    logger.debug(f"Using Qwen VL API key from: {'env' if api_key_from_env else 'settings'}")

    # Use Qwen VL OpenAI-compatible endpoint
    # Note: raw HTTP needs full path, OpenAI SDK would append /chat/completions automatically
    base_url = (
        os.getenv("QWEN_VL_API_URL")
        or os.getenv("QWEN_API_URL")  # Fallback to general Qwen API URL
        or "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    )
    model = (
        os.getenv("QWEN_VL_MODEL")
        or os.getenv("QWEN_MODEL")
        or settings.qwen_model
        or "qwen3-vl-plus"
    )

    abs_path = Path(file_path).resolve()
    if not abs_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    # Check file size - limit to 50MB for API
    file_size = abs_path.stat().st_size
    max_size = 50 * 1024 * 1024  # 50MB
    if file_size > max_size:
        raise ValueError(f"Image file too large: {file_size / 1024 / 1024:.2f}MB (max: {max_size / 1024 / 1024}MB)")

    logger.info(f"Processing image: {abs_path.name}, size: {file_size / 1024:.2f}KB")

    # Encode file as data URL
    with abs_path.open("rb") as f:
        data = f.read()
    mime, _ = mimetypes.guess_type(abs_path.name)
    if not mime:
        mime = "application/octet-stream"
    b64 = base64.b64encode(data).decode("utf-8")
    data_url = f"data:{mime};base64,{b64}"

    # OpenAI-compatible format for Qwen VL
    payload: Dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        "max_tokens": 4096,  # Explicit max tokens for response
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    logger.info(f"Calling Qwen VL API: model={model}, url={base_url}, prompt_len={len(prompt)}, image_size={len(b64)}B")

    timeout_seconds = 120  # 2 minutes for vision API
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(base_url, headers=headers, json=payload) as resp:
            text = await resp.text()
            if resp.status != 200:
                raise RuntimeError(f"Qwen vision API error {resp.status}: {text}")
            try:
                obj = json.loads(text)
            except json.JSONDecodeError:
                # Fallback: return raw text if JSON is not parseable
                logger.warning("Qwen vision response is not valid JSON; returning raw text.")
                return text

    # Parse OpenAI-compatible response format
    # Response structure: {"choices": [{"message": {"content": "..."}}]}
    try:
        choices = obj.get("choices") or []
        if not choices:
            logger.warning("Qwen vision response has no choices: %s", obj)
            return json.dumps(obj)

        message = choices[0].get("message") or {}
        content = message.get("content")

        # Content is typically a string in OpenAI-compatible format
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            # Handle if content is still a list (some providers mix formats)
            parts = []
            for part in content:
                if isinstance(part, dict):
                    if "text" in part:
                        parts.append(str(part.get("text", "")))
                    elif part.get("type") == "text":
                        parts.append(str(part.get("text", "")))
            if parts:
                return "\n".join(parts)
        # Fallback: serialize the whole message
        return json.dumps(message)
    except Exception as exc:
        logger.warning("Failed to parse Qwen vision response: %s", exc)
        return json.dumps(obj)


async def _read_pdf_with_qwen_long(
    pdf_path: str,
    prompt: str = "Read this document and extract all text while preserving original structure (paragraphs, lists, headings, etc.).",
) -> Dict[str, Any]:
    """Read PDF using Qwen-Long file upload API - much faster for text PDFs.
    
    Uses the file-extract endpoint to upload PDF, then queries with Qwen-Long model.
    This is significantly faster than converting to images and using vision model.
    
    Args:
        pdf_path: Path to PDF file
        prompt: Question or instruction for the document
        
    Returns:
        Dict with success status and extracted text
    """
    try:
        from openai import OpenAI
    except ImportError:
        logger.warning("OpenAI SDK not installed, falling back to vision model")
        return {"success": False, "error": "OpenAI SDK not installed"}
    
    settings = get_settings()
    
    # Get API key
    api_key = (
        os.getenv("QWEN_VL_API_KEY")
        or os.getenv("QWEN_API_KEY")
        or getattr(settings, "qwen_api_key", None)
    )
    if not api_key:
        return {"success": False, "error": "Qwen API key not configured"}
    
    # Get Qwen-Long model name
    model = os.getenv("QWEN_LONG_MODEL") or "qwen-long"
    
    abs_path = Path(pdf_path).resolve()
    if not abs_path.exists():
        return {"success": False, "error": f"PDF not found: {pdf_path}"}
    
    # Check file size - Qwen-Long supports up to 150MB
    file_size = abs_path.stat().st_size
    if file_size > 150 * 1024 * 1024:
        return {"success": False, "error": f"PDF too large: {file_size/1024/1024:.1f}MB (max: 150MB)"}
    
    logger.info(f"Using Qwen-Long for PDF: {abs_path.name}, size: {file_size/1024:.1f}KB")
    
    try:
        # Create OpenAI client pointing to DashScope
        client = OpenAI(
            api_key=api_key,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        
        # Step 1: Upload file
        logger.info(f"Uploading PDF to Qwen-Long: {abs_path.name}")
        file_object = client.files.create(
            file=abs_path,
            purpose="file-extract"
        )
        file_id = file_object.id
        logger.info(f"PDF uploaded, file_id: {file_id}")
        
        # Step 2: Query with Qwen-Long
        completion = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": f"fileid://{file_id}"},
                {"role": "user", "content": prompt}
            ],
            max_tokens=8192,
        )
        
        # Extract response
        content = completion.choices[0].message.content if completion.choices else ""
        
        logger.info(f"Qwen-Long response: {len(content)} characters")
        
        return {
            "success": True,
            "file_path": str(abs_path),
            "file_name": abs_path.name,
            "file_id": file_id,
            "model": model,
            "text": content,
            "text_length": len(content),
            "method": "qwen-long",
        }
        
    except Exception as e:
        logger.error(f"Qwen-Long PDF reading failed: {e}")
        return {
            "success": False,
            "error": str(e),
            "method": "qwen-long",
        }


async def _convert_pdf_to_images(pdf_path: str, max_pages: int = 100) -> list[str]:
    """Convert PDF pages to temporary image files for vision processing.
    
    Uses pdf2image (poppler) to convert PDF pages to PNG images.
    Returns list of image file paths.
    """
    try:
        from pdf2image import convert_from_path
    except ImportError:
        raise RuntimeError("Missing pdf2image, please run: pip install pdf2image")
    
    import tempfile
    
    abs_path = Path(pdf_path).resolve()
    if not abs_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")
    
    # Create temp directory for images
    temp_dir = tempfile.mkdtemp(prefix="vision_pdf_")
    
    try:
        # Convert PDF pages to images (300 DPI for good quality)
        images = convert_from_path(
            str(abs_path),
            dpi=200,  # Balance between quality and size
            first_page=1,
            last_page=max_pages,
        )
        
        image_paths = []
        for i, img in enumerate(images):
            img_path = Path(temp_dir) / f"page_{i+1:04d}.png"
            img.save(str(img_path), "PNG")
            image_paths.append(str(img_path))
        
        logger.info(f"Converted PDF to {len(image_paths)} images in {temp_dir}")
        return image_paths
        
    except Exception as e:
        # Clean up on error
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise RuntimeError(f"Failed to convert PDF to images: {e}")


async def _read_pdf_with_vision(
    pdf_path: str,
    page_numbers: Optional[list[int]] = None,
    max_pages: int = 50,
) -> Dict[str, Any]:
    """Read PDF using vision model by converting pages to images.
    
    Args:
        pdf_path: Path to PDF file
        page_numbers: Optional list of specific pages to read (1-indexed)
        max_pages: Maximum pages to process (default 50)
    """
    import shutil
    
    abs_path = Path(pdf_path).resolve()
    if not abs_path.exists():
        return {"success": False, "error": f"PDF not found: {pdf_path}"}
    
    # Check file size
    file_size = abs_path.stat().st_size
    max_size = 100 * 1024 * 1024  # 100MB limit for PDFs
    if file_size > max_size:
        return {"success": False, "error": f"PDF too large: {file_size/1024/1024:.1f}MB (max: 100MB)"}
    
    temp_dir = None
    try:
        # Convert PDF to images
        image_paths = await _convert_pdf_to_images(str(abs_path), max_pages=max_pages)
        temp_dir = str(Path(image_paths[0]).parent) if image_paths else None
        
        # If specific pages requested, filter
        if page_numbers:
            selected_paths = []
            for pn in page_numbers:
                if 1 <= pn <= len(image_paths):
                    selected_paths.append(image_paths[pn - 1])
            image_paths = selected_paths
        
        if not image_paths:
            return {"success": False, "error": "No pages to process"}
        
        # Process each page with vision model
        prompt = (
            "You are a vision assistant for document reading. Read this PDF page and "
            "extract ALL text content accurately, preserving the structure (paragraphs, "
            "lists, tables, headers). Include any equations, captions, and annotations. "
            "Return the text in proper reading order. Do not translate or add commentary."
        )
        
        pages_text = []
        for i, img_path in enumerate(image_paths):
            page_num = page_numbers[i] if page_numbers else i + 1
            logger.info(f"Processing PDF page {page_num}/{len(image_paths)}")
            
            try:
                text = await _call_qwen_vision_api(prompt, img_path)
                pages_text.append(f"--- Page {page_num} ---\n{text}")
            except Exception as e:
                pages_text.append(f"--- Page {page_num} ---\n[Error reading page: {e}]")
        
        full_text = "\n\n".join(pages_text)
        
        return {
            "success": True,
            "file_path": str(abs_path),
            "file_name": abs_path.name,
            "page_count": len(image_paths),
            "text": full_text,
            "text_length": len(full_text),
        }
        
    except Exception as e:
        logger.error(f"Failed to read PDF with vision: {e}")
        return {"success": False, "error": str(e)}
    finally:
        # Clean up temp images
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)


async def vision_reader_handler(
    operation: str,
    file_path: str = None,
    image_path: str = None,  # Alias for backward compatibility
    page_numbers: Optional[list[int]] = None,
    page_number: Optional[int] = None,
    region: Optional[Dict[str, float]] = None,
    question: Optional[str] = None,
    language: str = "en",
    max_pages: int = 50,
) -> Dict[str, Any]:
    """Vision reader tool handler - reads documents and images using vision model.

    This handler delegates visual understanding tasks to a multimodal model
    (qwen3-vl-plus) and returns text that can be consumed by downstream LLMs.

    Args:
        operation: One of "read_pdf", "read_image", "ocr_page", "read_equation_image", "describe_figure", "extract_table".
        file_path: Path to the file (PDF or image) on disk.
        image_path: Alias for file_path (backward compatibility).
        page_numbers: Optional list of specific pages to read (1-indexed, for PDFs).
        page_number: Optional single page index for logging / context.
        region: Optional normalized region of interest {x1,y1,x2,y2} in [0,1].
        question: Optional extra question or instruction about the content.
        language: Output language hint (currently only "en" is supported).
        max_pages: Maximum pages to process for PDFs (default 50).
    """

    op = (operation or "").strip().lower()
    if not op:
        return {
            "tool": "vision_reader",
            "success": False,
            "error": "operation must be a non-empty string.",
            "code": "missing_operation",
        }

    # Accept both file_path and image_path
    target_path = file_path or image_path
    if not target_path:
        return {
            "tool": "vision_reader",
            "success": False,
            "error": "file_path or image_path is required.",
            "code": "missing_path",
        }

    abs_path = Path(target_path).resolve()
    if not abs_path.exists():
        return {
            "tool": "vision_reader",
            "success": False,
            "error": f"File does not exist: {target_path}",
            "code": "file_not_found",
        }

    # Handle PDF reading
    if op == "read_pdf" or abs_path.suffix.lower() == ".pdf":
        # First try Qwen-Long (faster for text PDFs)
        logger.info(f"Attempting PDF read with Qwen-Long first: {abs_path.name}")
        qwen_long_result = await _read_pdf_with_qwen_long(
            str(abs_path),
            prompt=question
            or "Read this document and extract all text while preserving original structure (paragraphs, lists, headings, etc.)."
        )
        
        if qwen_long_result.get("success"):
            qwen_long_result["tool"] = "vision_reader"
            qwen_long_result["operation"] = "read_pdf"
            return qwen_long_result
        
        # Fallback to vision model if Qwen-Long fails
        logger.info(f"Qwen-Long failed ({qwen_long_result.get('error')}), falling back to vision model")
        
        # Use specialized PDF reading function with vision model
        pages = page_numbers
        if page_number and not pages:
            pages = [page_number]
        result = await _read_pdf_with_vision(str(abs_path), page_numbers=pages, max_pages=max_pages)
        result["tool"] = "vision_reader"
        result["operation"] = "read_pdf"
        result["fallback_from"] = "qwen-long"
        return result

    # Handle generic image reading
    if op == "read_image":
        prompt = (
            "You are a vision assistant. Describe what you see in this image in detail. "
            "Extract any text content, identify objects, people, or scenes, and provide "
            "a comprehensive description that captures all relevant information."
        )
        if question:
            prompt += f"\n\nUser's specific question: {question}"
        
        try:
            text = await _call_qwen_vision_api(prompt, str(abs_path))
            return {
                "tool": "vision_reader",
                "success": True,
                "operation": "read_image",
                "file_path": str(abs_path),
                "text": text,
            }
        except Exception as e:
            return {
                "tool": "vision_reader",
                "success": False,
                "operation": "read_image",
                "error": str(e),
            }

    # Construct a concise English prompt for the vision model
    lang = (language or "en").lower()
    if lang != "en":
        lang = "en"

    base_prompt: str
    if op == "ocr_page":
        base_prompt = (
            "You are a vision assistant. Read this scientific page image and "
            "extract all readable English text, including equations, axis labels, "
            "figure and table captions, and any annotations. Return ONLY the "
            "plain English text in logical reading order. Do not translate, and "
            "do not add commentary."
        )
    elif op == "read_equation_image":
        base_prompt = (
            "You are a vision assistant specialized in mathematical notation. "
            "Read the main equation or equations in this image and transcribe "
            "them into a linear text or LaTeX-like form. Then briefly explain "
            "the meaning of each symbol in English."
        )
    elif op == "describe_figure":
        base_prompt = (
            "You are a vision assistant for scientific figures. Describe in English "
            "what this figure shows, including the variables on each axis, the "
            "different curves or groups, the main trends, and the key conclusion a "
            "researcher should draw. If there is a legend, explain what each entry "
            "corresponds to."
        )
    elif op == "extract_table":
        base_prompt = (
            "You are a vision assistant for scientific tables. Read the table in "
            "this image and convert it into a plain-text table in English. Include "
            "column headers, row labels, and cell values."
        )
    else:
        base_prompt = (
            "You are a vision assistant for scientific documents. Read this image "
            "and provide a detailed English description of all scientifically "
            "relevant content."
        )

    if question:
        base_prompt += "\n\nAdditional instruction from the user: " + str(question)

    if region:
        # Region is currently informational only; some backends may support it in
        # the future. For now we just include it in the text prompt.
        try:
            rx1 = float(region.get("x1", 0.0))
            ry1 = float(region.get("y1", 0.0))
            rx2 = float(region.get("x2", 1.0))
            ry2 = float(region.get("y2", 1.0))
            base_prompt += (
                f"\n\nFocus on the region of interest with normalized coordinates "
                f"(x1={rx1:.2f}, y1={ry1:.2f}, x2={rx2:.2f}, y2={ry2:.2f})."
            )
        except Exception:
            # If region is malformed, ignore it but log a warning
            logger.warning("vision_reader received malformed region: %r", region)

    try:
        text = await _call_qwen_vision_api(base_prompt, str(abs_path))
        return {
            "tool": "vision_reader",
            "success": True,
            "operation": op,
            "image_path": str(abs_path),
            "page_number": page_number,
            "language": lang,
            "text": text,
        }
    except Exception as exc:
        logger.error("vision_reader failed: %s", exc)
        return {
            "tool": "vision_reader",
            "success": False,
            "operation": op,
            "image_path": str(abs_path),
            "page_number": page_number,
            "error": str(exc),
            "code": "vision_error",
        }


vision_reader_tool: Dict[str, Any] = {
    "name": "vision_reader",
    "description": (
        "Vision-based reader for documents and images. Uses qwen3-vl-plus multimodal model "
        "to read PDFs (page by page), OCR images, read equations, describe figures, and "
        "extract tables. Replaces document_reader for all file reading needs."
    ),
    "category": "vision",
    "parameters_schema": {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": [
                    "read_pdf",
                    "read_image",
                    "ocr_page",
                    "read_equation_image",
                    "describe_figure",
                    "extract_table",
                ],
                "description": "Type of vision task: read_pdf (PDF documents), read_image (general images), ocr_page (OCR text), read_equation_image (math formulas), describe_figure (chart/graph), extract_table (table data).",
            },
            "file_path": {
                "type": "string",
                "description": "Path to the file (PDF or image) on disk.",
            },
            "image_path": {
                "type": "string",
                "description": "Alias for file_path (backward compatibility).",
            },
            "page_numbers": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Optional list of specific pages to read (1-indexed, for PDFs).",
            },
            "page_number": {
                "type": "integer",
                "description": "Optional single page index for logging / context.",
            },
            "region": {
                "type": "object",
                "description": "Optional normalized region of interest with keys x1,y1,x2,y2 in [0,1].",
            },
            "question": {
                "type": "string",
                "description": "Optional additional instruction or question about the content.",
            },
            "max_pages": {
                "type": "integer",
                "description": "Maximum pages to process for PDFs (default: 50).",
                "default": 50,
            },
            "language": {
                "type": "string",
                "description": "Output language hint (currently only 'en' is supported).",
                "default": "en",
            },
        },
        "required": ["operation"],
    },
    "handler": vision_reader_handler,
    "tags": ["vision", "ocr", "figure", "equation", "pdf", "document"],
    "examples": [
        "Read a PDF document: operation='read_pdf', file_path='/path/to/paper.pdf'",
        "Read specific pages: operation='read_pdf', file_path='/path/to/doc.pdf', page_numbers=[1,2,5]",
        "Describe an image: operation='read_image', file_path='/path/to/figure.png'",
        "OCR a scanned page: operation='ocr_page', file_path='/path/to/scan.jpg'",
        "Read equations: operation='read_equation_image', file_path='/path/to/eq.png'",
    ],
}
