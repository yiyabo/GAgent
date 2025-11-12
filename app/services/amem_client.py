#!/usr/bin/env python
"""
A-mem (Agentic Memory) Client Service

提供与A-mem记忆系统的集成接口，用于：
- 查询历史执行经验
- 保存新的执行结果
- 支持Claude Code执行的经验积累
"""

import logging
from typing import List, Dict, Optional, Any
import httpx
from datetime import datetime

logger = logging.getLogger(__name__)


class AMemClient:
    """A-mem记忆系统客户端
    
    通过HTTP API与独立运行的A-mem服务通信
    """
    
    def __init__(
        self,
        base_url: str = "http://localhost:8001",
        timeout: float = 10.0,
        enabled: bool = True
    ):
        """初始化A-mem客户端
        
        Args:
            base_url: A-mem API服务地址
            timeout: 请求超时时间（秒）
            enabled: 是否启用A-mem功能
        """
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self.enabled = enabled
        self._client: Optional[httpx.AsyncClient] = None
        
        if self.enabled:
            logger.info(f"A-mem client initialized: {self.base_url}")
        else:
            logger.info("A-mem client disabled")
    
    async def _get_client(self) -> httpx.AsyncClient:
        """获取或创建HTTP客户端"""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client
    
    async def close(self):
        """关闭HTTP客户端"""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
    
    async def health_check(self) -> bool:
        """检查A-mem服务是否可用
        
        Returns:
            bool: 服务是否健康
        """
        if not self.enabled:
            return False
        
        try:
            client = await self._get_client()
            response = await client.get(f"{self.base_url}/health")
            return response.status_code == 200
        except Exception as e:
            logger.warning(f"A-mem health check failed: {e}")
            return False
    
    async def query_experiences(
        self,
        query: str,
        top_k: int = 3,
        context_filter: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """查询相似的执行经验
        
        Args:
            query: 查询文本（任务描述）
            top_k: 返回结果数量
            context_filter: 可选的上下文过滤器
        
        Returns:
            List[Dict]: 相关记忆列表，每个包含：
                - id: 记忆ID
                - content: 记忆内容
                - context: 上下文
                - keywords: 关键词列表
                - tags: 标签列表
                - timestamp: 时间戳
                - score: 相似度分数
        """
        if not self.enabled:
            return []
        
        try:
            client = await self._get_client()
            response = await client.post(
                f"{self.base_url}/query_memory",
                json={
                    "query": query,
                    "top_k": min(top_k, 10)  # 限制最大返回数量
                }
            )
            
            if response.status_code == 200:
                data = response.json()
                results = data.get("results", [])
                logger.info(f"A-mem query returned {len(results)} experiences for: {query[:50]}...")
                return results
            else:
                logger.warning(f"A-mem query failed with status {response.status_code}")
                return []
                
        except Exception as e:
            logger.warning(f"Failed to query A-mem: {e}")
            return []
    
    async def save_execution(
        self,
        task: str,
        result: Dict[str, Any],
        session_id: Optional[str] = None,
        plan_id: Optional[int] = None,
        **metadata
    ) -> Optional[str]:
        """保存执行结果到A-mem
        
        Args:
            task: 任务描述
            result: 执行结果字典
            session_id: 会话ID
            plan_id: 计划ID
            **metadata: 额外的元数据
        
        Returns:
            Optional[str]: 记忆ID，失败返回None
        """
        if not self.enabled:
            return None
        
        try:
            # 格式化执行记忆内容
            content = self._format_execution_memory(task, result, session_id, plan_id, metadata)
            
            # 提取标签
            tags = ["claude_code", "execution"]
            if result.get("success"):
                tags.append("success")
            else:
                tags.append("failure")
            
            # 添加自定义标签
            if "tags" in metadata:
                tags.extend(metadata["tags"])
            
            # 生成上下文
            context = metadata.get("context", "代码执行经验")
            
            client = await self._get_client()
            response = await client.post(
                f"{self.base_url}/add_memory",
                json={
                    "content": content,
                    "tags": tags,
                    "context": context,
                    "timestamp": datetime.now().strftime("%Y%m%d%H%M")
                }
            )
            
            if response.status_code == 200:
                data = response.json()
                memory_id = data.get("memory_id")
                logger.info(f"Saved execution to A-mem: {memory_id}")
                return memory_id
            else:
                logger.warning(f"Failed to save to A-mem: status {response.status_code}")
                return None
                
        except Exception as e:
            logger.warning(f"Failed to save execution to A-mem: {e}")
            return None
    
    def _format_execution_memory(
        self,
        task: str,
        result: Dict[str, Any],
        session_id: Optional[str],
        plan_id: Optional[int],
        metadata: Dict[str, Any]
    ) -> str:
        """格式化执行记忆内容
        
        创建结构化的记忆内容，便于后续检索和理解
        """
        lines = [
            "# Claude Code执行记录",
            "",
            "## 任务描述",
            task,
            "",
            "## 执行结果",
            f"状态: {'✅ 成功' if result.get('success') else '❌ 失败'}",
        ]
        
        # 添加工作目录信息
        if "working_directory" in result:
            lines.append(f"工作目录: {result['working_directory']}")
        
        if "task_directory" in result:
            lines.append(f"任务目录: {result['task_directory']}")
        
        # 添加输出信息
        if result.get("stdout"):
            stdout = result["stdout"]
            if len(stdout) > 500:
                stdout = stdout[:500] + "...(truncated)"
            lines.extend([
                "",
                "## 标准输出",
                stdout
            ])
        
        # 添加错误信息
        if result.get("error"):
            lines.extend([
                "",
                "## 错误信息",
                str(result["error"])
            ])
        
        if result.get("stderr"):
            stderr = result["stderr"]
            if len(stderr) > 300:
                stderr = stderr[:300] + "...(truncated)"
            lines.extend([
                "",
                "## 错误输出",
                stderr
            ])
        
        # 添加元数据
        if session_id:
            lines.append(f"\n会话ID: {session_id}")
        if plan_id:
            lines.append(f"计划ID: {plan_id}")
        
        # 添加关键发现
        if "key_findings" in metadata:
            lines.extend([
                "",
                "## 关键发现",
                metadata["key_findings"]
            ])
        
        return "\n".join(lines)
    
    def format_experiences_for_llm(self, experiences: List[Dict[str, Any]]) -> str:
        """格式化历史经验供LLM参考
        
        Args:
            experiences: 从A-mem查询到的经验列表
        
        Returns:
            str: 格式化的经验文本
        """
        if not experiences:
            return ""
        
        lines = ["以下是相关的历史执行经验，供参考：", ""]
        
        for i, exp in enumerate(experiences, 1):
            lines.append(f"### 经验 {i} (相似度: {exp.get('score', 0):.2f})")
            lines.append(exp.get("content", ""))
            
            # 添加关键词和标签
            keywords = exp.get("keywords", [])
            tags = exp.get("tags", [])
            if keywords:
                lines.append(f"\n关键词: {', '.join(keywords)}")
            if tags:
                lines.append(f"标签: {', '.join(tags)}")
            
            lines.append("\n---\n")
        
        return "\n".join(lines)


# 全局A-mem客户端实例
_amem_client: Optional[AMemClient] = None


def get_amem_client() -> AMemClient:
    """获取全局A-mem客户端实例
    
    Returns:
        AMemClient: A-mem客户端实例
    """
    global _amem_client
    
    if _amem_client is None:
        # 从配置读取设置
        from app.services.foundation.settings import get_settings
        settings = get_settings()
        
        # 检查是否启用A-mem
        amem_enabled = getattr(settings, "amem_enabled", False)
        amem_url = getattr(settings, "amem_url", "http://localhost:8001")
        
        _amem_client = AMemClient(
            base_url=amem_url,
            enabled=amem_enabled
        )
    
    return _amem_client


async def close_amem_client():
    """关闭全局A-mem客户端"""
    global _amem_client
    if _amem_client is not None:
        await _amem_client.close()
        _amem_client = None
