"""
TreeSimplifier 单元测试

测试图简化算法的核心功能：
- PlanTree 到 DAG 转换
- 节点合并逻辑
- 环路检测
- 相似度匹配
"""

import pytest
from copy import deepcopy

from app.services.plans.dag_models import DAG, DAGNode
from app.services.plans.plan_models import PlanNode, PlanTree
from app.services.plans.tree_simplifier import TreeSimplifier
from app.services.plans.similarity_matcher import SimpleSimilarityMatcher


class TestDAGNode:
    """DAGNode 数据模型测试"""

    def test_merge_from_basic(self):
        """测试基本的节点合并"""
        node1 = DAGNode(
            id=1,
            name="Task A",
            instruction="Do task A",
            source_node_ids=[1],
            parent_ids={0},
            child_ids={2},
            dependencies=set(),
            metadata={"key1": "value1"},
        )
        node2 = DAGNode(
            id=2,
            name="Task A",
            instruction="Also do task A",
            source_node_ids=[2],
            parent_ids={0},
            child_ids={3},
            dependencies={4},
            metadata={"key2": "value2"},
        )

        node1.merge_from(node2)

        # 验证合并结果
        assert node1.source_node_ids == [1, 2]
        assert node1.parent_ids == {0}
        assert node1.child_ids == {2, 3}
        assert node1.dependencies == {4}
        assert "key1" in node1.metadata
        assert "key2" in node1.metadata

    def test_merge_from_instruction(self):
        """测试 instruction 合并"""
        node1 = DAGNode(id=1, name="Task", instruction="Step 1", source_node_ids=[1])
        node2 = DAGNode(id=2, name="Task", instruction="Step 2", source_node_ids=[2])

        node1.merge_from(node2)

        assert "Step 1" in node1.instruction
        assert "Step 2" in node1.instruction
        assert "---" in node1.instruction

    def test_merge_from_empty_instruction(self):
        """测试空 instruction 合并"""
        node1 = DAGNode(id=1, name="Task", instruction=None, source_node_ids=[1])
        node2 = DAGNode(id=2, name="Task", instruction="Step 2", source_node_ids=[2])

        node1.merge_from(node2)

        assert node1.instruction == "Step 2"


class TestDAG:
    """DAG 数据结构测试"""

    def test_get_roots(self):
        """测试获取根节点"""
        dag = DAG(plan_id=1, title="Test Plan")
        dag.nodes[1] = DAGNode(id=1, name="Root", source_node_ids=[1])
        dag.nodes[2] = DAGNode(id=2, name="Child", source_node_ids=[2], parent_ids={1})

        roots = dag.get_roots()
        assert len(roots) == 1
        assert roots[0].id == 1

    def test_get_leaves(self):
        """测试获取叶节点"""
        dag = DAG(plan_id=1, title="Test Plan")
        dag.nodes[1] = DAGNode(id=1, name="Root", source_node_ids=[1], child_ids={2})
        dag.nodes[2] = DAGNode(id=2, name="Leaf", source_node_ids=[2], parent_ids={1})

        leaves = dag.get_leaves()
        assert len(leaves) == 1
        assert leaves[0].id == 2

    def test_topological_sort(self):
        """测试拓扑排序"""
        dag = DAG(plan_id=1, title="Test Plan")
        dag.nodes[1] = DAGNode(id=1, name="A", source_node_ids=[1], child_ids={2, 3})
        dag.nodes[2] = DAGNode(id=2, name="B", source_node_ids=[2], parent_ids={1}, child_ids={4})
        dag.nodes[3] = DAGNode(id=3, name="C", source_node_ids=[3], parent_ids={1}, child_ids={4})
        dag.nodes[4] = DAGNode(id=4, name="D", source_node_ids=[4], parent_ids={2, 3})

        sorted_ids = dag.topological_sort()

        # 验证顺序：1 必须在 2, 3 之前；2, 3 必须在 4 之前
        assert sorted_ids.index(1) < sorted_ids.index(2)
        assert sorted_ids.index(1) < sorted_ids.index(3)
        assert sorted_ids.index(2) < sorted_ids.index(4)
        assert sorted_ids.index(3) < sorted_ids.index(4)


