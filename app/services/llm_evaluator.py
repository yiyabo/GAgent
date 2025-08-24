"""
LLM-based Intelligent Content Evaluator

Replaces rule-based evaluation with intelligent LLM assessment
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..llm import get_default_client
from ..models import EvaluationResult, EvaluationDimensions, EvaluationConfig

logger = logging.getLogger(__name__)


class LLMEvaluator:
    """LLM-powered content evaluator for intelligent assessment"""
    
    def __init__(self, config: Optional[EvaluationConfig] = None):
        self.config = config or EvaluationConfig()
        self.llm_client = get_default_client()
        
    def evaluate_content_intelligent(
        self, 
        content: str, 
        task_context: Dict[str, Any],
        iteration: int = 0
    ) -> EvaluationResult:
        """
        Intelligent LLM-based content evaluation
        
        Args:
            content: Content to evaluate
            task_context: Task context information
            iteration: Current iteration number
            
        Returns:
            EvaluationResult with intelligent scores and suggestions
        """
        try:
            if not content or not content.strip():
                return self._create_empty_content_result(iteration)
            
            # Use LLM for intelligent evaluation
            dimensions = self._llm_evaluate_dimensions(content, task_context)
            overall_score = self._calculate_overall_score(dimensions)
            suggestions = self._llm_generate_suggestions(content, dimensions, task_context)
            
            needs_revision = overall_score < self.config.quality_threshold
            
            return EvaluationResult(
                overall_score=overall_score,
                dimensions=dimensions,
                suggestions=suggestions,
                needs_revision=needs_revision,
                iteration=iteration,
                timestamp=datetime.now(),
                metadata={
                    "task_name": task_context.get("name", ""),
                    "task_type": task_context.get("task_type", ""),
                    "content_length": len(content),
                    "word_count": len(content.split()),
                    "evaluation_method": "llm_intelligent"
                }
            )
            
        except Exception as e:
            logger.error(f"Error in LLM evaluation: {e}")
            return self._create_error_result(iteration, str(e))
    
    def _llm_evaluate_dimensions(
        self, 
        content: str, 
        task_context: Dict[str, Any]
    ) -> EvaluationDimensions:
        """Use LLM to evaluate content across all dimensions"""
        
        task_name = task_context.get("name", "")
        task_type = task_context.get("task_type", "")
        
        evaluation_prompt = f"""
作为一名资深的内容质量评估专家，请对以下内容进行专业评估。

任务背景："{task_name}"
任务类型：{task_type}

需要评估的内容：
```
{content[:1000]}  # 限制内容长度避免token过多
```

请从以下6个维度评估内容质量，每个维度给出0-1之间的分数：

1. **相关性(relevance)**: 内容与任务的相关程度
2. **完整性(completeness)**: 内容的完整性和充实度
3. **准确性(accuracy)**: 内容的事实准确性和可信度
4. **清晰度(clarity)**: 表达的清晰度和可读性
5. **连贯性(coherence)**: 逻辑连贯性和结构合理性
6. **科学严谨性(scientific_rigor)**: 科学方法和术语的规范性

请以JSON格式返回评估结果：
{{
    "relevance": 0.8,
    "completeness": 0.7,
    "accuracy": 0.9,
    "clarity": 0.8,
    "coherence": 0.8,
    "scientific_rigor": 0.7,
    "reasoning": "简要说明每个维度的评分理由"
}}
"""

        try:
            # 调用LLM进行评估
            response = self.llm_client.chat([
                {"role": "user", "content": evaluation_prompt}
            ])
            
            # 解析LLM返回的JSON结果
            result_text = response.get("content", "").strip()
            
            # 提取JSON部分
            json_start = result_text.find('{')
            json_end = result_text.rfind('}') + 1
            if json_start >= 0 and json_end > json_start:
                json_text = result_text[json_start:json_end]
                scores = json.loads(json_text)
                
                return EvaluationDimensions(
                    relevance=float(scores.get("relevance", 0.5)),
                    completeness=float(scores.get("completeness", 0.5)),
                    accuracy=float(scores.get("accuracy", 0.5)),
                    clarity=float(scores.get("clarity", 0.5)),
                    coherence=float(scores.get("coherence", 0.5)),
                    scientific_rigor=float(scores.get("scientific_rigor", 0.5))
                )
            else:
                logger.warning("Could not parse LLM evaluation JSON")
                return self._fallback_evaluation(content, task_context)
                
        except Exception as e:
            logger.error(f"LLM evaluation failed: {e}")
            return self._fallback_evaluation(content, task_context)
    
    def _llm_generate_suggestions(
        self, 
        content: str, 
        dimensions: EvaluationDimensions,
        task_context: Dict[str, Any]
    ) -> List[str]:
        """Use LLM to generate intelligent improvement suggestions"""
        
        low_scores = []
        if dimensions.relevance < 0.7:
            low_scores.append(f"相关性: {dimensions.relevance:.2f}")
        if dimensions.completeness < 0.7:
            low_scores.append(f"完整性: {dimensions.completeness:.2f}")
        if dimensions.accuracy < 0.7:
            low_scores.append(f"准确性: {dimensions.accuracy:.2f}")
        if dimensions.clarity < 0.7:
            low_scores.append(f"清晰度: {dimensions.clarity:.2f}")
        if dimensions.coherence < 0.7:
            low_scores.append(f"连贯性: {dimensions.coherence:.2f}")
        if dimensions.scientific_rigor < 0.7:
            low_scores.append(f"科学严谨性: {dimensions.scientific_rigor:.2f}")
        
        if not low_scores:
            return ["内容质量良好，无需重大修改"]
        
        suggestion_prompt = f"""
