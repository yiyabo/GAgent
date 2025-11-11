"""
Chat Memory Middleware - 聊天记忆中间件

使用LLM智能分析和保存重要的聊天消息为记忆
"""

import json
import logging
from typing import Optional

from ...llm import get_default_client
from ...models_memory import ImportanceLevel, MemoryType
from .memory_hooks import get_memory_hooks

logger = logging.getLogger(__name__)


class ChatMemoryMiddleware:
    """聊天记忆中间件 - 使用LLM智能判断和保存重要对话"""

    def __init__(self):
        self.hooks = get_memory_hooks()
        self.llm_client = get_default_client()
        self.enabled = True

    async def process_message(
        self,
        content: str,
        role: str = "user",
        session_id: Optional[str] = None,
        force_save: bool = False,
    ) -> Optional[str]:
        """
        处理聊天消息，使用LLM判断是否保存为记忆
        
        Args:
            content: 消息内容
            role: 角色 (user/assistant)
            session_id: 会话ID
            force_save: 强制保存
            
        Returns:
            记忆ID，如果未保存则返回None
        """
        if not self.enabled and not force_save:
            return None
        
        # 使用LLM判断是否需要保存
        should_save, importance, memory_type = await self._should_save_message(
            content, role, force_save
        )
        
        if not should_save:
            return None
        
        # 保存为记忆（使用LLM判断的类型）
        try:
            from ...models_memory import SaveMemoryRequest
            from .memory_service import get_memory_service
            
            memory_service = get_memory_service()
            
            # 添加角色标识
            memory_content = f"[{role}] {content}"
            
            tags = ["对话", role]
            if session_id:
                tags.append(f"session:{session_id}")
            
            request = SaveMemoryRequest(
                content=memory_content,
                memory_type=memory_type,
                importance=importance,
                tags=tags,
            )
            
            response = await memory_service.save_memory(request)
            memory_id = response.memory_id
            
            if memory_id:
                logger.info(f"💾 聊天消息已保存为记忆 ({memory_type.value}/{importance.value}): {memory_id[:8]}...")
            
            return memory_id
            
        except Exception as e:
            logger.error(f"保存聊天记忆失败: {e}")
            return None

    async def _should_save_message(
        self,
        content: str,
        role: str,
        force_save: bool = False,
    ) -> tuple[bool, ImportanceLevel, Optional[MemoryType]]:
        """
        使用LLM判断消息是否应该保存以及重要性级别
        
        Returns:
            (是否保存, 重要性级别, 记忆类型)
        """
        if force_save:
            return True, ImportanceLevel.HIGH, MemoryType.CONVERSATION
        
        # 太短的消息直接跳过
        if len(content) < 10:
            return False, ImportanceLevel.LOW, None
        
        # 使用LLM判断，最多重试3次
        max_retries = 3
        last_error = None
        
        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    logger.info(f"🔄 LLM判断重试 {attempt + 1}/{max_retries}")
                
                prompt = f"""You are an intelligent memory system analyzer. Analyze the following conversation message and determine if it's worth saving as long-term memory.

Role: {role}
Message Content: {content}

Analyze from the following dimensions:
1. Does it contain important knowledge, experience, or insights?
2. Is it a critical question, error, or solution?
3. Does it have reference value for future conversations or tasks?
4. Does it contain configuration, settings, or important decisions?

Return your judgment in JSON format:
{{
    "should_save": true/false,  // Whether to save
    "importance": "low/medium/high/critical",  // Importance level
    "memory_type": "knowledge/experience/conversation/context",  // Memory type
    "reason": "Brief explanation"
}}

Only return JSON, no other content."""

                # 注意：llm_client.chat() 是同步方法，需要用 run_in_executor
                import asyncio
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(
                    None,
                    lambda: self.llm_client.chat(prompt, temperature=0.3)
                )
                
                # 解析LLM响应
                response_text = response.strip() if isinstance(response, str) else response.get("content", "").strip()
                
                # 尝试提取JSON
                if "```json" in response_text:
                    response_text = response_text.split("```json")[1].split("```")[0].strip()
                elif "```" in response_text:
                    response_text = response_text.split("```")[1].split("```")[0].strip()
                
                result = json.loads(response_text)
                
                should_save = result.get("should_save", False)
                importance_str = result.get("importance", "low").lower()
                memory_type_str = result.get("memory_type", "conversation").lower()
                reason = result.get("reason", "")
                
                # 转换为枚举
                importance_map = {
                    "low": ImportanceLevel.LOW,
                    "medium": ImportanceLevel.MEDIUM,
                    "high": ImportanceLevel.HIGH,
                    "critical": ImportanceLevel.CRITICAL,
                }
                importance = importance_map.get(importance_str, ImportanceLevel.MEDIUM)
                
                memory_type_map = {
                    "knowledge": MemoryType.KNOWLEDGE,
                    "experience": MemoryType.EXPERIENCE,
                    "conversation": MemoryType.CONVERSATION,
                    "context": MemoryType.CONTEXT,
                }
                memory_type = memory_type_map.get(memory_type_str, MemoryType.CONVERSATION)
                
                if should_save:
                    logger.info(f"🤖 LLM判断应保存: {importance_str} - {reason}")
                else:
                    logger.debug(f"🤖 LLM判断不保存: {reason}")
                
                # 成功，直接返回
                return should_save, importance, memory_type
                
            except Exception as e:
                last_error = e
                logger.warning(f"⚠️  LLM判断失败 (尝试 {attempt + 1}/{max_retries}): {e}")
                # 如果还有重试机会，继续下一次
                if attempt < max_retries - 1:
                    continue
                # 否则跳出循环
                break
        
        # 所有重试都失败
        logger.error(f"❌ LLM判断失败，已重试{max_retries}次: {last_error}")
        return False, ImportanceLevel.LOW, None

    async def process_assistant_response(
        self,
        content: str,
        session_id: Optional[str] = None,
    ) -> Optional[str]:
        """
        处理助手响应，使用LLM判断
        
        Args:
            content: 响应内容
            session_id: 会话ID
            
        Returns:
            记忆ID
        """
        return await self.process_message(
            content=content,
            role="assistant",
            session_id=session_id,
            force_save=False
        )

    def enable(self):
        """启用中间件"""
        self.enabled = True
        logger.info("✅ Chat memory middleware enabled")

    def disable(self):
        """禁用中间件"""
        self.enabled = False
        logger.info("⏸️  Chat memory middleware disabled")


# 全局单例
_chat_memory_middleware: Optional[ChatMemoryMiddleware] = None


def get_chat_memory_middleware() -> ChatMemoryMiddleware:
    """获取聊天记忆中间件实例"""
    global _chat_memory_middleware
    if _chat_memory_middleware is None:
        _chat_memory_middleware = ChatMemoryMiddleware()
    return _chat_memory_middleware
