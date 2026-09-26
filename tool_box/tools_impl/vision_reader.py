"""Vision Reader Tool Implementation

This module provides reading capabilities for PDFs and images.

Images and figure/equation pages go to a multimodal model (qwen3.6-plus -
native multimodal supporting text/image/video). PDFs are read in cost order:

1. a text PDF is read locally with pypdf for free;
2. a document that yields no text (a scan or an image PDF) is rasterized locally
   with pypdfium2, and those pages are read by the multimodal model;
3. the paid per-page file-extract reader is the last resort.

Step 2 is what makes scans work on this platform at all: every model call goes
through the platform gateway, which serves /chat/completions and /embeddings but
NOT /files, so the paid reader's upload always 404s (verified 2026-09-26).

It is exposed as a text-only tool to the main agent: all outputs are English text
/ JSON that can be consumed by a text-only LLM such as Qwen3-Max.

The tool supports operations like:
- read_pdf: PDF text (local pypdf first, then local render + vision)
- ocr_page: extract all readable text (including equations and labels)
- read_equation_image: read and transcribe equations from an image
- describe_figure: describe the content and trends of a scientific figure

Every call that costs anything is recorded as usage (page count, tokens, tool
key), so the volume and the attributed cost are never invisible.

NOTE: The vision call assumes an OpenAI-compatible chat API that accepts image
URLs via the "image_url" content type. The actual vision backend can be
configured via environment variables or settings.
"""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp

from app.services.foundation.settings import get_settings
from app.services.foundation.llm_config import is_production, platform_profile

logger = logging.getLogger(__name__)

# Both paid paths below are billed to the project credential; their usage is
# recorded under this tool key so the cost is attributable per tool.
TOOL_NAME = "vision_reader"
CALL_PURPOSE_PDF_PARSE = "pdf_parse"
CALL_PURPOSE_VISION_READ = "vision_read"

# The PDF file-extract reader is billed per document page. NOTE (2026-09-26): that
# tariff is the provider console's, and this platform does not reach the provider
# directly — every model call goes through the platform gateway, which does not
# expose the Files API at all. Treat this rate as an internal estimate, not a bill.
PDF_PAGE_CNY_ENV = "VISION_READER_PDF_PAGE_CNY"
DEFAULT_PDF_PAGE_CNY = 0.02

# Routing: a text PDF is read locally for free (pypdf); a document that yields no
# text is a scan, which is rasterized locally and read by the multimodal model;
# the paid file-extract reader is the last resort. Set to 0 to disable a step.
LOCAL_TEXT_FIRST_ENV = "VISION_READER_LOCAL_TEXT_FIRST"
PDF_RENDER_ENV = "VISION_READER_PDF_RENDER"
# Page budget: neither a paid parse nor a rendered page should happen 300 times
# because nobody said which pages were wanted.
PDF_MAX_PAGES_ENV = "VISION_READER_PDF_MAX_PAGES"
DEFAULT_PDF_MAX_PAGES = 50
# A scan extracts (almost) no text; below this many characters the local reader
# is treated as having found nothing and the scan path takes over.
MIN_LOCAL_TEXT_CHARS = 400
# Rasterization: 150 dpi reads comfortably; the longest edge is capped so one page
# cannot blow up the image payload (and its token cost).
PDF_RENDER_DPI_ENV = "VISION_READER_PDF_RENDER_DPI"
DEFAULT_PDF_RENDER_DPI = 150
PDF_RENDER_MAX_EDGE_ENV = "VISION_READER_PDF_RENDER_MAX_EDGE"
DEFAULT_PDF_RENDER_MAX_EDGE = 2000

# Prompt for one rasterized page of a scan: transcribe, do not summarize or
# translate — the same contract the text readers keep.
_SCANNED_PAGE_PROMPT = (
    "You are a vision assistant for document reading. Read this PDF page and "
    "extract ALL text content accurately, preserving the structure (paragraphs, "
    "lists, tables, headers). Include any equations, captions, and annotations. "
    "Return the text in proper reading order. Do not translate or add commentary."
)


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"", "0", "false", "no", "off"}


