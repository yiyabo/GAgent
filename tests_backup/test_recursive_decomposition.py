"""
递归任务分解功能的单元测试
"""

from unittest.mock import MagicMock, Mock, patch

import pytest

from app.services.decomposition_with_evaluation import (
    decompose_task_with_evaluation,
    evaluate_decomposition_quality,
    should_decompose_with_quality_check,
)
from app.services.recursive_decomposition import (
    TaskType,
    _build_decomposition_prompt,
    decompose_task,
    determine_task_type,
    evaluate_task_complexity,
    recursive_decompose_plan,
    should_decompose_task,
)


class TestTaskComplexityEvaluation:
    """测试任务复杂度评估功能"""

    def test_high_complexity_evaluation(self):
        """测试高复杂度任务识别"""
        task_name = "设计完整系统架构"
        task_prompt = "构建端到端的微服务架构平台，包含完整的监控和日志系统"

        complexity = evaluate_task_complexity(task_name, task_prompt)
        assert complexity == "high"

    def test_medium_complexity_evaluation(self):
        """测试中等复杂度任务识别"""
        task_name = "实现用户模块"
        task_prompt = "开发用户注册登录功能，集成第三方认证"

        complexity = evaluate_task_complexity(task_name, task_prompt)
        assert complexity == "medium"

    def test_low_complexity_evaluation(self):
        """测试低复杂度任务识别"""
        task_name = "修复登录bug"
        task_prompt = "调试并修复用户登录问题"

        complexity = evaluate_task_complexity(task_name, task_prompt)
        assert complexity == "low"

    def test_empty_input_complexity(self):
        """测试空输入的复杂度评估"""
        complexity = evaluate_task_complexity("", "")
        assert complexity in ["low", "medium", "high"]


class TestTaskTypedetermination:
    """测试任务类型确定功能"""

    def test_root_task_determination(self):
        """测试根任务类型确定"""
        task = {"depth": 0, "task_type": "root"}
        task_type = determine_task_type(task)
        assert task_type == TaskType.ROOT

    def test_composite_task_determination(self):
        """测试复合任务类型确定"""
        task = {"depth": 1, "task_type": "composite"}
        task_type = determine_task_type(task)
        assert task_type == TaskType.COMPOSITE

    def test_atomic_task_determination(self):
        """测试原子任务类型确定"""
        task = {"depth": 2, "task_type": "atomic"}
        task_type = determine_task_type(task)
        assert task_type == TaskType.ATOMIC

    def test_complexity_based_determination(self):
        """测试基于复杂度的类型确定"""
        task = {"depth": 0, "name": "构建完整系统架构"}
        task_type = determine_task_type(task, complexity="high")
        assert task_type == TaskType.ROOT

        task_type = determine_task_type(task, complexity="medium")
        assert task_type == TaskType.COMPOSITE

        task_type = determine_task_type(task, complexity="low")
        assert task_type == TaskType.ATOMIC


class TestShouldDecomposeTask:
    """测试任务分解判断功能"""

    def test_should_decompose_root_task(self):
        """测试根任务是否应该分解"""
        task = {"id": 1, "depth": 0, "task_type": "root"}
        mock_repo = Mock()
        mock_repo.get_children.return_value = []

        should_decompose = should_decompose_task(task, mock_repo)
        assert should_decompose == True

    def test_should_not_decompose_atomic_task(self):
        """测试原子任务不应该分解"""
        task = {"id": 1, "depth": 2, "task_type": "atomic"}
        mock_repo = Mock()

        should_decompose = should_decompose_task(task, mock_repo)
        assert should_decompose == False

    def test_should_not_decompose_max_depth_task(self):
        """测试达到最大深度的任务不应该分解"""
        task = {"id": 1, "depth": 2, "task_type": "composite"}  # MAX_DEPTH-1 = 2
        mock_repo = Mock()

        should_decompose = should_decompose_task(task, mock_repo)
        assert should_decompose == False

    def test_should_not_decompose_task_with_sufficient_children(self):
        """测试已有足够子任务的任务不应该分解"""
        task = {"id": 1, "depth": 0, "task_type": "root"}
        mock_repo = Mock()
        mock_repo.get_children.return_value = [{"id": 2, "status": "pending"}, {"id": 3, "status": "pending"}]

        should_decompose = should_decompose_task(task, mock_repo)
        assert should_decompose == False


