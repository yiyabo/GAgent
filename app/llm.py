import json
import os
import random
import time
from typing import Any, Dict, Generator, Optional, List
from urllib import error, request

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
        timeout: int = 300,
        retries: Optional[int] = None,
        backoff_base: Optional[float] = None,
    ) -> None:
        self.api_key = api_key or os.getenv("GLM_API_KEY")
        self.url = url or os.getenv(
            "GLM_CHAT_API_URL", "https://open.bigmodel.cn/api/paas/v4/chat/completions"
        )
        self.model = model or os.getenv("GLM_MODEL", "glm-4-flash")
        self.timeout = timeout
        self.mock = _truthy(os.getenv("LLM_MOCK", ""))
        # Retry/backoff configuration
        try:
            self.retries = (
                int(os.getenv("LLM_RETRIES", "2")) if retries is None else int(retries)
            )
        except Exception:
            self.retries = 2
        try:
            self.backoff_base = (
                float(os.getenv("LLM_BACKOFF_BASE", "0.5"))
                if backoff_base is None
                else float(backoff_base)
            )
        except Exception:
            self.backoff_base = 0.5

    def chat(self, prompt: str, history: Optional[List[Dict[str, str]]] = None) -> str:
        if self.mock:
            # Return deterministic, parseable content in mock mode
            if (
                "JSON object" in prompt
                or '"tasks"' in prompt
                or "tasks" in prompt
                or "Break down" in prompt
            ):
                return '{"title":"AI医疗应用报告","tasks":[{"name":"引言和背景","prompt":"撰写人工智能在医疗领域应用报告的引言部分，介绍AI技术在医疗行业的发展历程和重要性。"},{"name":"核心技术概述","prompt":"详细介绍医疗AI的核心技术，包括机器学习、深度学习、自然语言处理等关键技术。"},{"name":"临床应用案例","prompt":"分析具体的医疗AI应用案例，如医学影像诊断、药物发现、个性化治疗等。"},{"name":"挑战与限制","prompt":"讨论当前医疗AI面临的技术挑战、伦理问题和监管限制。"},{"name":"未来发展趋势","prompt":"展望医疗AI的发展前景，分析新兴技术和应用方向。"}]}'
            return "This is a mock completion."

        if not self.api_key:
            raise RuntimeError("GLM_API_KEY is not set in environment")
        
        messages = history or []
        messages.append({"role": "user", "content": prompt})
        
        payload = {
            "model": self.model,
            "messages": messages,
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
                if (
                    isinstance(code, int)
                    and 500 <= code < 600
                    and attempt < self.retries
                ):
                    # backoff retry
                    delay = max(
                        0.0,
                        self.backoff_base * (2**attempt)
                        + random.uniform(0, self.backoff_base / 4.0),
                    )
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
                    delay = max(
                        0.0,
                        self.backoff_base * (2**attempt)
                        + random.uniform(0, self.backoff_base / 4.0),
                    )
                    time.sleep(delay)
                    last_err = e
                    continue
                raise RuntimeError(f"LLM request failed: {e}")

    def chat_stream(self, prompt: str) -> Generator[str, None, None]:
        """流式生成聊天响应"""
        if self.mock:
            # Mock模式下模拟流式输出
            mock_response = "这是 一个 模拟的 流式 响应。 我将 逐步 生成 内容 来 演示 流式输出 的 效果。"
            words = mock_response.split()
            for word in words:
                yield word + " "
                time.sleep(0.05)  # 模拟生成延迟
            return

        if not self.api_key:
            raise RuntimeError("GLM_API_KEY is not set in environment")

        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
            "max_tokens": 4096,
            "temperature": 0.7,
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
                    # 处理Server-Sent Events流
                    buffer = ""
                    while True:
                        chunk = resp.read(1024).decode("utf-8")
                        if not chunk:
                            break

                        buffer += chunk
                        lines = buffer.split("\n")
                        buffer = lines.pop()  # 保留最后不完整的行

                        for line in lines:
                            line = line.strip()
                            if line.startswith("data: "):
                                data_content = line[6:]  # 移除 'data: ' 前缀
                                if data_content == "[DONE]":
                                    return
                                try:
                                    chunk_data = json.loads(data_content)
                                    if (
                                        "choices" in chunk_data
                                        and chunk_data["choices"]
                                    ):
                                        delta = chunk_data["choices"][0].get(
                                            "delta", {}
                                        )
                                        content = delta.get("content", "")
                                        if content:
                                            yield content
                                except json.JSONDecodeError:
                                    continue
                return

            except error.HTTPError as e:
                # Retry logic similar to chat()
                code = getattr(e, "code", None)
                if (
                    isinstance(code, int)
                    and 500 <= code < 600
                    and attempt < self.retries
                ):
                    delay = max(
                        0.0,
                        self.backoff_base * (2**attempt)
                        + random.uniform(0, self.backoff_base / 4.0),
                    )
                    time.sleep(delay)
                    last_err = e
                    continue
                try:
                    msg = e.read().decode("utf-8")
                except Exception:
                    msg = str(e)
                raise RuntimeError(f"LLM HTTPError: {e.code} {msg}")
            except Exception as e:
                if attempt < self.retries:
                    delay = max(
                        0.0,
                        self.backoff_base * (2**attempt)
                        + random.uniform(0, self.backoff_base / 4.0),
                    )
                    time.sleep(delay)
                    last_err = e
                    continue
                raise RuntimeError(f"LLM streaming request failed: {e}")

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
    return _default_client
