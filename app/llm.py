import json
import os
import random
import time
from typing import Any, Dict, Optional
from urllib import error, request

from .interfaces import LLMProvider
from .services.foundation.settings import get_settings


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
        settings = get_settings()
        # 环境变量优先于集中配置，便于测试中 monkeypatch 生效
        env_api_key = os.getenv("GLM_API_KEY")
        env_url = os.getenv("GLM_API_URL")
        env_model = os.getenv("GLM_MODEL")
        self.api_key = api_key or env_api_key or settings.glm_api_key
        self.url = url or env_url or settings.glm_api_url or "https://open.bigmodel.cn/api/paas/v4/chat/completions"
        self.model = model or env_model or settings.glm_model or "glm-4-flash"
        self.timeout = timeout or settings.glm_request_timeout
        # CRITICAL FIX: Force mock mode to False to prevent unexpected mock responses.
        # The LLM_MOCK environment variable has caused persistent issues.
        self.mock = False

        # Embedded test key fallback (for local benchmarking only)
        # If no GLM_API_KEY provided, use a test key to simplify non-mock evaluations.
        # WARNING: Do not use in production environments.
        if not self.api_key:
            self.api_key = os.getenv("GLM_TEST_API_KEY") or "f887acb2128f41988821c38ee395f542.rmgIq0MwACMMh0Mw"
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

    def chat(self, prompt: str, force_real: bool = False) -> str:
        if self.mock and not force_real:
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
