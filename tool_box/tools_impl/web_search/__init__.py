"""
Web Search tool package.

Expose the tool definition and handler compatible with existing toolbox integration.
"""

from .handler import web_search_handler

web_search_tool = {
    "name": "web_search",
    "description": (
        "Broad web search via Alibaba DashScope Responses API using the built-in "
        "`web_search` tool (see Model Studio web-search docs). Default provider is `builtin` only. "
        "For broad comparison tasks, you can pass `queries` with 2-4 focused subqueries; they will run in parallel."
    ),
    "category": "information_retrieval",
    "parameters_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Query string to search for",
            },
            "queries": {
                "type": "array",
                "description": "Optional focused subqueries for parallel search on broad comparison tasks",
                "items": {"type": "string"},
                "minItems": 2,
                "maxItems": 6,
            },
            "provider": {
                "type": "string",
                "description": "Optional override: `builtin` (DashScope web_search), `perplexity`, or `tavily`",
                "enum": ["builtin", "perplexity", "tavily"],
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
        "dashscope",
        "builtin",
    ],
    "examples": [
        "Search for the latest AI news",
        "Query paper citation information",
        "Get industry market data",
        "Literature background for a metagenomics method",
    ],
}

__all__ = ["web_search_tool", "web_search_handler"]
