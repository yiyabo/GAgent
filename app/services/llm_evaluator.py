"""
LLM-based Intelligent Content Evaluator

Replaces rule-based evaluation with intelligent LLM assessment
"""

import logging
from typing import Any, Dict, List, Optional

from .base_evaluator import LLMBasedEvaluator
from ..models import EvaluationResult, EvaluationDimensions, EvaluationConfig

logger = logging.getLogger(__name__)


class LLMEvaluator(LLMBasedEvaluator):
    """LLM-powered content evaluator for intelligent assessment"""
    
    def get_evaluation_method_name(self) -> str:
        return "llm_intelligent"
        
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
            if not self.validate_content(content):
                return self.create_empty_content_result(iteration)
            
            # Use LLM for intelligent evaluation
            dimensions = self._llm_evaluate_dimensions(content, task_context)
            overall_score = self.calculate_weighted_score(dimensions)
            suggestions = self._llm_generate_suggestions(content, dimensions, task_context)
            
            # Additional metadata
            metrics = self.calculate_basic_metrics(content)
            task_name, task_type = self.extract_task_info(task_context)
            
            additional_metadata = {
                "task_name": task_name,
                "task_type": task_type,
                "content_length": metrics["char_count"],
                "word_count": metrics["word_count"]
            }
            
            return self.create_evaluation_result(
                overall_score=overall_score,
                dimensions=dimensions,
                suggestions=suggestions,
                iteration=iteration,
                additional_metadata=additional_metadata
            )
            
        except Exception as e:
            logger.error(f"Error in LLM evaluation: {e}")
            return self.create_error_result(iteration, str(e))
    
    def _llm_evaluate_dimensions(
        self,
        content: str,
        task_context: Dict[str, Any]
    ) -> EvaluationDimensions:
        """Use LLM to evaluate content across all dimensions"""
        
        evaluation_aspects = [
            "**相关性(relevance)**: 内容与任务的相关程度",
            "**完整性(completeness)**: 内容的完整性和充实度",
            "**准确性(accuracy)**: 内容的事实准确性和可信度",
            "**清晰度(clarity)**: 表达的清晰度和可读性",
            "**连贯性(coherence)**: 逻辑连贯性和结构合理性",
            "**科学严谨性(scientific_rigor)**: 科学方法和术语的规范性"
        ]
        
        specific_instructions = """
请以JSON格式返回评估结果：
{
    "relevance": 0.8,
    "completeness": 0.7,
    "accuracy": 0.9,
    "clarity": 0.8,
    "coherence": 0.8,
    "scientific_rigor": 0.7,
    "reasoning": "简要说明每个维度的评分理由"
}
"""
        
        evaluation_prompt = self.build_evaluation_prompt_template(
            content, task_context, evaluation_aspects, specific_instructions, 1000
        )
        
        required_fields = ["relevance", "completeness", "accuracy", "clarity", "coherence", "scientific_rigor"]
        scores = self.call_llm_with_json_parsing(evaluation_prompt, required_fields)
        
        if scores:
            return EvaluationDimensions(
                relevance=float(scores.get("relevance", 0.5)),
                completeness=float(scores.get("completeness", 0.5)),
                accuracy=float(scores.get("accuracy", 0.5)),
                clarity=float(scores.get("clarity", 0.5)),
                coherence=float(scores.get("coherence", 0.5)),
                scientific_rigor=float(scores.get("scientific_rigor", 0.5))
            )
        else:
            logger.warning("LLM evaluation failed, using fallback")
            return self.generate_fallback_dimensions(content)
    
    def _llm_generate_suggestions(
        self,
        content: str,
        dimensions: EvaluationDimensions,
        task_context: Dict[str, Any]
    ) -> List[str]:
        """Use LLM to generate intelligent improvement suggestions"""
        
        # Identify low-scoring dimensions
        low_scoring_dims = []
        threshold = 0.7
        
        if dimensions.relevance < threshold:
            low_scoring_dims.append(("相关性", dimensions.relevance))
        if dimensions.completeness < threshold:
            low_scoring_dims.append(("完整性", dimensions.completeness))
        if dimensions.accuracy < threshold:
            low_scoring_dims.append(("准确性", dimensions.accuracy))
        if dimensions.clarity < threshold:
            low_scoring_dims.append(("清晰度", dimensions.clarity))
        if dimensions.coherence < threshold:
            low_scoring_dims.append(("连贯性", dimensions.coherence))
        if dimensions.scientific_rigor < threshold:
            low_scoring_dims.append(("科学严谨性", dimensions.scientific_rigor))
        
        # Use base class method for suggestion generation
        return self.generate_improvement_suggestions(content, low_scoring_dims, task_context, 5)


def get_llm_evaluator(config: Optional[EvaluationConfig] = None) -> LLMEvaluator:
    """Factory function to get LLM evaluator instance"""
    return LLMEvaluator(config)