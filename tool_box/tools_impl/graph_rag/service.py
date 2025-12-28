from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence

from app.config import GraphRAGSettings

from .exceptions import GraphRAGError
from .graph_rag import GraphRAG

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _ServiceState:
    rag: GraphRAG
    triples_path: str


_SERVICE_STATE: Optional[_ServiceState] = None
_SERVICE_LOCK = asyncio.Lock()


def _validate_triples_path(path: str) -> None:
    if not os.path.exists(path):
        raise GraphRAGError(
            f"Knowledge graph file does not exist: {path}",
            code="missing_triples",
        )
    if not os.path.isfile(path):
        raise GraphRAGError(
            f"Specified triples path is not a file: {path}",
            code="invalid_triples_path",
        )


async def get_graph_rag_service(settings: GraphRAGSettings) -> GraphRAG:
    """
    Get GraphRAG instance (singleton).

    Automatically reloads when triples_path in config changes.
    """
    global _SERVICE_STATE

    async with _SERVICE_LOCK:
        if _SERVICE_STATE is not None:
            try:
                if os.path.samefile(
                    _SERVICE_STATE.triples_path, settings.triples_path
                ):
                    return _SERVICE_STATE.rag
            except FileNotFoundError:
                # If new path doesn't exist, will error in _validate_triples_path
                pass

        _validate_triples_path(settings.triples_path)
        logger.info("Loading GraphRAG triples: %s", settings.triples_path)

        try:
            rag = GraphRAG(settings.triples_path)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Failed to initialize GraphRAG: %s", exc)
            raise GraphRAGError(
                f"Failed to load knowledge graph: {exc}",
                code="initialisation_failed",
            ) from exc

        _SERVICE_STATE = _ServiceState(rag=rag, triples_path=settings.triples_path)
        return rag


def _prioritise_triples(
    triples: List[Dict[str, Any]],
    focus_entities: Optional[Sequence[str]],
) -> List[Dict[str, Any]]:
    if not triples:
        return triples
    if not focus_entities:
        return triples

    focus_tokens = {
        str(entity).strip().lower()
        for entity in focus_entities
        if isinstance(entity, str) and entity.strip()
    }
    if not focus_tokens:
        return triples

    def _score(triple: Dict[str, Any]) -> int:
        score = 0
        for key in ("entity1", "entity2"):
            value = str(triple.get(key) or "").strip().lower()
            if not value:
                continue
            if value in focus_tokens:
                score += 2
            elif any(token in value for token in focus_tokens):
                score += 1
        return -score  # sort ascending but using negative to prioritise higher score

    return sorted(triples, key=_score)


def _clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(value, maximum))


async def query_graph_rag(
    *,
    rag: GraphRAG,
    query: str,
    top_k: int,
    hops: int,
    return_subgraph: bool,
    focus_entities: Optional[Sequence[str]],
) -> Dict[str, Any]:
    try:
        result = rag.query(
            query,
            top_k=top_k,
            hops=hops,
            return_subgraph=return_subgraph,
        )
    except Exception as exc:
        logger.exception("GraphRAG query failed: %s", exc)
        raise GraphRAGError(str(exc), code="query_failed") from exc

    triples = _prioritise_triples(result.get("triples") or [], focus_entities)

    payload: Dict[str, Any] = {
        "query": query,
        "triples": triples,
        "prompt": result.get("prompt"),
        "metadata": {
            "top_k": top_k,
            "hops": hops,
            "triple_count": len(triples),
            "has_subgraph": bool(result.get("subgraph")),
            "focus_entities": [
                entity for entity in (focus_entities or []) if isinstance(entity, str)
            ],
        },
    }

    if return_subgraph and "subgraph" in result:
        payload["subgraph"] = result["subgraph"]

    return payload


def reset_graph_rag_service() -> None:
    """Reset GraphRAG singleton for testing scenarios."""

    global _SERVICE_STATE
    _SERVICE_STATE = None
