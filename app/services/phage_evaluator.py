"""
Bacteriophage Domain-Specific Evaluator

Specialized evaluator for bacteriophage research content with domain expertise
in phage biology, therapy applications, and clinical considerations.
"""

import logging
from typing import Any, Dict, List, Optional

from .base_evaluator import LLMBasedEvaluator
from ..models import EvaluationResult, EvaluationDimensions, EvaluationConfig

logger = logging.getLogger(__name__)


class PhageEvaluator(LLMBasedEvaluator):
    """Domain-specific evaluator for bacteriophage research content"""
    
    def __init__(self, config: Optional[EvaluationConfig] = None):
        super().__init__(config)
        
        # Phage-specific terminology and concepts
        self.phage_terminology = {
            "basic_terms": [
                "噬菌体", "bacteriophage", "phage", "病毒", "细菌病毒",
                "宿主", "host", "感染", "infection", "裂解", "lysis",
                "溶原", "lysogenic", "溶菌", "lytic", "复制", "replication"
            ],
            "molecular_terms": [
                "DNA", "RNA", "基因组", "genome", "蛋白质", "protein",
                "衣壳", "capsid", "尾部", "tail", "纤维", "fiber",
                "受体", "receptor", "转录", "transcription", "翻译", "translation"
            ],
            "therapeutic_terms": [
                "噬菌体疗法", "phage therapy", "抗生素耐药", "antibiotic resistance",
                "超级细菌", "superbug", "个性化治疗", "personalized treatment",
                "生物安全", "biosafety", "临床试验", "clinical trial"
            ],
            "technical_terms": [
                "滴度", "titer", "多重感染", "MOI", "突变", "mutation",
                "进化", "evolution", "共进化", "coevolution", "CRISPR",
                "基因工程", "genetic engineering", "合成生物学", "synthetic biology"
            ]
        }
        
        # Critical evaluation aspects for phage research
        self.phage_evaluation_aspects = {
            "scientific_accuracy": "科学准确性 - 生物学概念和机制的正确性",
            "clinical_relevance": "临床相关性 - 治疗应用的可行性和安全性",
            "technical_feasibility": "技术可行性 - 实验方法和技术路线的合理性",
            "safety_considerations": "安全考量 - 生物安全和临床安全评估",
            "regulatory_compliance": "法规合规 - 药物开发和审批要求",
            "innovation_potential": "创新潜力 - 技术突破和应用前景"
        }
    
    def get_evaluation_method_name(self) -> str:
        return "phage_domain_specific"
    
    def evaluate_phage_content(
        self,
        content: str,
        task_context: Dict[str, Any],
        iteration: int = 0
    ) -> EvaluationResult:
        """
        Evaluate content with phage domain expertise
        
        Args:
            content: Content to evaluate
            task_context: Task context information
            iteration: Current iteration number
            
        Returns:
            EvaluationResult with phage-specific evaluation
        """
        try:
            if not self.validate_content(content):
                return self.create_empty_content_result(iteration)
            
            # Domain-specific analysis
            terminology_analysis = self._analyze_phage_terminology(content)
            accuracy_assessment = self._assess_scientific_accuracy(content, task_context)
            clinical_evaluation = self._evaluate_clinical_relevance(content, task_context)
            safety_assessment = self._assess_safety_and_regulatory(content, task_context)
            llm_phage_evaluation = self._llm_phage_evaluate(content, task_context)
            
            # Calculate phage-specific dimensions
            phage_dimensions = self._calculate_phage_dimensions(
                terminology_analysis, accuracy_assessment, clinical_evaluation,
                safety_assessment, llm_phage_evaluation
            )
            
            # Generate suggestions
            phage_suggestions = self._generate_phage_suggestions(
                content, terminology_analysis, accuracy_assessment,
                clinical_evaluation, safety_assessment, llm_phage_evaluation
            )
            
            overall_score = self._calculate_phage_overall_score(phage_dimensions)
            
            # Additional metadata
            additional_metadata = {
                "terminology_coverage": terminology_analysis["coverage_score"],
                "scientific_accuracy": accuracy_assessment["accuracy_score"],
                "clinical_relevance": clinical_evaluation["relevance_score"],
                "safety_score": safety_assessment["safety_score"],
                "phage_expertise_level": llm_phage_evaluation.get("expertise_level", "intermediate"),
                "domain_focus": "bacteriophage_research"
            }
            
            return self.create_evaluation_result(
                overall_score=overall_score,
                dimensions=phage_dimensions,
                suggestions=phage_suggestions,
                iteration=iteration,
                additional_metadata=additional_metadata
            )
            
        except Exception as e:
            logger.error(f"Phage evaluation failed: {e}")
            return self.create_error_result(iteration, str(e))
    
    def _analyze_phage_terminology(self, content: str) -> Dict[str, Any]:
        """Analyze usage of phage-specific terminology"""
        
        content_lower = content.lower()
        terminology_usage = {}
        total_terms = 0
        found_terms = 0
        
        for category, terms in self.phage_terminology.items():
            category_found = 0
            category_total = len(terms)
            
            for term in terms:
                total_terms += 1
                if term.lower() in content_lower:
                    found_terms += 1
                    category_found += 1
            
            terminology_usage[category] = {
                "found": category_found,
                "total": category_total,
                "coverage": category_found / category_total if category_total > 0 else 0.0
            }
        
        overall_coverage = found_terms / total_terms if total_terms > 0 else 0.0
        
        # Assess terminology sophistication
        sophistication_score = 0.0
        if terminology_usage["technical_terms"]["coverage"] > 0.3:
            sophistication_score += 0.4
        if terminology_usage["molecular_terms"]["coverage"] > 0.2:
            sophistication_score += 0.3
        if terminology_usage["therapeutic_terms"]["coverage"] > 0.2:
            sophistication_score += 0.3
        
        return {
            "coverage_score": overall_coverage,
            "sophistication_score": sophistication_score,
            "category_usage": terminology_usage,
            "terminology_depth": "high" if sophistication_score > 0.7 else "medium" if sophistication_score > 0.4 else "low"
        }
    
    def _assess_scientific_accuracy(self, content: str, task_context: Dict[str, Any]) -> Dict[str, Any]:
        """Assess scientific accuracy of phage-related content"""
        
        accuracy_indicators = {
            "mechanism_accuracy": 0.0,
            "factual_correctness": 0.0,
            "conceptual_clarity": 0.0
        }
        
        content_lower = content.lower()
        
        # Check for common phage biology concepts
        phage_concepts = {
            "lytic_cycle": ["裂解周期", "lytic cycle", "裂解", "lysis"],
            "lysogenic_cycle": ["溶原周期", "lysogenic cycle", "溶原", "lysogenic"],
            "host_specificity": ["宿主特异性", "host specificity", "特异性", "specificity"],
            "resistance_mechanisms": ["耐药机制", "resistance mechanism", "耐药性", "resistance"]
        }
        
        concept_coverage = 0
        for concept, keywords in phage_concepts.items():
            if any(keyword in content_lower for keyword in keywords):
                concept_coverage += 1
        
        accuracy_indicators["mechanism_accuracy"] = concept_coverage / len(phage_concepts)
        
        # Check for potential inaccuracies (simple heuristics)
        inaccuracy_flags = [
            "噬菌体是细菌",  # Phages are not bacteria
            "噬菌体可以感染人类",  # Most phages don't infect humans directly
            "所有噬菌体都是有害的",  # Many phages are beneficial
        ]
        
        inaccuracy_count = sum(1 for flag in inaccuracy_flags if flag in content)
        accuracy_indicators["factual_correctness"] = max(0.0, 1.0 - (inaccuracy_count * 0.3))
        
        # Assess conceptual clarity
        clarity_indicators = [
            "机制" in content_lower,
            "原理" in content_lower,
            "过程" in content_lower,
            "步骤" in content_lower
        ]
        
        accuracy_indicators["conceptual_clarity"] = sum(clarity_indicators) / len(clarity_indicators)
        
        overall_accuracy = sum(accuracy_indicators.values()) / len(accuracy_indicators)
        
        return {
            "accuracy_score": overall_accuracy,
            "accuracy_indicators": accuracy_indicators,
            "concept_coverage": concept_coverage,
            "inaccuracy_flags": inaccuracy_count
        }
    
    def _evaluate_clinical_relevance(self, content: str, task_context: Dict[str, Any]) -> Dict[str, Any]:
        """Evaluate clinical relevance and therapeutic potential"""
        
        content_lower = content.lower()
        
        clinical_indicators = {
            "therapeutic_application": 0.0,
            "safety_awareness": 0.0,
            "clinical_feasibility": 0.0,
            "patient_benefit": 0.0
        }
        
        # Therapeutic application indicators
        therapeutic_terms = [
            "治疗", "therapy", "疗法", "treatment", "临床", "clinical",
            "患者", "patient", "疾病", "disease", "感染", "infection"
        ]
        therapeutic_score = sum(1 for term in therapeutic_terms if term in content_lower)
        clinical_indicators["therapeutic_application"] = min(1.0, therapeutic_score / 5)
        
        # Safety awareness indicators
        safety_terms = [
            "安全", "safety", "副作用", "side effect", "风险", "risk",
            "毒性", "toxicity", "免疫", "immune", "过敏", "allergy"
        ]
        safety_score = sum(1 for term in safety_terms if term in content_lower)
        clinical_indicators["safety_awareness"] = min(1.0, safety_score / 4)
        
        # Clinical feasibility indicators
        feasibility_terms = [
            "剂量", "dose", "给药", "administration", "制备", "preparation",
            "储存", "storage", "稳定性", "stability", "质控", "quality control"
        ]
        feasibility_score = sum(1 for term in feasibility_terms if term in content_lower)
        clinical_indicators["clinical_feasibility"] = min(1.0, feasibility_score / 4)
        
        # Patient benefit indicators
        benefit_terms = [
            "疗效", "efficacy", "有效", "effective", "改善", "improvement",
            "康复", "recovery", "治愈", "cure", "缓解", "relief"
        ]
        benefit_score = sum(1 for term in benefit_terms if term in content_lower)
        clinical_indicators["patient_benefit"] = min(1.0, benefit_score / 3)
        
        overall_relevance = sum(clinical_indicators.values()) / len(clinical_indicators)
        
        return {
            "relevance_score": overall_relevance,
            "clinical_indicators": clinical_indicators,
            "therapeutic_focus": therapeutic_score > 2,
            "safety_conscious": safety_score > 1
        }
    
    def _assess_safety_and_regulatory(self, content: str, task_context: Dict[str, Any]) -> Dict[str, Any]:
        """Assess safety considerations and regulatory compliance awareness"""
        
        content_lower = content.lower()
        
        safety_aspects = {
            "biosafety_awareness": 0.0,
            "regulatory_knowledge": 0.0,
            "risk_assessment": 0.0,
            "quality_control": 0.0
        }
        
        # Biosafety awareness
        biosafety_terms = [
            "生物安全", "biosafety", "生物防护", "biocontainment",
            "实验室安全", "laboratory safety", "防护", "protection"
        ]
        biosafety_score = sum(1 for term in biosafety_terms if term in content_lower)
        safety_aspects["biosafety_awareness"] = min(1.0, biosafety_score / 3)
        
        # Regulatory knowledge
        regulatory_terms = [
            "FDA", "NMPA", "药监局", "审批", "approval", "监管", "regulation",
            "临床试验", "clinical trial", "GMP", "质量标准", "quality standard"
        ]
        regulatory_score = sum(1 for term in regulatory_terms if term in content_lower)
        safety_aspects["regulatory_knowledge"] = min(1.0, regulatory_score / 4)
        
        # Risk assessment
        risk_terms = [
            "风险评估", "risk assessment", "风险", "risk", "危险", "hazard",
            "不良反应", "adverse reaction", "禁忌", "contraindication"
        ]
        risk_score = sum(1 for term in risk_terms if term in content_lower)
        safety_aspects["risk_assessment"] = min(1.0, risk_score / 3)
        
        # Quality control
        qc_terms = [
            "质量控制", "quality control", "质检", "QC", "标准化", "standardization",
            "纯度", "purity", "活性", "activity", "稳定性", "stability"
        ]
        qc_score = sum(1 for term in qc_terms if term in content_lower)
        safety_aspects["quality_control"] = min(1.0, qc_score / 3)
        
        overall_safety = sum(safety_aspects.values()) / len(safety_aspects)
        
        return {
            "safety_score": overall_safety,
            "safety_aspects": safety_aspects,
            "regulatory_aware": regulatory_score > 1,
            "safety_conscious": biosafety_score > 0 or risk_score > 0
        }
    
    def _llm_phage_evaluate(self, content: str, task_context: Dict[str, Any]) -> Dict[str, Any]:
        """Use LLM for phage domain expert evaluation"""
        
        evaluation_aspects = [
            "**科学准确性**: 噬菌体生物学概念和机制是否准确？",
            "**临床相关性**: 内容对噬菌体治疗应用的相关程度？",
            "**技术可行性**: 提到的技术方法是否在当前条件下可行？",
            "**安全考量**: 是否充分考虑了生物安全和临床安全？",
            "**创新潜力**: 内容是否体现了该领域的创新性和前沿性？",
            "**专业深度**: 内容的专业水平和技术深度如何？"
        ]
        
        specific_instructions = """
作为噬菌体研究领域的资深专家，请从专业角度评估内容。

请以JSON格式返回评估结果：
{
    "scientific_accuracy": 0.8,
    "clinical_relevance": 0.7,
    "technical_feasibility": 0.9,
    "safety_considerations": 0.8,
    "innovation_potential": 0.6,
    "professional_depth": 0.7,
    "overall_expert_score": 0.75,
    "expertise_level": "advanced",
    "key_strengths": ["优势1", "优势2"],
    "technical_concerns": ["技术问题1", "技术问题2"],
    "improvement_suggestions": ["建议1", "建议2", "建议3"],
    "phage_specific_insights": ["专业洞察1", "专业洞察2"],
    "confidence_level": 0.9
}
"""
        
        phage_expert_prompt = self.build_evaluation_prompt_template(
            content, task_context, evaluation_aspects, specific_instructions, 800
        )
        
        required_fields = ["scientific_accuracy", "clinical_relevance", "technical_feasibility"]
        result = self.call_llm_with_json_parsing(phage_expert_prompt, required_fields)
        
        return result if result else self._fallback_phage_llm_evaluation()
    
    def _fallback_phage_llm_evaluation(self) -> Dict[str, Any]:
        """Fallback evaluation when LLM is unavailable"""
        return {
            "scientific_accuracy": 0.6,
            "clinical_relevance": 0.6,
            "technical_feasibility": 0.6,
            "safety_considerations": 0.6,
            "innovation_potential": 0.5,
            "professional_depth": 0.5,
            "overall_expert_score": 0.55,
            "expertise_level": "basic",
            "key_strengths": ["基础内容覆盖"],
            "technical_concerns": ["LLM专家评估不可用"],
            "improvement_suggestions": ["建议增加专业深度"],
            "phage_specific_insights": ["需要更多噬菌体专业知识"],
            "confidence_level": 0.3,
            "evaluation_method": "fallback"
        }
    
    def _calculate_phage_dimensions(
        self,
        terminology_analysis: Dict[str, Any],
        accuracy_assessment: Dict[str, Any],
        clinical_evaluation: Dict[str, Any],
        safety_assessment: Dict[str, Any],
        llm_phage_evaluation: Dict[str, Any]
    ) -> EvaluationDimensions:
        """Calculate phage-specific evaluation dimensions"""
        
        # Map phage-specific assessments to standard dimensions
        relevance = (
            terminology_analysis["coverage_score"] * 0.3 +
            clinical_evaluation["relevance_score"] * 0.4 +
            llm_phage_evaluation.get("clinical_relevance", 0.5) * 0.3
        )
        
        completeness = (
            terminology_analysis["sophistication_score"] * 0.3 +
            accuracy_assessment["accuracy_score"] * 0.4 +
            llm_phage_evaluation.get("professional_depth", 0.5) * 0.3
        )
        
        accuracy = (
            accuracy_assessment["accuracy_score"] * 0.5 +
            llm_phage_evaluation.get("scientific_accuracy", 0.5) * 0.5
        )
        
        clarity = llm_phage_evaluation.get("professional_depth", 0.5)
        
        coherence = (
            accuracy_assessment["accuracy_score"] * 0.4 +
            llm_phage_evaluation.get("technical_feasibility", 0.5) * 0.6
        )
        
        scientific_rigor = (
            safety_assessment["safety_score"] * 0.4 +
            llm_phage_evaluation.get("safety_considerations", 0.5) * 0.3 +
            llm_phage_evaluation.get("scientific_accuracy", 0.5) * 0.3
        )
        
        return EvaluationDimensions(
            relevance=min(1.0, max(0.0, relevance)),
            completeness=min(1.0, max(0.0, completeness)),
            accuracy=min(1.0, max(0.0, accuracy)),
            clarity=min(1.0, max(0.0, clarity)),
            coherence=min(1.0, max(0.0, coherence)),
            scientific_rigor=min(1.0, max(0.0, scientific_rigor))
        )
    
    def _generate_phage_suggestions(
        self,
        content: str,
        terminology_analysis: Dict[str, Any],
        accuracy_assessment: Dict[str, Any],
        clinical_evaluation: Dict[str, Any],
        safety_assessment: Dict[str, Any],
        llm_phage_evaluation: Dict[str, Any]
    ) -> List[str]:
        """Generate phage-specific improvement suggestions"""
        
        suggestions = []
        
        # Terminology suggestions
        if terminology_analysis["coverage_score"] < 0.3:
            suggestions.append("建议增加更多噬菌体专业术语，提高内容的专业性")
        
        if terminology_analysis["sophistication_score"] < 0.5:
            suggestions.append("建议使用更多技术性和分子生物学术语，增强内容深度")
        
        # Accuracy suggestions
        if accuracy_assessment["accuracy_score"] < 0.7:
            suggestions.append("建议核实噬菌体生物学机制的准确性，确保科学概念正确")
        
        if accuracy_assessment["concept_coverage"] < 2:
            suggestions.append("建议补充噬菌体生命周期、宿主特异性等核心概念")
        
        # Clinical relevance suggestions
        if clinical_evaluation["relevance_score"] < 0.6:
            suggestions.append("建议增加噬菌体治疗应用的临床相关内容")
        
        if not clinical_evaluation["safety_conscious"]:
            suggestions.append("建议增加临床安全性和副作用的讨论")
        
        # Safety and regulatory suggestions
        if safety_assessment["safety_score"] < 0.5:
            suggestions.append("建议加强生物安全和监管合规方面的内容")
        
        if not safety_assessment["regulatory_aware"]:
            suggestions.append("建议补充药物审批和质量控制相关要求")
        
        # LLM expert suggestions
        llm_suggestions = llm_phage_evaluation.get("improvement_suggestions", [])
        suggestions.extend(llm_suggestions[:3])  # Add top 3 LLM suggestions
        
        # Innovation suggestions
        innovation_score = llm_phage_evaluation.get("innovation_potential", 0.5)
        if innovation_score < 0.6:
            suggestions.append("建议增加噬菌体领域的最新研究进展和创新应用")
        
        return suggestions[:8]  # Limit to top 8 suggestions
    
    def _calculate_phage_overall_score(self, dimensions: EvaluationDimensions) -> float:
        """Calculate overall score with phage-specific weights"""
        
        # Phage-specific dimension weights
        phage_weights = {
            "relevance": 0.20,      # Clinical relevance is important
            "completeness": 0.15,   # Professional completeness
            "accuracy": 0.25,       # Scientific accuracy is critical
            "clarity": 0.10,        # Professional clarity
            "coherence": 0.15,      # Technical coherence
            "scientific_rigor": 0.15  # Safety and rigor are crucial
        }
        
        return self.calculate_weighted_score(dimensions, phage_weights)


def get_phage_evaluator(config: Optional[EvaluationConfig] = None) -> PhageEvaluator:
    """Factory function to get phage evaluator instance"""
    return PhageEvaluator(config)