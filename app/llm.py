import asyncio
import json
import logging
import os
import random
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

import httpx

from .interfaces import LLMProvider
from .services.foundation.settings import get_settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared HTTP connection pools — eliminates per-request TCP/TLS handshake
# overhead (typically 60-150 ms saved per LLM call).
#
# * ``_shared_async_client`` is used by all ``async`` LLM methods.
# * ``_shared_sync_client`` is used by the synchronous ``chat()`` method,
#   replacing the legacy ``urllib.request.urlopen`` calls which created a
#   brand-new connection each time *and* blocked the event loop.
#
# Both clients are initialised lazily on first access and must be shut down
# explicitly via ``close_shared_clients()`` during application teardown
# (called from ``app/main.py`` lifespan).
# ---------------------------------------------------------------------------

_shared_async_client: Optional[httpx.AsyncClient] = None
_shared_sync_client: Optional[httpx.Client] = None

# Default pool limits — generous enough for multi-provider concurrent usage
# while keeping resource consumption bounded.
_POOL_LIMITS = httpx.Limits(
    max_connections=20,
    max_keepalive_connections=10,
    keepalive_expiry=30.0,
)
_SYNC_POOL_LIMITS = httpx.Limits(
    max_connections=10,
    max_keepalive_connections=5,
    keepalive_expiry=30.0,
)
# Default connect timeout (TCP + TLS); per-request read timeout is overridden
# at call sites to match the configured ``self.timeout`` / ``self.stream_timeout``.
_DEFAULT_CONNECT_TIMEOUT = 10.0
_DEFAULT_TIMEOUT = httpx.Timeout(60.0, connect=_DEFAULT_CONNECT_TIMEOUT)


def _make_request_timeout(overall: Optional[float]) -> Optional[httpx.Timeout]:
    """Build a per-request ``httpx.Timeout`` that respects the caller's budget.

    The connect phase timeout is capped at ``_DEFAULT_CONNECT_TIMEOUT`` (10 s) but
    will never *exceed* the caller-supplied ``overall`` timeout.  This way a
    deployment that sets ``llm_request_timeout=2`` will still fail-fast on connect
    rather than spending 10 s in DNS/TCP/TLS before each retry.
    """
    if overall is None:
        return None
    connect = min(_DEFAULT_CONNECT_TIMEOUT, overall)
    return httpx.Timeout(overall, connect=connect)


def _get_shared_async_client() -> httpx.AsyncClient:
    """Return (and lazily create) the shared async HTTP client."""
    global _shared_async_client
    if _shared_async_client is None:
        logger.info("[LLM] Creating shared async httpx client (connection pool)")
        _shared_async_client = httpx.AsyncClient(
            limits=_POOL_LIMITS,
            timeout=_DEFAULT_TIMEOUT,
            follow_redirects=True,
        )
    return _shared_async_client


def _get_shared_sync_client() -> httpx.Client:
    """Return (and lazily create) the shared synchronous HTTP client."""
    global _shared_sync_client
    if _shared_sync_client is None:
        logger.info("[LLM] Creating shared sync httpx client (connection pool)")
        _shared_sync_client = httpx.Client(
            limits=_SYNC_POOL_LIMITS,
            timeout=_DEFAULT_TIMEOUT,
            follow_redirects=True,
        )
    return _shared_sync_client


async def init_shared_clients() -> None:
    """Pre-warm the shared HTTP clients.  Called from application startup."""
    _get_shared_async_client()
    _get_shared_sync_client()
    logger.info("[LLM] Shared HTTP connection pools initialised")


async def close_shared_clients() -> None:
    """Gracefully close shared HTTP clients.  Called from application shutdown."""
    global _shared_async_client, _shared_sync_client
    if _shared_async_client is not None:
        try:
            await _shared_async_client.aclose()
            logger.info("[LLM] Shared async httpx client closed")
        except Exception as exc:
            logger.warning("[LLM] Error closing async client: %s", exc)
        _shared_async_client = None
    if _shared_sync_client is not None:
        try:
            _shared_sync_client.close()
            logger.info("[LLM] Shared sync httpx client closed")
        except Exception as exc:
            logger.warning("[LLM] Error closing sync client: %s", exc)
        _shared_sync_client = None


