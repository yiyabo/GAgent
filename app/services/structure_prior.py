#!/usr/bin/env python3
"""
结构先验权重计算模块

基于任务图关系计算结构先验权重，用于增强语义检索的准确性。
通过分析任务之间的依赖关系（requires、refers等），为检索结果
提供结构化的权重调整，使得相关性更高的任务获得更高的权重。
"""

import logging
from typing import Dict, List, Optional, Set, Tuple, Any
from collections import defaultdict, deque
import numpy as np

from ..repository.tasks import SqliteTaskRepository

logger = logging.getLogger(__name__)


class StructurePriorCalculator:
    """结构先验权重计算器"""
    
    def __init__(self, repo: Optional[SqliteTaskRepository] = None):
        self.repo = repo or SqliteTaskRepository()
        
        # 权重配置
        self.weights = {
            'requires': 0.8,      # 强依赖关系权重
            'refers': 0.4,        # 弱引用关系权重
            'sibling': 0.3,       # 兄弟节点权重
            'parent': 0.5,        # 父节点权重
            'child': 0.6,         # 子节点权重
            'distance_decay': 0.1 # 距离衰减因子
        }
        
        # 缓存
        self._graph_cache = {}
        self._weights_cache = {}
    
    def compute_structure_weights(self, query_task_id: int, 
                                candidate_task_ids: List[int]) -> Dict[int, float]:
        """
        计算查询任务与候选任务之间的结构先验权重
        
        Args:
            query_task_id: 查询任务ID
            candidate_task_ids: 候选任务ID列表
            
        Returns:
            任务ID到权重的映射字典
        """
        if not candidate_task_ids:
            return {}
        
        # 构建任务图
        task_graph = self._build_task_graph([query_task_id] + candidate_task_ids)
        
        # 计算各种关系权重
        weights = {}
        
        for candidate_id in candidate_task_ids:
            if candidate_id == query_task_id:
                weights[candidate_id] = 1.0  # 自身权重最高
                continue
            
            # 计算综合权重
            total_weight = self._calculate_relationship_weight(
                query_task_id, candidate_id, task_graph
            )
            
            weights[candidate_id] = max(0.0, min(1.0, total_weight))
        
        logger.debug(f"Computed structure weights for query {query_task_id}: {weights}")
        return weights
    
    def _build_task_graph(self, task_ids: List[int]) -> Dict[str, Any]:
        """构建任务图数据结构"""
        cache_key = tuple(sorted(task_ids))
        if cache_key in self._graph_cache:
            return self._graph_cache[cache_key]
        
        # 获取任务基本信息
        tasks = {}
        for task_id in task_ids:
            try:
                task = self.repo.get_task_info(task_id)
                if task:
                    tasks[task_id] = task
            except Exception as e:
                logger.warning(f"Failed to get task {task_id}: {e}")
        
        # 构建依赖关系图
        dependencies = defaultdict(list)  # from_id -> [(to_id, kind)]
        reverse_deps = defaultdict(list)  # to_id -> [(from_id, kind)]
        
        for task_id in task_ids:
            try:
                # 获取该任务的依赖关系
                deps = self.repo.list_dependencies(task_id)
                for dep in deps:
                    dep_id = dep['id']
                    kind = dep['kind']
                    if dep_id in task_ids:  # 只考虑候选任务范围内的依赖
                        dependencies[dep_id].append((task_id, kind))
                        reverse_deps[task_id].append((dep_id, kind))
            except Exception as e:
                logger.warning(f"Failed to get dependencies for task {task_id}: {e}")
        
        # 构建层次关系
        hierarchy = self._build_hierarchy_relations(tasks)
        
        graph = {
            'tasks': tasks,
            'dependencies': dict(dependencies),
            'reverse_deps': dict(reverse_deps),
            'hierarchy': hierarchy
        }
        
        self._graph_cache[cache_key] = graph
        return graph
    
    def _build_hierarchy_relations(self, tasks: Dict[int, Dict]) -> Dict[str, Dict[int, List[int]]]:
        """构建层次关系（父子、兄弟）"""
        parents = defaultdict(list)  # parent_id -> [child_ids]
        children = defaultdict(list)  # child_id -> [parent_id]
        siblings = defaultdict(list)  # task_id -> [sibling_ids]
        
        # 按parent_id分组
        by_parent = defaultdict(list)
        for task_id, task in tasks.items():
            parent_id = task.get('parent_id')
            if parent_id:
                by_parent[parent_id].append(task_id)
                parents[parent_id].append(task_id)
                children[task_id].append(parent_id)
        
        # 构建兄弟关系
        for parent_id, child_ids in by_parent.items():
            if len(child_ids) > 1:
                for child_id in child_ids:
                    siblings[child_id] = [cid for cid in child_ids if cid != child_id]
        
        return {
            'parents': dict(parents),
            'children': dict(children),
            'siblings': dict(siblings)
        }
    
    def _calculate_relationship_weight(self, query_id: int, candidate_id: int, 
                                     graph: Dict[str, Any]) -> float:
        """计算两个任务之间的关系权重"""
        total_weight = 0.0
        
        # 1. 直接依赖关系权重
        dep_weight = self._calculate_dependency_weight(query_id, candidate_id, graph)
        total_weight += dep_weight
        
        # 2. 层次关系权重
        hierarchy_weight = self._calculate_hierarchy_weight(query_id, candidate_id, graph)
        total_weight += hierarchy_weight
        
        # 3. 路径距离权重
        distance_weight = self._calculate_distance_weight(query_id, candidate_id, graph)
        total_weight += distance_weight
        
        # 4. 共同邻居权重
        neighbor_weight = self._calculate_neighbor_weight(query_id, candidate_id, graph)
        total_weight += neighbor_weight
        
        return total_weight
    
    def _calculate_dependency_weight(self, query_id: int, candidate_id: int, 
                                   graph: Dict[str, Any]) -> float:
        """计算直接依赖关系权重"""
        weight = 0.0
        
        dependencies = graph['dependencies']
        reverse_deps = graph['reverse_deps']
        
        # 检查query -> candidate的依赖
        if query_id in dependencies:
            for dep_id, kind in dependencies[query_id]:
                if dep_id == candidate_id:
                    weight += self.weights.get(kind, 0.0)
        
        # 检查candidate -> query的依赖
        if candidate_id in dependencies:
            for dep_id, kind in dependencies[candidate_id]:
                if dep_id == query_id:
                    weight += self.weights.get(kind, 0.0) * 0.8  # 反向依赖权重稍低
        
        return weight
    
    def _calculate_hierarchy_weight(self, query_id: int, candidate_id: int, 
                                  graph: Dict[str, Any]) -> float:
        """计算层次关系权重"""
        weight = 0.0
        hierarchy = graph['hierarchy']
        
        # 父子关系
        if query_id in hierarchy['parents'] and candidate_id in hierarchy['parents'][query_id]:
            weight += self.weights['child']
        elif candidate_id in hierarchy['parents'] and query_id in hierarchy['parents'][candidate_id]:
            weight += self.weights['parent']
        
        # 兄弟关系
        if query_id in hierarchy['siblings'] and candidate_id in hierarchy['siblings'][query_id]:
            weight += self.weights['sibling']
        
        return weight
    
    def _calculate_distance_weight(self, query_id: int, candidate_id: int, 
                                 graph: Dict[str, Any]) -> float:
        """计算路径距离权重（使用BFS）"""
        # 使用BFS计算最短路径距离
        distance = self._bfs_shortest_path(query_id, candidate_id, graph)
        
        if distance is None or distance == 0:
            return 0.0
        
        # 距离越近权重越高，使用指数衰减
        return max(0.0, 1.0 - distance * self.weights['distance_decay'])
    
    def _bfs_shortest_path(self, start_id: int, target_id: int, 
                          graph: Dict[str, Any]) -> Optional[int]:
        """使用BFS计算最短路径距离"""
        if start_id == target_id:
            return 0
        
        visited = set()
        queue = deque([(start_id, 0)])
        visited.add(start_id)
        
        dependencies = graph['dependencies']
        reverse_deps = graph['reverse_deps']
        hierarchy = graph['hierarchy']
        
        while queue:
            current_id, distance = queue.popleft()
            
            # 检查所有邻居节点
            neighbors = set()
            
            # 依赖关系邻居
            if current_id in dependencies:
                neighbors.update(dep_id for dep_id, _ in dependencies[current_id])
            if current_id in reverse_deps:
                neighbors.update(dep_id for dep_id, _ in reverse_deps[current_id])
            
            # 层次关系邻居
            if current_id in hierarchy['parents']:
                neighbors.update(hierarchy['parents'][current_id])
            if current_id in hierarchy['children']:
                neighbors.update(hierarchy['children'][current_id])
            if current_id in hierarchy['siblings']:
                neighbors.update(hierarchy['siblings'][current_id])
            
            for neighbor_id in neighbors:
                if neighbor_id == target_id:
                    return distance + 1
                
                if neighbor_id not in visited:
                    visited.add(neighbor_id)
                    queue.append((neighbor_id, distance + 1))
        
        return None  # 无路径连接
    
    def _calculate_neighbor_weight(self, query_id: int, candidate_id: int, 
                                 graph: Dict[str, Any]) -> float:
        """计算共同邻居权重"""
        # 获取两个任务的邻居集合
        query_neighbors = self._get_neighbors(query_id, graph)
        candidate_neighbors = self._get_neighbors(candidate_id, graph)
        
        # 计算共同邻居
        common_neighbors = query_neighbors.intersection(candidate_neighbors)
        
        if not common_neighbors:
            return 0.0
        
        # 共同邻居越多，权重越高
        neighbor_weight = len(common_neighbors) / max(len(query_neighbors), len(candidate_neighbors))
        return neighbor_weight * 0.2  # 共同邻居权重相对较低
    
    def _get_neighbors(self, task_id: int, graph: Dict[str, Any]) -> Set[int]:
        """获取任务的所有邻居节点"""
        neighbors = set()
        
        dependencies = graph['dependencies']
        reverse_deps = graph['reverse_deps']
        hierarchy = graph['hierarchy']
        
        # 依赖关系邻居
        if task_id in dependencies:
            neighbors.update(dep_id for dep_id, _ in dependencies[task_id])
        if task_id in reverse_deps:
            neighbors.update(dep_id for dep_id, _ in reverse_deps[task_id])
        
        # 层次关系邻居
        if task_id in hierarchy['parents']:
            neighbors.update(hierarchy['parents'][task_id])
        if task_id in hierarchy['children']:
            neighbors.update(hierarchy['children'][task_id])
        if task_id in hierarchy['siblings']:
            neighbors.update(hierarchy['siblings'][task_id])
        
        return neighbors
    
    def apply_structure_weights(self, semantic_scores: Dict[int, float], 
                              structure_weights: Dict[int, float],
                              alpha: float = 0.3) -> Dict[int, float]:
        """
        将结构先验权重应用到语义相似度分数上
        
        Args:
            semantic_scores: 语义相似度分数
            structure_weights: 结构先验权重
            alpha: 结构权重的影响因子 (0-1)
            
        Returns:
            调整后的综合分数
        """
        combined_scores = {}
        
        for task_id, semantic_score in semantic_scores.items():
            structure_weight = structure_weights.get(task_id, 0.0)
            
            # 线性组合语义分数和结构权重
            combined_score = (1 - alpha) * semantic_score + alpha * structure_weight
            combined_scores[task_id] = combined_score
        
        return combined_scores
    
    def get_structure_explanation(self, query_id: int, candidate_id: int) -> Dict[str, Any]:
        """
        获取结构权重的解释信息
        
        Args:
            query_id: 查询任务ID
            candidate_id: 候选任务ID
            
        Returns:
            包含权重解释的字典
        """
        graph = self._build_task_graph([query_id, candidate_id])
        
        explanation = {
            'query_task': query_id,
            'candidate_task': candidate_id,
            'relationships': [],
            'total_weight': 0.0
        }
        
        # 分析各种关系
        dep_weight = self._calculate_dependency_weight(query_id, candidate_id, graph)
        if dep_weight > 0:
            explanation['relationships'].append({
                'type': 'dependency',
                'weight': dep_weight,
                'description': '直接依赖关系'
            })
        
        hierarchy_weight = self._calculate_hierarchy_weight(query_id, candidate_id, graph)
        if hierarchy_weight > 0:
            explanation['relationships'].append({
                'type': 'hierarchy',
                'weight': hierarchy_weight,
                'description': '层次关系（父子/兄弟）'
            })
        
        distance_weight = self._calculate_distance_weight(query_id, candidate_id, graph)
        if distance_weight > 0:
            explanation['relationships'].append({
                'type': 'distance',
                'weight': distance_weight,
                'description': '路径距离权重'
            })
        
        neighbor_weight = self._calculate_neighbor_weight(query_id, candidate_id, graph)
        if neighbor_weight > 0:
            explanation['relationships'].append({
                'type': 'neighbor',
                'weight': neighbor_weight,
                'description': '共同邻居权重'
            })
        
        explanation['total_weight'] = sum(r['weight'] for r in explanation['relationships'])
        
        return explanation
    
    def clear_cache(self):
        """清空缓存"""
        self._graph_cache.clear()
        self._weights_cache.clear()
        logger.debug("Structure prior cache cleared")


# 全局实例
_structure_prior_calculator: Optional[StructurePriorCalculator] = None


def get_structure_prior_calculator() -> StructurePriorCalculator:
    """获取结构先验计算器单例"""
    global _structure_prior_calculator
    if _structure_prior_calculator is None:
        _structure_prior_calculator = StructurePriorCalculator()
    return _structure_prior_calculator
