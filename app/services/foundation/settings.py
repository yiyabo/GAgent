#!/usr/bin/env python3
"""
集中化应用配置（AppSettings）

目标：
- 统一管理日志、数据库、GLM 与外部 LLM 提供商密钥等配置
- 合并嵌入（Embeddings）与聊天（Chat）相关参数，避免在 services 下分散读取环境变量
- 默认从环境变量加载，支持 .env（如存在）
- 为后续扩展结构化日志与指标埋点提供稳定入口
"""
import os
from functools import lru_cache
from typing import Optional

_USE_PYDANTIC = False
_DOTENV_LOADED = False
try:
    # Pydantic v2: BaseSettings 已迁移到 pydantic_settings 包
    from pydantic import Field  # type: ignore
    from pydantic_settings import BaseSettings  # type: ignore

    _USE_PYDANTIC = True
except Exception:
    try:
        # Pydantic v1 回退
        from pydantic import BaseSettings, Field  # type: ignore

        _USE_PYDANTIC = True
    except Exception:
        # 本地回退：无 pydantic 时提供轻量实现
        class BaseSettings:  # type: ignore
            pass

        def Field(default=None, **kwargs):  # type: ignore
            return default

        _USE_PYDANTIC = False

# Best-effort: load .env even in fallback or mixed environments
try:
    from dotenv import load_dotenv  # type: ignore

    load_dotenv()  # no-op if file missing; respects current working dir
    _DOTENV_LOADED = True
except Exception:
    _DOTENV_LOADED = False


if _USE_PYDANTIC:

    class AppSettings(BaseSettings):
        # 日志配置
        log_level: str = Field(default="INFO", env="LOG_LEVEL")
        # json|plain
        log_format: str = Field(default="json", env="LOG_FORMAT")

        # 数据库配置（保留扩展位）
        database_url: str = Field(default="sqlite:///./tasks.db", env="DATABASE_URL")
        
        # API服务配置
        base_url: str = Field(default="http://127.0.0.1:9000", env="BASE_URL")

        # GLM / LLM 配置
        glm_api_key: Optional[str] = Field(default=None, env="GLM_API_KEY")
        glm_api_url: str = Field(
            default="https://open.bigmodel.cn/api/paas/v4/chat/completions",
            env="GLM_API_URL",
        )
        glm_model: str = Field(default="glm-4-flash", env="GLM_MODEL")
        glm_request_timeout: int = Field(default=60, env="GLM_REQUEST_TIMEOUT")
        llm_request_timeout: int = Field(default=60, env="LLM_REQUEST_TIMEOUT")
        llm_mock: bool = Field(default=False, env="LLM_MOCK")
        llm_retries: int = Field(default=2, env="LLM_RETRIES")
        llm_backoff_base: float = Field(default=0.5, env="LLM_BACKOFF_BASE")

        # Perplexity API 配置
        perplexity_api_key: Optional[str] = Field(default=None, env="PERPLEXITY_API_KEY")
        perplexity_api_url: str = Field(
            default="https://api.perplexity.ai/chat/completions",
            env="PERPLEXITY_API_URL",
        )
        perplexity_model: str = Field(default="sonar-reasoning-pro", env="PERPLEXITY_MODEL")

        # QWEN API 配置
        qwen_api_key: Optional[str] = Field(default=None, env="QWEN_API_KEY")
        qwen_api_url: str = Field(
            default="https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
            env="QWEN_API_URL",
        )
        qwen_model: str = Field(default="qwen-turbo", env="QWEN_MODEL")

        # 通用LLM配置（用于选择提供商）
        llm_provider: str = Field(default="glm", env="LLM_PROVIDER")  # glm, perplexity, qwen, openai, etc.

        # GLM Embeddings 专用配置（集中到此，供 app.services.config 使用）
        glm_embeddings_api_url: Optional[str] = Field(default=None, env="GLM_EMBEDDINGS_API_URL")
        glm_embedding_model: str = Field(default="embedding-3", env="GLM_EMBEDDING_MODEL")
        glm_embedding_dimension: int = Field(default=1536, env="GLM_EMBEDDING_DIM")
        glm_batch_size: int = Field(default=25, env="GLM_BATCH_SIZE")
        semantic_default_k: int = Field(default=5, env="SEMANTIC_DEFAULT_K")
        semantic_min_similarity: float = Field(default=0.3, env="SEMANTIC_MIN_SIMILARITY")
        glm_max_retries: int = Field(default=3, env="GLM_MAX_RETRIES")
        glm_retry_delay: float = Field(default=1.0, env="GLM_RETRY_DELAY")
        glm_debug: bool = Field(default=False, env="GLM_DEBUG")

        # 嵌入缓存配置（避免在多个模块中直接读取环境变量）
        embedding_cache_size: int = Field(default=10000, env="EMBEDDING_CACHE_SIZE")
        embedding_cache_persistent: bool = Field(default=True, env="EMBEDDING_CACHE_PERSISTENT")

        # 调试与上下文配置
        ctx_debug: bool = Field(default=False, env=["CTX_DEBUG", "CONTEXT_DEBUG"])
        budget_debug: bool = Field(default=False, env="BUDGET_DEBUG")
        decomp_debug: bool = Field(default=False, env="DECOMP_DEBUG")
        global_index_path: str = Field(default="INDEX.md", env="GLOBAL_INDEX_PATH")

        # 外部提供商（可选）
        openai_api_key: Optional[str] = Field(default=None, env=["OPENAI_API_KEY", "GPT_API_KEY"])
        xai_api_key: Optional[str] = Field(default=None, env=["XAI_API_KEY", "GROK_API_KEY"])
        anthropic_api_key: Optional[str] = Field(default=None, env=["ANTHROPIC_API_KEY", "CLAUDE_API_KEY"])
        tavily_api_key: Optional[str] = Field(default=None, env="TAVILY_API_KEY")

        # 服务器配置
        backend_host: str = Field(default="0.0.0.0", env="BACKEND_HOST")
        backend_port: int = Field(default=9000, env="BACKEND_PORT")
        cors_origins: str = Field(default="http://localhost:3000", env="CORS_ORIGINS")

        class Config:
            env_file = ".env"
            case_sensitive = False

