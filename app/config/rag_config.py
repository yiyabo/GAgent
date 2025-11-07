"""
Graph RAG 配置

集中管理 Graph RAG 模块的路径、缓存参数等设置。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional


@dataclass(slots=True)
class GraphRAGSettings:
    """Graph RAG 模块配置"""

    triples_path: str
    cache_ttl: int = 900
    max_top_k: int = 20
    max_hops: int = 2


def _env(key: str, default: Optional[str] = None) -> Optional[str]:
    value = os.getenv(key)
    if value is None:
        return default
    stripped = value.strip()
    return stripped or default


@lru_cache(maxsize=1)
def get_graph_rag_settings() -> GraphRAGSettings:
    """读取环境变量并返回 Graph RAG 设置"""

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


def reset_graph_rag_settings_cache() -> None:
    """测试场景下重置缓存"""

    get_graph_rag_settings.cache_clear()  # type: ignore[attr-defined]
