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

    try:
        builtin_timeout = float(_env("WEB_SEARCH_BUILTIN_TIMEOUT", "40.0"))
    except Exception:
        builtin_timeout = 40.0

    try:
        perplexity_timeout = float(_env("WEB_SEARCH_PERPLEXITY_TIMEOUT", "30.0"))
    except Exception:
        perplexity_timeout = 30.0

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
    )


def reset_search_settings_cache() -> None:
    """测试场景下清理缓存"""

    get_search_settings.cache_clear()  # type: ignore[attr-defined]
