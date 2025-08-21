#!/usr/bin/env python3
"""
❯ python demo_postorder_with_execution.py
后序遍历调度算法演示 - 包含实际任务执行
创建示例任务层次结构...
创建了以下任务:
项目根任务: 142
前端模块: 143
- UI设计: 144
- JS开发: 145
后端模块: 146
- API开发: 147
- 数据库设计: 148
测试模块: 149
- 单元测试: 150
- 集成测试: 151

============================================================
BFS调度算法 (广度优先) - 开始执行任务
正在执行: 构建网站项目
ID: 122, 优先级: 10, 深度: 0 [无依赖]
状态: 执行中...
Task 122 (构建网站项目) done.
结果: done

正在执行: 构建网站项目
ID: 132, 优先级: 10, 深度: 0 [无依赖]
状态: 执行中...
Task 132 (构建网站项目) done.
结果: done

正在执行: 构建网站项目
ID: 142, 优先级: 10, 深度: 0 [无依赖]
状态: 执行中...
Task 142 (构建网站项目) done.
结果: done

正在执行: [Mock Plan] Mock A
ID: 75, 优先级: 10, 深度: 0 [无依赖]
状态: 执行中...
Task 75 ([Mock Plan] Mock A) done.
结果: done

正在执行: 前端开发
ID: 123, 优先级: 20, 深度: 1 [无依赖]
状态: 执行中...
Task 123 (前端开发) done.
结果: done

正在执行: 前端开发
ID: 133, 优先级: 20, 深度: 1 [无依赖]
状态: 执行中...
Task 133 (前端开发) done.
结果: done

正在执行: 前端开发
ID: 143, 优先级: 20, 深度: 1 [无依赖]
状态: 执行中...
Task 143 (前端开发) done.
结果: done

正在执行: 后端开发
ID: 126, 优先级: 25, 深度: 1 [无依赖]
状态: 执行中...
Task 126 (后端开发) done.
结果: done

正在执行: 后端开发
ID: 136, 优先级: 25, 深度: 1 [无依赖]
状态: 执行中...
Task 136 (后端开发) done.
结果: done

正在执行: 后端开发
ID: 146, 优先级: 25, 深度: 1 [无依赖]
状态: 执行中...
Task 146 (后端开发) done.
结果: done

正在执行: 测试
ID: 129, 优先级: 50, 深度: 1 [无依赖]
状态: 执行中...
Task 129 (测试) done.
结果: done

正在执行: 测试
ID: 139, 优先级: 50, 深度: 1 [无依赖]
状态: 执行中...
Task 139 (测试) done.
结果: done

正在执行: 测试
ID: 149, 优先级: 50, 深度: 1 [无依赖]
状态: 执行中...
Task 149 (测试) done.
结果: done

正在执行: 设计UI界面
ID: 124, 优先级: 30, 深度: 2 [无依赖]
状态: 执行中...
Task 124 (设计UI界面) done.
结果: done

正在执行: 设计UI界面
ID: 134, 优先级: 30, 深度: 2 [无依赖]
状态: 执行中...
Task 134 (设计UI界面) done.
结果: done

正在执行: 设计UI界面
ID: 144, 优先级: 30, 深度: 2 [无依赖]
状态: 执行中...
Task 144 (设计UI界面) done.
结果: done

正在执行: 开发API接口
ID: 127, 优先级: 35, 深度: 2 [无依赖]
状态: 执行中...
Task 127 (开发API接口) done.
结果: done

正在执行: 开发API接口
ID: 137, 优先级: 35, 深度: 2 [无依赖]
状态: 执行中...
Task 137 (开发API接口) done.
结果: done

正在执行: 开发API接口
ID: 147, 优先级: 35, 深度: 2 [无依赖]
状态: 执行中...
Task 147 (开发API接口) done.
结果: done

正在执行: 编写JavaScript代码
ID: 125, 优先级: 40, 深度: 2 [无依赖]
状态: 执行中...
Task 125 (编写JavaScript代码) done.
结果: done

正在执行: 编写JavaScript代码
ID: 135, 优先级: 40, 深度: 2 [无依赖]
状态: 执行中...
Task 135 (编写JavaScript代码) done.
结果: done

正在执行: 编写JavaScript代码
ID: 145, 优先级: 40, 深度: 2 [无依赖]
状态: 执行中...
Task 145 (编写JavaScript代码) done.
结果: done

正在执行: 设计数据库
ID: 128, 优先级: 45, 深度: 2 [无依赖]
状态: 执行中...
Task 128 (设计数据库) done.
结果: done

正在执行: 设计数据库
ID: 138, 优先级: 45, 深度: 2 [无依赖]
状态: 执行中...
Task 138 (设计数据库) done.
结果: done

正在执行: 设计数据库
ID: 148, 优先级: 45, 深度: 2 [无依赖]
状态: 执行中...
Task 148 (设计数据库) done.
结果: done

正在执行: 单元测试
ID: 130, 优先级: 60, 深度: 2 [无依赖]
状态: 执行中...
Task 130 (单元测试) done.
结果: done

正在执行: 单元测试
ID: 140, 优先级: 60, 深度: 2 [无依赖]
状态: 执行中...
Task 140 (单元测试) done.
结果: done

正在执行: 单元测试
ID: 150, 优先级: 60, 深度: 2 [无依赖]
状态: 执行中...
Task 150 (单元测试) done.
结果: done

正在执行: 集成测试
ID: 131, 优先级: 70, 深度: 2 [无依赖]
状态: 执行中...
Task 131 (集成测试) done.
结果: done

正在执行: 集成测试
ID: 141, 优先级: 70, 深度: 2 [无依赖]
状态: 执行中...
Task 141 (集成测试) done.
结果: done

正在执行: 集成测试
ID: 151, 优先级: 70, 深度: 2 [无依赖]
状态: 执行中...
Task 151 (集成测试) done.
结果: done

正在执行: [Mock Plan] Mock B
ID: 76, 优先级: 20, 深度: 0 [无依赖]
状态: 执行中...
Task 76 ([Mock Plan] Mock B) done.
结果: done

BFS调度算法 (广度优先) 执行完成!
总共执行了 32 个任务
成功: 0, 失败: 0

重置所有任务状态为pending...
Traceback (most recent call last):
File "/Users/allenygy/Library/CloudStorage/OneDrive-Personal/WorkSpace/Project/GAgent/demo_postorder_with_execution.py", line 200, in <module>
main()
~~~~^^
File "/Users/allenygy/Library/CloudStorage/OneDrive-Personal/WorkSpace/Project/GAgent/demo_postorder_with_execution.py", line 168, in main
reset_tasks_to_pending()
~~~~~~~~~~~~~~~~~~~~~~^^
File "/Users/allenygy/Library/CloudStorage/OneDrive-Personal/WorkSpace/Project/GAgent/demo_postorder_with_execution.py", line 144, in reset_tasks_to_pending
all_tasks = repo.list_tasks()

演示后序遍历调度算法的示例脚本 - 包含实际任务执行
"""

