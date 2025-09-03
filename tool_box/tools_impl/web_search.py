"""
Web Search Tool Implementation

This module provides web search functionality for AI agents.
"""

import asyncio
import json
import logging
import os
from typing import Any, Dict, List, Optional

import aiohttp

logger = logging.getLogger(__name__)


async def web_search_handler(query: str, max_results: int = 5, search_engine: str = "searxng") -> Dict[str, Any]:
    """
    Web search tool handler

    Args:
        query: Search query string
        max_results: Maximum number of results to return
        search_engine: Search engine to use ("searxng", "duckduckgo", "google", etc.)

    Returns:
        Dict containing search results
    """
    try:
        if search_engine == "tavily":
            results = await _search_tavily(query, max_results)
        else:
            # Default to Tavily (AI-optimized)
            results = await _search_tavily(query, max_results)

        return {"query": query, "results": results, "total_results": len(results), "search_engine": search_engine}

    except Exception as e:
        logger.error(f"Web search failed: {e}")
        return {"query": query, "error": str(e), "results": [], "total_results": 0}


async def _search_tavily(query: str, max_results: int) -> List[Dict[str, Any]]:
    """Search using Tavily Search API (AI-optimized search)"""
    try:
        # Use hardcoded API key for reliable operation
        api_key = "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"

        if not api_key:
            logger.error("Tavily API key not configured")
            logger.error("Please set TAVILY_API_KEY environment variable")
            return []

        url = "https://api.tavily.com/search"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "query": query,
            "max_results": min(max_results, 10),  # Tavily recommends max 10
            "include_answer": True,
            "include_images": False,
            "include_raw_content": False,
        }

        timeout = aiohttp.ClientTimeout(total=15)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, headers=headers, json=payload) as response:
                if response.status == 200:
                    data = await response.json()

                    results = []

                    # Add AI-generated answer if available
                    if data.get("answer"):
                        results.append(
                            {
                                "title": "AI Answer",
                                "url": f"https://tavily.com/search?q={query.replace(' ', '+')}",
                                "snippet": data["answer"],
                                "source": "Tavily AI",
                            }
                        )

                    # Add search results
                    for item in data.get("results", [])[:max_results]:
                        results.append(
                            {
                                "title": item.get("title", "No Title"),
                                "url": item.get("url", ""),
                                "snippet": (
                                    item.get("content", "")[:200] + "..."
                                    if len(item.get("content", "")) > 200
                                    else item.get("content", "")
                                ),
                                "source": item.get("url", "").split("/")[2] if item.get("url") else "Tavily",
                            }
                        )

                    if results:
                        logger.info("Successfully searched using Tavily Search API")
                        return results

                elif response.status == 401:
                    logger.error("Invalid Tavily API key")
                elif response.status == 429:
                    logger.error("Tavily API rate limit exceeded")
                elif response.status == 402:
                    logger.error("Tavily API quota exceeded")
                else:
                    logger.error(f"Tavily API error: HTTP {response.status}")

    except Exception as e:
        logger.error(f"Tavily search failed: {e}")

    # Return empty results if API fails
    logger.error("Tavily search failed, returning empty results")
    return []


# Tool definition for web search
web_search_tool = {
    "name": "web_search",
    "description": "搜索网页内容并返回相关结果",
    "category": "information_retrieval",
    "parameters_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索查询字符串"},
            "max_results": {
                "type": "integer",
                "description": "最大返回结果数量",
                "default": 5,
                "minimum": 1,
                "maximum": 20,
            },
            "search_engine": {"type": "string", "description": "搜索引擎选择", "enum": ["tavily"], "default": "tavily"},
        },
        "required": ["query"],
    },
    "handler": web_search_handler,
    "tags": ["search", "web", "information", "retrieval"],
    "examples": ["搜索最新的AI新闻", "查找Python教程", "查询天气信息"],
}
