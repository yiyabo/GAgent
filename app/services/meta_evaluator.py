"""
Meta-Cognitive Evaluation System

Evaluates the quality of evaluations themselves, detects cognitive biases,
and provides meta-cognitive insights about the evaluation process.
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from statistics import mean, stdev

from .base_evaluator import LLMBasedEvaluator
from ..models import EvaluationResult, EvaluationDimensions, EvaluationConfig

logger = logging.getLogger(__name__)


class MetaEvaluator(LLMBasedEvaluator):
    """Meta-cognitive evaluator that evaluates evaluation quality"""
    
    def __init__(self, config: Optional[EvaluationConfig] = None):
        super().__init__(config)
        
        # Meta-evaluation criteria
        self.meta_criteria = {
            "consistency": "评估结果的一致性和稳定性",
            "objectivity": "评估过程的客观性，避免主观偏见",
            "comprehensiveness": "评估维度的全面性和完整性",
            "calibration": "评估分数与实际质量的校准程度",
            "discriminability": "评估系统区分不同质量内容的能力",
            "reliability": "评估结果的可靠性和可重复性"
        }
    
    def get_evaluation_method_name(self) -> str:
        return "meta_cognitive"
    
    def meta_evaluate(
        self, 
        evaluation_history: List[Dict[str, Any]], 
        content: str,
        task_context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Perform meta-evaluation on a series of evaluations
        
        Args:
            evaluation_history: List of evaluation results
            content: The content that was evaluated
            task_context: Context information about the task
            
        Returns:
            Meta-evaluation results with quality scores and insights
        """
        try:
            if not evaluation_history:
                return self._create_empty_meta_result()
            
            # Analyze evaluation consistency
            consistency_analysis = self._analyze_consistency(evaluation_history)
            
            # Detect cognitive biases
            bias_analysis = self._detect_cognitive_biases(evaluation_history, content, task_context)
            
            # Evaluate evaluation quality using LLM
            llm_meta_evaluation = self._llm_meta_evaluate(evaluation_history, content, task_context)
            
            # Calculate meta-scores
            meta_scores = self._calculate_meta_scores(
                consistency_analysis, 
                bias_analysis, 
                llm_meta_evaluation
            )
            
            # Generate meta-insights
            meta_insights = self._generate_meta_insights(
                consistency_analysis,
                bias_analysis, 
                llm_meta_evaluation,
                meta_scores
            )
            
            # Assess evaluation system health
            system_health = self._assess_system_health(evaluation_history, meta_scores)
            
            return {
                "meta_scores": meta_scores,
                "consistency_analysis": consistency_analysis,
                "bias_analysis": bias_analysis,
                "llm_meta_evaluation": llm_meta_evaluation,
                "meta_insights": meta_insights,
                "system_health": system_health,
                "evaluation_count": len(evaluation_history),
                "timestamp": datetime.now().isoformat(),
                "meta_evaluation_version": "1.0"
            }
            
        except Exception as e:
            logger.error(f"Meta-evaluation failed: {e}")
            return self._create_error_meta_result(str(e))
    
    def _analyze_consistency(self, evaluation_history: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Analyze consistency across evaluations"""
        
        if len(evaluation_history) < 2:
            return {
                "consistency_score": 1.0,
                "score_variance": 0.0,
                "trend_analysis": "insufficient_data",
                "stability_rating": "unknown"
            }
        
        # Extract scores
        overall_scores = [eval_data.get("overall_score", 0.0) for eval_data in evaluation_history]
        
        # Calculate variance and consistency
        score_variance = stdev(overall_scores) if len(overall_scores) > 1 else 0.0
        consistency_score = max(0.0, 1.0 - (score_variance * 2))  # Lower variance = higher consistency
        
        # Trend analysis
        if len(overall_scores) >= 3:
            early_scores = overall_scores[:len(overall_scores)//2]
            late_scores = overall_scores[len(overall_scores)//2:]
            
            early_mean = mean(early_scores)
            late_mean = mean(late_scores)
            
            if late_mean > early_mean + 0.1:
                trend = "improving"
            elif late_mean < early_mean - 0.1:
                trend = "declining"
            else:
                trend = "stable"
        else:
            trend = "insufficient_data"
        
        # Stability rating
        if score_variance < 0.05:
            stability = "very_stable"
        elif score_variance < 0.1:
            stability = "stable"
        elif score_variance < 0.2:
            stability = "moderately_stable"
        else:
            stability = "unstable"
        
        return {
            "consistency_score": consistency_score,
            "score_variance": score_variance,
            "score_range": (min(overall_scores), max(overall_scores)),
            "trend_analysis": trend,
            "stability_rating": stability,
            "score_progression": overall_scores
        }
    
    def _detect_cognitive_biases(
        self, 
        evaluation_history: List[Dict[str, Any]], 
        content: str,
        task_context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Detect potential cognitive biases in evaluations"""
        
        biases_detected = []
        bias_scores = {}
        
        # Anchoring bias - first evaluation heavily influences later ones
        if len(evaluation_history) >= 3:
            first_score = evaluation_history[0].get("overall_score", 0.5)
            subsequent_scores = [eval_data.get("overall_score", 0.5) for eval_data in evaluation_history[1:]]
            
            # Check if subsequent scores cluster around first score
            deviations = [abs(score - first_score) for score in subsequent_scores]
            avg_deviation = mean(deviations)
            
            if avg_deviation < 0.1:  # Very close to first score
                biases_detected.append("anchoring_bias")
                bias_scores["anchoring_bias"] = 1.0 - avg_deviation * 10
        
        # Confirmation bias - scores don't improve despite iterations
        if len(evaluation_history) >= 3:
            scores = [eval_data.get("overall_score", 0.5) for eval_data in evaluation_history]
            if all(abs(scores[i] - scores[0]) < 0.05 for i in range(1, len(scores))):
                biases_detected.append("confirmation_bias")
                bias_scores["confirmation_bias"] = 0.8
        
        # Halo effect - all dimensions score similarly
        dimension_correlation = self._analyze_dimension_correlation(evaluation_history)
        if dimension_correlation > 0.9:
            biases_detected.append("halo_effect")
            bias_scores["halo_effect"] = dimension_correlation
        
        # Recency bias - later evaluations weighted too heavily
        if len(evaluation_history) >= 4:
            early_scores = evaluation_history[:2]
            late_scores = evaluation_history[-2:]
            
            early_avg = mean([eval_data.get("overall_score", 0.5) for eval_data in early_scores])
            late_avg = mean([eval_data.get("overall_score", 0.5) for eval_data in late_scores])
            
            if abs(late_avg - early_avg) > 0.3:  # Large difference
                biases_detected.append("recency_bias")
                bias_scores["recency_bias"] = abs(late_avg - early_avg)
        
        # Severity bias - consistently harsh or lenient scoring
        all_scores = [eval_data.get("overall_score", 0.5) for eval_data in evaluation_history]
        avg_score = mean(all_scores)
        
        if avg_score < 0.3:
            biases_detected.append("severity_bias")
            bias_scores["severity_bias"] = 0.3 - avg_score
        elif avg_score > 0.9:
            biases_detected.append("leniency_bias")
            bias_scores["leniency_bias"] = avg_score - 0.9
        
        return {
            "biases_detected": biases_detected,
            "bias_scores": bias_scores,
            "bias_count": len(biases_detected),
            "overall_bias_risk": mean(bias_scores.values()) if bias_scores else 0.0,
            "dimension_correlation": dimension_correlation
        }
    
    def _analyze_dimension_correlation(self, evaluation_history: List[Dict[str, Any]]) -> float:
        """Analyze correlation between evaluation dimensions"""
        
        if not evaluation_history:
            return 0.0
        
        # Extract dimension scores
        all_dimensions = []
        for eval_data in evaluation_history:
            dimension_scores = eval_data.get("dimension_scores", {})
            if dimension_scores:
                all_dimensions.append(list(dimension_scores.values()))
        
        if len(all_dimensions) < 2:
            return 0.0
        
        # Calculate average correlation between dimensions
        correlations = []
        for i in range(len(all_dimensions[0])):
            for j in range(i + 1, len(all_dimensions[0])):
                dim_i_scores = [dims[i] for dims in all_dimensions if len(dims) > max(i, j)]
                dim_j_scores = [dims[j] for dims in all_dimensions if len(dims) > max(i, j)]
                
                if len(dim_i_scores) >= 2:
                    # Simple correlation calculation
                    mean_i = mean(dim_i_scores)
                    mean_j = mean(dim_j_scores)
                    
                    numerator = sum((dim_i_scores[k] - mean_i) * (dim_j_scores[k] - mean_j) 
                                  for k in range(len(dim_i_scores)))
                    
                    denom_i = sum((score - mean_i) ** 2 for score in dim_i_scores)
                    denom_j = sum((score - mean_j) ** 2 for score in dim_j_scores)
                    
                    if denom_i > 0 and denom_j > 0:
                        correlation = numerator / (denom_i * denom_j) ** 0.5
                        correlations.append(abs(correlation))
        
        return mean(correlations) if correlations else 0.0
    
    def _llm_meta_evaluate(
        self,
        evaluation_history: List[Dict[str, Any]],
        content: str,
        task_context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Use LLM to perform meta-evaluation"""
        
        # Prepare evaluation summary for LLM
        eval_summary = self._prepare_evaluation_summary(evaluation_history)
        
        evaluation_aspects = [
            "**评估准确性**: 评估结果是否准确反映内容质量？",
            "**评估全面性**: 评估维度是否全面覆盖内容质量要素？",
            "**评估一致性**: 多次评估结果是否保持一致？",
            "**评估客观性**: 评估过程是否客观，避免主观偏见？",
            "**评估实用性**: 评估建议是否具有实际指导价值？"
        ]
        
        specific_instructions = f"""
作为评估质量专家，请对以下评估过程进行元认知分析。

评估历史摘要：
{eval_summary}

请以JSON格式返回分析结果：
{{
    "accuracy_assessment": 0.8,
    "comprehensiveness_assessment": 0.7,
    "consistency_assessment": 0.9,
    "objectivity_assessment": 0.8,
    "utility_assessment": 0.7,
    "overall_meta_score": 0.78,
    "key_strengths": ["优势1", "优势2"],
    "improvement_areas": ["改进点1", "改进点2"],
    "meta_insights": ["洞察1", "洞察2"],
    "confidence_level": 0.85
}}
"""
        
        meta_prompt = self.build_evaluation_prompt_template(
            content, task_context, evaluation_aspects, specific_instructions, 500
        )
        
        required_fields = ["accuracy_assessment", "consistency_assessment", "overall_meta_score"]
        result = self.call_llm_with_json_parsing(meta_prompt, required_fields)
        
        return result if result else self._fallback_llm_meta_evaluation()
    
    def _prepare_evaluation_summary(self, evaluation_history: List[Dict[str, Any]]) -> str:
        """Prepare a summary of evaluation history for LLM"""
        
        if not evaluation_history:
            return "无评估历史"
        
        summary_lines = []
        for i, eval_data in enumerate(evaluation_history, 1):
            score = eval_data.get("overall_score", 0.0)
            suggestions_count = len(eval_data.get("suggestions", []))
            needs_revision = eval_data.get("needs_revision", False)
            
            summary_lines.append(
                f"第{i}轮: 评分{score:.2f}, {suggestions_count}条建议, "
                f"{'需要修订' if needs_revision else '质量达标'}"
            )
        
        return "\n".join(summary_lines)
    
    def _fallback_llm_meta_evaluation(self) -> Dict[str, Any]:
        """Fallback meta-evaluation when LLM is unavailable"""
        return {
            "accuracy_assessment": 0.6,
            "comprehensiveness_assessment": 0.6,
            "consistency_assessment": 0.6,
            "objectivity_assessment": 0.6,
            "utility_assessment": 0.6,
            "overall_meta_score": 0.6,
            "key_strengths": ["基础评估功能正常"],
            "improvement_areas": ["LLM元评估不可用"],
            "meta_insights": ["建议检查LLM连接"],
            "confidence_level": 0.3,
            "evaluation_method": "fallback"
        }
    
    def _calculate_meta_scores(
        self, 
        consistency_analysis: Dict[str, Any],
        bias_analysis: Dict[str, Any],
        llm_meta_evaluation: Dict[str, Any]
    ) -> Dict[str, float]:
        """Calculate comprehensive meta-evaluation scores"""
        
        # Weight different aspects
        consistency_weight = 0.3
        bias_weight = 0.3
        llm_weight = 0.4
        
        # Consistency score
        consistency_score = consistency_analysis.get("consistency_score", 0.5)
        
        # Bias score (lower bias = higher score)
        bias_risk = bias_analysis.get("overall_bias_risk", 0.0)
        bias_score = max(0.0, 1.0 - bias_risk)
        
        # LLM meta score
        llm_score = llm_meta_evaluation.get("overall_meta_score", 0.5)
        
        # Calculate weighted overall meta score
        overall_meta_score = (
            consistency_score * consistency_weight +
            bias_score * bias_weight +
            llm_score * llm_weight
        )
        
        return {
            "consistency": consistency_score,
            "objectivity": bias_score,
            "comprehensiveness": llm_meta_evaluation.get("comprehensiveness_assessment", 0.5),
            "accuracy": llm_meta_evaluation.get("accuracy_assessment", 0.5),
            "utility": llm_meta_evaluation.get("utility_assessment", 0.5),
            "overall_meta_score": overall_meta_score,
            "confidence": llm_meta_evaluation.get("confidence_level", 0.5)
        }
    
    def _generate_meta_insights(
        self,
        consistency_analysis: Dict[str, Any],
        bias_analysis: Dict[str, Any],
        llm_meta_evaluation: Dict[str, Any],
        meta_scores: Dict[str, float]
    ) -> List[str]:
        """Generate actionable meta-insights"""
        
        insights = []
        
        # Consistency insights
        stability = consistency_analysis.get("stability_rating", "unknown")
        if stability == "unstable":
            insights.append("评估结果不稳定，建议检查评估标准的一致性")
        elif stability == "very_stable":
            insights.append("评估结果高度稳定，显示评估系统运行良好")
        
        # Bias insights
        biases = bias_analysis.get("biases_detected", [])
        if "anchoring_bias" in biases:
            insights.append("检测到锚定偏见，后续评估过度依赖首次评估结果")
        if "halo_effect" in biases:
            insights.append("检测到光环效应，各评估维度相关性过高")
        if "severity_bias" in biases:
            insights.append("检测到严厉偏见，评估标准可能过于苛刻")
        if "leniency_bias" in biases:
            insights.append("检测到宽松偏见，评估标准可能过于宽松")
        
        # LLM insights
        llm_insights = llm_meta_evaluation.get("meta_insights", [])
        insights.extend(llm_insights)
        
        # Score-based insights
        if meta_scores.get("overall_meta_score", 0.5) < 0.6:
            insights.append("整体评估质量偏低，建议优化评估流程")
        elif meta_scores.get("overall_meta_score", 0.5) > 0.8:
            insights.append("评估系统表现优秀，质量控制良好")
        
        return insights[:10]  # Limit to top 10 insights
    
    def _assess_system_health(
        self, 
        evaluation_history: List[Dict[str, Any]], 
        meta_scores: Dict[str, float]
    ) -> Dict[str, Any]:
        """Assess overall evaluation system health"""
        
        health_indicators = {
            "evaluation_volume": len(evaluation_history),
            "average_meta_score": meta_scores.get("overall_meta_score", 0.5),
            "consistency_level": meta_scores.get("consistency", 0.5),
            "bias_control": meta_scores.get("objectivity", 0.5),
            "system_reliability": meta_scores.get("confidence", 0.5)
        }
        
        # Calculate overall health score
        health_score = mean(health_indicators.values())
        
        # Determine health status
        if health_score >= 0.8:
            health_status = "excellent"
        elif health_score >= 0.7:
            health_status = "good"
        elif health_score >= 0.6:
            health_status = "fair"
        else:
            health_status = "poor"
        
        # Generate recommendations
        recommendations = []
        if health_indicators["consistency_level"] < 0.6:
            recommendations.append("提高评估标准的一致性")
        if health_indicators["bias_control"] < 0.6:
            recommendations.append("加强认知偏见控制")
        if health_indicators["evaluation_volume"] < 5:
            recommendations.append("增加评估样本量以提高可靠性")
        
        return {
            "health_score": health_score,
            "health_status": health_status,
            "health_indicators": health_indicators,
            "recommendations": recommendations,
            "last_assessment": datetime.now().isoformat()
        }
    
    def _create_empty_meta_result(self) -> Dict[str, Any]:
        """Create result for empty evaluation history"""
        return {
            "meta_scores": {"overall_meta_score": 0.0},
            "consistency_analysis": {"consistency_score": 0.0},
            "bias_analysis": {"biases_detected": [], "overall_bias_risk": 0.0},
            "llm_meta_evaluation": {"overall_meta_score": 0.0},
            "meta_insights": ["无评估历史可供分析"],
            "system_health": {"health_status": "unknown"},
            "evaluation_count": 0,
            "timestamp": datetime.now().isoformat(),
            "error": "empty_evaluation_history"
        }
    
    def _create_error_meta_result(self, error_msg: str) -> Dict[str, Any]:
        """Create result for meta-evaluation errors"""
        return {
            "meta_scores": {"overall_meta_score": 0.0},
            "consistency_analysis": {"consistency_score": 0.0},
            "bias_analysis": {"biases_detected": [], "overall_bias_risk": 0.0},
            "llm_meta_evaluation": {"overall_meta_score": 0.0},
            "meta_insights": [f"元评估出错: {error_msg}"],
            "system_health": {"health_status": "error"},
            "evaluation_count": 0,
            "timestamp": datetime.now().isoformat(),
            "error": error_msg
        }


def get_meta_evaluator(config: Optional[EvaluationConfig] = None) -> MetaEvaluator:
    """Factory function to get meta-evaluator instance"""
    return MetaEvaluator(config)