import sys
import os
import time

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import init_db
from app.repository.tasks import SqliteTaskRepository
from app.scheduler import postorder_schedule, bfs_schedule
from app.executor import execute_task


def create_sample_hierarchy():
    """创建一个示例任务层次结构"""
    repo = SqliteTaskRepository()
    
    print("创建示例任务层次结构...")
    
    # 创建层次结构: 项目 -> 模块 -> 具体任务
    project_id = repo.create_task("构建网站项目", priority=10)
    repo.upsert_task_input(project_id, "整合所有模块，完成网站项目的最终交付")
    
    # 前端模块
    frontend_id = repo.create_task("前端开发", priority=20, parent_id=project_id)
    repo.upsert_task_input(frontend_id, "整合前端所有组件，完成前端模块")
    
    ui_id = repo.create_task("设计UI界面", priority=30, parent_id=frontend_id)
    repo.upsert_task_input(ui_id, "设计用户界面，包括布局、颜色和交互元素")
    
    js_id = repo.create_task("编写JavaScript代码", priority=40, parent_id=frontend_id)
    repo.upsert_task_input(js_id, "编写前端JavaScript逻辑，实现用户交互功能")
    
    # 后端模块  
    backend_id = repo.create_task("后端开发", priority=25, parent_id=project_id)
    repo.upsert_task_input(backend_id, "整合后端所有服务，完成后端模块")
    
    api_id = repo.create_task("开发API接口", priority=35, parent_id=backend_id)
    repo.upsert_task_input(api_id, "设计和实现RESTful API接口")
    
    db_id = repo.create_task("设计数据库", priority=45, parent_id=backend_id)
    repo.upsert_task_input(db_id, "设计数据库schema和创建数据表")
    
    # 测试模块
    test_id = repo.create_task("测试", priority=50, parent_id=project_id)
    repo.upsert_task_input(test_id, "整合所有测试结果，完成测试报告")
    
    unit_test_id = repo.create_task("单元测试", priority=60, parent_id=test_id)
    repo.upsert_task_input(unit_test_id, "编写和执行单元测试用例")
    
    integration_test_id = repo.create_task("集成测试", priority=70, parent_id=test_id)
    repo.upsert_task_input(integration_test_id, "执行集成测试，验证模块间协作")
    
    print(f"创建了以下任务:")
    print(f"  项目根任务: {project_id}")
    print(f"  前端模块: {frontend_id}")
    print(f"    - UI设计: {ui_id}")
    print(f"    - JS开发: {js_id}")
    print(f"  后端模块: {backend_id}")
    print(f"    - API开发: {api_id}")
    print(f"    - 数据库设计: {db_id}")
    print(f"  测试模块: {test_id}")
    print(f"    - 单元测试: {unit_test_id}")
    print(f"    - 集成测试: {integration_test_id}")
    
    return {
        'project': project_id,
        'frontend': frontend_id,
        'backend': backend_id,
        'test': test_id,
        'ui': ui_id,
        'js': js_id,
        'api': api_id,
        'db': db_id,
        'unit_test': unit_test_id,
        'integration_test': integration_test_id
    }


