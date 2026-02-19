"""
Similarity Matcher for DAG Node Comparison

LLM , task. 
"""

from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from typing import Any, List, Optional, Tuple

from .dag_models import DAGNode

logger = logging.getLogger(__name__)


class SimilarityMatcher(ABC):
    """"""

    @abstractmethod
    def find_similar_pairs(
        self, nodes: List[DAGNode]
    ) -> List[Tuple[int, int, float]]:
        """


        Args:
            nodes: DAG

        Returns:
            : [(node_id_1, node_id_2, similarity_score), ...]
            similarity_score  [0, 1], 1
        """
        pass

    @abstractmethod
    def should_merge(self, node1: DAGNode, node2: DAGNode) -> bool:
        """


        Args:
            node1: 
            node2: 

        Returns:

        """
        pass


class SimpleSimilarityMatcher(SimilarityMatcher):
    """
    (name)

    ,  LLM. 
    """

    def __init__(self, threshold: float = 0.9):
        self.threshold = threshold

    def find_similar_pairs(
        self, nodes: List[DAGNode]
    ) -> List[Tuple[int, int, float]]:
        """name"""
        if len(nodes) < 2:
            return []

        pairs = []
        for i in range(len(nodes)):
            for j in range(i + 1, len(nodes)):
                node1, node2 = nodes[i], nodes[j]
                similarity = self._compute_similarity(node1, node2)
                if similarity >= self.threshold:
                    pairs.append((node1.id, node2.id, similarity))

        return pairs

    def should_merge(self, node1: DAGNode, node2: DAGNode) -> bool:
        """name"""
        similarity = self._compute_similarity(node1, node2)
        return similarity >= self.threshold

    def _compute_similarity(self, node1: DAGNode, node2: DAGNode) -> float:
        """"""
        name1 = node1.name.strip().lower()
        name2 = node2.name.strip().lower()

        if name1 == name2:
            return 1.0

        words1 = set(name1.split())
        words2 = set(name2.split())

        if not words1 or not words2:
            return 0.0

        intersection = len(words1 & words2)
        union = len(words1 | words2)

        return intersection / union if union > 0 else 0.0


