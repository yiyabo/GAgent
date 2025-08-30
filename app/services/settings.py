#!/usr/bin/env python3
"""
集中化应用配置（AppSettings）

目标：
- 统一管理日志、数据库、GLM 与外部 LLM 提供商密钥等配置
- 默认从环境变量加载，支持 .env（如存在）
- 为后续扩展结构化日志与指标埋点提供稳定入口
"""
from functools import lru_cache
from typing import Optional
import os

_USE_PYDANTIC = False
try:
    # Pydantic v2: BaseSettings 已迁移到 pydantic_settings 包
    from pydantic_settings import BaseSettings  # type: ignore
    from pydantic import Field  # type: ignore
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


if _USE_PYDANTIC:
    class AppSettings(BaseSettings):
        # 日志配置
        log_level: str = Field(default="INFO", env="LOG_LEVEL")
        # json|plain
        log_format: str = Field(default="json", env="LOG_FORMAT")

        # 数据库配置（保留扩展位）
        database_url: str = Field(default="sqlite:///./tasks.db", env="DATABASE_URL")

        # GLM / LLM 配置
        glm_api_key: Optional[str] = Field(default=None, env="GLM_API_KEY")
        glm_api_url: str = Field(
            default="https://open.bigmodel.cn/api/paas/v4/chat/completions",
            env="GLM_API_URL",
        )
        glm_model: str = Field(default="glm-4-flash", env="GLM_MODEL")
        glm_request_timeout: int = Field(default=60, env="GLM_REQUEST_TIMEOUT")
        llm_mock: bool = Field(default=False, env="LLM_MOCK")
        llm_retries: int = Field(default=2, env="LLM_RETRIES")
        llm_backoff_base: float = Field(default=0.5, env="LLM_BACKOFF_BASE")

        # 外部提供商（可选）
        openai_api_key: Optional[str] = Field(default=None, env=["OPENAI_API_KEY", "GPT_API_KEY"])
        xai_api_key: Optional[str] = Field(default=None, env=["XAI_API_KEY", "GROK_API_KEY"])
        anthropic_api_key: Optional[str] = Field(default=None, env=["ANTHROPIC_API_KEY", "CLAUDE_API_KEY"])

        class Config:
            env_file = ".env"
            case_sensitive = False
else:
    class AppSettings:  # 轻量回退实现
        def __init__(self) -> None:
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
            self.llm_mock = (os.getenv("LLM_MOCK", "").strip().lower() in {"1","true","yes","on"})
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


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """获取全局应用配置（带缓存）"""
    return AppSettings()  # 自动从环境变量与 .env 读取


