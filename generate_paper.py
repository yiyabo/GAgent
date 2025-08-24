#!/usr/bin/env python3
"""
简单的论文生成脚本
用法: python generate_paper.py --topic "因果推理方法综述" --sections 5
"""

import argparse
import sys
import os
from typing import List, Dict

# 添加项目路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.repository.tasks import default_repo
from app.executor_enhanced import execute_task_with_llm_evaluation


def create_paper_sections(topic: str, num_sections: int = 5) -> List[int]:
    """创建论文章节任务"""
    
    # 预定义的章节模板
    section_templates = [
        "引言和背景",
        "文献综述", 
        "方法论",
        "应用案例",
        "讨论和结论"
    ]
    
    # 根据主题调整章节
    if "因果推理" in topic:
        sections = [
            f"{topic} - 引言：定义因果推理的基本概念和重要性",
            f"{topic} - 经典方法：随机对照试验、观察性研究设计",
            f"{topic} - 现代方法：工具变量、倾向性评分匹配、双重差分",
            f"{topic} - 机器学习方法：因果森林、深度学习因果推理",
            f"{topic} - 应用实例：医学、经济学、社会科学中的应用案例",
            f"{topic} - 挑战与展望：当前限制和未来发展方向"
        ]
    else:
        # 通用章节模板
        sections = [
            f"{topic} - {template}" for template in section_templates[:num_sections]
        ]
    
    print(f"📝 创建论文《{topic}》，共 {len(sections)} 个章节")
    
    task_ids = []
    for i, section_title in enumerate(sections, 1):
        # 创建任务
        task_id = default_repo.create_task(
            name=section_title,
            status="pending",
            priority=i * 10,
            task_type="atomic"
        )
        
        # 添加详细的提示词
        prompt = f"""
请为学术论文写一个高质量的章节内容。

章节标题: {section_title}

要求:
1. 内容要学术严谨，引用相关研究
2. 逻辑清晰，结构完整
3. 长度约500-800字
4. 使用专业术语，但保持可读性
5. 包含具体例子或案例说明
6. 如果是方法章节，要包含技术细节
7. 如果是综述章节，要涵盖主要观点和争议

请生成完整的章节内容:
"""
        
        default_repo.upsert_task_input(task_id, prompt)
        task_ids.append(task_id)
        print(f"  ✓ 章节 {i}: {section_title} (任务ID: {task_id})")
    
    return task_ids


def generate_paper_content(task_ids: List[int], use_evaluation: bool = True) -> Dict[str, str]:
    """生成论文内容"""
    
    print(f"\n🚀 开始生成论文内容...")
    print(f"评估模式: {'智能LLM评估' if use_evaluation else '基础生成'}")
    
    results = {}
    
    for i, task_id in enumerate(task_ids, 1):
        print(f"\n📖 生成第 {i}/{len(task_ids)} 章节 (任务ID: {task_id})")
        
        try:
            # 获取任务信息
            task = default_repo.get_task_info(task_id)
            if not task:
                print(f"❌ 任务 {task_id} 不存在")
                continue
            
            print(f"   章节: {task['name']}")
            
            if use_evaluation:
                # 使用智能评估生成
                result = execute_task_with_llm_evaluation(
                    task=task,
                    repo=default_repo,
                    max_iterations=3,
                    quality_threshold=0.8,
                    use_context=False
                )
                
                print(f"   ✅ 生成完成 - 状态: {result.status}")
                print(f"   📊 质量评分: {result.evaluation.overall_score:.3f}")
                print(f"   🔄 迭代次数: {result.iterations_completed}")
                
                results[task['name']] = result.content
                
            else:
                # 基础生成（备用方案）
                from app.executor_enhanced import execute_task
                status = execute_task(task, default_repo, enable_evaluation=False)
                
                if status == "done":
                    content = default_repo.get_task_output_content(task_id)
                    results[task['name']] = content
                    print(f"   ✅ 生成完成")
                else:
                    print(f"   ❌ 生成失败")
                    
        except Exception as e:
            print(f"   ❌ 生成失败: {e}")
            continue
    
    return results


def save_paper(results: Dict[str, str], topic: str, output_file: str = None) -> str:
    """保存论文到文件"""
    
    if not output_file:
        # 生成文件名
        safe_topic = "".join(c for c in topic if c.isalnum() or c in (' ', '-', '_')).rstrip()
        output_file = f"{safe_topic.replace(' ', '_')}_论文.md"
    
    print(f"\n💾 保存论文到: {output_file}")
    
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(f"# {topic}\n\n")
        f.write(f"*自动生成于 {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*\n\n")
        f.write("---\n\n")
        
        for section_title, content in results.items():
            # 提取章节标题（去掉主题前缀）
            clean_title = section_title.split(" - ", 1)[-1] if " - " in section_title else section_title
            f.write(f"## {clean_title}\n\n")
            f.write(f"{content}\n\n")
            f.write("---\n\n")
        
        # 添加统计信息
        total_words = sum(len(content.split()) for content in results.values())
        f.write(f"\n**论文统计:**\n")
        f.write(f"- 章节数: {len(results)}\n")
        f.write(f"- 总字数: 约 {total_words} 字\n")
        f.write(f"- 生成方式: 智能LLM评估系统\n")
    
    print(f"✅ 论文已保存! 共 {len(results)} 个章节，约 {sum(len(content.split()) for content in results.values())} 字")
    return output_file


def main():
    parser = argparse.ArgumentParser(description="自动生成学术论文综述")
    parser.add_argument("--topic", required=True, help="论文主题")
    parser.add_argument("--sections", type=int, default=6, help="章节数量 (默认: 6)")
    parser.add_argument("--output", help="输出文件名")
    parser.add_argument("--simple", action="store_true", help="使用简单模式（不使用评估）")
    
    args = parser.parse_args()
    
    print("🎓 学术论文自动生成系统")
    print("=" * 40)
    print(f"主题: {args.topic}")
    print(f"章节数: {args.sections}")
    print(f"评估模式: {'简单模式' if args.simple else '智能评估模式'}")
    
    try:
        # 1. 创建章节任务
        task_ids = create_paper_sections(args.topic, args.sections)
        
        # 2. 生成内容
        results = generate_paper_content(task_ids, use_evaluation=not args.simple)
        
        if not results:
            print("❌ 没有成功生成任何内容")
            return 1
        
        # 3. 保存论文
        output_file = save_paper(results, args.topic, args.output)
        
        print(f"\n🎉 论文生成完成!")
        print(f"📄 文件: {output_file}")
        print(f"📊 可以使用以下命令查看评估统计:")
        print(f"   python -m cli.main --eval-stats --detailed")
        
        return 0
        
    except KeyboardInterrupt:
        print("\n⏹️  用户中断生成")
        return 1
    except Exception as e:
        print(f"\n❌ 生成失败: {e}")
        return 1


if __name__ == "__main__":
    exit(main())