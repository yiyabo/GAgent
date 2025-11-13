#!/usr/bin/env python3
"""
Memory System Initialization Script

初始化记忆系统，导入历史数据作为初始记忆
"""

import asyncio
import logging
import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.services.memory.memory_service import get_memory_service
from app.models_memory import (
    ImportanceLevel,
    MemoryType,
    SaveMemoryRequest,
)
from app.database_pool import get_db

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def import_sample_knowledge():
    """导入示例知识记忆"""
    memory_service = get_memory_service()
    
    sample_knowledge = [
        {
            "content": "Python是一种高级编程语言，以其简洁的语法和强大的功能而闻名。适合数据分析、机器学习、Web开发等多个领域。",
            "tags": ["Python", "编程语言", "技术"],
            "importance": ImportanceLevel.MEDIUM,
        },
        {
            "content": "GLM (General Language Model) 是智谱AI开发的大语言模型，支持对话、文本生成、代码生成等多种任务。",
            "tags": ["GLM", "AI", "大模型"],
            "importance": ImportanceLevel.HIGH,
        },
        {
            "content": "Embedding向量用于将文本转换为数值表示，使计算机能够理解和处理自然语言。常用于语义搜索、相似度计算等场景。",
            "tags": ["Embedding", "NLP", "向量"],
            "importance": ImportanceLevel.HIGH,
        },
        {
            "content": "SQLite是一个轻量级的关系型数据库，无需独立服务器进程，适合嵌入式应用和小型项目。",
            "tags": ["SQLite", "数据库", "技术"],
            "importance": ImportanceLevel.MEDIUM,
        },
        {
            "content": "FastAPI是一个现代、快速的Python Web框架，基于标准Python类型提示，自动生成API文档。",
            "tags": ["FastAPI", "Web框架", "Python"],
            "importance": ImportanceLevel.MEDIUM,
        },
    ]
    
    saved_count = 0
    for knowledge in sample_knowledge:
        try:
            request = SaveMemoryRequest(
                content=knowledge["content"],
                memory_type=MemoryType.KNOWLEDGE,
                importance=knowledge["importance"],
                tags=knowledge["tags"],
            )
            
            response = await memory_service.save_memory(request)
            logger.info(f"✅ 保存知识记忆: {response.memory_id}")
            saved_count += 1
            
        except Exception as e:
            logger.error(f"❌ 保存知识记忆失败: {e}")
    
    return saved_count


async def import_sample_experiences():
    """导入示例经验记忆"""
    memory_service = get_memory_service()
    
    sample_experiences = [
        {
            "content": "在处理大批量数据时，使用批处理可以显著提高性能。建议批量大小设置为25-50之间。",
            "tags": ["性能优化", "批处理", "最佳实践"],
            "importance": ImportanceLevel.HIGH,
        },
        {
            "content": "数据库连接池的使用可以避免频繁创建和销毁连接，提高系统性能。建议池大小设置为5-10。",
            "tags": ["数据库", "连接池", "性能"],
            "importance": ImportanceLevel.HIGH,
        },
        {
            "content": "异步编程在处理I/O密集型任务时效果显著，但要注意避免阻塞操作。",
            "tags": ["异步编程", "性能", "Python"],
            "importance": ImportanceLevel.MEDIUM,
        },
        {
            "content": "缓存策略：热数据放在L1缓存，冷数据放在L2缓存，持久化数据放在L3磁盘缓存。",
            "tags": ["缓存", "架构", "性能优化"],
            "importance": ImportanceLevel.HIGH,
        },
    ]
    
    saved_count = 0
    for experience in sample_experiences:
        try:
            request = SaveMemoryRequest(
                content=experience["content"],
                memory_type=MemoryType.EXPERIENCE,
                importance=experience["importance"],
                tags=experience["tags"],
            )
            
            response = await memory_service.save_memory(request)
            logger.info(f"✅ 保存经验记忆: {response.memory_id}")
            saved_count += 1
            
        except Exception as e:
            logger.error(f"❌ 保存经验记忆失败: {e}")
    
    return saved_count


