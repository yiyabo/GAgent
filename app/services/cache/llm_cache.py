"""
LLM Response Cache Implementation using Unified Base Cache

Provides caching for LLM API responses with intelligent key generation
and response-specific features.
"""

import hashlib
import json
import logging
from typing import Any, Dict, List, Optional

from .base_cache import BaseCache, CacheEntry

logger = logging.getLogger(__name__)


class LLMCacheEntry(CacheEntry):
    """Enhanced cache entry for LLM responses with response-specific metadata."""

    def __init__(
        self,
        key: str,
        value: Any,
        ttl: Optional[int] = None,
        model: str = "default",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        response_time: float = 0.0,
        cost: float = 0.0
    ):
        super().__init__(key, value, ttl)
        self.model = model
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens
        self.response_time = response_time
        self.cost = cost

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        data = super().to_dict()
        data.update({
            'model': self.model,
            'prompt_tokens': self.prompt_tokens,
            'completion_tokens': self.completion_tokens,
            'total_tokens': self.total_tokens,
            'response_time': self.response_time,
            'cost': self.cost
        })
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'LLMCacheEntry':
        """Create from dictionary."""
        # Extract base class fields
        key = data['key']
        value = data['value']
        ttl = data.get('ttl')

        # Extract LLM-specific fields
        model = data.get('model', 'default')
        prompt_tokens = data.get('prompt_tokens', 0)
        completion_tokens = data.get('completion_tokens', 0)
        total_tokens = data.get('total_tokens', 0)
        response_time = data.get('response_time', 0.0)
        cost = data.get('cost', 0.0)

        return cls(
            key=key,
            value=value,
            ttl=ttl,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            response_time=response_time,
            cost=cost
        )


