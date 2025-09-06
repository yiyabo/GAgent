#!/usr/bin/env python3
"""
GLM API Client Module.

Responsible for communication with GLM API, including HTTP requests, 
retry logic, connection pool management, etc.
Separated from GLMEmbeddingsService following Single Responsibility Principle.
"""

import json
import logging
import time
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


class GLMApiClient:
    """GLM API client class responsible for API calls"""

    def __init__(self, config):
        """
        Initialize API client

        Args:
            config: Configuration object containing API-related settings
        """
        self.api_key = config.api_key
        self.api_url = config.api_url
        self.model = config.embedding_model
        self.max_retries = config.max_retries
        self.retry_delay = config.retry_delay
        self.request_timeout = config.request_timeout
        self.mock_mode = config.mock_mode

        # Connection pool reuse
        self.session = requests.Session()

        logger.info(f"GLM API Client initialized - Model: {self.model}, Mock: {self.mock_mode}")

    def get_embeddings_from_api(self, texts: List[str]) -> List[List[float]]:
        """
        Get embeddings from API

        Args:
            texts: List of texts

        Returns:
            List of embeddings

        Raises:
            Exception: Raised when API call fails
        """
        if self.mock_mode:
            return self._create_mock_embeddings(texts)

        for attempt in range(self.max_retries):
            try:
                embeddings = self._make_api_request(texts)
                logger.debug(f"API request successful, got {len(embeddings)} embeddings")
                return embeddings
            except Exception as e:
                logger.warning(f"API request attempt {attempt + 1} failed: {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay * (2**attempt))
                else:
                    logger.error(f"API request failed after {self.max_retries} attempts")
                    raise e

    def _make_api_request(self, texts: List[str]) -> List[List[float]]:
        """Execute actual API request"""
        headers = self._build_request_headers()
        payload = self._build_request_payload(texts)

        response = self.session.post(self.api_url, headers=headers, json=payload, timeout=self.request_timeout)

        if response.status_code != 200:
            raise Exception(f"API request failed with status {response.status_code}: {response.text}")

        return self._parse_api_response(response)

    def _build_request_headers(self) -> Dict[str, str]:
        """Build request headers"""
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _build_request_payload(self, texts: List[str]) -> Dict[str, Any]:
        """Build request payload"""
        return {"model": self.model, "input": texts}

    def _parse_api_response(self, response) -> List[List[float]]:
        """Parse API response"""
        try:
            data = response.json()
            if "data" not in data:
                raise Exception(f"Invalid API response format: {data}")

            embeddings = []
            for item in data["data"]:
                if "embedding" in item:
                    embeddings.append(item["embedding"])
                else:
                    raise Exception(f"Missing embedding in response item: {item}")

            return embeddings
        except json.JSONDecodeError as e:
            raise Exception(f"Failed to parse API response as JSON: {e}")

    def _create_mock_embeddings(self, texts: List[str]) -> List[List[float]]:
        """Create mock embeddings for testing"""
        import numpy as np

        embeddings = []
        for i, text in enumerate(texts):
            # Generate deterministic mock embedding based on text content
            np.random.seed(hash(text) % (2**32))
            embedding = np.random.normal(0, 1, 1024).tolist()
            embeddings.append(embedding)

        logger.debug(f"Generated {len(embeddings)} mock embeddings")
        return embeddings

    def test_connection(self) -> bool:
        """Test API connection"""
        if self.mock_mode:
            logger.info("Mock mode - connection test skipped")
            return True

        try:
            test_embeddings = self.get_embeddings_from_api(["test"])
            return len(test_embeddings) > 0
        except Exception as e:
            logger.error(f"API connection test failed: {e}")
            return False

    def get_client_info(self) -> Dict[str, Any]:
        """Get client information"""
        return {
            "model": self.model,
            "api_url": self.api_url,
            "mock_mode": self.mock_mode,
            "max_retries": self.max_retries,
            "request_timeout": self.request_timeout,
        }

    def get_embeddings(self, texts: List[str]) -> List[List[float]]:
        """
        Alias for get_embeddings_from_api to maintain compatibility

        Args:
            texts: List of texts to get embeddings for

        Returns:
            List of embeddings
        """
        return self.get_embeddings_from_api(texts)
