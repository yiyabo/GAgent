"""
Storage Services Package

This package contains storage-related services:
- milvus_service: Milvus vector database service
- hybrid_vector_storage: Hybrid vector storage manager
"""

# 使Milvus成为可选依赖
try:
    from .milvus_service import MilvusVectorService, get_milvus_service
except ImportError:
    MilvusVectorService = None
    get_milvus_service = None
from .hybrid_vector_storage import HybridVectorStorage, get_hybrid_storage

__all__ = [
    "MilvusVectorService",
    "get_milvus_service", 
    "HybridVectorStorage",
    "get_hybrid_storage",
]
