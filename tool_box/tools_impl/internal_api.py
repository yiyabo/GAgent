"""
Internal API Tool Implementation

This module provides internal API call functionality for AI agents.
"""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


async def internal_api_handler(
    endpoint: str, 
    method: str = "POST",
    data: Optional[Dict[str, Any]] = None,
    timeout: float = 60.0,
    base_url: Optional[str] = None
) -> Dict[str, Any]:
    """
    Internal API call tool handler

    Args:
        endpoint: API endpoint path (e.g., "/agent/create-workflow")
        method: HTTP method ("GET", "POST", "PUT", "DELETE")
        data: Request data for POST/PUT requests
        timeout: Request timeout in seconds
        base_url: Base URL for the API

    Returns:
        Dict containing API response
    """
    try:
        if base_url is None:
            try:
                from app.services.foundation.settings import get_settings
                base_url = get_settings().base_url
            except Exception:
                base_url = "http://127.0.0.1:9000"

        full_url = f"{base_url}{endpoint}"
        logger.info(f"🔗 Internal API call: {method} {full_url}")
        
        async with httpx.AsyncClient() as client:
            if method.upper() == "POST":
                response = await client.post(
                    full_url,
                    json=data,
                    timeout=timeout
                )
            elif method.upper() == "GET":
                response = await client.get(
                    full_url,
                    params=data,
                    timeout=timeout
                )
            elif method.upper() == "PUT":
                response = await client.put(
                    full_url,
                    json=data,
                    timeout=timeout
                )
            elif method.upper() == "DELETE":
                response = await client.delete(
                    full_url,
                    timeout=timeout
                )
            else:
                return {
                    "success": False,
                    "error": f"Unsupported HTTP method: {method}",
                    "status_code": 400
                }

            # Parse response
            try:
                response_data = response.json() if response.content else {}
            except json.JSONDecodeError:
                response_data = {"text": response.text}

            result = {
                "success": response.status_code < 400,
                "status_code": response.status_code,
                "data": response_data,
                "endpoint": endpoint,
                "method": method
            }
            
            if not result["success"]:
                result["error"] = f"API call failed with status {response.status_code}"
                logger.error(f"❌ Internal API call failed: {method} {full_url} - {response.status_code}")
            else:
                logger.info(f"✅ Internal API call successful: {method} {full_url}")
            
            return result

    except httpx.TimeoutException:
        return {
            "success": False,
            "error": f"Request timeout after {timeout} seconds",
            "status_code": 408,
            "endpoint": endpoint,
            "method": method
        }
    except Exception as e:
        logger.error(f"Internal API call failed: {e}")
        return {
            "success": False,
            "error": str(e),
            "status_code": 500,
            "endpoint": endpoint,
            "method": method
        }


# Tool definition for internal API calls
internal_api_tool = {
    "name": "internal_api",
    "description": "执行内部API调用，用于访问系统内部服务",
    "category": "system_integration",
    "parameters_schema": {
        "type": "object",
        "properties": {
            "endpoint": {
                "type": "string",
                "description": "API端点路径 (例如: /agent/create-workflow)"
            },
            "method": {
                "type": "string", 
                "description": "HTTP方法",
                "enum": ["GET", "POST", "PUT", "DELETE"],
                "default": "POST"
            },
            "data": {
                "type": "object",
                "description": "请求数据 (用于POST/PUT请求)"
            },
            "timeout": {
                "type": "number",
                "description": "请求超时时间（秒）",
                "default": 60.0
            },
            "base_url": {
                "type": "string",
                "description": "API基础URL"
            }
        },
        "required": ["endpoint"]
    },
    "handler": internal_api_handler,
    "tags": ["api", "internal", "system"],
    "examples": ["调用工作流程创建API", "访问内部服务接口", "系统间通信"],
}
