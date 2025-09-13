"""Services package for business logic.

This package has been reorganized into subpackages to reduce top-level clutter.
To preserve backward compatibility for existing import paths like
`app.services.<module>`, we register lightweight submodule aliases that
point to the new locations under subpackages.
"""

from importlib import import_module
import sys as _sys


_ALIAS_MAP = {
    # foundation
    "settings": "app.services.foundation.settings",
    "config": "app.services.foundation.config",
    "logging_config": "app.services.foundation.logging_config",

    # llm
    "llm_service": "app.services.llm.llm_service",
    "llm_cache": "app.services.llm.llm_cache",

    # embeddings
    "embeddings": "app.services.embeddings.embeddings",
    "thread_safe_embeddings": "app.services.embeddings.thread_safe_embeddings",
    "thread_safe_cache": "app.services.embeddings.thread_safe_cache",
    "thread_safe_batch_processor": "app.services.embeddings.thread_safe_batch_processor",
    "thread_safe_async_manager": "app.services.embeddings.thread_safe_async_manager",
    "embedding_batch_processor": "app.services.embeddings.embedding_batch_processor",
    "glm_api_client": "app.services.embeddings.glm_api_client",
    "cache": "app.services.embeddings.cache",
    "similarity_calculator": "app.services.embeddings.similarity_calculator",
    "async_embedding_manager": "app.services.embeddings.async_embedding_manager",

    # context
    "context": "app.services.context.context",
    "context_budget": "app.services.context.context_budget",
    "index_root": "app.services.context.index_root",
    "retrieval": "app.services.context.retrieval",
    "structure_prior": "app.services.context.structure_prior",
    "graph_attention": "app.services.context.graph_attention",

    # evaluation
    "evaluation_cache": "app.services.evaluation.evaluation_cache",
    "evaluation_supervisor": "app.services.evaluation.evaluation_supervisor",
    "expert_evaluator": "app.services.evaluation.expert_evaluator",
    "llm_evaluator": "app.services.evaluation.llm_evaluator",
    "content_evaluator": "app.services.evaluation.content_evaluator",
    "adversarial_evaluator": "app.services.evaluation.adversarial_evaluator",
    "meta_evaluator": "app.services.evaluation.meta_evaluator",
    "phage_evaluator": "app.services.evaluation.phage_evaluator",
    "benchmark": "app.services.evaluation.benchmark",

    # planning (do not alias package name to a submodule to avoid import conflicts)
    "recursive_decomposition": "app.services.planning.recursive_decomposition",
    "decomposition_with_evaluation": "app.services.planning.decomposition_with_evaluation",
    "tool_aware_decomposition": "app.services.planning.tool_aware_decomposition",

    # memory
    "memory_service": "app.services.memory.memory_service",
    "unified_cache": "app.services.memory.unified_cache",

    # optional/legacy
    "error_decorator": "app.services.legacy.error_decorator",
    "contrastive_learning": "app.services.legacy.contrastive_learning",
    "base_evaluator": "app.services.evaluation.base_evaluator",
}


def _register_aliases():
    pkg_name = __name__
    for short, target in _ALIAS_MAP.items():
        alias = f"{pkg_name}.{short}"
        if alias in _sys.modules:
            continue
        try:
            _sys.modules[alias] = import_module(target)
        except Exception:
            # Best-effort: ignore missing optional modules
            pass


_register_aliases()

__all__ = []
