#!/usr/bin/env python3
"""
任务DAG验证脚本 - 自动创建任务、展示DAG结构并按postorder顺序执行

功能：
1. 自动创建层次化任务结构
2. 可视化展示任务DAG
3. 按照postorder顺序执行任务（子任务优先于父任务）
4. 对比不同调度算法的执行顺序
"""

import os
import sys
import time
from typing import Any, Dict, List, Optional

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.database import init_db
from app.execution.executors import execute_task
from app.repository.tasks import SqliteTaskRepository
from app.scheduler import bfs_schedule, postorder_schedule, requires_dag_schedule


class TaskDAGValidator:
    """任务DAG验证器"""

    def __init__(self):
        self.repo = SqliteTaskRepository()
        self.created_tasks = {}

    def create_sample_project(self) -> Dict[str, int]:
        """创建示例项目任务层次结构"""
        print("🚀 创建噬菌体抗药性研究项目任务层次结构...")

        # 清理现有任务（可选）
        self._cleanup_existing_tasks()

        # 创建项目根任务
        project_id = self.repo.create_task("噬菌体抗药性研究项目", priority=10, task_type="composite")
        self.repo.upsert_task_input(project_id, "整合所有研究模块，完成噬菌体对抗细菌耐药性机制的综合研究报告")

        # 文献调研模块
        literature_id = self.repo.create_task("文献调研模块", priority=20, parent_id=project_id, task_type="composite")
        self.repo.upsert_task_input(literature_id, "整合文献调研结果，建立研究理论基础和背景知识体系")

        # 文献调研子任务
        phage_review_id = self.repo.create_task(
            "噬菌体生物学文献综述", priority=21, parent_id=literature_id, task_type="atomic"
        )
        self.repo.upsert_task_input(phage_review_id, "系统性回顾噬菌体的生物学特性、分类、生命周期和宿主特异性相关文献")

        resistance_review_id = self.repo.create_task(
            "细菌抗药性机制文献分析", priority=22, parent_id=literature_id, task_type="atomic"
        )
        self.repo.upsert_task_input(resistance_review_id, "分析细菌耐药性产生机制、传播途径和当前治疗挑战的相关研究")

        # 实验设计模块
        experiment_id = self.repo.create_task("实验设计模块", priority=30, parent_id=project_id, task_type="composite")
        self.repo.upsert_task_input(experiment_id, "整合实验设计方案，建立完整的研究方法学框架")

        # 实验设计子任务
        strain_selection_id = self.repo.create_task(
            "菌株筛选与培养", priority=31, parent_id=experiment_id, task_type="atomic"
        )
        self.repo.upsert_task_input(strain_selection_id, "筛选目标耐药细菌菌株，建立标准化培养条件和保存方法")

        phage_isolation_id = self.repo.create_task(
            "噬菌体分离纯化", priority=32, parent_id=experiment_id, task_type="atomic"
        )
        self.repo.upsert_task_input(phage_isolation_id, "从环境样本中分离特异性噬菌体，进行纯化和活性检测")

        protocol_design_id = self.repo.create_task(
            "实验方案设计", priority=33, parent_id=experiment_id, task_type="atomic"
        )
        self.repo.upsert_task_input(
            protocol_design_id, "设计噬菌体-细菌相互作用实验方案，包括感染效率、裂解动力学等测试"
        )

        # 数据分析模块
        analysis_id = self.repo.create_task("数据分析模块", priority=40, parent_id=project_id, task_type="composite")
        self.repo.upsert_task_input(analysis_id, "整合所有实验数据，进行统计分析和生物信息学解析")

        # 数据分析子任务
        genomic_analysis_id = self.repo.create_task(
            "基因组学分析", priority=41, parent_id=analysis_id, task_type="atomic"
        )
        self.repo.upsert_task_input(genomic_analysis_id, "对噬菌体和细菌基因组进行测序分析，识别抗性基因和毒力因子")

        statistical_analysis_id = self.repo.create_task(
            "统计学分析", priority=42, parent_id=analysis_id, task_type="atomic"
        )
        self.repo.upsert_task_input(statistical_analysis_id, "对实验数据进行统计学检验，评估噬菌体治疗效果的显著性")

        # 成果输出模块
        output_id = self.repo.create_task("成果输出模块", priority=50, parent_id=project_id, task_type="composite")
        self.repo.upsert_task_input(output_id, "整合研究成果，完成学术论文撰写和研究报告")

        # 成果输出子任务
        paper_writing_id = self.repo.create_task("学术论文撰写", priority=51, parent_id=output_id, task_type="atomic")
        self.repo.upsert_task_input(paper_writing_id, "撰写高质量学术论文，阐述噬菌体抗药性研究的发现和意义")

        presentation_id = self.repo.create_task("学术会议报告", priority=52, parent_id=output_id, task_type="atomic")
        self.repo.upsert_task_input(presentation_id, "准备学术会议演讲材料，展示研究成果和临床应用前景")

        # 保存任务ID映射
        self.created_tasks = {
            "project": project_id,
            "literature": literature_id,
            "phage_review": phage_review_id,
            "resistance_review": resistance_review_id,
            "experiment": experiment_id,
            "strain_selection": strain_selection_id,
            "phage_isolation": phage_isolation_id,
            "protocol_design": protocol_design_id,
            "analysis": analysis_id,
            "genomic_analysis": genomic_analysis_id,
            "statistical_analysis": statistical_analysis_id,
            "output": output_id,
            "paper_writing": paper_writing_id,
            "presentation": presentation_id,
        }

        print(f"✅ 成功创建了 {len(self.created_tasks)} 个任务")
        return self.created_tasks

    def _cleanup_existing_tasks(self):
        """清理现有的测试任务"""
        try:
            # 这里可以添加清理逻辑，比如删除特定前缀的任务
            pass
        except Exception as e:
            print(f"清理任务时出错: {e}")

    def display_dag_structure(self):
        """展示任务DAG结构"""
        print("\n" + "=" * 80)
        print("📊 任务DAG结构可视化")
        print("=" * 80)

        # 获取所有pending任务
        tasks = self.repo.list_pending_full()

        # 按层次结构组织任务
        tasks_by_depth = {}
        for task in tasks:
            depth = task.get("depth", 0)
            if depth not in tasks_by_depth:
                tasks_by_depth[depth] = []
            tasks_by_depth[depth].append(task)

        # 显示层次结构
        for depth in sorted(tasks_by_depth.keys()):
            level_tasks = sorted(tasks_by_depth[depth], key=lambda x: x.get("priority", 100))

            print(f"\n📋 层级 {depth}:")
            for task in level_tasks:
                indent = "  " * (depth + 1)
                task_id = task.get("id")
                name = task.get("name", "Unknown")
                priority = task.get("priority", "N/A")
                task_type = task.get("task_type", "atomic")

                # 获取子任务数量
                children = self.repo.get_children(task_id)
                child_count = len(children)
                child_info = f" ({child_count} 子任务)" if child_count > 0 else ""

                type_icon = "🏗️" if task_type == "composite" else "⚡"
                print(f"{indent}{type_icon} [{task_id:2d}] {name} (优先级: {priority}){child_info}")

        # 显示依赖关系统计
        print(f"\n📈 DAG统计信息:")
        print(f"  - 总任务数: {len(tasks)}")
        print(f"  - 层级深度: {max(tasks_by_depth.keys()) + 1}")

        # 统计任务类型
        composite_count = sum(1 for t in tasks if t.get("task_type") == "composite")
        atomic_count = len(tasks) - composite_count
        print(f"  - 复合任务: {composite_count}")
        print(f"  - 原子任务: {atomic_count}")

    def compare_scheduling_algorithms(self):
        """对比不同调度算法的执行顺序"""
        print("\n" + "=" * 80)
        print("🔄 调度算法对比")
        print("=" * 80)

        algorithms = [
            ("BFS调度 (广度优先)", bfs_schedule),
            ("后序遍历调度 (子任务优先)", postorder_schedule),
            ("DAG依赖调度", requires_dag_schedule),
        ]

        for name, scheduler_func in algorithms:
            print(f"\n📋 {name}:")
            print("-" * 60)

            try:
                tasks = list(scheduler_func())
                for i, task in enumerate(tasks, 1):
                    task_id = task.get("id")
                    task_name = task.get("name", "Unknown")
                    priority = task.get("priority", "N/A")
                    depth = task.get("depth", 0)
                    dependencies = task.get("dependencies", [])

                    indent = "  " * depth
                    deps_info = f" [依赖: {len(dependencies)}个子任务]" if dependencies else " [无子任务依赖]"

                    print(f"  {i:2d}. {indent}{task_name} (ID:{task_id}, 优先级:{priority}){deps_info}")

                print(f"\n  总计: {len(tasks)} 个任务")

            except Exception as e:
                print(f"  ❌ 调度失败: {e}")

    def execute_postorder_schedule(self, use_context: bool = False, show_details: bool = True):
        """按照postorder顺序执行任务"""
        print("\n" + "=" * 80)
        print("🚀 执行后序遍历调度 (子任务优先执行)")
        print("=" * 80)

        results = []
        start_time = time.time()

        try:
            for i, task in enumerate(postorder_schedule(), 1):
                task_id = task.get("id")
                name = task.get("name", "Unknown")
                priority = task.get("priority", "N/A")
                depth = task.get("depth", 0)
                dependencies = task.get("dependencies", [])

                if show_details:
                    indent = "  " * depth
                    deps_str = f" [等待 {len(dependencies)} 个子任务完成]" if dependencies else " [无依赖]"
                    print(f"\n{i:2d}. 🔄 执行: {indent}{name}")
                    print(f"     ID: {task_id}, 优先级: {priority}, 深度: {depth}{deps_str}")

                    if dependencies:
                        print(f"     子任务依赖: {dependencies}")

                # 执行任务
                try:
                    if show_details:
                        print(f"     状态: 执行中...")

                    status = execute_task(task, use_context=use_context)
                    self.repo.update_task_status(task_id, status)

                    if show_details:
                        if status == "done":
                            # 获取任务输出预览
                            output = self.repo.get_task_output_content(task_id)
                            if output:
                                preview = output[:100].replace("\n", " ")
                                print(f"     ✅ 完成: {preview}...")
                            else:
                                print(f"     ✅ 完成 (无输出)")
                        else:
                            print(f"     ❌ 失败: {status}")

                    results.append(
                        {"id": task_id, "name": name, "status": status, "depth": depth, "dependencies": dependencies}
                    )

                    # 短暂延迟以便观察
                    if show_details:
                        time.sleep(0.3)

                except Exception as e:
                    error_msg = str(e)
                    if show_details:
                        print(f"     ❌ 执行错误: {error_msg}")

                    self.repo.update_task_status(task_id, "failed")
                    results.append(
                        {
                            "id": task_id,
                            "name": name,
                            "status": "failed",
                            "error": error_msg,
                            "depth": depth,
                            "dependencies": dependencies,
                        }
                    )

        except Exception as e:
            print(f"\n❌ 调度器错误: {e}")
            return results

        # 执行结果统计
        end_time = time.time()
        execution_time = end_time - start_time

        print(f"\n" + "=" * 60)
        print("📊 执行结果统计")
        print("=" * 60)

        total_tasks = len(results)
        completed = sum(1 for r in results if r["status"] == "done")
        failed = sum(1 for r in results if r["status"] == "failed")

        print(f"总任务数: {total_tasks}")
        print(f"成功完成: {completed}")
        print(f"执行失败: {failed}")
        print(f"成功率: {(completed/total_tasks*100):.1f}%" if total_tasks > 0 else "N/A")
        print(f"执行时间: {execution_time:.2f}秒")

        # 按深度统计
        depth_stats = {}
        for result in results:
            depth = result.get("depth", 0)
            if depth not in depth_stats:
                depth_stats[depth] = {"total": 0, "completed": 0}
            depth_stats[depth]["total"] += 1
            if result["status"] == "done":
                depth_stats[depth]["completed"] += 1

        print(f"\n按层级统计:")
        for depth in sorted(depth_stats.keys()):
            stats = depth_stats[depth]
            rate = (stats["completed"] / stats["total"] * 100) if stats["total"] > 0 else 0
            print(f"  层级 {depth}: {stats['completed']}/{stats['total']} ({rate:.1f}%)")

        return results

    def reset_all_tasks(self):
        """重置所有任务状态为pending"""
        print("\n🔄 重置所有任务状态...")

        try:
            tasks = self.repo.list_all_tasks()
            for task in tasks:
                task_id = task.get("id")
                if task_id:
                    self.repo.update_task_status(task_id, "pending")

            print(f"✅ 成功重置 {len(tasks)} 个任务状态为pending")

        except Exception as e:
            print(f"❌ 重置失败: {e}")

    def validate_postorder_properties(self):
        """验证后序遍历的特性"""
        print("\n" + "=" * 80)
        print("🔍 验证后序遍历特性")
        print("=" * 80)

        tasks = list(postorder_schedule())
        task_positions = {task.get("id"): i for i, task in enumerate(tasks)}

        violations = []

        for i, task in enumerate(tasks):
            task_id = task.get("id")
            dependencies = task.get("dependencies", [])

            # 检查所有子任务是否在当前任务之前执行
            for dep_id in dependencies:
                if dep_id in task_positions:
                    dep_position = task_positions[dep_id]
                    if dep_position >= i:  # 子任务在父任务之后
                        violations.append(
                            {
                                "parent": task_id,
                                "parent_name": task.get("name"),
                                "child": dep_id,
                                "parent_pos": i,
                                "child_pos": dep_position,
                            }
                        )

        if violations:
            print("❌ 发现后序遍历违规:")
            for v in violations:
                print(f"  - 父任务 {v['parent']} ({v['parent_name']}) 在位置 {v['parent_pos']}")
                print(f"    但其子任务 {v['child']} 在位置 {v['child_pos']}")
        else:
            print("✅ 后序遍历特性验证通过!")
            print("  - 所有子任务都在其父任务之前执行")
            print("  - 执行顺序符合依赖关系要求")

        return len(violations) == 0


def main():
    """主函数"""
    print("🎯 任务DAG验证脚本")
    print("=" * 80)
    print("功能: 自动创建任务、展示DAG、验证postorder调度")
    print("=" * 80)

    # 初始化
    init_db()
    validator = TaskDAGValidator()

    try:
        # 1. 创建示例项目
        task_ids = validator.create_sample_project()

        # 2. 展示DAG结构
        validator.display_dag_structure()

        # 3. 对比调度算法
        validator.compare_scheduling_algorithms()

        # 4. 验证后序遍历特性
        validator.validate_postorder_properties()

        # 5. 执行后序遍历调度
        results = validator.execute_postorder_schedule(use_context=False, show_details=True)

        print(f"\n🎉 验证完成!")
        print(f"创建任务: {len(task_ids)} 个")
        print(f"执行任务: {len(results)} 个")
        print(f"成功率: {sum(1 for r in results if r['status'] == 'done')}/{len(results)}")

    except KeyboardInterrupt:
        print(f"\n\n⚠️  用户中断执行")
    except Exception as e:
        print(f"\n❌ 执行出错: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    main()