def _normalize_timeout(timeout: Optional[float], fallback: Optional[float]) -> Optional[float]:
    candidate = timeout
    if candidate is None:
        candidate = fallback
    try:
        if candidate is None:
            return None
        value = float(candidate)
    except (TypeError, ValueError):
        return None if candidate is None else None
    if value <= 0:
        return None
    return value


@dataclass
class NativeToolCall:
    """A single tool call returned by the model via native function calling."""
    id: str
    name: str
    arguments: Dict[str, Any]


@dataclass
class NativeStreamResult:
    """Accumulated result from a streaming response with native tool calls."""
    content: str = ""
    reasoning_content: str = ""
    tool_calls: List[NativeToolCall] = field(default_factory=list)
    finish_reason: Optional[str] = None


def _truthy(val: Optional[str]) -> bool:
    return str(val).strip().lower() in {"1", "true", "yes", "y", "on"}


class LLMClient(LLMProvider):
    """
    Multi-provider LLM client supporting GLM, Perplexity, and other APIs.

    Responsibilities:
    - Manage API configuration for different providers
    - Provide chat() to get completion content
    - Provide ping() for connectivity check
    - Auto-switch providers based on configuration
    """

    def __init__(
        self,
        provider: Optional[str] = None,
        api_key: Optional[str] = None,
        url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[float] = None,
        stream_timeout: Optional[float] = None,
        retries: Optional[int] = None,
        backoff_base: Optional[float] = None,
    ) -> None:
        settings = get_settings()

        requested_provider = (
            provider or os.getenv("LLM_PROVIDER") or settings.llm_provider or "qwen"
        )
        requested_provider = str(requested_provider).strip().lower()
        if requested_provider == "glm":
            logger.warning("LLM provider 'glm' is deprecated; forcing provider='qwen'")
            requested_provider = "qwen"
        self.provider = requested_provider

        provider_name = self.provider.lower()

        if provider_name == "perplexity":
            env_api_key = os.getenv("PERPLEXITY_API_KEY")
            env_url = os.getenv("PERPLEXITY_API_URL")
            env_model = os.getenv("PERPLEXITY_MODEL")
            self.api_key = api_key or env_api_key or settings.perplexity_api_key
            self.url = url or env_url or settings.perplexity_api_url
            self.model = model or env_model or settings.perplexity_model
        elif provider_name == "qwen":
            env_api_key = os.getenv("QWEN_API_KEY")
            env_url = os.getenv("QWEN_API_URL")
            env_model = os.getenv("QWEN_MODEL")
            self.api_key = api_key or env_api_key or settings.qwen_api_key
            self.url = url or env_url or settings.qwen_api_url or "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
            selected_model = model or env_model or settings.qwen_model or "qwen3.6-plus"
            selected_model_str = str(selected_model).strip().lower()
            if selected_model_str and not selected_model_str.startswith("qwen"):
                logger.warning(
                    "Model '%s' is not a Qwen-series model; forcing model='%s'",
                    selected_model,
                    settings.qwen_model or "qwen3.6-plus",
                )
                selected_model = settings.qwen_model or "qwen3.6-plus"
            self.model = selected_model
        elif provider_name == "kimi":
            env_api_key = os.getenv("KIMI_API_KEY")
            env_url = os.getenv("KIMI_API_URL")
            env_model = os.getenv("KIMI_MODEL")
            # Fallback: allow using DashScope OpenAI-compatible QWEN_* envs to host Kimi models
            # (Do NOT require changing .env; only used when KIMI_* is missing.)
            qwen_api_key = os.getenv("QWEN_API_KEY")
            qwen_api_url = os.getenv("QWEN_API_URL")
            qwen_kimi_model_new = os.getenv("QWEN_KIMI_MODEL_NEW")
            qwen_kimi_model = os.getenv("QWEN_KIMI_MODEL")
            settings_api_key = getattr(settings, "kimi_api_key", None)
            settings_api_url = getattr(settings, "kimi_api_url", None)
            settings_model = getattr(settings, "kimi_model", None)
            # Prefer explicit args > KIMI_* > settings.kimi_*; then fallback to QWEN_* if still missing
            self.api_key = (
                api_key
                or env_api_key
                or settings_api_key
                or qwen_api_key
                or getattr(settings, "qwen_api_key", None)
            )
            self.url = (
                url
                or env_url
                or settings_api_url
                or qwen_api_url
                or getattr(settings, "qwen_api_url", None)
            )
            # Default to user requested model name; allow override via env/args
            self.model = (
                model
                or env_model
                or settings_model
                or qwen_kimi_model_new
                or qwen_kimi_model
                or "kimi-k2.5"
            )
        elif provider_name == "openai":
            env_api_key = os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_API")
            env_url = os.getenv("OPENAI_URL")
            env_model = os.getenv("OPENAI_MODEL")
            self.api_key = api_key or env_api_key or settings.openai_api_key
            self.url = url or env_url or "https://api.openai.com/v1/chat/completions"
            self.model = model or env_model or "gpt-4o-mini"
            self.openai_project = os.getenv("OPENAI_PROJECT")
            self.openai_org = os.getenv("OPENAI_ORG") or os.getenv("OPENAI_ORGANIZATION")
        else:  # unknown provider -> fallback to qwen
            logger.warning("Unknown provider '%s'; forcing provider='qwen'", provider_name)
            self.provider = "qwen"
            env_api_key = os.getenv("QWEN_API_KEY")
            env_url = os.getenv("QWEN_API_URL")
            env_model = os.getenv("QWEN_MODEL")
            self.api_key = api_key or env_api_key or settings.qwen_api_key
            self.url = (
                url
                or env_url
                or settings.qwen_api_url
                or "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
            )
            self.model = model or env_model or settings.qwen_model or "qwen3.6-plus"

        settings_timeout = getattr(settings, "llm_request_timeout", None)
        if settings_timeout in (None, ""):
            settings_timeout = getattr(settings, "glm_request_timeout", None)
        self.timeout = _normalize_timeout(timeout, settings_timeout)
        settings_stream_timeout = getattr(settings, "llm_stream_timeout", None)
        self.stream_timeout = _normalize_timeout(stream_timeout, settings_stream_timeout)
        if stream_timeout is None:
            if self.stream_timeout is None:
                self.stream_timeout = self.timeout
            elif self.timeout is not None:
                self.stream_timeout = max(self.stream_timeout, self.timeout)
        self.mock = os.getenv("LLM_MOCK", "").strip().lower() in {"1", "true", "yes"}
        # Retry/backoff configuration
        try:
            if retries is None:
                env_r = os.getenv("LLM_RETRIES")
                self.retries = int(env_r) if env_r is not None else int(settings.llm_retries)
            else:
                self.retries = int(retries)
        except Exception:
            self.retries = 2
        try:
            if backoff_base is None:
                env_b = os.getenv("LLM_BACKOFF_BASE")
                self.backoff_base = float(env_b) if env_b is not None else float(settings.llm_backoff_base)
            else:
                self.backoff_base = float(backoff_base)
        except Exception:
            self.backoff_base = 0.5

    def chat(
        self,
        prompt: str,
        force_real: bool = False,
        model: Optional[str] = None,
        messages: Optional[list] = None,
        **kwargs: Any,
    ) -> str:
        if self.mock and not force_real:
            return "This is a mock completion."

        if not self.api_key:
            raise RuntimeError(f"{self.provider.upper()}_API_KEY is not set in environment")

        # Support full messages list for multi-turn conversations
        if messages:
            payload_messages = messages
        else:
            payload_messages = [{"role": "user", "content": prompt}]

        try:
            max_tokens = int(kwargs.pop("max_tokens"))
        except (KeyError, TypeError, ValueError):
            max_tokens = 16384
        timeout_override = _normalize_timeout(kwargs.pop("timeout", None), self.timeout)
        try:
            request_retries = max(0, int(kwargs.pop("retries")))
        except (KeyError, TypeError, ValueError):
            request_retries = self.retries
        payload = {
            "model": model or self.model,
            "messages": payload_messages,
            "max_tokens": max_tokens,
        }
        headers = self._build_headers()
        timeout = _make_request_timeout(timeout_override)

        client = _get_shared_sync_client()
        last_err: Optional[Exception] = None
        for attempt in range(request_retries + 1):
            try:
                response = client.post(
                    self.url, headers=headers, json=payload,
                    timeout=timeout,
                )
                response.raise_for_status()
                obj = response.json()
                try:
                    return obj["choices"][0]["message"]["content"]
                except Exception:
                    raise RuntimeError(f"Unexpected LLM response: {obj}")
            except httpx.HTTPStatusError as e:
                status_code = e.response.status_code if e.response is not None else None
                if isinstance(status_code, int) and 500 <= status_code < 600 and attempt < request_retries:
                    delay = max(0.0, self.backoff_base * (2**attempt) + random.uniform(0, self.backoff_base / 4.0))
                    time.sleep(delay)
                    last_err = e
                    continue
                body = ""
                try:
                    body = e.response.text if e.response is not None else ""
                except Exception:
                    body = str(e)
                raise RuntimeError(f"LLM HTTPError: {status_code} {body}".strip())
            except Exception as e:
                # Treat as transient (network) and retry
                if attempt < request_retries:
                    delay = max(0.0, self.backoff_base * (2**attempt) + random.uniform(0, self.backoff_base / 4.0))
                    time.sleep(delay)
                    last_err = e
                    continue
                raise RuntimeError(f"LLM request failed: {e}")

    async def chat_async(
        self,
        prompt: str,
        force_real: bool = False,
        model: Optional[str] = None,
        messages: Optional[list] = None,
        **kwargs: Any,
    ) -> str:
        if self.mock and not force_real:
            return "This is a mock completion."

        if not self.api_key:
            raise RuntimeError(f"{self.provider.upper()}_API_KEY is not set in environment")

        payload_messages = messages if messages else [{"role": "user", "content": prompt}]
        try:
            max_tokens = int(kwargs.pop("max_tokens"))
        except (KeyError, TypeError, ValueError):
            max_tokens = 16384
        timeout_override = _normalize_timeout(kwargs.pop("timeout", None), self.timeout)
        try:
            request_retries = max(0, int(kwargs.pop("retries")))
        except (KeyError, TypeError, ValueError):
            request_retries = self.retries
        payload = {
            "model": model or self.model,
            "messages": payload_messages,
            "max_tokens": max_tokens,
        }
        headers = self._build_headers()
        timeout = _make_request_timeout(timeout_override)
        client = _get_shared_async_client()

        for attempt in range(request_retries + 1):
            try:
                response = await client.post(
                    self.url, headers=headers, json=payload,
                    timeout=timeout,
                )
                response.raise_for_status()
                obj = response.json()
                try:
                    return obj["choices"][0]["message"]["content"]
                except Exception:
                    raise RuntimeError(f"Unexpected LLM response: {obj}")
            except httpx.HTTPStatusError as e:
                status_code = e.response.status_code if e.response is not None else None
                if isinstance(status_code, int) and 500 <= status_code < 600 and attempt < request_retries:
                    delay = max(0.0, self.backoff_base * (2**attempt) + random.uniform(0, self.backoff_base / 4.0))
                    await asyncio.sleep(delay)
                    continue
                body = ""
                try:
                    body = e.response.text if e.response is not None else ""
                except Exception:
                    body = str(e)
                raise RuntimeError(f"LLM HTTPError: {status_code} {body}".strip())
            except Exception as e:
                if attempt < request_retries:
                    delay = max(0.0, self.backoff_base * (2**attempt) + random.uniform(0, self.backoff_base / 4.0))
                    await asyncio.sleep(delay)
                    continue
                raise RuntimeError(f"LLM request failed: {e}")

    async def stream_chat_async(
        self,
        prompt: str,
        force_real: bool = False,
        model: Optional[str] = None,
        messages: Optional[list] = None,
        enable_thinking: Optional[bool] = None,
        thinking_budget: Optional[int] = None,
        on_reasoning_delta: Optional[Callable[[str], Any]] = None,
        **_: Any,
    ) -> AsyncIterator[str]:
        """Stream a chat completion, optionally with thinking enabled.

        When *enable_thinking* is ``True``, reasoning tokens are accumulated
        and relayed via *on_reasoning_delta* but **not** yielded as regular
        content.  Only final assistant content is yielded.
        """
        if self.mock and not force_real:
            yield "This is a mock completion."
            return

        if not self.api_key:
            raise RuntimeError(f"{self.provider.upper()}_API_KEY is not set in environment")

        # Support full messages list for multi-turn conversations
        if messages:
            payload_messages = messages
        else:
            payload_messages = [{"role": "user", "content": prompt}]

        payload: Dict[str, Any] = {
            "model": model or self.model,
            "messages": payload_messages,
            "stream": True,
            "max_tokens": 16384,
        }
        if enable_thinking is not None:
            payload["enable_thinking"] = enable_thinking
        if thinking_budget is not None:
            payload["thinking_budget"] = thinking_budget

        headers = self._build_headers()

        timeout = _make_request_timeout(self.stream_timeout)
        client = _get_shared_async_client()
        async with client.stream(
            "POST", self.url, headers=headers, json=payload,
            timeout=timeout,
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line:
                    continue
                if not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if not data:
                    continue
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue

                # Extract reasoning_content delta if present
                choices = obj.get("choices")
                if isinstance(choices, list) and choices:
                    delta = choices[0].get("delta") if isinstance(choices[0], dict) else None
                    if isinstance(delta, dict):
                        reasoning = delta.get("reasoning_content")
                        if isinstance(reasoning, str) and reasoning and on_reasoning_delta is not None:
                            try:
                                ret = on_reasoning_delta(reasoning)
                                if asyncio.iscoroutine(ret):
                                    await ret
                            except Exception:
                                pass

                # Yield regular content
                content_delta = self._extract_stream_delta(obj)
                if content_delta:
                    yield content_delta

    async def stream_chat_with_tools_async(
        self,
        messages: list,
        tools: List[Dict[str, Any]],
        tool_choice: str = "auto",
        model: Optional[str] = None,
        on_content_delta: Optional[Callable[[str], Any]] = None,
        on_reasoning_delta: Optional[Callable[[str], Any]] = None,
        enable_thinking: Optional[bool] = None,
        thinking_budget: Optional[int] = None,
    ) -> NativeStreamResult:
        """
        Stream a chat completion with native tool calling support.

        Calls *on_content_delta* for each text chunk so the caller can relay
        thinking tokens in real time.  When *enable_thinking* is ``True`` the
        model may emit ``reasoning_content`` deltas which are accumulated and
        relayed via *on_reasoning_delta*.

        Returns a ``NativeStreamResult`` with the full accumulated content,
        reasoning content, **and** any tool calls the model decided to make.
        """
        max_retries = max(1, self.retries)
        last_err: Optional[Exception] = None
        for attempt in range(max_retries + 1):
            try:
                return await self._stream_chat_with_tools_inner(
                    messages=messages,
                    tools=tools,
                    tool_choice=tool_choice,
                    model=model,
                    on_content_delta=on_content_delta,
                    on_reasoning_delta=on_reasoning_delta,
                    enable_thinking=enable_thinking,
                    thinking_budget=thinking_budget,
                )
            except Exception as e:
                err_text = str(e).lower()
                is_transient = (
                    "closed" in err_text
                    or "tcptransport" in err_text
                    or "connection" in err_text
                    or isinstance(e, (httpx.ConnectError, httpx.RemoteProtocolError))
                )
                if is_transient and attempt < max_retries:
                    delay = max(0.5, self.backoff_base * (2 ** attempt))
                    logger.warning(
                        "[LLM] stream_chat_with_tools transient error (attempt %d/%d): %s. Retrying in %.1fs",
                        attempt + 1, max_retries + 1, e, delay,
                    )
                    await asyncio.sleep(delay)
                    last_err = e
                    continue
                raise
        raise RuntimeError(f"stream_chat_with_tools failed after {max_retries + 1} attempts: {last_err}")

    async def _stream_chat_with_tools_inner(
        self,
        messages: list,
        tools: List[Dict[str, Any]],
        tool_choice: str = "auto",
        model: Optional[str] = None,
        on_content_delta: Optional[Callable[[str], Any]] = None,
        on_reasoning_delta: Optional[Callable[[str], Any]] = None,
        enable_thinking: Optional[bool] = None,
        thinking_budget: Optional[int] = None,
    ) -> NativeStreamResult:
        if self.mock:
            mock_content = "This is a mock completion."
            if on_content_delta:
                try:
                    ret = on_content_delta(mock_content)
                    if asyncio.iscoroutine(ret):
                        await ret
                except Exception:
                    pass
            return NativeStreamResult(
                content=mock_content,
                reasoning_content="",
                tool_calls=[],
                finish_reason="stop",
            )

        if not self.api_key:
            raise RuntimeError(f"{self.provider.upper()}_API_KEY is not set")

        payload: Dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": tool_choice,
            "stream": True,
            "max_tokens": 16384,
        }

        # Inject thinking parameters (DashScope OpenAI-compatible endpoint
        # accepts these as top-level fields alongside model/messages).
        if enable_thinking is not None:
            payload["enable_thinking"] = enable_thinking
        if thinking_budget is not None:
            payload["thinking_budget"] = thinking_budget

        headers = self._build_headers()

        result = NativeStreamResult()
        # Accumulator for streamed tool_calls keyed by index
        tc_accum: Dict[int, Dict[str, str]] = {}

        timeout = _make_request_timeout(self.stream_timeout)
        client = _get_shared_async_client()
        async with client.stream("POST", self.url, headers=headers, json=payload,
                                 timeout=timeout) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue

                choices = obj.get("choices")
                if not isinstance(choices, list) or not choices:
                    continue
                choice = choices[0]
                if not isinstance(choice, dict):
                    continue

                fr = choice.get("finish_reason")
                if isinstance(fr, str):
                    result.finish_reason = fr

                delta = choice.get("delta")
                if not isinstance(delta, dict):
                    continue

                # Reasoning content delta (thinking tokens from enable_thinking)
                reasoning = delta.get("reasoning_content")
                if isinstance(reasoning, str) and reasoning:
                    result.reasoning_content += reasoning
                    if on_reasoning_delta is not None:
                        try:
                            ret = on_reasoning_delta(reasoning)
                            if asyncio.iscoroutine(ret):
                                await ret
                        except Exception:
                            pass

                # Content delta
                content = delta.get("content")
                if isinstance(content, str) and content:
                    result.content += content
                    if on_content_delta is not None:
                        try:
                            ret = on_content_delta(content)
                            if asyncio.iscoroutine(ret):
                                await ret
                        except Exception:
                            pass

                # Tool call deltas
                tcs = delta.get("tool_calls")
                if isinstance(tcs, list):
                    for tc_delta in tcs:
                        if not isinstance(tc_delta, dict):
                            continue
                        idx = tc_delta.get("index", 0)
                        if idx not in tc_accum:
                            tc_accum[idx] = {"id": "", "name": "", "arguments": ""}
                        tc_id = tc_delta.get("id")
                        if isinstance(tc_id, str) and tc_id:
                            tc_accum[idx]["id"] = tc_id
                        fn = tc_delta.get("function")
                        if isinstance(fn, dict):
                            fn_name = fn.get("name")
                            if isinstance(fn_name, str) and fn_name:
                                tc_accum[idx]["name"] = fn_name
                            fn_args = fn.get("arguments")
                            if isinstance(fn_args, str):
                                tc_accum[idx]["arguments"] += fn_args

        # Parse accumulated tool calls
        for idx in sorted(tc_accum.keys()):
            raw = tc_accum[idx]
            try:
                args = json.loads(raw["arguments"]) if raw["arguments"] else {}
            except json.JSONDecodeError:
                args = {"_raw": raw["arguments"]}
            result.tool_calls.append(
                NativeToolCall(id=raw["id"], name=raw["name"], arguments=args)
            )

        return result

    def _extract_stream_delta(self, payload: Dict[str, Any]) -> Optional[str]:
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            choice = choices[0] if isinstance(choices[0], dict) else None
            if choice:
                delta = choice.get("delta")
                if isinstance(delta, dict) and isinstance(delta.get("content"), str):
                    return delta.get("content")
                message = choice.get("message")
                if isinstance(message, dict) and isinstance(message.get("content"), str):
                    return message.get("content")
                if isinstance(choice.get("text"), str):
                    return choice.get("text")
                if isinstance(choice.get("content"), str):
                    return choice.get("content")
        data = payload.get("data")
        if isinstance(data, str):
            return data
        return None

    def _build_headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        if self.provider.lower() == "openai":
            project = getattr(self, "openai_project", None)
            org = getattr(self, "openai_org", None)
            if project:
                headers["OpenAI-Project"] = project
            if org:
                headers["OpenAI-Organization"] = org
        return headers

    def ping(self) -> bool:
        if self.mock:
            return True
        try:
            _ = self.chat("ping")
            return True
        except Exception:
            return False

    def config(self) -> Dict[str, Any]:
        return {
            "url": self.url,
            "model": self.model,
            "has_api_key": bool(self.api_key),
            "mock": bool(self.mock),
        }


_default_client: Optional[LLMClient] = None


def get_default_client() -> LLMClient:
    global _default_client
    if _default_client is None:
        _default_client = LLMClient()
    return _default_client


def reset_default_client() -> None:
    global _default_client
    _default_client = None
    try:
        from app.services.llm.llm_service import reset_llm_services

        reset_llm_services()
    except Exception:
        # Avoid turning cache reset into a hard dependency during import-time
        # teardown paths.
        pass
