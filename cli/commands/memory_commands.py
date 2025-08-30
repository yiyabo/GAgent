"""
Memory management command implementations
"""

import asyncio
import json
from argparse import Namespace
from typing import Any, Dict, List

from .base import MultiCommand
from ..utils import IOUtils

# Import memory services
from app.services.memory_service import get_memory_service
from app.models_memory import SaveMemoryRequest, QueryMemoryRequest, MemoryType, ImportanceLevel


class MemoryCommands(MultiCommand):
    """Handle memory management operations"""
    
    @property
    def name(self) -> str:
        return "memory"
    
    @property
    def description(self) -> str:
        return "Memory management operations"
    
    def get_action_map(self) -> Dict[str, callable]:
        """Map memory arguments to handler methods"""
        return {
            'memory_save': self.handle_save_memory,
            'memory_query': self.handle_query_memory,
            'memory_stats': self.handle_memory_stats,
            'memory_list': self.handle_list_memories,
        }
    
    def handle_save_memory(self, args: Namespace) -> int:
        """Handle --memory-save operation"""
        content = getattr(args, 'memory_content', None)
        memory_type = getattr(args, 'memory_type', 'experience')
        importance = getattr(args, 'memory_importance', 'medium')
        tags = getattr(args, 'memory_tags', None)
        task_id = getattr(args, 'memory_task_id', None)
        
        if not content:
            self.io.print_error("Memory content is required")
            self.io.print_info("Use: --memory-save --memory-content 'Your memory content'")
            return 1
        
        try:
            # 解析标签
            tag_list = []
            if tags:
                tag_list = [tag.strip() for tag in tags.split(',')]
            
            # 创建保存请求
            request = SaveMemoryRequest(
                content=content,
                memory_type=MemoryType(memory_type),
                importance=ImportanceLevel(importance),
                tags=tag_list,
                related_task_id=int(task_id) if task_id else None
            )
            
            # 保存记忆
            memory_service = get_memory_service()
            response = asyncio.run(memory_service.save_memory(request))
            
            self.io.print_success(f"Memory saved with ID: {response.memory_id}")
            self.io.print_info(f"Type: {response.memory_type.value}")
            self.io.print_info(f"Keywords: {', '.join(response.keywords)}")
            self.io.print_info(f"Context: {response.context}")
            self.io.print_info(f"Tags: {', '.join(response.tags)}")
            self.io.print_info(f"Embedding generated: {response.embedding_generated}")
            
            return 0
            
        except Exception as e:
            self.io.print_error(f"Failed to save memory: {e}")
            return 1
    
    def handle_query_memory(self, args: Namespace) -> int:
        """Handle --memory-query operation"""
        search_text = getattr(args, 'memory_search', None)
        memory_types = getattr(args, 'memory_filter_types', None)
        limit = getattr(args, 'memory_limit', 10)
        min_similarity = getattr(args, 'memory_min_similarity', 0.3)
        
        if not search_text:
            self.io.print_error("Search text is required")
            self.io.print_info("Use: --memory-query --memory-search 'search keywords'")
            return 1
        
        try:
            # 解析记忆类型过滤
            type_filter = []
            if memory_types:
                type_filter = [MemoryType(t.strip()) for t in memory_types.split(',')]
            
            # 创建查询请求
            request = QueryMemoryRequest(
                search_text=search_text,
                memory_types=type_filter if type_filter else None,
                limit=limit,
                min_similarity=min_similarity
            )
            
            # 查询记忆
            memory_service = get_memory_service()
            response = asyncio.run(memory_service.query_memory(request))
            
            if not response.memories:
                self.io.print_warning("No memories found matching the search criteria")
                return 0
            
            self.io.print_section(f"Found {response.total} memories (search time: {response.search_time_ms:.1f}ms)")
            
            for i, memory in enumerate(response.memories, 1):
                print(f"\n{i}. Memory ID: {memory.memory_id}")
                print(f"   Type: {memory.memory_type.value}")
                print(f"   Importance: {memory.importance.value}")
                print(f"   Similarity: {memory.similarity:.3f}")
                print(f"   Created: {memory.created_at.strftime('%Y-%m-%d %H:%M')}")
                if memory.task_id:
                    print(f"   Related Task: {memory.task_id}")
                print(f"   Content: {memory.content[:100]}{'...' if len(memory.content) > 100 else ''}")
                if memory.keywords:
                    print(f"   Keywords: {', '.join(memory.keywords)}")
                if memory.tags:
                    print(f"   Tags: {', '.join(memory.tags)}")
                print(f"   Context: {memory.context}")
            
            return 0
            
        except Exception as e:
            self.io.print_error(f"Failed to query memories: {e}")
            return 1
    
    def handle_memory_stats(self, args: Namespace) -> int:
        """Handle --memory-stats operation"""
        try:
            memory_service = get_memory_service()
            stats = asyncio.run(memory_service.get_memory_stats())
            
            self.io.print_section("Memory System Statistics")
            print(f"  Total memories: {stats.total_memories}")
            print(f"  Evolution count: {stats.evolution_count}")
            print(f"  Average connections: {stats.average_connections:.2f}")
            print(f"  Embedding coverage: {stats.embedding_coverage:.1%}")
            
            print(f"\n  Memory type distribution:")
            for mem_type, count in stats.memory_type_distribution.items():
                print(f"    {mem_type}: {count}")
            
            print(f"\n  Importance distribution:")
            for importance, count in stats.importance_distribution.items():
                print(f"    {importance}: {count}")
            
            return 0
            
        except Exception as e:
            self.io.print_error(f"Failed to get memory stats: {e}")
            return 1
    
    def handle_list_memories(self, args: Namespace) -> int:
        """Handle --memory-list operation"""
        memory_type = getattr(args, 'memory_type_filter', None)
        limit = getattr(args, 'memory_limit', 20)
        
        try:
            # 使用查询功能列出记忆
            request = QueryMemoryRequest(
                search_text="",  # 空搜索返回所有记忆
                memory_types=[MemoryType(memory_type)] if memory_type else None,
                limit=limit,
                min_similarity=0.0  # 最低阈值
            )
            
            memory_service = get_memory_service()
            response = asyncio.run(memory_service.query_memory(request))
            
            if not response.memories:
                self.io.print_warning("No memories found")
                return 0
            
            self.io.print_section(f"Recent {len(response.memories)} memories")
            
            for i, memory in enumerate(response.memories, 1):
                print(f"\n{i}. [{memory.memory_type.value}] {memory.memory_id[:8]}...")
                print(f"   Content: {memory.content[:80]}{'...' if len(memory.content) > 80 else ''}")
                print(f"   Importance: {memory.importance.value}")
                print(f"   Created: {memory.created_at.strftime('%Y-%m-%d %H:%M')}")
                if memory.task_id:
                    print(f"   Task: {memory.task_id}")
                if memory.tags:
                    print(f"   Tags: {', '.join(memory.tags[:3])}{'...' if len(memory.tags) > 3 else ''}")
            
            return 0
            
        except Exception as e:
            self.io.print_error(f"Failed to list memories: {e}")
            return 1