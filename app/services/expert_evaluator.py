"""
Multi-Expert LLM Evaluator System

Implements multiple expert perspectives for comprehensive content evaluation
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .base_evaluator import LLMBasedEvaluator
from ..models import EvaluationResult, EvaluationDimensions, EvaluationConfig

logger = logging.getLogger(__name__)


class ExpertRole:
    """Represents a single expert role with specialized evaluation criteria"""
    
    def __init__(self, name: str, description: str, focus_areas: List[str], weight: float = 1.0):
        self.name = name
        self.description = description
        self.focus_areas = focus_areas
        self.weight = weight
    
    def get_evaluation_prompt(self, content: str, task_context: Dict[str, Any]) -> str:
        """Generate role-specific evaluation prompt"""
        
        task_name = task_context.get("name", "")
        focus_text = "、".join(self.focus_areas)
        
        return f"""
你现在是{self.description}。请从你的专业角度评估以下内容。

任务背景："{task_name}"

需要评估的内容：
```
{content[:800]}  # 限制长度
```

作为{self.name}，你主要关注：{focus_text}

请从以下维度进行专业评估，每个维度给出0-1之间的分数：

1. **相关性**: 内容与任务的专业相关程度
2. **完整性**: 从你的专业角度看内容是否完整
3. **准确性**: 专业事实和概念的准确性
4. **实用性**: 内容的实际应用价值
5. **创新性**: 是否包含新颖的见解或方法
6. **风险评估**: 潜在的问题和风险

