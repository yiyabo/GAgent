"""
结构对比学习训练数据构造测试

测试基于任务图结构关系的对比学习训练数据生成功能

Author: Cascade AI
Date: 2025-08-21
"""

import json
import os
import tempfile
from unittest.mock import Mock, patch

import pytest

from app.services.contrastive_learning import (
    ContrastiveSample,
    StructureContrastiveLearningDataGenerator,
    get_contrastive_learning_generator,
)


class TestStructureContrastiveLearningDataGenerator:
    """结构对比学习数据生成器测试"""

    @pytest.fixture
    def mock_repository(self):
        """模拟任务仓库"""
        repo = Mock()

        # 模拟任务数据
        mock_tasks = [Mock(id=1), Mock(id=2), Mock(id=3), Mock(id=4), Mock(id=5)]
        repo.get_all_tasks.return_value = mock_tasks

        # 模拟任务信息
        task_infos = {
            1: {
                "id": 1,
                "title": "任务1标题",
                "description": "任务1描述",
                "requires": [2],
                "refers": [3],
                "parent_id": None,
            },
            2: {
                "id": 2,
                "title": "任务2标题",
                "description": "任务2描述",
                "requires": [],
                "refers": [],
                "parent_id": 1,
            },
            3: {
                "id": 3,
                "title": "任务3标题",
                "description": "任务3描述",
                "requires": [],
                "refers": [4],
                "parent_id": 1,
            },
            4: {
                "id": 4,
                "title": "任务4标题",
                "description": "任务4描述",
                "requires": [],
                "refers": [],
                "parent_id": None,
            },
            5: {
                "id": 5,
                "title": "任务5标题",
                "description": "任务5描述",
                "requires": [],
                "refers": [],
                "parent_id": None,
            },
        }

        def get_task_info_side_effect(task_id):
            return task_infos.get(task_id)

        repo.get_task_info.side_effect = get_task_info_side_effect
        return repo

    @pytest.fixture
    def generator(self, mock_repository):
        """创建数据生成器实例"""
        with patch("app.services.contrastive_learning.default_repo", mock_repository):
            return StructureContrastiveLearningDataGenerator()

    def test_generator_initialization(self, generator):
        """测试生成器初始化"""
        assert generator is not None
        assert hasattr(generator, "relation_weights")
        assert hasattr(generator, "min_positive_score")
        assert hasattr(generator, "max_negative_score")
        assert hasattr(generator, "samples_per_task")

    def test_build_task_graph(self, generator):
        """测试任务图构建"""
        task_ids = [1, 2, 3, 4, 5]
        task_graph = generator._build_task_graph(task_ids)

        assert len(task_graph) == 5
        assert 1 in task_graph
        assert task_graph[1]["requires"] == [2]
        assert task_graph[1]["refers"] == [3]
        assert task_graph[1]["parent_id"] is None

        # 检查父子关系构建
        assert 2 in task_graph[1]["children"]
        assert 3 in task_graph[1]["children"]

    def test_get_task_texts(self, generator):
        """测试任务文本获取"""
        task_ids = [1, 2, 3]
        task_texts = generator._get_task_texts(task_ids)

        assert len(task_texts) == 3
        assert "任务1标题" in task_texts[1]
        assert "任务1描述" in task_texts[1]
        assert "任务2标题" in task_texts[2]

    def test_compute_relation_scores(self, generator):
        """测试关系分数计算"""
        task_ids = [1, 2, 3, 4, 5]
        task_graph = generator._build_task_graph(task_ids)

        # 计算任务1与其他任务的关系分数
        scores = generator._compute_relation_scores(1, task_graph)

        assert len(scores) == 4  # 除了任务1自己
        assert scores[2] > 0.5  # 任务2是任务1的依赖，应该有高分数
        assert scores[3] > 0.5  # 任务3被任务1引用，应该有高分数
        assert scores[4] < scores[3]  # 任务4与任务1关系较远

    def test_compute_path_distance(self, generator):
        """测试路径距离计算"""
        task_ids = [1, 2, 3, 4, 5]
        task_graph = generator._build_task_graph(task_ids)

        # 测试直接连接
        distance = generator._compute_path_distance(1, 2, task_graph)
        assert distance == 1

        # 测试自己到自己
        distance = generator._compute_path_distance(1, 1, task_graph)
        assert distance == 0

        # 测试无法到达的情况
        distance = generator._compute_path_distance(1, 5, task_graph)
        assert distance == -1 or distance > 0  # 取决于图的连通性

    def test_determine_relation_type(self, generator):
        """测试关系类型确定"""
        task_ids = [1, 2, 3, 4]
        task_graph = generator._build_task_graph(task_ids)

        # 测试依赖关系
        relation = generator._determine_relation_type(1, 2, task_graph)
        assert relation == "requires"

        # 测试引用关系
        relation = generator._determine_relation_type(1, 3, task_graph)
        assert relation == "refers"

        # 测试子关系
        relation = generator._determine_relation_type(2, 1, task_graph)
        assert relation == "parent"

    def test_generate_samples_for_task(self, generator):
        """测试单个任务的样本生成"""
        task_ids = [1, 2, 3, 4, 5]
        task_graph = generator._build_task_graph(task_ids)
        task_texts = generator._get_task_texts(task_ids)

        samples = generator._generate_samples_for_task(1, task_graph, task_texts)

        assert isinstance(samples, list)
        for sample in samples:
            assert isinstance(sample, ContrastiveSample)
            assert sample.anchor_id == 1
            assert sample.anchor_text is not None
            assert sample.positive_id != sample.negative_id
            assert sample.similarity_score >= 0

    def test_generate_training_data(self, generator):
        """测试训练数据生成"""
        task_ids = [1, 2, 3, 4, 5]
        samples = generator.generate_training_data(task_ids, max_samples=10)

        assert isinstance(samples, list)
        assert len(samples) <= 10

        for sample in samples:
            assert isinstance(sample, ContrastiveSample)
            assert sample.anchor_id in task_ids
            assert sample.positive_id in task_ids
            assert sample.negative_id in task_ids
            assert sample.anchor_id != sample.positive_id
            assert sample.anchor_id != sample.negative_id

    def test_generate_training_data_empty_tasks(self, generator):
        """测试空任务列表的处理"""
        samples = generator.generate_training_data([], max_samples=10)
        assert samples == []

    def test_export_json(self, generator):
        """测试JSON格式导出"""
        # 创建测试样本
        samples = [
            ContrastiveSample(
                anchor_id=1,
                anchor_text="anchor text",
                positive_id=2,
                positive_text="positive text",
                negative_id=3,
                negative_text="negative text",
                relation_type="requires",
                similarity_score=0.8,
            )
        ]

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            temp_path = f.name

        try:
            generator.export_training_data(samples, temp_path, format="json")

            # 验证文件内容
            with open(temp_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            assert len(data) == 1
            assert data[0]["anchor_id"] == 1
            assert data[0]["anchor_text"] == "anchor text"
            assert data[0]["relation_type"] == "requires"
            assert data[0]["similarity_score"] == 0.8
        finally:
            os.unlink(temp_path)

    def test_export_jsonl(self, generator):
        """测试JSONL格式导出"""
        samples = [
            ContrastiveSample(
                anchor_id=1,
                anchor_text="anchor text",
                positive_id=2,
                positive_text="positive text",
                negative_id=3,
                negative_text="negative text",
                relation_type="requires",
                similarity_score=0.8,
            )
        ]

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            temp_path = f.name

        try:
            generator.export_training_data(samples, temp_path, format="jsonl")

            # 验证文件内容
            with open(temp_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            assert len(lines) == 1
            data = json.loads(lines[0])
            assert data["anchor_id"] == 1
            assert data["relation_type"] == "requires"
        finally:
            os.unlink(temp_path)

    def test_export_csv(self, generator):
        """测试CSV格式导出"""
        samples = [
            ContrastiveSample(
                anchor_id=1,
                anchor_text="anchor text",
                positive_id=2,
                positive_text="positive text",
                negative_id=3,
                negative_text="negative text",
                relation_type="requires",
                similarity_score=0.8,
            )
        ]

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            temp_path = f.name

        try:
            generator.export_training_data(samples, temp_path, format="csv")

            # 验证文件内容
            with open(temp_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            assert len(lines) >= 2  # 表头 + 数据行
            assert "anchor_id" in lines[0]  # 表头
            assert "1," in lines[1]  # 数据行
        finally:
            os.unlink(temp_path)

    def test_export_unsupported_format(self, generator):
        """测试不支持的导出格式"""
        samples = []

        with pytest.raises(ValueError, match="不支持的导出格式"):
            generator.export_training_data(samples, "test.txt", format="txt")

    def test_get_statistics(self, generator):
        """测试统计信息获取"""
        samples = [
            ContrastiveSample(
                anchor_id=1,
                anchor_text="text1",
                positive_id=2,
                positive_text="text2",
                negative_id=3,
                negative_text="text3",
                relation_type="requires",
                similarity_score=0.8,
            ),
            ContrastiveSample(
                anchor_id=1,
                anchor_text="text1",
                positive_id=4,
                positive_text="text4",
                negative_id=5,
                negative_text="text5",
                relation_type="refers",
                similarity_score=0.6,
            ),
        ]

        stats = generator.get_statistics(samples)

        assert stats["total_samples"] == 2
        assert stats["relation_distribution"]["requires"] == 1
        assert stats["relation_distribution"]["refers"] == 1
        assert stats["similarity_stats"]["mean"] == 0.7
        assert stats["similarity_stats"]["min"] == 0.6
        assert stats["similarity_stats"]["max"] == 0.8
        assert stats["unique_anchors"] == 1
        assert stats["unique_positives"] == 2
        assert stats["unique_negatives"] == 2

    def test_get_statistics_empty_samples(self, generator):
        """测试空样本列表的统计"""
        stats = generator.get_statistics([])
        assert stats == {}

    def test_contrastive_sample_dataclass(self):
        """测试ContrastiveSample数据类"""
        sample = ContrastiveSample(
            anchor_id=1,
            anchor_text="anchor",
            positive_id=2,
            positive_text="positive",
            negative_id=3,
            negative_text="negative",
            relation_type="requires",
            similarity_score=0.8,
        )

        assert sample.anchor_id == 1
        assert sample.anchor_text == "anchor"
        assert sample.positive_id == 2
        assert sample.positive_text == "positive"
        assert sample.negative_id == 3
        assert sample.negative_text == "negative"
        assert sample.relation_type == "requires"
        assert sample.similarity_score == 0.8


class TestContrastiveLearningService:
    """对比学习服务测试"""

    def test_get_contrastive_learning_generator_singleton(self):
        """测试单例模式"""
        generator1 = get_contrastive_learning_generator()
        generator2 = get_contrastive_learning_generator()

        assert generator1 is generator2
        assert isinstance(generator1, StructureContrastiveLearningDataGenerator)

    def test_generator_with_real_repository(self):
        """测试与真实仓库的集成"""
        generator = get_contrastive_learning_generator()

        # 测试生成器可以正常工作
        assert generator is not None
        assert hasattr(generator, "repository")

        # 测试空任务列表的处理
        samples = generator.generate_training_data(task_ids=[], max_samples=5)
        assert isinstance(samples, list)
        assert len(samples) == 0


if __name__ == "__main__":
    pytest.main([__file__])
