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


async def web_search_handler(query: str, max_results: int = 5, search_engine: str = "perplexity") -> Dict[str, Any]:
    """
    Web search tool handler

    Args:
        query: Search query string
        max_results: Maximum number of results to return
        search_engine: Search engine to use ("perplexity", "tavily", etc.)

    Returns:
        Dict containing search results
    """
    try:
        # Perplexity-only implementation; unify return shape for downstream consumers
        response = await _search_perplexity(query)
        results = [
            {
                "title": "Perplexity Answer",
                "url": "",
                "snippet": response,
                "source": "Perplexity",
            }
        ]
        return {
            "query": query,
            "response": response,
            "results": results,
            "total_results": len(results),
            "search_engine": "perplexity",
            "success": True,
        }
    except Exception as e:
        logger.error(f"Web search failed: {e}")
        return {"query": query, "error": str(e), "search_engine": "perplexity", "success": False}


async def _search_tavily(query: str, max_results: int) -> List[Dict[str, Any]]:
    """Search using Tavily Search API (AI-optimized search)"""
    try:
        from app.services.foundation.settings import get_settings
        settings = get_settings()
        api_key = settings.tavily_api_key

        # Fallback: if settings not populated (e.g., cached before .env present), try environment directly
        if not api_key:
            import os
            api_key = os.getenv("TAVILY_API_KEY")
        # Last resort: attempt to load .env and read again
        if not api_key:
            try:
                from dotenv import load_dotenv
                load_dotenv()
                api_key = os.getenv("TAVILY_API_KEY")
            except Exception:
                pass

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


async def _search_perplexity(query: str) -> str:
    """Search using Perplexity API (AI-powered intelligent search)"""
    try:
        from app.services.foundation.settings import get_settings
        settings = get_settings()
        
        # Try to get from settings first
        api_key = getattr(settings, 'perplexity_api_key', None)
        api_url = getattr(settings, 'perplexity_api_url', None)
        model = getattr(settings, 'perplexity_model', None)

        # Fallback to environment variables
        if not api_key:
            api_key = os.getenv("PERPLEXITY_API_KEY")
        if not api_url:
            api_url = os.getenv("PERPLEXITY_API_URL", "https://api.perplexity.ai/chat/completions")
        if not model:
            model = os.getenv("PERPLEXITY_MODEL", "sonar-pro")

        # Last resort: attempt to load .env
        if not api_key:
            try:
                from dotenv import load_dotenv
                load_dotenv()
                api_key = os.getenv("PERPLEXITY_API_KEY")
                api_url = os.getenv("PERPLEXITY_API_URL", "https://api.perplexity.ai/chat/completions")
                model = os.getenv("PERPLEXITY_MODEL", "sonar-pro")
            except Exception:
                pass

        if not api_key:
            logger.error("Perplexity API key not configured")
            logger.error("Please set PERPLEXITY_API_KEY environment variable")
            return "Search failed: Perplexity API key not configured"

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

        # Build search-optimized prompt
        search_prompt = f"""Please provide accurate and detailed information for the following query:

Query: {query}

Please provide:
1. Direct answer to the user's question
2. Relevant latest information
3. If data is involved, provide specific numbers
4. Concise but comprehensive answer

Respond in the same language as the query."""

        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are an intelligent search assistant that provides accurate, timely, and useful information. Please answer the user's question based on the latest web information."
                },
                {
                    "role": "user",
                    "content": search_prompt
                }
            ],
            "max_tokens": 1000,
            "temperature": 0.1,  # Lower temperature for more accurate factual answers
            "stream": False
        }

        timeout = aiohttp.ClientTimeout(total=30)  # Perplexity may need longer time
        
        # Configure proxy settings
        connector = None
        proxy = None  # Disabled proxy to avoid HTTPS-over-HTTPS TLS-in-TLS issues
        
        # Check proxy environment variables (disabled to avoid compatibility issues)
        # https_proxy = os.getenv("https_proxy") or os.getenv("HTTPS_PROXY")
        # http_proxy = os.getenv("http_proxy") or os.getenv("HTTP_PROXY")
        
        # if https_proxy:
        #     proxy = https_proxy
        #     logger.info(f"Using proxy for Perplexity API: {proxy}")
        # elif http_proxy:
        #     proxy = http_proxy
        #     logger.info(f"Using proxy for Perplexity API: {proxy}")

        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
            async with session.post(api_url, headers=headers, json=payload, proxy=proxy) as response:
                if response.status == 200:
                    data = await response.json()
                    
                    # Extract answer content
                    if 'choices' in data and len(data['choices']) > 0:
                        answer = data['choices'][0]['message']['content']
                        logger.info("Successfully searched using Perplexity Search API")
                        return answer
                    else:
                        logger.error("Perplexity API returned unexpected format")
                        return "Search failed: API returned unexpected format"

                elif response.status == 401:
                    logger.error("Invalid Perplexity API key")
                    return "Search failed: Invalid API key"
                elif response.status == 429:
                    logger.error("Perplexity API rate limit exceeded")
                    return "Search failed: API rate limit exceeded"
                elif response.status == 400:
                    error_data = await response.json()
                    logger.error(f"Perplexity API bad request: {error_data}")
                    return "Search failed: Bad request format"
                else:
                    logger.error(f"Perplexity API error: HTTP {response.status}")
                    return f"Search failed: HTTP {response.status}"

    except Exception as e:
        logger.error(f"Perplexity search failed: {e}")
        return f"Search failed: {str(e)}"


# Tool definition for web search
web_search_tool = {
    "name": "web_search",
    "description": "Intelligent web search tool using Perplexity AI to provide accurate and up-to-date information",
    "category": "information_retrieval",
    "parameters_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query string"},
            "max_results": {
                "type": "integer",
                "description": "Maximum number of results to return (ignored in Perplexity mode)",
                "default": 5,
                "minimum": 1,
                "maximum": 20,
            },
            "search_engine": {
                "type": "string",
                "description": "Search engine (fixed to perplexity)",
                "enum": ["perplexity"],
                "default": "perplexity",
            },
        },
        "required": ["query"],
    },
    "handler": web_search_handler,
    "tags": ["search", "web", "information", "retrieval", "perplexity", "ai"],
    "examples": ["Search for the latest AI news", "Weather forecast for this week", "Query weather information", "What is quantum computing"],
}
