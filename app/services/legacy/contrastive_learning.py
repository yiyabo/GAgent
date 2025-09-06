"""
结构对比学习训练数据构造模块。

基于任务图的结构关系生成对比学习训练数据，用于微调embedding模型
以更好地理解和表示任务间的结构关系。

Author: Cascade AI
Date: 2025-08-21
"""

import json
import logging
import random
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

from app.repository.tasks import default_repo
from app.services.config import get_config

logger = logging.getLogger(__name__)


@dataclass
class ContrastiveSample:
    """对比学习样本数据结构"""

    anchor_id: int
    anchor_text: str
    positive_id: int
    positive_text: str
    negative_id: int
    negative_text: str
    relation_type: str
    similarity_score: float


class StructureContrastiveLearningDataGenerator:
    """
    结构对比学习训练数据生成器

    基于任务图的结构关系生成三元组对比学习数据：
    - Anchor: 查询任务
    - Positive: 与查询任务有强结构关系的任务
    - Negative: 与查询任务结构关系较弱或无关的任务
    """

    def __init__(self):
        """初始化数据生成器"""
        self.config = get_config()
        self.repository = default_repo

        # 关系权重配置
        self.relation_weights = {
            "requires": 0.9,  # 强依赖关系
            "refers": 0.7,  # 引用关系
            "parent": 0.8,  # 父子关系
            "child": 0.8,  # 子父关系
            "sibling": 0.6,  # 兄弟关系
            "neighbor": 0.4,  # 邻居关系
            "distant": 0.1,  # 远距离关系
        }

        # 生成参数
        self.min_positive_score = 0.5
        self.max_negative_score = 0.3
        self.samples_per_task = 3

        logger.info("结构对比学习数据生成器初始化完成")

    def generate_training_data(
        self, task_ids: Optional[List[int]] = None, max_samples: int = 1000
    ) -> List[ContrastiveSample]:
        """
        生成结构对比学习训练数据

        Args:
            task_ids: 指定任务ID列表，None表示使用所有任务
            max_samples: 最大样本数量

        Returns:
            对比学习样本列表
        """
        logger.info(f"开始生成结构对比学习训练数据，最大样本数: {max_samples}")

        # 获取任务数据
        if task_ids is None:
            all_tasks = self.repository.list_all_tasks()
            task_ids = [task["id"] for task in all_tasks if task.get("id")]

        if not task_ids:
            logger.warning("没有找到可用的任务数据")
            return []

        # 构建任务图
        task_graph = self._build_task_graph(task_ids)
        task_texts = self._get_task_texts(task_ids)

        samples = []
        processed_count = 0

        for anchor_id in task_ids:
            if processed_count >= max_samples:
                break

            if anchor_id not in task_texts:
                continue

            # 为每个anchor任务生成多个样本
            task_samples = self._generate_samples_for_task(anchor_id, task_graph, task_texts)

            samples.extend(task_samples)
            processed_count += len(task_samples)

            if processed_count % 100 == 0:
                logger.debug(f"已处理 {processed_count} 个样本")

        # 限制样本数量并打乱顺序
        if len(samples) > max_samples:
            samples = random.sample(samples, max_samples)

        random.shuffle(samples)

        logger.info(f"生成完成，共 {len(samples)} 个对比学习样本")
        return samples

    def _generate_samples_for_task(
        self, anchor_id: int, task_graph: Dict, task_texts: Dict[int, str]
    ) -> List[ContrastiveSample]:
        """为单个任务生成对比学习样本"""
        samples = []
        anchor_text = task_texts.get(anchor_id, "")

        if not anchor_text:
            return samples

        # 计算与其他任务的结构关系强度
        relation_scores = self._compute_relation_scores(anchor_id, task_graph)

        # 分离正样本和负样本候选
        positive_candidates = []
        negative_candidates = []

        for task_id, score in relation_scores.items():
            if task_id == anchor_id or task_id not in task_texts:
                continue

            if score >= self.min_positive_score:
                positive_candidates.append((task_id, score))
            elif score <= self.max_negative_score:
                negative_candidates.append((task_id, score))

        # 按分数排序
        positive_candidates.sort(key=lambda x: x[1], reverse=True)
        negative_candidates.sort(key=lambda x: x[1])

        # 生成样本
        for i in range(min(self.samples_per_task, len(positive_candidates))):
            if i >= len(negative_candidates):
                break

            positive_id, pos_score = positive_candidates[i]
            negative_id, neg_score = negative_candidates[i]

            # 确定关系类型
            relation_type = self._determine_relation_type(anchor_id, positive_id, task_graph)

            sample = ContrastiveSample(
                anchor_id=anchor_id,
                anchor_text=anchor_text,
                positive_id=positive_id,
                positive_text=task_texts[positive_id],
                negative_id=negative_id,
                negative_text=task_texts[negative_id],
                relation_type=relation_type,
                similarity_score=pos_score,
            )

            samples.append(sample)

        return samples

    def _build_task_graph(self, task_ids: List[int]) -> Dict[int, Dict]:
        """构建任务图结构"""
        task_graph = {}

        for task_id in task_ids:
            try:
                task_info = self.repository.get_task_info(task_id)
                if not task_info:
                    continue

                task_graph[task_id] = {
                    "requires": task_info.get("requires", []),
                    "refers": task_info.get("refers", []),
                    "parent_id": task_info.get("parent_id"),
                    "children": [],
                    "title": task_info.get("title", ""),
                    "description": task_info.get("description", ""),
                }
            except Exception as e:
                logger.warning(f"获取任务 {task_id} 信息失败: {e}")
                continue

        # 构建父子关系
        for task_id, info in task_graph.items():
            parent_id = info.get("parent_id")
            if parent_id and parent_id in task_graph:
                task_graph[parent_id]["children"].append(task_id)

        return task_graph

    def _get_task_texts(self, task_ids: List[int]) -> Dict[int, str]:
        """获取任务文本内容"""
        task_texts = {}

        for task_id in task_ids:
            try:
                task_info = self.repository.get_task_info(task_id)
                if task_info:
                    # 组合标题和描述作为文本内容
                    title = task_info.get("title", "")
                    description = task_info.get("description", "")
                    text = f"{title}\n{description}".strip()
                    if text:
                        task_texts[task_id] = text
            except Exception as e:
                logger.warning(f"获取任务 {task_id} 文本失败: {e}")
                continue

        return task_texts

    def _compute_relation_scores(self, anchor_id: int, task_graph: Dict) -> Dict[int, float]:
        """计算anchor任务与其他任务的结构关系分数"""
        scores = {}
        anchor_info = task_graph.get(anchor_id, {})

        for task_id, task_info in task_graph.items():
            if task_id == anchor_id:
                continue

            score = 0.0

            # 依赖关系
            if task_id in anchor_info.get("requires", []):
                score += self.relation_weights["requires"]
            if anchor_id in task_info.get("requires", []):
                score += self.relation_weights["requires"]

            # 引用关系
            if task_id in anchor_info.get("refers", []):
                score += self.relation_weights["refers"]
            if anchor_id in task_info.get("refers", []):
                score += self.relation_weights["refers"]

            # 父子关系
            if task_id == anchor_info.get("parent_id"):
                score += self.relation_weights["parent"]
            if anchor_id == task_info.get("parent_id"):
                score += self.relation_weights["child"]

            # 兄弟关系
            if anchor_info.get("parent_id") and anchor_info.get("parent_id") == task_info.get("parent_id"):
                score += self.relation_weights["sibling"]

            # 邻居关系（共同依赖或引用）
            anchor_deps = set(anchor_info.get("requires", []) + anchor_info.get("refers", []))
            task_deps = set(task_info.get("requires", []) + task_info.get("refers", []))
            common_deps = len(anchor_deps & task_deps)
            if common_deps > 0:
                score += self.relation_weights["neighbor"] * min(common_deps / 3.0, 1.0)

            # 路径距离（BFS）
            distance = self._compute_path_distance(anchor_id, task_id, task_graph)
            if distance > 0:
                distance_score = max(0, 1.0 - (distance - 1) * 0.2)
                score += self.relation_weights["distant"] * distance_score

            scores[task_id] = min(score, 1.0)  # 限制最大分数为1.0

        return scores

    def _compute_path_distance(self, start_id: int, target_id: int, task_graph: Dict) -> int:
        """计算两个任务间的最短路径距离"""
        if start_id == target_id:
            return 0

        visited = set()
        queue = deque([(start_id, 0)])

        while queue:
            current_id, distance = queue.popleft()

            if current_id in visited:
                continue
            visited.add(current_id)

            if current_id == target_id:
                return distance

            # 探索相邻节点
            current_info = task_graph.get(current_id, {})
            neighbors = []

            # 添加依赖和引用的任务
            neighbors.extend(current_info.get("requires", []))
            neighbors.extend(current_info.get("refers", []))

            # 添加父任务和子任务
            if current_info.get("parent_id"):
                neighbors.append(current_info["parent_id"])
            neighbors.extend(current_info.get("children", []))

            for neighbor_id in neighbors:
                if neighbor_id not in visited and neighbor_id in task_graph:
                    queue.append((neighbor_id, distance + 1))

        return -1  # 无法到达

    def _determine_relation_type(self, anchor_id: int, positive_id: int, task_graph: Dict) -> str:
        """确定两个任务间的主要关系类型"""
        anchor_info = task_graph.get(anchor_id, {})
        positive_info = task_graph.get(positive_id, {})

        # 检查各种关系类型
        if positive_id in anchor_info.get("requires", []):
            return "requires"
        if positive_id in anchor_info.get("refers", []):
            return "refers"
        if positive_id == anchor_info.get("parent_id"):
            return "parent"
        if anchor_id == positive_info.get("parent_id"):
            return "child"
        if anchor_info.get("parent_id") and anchor_info.get("parent_id") == positive_info.get("parent_id"):
            return "sibling"

        return "neighbor"

    def export_training_data(self, samples: List[ContrastiveSample], output_path: str, format: str = "json") -> None:
        """
        导出训练数据到文件

        Args:
            samples: 对比学习样本列表
            output_path: 输出文件路径
            format: 输出格式 ('json', 'jsonl', 'csv')
        """
        logger.info(f"导出 {len(samples)} 个样本到 {output_path}")

        if format == "json":
            self._export_json(samples, output_path)
        elif format == "jsonl":
            self._export_jsonl(samples, output_path)
        elif format == "csv":
            self._export_csv(samples, output_path)
        else:
            raise ValueError(f"不支持的导出格式: {format}")

        logger.info(f"数据导出完成: {output_path}")

    def _export_json(self, samples: List[ContrastiveSample], output_path: str) -> None:
        """导出为JSON格式"""
        data = []
        for sample in samples:
            data.append(
                {
                    "anchor_id": sample.anchor_id,
                    "anchor_text": sample.anchor_text,
                    "positive_id": sample.positive_id,
                    "positive_text": sample.positive_text,
                    "negative_id": sample.negative_id,
                    "negative_text": sample.negative_text,
                    "relation_type": sample.relation_type,
                    "similarity_score": sample.similarity_score,
                }
            )

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _export_jsonl(self, samples: List[ContrastiveSample], output_path: str) -> None:
        """导出为JSONL格式"""
        with open(output_path, "w", encoding="utf-8") as f:
            for sample in samples:
                data = {
                    "anchor_id": sample.anchor_id,
                    "anchor_text": sample.anchor_text,
                    "positive_id": sample.positive_id,
                    "positive_text": sample.positive_text,
                    "negative_id": sample.negative_id,
                    "negative_text": sample.negative_text,
                    "relation_type": sample.relation_type,
                    "similarity_score": sample.similarity_score,
                }
                f.write(json.dumps(data, ensure_ascii=False) + "\n")

    def _export_csv(self, samples: List[ContrastiveSample], output_path: str) -> None:
        """导出为CSV格式"""
        import csv

        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)

            # 写入表头
            writer.writerow(
                [
                    "anchor_id",
                    "anchor_text",
                    "positive_id",
                    "positive_text",
                    "negative_id",
                    "negative_text",
                    "relation_type",
                    "similarity_score",
                ]
            )

            # 写入数据
            for sample in samples:
                writer.writerow(
                    [
                        sample.anchor_id,
                        sample.anchor_text,
                        sample.positive_id,
                        sample.positive_text,
                        sample.negative_id,
                        sample.negative_text,
                        sample.relation_type,
                        sample.similarity_score,
                    ]
                )

    def get_statistics(self, samples: List[ContrastiveSample]) -> Dict[str, Any]:
        """获取训练数据统计信息"""
        if not samples:
            return {}

        relation_counts = defaultdict(int)
        similarity_scores = []

        for sample in samples:
            relation_counts[sample.relation_type] += 1
            similarity_scores.append(sample.similarity_score)

        stats = {
            "total_samples": len(samples),
            "relation_distribution": dict(relation_counts),
            "similarity_stats": {
                "mean": sum(similarity_scores) / len(similarity_scores),
                "min": min(similarity_scores),
                "max": max(similarity_scores),
            },
            "unique_anchors": len(set(s.anchor_id for s in samples)),
            "unique_positives": len(set(s.positive_id for s in samples)),
            "unique_negatives": len(set(s.negative_id for s in samples)),
        }

        return stats


# 全局单例实例
_contrastive_generator = None


def get_contrastive_learning_generator() -> StructureContrastiveLearningDataGenerator:
    """获取结构对比学习数据生成器单例"""
    global _contrastive_generator
    if _contrastive_generator is None:
        _contrastive_generator = StructureContrastiveLearningDataGenerator()
    return _contrastive_generator