class LLMCache(BaseCache):
    """
    Cache for LLM API responses with intelligent key generation.

    Extends BaseCache with LLM-specific features:
    - Smart prompt/response key generation
    - Token usage tracking
    - Cost calculation
    - Response time monitoring
    - Model-specific caching
    """

    def __init__(
        self,
        cache_name: str = "llm",
        max_size: int = 1000,
        default_ttl: int = 3600,  # 1 hour default for LLM responses
        enable_persistent: bool = True,
        cleanup_interval: int = 300
    ):
        """
        Initialize LLM cache.

        Args:
            cache_name: Name of the cache (default: "llm")
            max_size: Maximum number of entries in cache
            default_ttl: Default TTL in seconds (default: 3600)
            enable_persistent: Enable persistent storage
            cleanup_interval: Cleanup interval in seconds
        """
        super().__init__(
            cache_name=cache_name,
            max_size=max_size,
            default_ttl=default_ttl,
            enable_persistent=enable_persistent,
            cleanup_interval=cleanup_interval
        )

        # LLM-specific statistics
        self._llm_stats = {
            'total_prompt_tokens': 0,
            'total_completion_tokens': 0,
            'total_cost': 0.0,
            'avg_response_time': 0.0,
            'cache_hits_by_model': {},
            'total_requests_by_model': {}
        }

    def _generate_key(
        self,
        prompt: str,
        model: str = "default",
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> str:
        """
        Generate cache key for LLM request.

        Args:
            prompt: Input prompt
            model: Model name
            temperature: Sampling temperature
            max_tokens: Maximum tokens in response
            **kwargs: Additional parameters

        Returns:
            Cache key string
        """
        # Create a consistent key from all relevant parameters
        key_data = {
            'prompt': prompt.strip(),
            'model': model,
            'temperature': temperature,
            'max_tokens': max_tokens
        }

        # Add other parameters but exclude some that don't affect response
        exclude_params = {'stream', 'timeout', 'retry'}
        filtered_params = {k: v for k, v in kwargs.items() if k not in exclude_params}
        key_data.update(filtered_params)

        # Create deterministic hash
        key_string = json.dumps(key_data, sort_keys=True)
        return hashlib.sha256(key_string.encode('utf-8')).hexdigest()

    def get_response(
        self,
        prompt: str,
        model: str = "default",
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> Optional[str]:
        """
        Get cached LLM response.

        Args:
            prompt: Input prompt
            model: Model name
            temperature: Sampling temperature
            max_tokens: Maximum tokens in response
            **kwargs: Additional parameters

        Returns:
            Cached response or None if not found
        """
        key = self._generate_key(prompt, model, temperature, max_tokens, **kwargs)
        result = self.get(key)

        if result is not None:
            logger.debug(f"LLM cache hit for {model}: {prompt[:50]}...")

            # Update model-specific statistics
            if model not in self._llm_stats['cache_hits_by_model']:
                self._llm_stats['cache_hits_by_model'][model] = 0
            self._llm_stats['cache_hits_by_model'][model] += 1

        return result

    def set_response(
        self,
        prompt: str,
        response: str,
        model: str = "default",
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        ttl: Optional[int] = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        response_time: float = 0.0,
        cost: float = 0.0,
        **kwargs
    ) -> None:
        """
        Store LLM response in cache.

        Args:
            prompt: Input prompt
            response: LLM response
            model: Model name
            temperature: Sampling temperature
            max_tokens: Maximum tokens in response
            ttl: TTL in seconds
            prompt_tokens: Number of prompt tokens
            completion_tokens: Number of completion tokens
            response_time: Response time in seconds
            cost: Cost of the request
            **kwargs: Additional parameters
        """
        key = self._generate_key(prompt, model, temperature, max_tokens, **kwargs)

        # Create enhanced cache entry
        if ttl is None:
            ttl = self.default_ttl
        entry = LLMCacheEntry(
            key=key,
            value=response,
            ttl=ttl,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            response_time=response_time,
            cost=cost
        )

        # Update LLM-specific statistics first
        self._llm_stats['total_prompt_tokens'] += prompt_tokens
        self._llm_stats['total_completion_tokens'] += completion_tokens
        self._llm_stats['total_cost'] += cost

        # Update average response time (before updating total_requests)
        current_avg = self._llm_stats['avg_response_time']
        total_requests = self._stats['total_requests'] + 1  # Anticipate the increment
        if total_requests == 1:
            self._llm_stats['avg_response_time'] = response_time
        else:
            self._llm_stats['avg_response_time'] = (
                (current_avg * (total_requests - 1) + response_time) /
                total_requests
            )

        # Store the entry
        with self._lock:
            self._memory_cache[key] = entry
            if self.enable_persistent:
                self._save_to_db(entry)

        # Update base class statistics
        self._stats['total_requests'] += 1

        # Update model-specific statistics
        if model not in self._llm_stats['total_requests_by_model']:
            self._llm_stats['total_requests_by_model'][model] = 0
        self._llm_stats['total_requests_by_model'][model] += 1

        logger.debug(f"Cached LLM response for {model}: {prompt[:50]}...")

    def get_model_stats(self, model: str) -> Dict[str, Any]:
        """
        Get statistics for a specific model.

        Args:
            model: Model name

        Returns:
            Dictionary with model-specific statistics
        """
        total_requests = self._llm_stats['total_requests_by_model'].get(model, 0)
        cache_hits = self._llm_stats['cache_hits_by_model'].get(model, 0)
        hit_rate = (cache_hits / total_requests * 100) if total_requests > 0 else 0

        return {
            'model': model,
            'total_requests': total_requests,
            'cache_hits': cache_hits,
            'hit_rate': hit_rate,
            'estimated_savings': cache_hits * self._estimate_request_cost(model)
        }

    def _estimate_request_cost(self, model: str) -> float:
        """Estimate cost per request for a model."""
        # Simple cost estimation - could be enhanced with actual pricing
        cost_per_1k_tokens = {
            'gpt-4': 0.03,
            'gpt-4-turbo': 0.01,
            'gpt-3.5-turbo': 0.002,
            'claude-3': 0.015,
            'default': 0.01
        }
        return cost_per_1k_tokens.get(model, cost_per_1k_tokens['default'])

    def get_llm_stats(self) -> Dict[str, Any]:
        """
        Get LLM-specific statistics.

        Returns:
            Dictionary with LLM-specific statistics
        """
        return {
            **self.get_stats(),
            'llm_stats': {
                'total_prompt_tokens': self._llm_stats['total_prompt_tokens'],
                'total_completion_tokens': self._llm_stats['total_completion_tokens'],
                'total_tokens': self._llm_stats['total_prompt_tokens'] + self._llm_stats['total_completion_tokens'],
                'total_cost': self._llm_stats['total_cost'],
                'avg_response_time': self._llm_stats['avg_response_time'],
                'cache_hits_by_model': self._llm_stats['cache_hits_by_model'],
                'total_requests_by_model': self._llm_stats['total_requests_by_model'],
                'estimated_savings': sum(
                    hits * self._estimate_request_cost(model)
                    for model, hits in self._llm_stats['cache_hits_by_model'].items()
                )
            }
        }

    def _create_entry(self, key: str, value: Any, ttl: Optional[int] = None) -> CacheEntry:
        """Create LLM-specific cache entry."""
        return LLMCacheEntry(key=key, value=value, ttl=ttl)


# Convenience function to get LLM cache instance
def get_llm_cache() -> LLMCache:
    """Get the default LLM cache instance."""
    from .cache_factory import CacheFactory
    return CacheFactory.get_cache("llm", "default")