def local_text_first_enabled() -> bool:
    """Whether a PDF is tried locally (free) before the paid reader."""
    return _env_flag(LOCAL_TEXT_FIRST_ENV, True)


def pdf_page_budget(override: Optional[int] = None) -> int:
    """Per-read page budget: explicit argument, then env, then the default."""
    if isinstance(override, int) and not isinstance(override, bool) and override > 0:
        return override
    try:
        budget = int(os.getenv(PDF_MAX_PAGES_ENV, str(DEFAULT_PDF_MAX_PAGES)))
    except (TypeError, ValueError):
        budget = DEFAULT_PDF_MAX_PAGES
    return max(1, budget)


def pdf_render_enabled() -> bool:
    """Whether a text-less PDF is rasterized for the multimodal model."""
    return _env_flag(PDF_RENDER_ENV, True)


def pdf_render_dpi() -> int:
    try:
        dpi = int(os.getenv(PDF_RENDER_DPI_ENV, str(DEFAULT_PDF_RENDER_DPI)))
    except (TypeError, ValueError):
        dpi = DEFAULT_PDF_RENDER_DPI
    return max(36, min(600, dpi))


def pdf_render_max_edge() -> int:
    try:
        edge = int(os.getenv(PDF_RENDER_MAX_EDGE_ENV, str(DEFAULT_PDF_RENDER_MAX_EDGE)))
    except (TypeError, ValueError):
        edge = DEFAULT_PDF_RENDER_MAX_EDGE
    return max(200, edge)


def render_pdf_pages(
    path: Path,
    *,
    page_numbers: Optional[List[int]] = None,
    dest_dir: Path,
) -> Optional[List[Dict[str, Any]]]:
    """Rasterize PDF pages to PNG; ``None`` when rendering is unavailable.

    ``None`` (no pypdfium2, or an unreadable document) is the signal to hand the
    document to a reader that does not need local rendering.

    pypdfium2 rather than poppler: one pip wheel, no system package, no
    subprocess, and it can therefore be exercised for real in the test suite.
    """
    try:
        import pypdfium2 as pdfium
    except Exception as exc:
        logger.info("pypdfium2 unavailable, skipping local PDF rendering: %s", exc)
        return None

    dpi = pdf_render_dpi()
    scale = dpi / 72.0
    max_edge = pdf_render_max_edge()
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("Could not prepare the render directory %s: %s", dest_dir, exc)
        return None
    rendered: List[Dict[str, Any]] = []
    document = None
    try:
        document = pdfium.PdfDocument(str(path))
        total_pages = len(document)
        wanted = [
            number for number in (page_numbers or range(1, total_pages + 1))
            if 1 <= number <= total_pages
        ]
        for number in wanted:
            try:
                page = document[number - 1]
                image = page.render(scale=scale).to_pil()
            except Exception as exc:
                logger.warning("Could not render page %s of %s: %s", number, path.name, exc)
                continue
            longest = max(image.size)
            if longest > max_edge:
                ratio = max_edge / float(longest)
                image = image.resize(
                    (max(1, int(image.size[0] * ratio)), max(1, int(image.size[1] * ratio)))
                )
            png_path = dest_dir / f"page_{number:04d}.png"
            image.save(png_path)
            rendered.append({"page_number": number, "path": png_path, "size": image.size})
    except Exception as exc:
        logger.info("pypdfium2 could not render %s: %s", path.name, exc)
        return None
    finally:
        if document is not None:
            try:
                document.close()
            except Exception:
                pass
    return rendered or None