class TestDecomposeTask:
    """测试任务分解功能"""

    @patch("app.services.recursive_decomposition.propose_plan_service")
    def test_successful_task_decomposition(self, mock_propose_plan):
        """测试成功的任务分解"""
        # 模拟仓储
        mock_repo = Mock()
        mock_repo.get_task_info.return_value = {"id": 1, "name": "构建用户系统", "depth": 0, "task_type": "root"}
        mock_repo.get_task_input_prompt.return_value = "实现完整的用户管理系统"
        mock_repo.create_task.side_effect = [101, 102, 103]
        mock_repo.update_task_type.return_value = None
        mock_repo.upsert_task_input.return_value = None

        # 模拟规划服务响应
        mock_propose_plan.return_value = {
            "title": "分解_构建用户系统",
            "tasks": [
                {"name": "用户注册模块", "prompt": "实现用户注册功能", "priority": 100},
                {"name": "用户登录模块", "prompt": "实现用户登录功能", "priority": 110},
                {"name": "用户管理模块", "prompt": "实现用户信息管理", "priority": 120},
            ],
        }

        # 执行分解
        result = decompose_task(1, repo=mock_repo, max_subtasks=5)

        # 验证结果
        assert result["success"] == True
        assert result["task_id"] == 1
        assert len(result["subtasks"]) == 3
        assert result["subtasks"][0]["name"] == "用户注册模块"
        assert result["subtasks"][0]["type"] == "composite"
        assert result["decomposition_depth"] == 1

        # 验证调用
        mock_repo.create_task.assert_called()
        mock_repo.upsert_task_input.assert_called()

    def test_decompose_nonexistent_task(self):
        """测试分解不存在的任务"""
        mock_repo = Mock()
        mock_repo.get_task_info.return_value = None

        result = decompose_task(999, repo=mock_repo)

        assert result["success"] == False
        assert "not found" in result["error"].lower()

    @patch("app.services.recursive_decomposition.should_decompose_task")
    def test_decompose_task_that_should_not_be_decomposed(self, mock_should_decompose):
        """测试分解不应该分解的任务"""
        mock_repo = Mock()
        mock_repo.get_task_info.return_value = {"id": 1, "name": "简单任务", "depth": 2, "task_type": "atomic"}
        mock_should_decompose.return_value = False

        result = decompose_task(1, repo=mock_repo, force=False)

        assert result["success"] == False
        assert "not need" in result["error"].lower()


class TestBuildDecompositionPrompt:
    """测试分解提示构建功能"""

    def test_build_root_task_prompt(self):
        """测试根任务分解提示"""
        prompt = _build_decomposition_prompt("构建电商系统", "开发完整的电商平台", TaskType.ROOT, 6)

        assert "根任务分解" in prompt
        assert "构建电商系统" in prompt
        assert "2-6" in prompt
        assert "功能模块" in prompt

    def test_build_composite_task_prompt(self):
        """测试复合任务分解提示"""
        prompt = _build_decomposition_prompt("用户模块", "实现用户管理功能", TaskType.COMPOSITE, 4)

        assert "复合任务" in prompt
        assert "用户模块" in prompt
        assert "2-4" in prompt
        assert "实现步骤" in prompt

    def test_build_atomic_task_prompt(self):
        """测试原子任务分解提示（应该返回空字符串）"""
        prompt = _build_decomposition_prompt("修复bug", "修复登录问题", TaskType.ATOMIC, 3)

        assert prompt == ""


class TestRecursiveDecomposePlan:
    """测试递归计划分解功能"""

    @patch("app.services.recursive_decomposition.decompose_task")
    @patch("app.services.recursive_decomposition.should_decompose_task")
    def test_recursive_plan_decomposition(self, mock_should_decompose, mock_decompose):
        """测试递归计划分解"""
        mock_repo = Mock()

        # 模拟第一轮：计划中有一个根任务需要分解
        call_count = [0]  # 使用列表来允许修改

        def list_plan_tasks_side_effect(title):
            call_count[0] += 1
            if call_count[0] == 1:
                # 第一轮：只有根任务
                return [{"id": 1, "depth": 0, "task_type": "root"}]
            else:
                # 第二轮：根任务 + 新生成的子任务
                return [{"id": 1, "depth": 0, "task_type": "root"}, {"id": 2, "depth": 1, "task_type": "composite"}]

        mock_repo.list_plan_tasks.side_effect = list_plan_tasks_side_effect

        # 模拟分解条件：第一个任务需要分解，后续不需要
        mock_should_decompose.side_effect = [True, False, False]  # 为多次调用准备

        # 模拟分解结果
        mock_decompose.return_value = {
            "success": True,
            "task_id": 1,
            "subtasks": [{"id": 2, "name": "子任务1", "type": "composite"}],
        }

        result = recursive_decompose_plan("测试计划", repo=mock_repo, max_depth=2)

        assert result["success"] == True
        assert result["plan_title"] == "测试计划"
        assert result["total_tasks_decomposed"] == 1

    def test_recursive_plan_decomposition_error(self):
        """测试递归计划分解错误处理"""
        mock_repo = Mock()
        mock_repo.list_plan_tasks.side_effect = Exception("数据库错误")

        result = recursive_decompose_plan("测试计划", repo=mock_repo)

        assert result["success"] == False
        assert "错误" in result["error"]


