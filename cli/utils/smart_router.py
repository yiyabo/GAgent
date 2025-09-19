"""
智能引擎路由系统

根据用户请求自动选择最合适的AI引擎：
- Perplexity: 信息查询、实时搜索、知识问答
- GLM: 工具调用、任务执行、结构化操作
"""

import re
from typing import Dict, List, Tuple
from enum import Enum

class EngineType(Enum):
    PERPLEXITY = "perplexity"
    GLM = "glm"

class SmartEngineRouter:
    """智能引擎路由器"""
    
    def __init__(self):
        # 定义路由规则
        self.perplexity_patterns = {
            # 信息查询类
            "information_query": [
                r"什么是|什么叫|解释|介绍",
                r"今天|最新|最近|现在",
                r"趋势|发展|情况|状况",
                r"新闻|资讯|消息|报道",
                r"how|what|when|where|why",
                r"latest|recent|current|today",
                r"explain|introduce|describe",
                r"news|trend|development"
            ],
            # 比较分析类
            "comparison": [
                r"比较|对比|区别|差异",
                r"哪个更好|哪种更|优劣",
                r"compare|difference|versus|vs",
                r"better|worse|advantage"
            ],
            # 学习求知类
            "learning": [
                r"学习|了解|知识|研究|原理",
                r"learn|study|understand|research"
            ]
        }
        
        self.glm_patterns = {
            # 任务管理类
            "task_management": [
                r"添加待办|加待办|新建任务|创建任务",
                r"查看待办|列出待办|待办列表|我的任务",
                r"完成待办|完成任务|标记完成",
                r"add.*todo|create.*task|new.*task",
                r"list.*todo|show.*task|my.*task",
                r"complete.*todo|finish.*task|done.*task"
            ],
            # 计划制定类
            "planning": [
                r"制定计划|做计划|规划|安排",
                r"分解任务|拆分|步骤",
                r"执行|运行|开始",
                r"make.*plan|create.*plan|planning",
                r"decompose|break.*down|steps",
                r"execute|run|start"
            ],
            # 文件操作类
            "file_operations": [
                r"保存到文件|保存文件|写入文件",
                r"创建文件|新建文件",
                r"save.*file|write.*file|create.*file"
            ],
            # 明确的工具调用
            "tool_calls": [
                r"搜索.*论文|搜索.*研究|搜索.*资料",
                r"可视化|图表|展示",
                r"search.*paper|search.*research",
                r"visualize|chart|graph"
            ]
        }
        
        # 特殊关键词权重
        self.strong_perplexity_signals = [
            "今天", "最新", "最近", "现在", "实时", "当前",
            "today", "latest", "recent", "current", "real-time", "now"
        ]
        
        self.strong_glm_signals = [
            "添加", "创建", "保存", "执行", "制定", "规划",
            "add", "create", "save", "execute", "make", "plan"
        ]

    def analyze_intent(self, user_input: str) -> Tuple[EngineType, float, str]:
        """
        分析用户意图并返回推荐的引擎
        
        Returns:
            Tuple[EngineType, float, str]: (推荐引擎, 置信度, 原因)
        """
        user_input_lower = user_input.lower()
        
        # 计算各引擎的匹配分数
        perplexity_score = self._calculate_perplexity_score(user_input_lower)
        glm_score = self._calculate_glm_score(user_input_lower)
        
        # 强信号检测
        strong_perplexity = any(signal in user_input_lower for signal in self.strong_perplexity_signals)
        strong_glm = any(signal in user_input_lower for signal in self.strong_glm_signals)
        
        # 决策逻辑
        if strong_glm and not strong_perplexity:
            return EngineType.GLM, 0.9, "检测到明确的工具操作意图"
        elif strong_perplexity and not strong_glm:
            return EngineType.PERPLEXITY, 0.9, "检测到实时信息查询需求"
        
        # 基于分数决策
        if glm_score > perplexity_score + 0.2:  # GLM需要更高的阈值
            confidence = min(0.8, glm_score)
            reason = f"工具操作意图 (GLM:{glm_score:.2f} vs PPX:{perplexity_score:.2f})"
            return EngineType.GLM, confidence, reason
        elif perplexity_score > glm_score:
            confidence = min(0.8, perplexity_score)
            reason = f"信息查询意图 (PPX:{perplexity_score:.2f} vs GLM:{glm_score:.2f})"
            return EngineType.PERPLEXITY, confidence, reason
        else:
            # 默认使用Perplexity (适合更多场景)
            return EngineType.PERPLEXITY, 0.5, "默认选择：通用信息查询"

    def _calculate_perplexity_score(self, text: str) -> float:
        """计算Perplexity引擎的匹配分数"""
        score = 0.0
        total_patterns = 0
        
        for category, patterns in self.perplexity_patterns.items():
            if isinstance(patterns, list):
                for pattern in patterns:
                    total_patterns += 1
                    if re.search(pattern, text):
                        score += 1.0
            else:
                total_patterns += 1
                if re.search(patterns, text):
                    score += 1.0
        
        # 归一化分数
        return score / max(total_patterns, 1) if total_patterns > 0 else 0.0

    def _calculate_glm_score(self, text: str) -> float:
        """计算GLM引擎的匹配分数"""
        score = 0.0
        total_patterns = 0
        
        for category, patterns in self.glm_patterns.items():
            for pattern in patterns:
                total_patterns += 1
                if re.search(pattern, text):
                    score += 1.0
        
        # 归一化分数
        return score / max(total_patterns, 1) if total_patterns > 0 else 0.0

    def should_auto_route(self, confidence: float, threshold: float = 0.7) -> bool:
        """判断是否应该自动路由（置信度足够高）"""
        return confidence >= threshold

    def get_routing_explanation(self, engine: EngineType, confidence: float, reason: str) -> str:
        """生成路由解释"""
        engine_name = "🌐 Perplexity" if engine == EngineType.PERPLEXITY else "🛠️ GLM"
        confidence_level = "高" if confidence >= 0.8 else "中" if confidence >= 0.6 else "低"
        
        return f"🤖 智能路由: {engine_name} (置信度:{confidence_level} {confidence:.1%}) - {reason}"


# 全局路由器实例
_router = None

def get_smart_router() -> SmartEngineRouter:
    """获取智能路由器实例（单例模式）"""
    global _router
    if _router is None:
        _router = SmartEngineRouter()
    return _router
