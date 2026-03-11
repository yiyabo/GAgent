import json
import logging
import os
import random
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Dict, List, Optional
from urllib import error, request

import httpx

from .interfaces import LLMProvider
from .services.foundation.settings import get_settings

logger = logging.getLogger(__name__)


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
            selected_model = model or env_model or settings.qwen_model or "qwen3.5-plus"
            selected_model_str = str(selected_model).strip().lower()
            if selected_model_str and not selected_model_str.startswith("qwen"):
                logger.warning(
                    "Model '%s' is not a Qwen-series model; forcing model='%s'",
                    selected_model,
                    settings.qwen_model or "qwen3.5-plus",
                )
                selected_model = settings.qwen_model or "qwen3.5-plus"
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
            self.model = model or env_model or settings.qwen_model or "qwen3.5-plus"

        settings_timeout = getattr(settings, "llm_request_timeout", None)
        if settings_timeout in (None, ""):
            settings_timeout = getattr(settings, "glm_request_timeout", None)
        self.timeout = _normalize_timeout(timeout, settings_timeout)
        self.mock = False  # Mock
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

    def chat(self, prompt: str, force_real: bool = False, model: Optional[str] = None, messages: Optional[list] = None, **_: Any) -> str:
        if self.mock and not force_real:
            return "This is a mock completion."

        if not self.api_key:
            raise RuntimeError(f"{self.provider.upper()}_API_KEY is not set in environment")

        # Support full messages list for multi-turn conversations
        if messages:
            payload_messages = messages
        else:
            payload_messages = [{"role": "user", "content": prompt}]

        payload = {
            "model": model or self.model,
            "messages": payload_messages,
            "max_tokens": 16384,
        }
        headers = self._build_headers()
        data = json.dumps(payload).encode("utf-8")
        req = request.Request(self.url, data=data, headers=headers, method="POST")

        last_err: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            try:
                if self.timeout is None:
                    resp_ctx = request.urlopen(req)
                else:
                    resp_ctx = request.urlopen(req, timeout=self.timeout)
                with resp_ctx as resp:
                    resp_text = resp.read().decode("utf-8")
                    obj = json.loads(resp_text)
                try:
                    return obj["choices"][0]["message"]["content"]
                except Exception:
                    raise RuntimeError(f"Unexpected LLM response: {obj}")
            except error.HTTPError as e:
                # Retry only for 5xx; surface 4xx immediately
                code = getattr(e, "code", None)
                if isinstance(code, int) and 500 <= code < 600 and attempt < self.retries:
                    # backoff retry
                    delay = max(0.0, self.backoff_base * (2**attempt) + random.uniform(0, self.backoff_base / 4.0))
                    time.sleep(delay)
                    last_err = e
                    continue
                try:
                    msg = e.read().decode("utf-8")
                except Exception:
                    msg = str(e)
                raise RuntimeError(f"LLM HTTPError: {e.code} {msg}")
            except Exception as e:
                # Treat as transient (network) and retry
                if attempt < self.retries:
                    delay = max(0.0, self.backoff_base * (2**attempt) + random.uniform(0, self.backoff_base / 4.0))
                    time.sleep(delay)
                    last_err = e
                    continue
                raise RuntimeError(f"LLM request failed: {e}")

    async def stream_chat_async(
        self,
        prompt: str,
        force_real: bool = False,
        model: Optional[str] = None,
        messages: Optional[list] = None,
        **_: Any,
    ) -> AsyncIterator[str]:
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

        payload = {
            "model": model or self.model,
            "messages": payload_messages,
            "stream": True,
            "max_tokens": 16384,
        }
        headers = self._build_headers()

        timeout = None if self.timeout is None else httpx.Timeout(self.timeout)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST", self.url, headers=headers, json=payload
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
                    delta = self._extract_stream_delta(obj)
                    if delta:
                        yield delta

    async def stream_chat_with_tools_async(
        self,
        messages: list,
        tools: List[Dict[str, Any]],
        tool_choice: str = "auto",
        on_content_delta: Optional[Callable[[str], Any]] = None,
    ) -> NativeStreamResult:
        """
        Stream a chat completion with native tool calling support.

        Calls *on_content_delta* for each text chunk so the caller can relay
        thinking tokens in real time.  Returns a ``NativeStreamResult`` with
        the full accumulated content **and** any tool calls the model decided
        to make.
        """
        if not self.api_key:
            raise RuntimeError(f"{self.provider.upper()}_API_KEY is not set")

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": tool_choice,
            "stream": True,
            "max_tokens": 16384,
        }
        headers = self._build_headers()

        result = NativeStreamResult()
        # Accumulator for streamed tool_calls keyed by index
        tc_accum: Dict[int, Dict[str, str]] = {}

        timeout = None if self.timeout is None else httpx.Timeout(self.timeout)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", self.url, headers=headers, json=payload) as resp:
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

                    # Content delta
                    content = delta.get("content")
                    if isinstance(content, str) and content:
                        result.content += content
                        if on_content_delta is not None:
                            try:
                                ret = on_content_delta(content)
                                import asyncio
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
