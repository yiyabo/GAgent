#!/usr/bin/env python3
"""
GLM API客户端模块

专门负责与GLM API的通信，包括HTTP请求、重试逻辑、连接池管理等。
从GLMEmbeddingsService中拆分出来，遵循单一职责原则。
"""

import json
import time
import logging
import requests
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


class GLMApiClient:
    """GLM API客户端类，专门负责API调用"""
    
    def __init__(self, config):
        """
        初始化API客户端
        
        Args:
            config: 配置对象，包含API相关设置
        """
        self.api_key = config.api_key
        self.api_url = config.api_url
        self.model = config.embedding_model
        self.max_retries = config.max_retries
        self.retry_delay = config.retry_delay
        self.request_timeout = config.request_timeout
        self.mock_mode = config.mock_mode
        
        # 连接池复用
        self.session = requests.Session()
        
        logger.info(f"GLM API Client initialized - Model: {self.model}, Mock: {self.mock_mode}")
    
    def get_embeddings_from_api(self, texts: List[str]) -> List[List[float]]:
        """
        从API获取embeddings
        
        Args:
            texts: 文本列表
            
        Returns:
            embeddings列表
            
        Raises:
            Exception: API调用失败时抛出异常
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
                    time.sleep(self.retry_delay * (2 ** attempt))
                else:
                    logger.error(f"API request failed after {self.max_retries} attempts")
                    raise e
    
    def _make_api_request(self, texts: List[str]) -> List[List[float]]:
        """执行实际的API请求"""
        headers = self._build_request_headers()
        payload = self._build_request_payload(texts)
        
        response = self.session.post(
            self.api_url,
            headers=headers,
            json=payload,
            timeout=self.request_timeout
        )
        
        if response.status_code != 200:
            raise Exception(f"API request failed with status {response.status_code}: {response.text}")
        
        return self._parse_api_response(response)
    
    def _build_request_headers(self) -> Dict[str, str]:
        """构建请求头"""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
    
    def _build_request_payload(self, texts: List[str]) -> Dict[str, Any]:
        """构建请求体"""
        return {
            "model": self.model,
            "input": texts
        }
    
    def _parse_api_response(self, response) -> List[List[float]]:
        """解析API响应"""
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
        """创建模拟embeddings用于测试"""
        import numpy as np
        
        embeddings = []
        for i, text in enumerate(texts):
            # 基于文本内容生成确定性的mock embedding
            np.random.seed(hash(text) % (2**32))
            embedding = np.random.normal(0, 1, 1024).tolist()
            embeddings.append(embedding)
        
        logger.debug(f"Generated {len(embeddings)} mock embeddings")
        return embeddings
    
    def test_connection(self) -> bool:
        """测试API连接"""
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
        """获取客户端信息"""
        return {
            "model": self.model,
            "api_url": self.api_url,
            "mock_mode": self.mock_mode,
            "max_retries": self.max_retries,
            "request_timeout": self.request_timeout
        }
