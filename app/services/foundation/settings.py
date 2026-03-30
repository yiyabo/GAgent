#!/usr/bin/env python3
"""
mediumconfiguration(AppSettings)

: 
- log, database, GLM  LLM providerconfiguration
- (Embeddings)(Chat)relatedparameter,  services read
- defaultload, support .env()
- log
"""
import os
from functools import lru_cache
from typing import Optional

try:
    from pydantic import ConfigDict  # type: ignore
except Exception:  # pragma: no cover - pydantic v1 fallback
    ConfigDict = None  # type: ignore

_USE_PYDANTIC = False
_DOTENV_LOADED = False
try:
    from pydantic import Field  # type: ignore
    from pydantic_settings import BaseSettings  # type: ignore

    _USE_PYDANTIC = True
except Exception:
    try:
        from pydantic import BaseSettings, Field  # type: ignore

        _USE_PYDANTIC = True
    except Exception:
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

# Absolute ceiling for injected chat history (env `CHAT_HISTORY_MAX_MESSAGES` is clamped to this).
CHAT_HISTORY_ABS_MAX = 200

if _USE_PYDANTIC:

    class AppSettings(BaseSettings):
        log_level: str = Field(default="INFO", env="LOG_LEVEL")
        # json|plain
        log_format: str = Field(default="json", env="LOG_FORMAT")

        database_url: str = Field(default="sqlite:///./tasks.db", env="DATABASE_URL")

        base_url: Optional[str] = Field(default=None, env="BASE_URL")

        glm_api_key: Optional[str] = Field(default=None, env="GLM_API_KEY")
        glm_api_url: str = Field(
            default="https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
            env="GLM_API_URL",
        )
        glm_model: str = Field(default="qwen3-max-2026-01-23", env="GLM_MODEL")
        glm_request_timeout: int = Field(default=60, env="GLM_REQUEST_TIMEOUT")
        llm_request_timeout: int = Field(default=60, env="LLM_REQUEST_TIMEOUT")
        llm_stream_timeout: int = Field(default=300, env="LLM_STREAM_TIMEOUT")
        llm_mock: bool = Field(default=False, env="LLM_MOCK")
        llm_retries: int = Field(default=2, env="LLM_RETRIES")
        llm_backoff_base: float = Field(default=0.5, env="LLM_BACKOFF_BASE")

        perplexity_api_key: Optional[str] = Field(default=None, env="PERPLEXITY_API_KEY")
        perplexity_api_url: str = Field(
            default="https://api.perplexity.ai/chat/completions",
            env="PERPLEXITY_API_URL",
        )
        perplexity_model: str = Field(default="sonar-reasoning-pro", env="PERPLEXITY_MODEL")

        qwen_api_key: Optional[str] = Field(default=None, env="QWEN_API_KEY")
        qwen_api_url: str = Field(
            default="https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
            env="QWEN_API_URL",
        )
        qwen_model: str = Field(default="qwen3.5-plus", env="QWEN_MODEL")

        kimi_api_key: Optional[str] = Field(default=None, env="KIMI_API_KEY")
        kimi_api_url: Optional[str] = Field(default=None, env="KIMI_API_URL")
        kimi_model: str = Field(default="kimi-k2.5", env="KIMI_MODEL")

        qwen_embedding_api_url: str = Field(
            default="https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings",
            env="QWEN_EMBEDDING_API_URL",
        )
        qwen_embedding_model: str = Field(default="text-embedding-v4", env="QWEN_EMBEDDING_MODEL")
        qwen_embedding_dimension: int = Field(default=1536, env="QWEN_EMBEDDING_DIM")

        embedding_provider: str = Field(default="qwen", env="EMBEDDING_PROVIDER")

        llm_provider: str = Field(default="qwen", env="LLM_PROVIDER")  # qwen preferred

        glm_embeddings_api_url: Optional[str] = Field(default=None, env="GLM_EMBEDDINGS_API_URL")
        glm_embedding_model: str = Field(default="embedding-3", env="GLM_EMBEDDING_MODEL")
        glm_embedding_dimension: int = Field(default=1536, env="GLM_EMBEDDING_DIM")
        glm_batch_size: int = Field(default=25, env="GLM_BATCH_SIZE")
        semantic_default_k: int = Field(default=5, env="SEMANTIC_DEFAULT_K")
        semantic_min_similarity: float = Field(default=0.3, env="SEMANTIC_MIN_SIMILARITY")
        glm_max_retries: int = Field(default=3, env="GLM_MAX_RETRIES")
        glm_retry_delay: float = Field(default=1.0, env="GLM_RETRY_DELAY")
        glm_debug: bool = Field(default=False, env="GLM_DEBUG")

        embedding_cache_size: int = Field(default=10000, env="EMBEDDING_CACHE_SIZE")
        embedding_cache_persistent: bool = Field(default=True, env="EMBEDDING_CACHE_PERSISTENT")

        ctx_debug: bool = Field(default=False, env=["CTX_DEBUG", "CONTEXT_DEBUG"])
        budget_debug: bool = Field(default=False, env="BUDGET_DEBUG")
        decomp_debug: bool = Field(default=False, env="DECOMP_DEBUG")
        global_index_path: str = Field(default="INDEX.md", env="GLOBAL_INDEX_PATH")

        openai_api_key: Optional[str] = Field(default=None, env=["OPENAI_API_KEY", "GPT_API_KEY"])
        xai_api_key: Optional[str] = Field(default=None, env=["XAI_API_KEY", "GROK_API_KEY"])
        anthropic_api_key: Optional[str] = Field(default=None, env=["ANTHROPIC_API_KEY", "CLAUDE_API_KEY"])
        tavily_api_key: Optional[str] = Field(default=None, env="TAVILY_API_KEY")

        backend_host: str = Field(default="0.0.0.0", env="BACKEND_HOST")
        backend_port: int = Field(default=9000, env="BACKEND_PORT")
        app_env: str = Field(default="development", env="APP_ENV")
        cors_origins: str = Field(
            default="http://localhost:3000,http://127.0.0.1:3000,http://localhost:3001,http://127.0.0.1:3001",
            env="CORS_ORIGINS"
        )
        auth_mode: str = Field(default="local", env="AUTH_MODE")
        auth_cookie_name: str = Field(default="ga_session", env="AUTH_COOKIE_NAME")
        auth_session_ttl_hours: int = Field(default=168, env="AUTH_SESSION_TTL_HOURS")
        auth_open_signup: bool = Field(default=True, env="AUTH_OPEN_SIGNUP")
        chat_include_action_summary: bool = Field(
            default=True, env="CHAT_INCLUDE_ACTION_SUMMARY"
        )
        # Recent messages injected into prompts / DeepThink context (per session turn).
        # Higher values increase recall but also prompt size and API cost.
        chat_history_max_messages: int = Field(
            default=80, ge=1, le=CHAT_HISTORY_ABS_MAX, env="CHAT_HISTORY_MAX_MESSAGES"
        )
        job_log_retention_days: int = Field(
            default=30, env="JOB_LOG_RETENTION_DAYS"
        )
        job_log_max_rows: int = Field(
            default=10000, env="JOB_LOG_MAX_ROWS"
        )

        # Plan rubric evaluation & auto-optimization
        plan_rubric_threshold: int = Field(default=80, env="PLAN_RUBRIC_THRESHOLD")
        plan_optimize_max_iters: int = Field(default=3, env="PLAN_OPTIMIZE_MAX_ITERS")

        # DeepThink configuration
        # Mode:
        # - explicit: only explicit commands trigger DeepThink
        # - smart: LLM-routed decision (default)
        # - aggressive: kept as backward-compatible alias of smart
        deep_think_mode: str = Field(default="smart", env="DEEP_THINK_MODE")
        # Legacy threshold; may be used by compatibility paths.
        deep_think_confidence_threshold: float = Field(default=0.5, env="DEEP_THINK_CONFIDENCE_THRESHOLD")
        # Legacy min-length setting; may be used by compatibility paths.
        deep_think_min_message_length: int = Field(default=8, env="DEEP_THINK_MIN_MESSAGE_LENGTH")

        # Extended Thinking (enable_thinking) configuration
        thinking_enabled: bool = Field(default=True, env="THINKING_ENABLED")
        thinking_budget: int = Field(default=10000, env="THINKING_BUDGET")
        thinking_budget_simple: int = Field(default=2000, env="THINKING_BUDGET_SIMPLE")

        enable_skills: bool = Field(default=True, env="ENABLE_SKILLS")
        skill_budget_chars: int = Field(default=6000, env="SKILL_BUDGET_CHARS")
        skill_selection_mode: str = Field(default="hybrid", env="SKILL_SELECTION_MODE")
        skill_max_per_task: int = Field(default=3, env="SKILL_MAX_PER_TASK")
        skill_trace_enabled: bool = Field(default=True, env="SKILL_TRACE_ENABLED")

        amem_enabled: bool = Field(
            default=False, env="AMEM_ENABLED"
        )
        amem_url: str = Field(
            default="http://localhost:8001", env="AMEM_URL"
        )

        if ConfigDict is not None:
            model_config = ConfigDict(env_file=".env", extra="allow", case_sensitive=False)
        else:
            class Config:
                env_file = ".env"
                case_sensitive = False
                extra = "allow"