def execute_with_scheduler(scheduler_name, scheduler_func, use_context=False):
    """使用指定调度器执行任务"""
    repo = SqliteTaskRepository()
    
    print(f"\n{'='*60}")
    print(f"{scheduler_name} - 开始执行任务")
    print(f"{'='*60}")
    
    results = []
    
    for i, task in enumerate(scheduler_func(), 1):
        task_id = task.get('id')
        name = task.get('name', 'Unknown')
        priority = task.get('priority', 'N/A')
        depth = task.get('depth', 0)
        dependencies = task.get('dependencies', [])
        indent = "  " * depth
        
        deps_str = f" [依赖: {dependencies}]" if dependencies else " [无依赖]"
        print(f"\n{i:2d}. 正在执行: {indent}{name}")
        print(f"    ID: {task_id}, 优先级: {priority}, 深度: {depth}{deps_str}")
        
        # 执行任务
        try:
            print(f"    状态: 执行中...")
            status = execute_task(task, use_context=use_context)
            
            # 更新任务状态
            repo.update_task_status(task_id, status)
            
            # 获取并显示任务执行的实际内容
            if status == "done":
                output = repo.get_task_output_content(task_id)
                if output:
                    print(f"    结果: {status}")
                    print(f"    执行内容预览: {output[:100]}...")
                else:
                    print(f"    结果: {status} (无输出内容)")
            else:
                print(f"    结果: {status}")
                
            results.append({"id": task_id, "name": name, "status": status})
            
            # 短暂延迟以便观察执行过程
            time.sleep(0.5)
            
        except Exception as e:
            print(f"    错误: {str(e)}")
            repo.update_task_status(task_id, "failed")
            results.append({"id": task_id, "name": name, "status": "failed", "error": str(e)})
    
    print(f"\n{scheduler_name} 执行完成!")
    print(f"总共执行了 {len(results)} 个任务")
    
    # 统计执行结果
    completed = sum(1 for r in results if r["status"] == "done")
    failed = sum(1 for r in results if r["status"] == "failed")
    
    print(f"成功: {completed}, 失败: {failed}")
    
    return results


def reset_tasks_to_pending():
    """重置所有任务状态为pending，以便重新执行"""
    repo = SqliteTaskRepository()
    print("\n重置所有任务状态为pending...")
    
    # 获取所有任务
    all_tasks = repo.list_all_tasks()
    for task in all_tasks:
        task_id = task.get('id')
        if task_id:
            repo.update_task_status(task_id, 'pending')
    
    print("任务状态重置完成")


def main():
    """主函数"""
    print("后序遍历调度算法演示 - 包含实际任务执行")
    print("=" * 60)
    
    # 初始化数据库
    init_db()
    
    # 创建示例层次结构
    task_ids = create_sample_hierarchy()
    
    # 演示BFS调度算法执行
    bfs_results = execute_with_scheduler("BFS调度算法 (广度优先)", bfs_schedule)
    
    # 重置任务状态
    reset_tasks_to_pending()
    
    # 演示后序遍历调度算法执行
    postorder_results = execute_with_scheduler("后序遍历调度算法 (子任务优先)", postorder_schedule)
    
    print(f"\n{'='*60}")
    print("执行结果对比")
    print(f"{'='*60}")
    
    print(f"\nBFS调度算法执行顺序:")
    for i, result in enumerate(bfs_results, 1):
        print(f"  {i:2d}. {result['name']} (ID: {result['id']}) - {result['status']}")
    
    print(f"\n后序遍历调度算法执行顺序:")
    for i, result in enumerate(postorder_results, 1):
        print(f"  {i:2d}. {result['name']} (ID: {result['id']}) - {result['status']}")
    
    print(f"\n{'='*60}")
    print("调度算法对比说明:")
    print(f"{'='*60}")
    print("1. BFS调度: 按层次广度优先，父任务先于子任务执行")
    print("   - 适合需要先完成规划再执行具体工作的场景")
    print("   - 执行顺序: 项目规划 -> 模块设计 -> 具体实现")
    
    print("\n2. 后序遍历调度: 子任务先于父任务执行")
    print("   - 适合需要先完成基础工作再进行整合的场景")
    print("   - 执行顺序: 具体实现 -> 模块整合 -> 项目完成")
    print("   - 每个任务都包含其直接子任务的依赖信息")
    print("   - 父任务可以基于子任务的完成结果进行决策")


if __name__ == "__main__":
    main()
