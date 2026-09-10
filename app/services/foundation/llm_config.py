"""Validated production configuration for the platform LLM gateway."""
from __future__ import annotations

import ipaddress
import os
import socket
from dataclasses import dataclass
from urllib.parse import urlparse


class LLMConfigurationError(RuntimeError):
    """Raised when an LLM gateway configuration is incomplete or unsafe."""


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def is_production() -> bool:
    return _env("APP_ENV", "development").lower() in {"prod", "production"}


def _allowed_hosts() -> set[str]:
    return {
        item.strip().lower()
        for item in _env("PLATFORM_LLM_ALLOWED_HOSTS").split(",")
        if item.strip()
    }


def _is_public_address(address: str) -> bool:
    try:
        return ipaddress.ip_address(address).is_global
    except ValueError:
        return False


def _validate_host(name: str, host: str, *, production: bool) -> None:
    if not host:
        raise LLMConfigurationError(f"{name} must include a host")
    normalized = host.rstrip(".").lower()
    if normalized in {"localhost", "localhost.localdomain"}:
        raise LLMConfigurationError(f"{name} must not target localhost")
    allowed = _allowed_hosts()
    if production and not allowed:
        raise LLMConfigurationError("PLATFORM_LLM_ALLOWED_HOSTS is required in production")
    if allowed and normalized not in allowed:
        raise LLMConfigurationError(
            f"{name} host '{normalized}' is not in PLATFORM_LLM_ALLOWED_HOSTS"
        )
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(normalized, None)}
    except socket.gaierror as exc:
        raise LLMConfigurationError(f"{name} host '{normalized}' could not be resolved") from exc
    if not addresses or any(not _is_public_address(address) for address in addresses):
        raise LLMConfigurationError(f"{name} host must resolve only to public addresses")


def _require_url(
    name: str, value: str, *, production: bool, allow_dashscope: bool = False
) -> str:
    candidate = value.rstrip("/")
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise LLMConfigurationError(f"{name} must be an absolute HTTP(S) URL without userinfo")
    if production and parsed.scheme != "https":
        raise LLMConfigurationError(f"{name} must use HTTPS in production")
    _validate_host(name, parsed.hostname, production=production)
    if not allow_dashscope and "dashscope.aliyuncs.com" in parsed.hostname.lower():
        raise LLMConfigurationError(f"{name} must not point to DashScope")
    return candidate


def _require_nonempty(name: str, value: str) -> str:
    if not value:
        raise LLMConfigurationError(f"{name} is required")
    return value


def _derived_endpoint(chat_url: str, endpoint: str) -> str:
    suffix = "chat/completions"
    if chat_url.endswith(suffix):
        return chat_url[: -len(suffix)] + endpoint
    raise LLMConfigurationError(
        "PLATFORM_LLM_API_URL must end in /chat/completions when a derived endpoint is needed"
    )


@dataclass(frozen=True)
class LLMProfile:
    provider: str
    api_url: str
    api_key: str
    model: str
    responses_api_url: str
    embeddings_api_url: str
    search_model: str
    embedding_model: str


def platform_profile() -> LLMProfile:
    production = is_production()
    api_url = _require_url(
        "PLATFORM_LLM_API_URL", _env("PLATFORM_LLM_API_URL"), production=production
    )
    api_key = _require_nonempty("PLATFORM_LLM_API_KEY", _env("PLATFORM_LLM_API_KEY"))
    model = _require_nonempty("PLATFORM_LLM_MODEL", _env("PLATFORM_LLM_MODEL"))
    responses_url = _env("PLATFORM_LLM_RESPONSES_API_URL") or _derived_endpoint(api_url, "responses")
    embeddings_url = _env("PLATFORM_LLM_EMBEDDINGS_API_URL") or _derived_endpoint(api_url, "embeddings")
    return LLMProfile(
        provider="platform",
        api_url=api_url,
        api_key=api_key,
        model=model,
        responses_api_url=_require_url(
            "PLATFORM_LLM_RESPONSES_API_URL", responses_url, production=production
        ),
        embeddings_api_url=_require_url(
            "PLATFORM_LLM_EMBEDDINGS_API_URL", embeddings_url, production=production
        ),
        search_model=_env("PLATFORM_LLM_SEARCH_MODEL") or model,
        embedding_model=_env("PLATFORM_LLM_EMBEDDING_MODEL") or "text-embedding-v4",
    )


def dashscope_test_profile() -> LLMProfile:
    if is_production():
        raise LLMConfigurationError("DashScope test profile is disabled in production")
    api_url = _require_url(
        "DASHSCOPE_TEST_API_URL",
        _env("DASHSCOPE_TEST_API_URL"),
        production=False,
        allow_dashscope=True,
    )
    api_key = _require_nonempty("DASHSCOPE_TEST_API_KEY", _env("DASHSCOPE_TEST_API_KEY"))
    model = _require_nonempty("DASHSCOPE_TEST_MODEL", _env("DASHSCOPE_TEST_MODEL"))
    return LLMProfile(
        provider="dashscope_test",
        api_url=api_url,
        api_key=api_key,
        model=model,
        responses_api_url=_require_url(
            "DASHSCOPE_TEST_RESPONSES_API_URL",
            _env("DASHSCOPE_TEST_RESPONSES_API_URL") or _derived_endpoint(api_url, "responses"),
            production=False,
            allow_dashscope=True,
        ),
        embeddings_api_url=_require_url(
            "DASHSCOPE_TEST_EMBEDDINGS_API_URL",
            _env("DASHSCOPE_TEST_EMBEDDINGS_API_URL") or _derived_endpoint(api_url, "embeddings"),
            production=False,
            allow_dashscope=True,
        ),
        search_model=_env("DASHSCOPE_TEST_SEARCH_MODEL") or model,
        embedding_model=_env("DASHSCOPE_TEST_EMBEDDING_MODEL") or "text-embedding-v4",
    )