def extract_local_pdf_text(path: Path, page_numbers: Optional[List[int]] = None) -> Optional[Dict[str, Any]]:
    """Free pypdf text extraction; ``None`` when the document yields no text.

    ``None`` is the signal that this is a scan or an image PDF, which is exactly
    the case the paid file-extract reader exists for.
    """
    try:
        import pypdf
    except Exception as exc:
        logger.info("pypdf unavailable, skipping the free PDF text path: %s", exc)
        return None

    try:
        with path.open("rb") as handle:
            reader = pypdf.PdfReader(handle)
            total_pages = len(reader.pages)
            wanted = [
                number for number in (page_numbers or range(1, total_pages + 1))
                if 1 <= number <= total_pages
            ]
            parts: List[str] = []
            pages_with_text = 0
            for number in wanted:
                try:
                    page_text = reader.pages[number - 1].extract_text() or ""
                except Exception:
                    page_text = ""
                if page_text.strip():
                    pages_with_text += 1
                parts.append(f"--- Page {number} ---\n{page_text}")
    except Exception as exc:
        logger.info("pypdf could not read %s: %s", path.name, exc)
        return None

    text = "\n\n".join(parts)
    if pages_with_text == 0 or len(text.strip()) < MIN_LOCAL_TEXT_CHARS:
        return None
    return {
        "page_count": total_pages,
        "pages_read": wanted,
        "text": text,
        "text_length": len(text),
    }


def build_page_subset_pdf(path: Path, page_numbers: List[int], dest_dir: Path) -> Optional[Path]:
    """Write a PDF holding only ``page_numbers``, so only those pages are billed.

    The file-extract reader prices the document it is handed, so the cheapest
    whole-document parse is the one that never happens.
    """
    try:
        import pypdf

        reader = pypdf.PdfReader(str(path))
        total_pages = len(reader.pages)
        selected = [number for number in page_numbers if 1 <= number <= total_pages]
        if not selected:
            return None
        writer = pypdf.PdfWriter()
        for number in selected:
            writer.add_page(reader.pages[number - 1])
        label = "-".join(str(number) for number in selected[:8])
        dest = dest_dir / f"{path.stem}.pages_{label}.pdf"
        with dest.open("wb") as handle:
            writer.write(handle)
        return dest
    except Exception as exc:
        logger.warning("Could not build a page subset of %s: %s", path.name, exc)
        return None


def count_pdf_pages(path: Path) -> Optional[int]:
    """Free page count via pypdf; ``None`` when the file cannot be parsed."""
    try:
        import pypdf

        with path.open("rb") as handle:
            return len(pypdf.PdfReader(handle).pages)
    except Exception as exc:
        logger.info("vision_reader could not count pages of %s: %s", path.name, exc)
        return None


def _page_parse_cost_cny(page_count: Optional[int]) -> float:
    if not page_count:
        return 0.0
    try:
        rate = float(os.getenv(PDF_PAGE_CNY_ENV, DEFAULT_PDF_PAGE_CNY))
    except (TypeError, ValueError):
        rate = DEFAULT_PDF_PAGE_CNY
    return max(0.0, rate) * int(page_count)


def _usage_from_response(payload: Any) -> Dict[str, int]:
    """Pull token counts out of an OpenAI-compatible response body."""
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        return {"prompt_tokens": 0, "completion_tokens": 0}
    prompt = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    completion = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    return {"prompt_tokens": max(0, prompt), "completion_tokens": max(0, completion)}


def record_usage(
    *,
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    call_purpose: str,
    call_status: str = "ok",
    duration_ms: Optional[float] = None,
    page_count: Optional[int] = None,
) -> None:
    """Best-effort usage row for one paid reader call; never raises.

    Attribution (session/plan/task/run) is inherited from the ambient usage
    context, so a PDF read lands on the conversation turn or plan task that
    asked for it. ``parent_run_id`` is intentionally left unset: that column
    links a *delegated sub-agent run* to its parent, and this call is not a
    sub-agent run of its own — the ambient ``run_id`` already carries it.
    """
    try:
        from app.llm import _usage_context
        from app.repository.llm_usage import estimate_llm_cost, log_llm_usage
    except Exception:  # pragma: no cover - usage plumbing unavailable
        return
    try:
        ctx = _usage_context.get()
        ctx = ctx if isinstance(ctx, dict) else {}
        model_name = str(model or "unknown").strip() or "unknown"
        prompt = max(0, int(prompt_tokens or 0))
        completion = max(0, int(completion_tokens or 0))
        cost = estimate_llm_cost(
            provider=provider, model=model_name, prompt_tokens=prompt, completion_tokens=completion
        )
        page_cost = _page_parse_cost_cny(page_count)
        log_llm_usage(
            provider=provider,
            model=model_name,
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=prompt + completion,
            session_id=ctx.get("session_id"),
            plan_id=ctx.get("plan_id"),
            task_id=ctx.get("task_id"),
            call_purpose=call_purpose,
            run_id=ctx.get("run_id"),
            phase=ctx.get("phase") or "tool_execution",
            tool_name=TOOL_NAME,
            call_status=call_status,
            duration_ms=duration_ms,
            page_count=page_count,
            input_cost=cost["input_cost"],
            output_cost=cost["output_cost"],
            # The per-page charge is the dominant cost of a PDF parse and no
            # token column can express it, so it is folded into the estimate.
            estimated_cost=cost["estimated_cost"] + page_cost,
            cost_currency=cost["cost_currency"],
        )
    except Exception as exc:  # pragma: no cover - observability must not break reads
        logger.warning("vision_reader usage record failed: %s", exc)