作为内容改进专家，请为以下内容提供具体的改进建议。

任务："{task_context.get('name', '')}"

内容片段：
```
{content[:500]}
```

需要改进的维度：
{', '.join(low_scores)}

请提供3-5个具体、可操作的改进建议，每个建议要明确指出：
1. 需要改进的具体问题
2. 具体的改进方法

请以简洁的列表形式返回建议。
"""

        try:
            response = self.llm_client.chat([
                {"role": "user", "content": suggestion_prompt}
            ])
            
            suggestions_text = response.get("content", "").strip()
            
            # 简单解析建议列表
            suggestions = []
            for line in suggestions_text.split('\n'):
                line = line.strip()
                if line and (line.startswith('-') or line.startswith('•') or line[0].isdigit()):
                    # 清理格式字符
                    clean_line = line.lstrip('-•0123456789. ').strip()
                    if clean_line:
                        suggestions.append(clean_line)
            
            return suggestions[:5] if suggestions else ["建议重新组织内容结构，提高相关性和完整性"]
            
        except Exception as e:
            logger.error(f"LLM suggestion generation failed: {e}")
            return [f"建议改进评分较低的维度: {', '.join(low_scores)}"]
    
    def _fallback_evaluation(
        self, 
        content: str, 
        task_context: Dict[str, Any]
    ) -> EvaluationDimensions:
        """Fallback to basic evaluation when LLM fails"""
        # 简单的fallback逻辑
        word_count = len(content.split())
        length_score = min(word_count / 200, 1.0)
        
        return EvaluationDimensions(
            relevance=max(0.5, length_score),
            completeness=length_score,
            accuracy=0.7,  # 保守评分
            clarity=max(0.6, min(1.0, 1.0 - (word_count - 150) / 500)) if word_count > 150 else 0.8,
            coherence=0.7,
            scientific_rigor=0.6
        )
    
    def _calculate_overall_score(self, dimensions: EvaluationDimensions) -> float:
        """Calculate weighted overall score"""
        if self.config.custom_weights:
            weights = self.config.custom_weights
        else:
            weights = {
                "relevance": 0.25,
                "completeness": 0.20,
                "accuracy": 0.20,
                "clarity": 0.15,
                "coherence": 0.15,
                "scientific_rigor": 0.05
            }
        
        total_score = 0.0
        for dim_name in self.config.evaluation_dimensions:
            if hasattr(dimensions, dim_name) and dim_name in weights:
                score = getattr(dimensions, dim_name)
                weight = weights[dim_name]
                total_score += score * weight
        
        return total_score
    
    def _create_empty_content_result(self, iteration: int) -> EvaluationResult:
        """Create result for empty content"""
        return EvaluationResult(
            overall_score=0.0,
            dimensions=EvaluationDimensions(),
            suggestions=["内容为空，请提供实质性内容"],
            needs_revision=True,
            iteration=iteration,
            timestamp=datetime.now()
        )
    
    def _create_error_result(self, iteration: int, error_msg: str) -> EvaluationResult:
        """Create result for evaluation errors"""
        return EvaluationResult(
            overall_score=0.0,
            dimensions=EvaluationDimensions(),
            suggestions=[f"评估出错: {error_msg}"],
            needs_revision=True,
            iteration=iteration,
            timestamp=datetime.now(),
            metadata={"error": error_msg}
        )


def get_llm_evaluator(config: Optional[EvaluationConfig] = None) -> LLMEvaluator:
    """Factory function to get LLM evaluator instance"""
    return LLMEvaluator(config)