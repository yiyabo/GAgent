"""
Example client for Amem Memory System API
Demonstrates how to use the API endpoints
"""
import requests
from typing import List, Dict, Any, Optional
import json


class AmemClient:
    """Client for interacting with Amem Memory System API"""

    def __init__(self, base_url: str = "http://localhost:8000"):
        """
        Initialize the Amem client

        Args:
            base_url: Base URL of the API server
        """
        self.base_url = base_url.rstrip('/')

    def health_check(self) -> Dict[str, Any]:
        """
        Check the health status of the API

        Returns:
            Dict containing status, memory count, and timestamp
        """
        response = requests.get(f"{self.base_url}/health")
        response.raise_for_status()
        return response.json()

    def add_memory(
        self,
        content: str,
        timestamp: Optional[str] = None,
        keywords: Optional[List[str]] = None,
        context: Optional[str] = None,
        tags: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Add a new memory to the system

        Args:
            content: The memory content
            timestamp: Optional timestamp in format YYYYMMDDHHMM
            keywords: Optional list of keywords
            context: Optional context description
            tags: Optional list of tags

        Returns:
            Dict containing success status, memory_id, and message
        """
        payload = {"content": content}

        if timestamp:
            payload["timestamp"] = timestamp
        if keywords:
            payload["keywords"] = keywords
        if context:
            payload["context"] = context
        if tags:
            payload["tags"] = tags

        response = requests.post(
            f"{self.base_url}/add_memory",
            json=payload
        )
        response.raise_for_status()
        return response.json()

    def query_memory(
        self,
        query: str,
        top_k: int = 5
    ) -> Dict[str, Any]:
        """
        Query memories using semantic search

        Args:
            query: The search query
            top_k: Number of results to return (1-20)

        Returns:
            Dict containing success status, query, results, and count
        """
        payload = {
            "query": query,
            "top_k": top_k
        }

        response = requests.post(
            f"{self.base_url}/query_memory",
            json=payload
        )
        response.raise_for_status()
        return response.json()

    def print_query_results(self, results: Dict[str, Any]):
        """
        Pretty print query results

        Args:
            results: Query results from query_memory()
        """
        print(f"\n查询: {results['query']}")
        print(f"找到 {results['count']} 条相关记忆:\n")

        for i, memory in enumerate(results['results'], 1):
            print(f"{'='*80}")
            print(f"记忆 {i}:")
            print(f"ID: {memory['id']}")
            print(f"时间: {memory['timestamp']}")
            print(f"内容: {memory['content']}")
            print(f"上下文: {memory['context']}")
            print(f"关键词: {', '.join(memory['keywords'])}")
            print(f"标签: {', '.join(memory['tags'])}")
            if memory.get('score') is not None:
                print(f"相关度评分: {memory['score']:.4f}")
            print()


def main():
    """Example usage of the Amem client"""

    # Initialize client
    client = AmemClient()

    print("Amem Memory System API 客户端示例\n")

    # Check health
    print("1. 检查服务状态...")
    try:
        health = client.health_check()
        print(f"   状态: {health['status']}")
        print(f"   记忆数量: {health['memory_count']}")
        print(f"   时间: {health['timestamp']}\n")
    except Exception as e:
        print(f"   错误: 无法连接到服务 - {e}")
        print("   请确保API服务正在运行 (python api.py)")
        return

    # Add memories
    print("2. 添加记忆...")

    memories_to_add = [
        {
            "content": "今天学习了FastAPI框架，它是一个现代、快速的Python Web框架",
            "tags": ["Python", "FastAPI", "Web开发"],
            "context": "学习笔记"
        },
        {
            "content": "讨论了微服务架构的优缺点，特别是服务间通信和分布式事务的处理",
            "tags": ["架构", "微服务", "分布式系统"],
            "context": "技术讨论"
        },
        {
            "content": "研究了向量数据库ChromaDB的使用，它非常适合存储和检索embeddings",
            "tags": ["数据库", "ChromaDB", "向量搜索"],
            "context": "技术研究"
        },
        {
            "content": "阅读了关于大语言模型prompt engineering的文章，学习了few-shot learning技巧",
            "tags": ["LLM", "Prompt Engineering", "AI"],
            "context": "学习笔记"
        }
    ]

    memory_ids = []
    for mem in memories_to_add:
        try:
            result = client.add_memory(**mem)
            memory_ids.append(result['memory_id'])
            print(f"   ✓ 已添加: {mem['content'][:50]}...")
        except Exception as e:
            print(f"   ✗ 添加失败: {e}")

    print(f"\n   成功添加 {len(memory_ids)} 条记忆\n")

    # Query memories
    print("3. 查询记忆...\n")

    queries = [
        "有哪些关于Python和Web开发的内容？",
        "分布式系统相关的讨论",
        "向量数据库的使用",
        "人工智能和机器学习"
    ]

    for query in queries:
        try:
            results = client.query_memory(query, top_k=3)
            client.print_query_results(results)
        except Exception as e:
            print(f"查询失败: {e}\n")

    # Final health check
    print(f"{'='*80}")
    print("4. 最终状态检查...")
    try:
        health = client.health_check()
        print(f"   总记忆数量: {health['memory_count']}\n")
    except Exception as e:
        print(f"   错误: {e}\n")


if __name__ == "__main__":
    main()