async def import_sample_contexts():
    """导入示例上下文记忆"""
    memory_service = get_memory_service()
    
    sample_contexts = [
        {
            "content": "项目使用Python 3.11+，主要依赖包括FastAPI、SQLite、智谱AI SDK等。",
            "tags": ["项目配置", "技术栈"],
            "importance": ImportanceLevel.MEDIUM,
        },
        {
            "content": "系统采用模块化架构，分为API层、服务层、数据层。记忆系统集成在服务层。",
            "tags": ["架构", "系统设计"],
            "importance": ImportanceLevel.MEDIUM,
        },
    ]
    
    saved_count = 0
    for context in sample_contexts:
        try:
            request = SaveMemoryRequest(
                content=context["content"],
                memory_type=MemoryType.CONTEXT,
                importance=context["importance"],
                tags=context["tags"],
            )
            
            response = await memory_service.save_memory(request)
            logger.info(f"✅ 保存上下文记忆: {response.memory_id}")
            saved_count += 1
            
        except Exception as e:
            logger.error(f"❌ 保存上下文记忆失败: {e}")
    
    return saved_count


async def check_existing_memories():
    """检查现有记忆数量"""
    try:
        with get_db() as conn:
            result = conn.execute("SELECT COUNT(*) FROM memories").fetchone()
            count = result[0] if result else 0
            return count
    except Exception as e:
        logger.error(f"检查记忆数量失败: {e}")
        return 0


async def main():
    """主函数"""
    logger.info("=" * 60)
    logger.info("🚀 Memory System Initialization")
    logger.info("=" * 60)
    
    # 检查现有记忆
    existing_count = await check_existing_memories()
    logger.info(f"📊 现有记忆数量: {existing_count}")
    
    if existing_count > 0:
        response = input(f"\n⚠️  数据库中已有 {existing_count} 条记忆，是否继续添加示例数据？(y/N): ")
        if response.lower() != 'y':
            logger.info("❌ 取消初始化")
            return
    
    logger.info("\n" + "=" * 60)
    logger.info("📚 开始导入示例记忆...")
    logger.info("=" * 60)
    
    # 导入各类记忆
    knowledge_count = await import_sample_knowledge()
    logger.info(f"\n✅ 导入知识记忆: {knowledge_count} 条")
    
    experience_count = await import_sample_experiences()
    logger.info(f"✅ 导入经验记忆: {experience_count} 条")
    
    context_count = await import_sample_contexts()
    logger.info(f"✅ 导入上下文记忆: {context_count} 条")
    
    total_imported = knowledge_count + experience_count + context_count
    
    # 获取统计信息
    memory_service = get_memory_service()
    stats = await memory_service.get_memory_stats()
    
    logger.info("\n" + "=" * 60)
    logger.info("📊 Memory System Statistics")
    logger.info("=" * 60)
    logger.info(f"总记忆数: {stats.total_memories}")
    logger.info(f"本次导入: {total_imported} 条")
    logger.info(f"嵌入向量覆盖率: {stats.embedding_coverage:.2%}")
    logger.info(f"记忆类型分布: {stats.memory_type_distribution}")
    logger.info(f"重要性分布: {stats.importance_distribution}")
    
    logger.info("\n" + "=" * 60)
    logger.info("✅ Memory System Initialization Complete!")
    logger.info("=" * 60)
    
    logger.info("\n💡 提示:")
    logger.info("  - 访问 http://localhost:9000/mcp/memory/stats 查看统计信息")
    logger.info("  - 访问前端 Memory 页面查看和管理记忆")
    logger.info("  - 记忆系统已启用，会自动保存重要事件")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("\n\n⚠️  用户中断")
    except Exception as e:
        logger.error(f"\n\n❌ 初始化失败: {e}", exc_info=True)
        sys.exit(1)
