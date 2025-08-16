import os
import json
import time
import random
from typing import Any, Dict, Optional
from urllib import request, error
from .interfaces import LLMProvider


def _truthy(val: Optional[str]) -> bool:
    return str(val).strip().lower() in {"1", "true", "yes", "y", "on"}


class LLMClient(LLMProvider):
    """
    A thin client for GLM-like chat completion APIs.

    Responsibilities:
    - Manage API configuration (key, url, model)
    - Provide chat() to get completion content
    - Provide ping() for connectivity check
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: int = 60,
        retries: Optional[int] = None,
        backoff_base: Optional[float] = None,
    ) -> None:
        self.api_key = api_key or os.getenv("GLM_API_KEY")
        self.url = url or os.getenv(
            "GLM_API_URL", "https://open.bigmodel.cn/api/paas/v4/chat/completions"
        )
        self.model = model or os.getenv("GLM_MODEL", "glm-4-flash")
        self.timeout = timeout
        self.mock = _truthy(os.getenv("LLM_MOCK", ""))
        # Retry/backoff configuration
        try:
            self.retries = int(os.getenv("LLM_RETRIES", "2")) if retries is None else int(retries)
        except Exception:
            self.retries = 2
        try:
            self.backoff_base = float(os.getenv("LLM_BACKOFF_BASE", "0.5")) if backoff_base is None else float(backoff_base)
        except Exception:
            self.backoff_base = 0.5

    def chat(self, prompt: str) -> str:
        if self.mock:
            # Return deterministic, parseable content in mock mode
            if "JSON object" in prompt or "\"tasks\"" in prompt or "tasks" in prompt:
                return (
                    '{"title":"Mock Plan","tasks":[{"name":"Mock A","prompt":"Do A"},{"name":"Mock B","prompt":"Do B"}]}'
                )
            return "This is a mock completion."

        if not self.api_key:
            raise RuntimeError("GLM_API_KEY is not set in environment")
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        data = json.dumps(payload).encode("utf-8")
        req = request.Request(self.url, data=data, headers=headers, method="POST")

        last_err: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            try:
                with request.urlopen(req, timeout=self.timeout) as resp:
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
                    delay = max(0.0, self.backoff_base * (2 ** attempt) + random.uniform(0, self.backoff_base / 4.0))
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
                    delay = max(0.0, self.backoff_base * (2 ** attempt) + random.uniform(0, self.backoff_base / 4.0))
                    time.sleep(delay)
                    last_err = e
                    continue
                raise RuntimeError(f"LLM request failed: {e}")

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
