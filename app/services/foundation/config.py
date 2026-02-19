#!/usr/bin/env python3
"""
GLMconfiguration

medium GLM( Embeddings)relatedconfiguration. 
app.services.settings read(), mediumread. 
"""

from dataclasses import dataclass
from typing import Optional

from app.services.foundation.settings import get_settings


@dataclass
class GLMConfig:
    """GLMserviceconfiguration(support Embedding Provider)"""

    api_key: Optional[str]
    api_url: str

    embedding_model: str
    embedding_dimension: int
    max_batch_size: int

    # Embedding provider: qwen, glm, local
    embedding_provider: str

    qwen_embedding_api_url: str
    qwen_embedding_model: str
    qwen_embedding_dimension: int
    qwen_api_key: Optional[str]

    use_local_embedding: bool
    local_embedding_model: str

    default_semantic_k: int
    min_similarity_threshold: float

    max_retries: int
    retry_delay: float
    request_timeout: int

    mock_mode: bool
    debug_mode: bool

    @classmethod
    def from_env(cls) -> "GLMConfig":
        """mediumconfigurationcreateconfiguration(read os.getenv)"""
        s = get_settings()

        embedding_provider = getattr(s, "embedding_provider", "qwen")

        def _derive_embeddings_url(chat_url: Optional[str]) -> str:
            default_url = "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"
            if not chat_url:
                return default_url
            cu = str(chat_url)
            if "chat/completions" in cu:
                return cu.replace("chat/completions", "embeddings")
            return cu if "/embeddings" in cu else default_url

        api_url = s.glm_embeddings_api_url or _derive_embeddings_url(s.glm_api_url)

        use_local = embedding_provider == "local"
        local_model = getattr(s, "local_embedding_model", "sentence-transformers/all-mpnet-base-v2")

        qwen_embedding_api_url = getattr(s, "qwen_embedding_api_url", "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings")
        qwen_embedding_model = getattr(s, "qwen_embedding_model", "text-embedding-v4")
        qwen_embedding_dimension = int(getattr(s, "qwen_embedding_dimension", 1536))
        qwen_api_key = getattr(s, "qwen_api_key", None)

        if embedding_provider == "qwen":
            embedding_model = qwen_embedding_model
            embedding_dimension = qwen_embedding_dimension
        else:
            embedding_model = s.glm_embedding_model
            embedding_dimension = int(s.glm_embedding_dimension)

        return cls(
            api_key=s.glm_api_key,
            api_url=api_url,
            embedding_model=embedding_model,
            embedding_dimension=embedding_dimension,
            max_batch_size=int(s.glm_batch_size),
            # Embedding provider
            embedding_provider=embedding_provider,
            qwen_embedding_api_url=qwen_embedding_api_url,
            qwen_embedding_model=qwen_embedding_model,
            qwen_embedding_dimension=qwen_embedding_dimension,
            qwen_api_key=qwen_api_key,
            use_local_embedding=use_local,
            local_embedding_model=local_model,
            default_semantic_k=int(s.semantic_default_k),
            min_similarity_threshold=float(s.semantic_min_similarity),
            max_retries=int(s.glm_max_retries),
            retry_delay=float(s.glm_retry_delay),
            request_timeout=int(s.glm_request_timeout),
            mock_mode=bool(s.llm_mock),
            debug_mode=bool(getattr(s, "glm_debug", False)),
        )

    @staticmethod
    def _parse_bool(val: Optional[str]) -> bool:
        """"""
        if not val:
            return False
        return val.lower() in ("true", "1", "yes", "on")

    def validate(self) -> None:
        """configuration"""
        if not self.mock_mode and not self.api_key:
            raise ValueError("GLM_API_KEY is required when not in mock mode")

        if self.embedding_dimension <= 0:
            raise ValueError("Embedding dimension must be positive")

        if self.max_batch_size <= 0:
            raise ValueError("Batch size must be positive")

        if not (0.0 <= self.min_similarity_threshold <= 1.0):
            raise ValueError("Similarity threshold must be between 0.0 and 1.0")


_config: Optional[GLMConfig] = None


def get_config() -> GLMConfig:
    """getconfiguration"""
    global _config
    if _config is None:
        _config = GLMConfig.from_env()
        _config.validate()
    return _config


def reload_config() -> GLMConfig:
    """loadconfiguration()"""
    global _config
    _config = None
    return get_config()