async def _call_qwen_vision_api(prompt: str, file_path: str) -> str:
    """Call Qwen native multimodal model (qwen3.6-plus) with an image or PDF.

    The API is assumed to be OpenAI-compatible chat completions with
    `messages: [{role: "user", content: [{type: "text"}, {type: "image_url"}]}]`.

    Environment / settings used:
    - QWEN_VL_API_KEY (fallback: QWEN_API_KEY, settings.qwen_api_key)
    - QWEN_VL_API_URL (fallback: QWEN_API_URL, settings.qwen_api_url)
    - QWEN_VL_MODEL   (fallback: QWEN_MODEL, settings.qwen_model, default: qwen3.6-plus)
    """

    settings = get_settings()
    if is_production():
        profile = platform_profile()
        api_key = profile.api_key
        base_url = profile.api_url
        model = os.getenv("PLATFORM_LLM_VISION_MODEL") or profile.model
        try:
            from app.llm import get_project_llm_credentials
            creds = get_project_llm_credentials()
        except Exception:
            creds = None
        if creds:
            api_key = str(creds["api_key"])
            base_url = str(creds["chat_url"])
    else:
        api_key_from_env = os.getenv("QWEN_VL_API_KEY") or os.getenv("QWEN_API_KEY")
        api_key_from_settings = getattr(settings, "qwen_api_key", None)
        api_key = api_key_from_env or api_key_from_settings
        if not api_key:
            raise RuntimeError("Qwen vision API key is not configured")
        base_url = (
            os.getenv("QWEN_VL_API_URL")
            or os.getenv("QWEN_API_URL")
            or "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
        )
        model = (
            os.getenv("QWEN_VL_MODEL")
            or os.getenv("QWEN_MODEL")
            or settings.qwen_model
            or "qwen3.6-plus"
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

    started = time.perf_counter()
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(base_url, headers=headers, json=payload) as resp:
            text = await resp.text()
            if resp.status != 200:
                record_usage(
                    provider="qwen",
                    model=model,
                    prompt_tokens=0,
                    completion_tokens=0,
                    call_purpose=CALL_PURPOSE_VISION_READ,
                    call_status=f"http_{resp.status}",
                    duration_ms=(time.perf_counter() - started) * 1000.0,
                )
                raise RuntimeError(f"Qwen vision API error {resp.status}: {text}")
            try:
                obj = json.loads(text)
            except json.JSONDecodeError:
                # Fallback: return raw text if JSON is not parseable
                logger.warning("Qwen vision response is not valid JSON; returning raw text.")
                record_usage(
                    provider="qwen",
                    model=model,
                    prompt_tokens=0,
                    completion_tokens=0,
                    call_purpose=CALL_PURPOSE_VISION_READ,
                    call_status="unparsed_response",
                    duration_ms=(time.perf_counter() - started) * 1000.0,
                )
                return text

    tokens = _usage_from_response(obj)
    record_usage(
        provider="qwen",
        model=model,
        prompt_tokens=tokens["prompt_tokens"],
        completion_tokens=tokens["completion_tokens"],
        call_purpose=CALL_PURPOSE_VISION_READ,
        duration_ms=(time.perf_counter() - started) * 1000.0,
    )

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
    """Read a PDF through the paid file-extract endpoint (Qwen-Long).

    This is the paid path — it prices the *document it is handed*, so the caller
    uploads a page subset when it only needs a few pages. It is reached only when
    the free local reader found no text (a scan or an image PDF); a text PDF
    never gets here.

    Args:
        pdf_path: Path to PDF file
        prompt: Question or instruction for the document

    Returns:
        Dict with success status and extracted text
    """
    try:
        from openai import OpenAI
    except ImportError:
        # The SDK is not declared in any requirements file, so this is the
        # production state: the paid path is unreachable until it is either
        # declared and installed, or rewritten on the aiohttp client already
        # used by the vision call.
        logger.error(
            "PDF file-extract unavailable: the openai SDK is not installed "
            "(scanned/image PDFs cannot be read without it)"
        )
        return {
            "success": False,
            "error": (
                "PDF file-extract parsing is unavailable: the openai SDK is not "
                "installed in this environment."
            ),
            "code": "pdf_extract_sdk_missing",
        }
    
    settings = get_settings()
    
    if is_production():
        profile = platform_profile()
        api_key = profile.api_key
        model = os.getenv("PLATFORM_LLM_LONG_MODEL") or profile.model
        base_url = profile.api_url.rsplit("/chat/completions", 1)[0]
        try:
            from app.llm import get_project_llm_credentials
            creds = get_project_llm_credentials()
        except Exception:
            creds = None
        if creds:
            api_key = str(creds["api_key"])
            base_url = str(creds["chat_url"]).rsplit("/chat/completions", 1)[0]
    else:
        api_key = (
            os.getenv("QWEN_VL_API_KEY")
            or os.getenv("QWEN_API_KEY")
            or getattr(settings, "qwen_api_key", None)
        )
        if not api_key:
            return {"success": False, "error": "Qwen API key not configured"}
        model = os.getenv("QWEN_LONG_MODEL") or "qwen-long"
        base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    
    abs_path = Path(pdf_path).resolve()
    if not abs_path.exists():
        return {"success": False, "error": f"PDF not found: {pdf_path}"}
    
    # Check file size - Qwen-Long supports up to 150MB
    file_size = abs_path.stat().st_size
    if file_size > 150 * 1024 * 1024:
        return {"success": False, "error": f"PDF too large: {file_size/1024/1024:.1f}MB (max: 150MB)"}
    
    logger.info(f"Using Qwen-Long for PDF: {abs_path.name}, size: {file_size/1024:.1f}KB")
    page_count = count_pdf_pages(abs_path)
    
    started = time.perf_counter()
    uploaded = False
    try:
        client = OpenAI(api_key=api_key, base_url=base_url)
        
        # Step 1: Upload file
        logger.info(f"Uploading PDF to Qwen-Long: {abs_path.name}")
        file_object = client.files.create(
            file=abs_path,
            purpose="file-extract"
        )
        file_id = file_object.id
        uploaded = True
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
        usage = getattr(completion, "usage", None)
        
        logger.info(f"Qwen-Long response: {len(content)} characters")
        record_usage(
            provider="qwen",
            model=model,
            prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            call_purpose=CALL_PURPOSE_PDF_PARSE,
            duration_ms=(time.perf_counter() - started) * 1000.0,
            page_count=page_count,
        )
        
        return {
            "success": True,
            "file_path": str(abs_path),
            "file_name": abs_path.name,
            "file_id": file_id,
            "model": model,
            "page_count": page_count,
            "text": content,
            "text_length": len(content),
            "method": "qwen-long",
        }
        
    except Exception as e:
        message = str(e)
        logger.error(f"Qwen-Long PDF reading failed: {message}")
        # A page is charged when the document is *accepted* for parsing, so the
        # charge is only real once the upload landed. A failed upload (e.g. a
        # gateway without the Files API) parsed nothing and must not be billed.
        record_usage(
            provider="qwen",
            model=model,
            prompt_tokens=0,
            completion_tokens=0,
            call_purpose=CALL_PURPOSE_PDF_PARSE,
            call_status="error",
            duration_ms=(time.perf_counter() - started) * 1000.0,
            page_count=page_count if uploaded else None,
        )
        result = {
            "success": False,
            "error": message,
            "method": "qwen-long",
            "page_count": page_count,
        }
        if not uploaded:
            # The endpoint itself is missing, not the document: verified 2026-09-26
            # that the platform gateway serves /chat/completions and /embeddings but
            # 404s /files, which is what the Files API needs.
            result["code"] = (
                "pdf_extract_endpoint_unavailable"
                if "404" in message
                else "pdf_extract_upload_failed"
            )
        return result


async def vision_reader_handler(
    operation: str,
    file_path: str = None,
    image_path: str = None,  # Alias for backward compatibility
    page_numbers: Optional[list[int]] = None,
    page_number: Optional[int] = None,
    region: Optional[Dict[str, float]] = None,
    question: Optional[str] = None,
    language: str = "en",
    max_pages: Optional[int] = None,
) -> Dict[str, Any]:
    """Vision reader tool handler - reads documents and images using vision model.

    This handler delegates visual understanding tasks to a multimodal model
    (qwen3.6-plus - native multimodal) and returns text that can be consumed by downstream LLMs.

    PDFs are read locally first (free) and only handed to the paid file-extract
    reader when the local reader finds no text, so a page count is a charge.

    Args:
        operation: One of "read_pdf", "read_image", "ocr_page", "read_equation_image", "describe_figure", "extract_table".
        file_path: Path to the file (PDF or image) on disk.
        image_path: Alias for file_path (backward compatibility).
        page_numbers: Optional list of specific pages to read (1-indexed, for PDFs).
        page_number: Optional single page index for logging / context.
        region: Optional normalized region of interest {x1,y1,x2,y2} in [0,1].
        question: Optional extra question or instruction about the content.
        language: Output language hint (currently only "en" is supported).
        max_pages: Per-read page budget; defaults to
            ``VISION_READER_PDF_MAX_PAGES`` (50). It bounds both the rendered pages
            and a paid parse: a document above the budget is refused until the
            caller names the pages it needs.
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
        # One canonical order, so the page subset, the prompt note and the report
        # all describe the same pages.
        selected_pages: List[int] = sorted({
            int(number) for number in (page_numbers or [])
            if isinstance(number, int) and not isinstance(number, bool) and number > 0
        })
        if isinstance(page_number, int) and not isinstance(page_number, bool) and not selected_pages:
            selected_pages = [page_number]
        budget = pdf_page_budget(max_pages)
        total_pages = count_pdf_pages(abs_path)

        # 1) Free local text extraction: a text PDF needs neither an upload nor a
        #    page charge. A scan yields almost no text and falls through.
        if local_text_first_enabled():
            local = extract_local_pdf_text(abs_path, page_numbers=selected_pages or None)
            if local is not None:
                logger.info(
                    "PDF read locally with pypdf: %s (%s pages, %s chars)",
                    abs_path.name, local["page_count"], local["text_length"],
                )
                return {
                    "tool": TOOL_NAME,
                    "success": True,
                    "operation": "read_pdf",
                    "file_path": str(abs_path),
                    "file_name": abs_path.name,
                    "method": "pypdf-local",
                    "page_count": local["page_count"],
                    "pages_read": local["pages_read"],
                    "text": local["text"],
                    "text_length": local["text_length"],
                }

        # 2) Paid file-extract, bounded: a page is a charge, so an explicit page
        #    selection is how a caller spends less; a blind whole-document parse
        #    above the budget is refused with the way out.
        if selected_pages and len(selected_pages) > budget:
            return {
                "tool": TOOL_NAME,
                "success": False,
                "operation": "read_pdf",
                "code": "page_budget_exceeded",
                "error": (
                    f"Requested {len(selected_pages)} pages but the per-read budget is {budget}. "
                    f"Narrow page_numbers, or raise {PDF_MAX_PAGES_ENV} deliberately."
                ),
                "page_count": total_pages,
                "page_budget": budget,
            }
        if not selected_pages and total_pages and total_pages > budget:
            return {
                "tool": TOOL_NAME,
                "success": False,
                "operation": "read_pdf",
                "code": "page_budget_exceeded",
                "error": (
                    f"{abs_path.name} has {total_pages} pages, above the per-read budget of {budget}. "
                    f"Pass page_numbers=[...] for the pages you actually need, or raise "
                    f"{PDF_MAX_PAGES_ENV} if the whole document really is required."
                ),
                "page_count": total_pages,
                "page_budget": budget,
            }

        # 2) A scan: rasterize the pages locally and let the multimodal model read
        #    them. This needs nothing beyond the chat API, so it works wherever the
        #    model does — including gateways without a Files API.
        if pdf_render_enabled():
            with tempfile.TemporaryDirectory(prefix="vision_pdf_render_") as scratch:
                rendered = render_pdf_pages(
                    abs_path,
                    page_numbers=selected_pages or None,
                    dest_dir=Path(scratch),
                )
                if rendered:
                    logger.info(
                        "PDF rendered locally with pypdfium2: %s (%s pages at %s dpi)",
                        abs_path.name, len(rendered), pdf_render_dpi(),
                    )
                    page_prompt = _SCANNED_PAGE_PROMPT
                    if question:
                        page_prompt = f"{page_prompt}\n\nAdditional instruction from the user: {question}"
                    pages_text: List[str] = []
                    for page in rendered:
                        number = page["page_number"]
                        try:
                            page_text = await _call_qwen_vision_api(page_prompt, str(page["path"]))
                        except Exception as exc:
                            logger.warning("Vision read failed for page %s of %s: %s", number, abs_path.name, exc)
                            page_text = ""
                        if page_text.strip():
                            pages_text.append(f"--- Page {number} ---\n{page_text}")
                    if pages_text:
                        text = "\n\n".join(pages_text)
                        return {
                            "tool": TOOL_NAME,
                            "success": True,
                            "operation": "read_pdf",
                            "file_path": str(abs_path),
                            "file_name": abs_path.name,
                            "method": "pypdfium2-vision",
                            "page_count": total_pages,
                            "pages_read": [page["page_number"] for page in rendered],
                            "text": text,
                            "text_length": len(text),
                        }
                    logger.info(
                        "Local rendering produced no readable text for %s; falling through to file-extract",
                        abs_path.name,
                    )

        prompt = question or (
            "Read this document and extract all text while preserving original structure "
            "(paragraphs, lists, headings, etc.)."
        )
        with tempfile.TemporaryDirectory(prefix="vision_pdf_pages_") as scratch:
            upload_path = abs_path
            if selected_pages:
                subset = build_page_subset_pdf(abs_path, selected_pages, Path(scratch))
                if subset is not None:
                    upload_path = subset
                prompt = (
                    f"{prompt}\n\n(Only pages {sorted(selected_pages)} of the original document "
                    f"are included in this file.)"
                )
            logger.info(f"Reading PDF with Qwen-Long file-extract: {upload_path.name}")
            result = await _read_pdf_with_qwen_long(str(upload_path), prompt=prompt)

        # The upload may have been a page subset; report the caller's document.
        result["tool"] = TOOL_NAME
        result["operation"] = "read_pdf"
        result["file_path"] = str(abs_path)
        result["file_name"] = abs_path.name
        if selected_pages:
            result["pages_parsed"] = sorted(selected_pages)
        if total_pages:
            result["source_page_count"] = total_pages
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
        "Reads PDFs and images. A text PDF is read locally for free (pypdf); a scan is "
        "rasterized locally (pypdfium2) and read by the multimodal model. Reading is "
        "page-bounded, so pass page_numbers for the pages you actually need — a "
        "whole-document read above the per-read budget is refused. Use for visual OCR, "
        "figures, and equations, not for DOCX."
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
                "description": (
                    "Optional list of specific pages to read (1-indexed, for PDFs). Only these "
                    "pages are uploaded, so only they are billed."
                ),
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
                "description": (
                    "Per-read page budget for reading a PDF (default: 50, env "
                    "VISION_READER_PDF_MAX_PAGES). It bounds the rendered pages as well as a "
                    "paid parse: a document above the budget is refused until page_numbers "
                    "names the pages that are needed."
                ),
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
