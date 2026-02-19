"""
Bacteriophage Domain-Specific Evaluator.

Specialized evaluator for bacteriophage research content with domain expertise
in phage biology, therapy applications, and clinical considerations.
"""

import logging
from typing import Any, Dict, List, Optional

from ...models import EvaluationConfig, EvaluationDimensions, EvaluationResult
from .base_evaluator import LLMBasedEvaluator

logger = logging.getLogger(__name__)


class PhageEvaluator(LLMBasedEvaluator):
    """Domain-specific evaluator for bacteriophage research content"""

    def __init__(self, config: Optional[EvaluationConfig] = None):
        super().__init__(config)

        # Phage-specific terminology and concepts
        self.phage_terminology = {
            "basic_terms": [
                "bacteriophage",
                "phage",
                "host",
                "infection",
                "lysis",
                "lysogenic",
                "lytic",
                "replication",
            ],
            "molecular_terms": [
                "DNA",
                "RNA",
                "genome",
                "protein",
                "capsid",
                "tail",
                "fiber",
                "receptor",
                "transcription",
                "translation",
            ],
            "therapeutic_terms": [
                "phage therapy",
                "antibiotic resistance",
                "superbug",
                "personalized treatment",
                "biosafety",
                "clinical trial",
            ],
            "technical_terms": [
                "titer",
                "MOI",
                "mutation",
                "evolution",
                "coevolution",
                "CRISPR",
                "genetic engineering",
                "synthetic biology",
            ],
        }

        # Critical evaluation aspects for phage research
        self.phage_evaluation_aspects = {
            "scientific_accuracy": "Scientific accuracy - correctness of biological concepts and mechanisms",
            "clinical_relevance": "Clinical relevance - feasibility and safety of therapeutic applications",
            "technical_feasibility": "Technical feasibility - reasonableness of experimental methods and technical path",
            "safety_considerations": "Safety considerations - biosafety and clinical safety assessment",
            "regulatory_compliance": "Regulatory compliance - development and approval requirements",
            "innovation_potential": "Innovation potential - technical breakthroughs and application outlook",
        }

    def get_evaluation_method_name(self) -> str:
        return "phage_domain_specific"

    def evaluate_phage_content(
        self, content: str, task_context: Dict[str, Any], iteration: int = 0
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
                terminology_analysis, accuracy_assessment, clinical_evaluation, safety_assessment, llm_phage_evaluation
            )

            # Generate suggestions
            phage_suggestions = self._generate_phage_suggestions(
                content,
                terminology_analysis,
                accuracy_assessment,
                clinical_evaluation,
                safety_assessment,
                llm_phage_evaluation,
            )

            overall_score = self._calculate_phage_overall_score(phage_dimensions)

            # Additional metadata
            additional_metadata = {
                "terminology_coverage": terminology_analysis["coverage_score"],
                "scientific_accuracy": accuracy_assessment["accuracy_score"],
                "clinical_relevance": clinical_evaluation["relevance_score"],
                "safety_score": safety_assessment["safety_score"],
                "phage_expertise_level": llm_phage_evaluation.get("expertise_level", "intermediate"),
                "domain_focus": "bacteriophage_research",
            }

            return self.create_evaluation_result(
                overall_score=overall_score,
                dimensions=phage_dimensions,
                suggestions=phage_suggestions,
                iteration=iteration,
                additional_metadata=additional_metadata,
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
                "coverage": category_found / category_total if category_total > 0 else 0.0,
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
            "terminology_depth": (
                "high" if sophistication_score > 0.7 else "medium" if sophistication_score > 0.4 else "low"
            ),
        }

    def _assess_scientific_accuracy(self, content: str, task_context: Dict[str, Any]) -> Dict[str, Any]:
        """Assess scientific accuracy of phage-related content"""

        accuracy_indicators = {"mechanism_accuracy": 0.0, "factual_correctness": 0.0, "conceptual_clarity": 0.0}

        content_lower = content.lower()

        # Check for common phage biology concepts
        phage_concepts = {
            "lytic_cycle": ["lytic cycle", "lysis", "lytic"],
            "lysogenic_cycle": ["lysogenic cycle", "lysogenic"],
            "host_specificity": ["host specificity", "specificity"],
            "resistance_mechanisms": ["resistance mechanism", "resistance"],
        }

        concept_coverage = 0
        for concept, keywords in phage_concepts.items():
            if any(keyword in content_lower for keyword in keywords):
                concept_coverage += 1

        accuracy_indicators["mechanism_accuracy"] = concept_coverage / len(phage_concepts)

        # Check for potential inaccuracies (simple heuristics)
        inaccuracy_flags = [
            "phages are bacteria",  # Phages are not bacteria
            "phages infect humans directly",  # Most phages do not directly infect humans
            "all phages are harmful",  # Many phages are beneficial
        ]

        inaccuracy_count = sum(1 for flag in inaccuracy_flags if flag in content)
        accuracy_indicators["factual_correctness"] = max(0.0, 1.0 - (inaccuracy_count * 0.3))

        # Assess conceptual clarity
        clarity_indicators = [
            "mechanism" in content_lower,
            "principle" in content_lower,
            "process" in content_lower,
            "step" in content_lower,
        ]

        accuracy_indicators["conceptual_clarity"] = sum(clarity_indicators) / len(clarity_indicators)

        overall_accuracy = sum(accuracy_indicators.values()) / len(accuracy_indicators)

        return {
            "accuracy_score": overall_accuracy,
            "accuracy_indicators": accuracy_indicators,
            "concept_coverage": concept_coverage,
            "inaccuracy_flags": inaccuracy_count,
        }

    def _evaluate_clinical_relevance(self, content: str, task_context: Dict[str, Any]) -> Dict[str, Any]:
        """Evaluate clinical relevance and therapeutic potential"""

        content_lower = content.lower()

        clinical_indicators = {
            "therapeutic_application": 0.0,
            "safety_awareness": 0.0,
            "clinical_feasibility": 0.0,
            "patient_benefit": 0.0,
        }

        # Therapeutic application indicators
        therapeutic_terms = [
            "therapy",
            "treatment",
            "clinical",
            "patient",
            "disease",
            "infection",
        ]
        therapeutic_score = sum(1 for term in therapeutic_terms if term in content_lower)
        clinical_indicators["therapeutic_application"] = min(1.0, therapeutic_score / 5)

        # Safety awareness indicators
        safety_terms = [
            "safety",
            "side effect",
            "risk",
            "toxicity",
            "immune",
            "allergy",
        ]
        safety_score = sum(1 for term in safety_terms if term in content_lower)
        clinical_indicators["safety_awareness"] = min(1.0, safety_score / 4)

        # Clinical feasibility indicators
        feasibility_terms = [
            "dose",
            "administration",
            "preparation",
            "storage",
            "stability",
            "quality control",
        ]
        feasibility_score = sum(1 for term in feasibility_terms if term in content_lower)
        clinical_indicators["clinical_feasibility"] = min(1.0, feasibility_score / 4)

        # Patient benefit indicators
        benefit_terms = [
            "efficacy",
            "effective",
            "improvement",
            "recovery",
            "cure",
            "relief",
        ]
        benefit_score = sum(1 for term in benefit_terms if term in content_lower)
        clinical_indicators["patient_benefit"] = min(1.0, benefit_score / 3)

        overall_relevance = sum(clinical_indicators.values()) / len(clinical_indicators)

        return {
            "relevance_score": overall_relevance,
            "clinical_indicators": clinical_indicators,
            "therapeutic_focus": therapeutic_score > 2,
            "safety_conscious": safety_score > 1,
        }

    def _assess_safety_and_regulatory(self, content: str, task_context: Dict[str, Any]) -> Dict[str, Any]:
        """Assess safety considerations and regulatory compliance awareness"""

        content_lower = content.lower()

        safety_aspects = {
            "biosafety_awareness": 0.0,
            "regulatory_knowledge": 0.0,
            "risk_assessment": 0.0,
            "quality_control": 0.0,
        }

        # Biosafety awareness
        biosafety_terms = [
            "biosafety",
            "biocontainment",
            "laboratory safety",
            "protection",
        ]
        biosafety_score = sum(1 for term in biosafety_terms if term in content_lower)
        safety_aspects["biosafety_awareness"] = min(1.0, biosafety_score / 3)

        # Regulatory knowledge
        regulatory_terms = [
            "FDA",
            "NMPA",
            "approval",
            "regulation",
            "clinical trial",
            "GMP",
            "quality standard",
        ]
        regulatory_score = sum(1 for term in regulatory_terms if term in content_lower)
        safety_aspects["regulatory_knowledge"] = min(1.0, regulatory_score / 4)

        # Risk assessment
        risk_terms = [
            "risk assessment",
            "risk",
            "hazard",
            "adverse reaction",
            "contraindication",
        ]
        risk_score = sum(1 for term in risk_terms if term in content_lower)
        safety_aspects["risk_assessment"] = min(1.0, risk_score / 3)

        # Quality control
        qc_terms = [
            "quality control",
            "QC",
            "standardization",
            "purity",
            "activity",
            "stability",
        ]
        qc_score = sum(1 for term in qc_terms if term in content_lower)
        safety_aspects["quality_control"] = min(1.0, qc_score / 3)

        overall_safety = sum(safety_aspects.values()) / len(safety_aspects)

        return {
            "safety_score": overall_safety,
            "safety_aspects": safety_aspects,
            "regulatory_aware": regulatory_score > 1,
            "safety_conscious": biosafety_score > 0 or risk_score > 0,
        }

    def _llm_phage_evaluate(self, content: str, task_context: Dict[str, Any]) -> Dict[str, Any]:
        """Use LLM for phage domain expert evaluation"""

        evaluation_aspects = [
            "**Scientific Accuracy**: Are phage biology concepts and mechanisms accurate?",
            "**Clinical Relevance**: How relevant is the content to phage therapy applications?",
            "**Technical Feasibility**: Are proposed methods feasible under current constraints?",
            "**Safety Considerations**: Are biosafety and clinical safety addressed adequately?",
            "**Innovation Potential**: Does the content demonstrate novelty and frontier value?",
            "**Professional Depth**: How strong is the technical and domain depth?",
        ]

        specific_instructions = """
You are a senior expert in phage research. Evaluate the content from a professional perspective.

Return the result in JSON format:
{
    "scientific_accuracy": 0.8,
    "clinical_relevance": 0.7,
    "technical_feasibility": 0.9,
    "safety_considerations": 0.8,
    "innovation_potential": 0.6,
    "professional_depth": 0.7,
    "overall_expert_score": 0.75,
    "expertise_level": "advanced",
    "key_strengths": ["Strength 1", "Strength 2"],
    "technical_concerns": ["Concern 1", "Concern 2"],
    "improvement_suggestions": ["Suggestion 1", "Suggestion 2", "Suggestion 3"],
    "phage_specific_insights": ["Insight 1", "Insight 2"],
    "confidence_level": 0.9
}
"""

        phage_expert_prompt = self.build_evaluation_prompt_template(
            content, task_context, evaluation_aspects, specific_instructions, 800
        )

        required_fields = ["scientific_accuracy", "clinical_relevance", "technical_feasibility"]
        result = self.call_llm_with_json_parsing(phage_expert_prompt, required_fields)

        if result:
            return result
        raise RuntimeError(
            "LLM phage expert evaluation failed: no valid structured payload was produced."
        )

    def _fallback_phage_llm_evaluation(self) -> Dict[str, Any]:
        """Fallback phage expert scoring is disabled by policy."""
        raise RuntimeError(
            "Fallback phage evaluation is disabled. Use strict LLM expert output only."
        )

    def _calculate_phage_dimensions(
        self,
        terminology_analysis: Dict[str, Any],
        accuracy_assessment: Dict[str, Any],
        clinical_evaluation: Dict[str, Any],
        safety_assessment: Dict[str, Any],
        llm_phage_evaluation: Dict[str, Any],
    ) -> EvaluationDimensions:
        """Calculate phage-specific evaluation dimensions"""

        # Map phage-specific assessments to standard dimensions
        relevance = (
            terminology_analysis["coverage_score"] * 0.3
            + clinical_evaluation["relevance_score"] * 0.4
            + llm_phage_evaluation.get("clinical_relevance", 0.5) * 0.3
        )

        completeness = (
            terminology_analysis["sophistication_score"] * 0.3
            + accuracy_assessment["accuracy_score"] * 0.4
            + llm_phage_evaluation.get("professional_depth", 0.5) * 0.3
        )

        accuracy = (
            accuracy_assessment["accuracy_score"] * 0.5 + llm_phage_evaluation.get("scientific_accuracy", 0.5) * 0.5
        )

        clarity = llm_phage_evaluation.get("professional_depth", 0.5)

        coherence = (
            accuracy_assessment["accuracy_score"] * 0.4 + llm_phage_evaluation.get("technical_feasibility", 0.5) * 0.6
        )

        scientific_rigor = (
            safety_assessment["safety_score"] * 0.4
            + llm_phage_evaluation.get("safety_considerations", 0.5) * 0.3
            + llm_phage_evaluation.get("scientific_accuracy", 0.5) * 0.3
        )

        return EvaluationDimensions(
            relevance=min(1.0, max(0.0, relevance)),
            completeness=min(1.0, max(0.0, completeness)),
            accuracy=min(1.0, max(0.0, accuracy)),
            clarity=min(1.0, max(0.0, clarity)),
            coherence=min(1.0, max(0.0, coherence)),
            scientific_rigor=min(1.0, max(0.0, scientific_rigor)),
        )

    def _generate_phage_suggestions(
        self,
        content: str,
        terminology_analysis: Dict[str, Any],
        accuracy_assessment: Dict[str, Any],
        clinical_evaluation: Dict[str, Any],
        safety_assessment: Dict[str, Any],
        llm_phage_evaluation: Dict[str, Any],
    ) -> List[str]:
        """Generate phage-specific improvement suggestions"""

        suggestions = []

        # Terminology suggestions
        if terminology_analysis["coverage_score"] < 0.3:
            suggestions.append("Add more phage-specific terminology to improve technical precision.")

        if terminology_analysis["sophistication_score"] < 0.5:
            suggestions.append("Use more technical and molecular-biology terms to increase depth.")

        # Accuracy suggestions
        if accuracy_assessment["accuracy_score"] < 0.7:
            suggestions.append("Verify phage-biological mechanisms for scientific correctness.")

        if accuracy_assessment["concept_coverage"] < 2:
            suggestions.append("Add core concepts such as phage life cycle and host specificity.")

        # Clinical relevance suggestions
        if clinical_evaluation["relevance_score"] < 0.6:
            suggestions.append("Increase clinically relevant content for phage-therapy applications.")

        if not clinical_evaluation["safety_conscious"]:
            suggestions.append("Add discussion of clinical safety and side effects.")

        # Safety and regulatory suggestions
        if safety_assessment["safety_score"] < 0.5:
            suggestions.append("Strengthen biosafety and regulatory-compliance coverage.")

        if not safety_assessment["regulatory_aware"]:
            suggestions.append("Include drug approval and quality-control requirements.")

        # LLM expert suggestions
        llm_suggestions = llm_phage_evaluation.get("improvement_suggestions", [])
        suggestions.extend(llm_suggestions[:3])  # Add top 3 LLM suggestions

        # Innovation suggestions
        innovation_score = llm_phage_evaluation.get("innovation_potential", 0.5)
        if innovation_score < 0.6:
            suggestions.append("Add recent phage-research advances and innovative applications.")

        return suggestions[:8]  # Limit to top 8 suggestions

    def _calculate_phage_overall_score(self, dimensions: EvaluationDimensions) -> float:
        """Calculate overall score with phage-specific weights"""

        # Phage-specific dimension weights
        phage_weights = {
            "relevance": 0.20,  # Clinical relevance is important
            "completeness": 0.15,  # Professional completeness
            "accuracy": 0.25,  # Scientific accuracy is critical
            "clarity": 0.10,  # Professional clarity
            "coherence": 0.15,  # Technical coherence
            "scientific_rigor": 0.15,  # Safety and rigor are crucial
        }

        return self.calculate_weighted_score(dimensions, phage_weights)


def get_phage_evaluator(config: Optional[EvaluationConfig] = None) -> PhageEvaluator:
    """Factory function to get phage evaluator instance"""
    return PhageEvaluator(config)