class TestTreeSimplifier:
    """TreeSimplifier 核心功能测试"""

    @pytest.fixture
    def simplifier(self):
        """创建测试用简化器（不使用 LLM）"""
        return TreeSimplifier(use_llm=False, use_cache=False)

    @pytest.fixture
    def sample_tree(self):
        """创建测试用计划树"""
        tree = PlanTree(id=1, title="Test Plan", description="Test")
        tree.nodes = {
            1: PlanNode(id=1, plan_id=1, name="Root Task", instruction="Main task", parent_id=None, dependencies=[]),
            2: PlanNode(id=2, plan_id=1, name="Sub Task A", instruction="Do A", parent_id=1, dependencies=[]),
            3: PlanNode(id=3, plan_id=1, name="Sub Task B", instruction="Do B", parent_id=1, dependencies=[]),
            4: PlanNode(id=4, plan_id=1, name="Sub Task A", instruction="Do A again", parent_id=1, dependencies=[]),
        }
        tree.adjacency = {
            None: [1],
            1: [2, 3, 4],
        }
        return tree

    def test_tree_to_dag(self, simplifier, sample_tree):
        """测试 PlanTree 到 DAG 转换"""
        dag = simplifier.tree_to_dag(sample_tree)

        assert dag.plan_id == sample_tree.id
        assert dag.title == sample_tree.title
        assert len(dag.nodes) == len(sample_tree.nodes)

        # 验证父子关系
        assert 2 in dag.nodes[1].child_ids
        assert 3 in dag.nodes[1].child_ids
        assert 4 in dag.nodes[1].child_ids
        assert 1 in dag.nodes[2].parent_ids

    def test_is_reachable(self, simplifier):
        """测试可达性检查"""
        dag = DAG(plan_id=1, title="Test")
        dag.nodes[1] = DAGNode(id=1, name="A", source_node_ids=[1], child_ids={2})
        dag.nodes[2] = DAGNode(id=2, name="B", source_node_ids=[2], parent_ids={1}, child_ids={3})
        dag.nodes[3] = DAGNode(id=3, name="C", source_node_ids=[3], parent_ids={2})

        assert simplifier.is_reachable(dag, 1, 3) is True
        assert simplifier.is_reachable(dag, 3, 1) is False
        assert simplifier.is_reachable(dag, 1, 1) is True

    def test_can_merge_parallel_nodes(self, simplifier):
        """测试并行节点可合并"""
        dag = DAG(plan_id=1, title="Test")
        dag.nodes[1] = DAGNode(id=1, name="Root", source_node_ids=[1], child_ids={2, 3})
        dag.nodes[2] = DAGNode(id=2, name="Task A", source_node_ids=[2], parent_ids={1})
        dag.nodes[3] = DAGNode(id=3, name="Task A", source_node_ids=[3], parent_ids={1})

        can, reason = simplifier.can_merge(dag, 2, 3)
        assert can is True

    def test_cannot_merge_parent_child(self, simplifier):
        """测试父子节点不可合并"""
        dag = DAG(plan_id=1, title="Test")
        dag.nodes[1] = DAGNode(id=1, name="Parent", source_node_ids=[1], child_ids={2})
        dag.nodes[2] = DAGNode(id=2, name="Child", source_node_ids=[2], parent_ids={1})

        can, reason = simplifier.can_merge(dag, 1, 2)
        assert can is False
        assert "父子" in reason or "parent" in reason.lower()

    def test_cannot_merge_with_dependency(self, simplifier):
        """测试有依赖关系的节点不可合并"""
        dag = DAG(plan_id=1, title="Test")
        dag.nodes[1] = DAGNode(id=1, name="Task A", source_node_ids=[1])
        dag.nodes[2] = DAGNode(id=2, name="Task B", source_node_ids=[2], dependencies={1})

        can, reason = simplifier.can_merge(dag, 1, 2)
        assert can is False
        assert "依赖" in reason

    def test_merge_nodes(self, simplifier):
        """测试节点合并"""
        dag = DAG(plan_id=1, title="Test")
        dag.nodes[1] = DAGNode(id=1, name="Root", source_node_ids=[1], child_ids={2, 3})
        dag.nodes[2] = DAGNode(id=2, name="Task A", source_node_ids=[2], parent_ids={1}, child_ids={4})
        dag.nodes[3] = DAGNode(id=3, name="Task A", source_node_ids=[3], parent_ids={1}, child_ids={5})
        dag.nodes[4] = DAGNode(id=4, name="End 1", source_node_ids=[4], parent_ids={2})
        dag.nodes[5] = DAGNode(id=5, name="End 2", source_node_ids=[5], parent_ids={3})

        result = simplifier.merge_nodes(dag, 2, 3)

        assert result is True
        assert 3 not in dag.nodes
        assert 3 in dag.merge_map
        assert dag.merge_map[3] == 2
        assert dag.nodes[2].source_node_ids == [2, 3]

    def test_simplify_fast(self, simplifier, sample_tree):
        """测试快速简化（基于名称匹配）"""
        dag = simplifier.simplify_fast(sample_tree)

        # 应该合并相同名称的节点
        assert dag.node_count() < len(sample_tree.nodes)
        assert len(dag.merge_map) > 0


class TestSimpleSimilarityMatcher:
    """SimpleSimilarityMatcher 测试"""

    def test_exact_match(self):
        """测试完全匹配"""
        matcher = SimpleSimilarityMatcher(threshold=0.9)
        node1 = DAGNode(id=1, name="Install dependencies", source_node_ids=[1])
        node2 = DAGNode(id=2, name="Install dependencies", source_node_ids=[2])

        assert matcher.should_merge(node1, node2) is True

    def test_similar_match(self):
        """测试相似匹配"""
        matcher = SimpleSimilarityMatcher(threshold=0.5)
        node1 = DAGNode(id=1, name="Install npm dependencies", source_node_ids=[1])
        node2 = DAGNode(id=2, name="Install pip dependencies", source_node_ids=[2])

        pairs = matcher.find_similar_pairs([node1, node2])
        assert len(pairs) > 0

    def test_no_match(self):
        """测试不匹配"""
        matcher = SimpleSimilarityMatcher(threshold=0.9)
        node1 = DAGNode(id=1, name="Build project", source_node_ids=[1])
        node2 = DAGNode(id=2, name="Run tests", source_node_ids=[2])

        assert matcher.should_merge(node1, node2) is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