else:

    class AppSettings:  # 
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
            self.base_url = os.getenv("BASE_URL")
            self.glm_api_key = os.getenv("GLM_API_KEY")
            self.glm_api_url = os.getenv("GLM_API_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions")
            self.glm_model = os.getenv("GLM_MODEL", "qwen3-max-2026-01-23")
            try:
                self.glm_request_timeout = int(os.getenv("GLM_REQUEST_TIMEOUT", "60"))
            except Exception:
                self.glm_request_timeout = 60
            try:
                self.llm_request_timeout = int(os.getenv("LLM_REQUEST_TIMEOUT", "60"))
            except Exception:
                self.llm_request_timeout = 60
            try:
                self.llm_stream_timeout = int(os.getenv("LLM_STREAM_TIMEOUT", "300"))
            except Exception:
                self.llm_stream_timeout = 300
            self.llm_mock = os.getenv("LLM_MOCK", "").strip().lower() in {"1", "true", "yes", "on"}

            self.perplexity_api_key = os.getenv("PERPLEXITY_API_KEY")
            self.perplexity_api_url = os.getenv("PERPLEXITY_API_URL", "https://api.perplexity.ai/chat/completions")
            self.perplexity_model = os.getenv("PERPLEXITY_MODEL", "sonar-reasoning-pro")

            self.llm_provider = os.getenv("LLM_PROVIDER", "qwen")
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

            self.qwen_api_key = os.getenv("QWEN_API_KEY")
            self.qwen_api_url = os.getenv("QWEN_API_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions")
            self.qwen_model = os.getenv("QWEN_MODEL", "qwen3.5-plus")

            self.kimi_api_key = os.getenv("KIMI_API_KEY")
            self.kimi_api_url = os.getenv("KIMI_API_URL")
            self.kimi_model = os.getenv("KIMI_MODEL", "kimi-k2.5")

            self.qwen_embedding_api_url = os.getenv("QWEN_EMBEDDING_API_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings")
            self.qwen_embedding_model = os.getenv("QWEN_EMBEDDING_MODEL", "text-embedding-v4")
            try:
                self.qwen_embedding_dimension = int(os.getenv("QWEN_EMBEDDING_DIM", "1536"))
            except Exception:
                self.qwen_embedding_dimension = 1536

            self.embedding_provider = os.getenv("EMBEDDING_PROVIDER", "qwen")

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

            try:
                self.embedding_cache_size = int(os.getenv("EMBEDDING_CACHE_SIZE", "10000"))
            except Exception:
                self.embedding_cache_size = 10000
            self.embedding_cache_persistent = (os.getenv("EMBEDDING_CACHE_PERSISTENT", "1").strip() == "1")

            def _truthy(v: str) -> bool:
                return str(v).strip().lower() in {"1", "true", "yes", "on"}

            self.ctx_debug = _truthy(os.getenv("CTX_DEBUG", "")) or _truthy(os.getenv("CONTEXT_DEBUG", ""))
            self.budget_debug = _truthy(os.getenv("BUDGET_DEBUG", ""))
            self.decomp_debug = _truthy(os.getenv("DECOMP_DEBUG", ""))
            self.global_index_path = os.getenv("GLOBAL_INDEX_PATH", "INDEX.md")

            self.backend_host = os.getenv("BACKEND_HOST", "0.0.0.0")
            try:
                self.backend_port = int(os.getenv("BACKEND_PORT", "9000"))
            except Exception:
                self.backend_port = 9000
            self.app_env = os.getenv("APP_ENV", "development")
            self.cors_origins = os.getenv(
                "CORS_ORIGINS",
                "http://localhost:3000,http://127.0.0.1:3000,http://localhost:3001,http://127.0.0.1:3001"
            )
            self.auth_mode = os.getenv("AUTH_MODE", "local")
            self.auth_cookie_name = os.getenv("AUTH_COOKIE_NAME", "ga_session")
            try:
                self.auth_session_ttl_hours = int(os.getenv("AUTH_SESSION_TTL_HOURS", "168"))
            except Exception:
                self.auth_session_ttl_hours = 168
            self.auth_open_signup = os.getenv("AUTH_OPEN_SIGNUP", "1").strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
            self.chat_include_action_summary = os.getenv("CHAT_INCLUDE_ACTION_SUMMARY", "1").strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
            try:
                self.job_log_retention_days = int(os.getenv("JOB_LOG_RETENTION_DAYS", "30"))
            except Exception:
                self.job_log_retention_days = 30
            try:
                self.job_log_max_rows = int(os.getenv("JOB_LOG_MAX_ROWS", "10000"))
            except Exception:
                self.job_log_max_rows = 10000
            # Plan rubric evaluation & auto-optimization
            try:
                self.plan_rubric_threshold = int(os.getenv("PLAN_RUBRIC_THRESHOLD", "80"))
            except Exception:
                self.plan_rubric_threshold = 80
            try:
                self.plan_optimize_max_iters = int(os.getenv("PLAN_OPTIMIZE_MAX_ITERS", "3"))
            except Exception:
                self.plan_optimize_max_iters = 3

            # DeepThink settings
            self.deep_think_mode = os.getenv("DEEP_THINK_MODE", "smart")
            try:
                self.deep_think_confidence_threshold = float(
                    os.getenv("DEEP_THINK_CONFIDENCE_THRESHOLD", "0.5")
                )
            except Exception:
                self.deep_think_confidence_threshold = 0.5
            try:
                self.deep_think_min_message_length = int(
                    os.getenv("DEEP_THINK_MIN_MESSAGE_LENGTH", "8")
                )
            except Exception:
                self.deep_think_min_message_length = 8

            # Extended Thinking settings
            self.thinking_enabled = os.getenv("THINKING_ENABLED", "1").strip().lower() in {
                "1", "true", "yes", "on",
            }
            try:
                self.thinking_budget = int(os.getenv("THINKING_BUDGET", "10000"))
            except Exception:
                self.thinking_budget = 10000
            try:
                self.thinking_budget_simple = int(os.getenv("THINKING_BUDGET_SIMPLE", "2000"))
            except Exception:
                self.thinking_budget_simple = 2000

            self.enable_skills = os.getenv("ENABLE_SKILLS", "1").strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
            try:
                self.skill_budget_chars = int(os.getenv("SKILL_BUDGET_CHARS", "6000"))
            except Exception:
                self.skill_budget_chars = 6000
            skill_selection_mode = os.getenv("SKILL_SELECTION_MODE", "hybrid").strip().lower()
            if skill_selection_mode not in {"hybrid", "llm_only"}:
                skill_selection_mode = "hybrid"
            self.skill_selection_mode = skill_selection_mode
            try:
                self.skill_max_per_task = int(os.getenv("SKILL_MAX_PER_TASK", "3"))
            except Exception:
                self.skill_max_per_task = 3
            if self.skill_max_per_task <= 0:
                self.skill_max_per_task = 3
            self.skill_trace_enabled = os.getenv("SKILL_TRACE_ENABLED", "1").strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }

            try:
                raw_ch = int(os.getenv("CHAT_HISTORY_MAX_MESSAGES", "80"))
            except Exception:
                raw_ch = 80
            self.chat_history_max_messages = max(1, min(CHAT_HISTORY_ABS_MAX, raw_ch))


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """getconfiguration()"""
    settings = AppSettings()  #  .env read

    # Ensure BASE_URL fallback stays in sync with BACKEND_HOST/BACKEND_PORT
    base_url = getattr(settings, "base_url", None)
    if not base_url:
        host = getattr(settings, "backend_host", "0.0.0.0")
        port = getattr(settings, "backend_port", 9000)
        settings.base_url = f"http://{host}:{port}"

    return settings
