"""
统一的API客户端工具类

这个模块提供了一个统一的API客户端，用于CLI命令与后端API的交互。
目标是让所有CLI命令都通过这个客户端调用API，而不是直接调用服务层。
"""

import os
import requests
import logging
from typing import Dict, Any, Optional
from requests.exceptions import RequestException, Timeout
from requests.exceptions import ConnectionError as RequestsConnectionError


logger = logging.getLogger(__name__)


class APIClient:
    """统一的API客户端"""
    
    def __init__(self, base_url: Optional[str] = None):
        self.base_url = base_url or os.getenv("BASE_URL", "http://127.0.0.1:9000")
        self.timeout = 300  # 默认5分钟超时
    
    def get(self, endpoint: str, params: Optional[Dict] = None, **kwargs) -> Dict[str, Any]:
        """GET请求"""
        return self._request("GET", endpoint, params=params, **kwargs)
    
    def post(self, endpoint: str, json_data: Optional[Dict] = None, **kwargs) -> Dict[str, Any]:
        """POST请求"""
        return self._request("POST", endpoint, json=json_data, **kwargs)
    
    def put(self, endpoint: str, json_data: Optional[Dict] = None, **kwargs) -> Dict[str, Any]:
        """PUT请求"""
        return self._request("PUT", endpoint, json=json_data, **kwargs)
    
    def delete(self, endpoint: str, **kwargs) -> Dict[str, Any]:
        """DELETE请求"""
        return self._request("DELETE", endpoint, **kwargs)
    
    def _request(self, method: str, endpoint: str, **kwargs) -> Dict[str, Any]:
        """统一的请求处理"""
        url = f"{self.base_url.rstrip('/')}/{endpoint.lstrip('/')}"
        # 设置默认超时，但不覆盖已存在的超时设置
        if 'timeout' not in kwargs:
            kwargs['timeout'] = self.timeout
        
        logger.debug("API Request: %s %s", method, url)
        if 'json' in kwargs:
            logger.debug("Request payload: %s", kwargs['json'])
        
        try:
            response = requests.request(method, url, **kwargs)
            logger.debug("Response status: %s", response.status_code)
            
            response.raise_for_status()
            
            # 尝试解析JSON
            try:
                result = response.json()
                logger.debug("Response data: %s", result)
                return result
            except ValueError:
                return {"status": "success", "data": response.text}
                
        except Timeout as exc:
            error_msg = f"Request timeout after {self.timeout}s for {method} {url}"
            logger.error(error_msg)
            raise APIClientError(error_msg) from exc
        except RequestsConnectionError as exc:
            error_msg = f"Cannot connect to API server at {self.base_url}. Is the server running?"
            logger.error(error_msg)
            raise APIClientError(error_msg) from exc
        except RequestException as e:
            if hasattr(e, 'response') and e.response is not None:
                try:
                    error_data = e.response.json()
                    error_msg = f"API Error ({e.response.status_code}): {error_data}"
                    logger.error(error_msg)
                    raise APIClientError(error_msg) from e
                except ValueError as exc:
                    error_msg = f"API Error ({e.response.status_code}): {e.response.text}"
                    logger.error(error_msg)
                    raise APIClientError(error_msg) from exc
            else:
                error_msg = f"Network Error: {e}"
                logger.error(error_msg)
                raise APIClientError(error_msg) from e


class APIClientError(Exception):
    """API客户端异常"""
    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# 单例模式的默认客户端
_default_client = None

def get_api_client() -> APIClient:
    """获取默认API客户端"""
    global _default_client
    if _default_client is None:
        _default_client = APIClient()
    return _default_client

def reset_api_client():
    """重置API客户端（主要用于测试）"""
    global _default_client
    _default_client = None
