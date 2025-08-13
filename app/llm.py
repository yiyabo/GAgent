import os
import json
from typing import Any, Dict, Optional
from urllib import request, error


class LLMClient:
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
    ) -> None:
        self.api_key = api_key or os.getenv("GLM_API_KEY")
        self.url = url or os.getenv(
            "GLM_API_URL", "https://open.bigmodel.cn/api/paas/v4/chat/completions"
        )
        self.model = model or os.getenv("GLM_MODEL", "glm-4-flash")
        self.timeout = timeout

    def chat(self, prompt: str) -> str:
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
        try:
            with request.urlopen(req, timeout=self.timeout) as resp:
                resp_text = resp.read().decode("utf-8")
                obj = json.loads(resp_text)
        except error.HTTPError as e:
            try:
                msg = e.read().decode("utf-8")
            except Exception:
                msg = str(e)
            raise RuntimeError(f"LLM HTTPError: {e.code} {msg}")
        except Exception as e:
            raise RuntimeError(f"LLM request failed: {e}")

        try:
            return obj["choices"][0]["message"]["content"]
        except Exception:
            raise RuntimeError(f"Unexpected LLM response: {obj}")

    def ping(self) -> bool:
        try:
            _ = self.chat("ping")
            return True
        except Exception:
            return False

    def config(self) -> Dict[str, Any]:
        return {"url": self.url, "model": self.model, "has_api_key": bool(self.api_key)}


_default_client: Optional[LLMClient] = None


def get_default_client() -> LLMClient:
    global _default_client
    if _default_client is None:
        _default_client = LLMClient()
    return _default_client
