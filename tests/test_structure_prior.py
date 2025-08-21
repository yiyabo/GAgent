#!/usr/bin/env python3
"""
结构先验权重计算测试模块

测试基于任务图关系的结构先验权重计算功能，包括：
- 依赖关系权重计算
- 层次关系权重计算
- 路径距离权重计算
- 综合权重应用
"""

import pytest
from unittest.mock import patch, MagicMock

from app.database import init_db
from app.repository.tasks import SqliteTaskRepository
from app.services.structure_prior import StructurePriorCalculator, get_structure_prior_calculator


@pytest.fixture
def setup_test_db(tmp_path, monkeypatch):
    """设置测试数据库"""
    test_db = tmp_path / "structure_test.db"
    monkeypatch.setattr("app.database.DB_PATH", str(test_db), raising=False)
    init_db()
    return SqliteTaskRepository()


@pytest.fixture
def sample_tasks(setup_test_db):
    """创建测试任务数据"""
    repo = setup_test_db
    
    # 创建层次结构的任务
    parent_id = repo.create_task("父任务", status="pending", priority=1)
    child1_id = repo.create_task("子任务1", status="pending", priority=2, parent_id=parent_id)
    child2_id = repo.create_task("子任务2", status="pending", priority=3, parent_id=parent_id)
    
    # 创建独立任务
    independent_id = repo.create_task("独立任务", status="pending", priority=4)
    
    # 创建依赖关系
    repo.create_link(child1_id, child2_id, "requires")  # child2 requires child1
    repo.create_link(independent_id, parent_id, "refers")  # parent refers to independent
    
    return {
        'parent': parent_id,
        'child1': child1_id,
        'child2': child2_id,
        'independent': independent_id,
        'repo': repo
    }


def test_structure_prior_calculator_initialization():
    """测试结构先验计算器初始化"""
    calculator = StructurePriorCalculator()
    
    assert calculator is not None
    assert calculator.weights['requires'] == 0.8
    assert calculator.weights['refers'] == 0.4
    assert calculator.weights['sibling'] == 0.3


def test_dependency_weight_calculation(sample_tasks):
    """测试依赖关系权重计算"""
    data = sample_tasks
    calculator = StructurePriorCalculator(data['repo'])
    
    # 测试requires依赖关系
    weights = calculator.compute_structure_weights(
        data['child1'], [data['child2']]
    )
    
    # child2 requires child1，所以应该有较高权重
    assert data['child2'] in weights
    assert weights[data['child2']] > 0.0


def test_hierarchy_weight_calculation(sample_tasks):
    """测试层次关系权重计算"""
    data = sample_tasks
    calculator = StructurePriorCalculator(data['repo'])
    
    # 测试父子关系
    weights = calculator.compute_structure_weights(
        data['parent'], [data['child1'], data['child2']]
    )
    
    # 子任务应该有权重
    assert data['child1'] in weights
    assert data['child2'] in weights
    assert weights[data['child1']] > 0.0
    assert weights[data['child2']] > 0.0


def test_sibling_relationship(sample_tasks):
    """测试兄弟关系权重计算"""
    data = sample_tasks
    calculator = StructurePriorCalculator(data['repo'])
    
    # 测试兄弟关系
    weights = calculator.compute_structure_weights(
        data['child1'], [data['child2']]
    )
    
    # 兄弟任务应该有权重
    assert data['child2'] in weights
    assert weights[data['child2']] > 0.0


def test_self_reference_weight(sample_tasks):
    """测试自引用权重"""
    data = sample_tasks
    calculator = StructurePriorCalculator(data['repo'])
    
    # 测试自引用
    weights = calculator.compute_structure_weights(
        data['parent'], [data['parent']]
    )
    
    # 自引用应该有最高权重
    assert weights[data['parent']] == 1.0


def test_apply_structure_weights():
    """测试结构权重应用"""
    calculator = StructurePriorCalculator()
    
    semantic_scores = {
        1: 0.8,
        2: 0.6,
        3: 0.4
    }
    
    structure_weights = {
        1: 0.2,
        2: 0.8,
        3: 0.1
    }
    
    # 应用结构权重
    combined_scores = calculator.apply_structure_weights(
        semantic_scores, structure_weights, alpha=0.3
    )
    
    # 验证组合分数
    assert len(combined_scores) == 3
    for task_id in semantic_scores:
        assert task_id in combined_scores
        # 组合分数应该在语义分数和结构权重之间
        semantic = semantic_scores[task_id]
        structure = structure_weights[task_id]
        expected = 0.7 * semantic + 0.3 * structure
        assert abs(combined_scores[task_id] - expected) < 0.001


def test_structure_explanation(sample_tasks):
    """测试结构权重解释功能"""
    data = sample_tasks
    calculator = StructurePriorCalculator(data['repo'])
    
    # 获取权重解释
    explanation = calculator.get_structure_explanation(
        data['parent'], data['child1']
    )
    
    # 验证解释结构
    assert 'query_task' in explanation
    assert 'candidate_task' in explanation
    assert 'relationships' in explanation
    assert 'total_weight' in explanation
    
    assert explanation['query_task'] == data['parent']
    assert explanation['candidate_task'] == data['child1']
    assert isinstance(explanation['relationships'], list)


def test_empty_candidate_list():
    """测试空候选列表处理"""
    calculator = StructurePriorCalculator()
    
    weights = calculator.compute_structure_weights(1, [])
    assert weights == {}


def test_nonexistent_task_handling(sample_tasks):
    """测试不存在任务的处理"""
    data = sample_tasks
    calculator = StructurePriorCalculator(data['repo'])
    
    # 使用不存在的任务ID
    weights = calculator.compute_structure_weights(
        999, [data['parent']]
    )
    
    # 应该能正常处理，不抛出异常
    assert isinstance(weights, dict)


def test_cache_functionality(sample_tasks):
    """测试缓存功能"""
    data = sample_tasks
    calculator = StructurePriorCalculator(data['repo'])
    
    # 第一次计算
    weights1 = calculator.compute_structure_weights(
        data['parent'], [data['child1'], data['child2']]
    )
    
    # 第二次计算（应该使用缓存）
    weights2 = calculator.compute_structure_weights(
        data['parent'], [data['child1'], data['child2']]
    )
    
    # 结果应该相同
    assert weights1 == weights2
    
    # 清空缓存
    calculator.clear_cache()
    
    # 再次计算
    weights3 = calculator.compute_structure_weights(
        data['parent'], [data['child1'], data['child2']]
    )
    
    # 结果仍应该相同
    assert weights1 == weights3


def test_global_calculator_instance():
    """测试全局计算器实例"""
    calculator1 = get_structure_prior_calculator()
    calculator2 = get_structure_prior_calculator()
    
    # 应该是同一个实例
    assert calculator1 is calculator2


def test_weight_bounds():
    """测试权重边界"""
    calculator = StructurePriorCalculator()
    
    semantic_scores = {1: 1.5, 2: -0.5, 3: 0.5}  # 超出正常范围的分数
    structure_weights = {1: 2.0, 2: -1.0, 3: 0.5}  # 超出正常范围的权重
    
    combined_scores = calculator.apply_structure_weights(
        semantic_scores, structure_weights, alpha=0.5
    )
    
    # 验证所有分数都在合理范围内
    for score in combined_scores.values():
        assert score >= -1.0  # 允许负值但不能太小
        assert score <= 2.0   # 允许超过1但不能太大


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
