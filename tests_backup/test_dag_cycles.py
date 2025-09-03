#!/usr/bin/env python3
"""
DAG循环检测测试脚本

功能：
1. 测试任务依赖关系的循环检测
2. 验证拓扑排序的正确性
3. 提供详细的循环诊断信息
"""

import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.database import init_db
from app.repository.tasks import SqliteTaskRepository
from app.scheduler import requires_dag_order, requires_dag_schedule


class DAGCycleTester:
    """DAG循环检测测试器"""

    def __init__(self):
        self.repo = SqliteTaskRepository()

    def create_test_scenario(self, name: str, structure: Dict[str, Any]) -> str:
        """创建测试场景"""
        print(f"🎯 创建测试场景: {name}")

        # 创建任务
        task_map = {}
        for task_name, config in structure["tasks"].items():
            task_id = self.repo.create_task(
                f"[{name}] {task_name}", status="pending", priority=config.get("priority", 50)
            )
            task_map[task_name] = task_id

            # 添加任务描述
            if "description" in config:
                self.repo.upsert_task_input(task_id, config["description"])

        # 创建依赖关系
        for from_task, to_task in structure["dependencies"]:
            if from_task in task_map and to_task in task_map:
                self.repo.create_link(task_map[from_task], task_map[to_task], "requires")

        return name

    def test_dag_validity(self, scenario_name: str) -> Dict[str, Any]:
        """测试DAG有效性"""
        print(f"🔍 测试DAG有效性: {scenario_name}")

        # 获取任务
        tasks = self.repo.list_plan_tasks(scenario_name)

        # 验证DAG排序
        order, cycle = requires_dag_order(scenario_name)

        # 构建依赖图用于详细分析
        graph = self._build_dependency_graph(tasks)

        # 分析连通性
        connectivity = self._analyze_connectivity(graph)

        return {
            "scenario": scenario_name,
            "total_tasks": len(tasks),
            "valid_dag": cycle is None,
            "cycle_info": cycle,
            "topological_order": [t["id"] for t in order],
            "task_names": [t["name"] for t in order],
            "graph": graph,
            "connectivity": connectivity,
        }

    def _build_dependency_graph(self, tasks: List[Dict[str, Any]]) -> Dict[int, List[int]]:
        """构建依赖图"""
        task_ids = [t["id"] for t in tasks]
        graph = {task_id: [] for task_id in task_ids}

        for task in tasks:
            task_id = task["id"]
            # 获取requires依赖
            links = self.repo.list_links(from_id=task_id)
            for link in links:
                if link["kind"] == "requires" and link["to_id"] in task_ids:
                    graph[task_id].append(link["to_id"])

        return graph

    def _analyze_connectivity(self, graph: Dict[int, List[int]]) -> Dict[str, Any]:
        """分析图的连通性"""
        if not graph:
            return {"is_connected": False, "components": 0, "isolated_nodes": []}

        # 找出所有节点
        all_nodes = set(graph.keys())

        # 找出所有连接的节点（包括入边和出边）
        connected_nodes = set()
        for node, neighbors in graph.items():
            connected_nodes.add(node)
            connected_nodes.update(neighbors)

        # 找出孤立节点
        isolated_nodes = all_nodes - connected_nodes

        # 使用DFS找出连通分量
        visited = set()
        components = 0

        def dfs(node: int):
            if node in visited:
                return
            visited.add(node)
            for neighbor in graph.get(node, []):
                dfs(neighbor)
            # 也检查反向边
            for n in all_nodes:
                if node in graph.get(n, []):
                    dfs(n)

        for node in all_nodes:
            if node not in visited:
                dfs(node)
                components += 1

        return {
            "is_connected": components == 1,
            "components": components,
            "isolated_nodes": list(isolated_nodes),
            "total_nodes": len(all_nodes),
        }

    def run_standard_test_cases(self) -> List[Dict[str, Any]]:
        """运行标准测试用例"""
        test_cases = [
            {
                "name": "线性依赖",
                "structure": {
                    "tasks": {
                        "A": {"priority": 10, "description": "需求分析"},
                        "B": {"priority": 20, "description": "系统设计"},
                        "C": {"priority": 30, "description": "开发实现"},
                        "D": {"priority": 40, "description": "测试验证"},
                    },
                    "dependencies": [("B", "A"), ("C", "B"), ("D", "C")],
                },
            },
            {
                "name": "树形结构",
                "structure": {
                    "tasks": {
                        "A": {"priority": 10, "description": "项目启动"},
                        "B": {"priority": 20, "description": "前端开发"},
                        "C": {"priority": 20, "description": "后端开发"},
                        "D": {"priority": 30, "description": "UI设计"},
                        "E": {"priority": 30, "description": "API开发"},
                        "F": {"priority": 40, "description": "集成测试"},
                    },
                    "dependencies": [("B", "A"), ("C", "A"), ("D", "B"), ("E", "C"), ("F", "D"), ("F", "E")],
                },
            },
            {
                "name": "复杂网络",
                "structure": {
                    "tasks": {
                        "A": {"priority": 10, "description": "需求收集"},
                        "B": {"priority": 15, "description": "架构设计"},
                        "C": {"priority": 20, "description": "数据库设计"},
                        "D": {"priority": 25, "description": "API规范"},
                        "E": {"priority": 30, "description": "前端开发"},
                        "F": {"priority": 30, "description": "后端开发"},
                        "G": {"priority": 35, "description": "单元测试"},
                        "H": {"priority": 40, "description": "集成测试"},
                    },
                    "dependencies": [
                        ("B", "A"),
                        ("C", "B"),
                        ("D", "B"),
                        ("E", "D"),
                        ("F", "C"),
                        ("F", "D"),
                        ("G", "E"),
                        ("G", "F"),
                        ("H", "G"),
                    ],
                },
            },
            {
                "name": "循环依赖",
                "structure": {
                    "tasks": {
                        "A": {"priority": 10, "description": "任务A"},
                        "B": {"priority": 20, "description": "任务B"},
                        "C": {"priority": 30, "description": "任务C"},
                    },
                    "dependencies": [("B", "A"), ("C", "B"), ("A", "C")],  # 创建循环
                },
            },
        ]

        results = []

        for test_case in test_cases:
            # 创建测试场景
            scenario_name = self.create_test_scenario(test_case["name"], test_case["structure"])

            # 测试DAG有效性
            result = self.test_dag_validity(scenario_name)

            results.append(result)

            # 打印结果
            print(f"\n📊 测试结果: {scenario_name}")
            print(f"   总任务数: {result['total_tasks']}")
            print(f"   DAG有效: {'✅ 是' if result['valid_dag'] else '❌ 否'}")

            if result["cycle_info"]:
                print(f"   循环信息: {result['cycle_info']['message']}")
            else:
                print(f"   执行顺序: {' -> '.join(result['task_names'])}")

        return results

    def test_edge_cases(self) -> List[Dict[str, Any]]:
        """测试边界情况"""
        edge_cases = [
            {"name": "空图", "structure": {"tasks": {}, "dependencies": []}},
            {"name": "单节点", "structure": {"tasks": {"A": {"priority": 10}}, "dependencies": []}},
            {
                "name": "孤立节点",
                "structure": {
                    "tasks": {"A": {"priority": 10}, "B": {"priority": 20}, "C": {"priority": 30}},
                    "dependencies": [],
                },
            },
        ]

        results = []

        for case in edge_cases:
            scenario_name = self.create_test_scenario(case["name"], case["structure"])
            result = self.test_dag_validity(scenario_name)
            results.append(result)

            print(f"\n🔍 边界测试: {scenario_name}")
            print(f"   结果: {'✅ 通过' if result['valid_dag'] else '❌ 失败'}")

        return results

    def generate_report(self, standard_results: List[Dict], edge_results: List[Dict]) -> Dict[str, Any]:
        """生成测试报告"""
        all_results = standard_results + edge_results

        total_tests = len(all_results)
        valid_dags = sum(1 for r in all_results if r["valid_dag"])
        cycles_detected = total_tests - valid_dags

        report = {
            "summary": {
                "total_tests": total_tests,
                "valid_dags": valid_dags,
                "cycles_detected": cycles_detected,
                "success_rate": (valid_dags / total_tests * 100) if total_tests > 0 else 0,
            },
            "standard_tests": standard_results,
            "edge_tests": edge_results,
            "recommendations": [
                "始终验证任务依赖关系",
                "使用拓扑排序确保执行顺序",
                "实现循环检测机制",
                "提供清晰的错误诊断",
                "支持任务重排和修复",
            ],
        }

        return report

    def cleanup_test_data(self):
        """清理测试数据"""
        print("🧹 清理测试数据...")
        # 这里可以添加清理逻辑
        pass


