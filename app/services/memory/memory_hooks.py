"""
Memory Hooks Service - 自动记忆保存钩子

在关键事件发生时自动保存记忆，无需手动干预
"""

import logging
from typing import Any, Dict, Optional
from datetime import datetime

from ...models_memory import (
    ImportanceLevel,
    MemoryType,
    SaveMemoryRequest,
)
from .memory_service import get_memory_service

logger = logging.getLogger(__name__)


class MemoryHooks:
    """记忆钩子服务 - 自动捕获和保存重要事件"""

    def __init__(self):
        self.memory_service = get_memory_service()
        self.enabled = True
        self.stats = {
            "total_saved": 0,
            "by_type": {},
            "last_save_time": None,
        }

    async def on_task_complete(
        self,
        task_id: int,
        task_name: str,
        task_content: str,
        task_result: Optional[str] = None,
        success: bool = True,
    ) -> Optional[str]:
        """
        任务完成时的钩子
        
        Args:
            task_id: 任务ID
            task_name: 任务名称
            task_content: 任务内容
            task_result: 任务结果
            success: 是否成功
            
        Returns:
            记忆ID，如果保存失败则返回None
        """
        if not self.enabled:
            return None

        try:
            # 构建记忆内容
            content_parts = [f"任务: {task_name}"]
            
            if task_content:
                content_parts.append(f"描述: {task_content}")
            
            if task_result:
                status = "成功完成" if success else "执行失败"
                content_parts.append(f"结果: {status}")
                content_parts.append(f"详情: {task_result}")
            
            content = "\n".join(content_parts)
            
            # 确定重要性
            importance = ImportanceLevel.HIGH if success else ImportanceLevel.CRITICAL
            
            # 自动生成标签
            tags = ["任务执行"]
            if success:
                tags.append("成功")
            else:
                tags.append("失败")
            
            # 保存记忆
            request = SaveMemoryRequest(
                content=content,
                memory_type=MemoryType.TASK_OUTPUT,
                importance=importance,
                tags=tags,
                related_task_id=task_id,
            )
            
            response = await self.memory_service.save_memory(request)
            
            # 更新统计
            self._update_stats(MemoryType.TASK_OUTPUT)
            
            logger.info(f"✅ 任务 {task_id} 已自动保存为记忆 {response.memory_id}")
            return response.memory_id
            
        except Exception as e:
            logger.error(f"❌ 保存任务记忆失败: {e}")
            return None

    async def on_conversation_important(
        self,
        content: str,
        role: str = "user",
        session_id: Optional[str] = None,
        importance: ImportanceLevel = ImportanceLevel.MEDIUM,
    ) -> Optional[str]:
        """
        重要对话时的钩子
        
        Args:
            content: 对话内容
            role: 角色 (user/assistant)
            session_id: 会话ID
            importance: 重要性级别
            
        Returns:
            记忆ID
        """
        if not self.enabled:
            return None

        try:
            # 添加角色标识
            memory_content = f"[{role}] {content}"
            
            tags = ["对话", role]
            if session_id:
                tags.append(f"session:{session_id}")
            
            request = SaveMemoryRequest(
                content=memory_content,
                memory_type=MemoryType.CONVERSATION,
                importance=importance,
                tags=tags,
            )
            
            response = await self.memory_service.save_memory(request)
            self._update_stats(MemoryType.CONVERSATION)
            
            logger.info(f"✅ 对话已自动保存为记忆 {response.memory_id}")
            return response.memory_id
            
        except Exception as e:
            logger.error(f"❌ 保存对话记忆失败: {e}")
            return None

    async def on_error_occurred(
        self,
        error_message: str,
        error_type: str,
        context: Optional[Dict[str, Any]] = None,
        task_id: Optional[int] = None,
    ) -> Optional[str]:
        """
        错误发生时的钩子
        
        Args:
            error_message: 错误消息
            error_type: 错误类型
            context: 错误上下文
            task_id: 相关任务ID
            
        Returns:
            记忆ID
        """
        if not self.enabled:
            return None

        try:
            content_parts = [
                f"错误类型: {error_type}",
                f"错误信息: {error_message}",
            ]
            
            if context:
                content_parts.append(f"上下文: {context}")
            
            content = "\n".join(content_parts)
            
            request = SaveMemoryRequest(
                content=content,
                memory_type=MemoryType.EXPERIENCE,
                importance=ImportanceLevel.HIGH,
                tags=["错误", error_type],
                related_task_id=task_id,
            )
            
            response = await self.memory_service.save_memory(request)
            self._update_stats(MemoryType.EXPERIENCE)
            
            logger.info(f"✅ 错误已自动保存为记忆 {response.memory_id}")
            return response.memory_id
            
        except Exception as e:
            logger.error(f"❌ 保存错误记忆失败: {e}")
            return None

    async def on_knowledge_learned(
        self,
        knowledge: str,
        source: str,
        tags: Optional[list] = None,
    ) -> Optional[str]:
        """
        学习到新知识时的钩子
        
        Args:
            knowledge: 知识内容
            source: 知识来源
            tags: 标签列表
            
        Returns:
            记忆ID
        """
        if not self.enabled:
            return None

        try:
            content = f"来源: {source}\n知识: {knowledge}"
            
            request_tags = tags or []
            request_tags.extend(["知识", source])
            
            request = SaveMemoryRequest(
                content=content,
                memory_type=MemoryType.KNOWLEDGE,
                importance=ImportanceLevel.MEDIUM,
                tags=request_tags,
            )
            
            response = await self.memory_service.save_memory(request)
            self._update_stats(MemoryType.KNOWLEDGE)
            
            logger.info(f"✅ 知识已自动保存为记忆 {response.memory_id}")
            return response.memory_id
            
        except Exception as e:
            logger.error(f"❌ 保存知识记忆失败: {e}")
            return None

    async def on_evaluation_complete(
        self,
        task_id: int,
        evaluation_result: Dict[str, Any],
        score: float,
    ) -> Optional[str]:
        """
        评估完成时的钩子
        
        Args:
            task_id: 任务ID
            evaluation_result: 评估结果
            score: 评分
            
        Returns:
            记忆ID
        """
        if not self.enabled:
            return None

        try:
            content = f"任务评估\n评分: {score}\n结果: {evaluation_result}"
            
            # 根据评分确定重要性
            if score >= 0.8:
                importance = ImportanceLevel.HIGH
            elif score >= 0.5:
                importance = ImportanceLevel.MEDIUM
            else:
                importance = ImportanceLevel.LOW
            
            request = SaveMemoryRequest(
                content=content,
                memory_type=MemoryType.EVALUATION,
                importance=importance,
                tags=["评估", f"score:{score:.2f}"],
                related_task_id=task_id,
            )
            
            response = await self.memory_service.save_memory(request)
            self._update_stats(MemoryType.EVALUATION)
            
            logger.info(f"✅ 评估已自动保存为记忆 {response.memory_id}")
            return response.memory_id
            
        except Exception as e:
            logger.error(f"❌ 保存评估记忆失败: {e}")
            return None

    def _update_stats(self, memory_type: MemoryType):
        """更新统计信息"""
        self.stats["total_saved"] += 1
        self.stats["last_save_time"] = datetime.now()
        
        type_key = memory_type.value
        if type_key not in self.stats["by_type"]:
            self.stats["by_type"][type_key] = 0
        self.stats["by_type"][type_key] += 1

    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            "enabled": self.enabled,
            "total_saved": self.stats["total_saved"],
            "by_type": self.stats["by_type"],
            "last_save_time": self.stats["last_save_time"].isoformat() if self.stats["last_save_time"] else None,
        }

    def enable(self):
        """启用记忆钩子"""
        self.enabled = True
        logger.info("✅ Memory hooks enabled")

    def disable(self):
        """禁用记忆钩子"""
        self.enabled = False
        logger.info("⏸️  Memory hooks disabled")


# 全局单例
_memory_hooks: Optional[MemoryHooks] = None


def get_memory_hooks() -> MemoryHooks:
    """获取记忆钩子服务实例"""
    global _memory_hooks
    if _memory_hooks is None:
        _memory_hooks = MemoryHooks()
    return _memory_hooks
