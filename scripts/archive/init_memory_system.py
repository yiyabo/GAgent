#!/usr/bin/env python3
"""
Memory System Initialization Script

Initialize memory system, import historical data as initial memories
"""

import asyncio
import logging
import sys
from pathlib import Path

# Add project root to path
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
    """Import sample knowledge memories"""
    memory_service = get_memory_service()
    
    sample_knowledge = [
        {
            "content": "Python is a high-level programming language known for its concise syntax and powerful features. Suitable for data analysis, machine learning, web development, and many other domains.",
            "tags": ["Python", "Programming Language", "Technology"],
            "importance": ImportanceLevel.MEDIUM,
        },
        {
            "content": "GLM (General Language Model) is a large language model developed by Zhipu AI, supporting various tasks including dialogue, text generation, and code generation.",
            "tags": ["GLM", "AI", "Large Model"],
            "importance": ImportanceLevel.HIGH,
        },
        {
            "content": "Embedding vectors are used to convert text into numerical representations, enabling computers to understand and process natural language. Commonly used in semantic search, similarity calculation, and other scenarios.",
            "tags": ["Embedding", "NLP", "Vector"],
            "importance": ImportanceLevel.HIGH,
        },
        {
            "content": "SQLite is a lightweight relational database that does not require an independent server process, suitable for embedded applications and small projects.",
            "tags": ["SQLite", "Database", "Technology"],
            "importance": ImportanceLevel.MEDIUM,
        },
        {
            "content": "FastAPI is a modern, fast Python web framework based on standard Python type hints with automatic API documentation generation.",
            "tags": ["FastAPI", "Web Framework", "Python"],
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
            logger.info(f"Saved knowledge memory: {response.memory_id}")
            saved_count += 1
            
        except Exception as e:
            logger.error(f"Failed to save knowledge memory: {e}")
    
    return saved_count


async def import_sample_experiences():
    """Import sample experience memories"""
    memory_service = get_memory_service()
    
    sample_experiences = [
        {
            "content": "When processing large batches of data, using batch processing can significantly improve performance. Recommended batch size is between 25-50.",
            "tags": ["Performance Optimization", "Batch Processing", "Best Practices"],
            "importance": ImportanceLevel.HIGH,
        },
        {
            "content": "Using database connection pools can avoid frequent creation and destruction of connections, improving system performance. Recommended pool size is 5-10.",
            "tags": ["Database", "Connection Pool", "Performance"],
            "importance": ImportanceLevel.HIGH,
        },
        {
            "content": "Asynchronous programming is highly effective for I/O-intensive tasks, but be careful to avoid blocking operations.",
            "tags": ["Async Programming", "Performance", "Python"],
            "importance": ImportanceLevel.MEDIUM,
        },
        {
            "content": "Caching strategy: Hot data in L1 cache, cold data in L2 cache, persistent data in L3 disk cache.",
            "tags": ["Caching", "Architecture", "Performance Optimization"],
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
            logger.info(f"Saved experience memory: {response.memory_id}")
            saved_count += 1
            
        except Exception as e:
            logger.error(f"Failed to save experience memory: {e}")
    
    return saved_count


async def import_sample_contexts():
    """Import sample context memories"""
    memory_service = get_memory_service()
    
    sample_contexts = [
        {
            "content": "Project uses Python 3.11+, main dependencies include FastAPI, SQLite, Zhipu AI SDK, etc.",
            "tags": ["Project Config", "Tech Stack"],
            "importance": ImportanceLevel.MEDIUM,
        },
        {
            "content": "System uses modular architecture, divided into API layer, service layer, and data layer. Memory system is integrated in the service layer.",
            "tags": ["Architecture", "System Design"],
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
            logger.info(f"Saved context memory: {response.memory_id}")
            saved_count += 1
            
        except Exception as e:
            logger.error(f"Failed to save context memory: {e}")
    
    return saved_count


async def check_existing_memories():
    """Check existing memory count"""
    try:
        with get_db() as conn:
            result = conn.execute("SELECT COUNT(*) FROM memories").fetchone()
            count = result[0] if result else 0
            return count
    except Exception as e:
        logger.error(f"Failed to check memory count: {e}")
        return 0


async def main():
    """Main function"""
    logger.info("=" * 60)
    logger.info("Memory System Initialization")
    logger.info("=" * 60)
    
    # Check existing memories
    existing_count = await check_existing_memories()
    logger.info(f"Existing memory count: {existing_count}")
    
    if existing_count > 0:
        response = input(f"\nDatabase already has {existing_count} memories, continue adding sample data? (y/N): ")
        if response.lower() != 'y':
            logger.info("Initialization cancelled")
            return
    
    logger.info("\n" + "=" * 60)
    logger.info("Starting sample memory import...")
    logger.info("=" * 60)
    
    # Import various memory types
    knowledge_count = await import_sample_knowledge()
    logger.info(f"\nImported knowledge memories: {knowledge_count}")
    
    experience_count = await import_sample_experiences()
    logger.info(f"Imported experience memories: {experience_count}")
    
    context_count = await import_sample_contexts()
    logger.info(f"Imported context memories: {context_count}")
    
    total_imported = knowledge_count + experience_count + context_count
    
    # Get statistics
    memory_service = get_memory_service()
    stats = await memory_service.get_memory_stats()
    
    logger.info("\n" + "=" * 60)
    logger.info("Memory System Statistics")
    logger.info("=" * 60)
    logger.info(f"Total memories: {stats.total_memories}")
    logger.info(f"Imported this session: {total_imported}")
    logger.info(f"Embedding coverage: {stats.embedding_coverage:.2%}")
    logger.info(f"Memory type distribution: {stats.memory_type_distribution}")
    logger.info(f"Importance distribution: {stats.importance_distribution}")
    
    logger.info("\n" + "=" * 60)
    logger.info("Memory System Initialization Complete!")
    logger.info("=" * 60)
    
    logger.info("\nTips:")
    logger.info("  - Visit http://localhost:9000/mcp/memory/stats to view statistics")
    logger.info("  - Visit frontend Memory page to view and manage memories")
    logger.info("  - Memory system is enabled and will automatically save important events")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("\n\nUser interrupted")
    except Exception as e:
        logger.error(f"\n\nInitialization failed: {e}", exc_info=True)
        sys.exit(1)
