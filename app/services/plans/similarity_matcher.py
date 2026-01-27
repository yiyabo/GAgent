"""
Similarity Matcher for DAG Node Comparison

提供基于 LLM 的节点相似度匹配功能，用于识别可合并的任务节点。
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
    """相似度匹配器接口"""

    @abstractmethod
    def find_similar_pairs(
        self, nodes: List[DAGNode]
    ) -> List[Tuple[int, int, float]]:
        """
        找出所有相似节点对

        Args:
            nodes: DAG节点列表

        Returns:
            相似节点对列表: [(node_id_1, node_id_2, similarity_score), ...]
            similarity_score 范围 [0, 1]，1表示完全相同
        """
        pass

    @abstractmethod
    def should_merge(self, node1: DAGNode, node2: DAGNode) -> bool:
        """
        判断两个节点是否应该合并

        Args:
            node1: 第一个节点
            node2: 第二个节点

        Returns:
            是否应该合并
        """
        pass


class SimpleSimilarityMatcher(SimilarityMatcher):
    """
    简单的相似度匹配器（基于名称比较）

    用于快速匹配，不依赖 LLM。
    """

    def __init__(self, threshold: float = 0.9):
        self.threshold = threshold

    def find_similar_pairs(
        self, nodes: List[DAGNode]
    ) -> List[Tuple[int, int, float]]:
        """基于名称的简单相似度匹配"""
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
        """基于名称判断是否应合并"""
        similarity = self._compute_similarity(node1, node2)
        return similarity >= self.threshold

    def _compute_similarity(self, node1: DAGNode, node2: DAGNode) -> float:
        """计算两个节点的相似度"""
        name1 = node1.name.strip().lower()
        name2 = node2.name.strip().lower()

        # 完全相同
        if name1 == name2:
            return 1.0

        # 简单的 Jaccard 相似度
        words1 = set(name1.split())
        words2 = set(name2.split())

        if not words1 or not words2:
            return 0.0

        intersection = len(words1 & words2)
        union = len(words1 | words2)

        return intersection / union if union > 0 else 0.0


class LLMSimilarityMatcher(SimilarityMatcher):
    """基于LLM的相似度匹配器"""

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
        """从LLM响应中提取JSON"""
        # 尝试直接解析
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            pass

        # 提取 ```json ... ``` 块
        match = re.search(
            r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", text, re.DOTALL
        )
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        # 提取第一个 {...} 或 [...]
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
        """使用LLM批量找出相似节点对"""
        if len(nodes) < 2:
            return []

        from .prompts.merge_similarity import (
            BATCH_SIMILARITY_SYSTEM,
            BATCH_SIMILARITY_USER,
        )

        # 构建节点描述
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
                            # 安全地转换 id1/id2 为 int
                            raw_id1 = item.get("id1")
                            raw_id2 = item.get("id2")
                            id1 = int(raw_id1) if raw_id1 is not None else None
                            id2 = int(raw_id2) if raw_id2 is not None else None
                            # 安全地转换 similarity，处理 None 值
                            sim_val = item.get("similarity")
                            sim = float(sim_val) if sim_val is not None else 0.0
                            if id1 is not None and id2 is not None and sim >= self.threshold:
                                pairs.append((id1, id2, sim))
                        except (ValueError, TypeError) as e:
                            logger.debug(f"跳过无效相似度条目: {item}, 错误: {e}")
                            continue
                return pairs
        except Exception as e:
            logger.warning(f"LLM相似度检测失败: {e}")

        return []

    def should_merge(self, node1: DAGNode, node2: DAGNode) -> bool:
        """使用LLM判断两个节点是否应合并"""
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
                instruction1=node1.instruction or "(无)",
                id2=node2.id,
                name2=node2.name,
                instruction2=node2.instruction or "(无)",
            )
        )

        try:
            response = self.llm.chat(prompt)
            result = self._parse_json(response)

            if isinstance(result, dict):
                can_merge = result.get("can_merge", False)
                # 安全地转换 similarity，处理 None 值
                sim_val = result.get("similarity")
                similarity = float(sim_val) if sim_val is not None else 0.0
                reason = result.get("reason", "")

                if can_merge and similarity >= self.threshold:
                    logger.info(
                        f"LLM判定可合并 [{node1.id}]+[{node2.id}]: {reason}"
                    )
                    return True
                else:
                    logger.debug(
                        f"LLM判定不合并 [{node1.id}]+[{node2.id}]: {reason}"
                    )
        except Exception as e:
            logger.warning(f"LLM判断失败: {e}")

        return False


class CachedSimilarityMatcher(SimilarityMatcher):
    """
    带缓存的相似度匹配器

    包装另一个匹配器，缓存结果以减少 LLM 调用。
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
        """代理访问底层匹配器的阈值"""
        return getattr(self.matcher, "threshold", 0.8)

    @threshold.setter
    def threshold(self, value: float) -> None:
        """设置底层匹配器的阈值"""
        if hasattr(self.matcher, "threshold"):
            self.matcher.threshold = value

    def _cache_key(self, id1: int, id2: int) -> Tuple[int, int]:
        """生成缓存键（保证顺序一致）"""
        return (min(id1, id2), max(id1, id2))

    def find_similar_pairs(
        self, nodes: List[DAGNode]
    ) -> List[Tuple[int, int, float]]:
        """使用缓存的相似度匹配"""
        # 检查缓存中是否有所有节点对
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

        # 如果有未缓存的节点对，调用底层匹配器
        if uncached_nodes:
            new_pairs = self.matcher.find_similar_pairs(nodes)
            for id1, id2, sim in new_pairs:
                key = self._cache_key(id1, id2)
                self._pair_cache[key] = sim
                if key not in [self._cache_key(p[0], p[1]) for p in cached_pairs]:
                    cached_pairs.append((id1, id2, sim))

        # 限制缓存大小
        if len(self._pair_cache) > self.cache_size:
            # 简单的 FIFO 淘汰
            keys_to_remove = list(self._pair_cache.keys())[
                : len(self._pair_cache) - self.cache_size
            ]
            for key in keys_to_remove:
                del self._pair_cache[key]

        return cached_pairs

    def should_merge(self, node1: DAGNode, node2: DAGNode) -> bool:
        """使用缓存的合并判断"""
        key = self._cache_key(node1.id, node2.id)

        if key in self._merge_cache:
            return self._merge_cache[key]

        result = self.matcher.should_merge(node1, node2)
        self._merge_cache[key] = result

        # 限制缓存大小
        if len(self._merge_cache) > self.cache_size:
            keys_to_remove = list(self._merge_cache.keys())[
                : len(self._merge_cache) - self.cache_size
            ]
            for key in keys_to_remove:
                del self._merge_cache[key]

        return result