class TestDecompositionQualityEvaluation:
    """测试分解质量评估功能"""

    def test_evaluate_good_decomposition_quality(self):
        """测试良好分解的质量评估"""
        parent_task = {"name": "构建用户系统", "task_type": "root", "depth": 0}
        subtasks = [
            {"name": "用户注册功能", "type": "composite"},
            {"name": "用户认证功能", "type": "composite"},
            {"name": "用户信息管理", "type": "composite"},
        ]

        evaluation = evaluate_decomposition_quality(parent_task, subtasks)

        assert evaluation["quality_score"] >= 0.7
        assert evaluation["num_subtasks"] == 3
        assert evaluation["type_consistency"] == True
        assert evaluation["expected_child_type"] == "composite"
        assert evaluation["needs_refinement"] == False

    def test_evaluate_poor_decomposition_quality(self):
        """测试劣质分解的质量评估"""
        parent_task = {"name": "简单任务", "task_type": "composite", "depth": 1}
        subtasks = [
            {"name": "子任务 1", "type": "atomic"},  # 名称不具体
            {"name": "", "type": "composite"},  # 空名称，类型不一致
        ]

        evaluation = evaluate_decomposition_quality(parent_task, subtasks)

        assert evaluation["quality_score"] < 0.7
        assert len(evaluation["issues"]) > 0
        assert len(evaluation["suggestions"]) > 0
        assert evaluation["type_consistency"] == False
        assert evaluation["needs_refinement"] == True


class TestDecomposeTaskWithEvaluation:
    """测试带评估的任务分解功能"""

    @patch("app.services.decomposition_with_evaluation.base_decompose_task")
    @patch("app.services.decomposition_with_evaluation.evaluate_decomposition_quality")
    def test_decompose_with_evaluation_meets_threshold(self, mock_eval_quality, mock_base_decompose):
        """测试达到质量阈值的分解"""
        mock_repo = Mock()
        mock_repo.get_task_info.return_value = {"id": 1, "name": "测试任务", "task_type": "root"}

        # 模拟基础分解结果
        mock_base_decompose.return_value = {
            "success": True,
            "subtasks": [
                {"id": 2, "name": "子任务1", "type": "composite"},
                {"id": 3, "name": "子任务2", "type": "composite"},
            ],
        }

        # 模拟高质量评估
        mock_eval_quality.return_value = {
            "quality_score": 0.85,
            "needs_refinement": False,
            "issues": [],
            "suggestions": [],
        }

        result = decompose_task_with_evaluation(task_id=1, repo=mock_repo, quality_threshold=0.8, max_iterations=2)

        assert result["success"] == True
        assert result["meets_threshold"] == True
        assert result["best_quality_score"] == 0.85
        assert result["iterations_performed"] == 1  # 第一次就满足阈值

    @patch("app.services.decomposition_with_evaluation.base_decompose_task")
    @patch("app.services.decomposition_with_evaluation.evaluate_decomposition_quality")
    def test_decompose_with_evaluation_multiple_iterations(self, mock_eval_quality, mock_base_decompose):
        """测试多次迭代的分解过程"""
        mock_repo = Mock()
        mock_repo.get_task_info.return_value = {"id": 1, "name": "复杂任务", "task_type": "root"}

        # 模拟基础分解结果（两次都成功）
        mock_base_decompose.return_value = {
            "success": True,
            "subtasks": [{"id": 2, "name": "子任务", "type": "composite"}],
        }

        # 模拟质量评估（第一次不满足，第二次满足）
        mock_eval_quality.side_effect = [
            {"quality_score": 0.6, "needs_refinement": True},  # 第一次
            {"quality_score": 0.85, "needs_refinement": False},  # 第二次
        ]

        result = decompose_task_with_evaluation(task_id=1, repo=mock_repo, quality_threshold=0.8, max_iterations=2)

        assert result["success"] == True
        assert result["iterations_performed"] == 2
        assert result["best_quality_score"] == 0.85
        assert result["meets_threshold"] == True


class TestShouldDecomposeWithQualityCheck:
    """测试带质量检查的分解建议功能"""

    def test_should_decompose_high_complexity_task(self):
        """测试高复杂度任务的分解建议"""
        task = {"id": 1, "name": "构建完整系统架构", "depth": 0, "task_type": "root"}
        mock_repo = Mock()
        mock_repo.get_task_input_prompt.return_value = "设计端到端的架构平台"
        mock_repo.get_children.return_value = []

        recommendation = should_decompose_with_quality_check(task, mock_repo, min_complexity_score=0.6)

        assert recommendation["should_decompose"] == True
        assert recommendation["complexity"] == "high"
        assert recommendation["complexity_score"] >= 0.8
        assert len(recommendation["recommendations"]) > 0
        assert "建议分解" in " ".join(recommendation["recommendations"])

    def test_should_not_decompose_low_complexity_task(self):
        """测试低复杂度任务的分解建议"""
        task = {"id": 1, "name": "修复小bug", "depth": 0, "task_type": "composite"}
        mock_repo = Mock()
        mock_repo.get_task_input_prompt.return_value = "修复登录验证问题"
        mock_repo.get_children.return_value = []

        recommendation = should_decompose_with_quality_check(task, mock_repo, min_complexity_score=0.6)

        assert recommendation["should_decompose"] == False
        assert recommendation["complexity"] == "low"
        assert recommendation["complexity_score"] < 0.6
        assert "复杂度不足" in " ".join(recommendation["recommendations"])


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