class LLMSimilarityMatcher(SimilarityMatcher):
    """LLM"""

    def __init__(self, threshold: float = 0.8):
        self.threshold = threshold
        self._llm = None

    @property
    def llm(self):
        if self._llm is None:
            from ...llm import get_default_client
            self._llm = get_default_client()
        return self._llm

    def _parse_json(self, text: str) -> Any:
        """LLMmediumJSON"""
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            pass

        match = re.search(
            r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", text, re.DOTALL
        )
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        match = re.search(r"(\{[^{}]*\}|\[[^\[\]]*\])", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        return None

    def find_similar_pairs(
        self, nodes: List[DAGNode]
    ) -> List[Tuple[int, int, float]]:
        """LLM"""
        if len(nodes) < 2:
            return []

        from .prompts.merge_similarity import (
            BATCH_SIMILARITY_SYSTEM,
            BATCH_SIMILARITY_USER,
        )

        nodes_lines = []
        for node in nodes:
            instr = (node.instruction or "")[:100]
            nodes_lines.append(f"[{node.id}] {node.name}: {instr}")
        nodes_text = "\n".join(nodes_lines)

        prompt = f"{BATCH_SIMILARITY_SYSTEM}\n\n{BATCH_SIMILARITY_USER.format(nodes_text=nodes_text)}"

        try:
            response = self.llm.chat(prompt)
            result = self._parse_json(response)

            if isinstance(result, list):
                pairs = []
                for item in result:
                    if isinstance(item, dict):
                        try:
                            raw_id1 = item.get("id1")
                            raw_id2 = item.get("id2")
                            id1 = int(raw_id1) if raw_id1 is not None else None
                            id2 = int(raw_id2) if raw_id2 is not None else None
                            sim_val = item.get("similarity")
                            sim = float(sim_val) if sim_val is not None else 0.0
                            if id1 is not None and id2 is not None and sim >= self.threshold:
                                pairs.append((id1, id2, sim))
                        except (ValueError, TypeError) as e:
                            logger.debug(f": {item}, error: {e}")
                            continue
                return pairs
        except Exception as e:
            logger.warning(f"LLMfailed: {e}")

        return []

    def should_merge(self, node1: DAGNode, node2: DAGNode) -> bool:
        """LLM"""
        from .prompts.merge_similarity import (
            MERGE_SIMILARITY_SYSTEM,
            MERGE_SIMILARITY_USER,
        )

        prompt = (
            MERGE_SIMILARITY_SYSTEM
            + "\n\n"
            + MERGE_SIMILARITY_USER.format(
                id1=node1.id,
                name1=node1.name,
                instruction1=node1.instruction or "()",
                id2=node2.id,
                name2=node2.name,
                instruction2=node2.instruction or "()",
            )
        )

        try:
            response = self.llm.chat(prompt)
            result = self._parse_json(response)

            if isinstance(result, dict):
                can_merge = result.get("can_merge", False)
                sim_val = result.get("similarity")
                similarity = float(sim_val) if sim_val is not None else 0.0
                reason = result.get("reason", "")

                if can_merge and similarity >= self.threshold:
                    logger.info(
                        f"LLM [{node1.id}]+[{node2.id}]: {reason}"
                    )
                    return True
                else:
                    logger.debug(
                        f"LLM [{node1.id}]+[{node2.id}]: {reason}"
                    )
        except Exception as e:
            logger.warning(f"LLMfailed: {e}")

        return False


class CachedSimilarityMatcher(SimilarityMatcher):
    """


    , result LLM . 
    """

    def __init__(
        self,
        matcher: SimilarityMatcher,
        cache_size: int = 1000,
    ):
        self.matcher = matcher
        self.cache_size = cache_size
        self._pair_cache: dict[Tuple[int, int], float] = {}
        self._merge_cache: dict[Tuple[int, int], bool] = {}

    @property
    def threshold(self) -> float:
        """"""
        return getattr(self.matcher, "threshold", 0.8)

    @threshold.setter
    def threshold(self, value: float) -> None:
        """"""
        if hasattr(self.matcher, "threshold"):
            self.matcher.threshold = value

    def _cache_key(self, id1: int, id2: int) -> Tuple[int, int]:
        """()"""
        return (min(id1, id2), max(id1, id2))

    def find_similar_pairs(
        self, nodes: List[DAGNode]
    ) -> List[Tuple[int, int, float]]:
        """"""
        uncached_nodes = []
        cached_pairs = []

        node_ids = [n.id for n in nodes]
        for i in range(len(node_ids)):
            for j in range(i + 1, len(node_ids)):
                key = self._cache_key(node_ids[i], node_ids[j])
                if key in self._pair_cache:
                    sim = self._pair_cache[key]
                    if sim >= getattr(self.matcher, "threshold", 0.8):
                        cached_pairs.append((key[0], key[1], sim))
                else:
                    uncached_nodes.append(key)

        if uncached_nodes:
            new_pairs = self.matcher.find_similar_pairs(nodes)
            for id1, id2, sim in new_pairs:
                key = self._cache_key(id1, id2)
                self._pair_cache[key] = sim
                if key not in [self._cache_key(p[0], p[1]) for p in cached_pairs]:
                    cached_pairs.append((id1, id2, sim))

        if len(self._pair_cache) > self.cache_size:
            keys_to_remove = list(self._pair_cache.keys())[
                : len(self._pair_cache) - self.cache_size
            ]
            for key in keys_to_remove:
                del self._pair_cache[key]

        return cached_pairs

    def should_merge(self, node1: DAGNode, node2: DAGNode) -> bool:
        """"""
        key = self._cache_key(node1.id, node2.id)

        if key in self._merge_cache:
            return self._merge_cache[key]

        result = self.matcher.should_merge(node1, node2)
        self._merge_cache[key] = result

        if len(self._merge_cache) > self.cache_size:
            keys_to_remove = list(self._merge_cache.keys())[
                : len(self._merge_cache) - self.cache_size
            ]
            for key in keys_to_remove:
                del self._merge_cache[key]

        return result
