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


async def _call_qwen_vision_api(prompt: str, image_path: str) -> str:
    """Call a Qwen vision-capable model (e.g., qwen3-vl-plus) with an image.

    The API is assumed to be OpenAI-compatible chat completions with
    `messages: [{role: "user", content: [{type: "text"}, {type: "image_url"}]}]`.

    Environment / settings used:
    - QWEN_VL_API_KEY (fallback: QWEN_API_KEY, settings.qwen_api_key)
    - QWEN_VL_API_URL (fallback: QWEN_API_URL, settings.qwen_api_url)
    - QWEN_VL_MODEL   (fallback: QWEN_MODEL, settings.qwen_model)
    """

    settings = get_settings()

    api_key = (
        os.getenv("QWEN_VL_API_KEY")
        or os.getenv("QWEN_API_KEY")
        or settings.qwen_api_key
    )
    if not api_key:
        raise RuntimeError("Qwen vision API key is not configured (QWEN_VL_API_KEY / QWEN_API_KEY).")

    base_url = (
        os.getenv("QWEN_VL_API_URL")
        or os.getenv("QWEN_API_URL")
        or settings.qwen_api_url
        or "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    )
    model = (
        os.getenv("QWEN_VL_MODEL")
        or os.getenv("QWEN_MODEL")
        or settings.qwen_model
        or "qwen-vl-plus"
    )

    abs_path = Path(image_path).resolve()
    if not abs_path.exists():
        raise FileNotFoundError(f"Image file not found: {image_path}")

    # Encode image as data URL
    with abs_path.open("rb") as f:
        data = f.read()
    mime, _ = mimetypes.guess_type(abs_path.name)
    if not mime:
        mime = "image/png"
    b64 = base64.b64encode(data).decode("utf-8")
    data_url = f"data:{mime};base64,{b64}"

    payload: Dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    timeout_seconds = getattr(settings, "glm_request_timeout", 60) or 60
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

    # Try to extract the assistant message content
    try:
        choices = obj.get("choices") or []
        if not choices:
            logger.warning("Qwen vision response has no choices: %s", obj)
            return json.dumps(obj)
        message = choices[0].get("message") or {}
        content = message.get("content")
        # Some providers return a simple string; others return a list of blocks
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    parts.append(str(part.get("text", "")))
            if parts:
                return "\n".join(parts)
        # Fallback: serialize the whole message
        return json.dumps(message)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to parse Qwen vision response: %s", exc)
        return json.dumps(obj)


async def vision_reader_handler(
    operation: str,
    image_path: str,
    page_number: Optional[int] = None,
    region: Optional[Dict[str, float]] = None,
    question: Optional[str] = None,
    language: str = "en",
) -> Dict[str, Any]:
    """Vision reader tool handler.

    This handler delegates visual understanding tasks to a multimodal model
    (e.g., qwen3-vl-plus) and returns **English text only** so that a
    text-only LLM can reason on top of it.

    Args:
        operation: One of "ocr_page", "read_equation_image", "describe_figure", "extract_table".
        image_path: Path to the image file (or page screenshot) on disk.
        page_number: Optional page index for logging / context.
        region: Optional normalized region of interest {x1,y1,x2,y2} in [0,1].
        question: Optional extra question or instruction about the image.
        language: Output language hint (currently only "en" is supported).
    """

    op = (operation or "").strip()
    if not op:
        return {
            "tool": "vision_reader",
            "success": False,
            "error": "operation must be a non-empty string.",
            "code": "missing_operation",
        }

    abs_path = Path(image_path).resolve()
    if not abs_path.exists():
        return {
            "tool": "vision_reader",
            "success": False,
            "error": f"Image file does not exist: {image_path}",
            "code": "file_not_found",
        }

    # Construct a concise English prompt for the vision model
    lang = (language or "en").lower()
    if lang != "en":
        # We force English output to keep the downstream environment consistent.
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
        "Vision-based reader for scientific documents. Uses a multimodal model "
        "(e.g., qwen3-vl-plus) to OCR pages, read equation images, and describe "
        "figures, returning English text for downstream reasoning."
    ),
    "category": "vision",
    "parameters_schema": {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": [
                    "ocr_page",
                    "read_equation_image",
                    "describe_figure",
                    "extract_table",
                ],
                "description": "Type of vision task to perform.",
            },
            "image_path": {
                "type": "string",
                "description": "Path to the image file or page screenshot on disk.",
            },
            "page_number": {
                "type": "integer",
                "description": "Optional page index for logging / context.",
            },
            "region": {
                "type": "object",
                "description": "Optional normalized region of interest with keys x1,y1,x2,y2 in [0,1].",
            },
            "question": {
                "type": "string",
                "description": "Optional additional instruction or question about the image.",
            },
            "language": {
                "type": "string",
                "description": "Output language hint (currently only 'en' is supported).",
                "default": "en",
            },
        },
        "required": ["operation", "image_path"],
    },
    "handler": vision_reader_handler,
    "tags": ["vision", "ocr", "figure", "equation", "pdf"],
    "examples": [
        "Extract all text from page 3 of a scanned PDF.",
        "Read the main equation from an equation screenshot and explain the symbols.",
        "Describe the trends shown in Figure 2(b).",
    ],
}
