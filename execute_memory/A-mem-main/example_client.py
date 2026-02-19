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
        print(f"\n: {results['query']}")
        print(f" {results['count']} :\n")

        for i, memory in enumerate(results['results'], 1):
            print(f"{'='*80}")
            print(f" {i}:")
            print(f"ID: {memory['id']}")
            print(f": {memory['timestamp']}")
            print(f": {memory['content']}")
            print(f": {memory['context']}")
            print(f": {', '.join(memory['keywords'])}")
            print(f": {', '.join(memory['tags'])}")
            if memory.get('score') is not None:
                print(f": {memory['score']:.4f}")
            print()


def main():
    """Example usage of the Amem client"""

    # Initialize client
    client = AmemClient()

    print("Amem Memory System API \n")

    # Check health
    print("1. ...")
    try:
        health = client.health_check()
        print(f"   : {health['status']}")
        print(f"   : {health['memory_count']}")
        print(f"   : {health['timestamp']}\n")
    except Exception as e:
        print(f"   :  - {e}")
        print("   API (python api.py)")
        return

    # Add memories
    print("2. ...")

    memories_to_add = [
        {
            "content": "FastAPI，Python Web",
            "tags": ["Python", "FastAPI", "Web"],
            "context": ""
        },
        {
            "content": "，",
            "tags": ["", "", ""],
            "context": ""
        },
        {
            "content": "ChromaDB，embeddings",
            "tags": ["", "ChromaDB", ""],
            "context": ""
        },
        {
            "content": "prompt engineering，few-shot learning",
            "tags": ["LLM", "Prompt Engineering", "AI"],
            "context": ""
        }
    ]

    memory_ids = []
    for mem in memories_to_add:
        try:
            result = client.add_memory(**mem)
            memory_ids.append(result['memory_id'])
            print(f"   ✓ : {mem['content'][:50]}...")
        except Exception as e:
            print(f"   ✗ : {e}")

    print(f"\n    {len(memory_ids)} \n")

    # Query memories
    print("3. ...\n")

    queries = [
        "PythonWeb？",
        "",
        "",
        ""
    ]

    for query in queries:
        try:
            results = client.query_memory(query, top_k=3)
            client.print_query_results(results)
        except Exception as e:
            print(f": {e}\n")

    # Final health check
    print(f"{'='*80}")
    print("4. ...")
    try:
        health = client.health_check()
        print(f"   : {health['memory_count']}\n")
    except Exception as e:
        print(f"   : {e}\n")


if __name__ == "__main__":
    main()
