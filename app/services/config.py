#!/usr/bin/env python3
"""
GLM配置管理模块

统一管理GLM相关的环境变量和配置参数，
提供类型安全的配置访问接口。
"""

import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class GLMConfig:
    """GLM服务配置类"""

    # API配置
    api_key: Optional[str]
    api_url: str

    # Embedding配置
    embedding_model: str
    embedding_dimension: int
    max_batch_size: int

    # 检索配置
    default_semantic_k: int
    min_similarity_threshold: float

    # 性能配置
    max_retries: int
    retry_delay: float
    request_timeout: int

    # 调试配置
    mock_mode: bool
    debug_mode: bool

    @classmethod
    def from_env(cls) -> "GLMConfig":
        """从环境变量创建配置实例"""
        return cls(
            # API配置
            api_key=os.getenv("GLM_API_KEY"),
            api_url=os.getenv("GLM_API_URL", "https://open.bigmodel.cn/api/paas/v4/embeddings"),
            # Embedding配置
            embedding_model=os.getenv("GLM_EMBEDDING_MODEL", "embedding-3"),
            embedding_dimension=int(os.getenv("GLM_EMBEDDING_DIM", "1536")),
            max_batch_size=int(os.getenv("GLM_BATCH_SIZE", "25")),
            # 检索配置
            default_semantic_k=int(os.getenv("SEMANTIC_DEFAULT_K", "5")),
            min_similarity_threshold=float(os.getenv("SEMANTIC_MIN_SIMILARITY", "0.3")),
            # 性能配置
            max_retries=int(os.getenv("GLM_MAX_RETRIES", "3")),
            retry_delay=float(os.getenv("GLM_RETRY_DELAY", "1.0")),
            request_timeout=int(os.getenv("GLM_REQUEST_TIMEOUT", "30")),
            # 调试配置
            mock_mode=cls._parse_bool(os.getenv("LLM_MOCK", "0")),
            debug_mode=cls._parse_bool(os.getenv("GLM_DEBUG", "0")),
        )

    @staticmethod
    def _parse_bool(val: Optional[str]) -> bool:
        """解析布尔值环境变量"""
        if not val:
            return False
        return val.lower() in ("true", "1", "yes", "on")

    def validate(self) -> None:
        """验证配置的有效性"""
        if not self.mock_mode and not self.api_key:
            raise ValueError("GLM_API_KEY is required when not in mock mode")

        if self.embedding_dimension <= 0:
            raise ValueError("Embedding dimension must be positive")

        if self.max_batch_size <= 0:
            raise ValueError("Batch size must be positive")

        if not (0.0 <= self.min_similarity_threshold <= 1.0):
            raise ValueError("Similarity threshold must be between 0.0 and 1.0")


# 全局配置实例
_config: Optional[GLMConfig] = None


def get_config() -> GLMConfig:
    """获取全局配置实例"""
    global _config
    if _config is None:
        _config = GLMConfig.from_env()
        _config.validate()
    return _config


def reload_config() -> GLMConfig:
    """重新加载配置（主要用于测试）"""
    global _config
    _config = None
    return get_config()
