"""
LLM-based Intelligent Content Evaluator

Replaces rule-based evaluation with intelligent LLM assessment
"""

import json
import logging
from typing import Any, Dict, List, Optional

from ..models import EvaluationConfig, EvaluationDimensions, EvaluationResult
from ..prompts import prompt_manager
from .base_evaluator import LLMBasedEvaluator
from .llm_cache import get_llm_cache

logger = logging.getLogger(__name__)


class LLMEvaluator(LLMBasedEvaluator):
    """LLM-powered content evaluator with English prompts and caching"""

    def __init__(self, config: Optional[EvaluationConfig] = None, use_cache: bool = True):
        """Initialize with caching support."""
        super().__init__(config)
        self.cache = get_llm_cache() if use_cache else None

    def get_evaluation_method_name(self) -> str:
        return "llm_intelligent"

    def evaluate_content_intelligent(
        self, content: str, task_context: Dict[str, Any], iteration: int = 0
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
                "word_count": metrics["word_count"],
            }

            return self.create_evaluation_result(
                overall_score=overall_score,
                dimensions=dimensions,
                suggestions=suggestions,
                iteration=iteration,
                additional_metadata=additional_metadata,
            )

        except Exception as e:
            logger.error(f"Error in LLM evaluation: {e}")
            return self.create_error_result(iteration, str(e))

    def _llm_evaluate_dimensions(self, content: str, task_context: Dict[str, Any]) -> EvaluationDimensions:
        """Use LLM to evaluate content across all dimensions"""

        # Get English evaluation dimensions from prompt manager
        dimensions_config = prompt_manager.get_category("evaluation")["dimensions"]
        evaluation_aspects = []

        for dim_key, dim_data in dimensions_config.items():
            name = dim_data["name"]
            desc = dim_data["description"]
            evaluation_aspects.append(f"**{name} ({dim_key})**: {desc}")

        # Get English instructions from prompt manager
        json_instruction = prompt_manager.get("evaluation.instructions.json_format")
        explain_instruction = prompt_manager.get("evaluation.instructions.explain_scores")

        specific_instructions = f"""{json_instruction}
{{
    "relevance": 0.8,
    "completeness": 0.7,
    "accuracy": 0.9,
    "clarity": 0.8,
    "coherence": 0.8,
    "scientific_rigor": 0.7,
    "reasoning": "{explain_instruction}"
}}"""

        evaluation_prompt = self.build_evaluation_prompt_template(
            content, task_context, evaluation_aspects, specific_instructions, 1000
        )

        # Use cached LLM call if available
        cache_key = f"llm_eval_{hash(evaluation_prompt)}"
        if self.cache:
            cached_response = self.cache.get(evaluation_prompt, "llm_evaluation")
            if cached_response:
                logger.debug("Using cached LLM evaluation result")
                scores = self._parse_cached_response(cached_response)
            else:
                required_fields = ["relevance", "completeness", "accuracy", "clarity", "coherence", "scientific_rigor"]
                scores = self.call_llm_with_json_parsing(evaluation_prompt, required_fields)
                if scores:
                    # Cache the successful response
                    self.cache.set(
                        prompt=evaluation_prompt,
                        response=json.dumps(scores),
                        model="llm_evaluation",
                        temperature=0.0,
                        token_count=0,
                        cost=0,
                    )
        else:
            required_fields = ["relevance", "completeness", "accuracy", "clarity", "coherence", "scientific_rigor"]
            scores = self.call_llm_with_json_parsing(evaluation_prompt, required_fields)

        if scores:
            return EvaluationDimensions(
                relevance=float(scores.get("relevance", 0.5)),
                completeness=float(scores.get("completeness", 0.5)),
                accuracy=float(scores.get("accuracy", 0.5)),
                clarity=float(scores.get("clarity", 0.5)),
                coherence=float(scores.get("coherence", 0.5)),
                scientific_rigor=float(scores.get("scientific_rigor", 0.5)),
            )
        else:
            logger.warning("LLM evaluation failed, using fallback")
            return self.generate_fallback_dimensions(content)

    def _llm_generate_suggestions(
        self, content: str, dimensions: EvaluationDimensions, task_context: Dict[str, Any]
    ) -> List[str]:
        """Use LLM to generate intelligent improvement suggestions"""

        # Identify low-scoring dimensions
        low_scoring_dims = []
        threshold = 0.7

        # Use English dimension names from prompt manager
        dimensions_config = prompt_manager.get_category("evaluation")["dimensions"]

        if dimensions.relevance < threshold:
            low_scoring_dims.append((dimensions_config["relevance"]["name"], dimensions.relevance))
        if dimensions.completeness < threshold:
            low_scoring_dims.append((dimensions_config["completeness"]["name"], dimensions.completeness))
        if dimensions.accuracy < threshold:
            low_scoring_dims.append((dimensions_config["accuracy"]["name"], dimensions.accuracy))
        if dimensions.clarity < threshold:
            low_scoring_dims.append((dimensions_config["clarity"]["name"], dimensions.clarity))
        if dimensions.coherence < threshold:
            low_scoring_dims.append((dimensions_config["coherence"]["name"], dimensions.coherence))
        if dimensions.scientific_rigor < threshold:
            low_scoring_dims.append((dimensions_config["scientific_rigor"]["name"], dimensions.scientific_rigor))

        # Use base class method for suggestion generation
        return self.generate_improvement_suggestions(content, low_scoring_dims, task_context, 5)

    def _parse_cached_response(self, cached_response: Any) -> Optional[Dict[str, Any]]:
        """Parse cached LLM response."""
        if isinstance(cached_response, dict):
            return cached_response
        try:
            if isinstance(cached_response, str):
                return json.loads(cached_response)
        except json.JSONDecodeError:
            logger.warning("Failed to parse cached response, will regenerate")
        return None


def get_llm_evaluator(config: Optional[EvaluationConfig] = None) -> LLMEvaluator:
    """Factory function to get LLM evaluator instance"""
    return LLMEvaluator(config)
