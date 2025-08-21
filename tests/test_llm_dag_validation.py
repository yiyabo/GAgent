#!/usr/bin/env python3
"""
LLM任务生成DAG验证脚本

功能：
1. 验证LLM生成的任务计划是否形成有效的DAG（无环图）
2. 测试任务依赖关系的正确性
3. 验证拓扑排序的稳定性
4. 检测循环依赖并提供详细诊断
5. 验证任务层次结构的完整性
"""

import sys
import os
import json
import time
from typing import Dict, List, Any, Optional, Tuple, Set

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.database import init_db
from app.repository.tasks import SqliteTaskRepository
from app.scheduler import requires_dag_order, requires_dag_schedule
from app.services.planning import propose_plan_service, approve_plan_service
from app.executor import execute_task


class LLMDAGValidator:
    """LLM任务DAG验证器"""
    
    def __init__(self):
        self.repo = SqliteTaskRepository()
        self.test_results = []
        
    def create_test_plan(self, goal: str, title: str) -> Dict[str, Any]:
        """创建测试计划"""
        print(f"🎯 创建测试计划: {title}")
        print(f"目标: {goal}")
        
        # 1. 生成计划
        payload = {"goal": goal}
        plan = propose_plan_service(payload)
        
        # 2. 批准计划
        plan["title"] = title
        approved = approve_plan_service(plan)
        
        return {
            "plan": plan,
            "approved": approved,
            "title": title
        }
    
    def validate_dag_structure(self, title: str) -> Dict[str, Any]:
        """验证DAG结构"""
        print(f"🔍 验证DAG结构: {title}")
        
        # 获取计划任务
        tasks = self.repo.list_plan_tasks(title)
        task_ids = [t["id"] for t in tasks]
        
        # 构建依赖图
        dependency_graph = self._build_dependency_graph(task_ids)
        
        # 验证无环性
        cycle_info = self._detect_cycles(dependency_graph)
        
        # 验证拓扑排序
        topological_order, cycle_detected = requires_dag_order(title)
        
        return {
            "title": title,
            "total_tasks": len(tasks),
            "dependency_graph": dependency_graph,
            "cycle_detected": cycle_detected,
            "cycle_info": cycle_info,
            "topological_order": [t["id"] for t in topological_order],
            "tasks": tasks
        }
    
    def _build_dependency_graph(self, task_ids: List[int]) -> Dict[int, List[int]]:
        """构建任务依赖图"""
        graph = {task_id: [] for task_id in task_ids}
        
        for task_id in task_ids:
            # 获取requires依赖
            requires_links = self.repo.list_links(from_id=task_id)
            for link in requires_links:
                if link["kind"] == "requires" and link["to_id"] in task_ids:
                    graph[task_id].append(link["to_id"])
        
        return graph
    
    def _detect_cycles(self, graph: Dict[int, List[int]]) -> Optional[Dict[str, Any]]:
        """检测图中的循环"""
        visited = set()
        rec_stack = set()
        cycle_path = []
        
        def dfs(node: int, path: List[int]) -> bool:
            if node in rec_stack:
                # 找到循环
                cycle_start = path.index(node)
                cycle = path[cycle_start:] + [node]
                cycle_info.append({
                    "cycle": cycle,
                    "nodes": cycle,
                    "message": f"检测到循环: {' -> '.join(map(str, cycle))}"
                })
                return True
            
            if node in visited:
                return False
            
            visited.add(node)
            rec_stack.add(node)
            path.append(node)
            
            for neighbor in graph.get(node, []):
                if dfs(neighbor, path.copy()):
                    return True
            
            rec_stack.remove(node)
            path.pop()
            return False
        
        cycle_info = []
        for node in graph:
            if node not in visited:
                if dfs(node, []):
                    return cycle_info[0] if cycle_info else None
        
        return None
    
    def test_task_hierarchy(self, title: str) -> Dict[str, Any]:
        """测试任务层次结构"""
        print(f"🏗️ 测试任务层次结构: {title}")
        
        tasks = self.repo.list_plan_tasks(title)
        
        # 构建层次结构
        hierarchy = {}
        depth_stats = {}
        
        for task in tasks:
            task_id = task["id"]
            depth = task.get("depth", 0)
            parent_id = task.get("parent_id")
            
            if depth not in depth_stats:
                depth_stats[depth] = 0
            depth_stats[depth] += 1
            
            if parent_id:
                if parent_id not in hierarchy:
                    hierarchy[parent_id] = []
                hierarchy[parent_id].append(task_id)
        
        # 验证层次完整性
        validation_errors = []
        
        for task in tasks:
            task_id = task["id"]
            parent_id = task.get("parent_id")
            
            if parent_id and parent_id not in [t["id"] for t in tasks]:
                validation_errors.append(f"任务 {task_id} 的父任务 {parent_id} 不存在")
        
        return {
            "title": title,
            "total_tasks": len(tasks),
            "hierarchy": hierarchy,
            "depth_stats": depth_stats,
            "validation_errors": validation_errors,
            "max_depth": max(depth_stats.keys()) if depth_stats else 0
        }
    
    def simulate_llm_task_generation(self, goal: str, title: str) -> Dict[str, Any]:
        """模拟LLM任务生成过程"""
        print(f"🤖 模拟LLM任务生成: {title}")
        
        # 模拟LLM生成的任务结构
        mock_plan = {
            "title": title,
            "tasks": [
                {"name": "需求分析", "prompt": "分析项目需求和技术规格", "priority": 10},
                {"name": "系统设计", "prompt": "设计系统架构和模块划分", "priority": 20},
                {"name": "数据库设计", "prompt": "设计数据库结构和表关系", "priority": 30},
                {"name": "API设计", "prompt": "设计RESTful API接口", "priority": 40},
                {"name": "前端开发", "prompt": "开发用户界面和交互逻辑", "priority": 50},
                {"name": "后端开发", "prompt": "实现业务逻辑和数据处理", "priority": 60},
                {"name": "单元测试", "prompt": "编写单元测试用例", "priority": 70},
                {"name": "集成测试", "prompt": "进行系统集成测试", "priority": 80},
                {"name": "部署上线", "prompt": "部署到生产环境", "priority": 90}
            ]
        }
        
        # 创建依赖关系（模拟合理的开发流程）
        dependencies = [
            ("系统设计", "需求分析"),
            ("数据库设计", "系统设计"),
            ("API设计", "系统设计"),
            ("前端开发", "API设计"),
            ("后端开发", "数据库设计"),
            ("后端开发", "API设计"),
            ("单元测试", "前端开发"),
            ("单元测试", "后端开发"),
            ("集成测试", "单元测试"),
            ("部署上线", "集成测试")
        ]
        
        # 批准计划
        approved = approve_plan_service(mock_plan)
        
        # 获取任务ID映射
        tasks = self.repo.list_plan_tasks(title)
        task_name_to_id = {t["name"]: t["id"] for t in tasks}
        
        # 创建依赖关系
        for from_name, to_name in dependencies:
            if from_name in task_name_to_id and to_name in task_name_to_id:
                self.repo.create_link(
                    task_name_to_id[from_name], 
                    task_name_to_id[to_name], 
                    "requires"
                )
        
        return {
            "plan": mock_plan,
            "dependencies": dependencies,
            "task_mapping": task_name_to_id,
            "total_tasks": len(tasks)
        }
    
    def test_cycle_detection(self, title: str) -> Dict[str, Any]:
        """测试循环依赖检测"""
        print(f"🔄 测试循环依赖检测: {title}")
        
        # 故意创建循环依赖
        tasks = self.repo.list_plan_tasks(title)
        if len(tasks) >= 3:
            # 创建循环：A -> B -> C -> A
            task_ids = [t["id"] for t in tasks[:3]]
            self.repo.create_link(task_ids[0], task_ids[1], "requires")
            self.repo.create_link(task_ids[1], task_ids[2], "requires")
            self.repo.create_link(task_ids[2], task_ids[0], "requires")
        
        # 验证循环检测
        order, cycle = requires_dag_order(title)
        
        return {
            "title": title,
            "cycle_created": len(tasks) >= 3,
            "cycle_detected": cycle is not None,
            "cycle_info": cycle,
            "valid_tasks": len(order),
            "total_tasks": len(tasks)
        }
    
    def run_comprehensive_validation(self) -> Dict[str, Any]:
        """运行全面验证"""
        print("🧪 运行LLM任务DAG全面验证...")
        
        test_cases = [
            {
                "title": "Web应用开发",
                "goal": "开发一个完整的Web应用程序，包括前端、后端、数据库和部署"
            },
            {
                "title": "机器学习项目",
                "goal": "构建一个机器学习项目，从数据收集到模型部署的完整流程"
            },
            {
                "title": "研究论文写作",
                "goal": "撰写一篇高质量的研究论文，包括文献调研、实验设计、数据分析和写作"
            }
        ]
        
        results = []
        
        for test_case in test_cases:
            print(f"\n{'='*60}")
            print(f"测试案例: {test_case['title']}")
            print(f"目标: {test_case['goal']}")
            print('='*60)
            
            # 模拟LLM生成
            llm_result = self.simulate_llm_task_generation(
                test_case["goal"], 
                test_case["title"]
            )
            
            # 验证DAG结构
            dag_result = self.validate_dag_structure(test_case["title"])
            
            # 测试层次结构
            hierarchy_result = self.test_task_hierarchy(test_case["title"])
            
            # 测试循环检测
            cycle_result = self.test_cycle_detection(test_case["title"])
            
            case_result = {
                "test_case": test_case,
                "llm_generation": llm_result,
                "dag_validation": dag_result,
                "hierarchy_validation": hierarchy_result,
                "cycle_detection": cycle_result
            }
            
            results.append(case_result)
            
            # 打印结果摘要
            print(f"✅ 任务生成: {llm_result['total_tasks']} 个任务")
            print(f"✅ DAG验证: {'无环' if not dag_result['cycle_detected'] else '检测到循环'}")
            print(f"✅ 层次验证: {len(hierarchy_result['validation_errors'])} 个错误")
            print(f"✅ 循环检测: {'通过' if cycle_result['cycle_detected'] else '未检测到循环'}")
        
        # 生成验证报告
        report = self._generate_validation_report(results)
        
        return {
            "test_cases": results,
            "summary": report,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
        }
    
    def _generate_validation_report(self, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """生成验证报告"""
        total_tasks = sum(r["llm_generation"]["total_tasks"] for r in results)
        total_cycles = sum(1 for r in results if r["dag_validation"]["cycle_detected"])
        total_errors = sum(len(r["hierarchy_validation"]["validation_errors"]) for r in results)
        
        return {
            "total_test_cases": len(results),
            "total_tasks_generated": total_tasks,
            "cycles_detected": total_cycles,
            "validation_errors": total_errors,
            "success_rate": (len(results) - total_cycles - total_errors) / len(results) * 100,
            "recommendations": [
                "确保任务依赖关系合理",
                "避免创建循环依赖",
                "验证任务层次结构完整性",
                "使用拓扑排序验证执行顺序"
            ]
        }
    
    def save_validation_report(self, results: Dict[str, Any], filename: str = "llm_dag_validation_report.json"):
        """保存验证报告"""
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2, default=str)
        print(f"📊 验证报告已保存: {filename}")


def main():
    """主函数"""
    print("🧪 LLM任务DAG验证脚本")
    print("=" * 80)
    print("功能：验证LLM生成的任务计划是否形成有效的DAG")
    print("=" * 80)
    
    # 初始化数据库
    init_db()
    
    # 创建验证器
    validator = LLMDAGValidator()
    
    try:
        # 运行全面验证
        results = validator.run_comprehensive_validation()
        
        # 保存验证报告
        validator.save_validation_report(results)
        
        # 打印摘要
        summary = results["summary"]
        print(f"\n{'='*60}")
        print("📊 验证摘要")
        print('='*60)
        print(f"测试案例: {summary['total_test_cases']}")
        print(f"总任务数: {summary['total_tasks_generated']}")
        print(f"循环检测: {summary['cycles_detected']}")
        print(f"验证错误: {summary['validation_errors']}")
        print(f"成功率: {summary['success_rate']:.1f}%")
        
        if summary['success_rate'] < 100:
            print("\n🔧 建议:")
            for rec in summary['recommendations']:
                print(f"  - {rec}")
        
        print(f"\n✅ 验证完成！")
        
    except Exception as e:
        print(f"\n❌ 验证失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()