"""
Web Search 配置

统一管理 Web Search 相关的配置，包括默认 Provider、各 Provider 使用的
API Endpoint、模型名称以及鉴权参数。
"""

from dataclasses import dataclass
from functools import lru_cache
import os
from typing import Optional


@dataclass(slots=True)
class SearchSettings:
    """Web Search 模块配置"""

    default_provider: str = "builtin"
    builtin_provider: str = "qwen"  # qwen | glm | ...

    # Builtin（模型内置搜索）配置
    qwen_api_key: Optional[str] = None
    qwen_api_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    qwen_model: str = "qwen-turbo"

    glm_api_key: Optional[str] = None
    glm_api_url: str = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    glm_model: str = "glm-4-flash"

    builtin_request_timeout: float = 40.0

    # 外部 Provider：Perplexity
    perplexity_api_key: Optional[str] = None
    perplexity_api_url: str = "https://api.perplexity.ai/chat/completions"
    perplexity_model: str = "sonar-pro"
    perplexity_timeout: float = 30.0

    # 外部 Provider：Tavily MCP
    tavily_api_key: Optional[str] = None
    tavily_api_url: str = "https://api.tavily.com/search"
    tavily_mcp_url: str = "https://mcp.tavily.com/mcp/"
    tavily_timeout: float = 30.0
    tavily_tool_name: Optional[str] = None
    tavily_search_depth: str = "advanced"
    tavily_topic: str = "general"
    tavily_time_range: Optional[str] = None
    tavily_include_answer: bool = False
    tavily_include_raw_content: bool = False
    tavily_auto_parameters: bool = False


def _env(key: str, default: Optional[str] = None) -> Optional[str]:
    value = os.getenv(key)
    if value is None:
        return default
    value = value.strip()
    return value or default


@lru_cache(maxsize=1)
def get_search_settings() -> SearchSettings:
    """读取环境变量并返回 SearchSettings"""

    default_provider = _env("DEFAULT_WEB_SEARCH_PROVIDER", "builtin")
    builtin_provider = _env("BUILTIN_SEARCH_PROVIDER", "qwen")

    qwen_api_key = _env("QWEN_API_KEY")
    qwen_api_url = _env("QWEN_API_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions")
    qwen_model = _env("QWEN_MODEL", "qwen-turbo")

    glm_api_key = _env("GLM_API_KEY")
    glm_api_url = _env("GLM_API_URL", "https://open.bigmodel.cn/api/paas/v4/chat/completions")
    glm_model = _env("GLM_MODEL", "glm-4-flash")

    perplexity_api_key = _env("PERPLEXITY_API_KEY")
    perplexity_api_url = _env("PERPLEXITY_API_URL", "https://api.perplexity.ai/chat/completions")
    perplexity_model = _env("PERPLEXITY_MODEL", "sonar-pro")

    tavily_api_key = _env("TAVILY_API_KEY")
    tavily_api_url = _env("TAVILY_API_URL", "https://api.tavily.com/search")
    tavily_mcp_url = _env("TAVILY_MCP_URL", "https://mcp.tavily.com/mcp/")
    tavily_tool_name = _env("TAVILY_MCP_TOOL")
    tavily_search_depth = _env("TAVILY_SEARCH_DEPTH", "advanced")
    tavily_topic = _env("TAVILY_TOPIC", "general")
    tavily_time_range = _env("TAVILY_TIME_RANGE")
    tavily_include_answer = _env("TAVILY_INCLUDE_ANSWER", "false")
    tavily_include_raw = _env("TAVILY_INCLUDE_RAW_CONTENT", "false")
    tavily_auto_parameters = _env("TAVILY_AUTO_PARAMETERS", "false")

    try:
        builtin_timeout = float(_env("WEB_SEARCH_BUILTIN_TIMEOUT", "40.0"))
    except Exception:
        builtin_timeout = 40.0

    try:
        perplexity_timeout = float(_env("WEB_SEARCH_PERPLEXITY_TIMEOUT", "30.0"))
    except Exception:
        perplexity_timeout = 30.0

    try:
        tavily_timeout = float(_env("WEB_SEARCH_TAVILY_TIMEOUT", "30.0"))
    except Exception:
        tavily_timeout = 30.0

    return SearchSettings(
        default_provider=default_provider or "builtin",
        builtin_provider=builtin_provider or "qwen",
        qwen_api_key=qwen_api_key,
        qwen_api_url=qwen_api_url or "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        qwen_model=qwen_model or "qwen-turbo",
        glm_api_key=glm_api_key,
        glm_api_url=glm_api_url or "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        glm_model=glm_model or "glm-4-flash",
        builtin_request_timeout=builtin_timeout,
        perplexity_api_key=perplexity_api_key,
        perplexity_api_url=perplexity_api_url or "https://api.perplexity.ai/chat/completions",
        perplexity_model=perplexity_model or "sonar-pro",
        perplexity_timeout=perplexity_timeout,
        tavily_api_key=tavily_api_key,
        tavily_api_url=tavily_api_url or "https://api.tavily.com/search",
        tavily_mcp_url=tavily_mcp_url or "https://mcp.tavily.com/mcp/",
        tavily_timeout=tavily_timeout,
        tavily_tool_name=tavily_tool_name,
        tavily_search_depth=(tavily_search_depth or "advanced"),
        tavily_topic=(tavily_topic or "general"),
        tavily_time_range=tavily_time_range,
        tavily_include_answer=(str(tavily_include_answer).lower() in {"1", "true", "yes", "advanced", "basic"}),
        tavily_include_raw_content=(str(tavily_include_raw).lower() in {"1", "true", "yes", "markdown", "text"}),
        tavily_auto_parameters=(str(tavily_auto_parameters).lower() in {"1", "true", "yes"}),
    )


def reset_search_settings_cache() -> None:
    """测试场景下清理缓存"""

    get_search_settings.cache_clear()  # type: ignore[attr-defined]
