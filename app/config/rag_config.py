"""
Graph RAG / LightRAG gateway configuration.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional


@dataclass(slots=True)
class GraphRAGSettings:
    """Legacy local triples Graph RAG configuration."""

    triples_path: str
    cache_ttl: int = 900
    max_top_k: int = 20
    max_hops: int = 2


@dataclass(slots=True)
class LightRAGGatewaySettings:
    """HTTP client settings for the external LightRAG 8-shard query gateway."""

    enabled: bool = True
    base_url: str = "http://127.0.0.1:9660"
    api_key: str = ""
    timeout_seconds: float = 120.0
    default_mode: str = "mix"
    default_top_k: int = 5
    default_max_chunks: int = 12
    default_max_references: int = 12


def _env(key: str, default: Optional[str] = None) -> Optional[str]:
    value = os.getenv(key)
    if value is None:
        return default
    stripped = value.strip()
    return stripped or default


def _env_bool(key: str, default: bool) -> bool:
    raw = _env(key)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _env_float(key: str, default: float) -> float:
    raw = _env(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(key: str, default: int) -> int:
    raw = _env(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@lru_cache(maxsize=1)
def get_graph_rag_settings() -> GraphRAGSettings:
    """Read legacy local Graph RAG settings."""

    root_dir = os.getenv("GRAPH_RAG_ROOT_DIR")
    default_path = os.path.join(
        root_dir or os.path.dirname(__file__),
        "..",
        "..",
        "tool_box",
        "tools_impl",
        "graph_rag",
        "Triples",
        "all_triples.csv",
    )
    default_path = os.path.abspath(default_path)

    triples_path = _env("GRAPH_RAG_TRIPLES_PATH", default_path)

    try:
        cache_ttl = int(_env("GRAPH_RAG_CACHE_TTL", "900") or "900")
    except ValueError:
        cache_ttl = 900

    try:
        max_top_k = int(_env("GRAPH_RAG_MAX_TOP_K", "20") or "20")
    except ValueError:
        max_top_k = 20

    try:
        max_hops = int(_env("GRAPH_RAG_MAX_HOPS", "2") or "2")
    except ValueError:
        max_hops = 2

    return GraphRAGSettings(
        triples_path=triples_path,
        cache_ttl=max(cache_ttl, 0),
        max_top_k=max(max_top_k, 1),
        max_hops=max(max_hops, 0),
    )


@lru_cache(maxsize=1)
def get_lightrag_gateway_settings() -> LightRAGGatewaySettings:
    """Read LightRAG gateway client settings."""

    return LightRAGGatewaySettings(
        enabled=_env_bool("LIGHTRAG_GATEWAY_ENABLED", True),
        base_url=(_env("LIGHTRAG_GATEWAY_URL", "http://127.0.0.1:9660") or "").rstrip("/"),
        api_key=_env("LIGHTRAG_GATEWAY_API_KEY", "") or "",
        timeout_seconds=max(5.0, _env_float("LIGHTRAG_GATEWAY_TIMEOUT", 120.0)),
        default_mode=_env("LIGHTRAG_GATEWAY_DEFAULT_MODE", "mix") or "mix",
        default_top_k=max(1, _env_int("LIGHTRAG_GATEWAY_DEFAULT_TOP_K", 5)),
        default_max_chunks=max(1, _env_int("LIGHTRAG_GATEWAY_DEFAULT_MAX_CHUNKS", 12)),
        default_max_references=max(
            1, _env_int("LIGHTRAG_GATEWAY_DEFAULT_MAX_REFERENCES", 12)
        ),
    )


def reset_graph_rag_settings_cache() -> None:
    """Clear cached Graph RAG settings."""

    get_graph_rag_settings.cache_clear()  # type: ignore[attr-defined]


def reset_lightrag_gateway_settings_cache() -> None:
    """Clear cached LightRAG gateway settings."""

    get_lightrag_gateway_settings.cache_clear()  # type: ignore[attr-defined]
