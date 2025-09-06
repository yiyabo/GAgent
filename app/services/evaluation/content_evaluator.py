"""
Content Evaluator Service.

Provides content quality evaluation and improvement suggestions for generated text.
Supports both generic evaluation and domain-specific assessment.
"""

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from ...llm import get_default_client
from ...models import EvaluationConfig, EvaluationDimensions, EvaluationResult

logger = logging.getLogger(__name__)


class ContentEvaluator:
    """Base content evaluator for generic content assessment"""

    def __init__(self, config: Optional[EvaluationConfig] = None):
        self.config = config or EvaluationConfig()
        self.llm_client = get_default_client()

    def evaluate_content(self, content: str, task_context: Dict[str, Any], iteration: int = 0) -> EvaluationResult:
        """
        Evaluate content quality across multiple dimensions

        Args:
            content: The generated content to evaluate
            task_context: Context about the task (name, type, etc.)
            iteration: Current iteration number

        Returns:
            EvaluationResult with scores and suggestions
        """
        try:
            # Basic content checks
            if not content or not content.strip():
                return self._create_empty_content_result(iteration)

            # Evaluate each dimension
            dimensions = self._evaluate_dimensions(content, task_context)

            # Calculate overall score
            overall_score = self._calculate_overall_score(dimensions)

            # Generate improvement suggestions
            suggestions = self._generate_suggestions(content, dimensions, task_context)

            # Determine if revision is needed
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
                },
            )

        except Exception as e:
            logger.error(f"Error evaluating content: {e}")
            return self._create_error_result(iteration, str(e))

    def _evaluate_dimensions(self, content: str, task_context: Dict[str, Any]) -> EvaluationDimensions:
        """Evaluate individual quality dimensions"""

        return EvaluationDimensions(
            relevance=self._evaluate_relevance(content, task_context),
            completeness=self._evaluate_completeness(content, task_context),
            accuracy=self._evaluate_accuracy(content, task_context),
            clarity=self._evaluate_clarity(content),
            coherence=self._evaluate_coherence(content),
            scientific_rigor=self._evaluate_scientific_rigor(content),
        )

    def _evaluate_relevance(self, content: str, task_context: Dict[str, Any]) -> float:
        """Evaluate how relevant the content is to the task"""
        task_name = task_context.get("name", "")

        if not task_name:
            return 0.5  # Neutral score if no task context

        # Simple keyword matching approach
        task_keywords = self._extract_keywords(task_name.lower())
        content_lower = content.lower()

        matches = sum(1 for keyword in task_keywords if keyword in content_lower)
        relevance_score = min(matches / max(len(task_keywords), 1), 1.0)

        # Boost score if content length is appropriate
        word_count = len(content.split())
        if 100 <= word_count <= 500:  # Reasonable length
            relevance_score = min(relevance_score + 0.1, 1.0)

        return relevance_score

    def _evaluate_completeness(self, content: str, task_context: Dict[str, Any]) -> float:
        """Evaluate content completeness"""
        word_count = len(content.split())

        # Score based on content length and structure
        length_score = min(word_count / 200, 1.0)  # Target ~200 words

        # Check for structural elements
        structure_score = 0.0
        if self._has_introduction(content):
            structure_score += 0.3
        if self._has_main_content(content):
            structure_score += 0.4
        if self._has_conclusion(content):
            structure_score += 0.3

        return length_score * 0.6 + structure_score * 0.4

    def _evaluate_accuracy(self, content: str, task_context: Dict[str, Any]) -> float:
        """Evaluate content accuracy (basic checks)"""
        # Basic accuracy indicators
        score = 0.8  # Default baseline

        # Penalize obviously incorrect statements
        if self._has_contradictions(content):
            score -= 0.2

        # Reward specific, detailed information
        if self._has_specific_details(content):
            score += 0.1

        # Penalize vague or generic content
        if self._is_too_generic(content):
            score -= 0.1

        return max(0.0, min(score, 1.0))

    def _evaluate_clarity(self, content: str) -> float:
        """Evaluate content clarity and readability"""
        sentences = self._split_sentences(content)

        if not sentences:
            return 0.0

        # Average sentence length (shorter is generally clearer)
        avg_sentence_length = sum(len(s.split()) for s in sentences) / len(sentences)
        length_score = max(0, 1.0 - (avg_sentence_length - 15) / 20)  # Optimal ~15 words

        # Check for clear structure
        structure_score = 0.8  # Default
        if self._has_clear_paragraphs(content):
            structure_score += 0.1
        if self._has_transition_words(content):
            structure_score += 0.1

        return length_score * 0.5 + structure_score * 0.5

    def _evaluate_coherence(self, content: str) -> float:
        """Evaluate logical coherence and flow"""
        paragraphs = content.split("\n\n")

        if len(paragraphs) < 2:
            return 0.6  # Single paragraph gets neutral score

        coherence_score = 0.7  # Baseline

        # Check for logical flow between paragraphs
        if self._has_logical_flow(paragraphs):
            coherence_score += 0.2

        # Check for consistent terminology
        if self._has_consistent_terminology(content):
            coherence_score += 0.1

        return min(coherence_score, 1.0)

    def _evaluate_scientific_rigor(self, content: str) -> float:
        """Evaluate scientific accuracy and methodology"""
        rigor_score = 0.5  # Neutral baseline

        # Check for scientific terminology
        if self._has_scientific_terms(content):
            rigor_score += 0.2

        # Check for methodological language
        if self._has_methodological_language(content):
            rigor_score += 0.2

        # Check for quantitative information
        if self._has_quantitative_info(content):
            rigor_score += 0.1

        return min(rigor_score, 1.0)

    def _calculate_overall_score(self, dimensions: EvaluationDimensions) -> float:
        """Calculate weighted overall score"""
        if self.config.custom_weights:
            weights = self.config.custom_weights
        else:
            # Default weights
            weights = {
                "relevance": 0.25,
                "completeness": 0.20,
                "accuracy": 0.20,
                "clarity": 0.15,
                "coherence": 0.15,
                "scientific_rigor": 0.05,
            }

        total_score = 0.0
        total_weight = 0.0

        for dim_name in self.config.evaluation_dimensions:
            if hasattr(dimensions, dim_name) and dim_name in weights:
                score = getattr(dimensions, dim_name)
                weight = weights[dim_name]
                total_score += score * weight
                total_weight += weight

        return total_score / total_weight if total_weight > 0 else 0.0

    def _generate_suggestions(
        self, content: str, dimensions: EvaluationDimensions, task_context: Dict[str, Any]
    ) -> List[str]:
        """Generate specific improvement suggestions"""
        suggestions = []

        if dimensions.relevance < 0.7:
            suggestions.append(f"Content should be more relevant to the task: '{task_context.get('name', '')}'")

        if dimensions.completeness < 0.7:
            word_count = len(content.split())
            if word_count < 100:
                suggestions.append("Content is too brief - add more detail and explanation")
            elif not self._has_introduction(content):
                suggestions.append("Add a clear introduction to the topic")
            elif not self._has_conclusion(content):
                suggestions.append("Include a summary or conclusion")

        if dimensions.accuracy < 0.7:
            suggestions.append("Verify factual accuracy and add specific details")

        if dimensions.clarity < 0.7:
            suggestions.append("Improve clarity by using shorter sentences and clearer language")

        if dimensions.coherence < 0.7:
            suggestions.append("Improve logical flow and connections between ideas")

        if dimensions.scientific_rigor < 0.7 and self.config.domain_specific:
            suggestions.append("Enhance scientific rigor with proper methodology and terminology")

        return suggestions

    # Helper methods for content analysis
    def _extract_keywords(self, text: str) -> List[str]:
        """Extract important keywords from text"""
        # Simple approach: split and filter common words
        common_words = {"the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "of", "with", "by"}
        words = re.findall(r"\b\w+\b", text.lower())
        return [w for w in words if len(w) > 3 and w not in common_words]

    def _split_sentences(self, content: str) -> List[str]:
        """Split content into sentences"""
        return [s.strip() for s in re.split(r"[.!?]+", content) if s.strip()]

    def _has_introduction(self, content: str) -> bool:
        """Check if content has an introduction"""
        first_paragraph = content.split("\n\n")[0]
        intro_indicators = ["introduction", "overview", "background", "purpose", "objective"]
        return any(indicator in first_paragraph.lower() for indicator in intro_indicators)

    def _has_main_content(self, content: str) -> bool:
        """Check if content has substantial main content"""
        word_count = len(content.split())
        return word_count >= 100  # At least 100 words

    def _has_conclusion(self, content: str) -> bool:
        """Check if content has a conclusion"""
        last_paragraph = content.split("\n\n")[-1]
        conclusion_indicators = ["conclusion", "summary", "therefore", "in summary", "finally"]
        return any(indicator in last_paragraph.lower() for indicator in conclusion_indicators)

    def _has_contradictions(self, content: str) -> bool:
        """Basic check for obvious contradictions"""
        # Simple implementation - could be enhanced with NLP
        contradiction_patterns = [(r"\bnot\b.*\bis\b", r"\bis\b.*\bnot\b"), (r"\bno\b.*\byes\b", r"\byes\b.*\bno\b")]

        content_lower = content.lower()
        for pattern1, pattern2 in contradiction_patterns:
            if re.search(pattern1, content_lower) and re.search(pattern2, content_lower):
                return True
        return False

    def _has_specific_details(self, content: str) -> bool:
        """Check for specific, detailed information"""
        # Look for numbers, specific terms, proper nouns
        has_numbers = bool(re.search(r"\d+", content))
        has_proper_nouns = bool(re.search(r"\b[A-Z][a-z]+\b", content))
        has_technical_terms = len(self._extract_keywords(content)) > 5

        return has_numbers or has_proper_nouns or has_technical_terms

    def _is_too_generic(self, content: str) -> bool:
        """Check if content is too generic or vague"""
        generic_phrases = [
            "in general",
            "usually",
            "often",
            "sometimes",
            "many",
            "various",
            "it depends",
            "may be",
            "could be",
            "might be",
        ]
        content_lower = content.lower()
        generic_count = sum(1 for phrase in generic_phrases if phrase in content_lower)

        return generic_count > 3  # Too many generic phrases

    def _has_clear_paragraphs(self, content: str) -> bool:
        """Check for clear paragraph structure"""
        paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
        return len(paragraphs) >= 2 and all(len(p.split()) >= 30 for p in paragraphs)

    def _has_transition_words(self, content: str) -> bool:
        """Check for transition words and phrases"""
        transitions = [
            "however",
            "furthermore",
            "moreover",
            "therefore",
            "consequently",
            "additionally",
            "meanwhile",
            "nevertheless",
            "thus",
            "hence",
        ]
        content_lower = content.lower()
        return any(transition in content_lower for transition in transitions)

    def _has_logical_flow(self, paragraphs: List[str]) -> bool:
        """Basic check for logical flow between paragraphs"""
        # Simple heuristic: each paragraph should have some thematic connection
        if len(paragraphs) < 2:
            return True

        # Check if paragraphs share keywords
        for i in range(len(paragraphs) - 1):
            current_keywords = set(self._extract_keywords(paragraphs[i]))
            next_keywords = set(self._extract_keywords(paragraphs[i + 1]))

            if len(current_keywords & next_keywords) == 0:
                return False  # No shared keywords indicates poor flow

        return True

    def _has_consistent_terminology(self, content: str) -> bool:
        """Check for consistent use of terminology"""
        # Simple check: same concepts should use same terms
        keywords = self._extract_keywords(content)
        keyword_counts = {}

        for keyword in keywords:
            keyword_counts[keyword] = keyword_counts.get(keyword, 0) + 1

        # Consistent if important terms are repeated
        frequent_terms = [k for k, v in keyword_counts.items() if v >= 2]
        return len(frequent_terms) >= 2

    def _has_scientific_terms(self, content: str) -> bool:
        """Check for scientific terminology"""
        scientific_indicators = [
            "study",
            "research",
            "analysis",
            "method",
            "result",
            "data",
            "experiment",
            "hypothesis",
            "theory",
            "observation",
            "measurement",
        ]
        content_lower = content.lower()
        return any(term in content_lower for term in scientific_indicators)

    def _has_methodological_language(self, content: str) -> bool:
        """Check for methodological language"""
        method_indicators = [
            "protocol",
            "procedure",
            "approach",
            "technique",
            "methodology",
            "conducted",
            "performed",
            "measured",
            "analyzed",
            "evaluated",
        ]
        content_lower = content.lower()
        return any(term in content_lower for term in method_indicators)

    def _has_quantitative_info(self, content: str) -> bool:
        """Check for quantitative information"""
        # Look for numbers, percentages, units
        has_numbers = bool(re.search(r"\d+", content))
        has_percentages = bool(re.search(r"\d+%", content))
        has_units = bool(re.search(r"\d+\s*(mg|ml|kg|μg|nm|μm|bp|kDa)", content))

        return has_numbers or has_percentages or has_units

    def _create_empty_content_result(self, iteration: int) -> EvaluationResult:
        """Create result for empty content"""
        return EvaluationResult(
            overall_score=0.0,
            dimensions=EvaluationDimensions(),
            suggestions=["Content is empty - please provide substantial content"],
            needs_revision=True,
            iteration=iteration,
            timestamp=datetime.now(),
        )

    def _create_error_result(self, iteration: int, error_msg: str) -> EvaluationResult:
        """Create result for evaluation errors"""
        return EvaluationResult(
            overall_score=0.0,
            dimensions=EvaluationDimensions(),
            suggestions=[f"Evaluation error: {error_msg}"],
            needs_revision=True,
            iteration=iteration,
            timestamp=datetime.now(),
            metadata={"error": error_msg},
        )


def get_evaluator(config: Optional[EvaluationConfig] = None) -> ContentEvaluator:
    """Factory function to get content evaluator instance"""
    return ContentEvaluator(config)
