"""
Web Search tool package.

Expose the tool definition and handler compatible with existing toolbox integration.
"""

from .handler import web_search_handler

web_search_tool = {
    "name": "web_search",
    "description": "智能网络搜索工具，支持模型内置搜索与外部搜索服务。",
    "category": "information_retrieval",
    "parameters_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "需要检索的查询字符串",
            },
            "provider": {
                "type": "string",
                "description": "搜索提供商（为空时使用系统默认值）",
                "enum": ["builtin", "perplexity"],
            },
            "max_results": {
                "type": "integer",
                "description": "最大结果数量（部分提供商可能忽略此选项）",
                "default": 5,
                "minimum": 1,
                "maximum": 20,
            },
        },
        "required": ["query"],
    },
    "handler": web_search_handler,
    "tags": [
        "search",
        "web",
        "information",
        "retrieval",
        "perplexity",
        "builtin",
    ],
    "examples": [
        "搜索最新的AI新闻",
        "2025年珠海附近的台风情况",
        "查询论文引用情况",
        "获取行业市场数据",
    ],
}

__all__ = ["web_search_tool", "web_search_handler"]
