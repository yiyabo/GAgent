"""Centralized production LLM provider configuration."""
from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse


class LLMConfigurationError(RuntimeError):
    """Raised when an LLM provider profile is incomplete or unsafe."""


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def is_production() -> bool:
    return _env("APP_ENV", "development").lower() in {"prod", "production"}


def _require_url(name: str, value: str, *, production: bool) -> str:
    value = value.rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise LLMConfigurationError(f"{name} must be an absolute HTTP(S) URL")
    if production and parsed.scheme != "https" and _env("LLM_ALLOW_INSECURE_HTTP") not in {"1", "true", "yes"}:
        raise LLMConfigurationError(f"{name} must use HTTPS in production")
    return value


def _require_nonempty(name: str, value: str) -> str:
    if not value:
        raise LLMConfigurationError(f"{name} is required")
    return value


def _validate_platform_host(url: str) -> None:
    allowed = [x.lower() for x in _env("PLATFORM_LLM_ALLOWED_HOSTS").split(",") if x.strip()]
    if not allowed:
        return
    host = (urlparse(url).hostname or "").lower()
    if host not in allowed:
        raise LLMConfigurationError(f"platform LLM URL host '{host}' is not in PLATFORM_LLM_ALLOWED_HOSTS")


@dataclass(frozen=True)
class LLMProfile:
    provider: str
    api_url: str
    api_key: str
    model: str
    responses_api_url: str


def platform_profile() -> LLMProfile:
    production = is_production()
    url = _require_url("PLATFORM_LLM_API_URL", _env("PLATFORM_LLM_API_URL"), production=production)
    key = _require_nonempty("PLATFORM_LLM_API_KEY", _env("PLATFORM_LLM_API_KEY"))
    model = _require_nonempty("PLATFORM_LLM_MODEL", _env("PLATFORM_LLM_MODEL"))
    _validate_platform_host(url)
    if "dashscope.aliyuncs.com" in (urlparse(url).hostname or "").lower():
        raise LLMConfigurationError("PLATFORM_LLM_API_URL must not point to DashScope")
    responses = _env("PLATFORM_LLM_RESPONSES_API_URL")
    if not responses and url.endswith("/chat/completions"):
        responses = url[:-len("chat/completions")] + "responses"
    responses = _require_url("PLATFORM_LLM_RESPONSES_API_URL", responses or url, production=production)
    if "dashscope.aliyuncs.com" in (urlparse(responses).hostname or "").lower():
        raise LLMConfigurationError("PLATFORM_LLM_RESPONSES_API_URL must not point to DashScope")
    return LLMProfile("platform", url, key, model, responses)


def dashscope_test_profile() -> LLMProfile:
    if is_production():
        raise LLMConfigurationError("DashScope test profile is disabled in production")
    url = _require_url("DASHSCOPE_TEST_API_URL", _env("DASHSCOPE_TEST_API_URL"), production=False)
    key = _require_nonempty("DASHSCOPE_TEST_API_KEY", _env("DASHSCOPE_TEST_API_KEY"))
    model = _require_nonempty("DASHSCOPE_TEST_MODEL", _env("DASHSCOPE_TEST_MODEL"))
    responses = _env("DASHSCOPE_TEST_RESPONSES_API_URL")
    if not responses and url.endswith("/chat/completions"):
        responses = url[:-len("chat/completions")] + "responses"
    return LLMProfile("dashscope_test", url, key, model, _require_url("DASHSCOPE_TEST_RESPONSES_API_URL", responses or url, production=False))


def assert_no_production_direct_endpoint(url: str) -> None:
    if not is_production():
        return
    candidate = _require_url("LLM endpoint", url, production=True)
    host = (urlparse(candidate).hostname or "").lower()
    if "dashscope.aliyuncs.com" in host:
        raise LLMConfigurationError("direct DashScope endpoint is disabled in production")
    allowed = [x.lower() for x in _env("PLATFORM_LLM_ALLOWED_HOSTS").split(",") if x.strip()]
    if allowed and host not in allowed:
        raise LLMConfigurationError(f"LLM endpoint host '{host}' is not production-approved")
