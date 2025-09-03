#!/usr/bin/env python3
"""
图注意力机制测试模块

测试图注意力网络(GAT)对语义检索结果的重排功能，包括：
- 注意力权重计算
- 图结构构建
- 候选结果重排
- 多层次特征融合
"""

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from app.database import init_db
from app.repository.tasks import SqliteTaskRepository
from app.services.graph_attention import (
    GraphAttentionReranker,
    get_graph_attention_reranker,
)


@pytest.fixture
def setup_test_db(tmp_path, monkeypatch):
    """设置测试数据库"""
    test_db = tmp_path / "graph_attention_test.db"
    monkeypatch.setattr("app.database.DB_PATH", str(test_db), raising=False)
    init_db()
    return SqliteTaskRepository()


@pytest.fixture
def sample_graph_data(setup_test_db):
    """创建测试图数据"""
    repo = setup_test_db

    # 创建任务层次结构
    root_id = repo.create_task("根任务", status="pending", priority=1)
    branch1_id = repo.create_task("分支1", status="pending", priority=2, parent_id=root_id)
    branch2_id = repo.create_task("分支2", status="pending", priority=3, parent_id=root_id)
    leaf1_id = repo.create_task("叶子1", status="done", priority=4, parent_id=branch1_id)
    leaf2_id = repo.create_task("叶子2", status="in_progress", priority=5, parent_id=branch2_id)

    # 创建依赖关系
    repo.create_link(leaf1_id, leaf2_id, "requires")
    repo.create_link(branch1_id, branch2_id, "refers")

    # 模拟embeddings
    embeddings = {
        root_id: [0.1] * 1024,
        branch1_id: [0.2] * 1024,
        branch2_id: [0.3] * 1024,
        leaf1_id: [0.4] * 1024,
        leaf2_id: [0.5] * 1024,
    }

    return {
        "root": root_id,
        "branch1": branch1_id,
        "branch2": branch2_id,
        "leaf1": leaf1_id,
        "leaf2": leaf2_id,
        "embeddings": embeddings,
        "repo": repo,
    }


def test_graph_attention_reranker_initialization():
    """测试图注意力重排器初始化"""
    reranker = GraphAttentionReranker()

    assert reranker is not None
    assert reranker.attention_dim == 64
    assert reranker.num_heads == 4
    assert reranker.relation_weights["requires"] == 1.0


def test_adjacency_matrix_construction(sample_graph_data):
    """测试邻接矩阵构建"""
    data = sample_graph_data
    reranker = GraphAttentionReranker(data["repo"])

    task_ids = [data["root"], data["branch1"], data["branch2"]]
    tasks = {}
    for task_id in task_ids:
        tasks[task_id] = data["repo"].get_task_info(task_id)

    adjacency = reranker._build_adjacency_matrix(task_ids, tasks)

    # 验证矩阵形状
    assert adjacency.shape == (3, 3)

    # 验证父子关系
    root_idx = task_ids.index(data["root"])
    branch1_idx = task_ids.index(data["branch1"])

    # 应该有父子连接
    assert adjacency[branch1_idx, root_idx] > 0  # child -> parent
    assert adjacency[root_idx, branch1_idx] > 0  # parent -> child


def test_node_features_construction(sample_graph_data):
    """测试节点特征构建"""
    data = sample_graph_data
    reranker = GraphAttentionReranker(data["repo"])

    task_ids = [data["root"], data["branch1"], data["leaf1"]]
    tasks = {}
    for task_id in task_ids:
        tasks[task_id] = data["repo"].get_task_info(task_id)

    embeddings = {task_id: data["embeddings"][task_id] for task_id in task_ids}

    node_features = reranker._build_node_features(task_ids, tasks, embeddings)

    # 验证特征矩阵形状
    expected_dim = 1024 + 5  # embedding + 5个结构特征
    assert node_features.shape == (3, expected_dim)

    # 验证embedding部分
    for i, task_id in enumerate(task_ids):
        embedding = embeddings[task_id]
        np.testing.assert_array_almost_equal(node_features[i, : len(embedding)], embedding, decimal=6)


def test_attention_score_computation(sample_graph_data):
    """测试注意力分数计算"""
    data = sample_graph_data
    reranker = GraphAttentionReranker(data["repo"])

    task_ids = [data["root"], data["branch1"], data["branch2"]]
    subgraph = reranker._build_attention_subgraph(task_ids, data["embeddings"])

    attention_scores = reranker._compute_attention_scores(data["root"], subgraph)

    # 验证分数结构
    assert len(attention_scores) == len(task_ids)
    assert data["root"] in attention_scores
    assert attention_scores[data["root"]] == 1.0  # 查询节点自身

    # 验证分数范围
    for score in attention_scores.values():
        assert 0.0 <= score <= 1.0


def test_rerank_with_attention(sample_graph_data):
    """测试注意力重排功能"""
    data = sample_graph_data
    reranker = GraphAttentionReranker(data["repo"])

    # 准备候选结果
    candidates = [
        {"id": data["branch1"], "similarity": 0.8, "name": "Branch1"},
        {"id": data["branch2"], "similarity": 0.6, "name": "Branch2"},
        {"id": data["leaf1"], "similarity": 0.4, "name": "Leaf1"},
    ]

    # 执行重排
    reranked = reranker.rerank_with_attention(data["root"], candidates, data["embeddings"], alpha=0.3)

    # 验证结果结构
    assert len(reranked) == len(candidates)
    for result in reranked:
        assert "attention_score" in result
        assert "combined_score" in result
        assert 0.0 <= result["attention_score"] <= 1.0