else:

    class AppSettings:  # 轻量回退实现
        def __init__(self) -> None:
            # Ensure .env is loaded in pure os.getenv path as well
            if not _DOTENV_LOADED:
                try:
                    from dotenv import load_dotenv  # type: ignore

                    load_dotenv()
                except Exception:
                    pass
            self.log_level = os.getenv("LOG_LEVEL", "INFO")
            self.log_format = os.getenv("LOG_FORMAT", "json")
            self.database_url = os.getenv("DATABASE_URL", "sqlite:///./tasks.db")
            self.glm_api_key = os.getenv("GLM_API_KEY")
            self.glm_api_url = os.getenv("GLM_API_URL", "https://open.bigmodel.cn/api/paas/v4/chat/completions")
            self.glm_model = os.getenv("GLM_MODEL", "glm-4-flash")
            try:
                self.glm_request_timeout = int(os.getenv("GLM_REQUEST_TIMEOUT", "60"))
            except Exception:
                self.glm_request_timeout = 60
            try:
                self.llm_request_timeout = int(os.getenv("LLM_REQUEST_TIMEOUT", "60"))
            except Exception:
                self.llm_request_timeout = 60
            self.llm_mock = os.getenv("LLM_MOCK", "").strip().lower() in {"1", "true", "yes", "on"}
            
            # Perplexity API 配置
            self.perplexity_api_key = os.getenv("PERPLEXITY_API_KEY")
            self.perplexity_api_url = os.getenv("PERPLEXITY_API_URL", "https://api.perplexity.ai/chat/completions")
            self.perplexity_model = os.getenv("PERPLEXITY_MODEL", "sonar-reasoning-pro")
            
            # 通用LLM配置
            self.llm_provider = os.getenv("LLM_PROVIDER", "glm")
            try:
                self.llm_retries = int(os.getenv("LLM_RETRIES", "2"))
            except Exception:
                self.llm_retries = 2
            try:
                self.llm_backoff_base = float(os.getenv("LLM_BACKOFF_BASE", "0.5"))
            except Exception:
                self.llm_backoff_base = 0.5
            self.openai_api_key = os.getenv("OPENAI_API_KEY") or os.getenv("GPT_API_KEY")
            self.xai_api_key = os.getenv("XAI_API_KEY") or os.getenv("GROK_API_KEY")
            self.anthropic_api_key = os.getenv("ANTHROPIC_API_KEY") or os.getenv("CLAUDE_API_KEY")
            self.tavily_api_key = os.getenv("TAVILY_API_KEY")

            # Embeddings 专用配置
            self.glm_embeddings_api_url = os.getenv("GLM_EMBEDDINGS_API_URL")
            self.glm_embedding_model = os.getenv("GLM_EMBEDDING_MODEL", "embedding-3")
            try:
                self.glm_embedding_dimension = int(os.getenv("GLM_EMBEDDING_DIM", "1536"))
            except Exception:
                self.glm_embedding_dimension = 1536
            try:
                self.glm_batch_size = int(os.getenv("GLM_BATCH_SIZE", "25"))
            except Exception:
                self.glm_batch_size = 25
            try:
                self.semantic_default_k = int(os.getenv("SEMANTIC_DEFAULT_K", "5"))
            except Exception:
                self.semantic_default_k = 5
            try:
                self.semantic_min_similarity = float(os.getenv("SEMANTIC_MIN_SIMILARITY", "0.3"))
            except Exception:
                self.semantic_min_similarity = 0.3
            try:
                self.glm_max_retries = int(os.getenv("GLM_MAX_RETRIES", "3"))
            except Exception:
                self.glm_max_retries = 3
            try:
                self.glm_retry_delay = float(os.getenv("GLM_RETRY_DELAY", "1.0"))
            except Exception:
                self.glm_retry_delay = 1.0
            self.glm_debug = os.getenv("GLM_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}

            # 嵌入缓存配置
            try:
                self.embedding_cache_size = int(os.getenv("EMBEDDING_CACHE_SIZE", "10000"))
            except Exception:
                self.embedding_cache_size = 10000
            self.embedding_cache_persistent = (os.getenv("EMBEDDING_CACHE_PERSISTENT", "1").strip() == "1")

            # 调试与上下文配置
            def _truthy(v: str) -> bool:
                return str(v).strip().lower() in {"1", "true", "yes", "on"}

            self.ctx_debug = _truthy(os.getenv("CTX_DEBUG", "")) or _truthy(os.getenv("CONTEXT_DEBUG", ""))
            self.budget_debug = _truthy(os.getenv("BUDGET_DEBUG", ""))
            self.decomp_debug = _truthy(os.getenv("DECOMP_DEBUG", ""))
            self.global_index_path = os.getenv("GLOBAL_INDEX_PATH", "INDEX.md")

            # 服务器配置
            self.backend_host = os.getenv("BACKEND_HOST", "0.0.0.0")
            try:
                self.backend_port = int(os.getenv("BACKEND_PORT", "9000"))
            except Exception:
                self.backend_port = 9000
            self.cors_origins = os.getenv("CORS_ORIGINS", "http://localhost:3000")


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """获取全局应用配置（带缓存）"""
    return AppSettings()  # 自动从环境变量与 .env 读取
