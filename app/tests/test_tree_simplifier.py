"""
TreeSimplifier 

: 
- PlanTree  DAG 
- 
- 
- 
"""

import pytest
from copy import deepcopy

from app.services.plans.dag_models import DAG, DAGNode
from app.services.plans.plan_models import PlanNode, PlanTree
from app.services.plans.tree_simplifier import TreeSimplifier
from app.services.plans.similarity_matcher import SimpleSimilarityMatcher


class TestDAGNode:
    """DAGNode model"""

    def test_merge_from_basic(self):
        """"""
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

        assert node1.source_node_ids == [1, 2]
        assert node1.parent_ids == {0}
        assert node1.child_ids == {2, 3}
        assert node1.dependencies == {4}
        assert "key1" in node1.metadata
        assert "key2" in node1.metadata

    def test_merge_from_instruction(self):
        """ instruction """
        node1 = DAGNode(id=1, name="Task", instruction="Step 1", source_node_ids=[1])
        node2 = DAGNode(id=2, name="Task", instruction="Step 2", source_node_ids=[2])

        node1.merge_from(node2)

        assert "Step 1" in node1.instruction
        assert "Step 2" in node1.instruction
        assert "---" in node1.instruction

    def test_merge_from_empty_instruction(self):
        """ instruction """
        node1 = DAGNode(id=1, name="Task", instruction=None, source_node_ids=[1])
        node2 = DAGNode(id=2, name="Task", instruction="Step 2", source_node_ids=[2])

        node1.merge_from(node2)

        assert node1.instruction == "Step 2"


class TestDAG:
    """DAG """

    def test_get_roots(self):
        """get"""
        dag = DAG(plan_id=1, title="Test Plan")
        dag.nodes[1] = DAGNode(id=1, name="Root", source_node_ids=[1])
        dag.nodes[2] = DAGNode(id=2, name="Child", source_node_ids=[2], parent_ids={1})

        roots = dag.get_roots()
        assert len(roots) == 1
        assert roots[0].id == 1

    def test_get_leaves(self):
        """get"""
        dag = DAG(plan_id=1, title="Test Plan")
        dag.nodes[1] = DAGNode(id=1, name="Root", source_node_ids=[1], child_ids={2})
        dag.nodes[2] = DAGNode(id=2, name="Leaf", source_node_ids=[2], parent_ids={1})

        leaves = dag.get_leaves()
        assert len(leaves) == 1
        assert leaves[0].id == 2

    def test_topological_sort(self):
        """"""
        dag = DAG(plan_id=1, title="Test Plan")
        dag.nodes[1] = DAGNode(id=1, name="A", source_node_ids=[1], child_ids={2, 3})
        dag.nodes[2] = DAGNode(id=2, name="B", source_node_ids=[2], parent_ids={1}, child_ids={4})
        dag.nodes[3] = DAGNode(id=3, name="C", source_node_ids=[3], parent_ids={1}, child_ids={4})
        dag.nodes[4] = DAGNode(id=4, name="D", source_node_ids=[4], parent_ids={2, 3})

        sorted_ids = dag.topological_sort()

        assert sorted_ids.index(1) < sorted_ids.index(2)
        assert sorted_ids.index(1) < sorted_ids.index(3)
        assert sorted_ids.index(2) < sorted_ids.index(4)
        assert sorted_ids.index(3) < sorted_ids.index(4)


class TestTreeSimplifier:
    """TreeSimplifier """

    @pytest.fixture
    def simplifier(self):
        """create( LLM)"""
        return TreeSimplifier(use_llm=False, use_cache=False)

    @pytest.fixture
    def sample_tree(self):
        """createplan"""
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
        """ PlanTree  DAG """
        dag = simplifier.tree_to_dag(sample_tree)

        assert dag.plan_id == sample_tree.id
        assert dag.title == sample_tree.title
        assert len(dag.nodes) == len(sample_tree.nodes)

        assert 2 in dag.nodes[1].child_ids
        assert 3 in dag.nodes[1].child_ids
        assert 4 in dag.nodes[1].child_ids
        assert 1 in dag.nodes[2].parent_ids

    def test_is_reachable(self, simplifier):
        """"""
        dag = DAG(plan_id=1, title="Test")
        dag.nodes[1] = DAGNode(id=1, name="A", source_node_ids=[1], child_ids={2})
        dag.nodes[2] = DAGNode(id=2, name="B", source_node_ids=[2], parent_ids={1}, child_ids={3})
        dag.nodes[3] = DAGNode(id=3, name="C", source_node_ids=[3], parent_ids={2})

        assert simplifier.is_reachable(dag, 1, 3) is True
        assert simplifier.is_reachable(dag, 3, 1) is False
        assert simplifier.is_reachable(dag, 1, 1) is True

    def test_can_merge_parallel_nodes(self, simplifier):
        """"""
        dag = DAG(plan_id=1, title="Test")
        dag.nodes[1] = DAGNode(id=1, name="Root", source_node_ids=[1], child_ids={2, 3})
        dag.nodes[2] = DAGNode(id=2, name="Task A", source_node_ids=[2], parent_ids={1})
        dag.nodes[3] = DAGNode(id=3, name="Task A", source_node_ids=[3], parent_ids={1})

        can, reason = simplifier.can_merge(dag, 2, 3)
        assert can is True

    def test_cannot_merge_parent_child(self, simplifier):
        """"""
        dag = DAG(plan_id=1, title="Test")
        dag.nodes[1] = DAGNode(id=1, name="Parent", source_node_ids=[1], child_ids={2})
        dag.nodes[2] = DAGNode(id=2, name="Child", source_node_ids=[2], parent_ids={1})

        can, reason = simplifier.can_merge(dag, 1, 2)
        assert can is False
        assert "" in reason or "parent" in reason.lower()

    def test_cannot_merge_with_dependency(self, simplifier):
        """"""
        dag = DAG(plan_id=1, title="Test")
        dag.nodes[1] = DAGNode(id=1, name="Task A", source_node_ids=[1])
        dag.nodes[2] = DAGNode(id=2, name="Task B", source_node_ids=[2], dependencies={1})

        can, reason = simplifier.can_merge(dag, 1, 2)
        assert can is False
        assert "" in reason

    def test_merge_nodes(self, simplifier):
        """"""
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
        """(name)"""
        dag = simplifier.simplify_fast(sample_tree)

        assert dag.node_count() < len(sample_tree.nodes)
        assert len(dag.merge_map) > 0


class TestSimpleSimilarityMatcher:
    """SimpleSimilarityMatcher """

    def test_exact_match(self):
        """"""
        matcher = SimpleSimilarityMatcher(threshold=0.9)
        node1 = DAGNode(id=1, name="Install dependencies", source_node_ids=[1])
        node2 = DAGNode(id=2, name="Install dependencies", source_node_ids=[2])

        assert matcher.should_merge(node1, node2) is True

    def test_similar_match(self):
        """"""
        matcher = SimpleSimilarityMatcher(threshold=0.5)
        node1 = DAGNode(id=1, name="Install npm dependencies", source_node_ids=[1])
        node2 = DAGNode(id=2, name="Install pip dependencies", source_node_ids=[2])

        pairs = matcher.find_similar_pairs([node1, node2])
        assert len(pairs) > 0

    def test_no_match(self):
        """"""
        matcher = SimpleSimilarityMatcher(threshold=0.9)
        node1 = DAGNode(id=1, name="Build project", source_node_ids=[1])
        node2 = DAGNode(id=2, name="Run tests", source_node_ids=[2])

        assert matcher.should_merge(node1, node2) is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
