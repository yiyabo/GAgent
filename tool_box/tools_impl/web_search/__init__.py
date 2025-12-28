"""
Web Search tool package.

Expose the tool definition and handler compatible with existing toolbox integration.
"""

from .handler import web_search_handler

web_search_tool = {
    "name": "web_search",
    "description": "Intelligent web search tool supporting built-in model search and external search services.",
    "category": "information_retrieval",
    "parameters_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Query string to search for",
            },
            "provider": {
                "type": "string",
                "description": "Search provider (uses system default if empty)",
                "enum": ["builtin", "perplexity"],
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of results (some providers may ignore this option)",
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
        "Search for the latest AI news",
        "Weather forecast for this week",
        "Query paper citation information",
        "Get industry market data",
    ],
}

__all__ = ["web_search_tool", "web_search_handler"]