def test_cosine_similarity_calculation():
    """测试余弦相似度计算"""
    reranker = GraphAttentionReranker()

    # 测试相同向量
    vec1 = np.array([1.0, 2.0, 3.0])
    vec2 = np.array([1.0, 2.0, 3.0])
    similarity = reranker._cosine_similarity(vec1, vec2)
    assert abs(similarity - 1.0) < 1e-6

    # 测试正交向量
    vec1 = np.array([1.0, 0.0, 0.0])
    vec2 = np.array([0.0, 1.0, 0.0])
    similarity = reranker._cosine_similarity(vec1, vec2)
    assert abs(similarity - 0.0) < 1e-6

    # 测试零向量
    vec1 = np.array([0.0, 0.0, 0.0])
    vec2 = np.array([1.0, 2.0, 3.0])
    similarity = reranker._cosine_similarity(vec1, vec2)
    assert similarity == 0.0


def test_attention_explanation(sample_graph_data):
    """测试注意力权重解释"""
    data = sample_graph_data
    reranker = GraphAttentionReranker(data["repo"])

    explanation = reranker.get_attention_explanation(data["root"], data["branch1"], data["embeddings"])

    # 验证解释结构
    assert "query_task_id" in explanation
    assert "candidate_task_id" in explanation
    assert "feature_similarity" in explanation
    assert "structural_weight" in explanation
    assert "attention_weight" in explanation
    assert "explanation" in explanation

    assert explanation["query_task_id"] == data["root"]
    assert explanation["candidate_task_id"] == data["branch1"]


def test_empty_candidates_handling():
    """测试空候选列表处理"""
    reranker = GraphAttentionReranker()

    result = reranker.rerank_with_attention(1, [], {}, alpha=0.3)
    assert result == []


def test_single_candidate_handling(sample_graph_data):
    """测试单个候选结果处理"""
    data = sample_graph_data
    reranker = GraphAttentionReranker(data["repo"])

    candidates = [{"id": data["branch1"], "similarity": 0.8}]

    result = reranker.rerank_with_attention(data["root"], candidates, data["embeddings"], alpha=0.3)

    assert len(result) == 1
    assert result[0]["id"] == data["branch1"]


def test_attention_weight_bounds(sample_graph_data):
    """测试注意力权重边界"""
    data = sample_graph_data
    reranker = GraphAttentionReranker(data["repo"])

    candidates = [
        {"id": data["branch1"], "similarity": 1.5},  # 超出正常范围
        {"id": data["branch2"], "similarity": -0.5},  # 负值
    ]

    reranked = reranker.rerank_with_attention(data["root"], candidates, data["embeddings"], alpha=0.5)

    # 验证注意力分数在合理范围内
    for result in reranked:
        assert 0.0 <= result["attention_score"] <= 1.0


def test_cache_functionality(sample_graph_data):
    """测试缓存功能"""
    data = sample_graph_data
    reranker = GraphAttentionReranker(data["repo"])

    candidates = [{"id": data["branch1"], "similarity": 0.8}]

    # 第一次计算
    result1 = reranker.rerank_with_attention(data["root"], candidates, data["embeddings"], alpha=0.3)

    # 清空缓存
    reranker.clear_cache()

    # 再次计算
    result2 = reranker.rerank_with_attention(data["root"], candidates, data["embeddings"], alpha=0.3)

    # 结果应该相似（可能由于浮点精度略有差异）
    assert len(result1) == len(result2)
    assert result1[0]["id"] == result2[0]["id"]


def test_global_reranker_instance():
    """测试全局重排器实例"""
    reranker1 = get_graph_attention_reranker()
    reranker2 = get_graph_attention_reranker()

    # 应该是同一个实例
    assert reranker1 is reranker2


def test_error_handling_in_reranking(sample_graph_data):
    """测试重排过程中的错误处理"""
    data = sample_graph_data

    # 使用无效的repo模拟错误
    with patch.object(SqliteTaskRepository, "get_task_info", side_effect=Exception("DB Error")):
        reranker = GraphAttentionReranker(data["repo"])

        candidates = [{"id": data["branch1"], "similarity": 0.8}]

        # 应该优雅处理错误，返回原始结果
        result = reranker.rerank_with_attention(data["root"], candidates, data["embeddings"], alpha=0.3)

        assert len(result) == 1
        assert result[0]["id"] == data["branch1"]


def test_multi_head_attention_concept():
    """测试多头注意力概念（简化版本）"""
    reranker = GraphAttentionReranker()

    # 验证多头注意力参数设置
    assert reranker.num_heads == 4
    assert reranker.attention_dim == 64

    # 验证关系权重配置
    assert reranker.relation_weights["requires"] > reranker.relation_weights["refers"]
    assert reranker.relation_weights["child"] > reranker.relation_weights["sibling"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
