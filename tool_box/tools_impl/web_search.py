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
            return "❌ 搜索失败：Perplexity API密钥未配置"

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

        # 构建针对搜索优化的prompt
        search_prompt = f"""请根据以下查询提供准确、详细的信息回答：

查询：{query}

请提供：
1. 直接回答用户的问题
2. 相关的最新信息
3. 如果涉及数据，请提供具体数字
4. 简洁但全面的回答

回答语言与查询语言保持一致。"""

        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": "你是一个智能搜索助手，能够提供准确、及时、有用的信息。请基于最新的网络信息来回答用户的问题。"
                },
                {
                    "role": "user", 
                    "content": search_prompt
                }
            ],
            "max_tokens": 1000,
            "temperature": 0.1,  # 降低温度以获得更准确的事实性回答
            "stream": False
        }

        timeout = aiohttp.ClientTimeout(total=30)  # Perplexity可能需要更长时间
        
        # 配置代理设置
        connector = None
        proxy = None  # 🔧 禁用代理以避免 HTTPS-over-HTTPS TLS-in-TLS 问题
        
        # 检查代理环境变量（已禁用以避免兼容性问题）
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
                    
                    # 提取回答内容
                    if 'choices' in data and len(data['choices']) > 0:
                        answer = data['choices'][0]['message']['content']
                        logger.info("Successfully searched using Perplexity Search API")
                        return answer
                    else:
                        logger.error("Perplexity API returned unexpected format")
                        return "❌ 搜索失败：API返回格式异常"

                elif response.status == 401:
                    logger.error("Invalid Perplexity API key")
                    return "❌ 搜索失败：API密钥无效"
                elif response.status == 429:
                    logger.error("Perplexity API rate limit exceeded")  
                    return "❌ 搜索失败：API请求频率限制"
                elif response.status == 400:
                    error_data = await response.json()
                    logger.error(f"Perplexity API bad request: {error_data}")
                    return "❌ 搜索失败：请求格式错误"
                else:
                    logger.error(f"Perplexity API error: HTTP {response.status}")
                    return f"❌ 搜索失败：HTTP {response.status}"

    except Exception as e:
        logger.error(f"Perplexity search failed: {e}")
        return f"❌ 搜索失败：{str(e)}"


# Tool definition for web search
web_search_tool = {
    "name": "web_search",
    "description": "智能网络搜索工具，使用Perplexity AI提供准确、最新的信息回答",
    "category": "information_retrieval",
    "parameters_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索查询字符串"},
            "max_results": {
                "type": "integer",
                "description": "最大返回结果数量（Perplexity模式下忽略）",
                "default": 5,
                "minimum": 1,
                "maximum": 20,
            },
            "search_engine": {
                "type": "string",
                "description": "固定为perplexity",
                "enum": ["perplexity"],
                "default": "perplexity",
            },
        },
        "required": ["query"],
    },
    "handler": web_search_handler,
    "tags": ["search", "web", "information", "retrieval", "perplexity", "ai"],
    "examples": ["搜索最新的AI新闻", "2025年珠海附近的台风情况", "查询天气信息", "什么是量子计算"],
}
