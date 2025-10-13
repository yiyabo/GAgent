#!/usr/bin/env python3
"""
GLM配置管理模块

集中化管理 GLM（尤其是 Embeddings）相关配置。
从 app.services.settings 读取（单一来源），避免在多个模块中直接读取环境变量。
"""

from dataclasses import dataclass
from typing import Optional

from app.services.foundation.settings import get_settings


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
        """从集中配置创建配置实例（不直接读取 os.getenv）"""
        s = get_settings()

        def _derive_embeddings_url(chat_url: Optional[str]) -> str:
            # 若未显式配置 embeddings API，则基于 chat/completions 推导，最后回退到默认
            default_url = "https://open.bigmodel.cn/api/paas/v4/embeddings"
            if not chat_url:
                return default_url
            cu = str(chat_url)
            if "chat/completions" in cu:
                return cu.replace("chat/completions", "embeddings")
            # 若用户直接给的是 embeddings 地址或其他基地址，则原样使用
            return cu if "/embeddings" in cu else default_url

        api_url = s.glm_embeddings_api_url or _derive_embeddings_url(s.glm_api_url)

        return cls(
            # API配置
            api_key=s.glm_api_key,
            api_url=api_url,
            # Embedding配置
            embedding_model=s.glm_embedding_model,
            embedding_dimension=int(s.glm_embedding_dimension),
            max_batch_size=int(s.glm_batch_size),
            # 检索配置
            default_semantic_k=int(s.semantic_default_k),
            min_similarity_threshold=float(s.semantic_min_similarity),
            # 性能配置
            max_retries=int(s.glm_max_retries),
            retry_delay=float(s.glm_retry_delay),
            request_timeout=int(s.glm_request_timeout),
            # 调试配置
            mock_mode=bool(s.llm_mock),
            debug_mode=bool(getattr(s, "glm_debug", False)),
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