请以JSON格式返回：
{{
    "expert_role": "{self.name}",
    "relevance": 0.8,
    "completeness": 0.7,
    "accuracy": 0.9,
    "practicality": 0.8,
    "innovation": 0.6,
    "risk_assessment": 0.8,
    "overall_score": 0.77,
    "key_strengths": ["优势1", "优势2"],
    "major_concerns": ["问题1", "问题2"],
    "specific_suggestions": ["建议1", "建议2", "建议3"],
    "confidence_level": 0.9
}}
"""


class MultiExpertEvaluator(LLMBasedEvaluator):
    """Multi-expert evaluation system with specialized roles"""
    
    def __init__(self, config: Optional[EvaluationConfig] = None):
        super().__init__(config)
        self.experts = self._initialize_experts()
    
    def get_evaluation_method_name(self) -> str:
        return "multi_expert_llm"
    
    def _initialize_experts(self) -> Dict[str, ExpertRole]:
        """Initialize expert roles for bacteriophage research"""
        
        experts = {
            "theoretical_biologist": ExpertRole(
                name="理论生物学家",
                description="资深的理论生物学专家，专注于噬菌体生物学机制和理论基础",
                focus_areas=["生物学机制", "理论基础", "科学原理", "分子机制"],
                weight=1.0
            ),
            
            "clinical_physician": ExpertRole(
                name="临床医师",
                description="具有丰富临床经验的感染科医师，关注噬菌体治疗的临床应用",
                focus_areas=["临床安全性", "治疗效果", "患者安全", "临床可行性"],
                weight=1.2  # 临床安全性权重更高
            ),
            
            "regulatory_expert": ExpertRole(
                name="药监局审批专家",
                description="药物监管机构的审批专家，专注于法规合规性和质量控制",
                focus_areas=["法规合规", "质量控制", "安全标准", "审批要求"],
                weight=1.1
            ),
            
            "research_scientist": ExpertRole(
                name="科研工作者",
                description="噬菌体研究领域的资深科学家，关注研究方法和实验设计",
                focus_areas=["实验设计", "研究方法", "数据分析", "研究严谨性"],
                weight=0.9
            ),
            
            "biotech_entrepreneur": ExpertRole(
                name="生物技术企业家",
                description="生物技术公司的创始人/CEO，关注商业化潜力和市场前景",
                focus_areas=["商业化可行性", "市场前景", "技术壁垒", "投资回报"],
                weight=0.8  # 商业角度权重稍低
            )
        }
        
        return experts
    
    def evaluate_with_multiple_experts(
        self, 
        content: str, 
        task_context: Dict[str, Any],
        selected_experts: Optional[List[str]] = None,
        iteration: int = 0
    ) -> Dict[str, Any]:
        """
        Evaluate content using multiple expert perspectives
        
        Args:
            content: Content to evaluate
            task_context: Task context information
            selected_experts: List of expert names to use (default: all)
            iteration: Current iteration number
            
        Returns:
            Dict with individual expert evaluations and consensus
        """
        
        if not content or not content.strip():
            return self._create_empty_multi_expert_result(iteration)
        
        # Select experts to use
        if selected_experts:
            experts_to_use = {name: self.experts[name] for name in selected_experts if name in self.experts}
        else:
            experts_to_use = self.experts
        
        logger.info(f"Evaluating with {len(experts_to_use)} experts: {list(experts_to_use.keys())}")
        
        # Collect expert evaluations
        expert_evaluations = {}
        expert_weights = {}
        successful_evaluations = 0
        
        for expert_name, expert_role in experts_to_use.items():
            try:
                evaluation = self._evaluate_with_single_expert(expert_role, content, task_context)
                if evaluation:
                    expert_evaluations[expert_name] = evaluation
                    expert_weights[expert_name] = expert_role.weight
                    successful_evaluations += 1
                    logger.debug(f"Expert {expert_name} evaluation successful")
                else:
                    logger.warning(f"Expert {expert_name} evaluation returned None")
                    
            except Exception as e:
                logger.error(f"Expert {expert_name} evaluation failed: {e}")
                continue
        
        if successful_evaluations == 0:
            logger.error("All expert evaluations failed")
            return self._create_error_multi_expert_result(iteration, "All expert evaluations failed")
        
        # Generate consensus evaluation
        consensus = self._generate_expert_consensus(expert_evaluations, expert_weights)
        
        # Detect disagreements
        disagreements = self._detect_expert_disagreements(expert_evaluations)
        
        result = {
            "expert_evaluations": expert_evaluations,
            "consensus": consensus,
            "disagreements": disagreements,
            "metadata": {
                "successful_experts": successful_evaluations,
                "total_experts": len(experts_to_use),
                "evaluation_method": "multi_expert_llm",
                "iteration": iteration,
                "timestamp": datetime.now().isoformat()
            }
        }
        
        logger.info(f"Multi-expert evaluation completed: {successful_evaluations}/{len(experts_to_use)} experts, consensus score: {consensus.get('overall_score', 0):.3f}")
        
        return result
    
    def _evaluate_with_single_expert(
        self, 
        expert_role: ExpertRole, 
        content: str, 
        task_context: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Evaluate content from single expert perspective"""
        
        try:
            prompt = expert_role.get_evaluation_prompt(content, task_context)
            
            response = self.llm_client.chat([
                {"role": "user", "content": prompt}
            ])
            
            result_text = response.get("content", "").strip()
            
            # Parse JSON response
            json_start = result_text.find('{')
            json_end = result_text.rfind('}') + 1
            
            if json_start >= 0 and json_end > json_start:
                json_text = result_text[json_start:json_end]
                evaluation_data = json.loads(json_text)
                
                # Validate required fields
                required_fields = ["relevance", "completeness", "accuracy", "overall_score"]
                if all(field in evaluation_data for field in required_fields):
                    return evaluation_data
                else:
                    logger.warning(f"Expert evaluation missing required fields: {required_fields}")
                    return None
            else:
                logger.warning(f"Could not parse expert evaluation JSON from: {result_text[:200]}")
                return None
                
        except json.JSONDecodeError as e:
            logger.error(f"JSON parsing error in expert evaluation: {e}")
            return None
        except Exception as e:
            logger.error(f"Expert evaluation error: {e}")
            # Fallback to basic expert-specific evaluation
            return self._fallback_expert_evaluation(expert_role, content, task_context)
    
    def _fallback_expert_evaluation(
        self, 
        expert_role: ExpertRole, 
        content: str, 
        task_context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Fallback evaluation when LLM is unavailable"""
        
        word_count = len(content.split())
        content_lower = content.lower()
        
        # Basic scoring based on expert focus
        base_score = 0.6  # Conservative baseline
        
        # Expert-specific adjustments
        if expert_role.name == "理论生物学家":
            # Look for scientific terminology
            science_terms = ["噬菌体", "细菌", "病毒", "机制", "分子", "生物学"]
            science_score = sum(1 for term in science_terms if term in content_lower) / len(science_terms)
            base_score = max(base_score, 0.5 + science_score * 0.3)
            
        elif expert_role.name == "临床医师":
            # Look for clinical terminology
            clinical_terms = ["临床", "患者", "治疗", "安全", "副作用", "疗效"]
            clinical_score = sum(1 for term in clinical_terms if term in content_lower) / len(clinical_terms)
            base_score = max(base_score, 0.5 + clinical_score * 0.4)
            
        elif expert_role.name == "药监局审批专家":
            # Look for regulatory terminology
            regulatory_terms = ["安全", "标准", "质量", "审批", "监管", "合规"]
            reg_score = sum(1 for term in regulatory_terms if term in content_lower) / len(regulatory_terms)
            base_score = max(base_score, 0.4 + reg_score * 0.4)
            
        elif expert_role.name == "科研工作者":
            # Look for research terminology
            research_terms = ["研究", "实验", "数据", "分析", "试验", "方法"]
            research_score = sum(1 for term in research_terms if term in content_lower) / len(research_terms)
            base_score = max(base_score, 0.5 + research_score * 0.3)
            
        elif expert_role.name == "生物技术企业家":
            # Look for business terminology
            business_terms = ["市场", "商业", "投资", "成本", "前景", "应用"]
            business_score = sum(1 for term in business_terms if term in content_lower) / len(business_terms)
            base_score = max(base_score, 0.3 + business_score * 0.4)
        
        # Length adjustment
        length_factor = min(word_count / 100, 1.0)  # Optimal around 100 words
        adjusted_score = base_score * (0.7 + 0.3 * length_factor)
        
        return {
            "expert_role": expert_role.name,
            "relevance": min(adjusted_score + 0.1, 1.0),
            "completeness": max(adjusted_score - 0.1, 0.1),
            "accuracy": base_score,
            "practicality": base_score,
            "innovation": max(base_score - 0.2, 0.1),
            "risk_assessment": 0.7,  # Conservative risk assessment
            "overall_score": adjusted_score,
            "key_strengths": [f"内容与{expert_role.name}关注领域相关"],
            "major_concerns": ["LLM评估不可用，使用基础评估"],
            "specific_suggestions": [f"建议从{expert_role.name}角度进一步完善内容"],
            "confidence_level": 0.5,  # Low confidence for fallback
            "evaluation_method": "fallback"
        }
    
    def _generate_expert_consensus(
        self, 
        expert_evaluations: Dict[str, Dict[str, Any]], 
        expert_weights: Dict[str, float]
    ) -> Dict[str, Any]:
        """Generate weighted consensus from expert evaluations"""
        
        if not expert_evaluations:
            return {"overall_score": 0.0, "confidence": 0.0}
        
        # Calculate weighted averages
        weighted_scores = {}
        total_weight = sum(expert_weights.values())
        
        # Get all numeric fields from first evaluation
        sample_evaluation = next(iter(expert_evaluations.values()))
        numeric_fields = [key for key, value in sample_evaluation.items() 
                         if isinstance(value, (int, float))]
        
        for field in numeric_fields:
            weighted_sum = 0.0
            valid_count = 0
            
            for expert_name, evaluation in expert_evaluations.items():
                if field in evaluation and isinstance(evaluation[field], (int, float)):
                    weight = expert_weights.get(expert_name, 1.0)
                    weighted_sum += evaluation[field] * weight
                    valid_count += 1
            
            if valid_count > 0:
                weighted_scores[field] = weighted_sum / total_weight
            else:
                weighted_scores[field] = 0.0
        
        # Aggregate text fields
        all_strengths = []
        all_concerns = []
        all_suggestions = []
        
        for evaluation in expert_evaluations.values():
            if "key_strengths" in evaluation and isinstance(evaluation["key_strengths"], list):
                all_strengths.extend(evaluation["key_strengths"])
            if "major_concerns" in evaluation and isinstance(evaluation["major_concerns"], list):
                all_concerns.extend(evaluation["major_concerns"])
            if "specific_suggestions" in evaluation and isinstance(evaluation["specific_suggestions"], list):
                all_suggestions.extend(evaluation["specific_suggestions"])
        
        # Remove duplicates while preserving order
        consensus = {
            **weighted_scores,
            "key_strengths": list(dict.fromkeys(all_strengths)),
            "major_concerns": list(dict.fromkeys(all_concerns)),
            "specific_suggestions": list(dict.fromkeys(all_suggestions)),
            "expert_count": len(expert_evaluations),
            "consensus_confidence": self._calculate_consensus_confidence(expert_evaluations)
        }
        
        return consensus
    
    def _detect_expert_disagreements(
        self, 
        expert_evaluations: Dict[str, Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Detect significant disagreements between experts"""
        
        if len(expert_evaluations) < 2:
            return []
        
        disagreements = []
        numeric_fields = ["overall_score", "relevance", "completeness", "accuracy"]
        
        for field in numeric_fields:
            scores = []
            for expert_name, evaluation in expert_evaluations.items():
                if field in evaluation and isinstance(evaluation[field], (int, float)):
                    scores.append((expert_name, evaluation[field]))
            
            if len(scores) >= 2:
                min_score = min(scores, key=lambda x: x[1])
                max_score = max(scores, key=lambda x: x[1])
                
                # Significant disagreement if difference > 0.3
                if max_score[1] - min_score[1] > 0.3:
                    disagreements.append({
                        "field": field,
                        "disagreement_level": max_score[1] - min_score[1],
                        "lowest_scorer": min_score[0],
                        "highest_scorer": max_score[0],
                        "lowest_score": min_score[1],
                        "highest_score": max_score[1]
                    })
        
        return disagreements
    
    def _calculate_consensus_confidence(
        self, 
        expert_evaluations: Dict[str, Dict[str, Any]]
    ) -> float:
        """Calculate confidence level of expert consensus"""
        
        if len(expert_evaluations) < 2:
            return 1.0
        
        # Calculate variance in overall scores
        overall_scores = []
        for evaluation in expert_evaluations.values():
            if "overall_score" in evaluation:
                overall_scores.append(evaluation["overall_score"])
        
        if len(overall_scores) < 2:
            return 0.5
        
        # Low variance = high confidence
        mean_score = sum(overall_scores) / len(overall_scores)
        variance = sum((score - mean_score) ** 2 for score in overall_scores) / len(overall_scores)
        
        # Convert variance to confidence (inverse relationship)
        confidence = max(0.1, 1.0 - (variance * 4))  # Scale and invert
        
        return min(1.0, confidence)
    
    def _create_empty_multi_expert_result(self, iteration: int) -> Dict[str, Any]:
        """Create result for empty content"""
        return {
            "expert_evaluations": {},
            "consensus": {"overall_score": 0.0, "confidence": 0.0},
            "disagreements": [],
            "metadata": {
                "error": "empty_content",
                "iteration": iteration,
                "timestamp": datetime.now().isoformat()
            }
        }
    
    def _create_error_multi_expert_result(self, iteration: int, error_msg: str) -> Dict[str, Any]:
        """Create result for evaluation errors"""
        return {
            "expert_evaluations": {},
            "consensus": {"overall_score": 0.0, "confidence": 0.0},
            "disagreements": [],
            "metadata": {
                "error": error_msg,
                "iteration": iteration,
                "timestamp": datetime.now().isoformat()
            }
        }


def get_multi_expert_evaluator(config: Optional[EvaluationConfig] = None) -> MultiExpertEvaluator:
    """Factory function to get multi-expert evaluator instance"""
    return MultiExpertEvaluator(config)