def main():
    """主函数"""
    print("🧪 DAG循环检测测试脚本")
    print("=" * 60)

    # 初始化
    init_db()
    tester = DAGCycleTester()

    try:
        # 运行标准测试
        print("\n📊 运行标准测试用例...")
        standard_results = tester.run_standard_test_cases()

        # 运行边界测试
        print("\n🔍 运行边界测试用例...")
        edge_results = tester.test_edge_cases()

        # 生成报告
        report = tester.generate_report(standard_results, edge_results)

        # 保存报告
        with open("dag_test_report.json", "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        # 打印摘要
        print(f"\n{'='*60}")
        print("📊 测试摘要")
        print("=" * 60)
        summary = report["summary"]
        print(f"总测试数: {summary['total_tests']}")
        print(f"有效DAG: {summary['valid_dags']}")
        print(f"循环检测: {summary['cycles_detected']}")
        print(f"成功率: {summary['success_rate']:.1f}%")

        if summary["success_rate"] < 100:
            print("\n🔧 建议:")
            for rec in report["recommendations"]:
                print(f"  - {rec}")

        print(f"\n✅ 测试完成！报告已保存到 dag_test_report.json")

    except Exception as e:
        print(f"❌ 测试失败: {e}")
        import traceback

        traceback.print_exc()
    finally:
        tester.cleanup_test_data()


if __name__ == "__main__":
    main()
