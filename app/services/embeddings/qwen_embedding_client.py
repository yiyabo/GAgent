#!/usr/bin/env python3
"""
Qwen Embedding Client

DashScope  text-embedding-v4 model. 
support OpenAI  API , model fallback. 
"""

import logging
import time
from typing import Any, Dict, List, Optional

import aiohttp
import requests

from app.services.foundation.config import GLMConfig

logger = logging.getLogger(__name__)


class QwenEmbeddingClient:
    """Qwen Embedding API ( fallback)"""

    def __init__(self, config: GLMConfig):
        self.config = config
        self.api_url = config.qwen_embedding_api_url
        self.model = config.qwen_embedding_model
        self.dimension = config.qwen_embedding_dimension
        self.api_key = config.qwen_api_key
        self.max_retries = config.max_retries
        self.retry_delay = config.retry_delay
        self.timeout = config.request_timeout

        self._local_client = None
        self._fallback_to_local = False

        logger.info(
            f"QwenEmbeddingClient initialized - "
            f"Model: {self.model}, Dimension: {self.dimension}, "
            f"URL: {self.api_url}"
        )

    def _get_local_client(self):
        """load embedding """
        if self._local_client is None:
            from app.services.embeddings.local_embedding_client import LocalEmbeddingClient
            self._local_client = LocalEmbeddingClient(self.config)
            logger.info("Local embedding client initialized for fallback")
        return self._local_client

    def get_embeddings(self, texts: List[str]) -> List[List[float]]:
        """
        get

        Args:
            texts: 

        Returns:

        """
        if not texts:
            return []

        if self._fallback_to_local:
            return self._get_local_embeddings(texts)

        for attempt in range(self.max_retries):
            try:
                return self._call_qwen_api(texts)
            except Exception as e:
                logger.warning(f"Qwen embedding API failed (attempt {attempt + 1}/{self.max_retries}): {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay * (attempt + 1))

        logger.warning("Qwen API exhausted, falling back to local embedding model")
        self._fallback_to_local = True
        return self._get_local_embeddings(texts)

    def _call_qwen_api(self, texts: List[str]) -> List[List[float]]:
        """ Qwen Embedding API"""
        if not self.api_key:
            raise ValueError("QWEN_API_KEY not configured")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": self.model,
            "input": texts,
            "dimensions": self.dimension,
        }

        response = requests.post(
            self.api_url,
            headers=headers,
            json=payload,
            timeout=self.timeout,
        )

        if response.status_code != 200:
            raise RuntimeError(f"Qwen API error {response.status_code}: {response.text}")

        result = response.json()

        # {"data": [{"embedding": [...], "index": 0}, ...], "model": "...", "usage": {...}}
        embeddings = []
        for item in sorted(result.get("data", []), key=lambda x: x.get("index", 0)):
            embeddings.append(item.get("embedding", []))

        return embeddings

    async def get_embeddings_async(self, texts: List[str]) -> List[List[float]]:
        """
        get

        Args:
            texts: 

        Returns:

        """
        if not texts:
            return []

        if self._fallback_to_local:
            return self._get_local_embeddings(texts)

        for attempt in range(self.max_retries):
            try:
                return await self._call_qwen_api_async(texts)
            except Exception as e:
                logger.warning(f"Qwen embedding API async failed (attempt {attempt + 1}/{self.max_retries}): {e}")
                if attempt < self.max_retries - 1:
                    await self._async_sleep(self.retry_delay * (attempt + 1))

        logger.warning("Qwen API async exhausted, falling back to local embedding model")
        self._fallback_to_local = True
        return self._get_local_embeddings(texts)

    async def _call_qwen_api_async(self, texts: List[str]) -> List[List[float]]:
        """ Qwen Embedding API"""
        if not self.api_key:
            raise ValueError("QWEN_API_KEY not configured")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": self.model,
            "input": texts,
            "dimensions": self.dimension,
        }

        timeout = aiohttp.ClientTimeout(total=self.timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(self.api_url, headers=headers, json=payload) as response:
                if response.status != 200:
                    text = await response.text()
                    raise RuntimeError(f"Qwen API error {response.status}: {text}")

                result = await response.json()

        embeddings = []
        for item in sorted(result.get("data", []), key=lambda x: x.get("index", 0)):
            embeddings.append(item.get("embedding", []))

        return embeddings

    async def _async_sleep(self, seconds: float):
        """waiting"""
        import asyncio
        await asyncio.sleep(seconds)

    def _get_local_embeddings(self, texts: List[str]) -> List[List[float]]:
        """modelget"""
        local_client = self._get_local_client()
        return local_client.get_embeddings(texts)

    def get_single_embedding(self, text: str) -> List[float]:
        """get"""
        embeddings = self.get_embeddings([text])
        return embeddings[0] if embeddings else []

    def test_connection(self) -> bool:
        """ API connection"""
        try:
            result = self.get_embeddings(["test"])
            return len(result) > 0 and len(result[0]) > 0
        except Exception as e:
            logger.warning(f"Qwen embedding connection test failed: {e}")
            return False

    def get_client_info(self) -> Dict[str, Any]:
        """get"""
        return {
            "type": "QwenEmbeddingClient",
            "model": self.model,
            "dimension": self.dimension,
            "api_url": self.api_url,
            "fallback_to_local": self._fallback_to_local,
            "has_api_key": bool(self.api_key),
        }

    def reset_fallback(self):
        """ fallback status,  API"""
        self._fallback_to_local = False
        logger.info("Qwen embedding client fallback reset, will retry API")
