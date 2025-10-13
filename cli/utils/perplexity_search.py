#!/usr/bin/env python3
"""
Perplexity搜索工具

将Perplexity从独立的LLM引擎改为专用的网络搜索工具
"""

import logging
import requests
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class PerplexitySearchTool:
    """Perplexity网络搜索工具"""
    
    def __init__(self):
        self.api_key = None
        self.api_url = None
        self.model = None
        self._load_config()
    
    def _load_config(self):
        """加载Perplexity配置"""
        try:
            from app.services.foundation.settings import get_settings
            settings = get_settings()
            
            self.api_key = settings.perplexity_api_key
            self.api_url = settings.perplexity_api_url
            self.model = settings.perplexity_model or "sonar-pro"
            
        except Exception as e:
            logger.error(f"加载Perplexity配置失败: {e}")
    
    def search(self, query: str, max_results: int = 5) -> Dict[str, Any]:
        """
        使用Perplexity进行网络搜索
        
        Args:
            query: 搜索查询
            max_results: 最大结果数量（用于兼容，实际由Perplexity控制）
            
        Returns:
            包含搜索结果的字典
        """
        if not self.api_key:
            return {
                "status": "error",
                "message": "Perplexity API密钥未配置",
                "results": []
            }
        
        try:
            # 构建搜索请求
            payload = {
                "model": self.model,
                "messages": [
                    {
                        "role": "system", 
                        "content": """你是一个专业的搜索助手。请为用户的查询提供准确、最新的信息。

搜索要求：
1. 提供最新、最准确的信息
2. 如果是实时信息（如新闻、价格等），请特别注明时间
3. 结构化地组织信息
4. 提供信息来源（如果可能）
5. 如果查询涉及多个方面，请分别回答

请直接回答用户的问题，不需要说明你正在搜索。"""
                    },
                    {
                        "role": "user",
                        "content": f"请搜索并回答：{query}"
                    }
                ],
                "temperature": 0.1,
                "max_tokens": 1000
            }
            
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            
            logger.info(f"开始Perplexity搜索: {query}")
            
            response = requests.post(
                self.api_url, 
                json=payload, 
                headers=headers, 
                timeout=30
            )
            
            response.raise_for_status()
            result = response.json()
            
            if "choices" in result and len(result["choices"]) > 0:
                search_content = result["choices"][0]["message"]["content"]
                
                return {
                    "status": "success",
                    "query": query,
                    "content": search_content,
                    "source": "Perplexity API",
                    "results": [{
                        "title": f"搜索结果：{query}",
                        "content": search_content,
                        "url": "via Perplexity API",
                        "score": 1.0
                    }]
                }
            else:
                return {
                    "status": "error", 
                    "message": "Perplexity API返回格式异常",
                    "results": []
                }
                
        except requests.exceptions.Timeout:
            logger.error("Perplexity搜索超时")
            return {
                "status": "error",
                "message": "搜索请求超时，请稍后重试",
                "results": []
            }
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Perplexity搜索请求失败: {e}")
            return {
                "status": "error",
                "message": f"搜索请求失败: {str(e)}",
                "results": []
            }
            
        except Exception as e:
            logger.error(f"Perplexity搜索异常: {e}")
            return {
                "status": "error",
                "message": f"搜索过程中发生错误: {str(e)}",
                "results": []
            }


# 全局实例
_perplexity_search_instance = None

def get_perplexity_search() -> PerplexitySearchTool:
    """获取Perplexity搜索工具实例"""
    global _perplexity_search_instance
    if _perplexity_search_instance is None:
        _perplexity_search_instance = PerplexitySearchTool()
    return _perplexity_search_instance


def search_with_perplexity(query: str, max_results: int = 5) -> Dict[str, Any]:
    """便捷的Perplexity搜索函数"""
    search_tool = get_perplexity_search()
    return search_tool.search(query, max_results)
