"""
Base Evaluator Class

Provides common functionality for all evaluators to eliminate code duplication.
"""

import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from ...llm import get_default_client
from ...models import EvaluationConfig, EvaluationDimensions, EvaluationResult

logger = logging.getLogger(__name__)


class BaseEvaluator(ABC):
    """Base class for all evaluators with common functionality"""

    def __init__(self, config: Optional[EvaluationConfig] = None):
        self.config = config or EvaluationConfig()
        self.llm_client = get_default_client()
        self.evaluator_name = self.__class__.__name__

    @abstractmethod
    def get_evaluation_method_name(self) -> str:
        """Return the evaluation method name for metadata"""
        pass

    def call_llm_with_json_parsing(
        self, prompt: str, required_fields: Optional[List[str]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Common LLM calling and JSON parsing logic

        Args:
            prompt: The prompt to send to LLM
            required_fields: List of required fields in JSON response

        Returns:
            Parsed JSON dict or None if parsing failed
        """
        try:
            response = self.llm_client.chat([{"role": "user", "content": prompt}])

            # Handle both dict and string responses
            if isinstance(response, dict):
                result_text = response.get("content", "").strip()
            else:
                result_text = str(response).strip()

            return self._parse_json_response(result_text, required_fields)

        except Exception as e:
            logger.error(f"LLM call failed in {self.evaluator_name}: {e}")
            return None

    def _parse_json_response(
        self, result_text: str, required_fields: Optional[List[str]] = None
    ) -> Optional[Dict[str, Any]]:
        """Parse JSON from LLM response text"""

        # Extract JSON block
        json_start = result_text.find("{")
        json_end = result_text.rfind("}") + 1

        if json_start >= 0 and json_end > json_start:
            json_text = result_text[json_start:json_end]

            try:
                parsed_data = json.loads(json_text)

                # Validate required fields if specified
                if required_fields:
                    missing_fields = [field for field in required_fields if field not in parsed_data]
                    if missing_fields:
                        logger.warning(f"Missing required fields in {self.evaluator_name}: {missing_fields}")
                        return None

                return parsed_data

            except json.JSONDecodeError as e:
                logger.error(f"JSON parsing error in {self.evaluator_name}: {e}")
                return None
        else:
            logger.warning(f"No JSON block found in {self.evaluator_name} response")
            return None

    def calculate_weighted_score(
        self, dimensions: EvaluationDimensions, weights: Optional[Dict[str, float]] = None
    ) -> float:
        """Calculate weighted overall score from dimensions"""

        if weights is None:
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

    def create_evaluation_result(
        self,
        overall_score: float,
        dimensions: EvaluationDimensions,
        suggestions: List[str],
        iteration: int,
        additional_metadata: Optional[Dict[str, Any]] = None,
    ) -> EvaluationResult:
        """Create standardized evaluation result"""

        needs_revision = overall_score < self.config.quality_threshold

        metadata = {
            "evaluation_method": self.get_evaluation_method_name(),
            "evaluator_class": self.evaluator_name,
            "timestamp": datetime.now().isoformat(),
        }

        if additional_metadata:
            metadata.update(additional_metadata)

        return EvaluationResult(
            overall_score=overall_score,
            dimensions=dimensions,
            suggestions=suggestions,
            needs_revision=needs_revision,
            iteration=iteration,
            timestamp=datetime.now(),
            metadata=metadata,
        )

    def create_empty_content_result(self, iteration: int) -> EvaluationResult:
        """Create standardized result for empty content"""
        return EvaluationResult(
            overall_score=0.0,
            dimensions=EvaluationDimensions(),
            suggestions=["内容为空，请提供实质性内容"],
            needs_revision=True,
            iteration=iteration,
            timestamp=datetime.now(),
            metadata={
                "evaluation_method": self.get_evaluation_method_name(),
                "evaluator_class": self.evaluator_name,
                "error": "empty_content",
            },
        )

    def create_error_result(self, iteration: int, error_msg: str) -> EvaluationResult:
        """Create standardized result for evaluation errors"""
        return EvaluationResult(
            overall_score=0.0,
            dimensions=EvaluationDimensions(),
            suggestions=[f"评估出错: {error_msg}"],
            needs_revision=True,
            iteration=iteration,
            timestamp=datetime.now(),
            metadata={
                "evaluation_method": self.get_evaluation_method_name(),
                "evaluator_class": self.evaluator_name,
                "error": error_msg,
            },
        )

    def validate_content(self, content: str) -> bool:
        """Validate content is not empty"""
        return content and content.strip()

    def extract_task_info(self, task_context: Dict[str, Any]) -> Tuple[str, str]:
        """Extract common task information"""
        task_name = task_context.get("name", "")
        task_type = task_context.get("task_type", "")
        return task_name, task_type

    def calculate_basic_metrics(self, content: str) -> Dict[str, Any]:
        """Calculate basic content metrics"""
        word_count = len(content.split())
        char_count = len(content)
        paragraph_count = content.count("\n\n") + 1

        return {
            "word_count": word_count,
            "char_count": char_count,
            "paragraph_count": paragraph_count,
            "avg_words_per_paragraph": word_count / paragraph_count if paragraph_count > 0 else 0,
        }

    def generate_fallback_dimensions(self, content: str, base_score: float = 0.7) -> EvaluationDimensions:
        """Generate fallback dimensions when LLM is unavailable"""

        metrics = self.calculate_basic_metrics(content)
        word_count = metrics["word_count"]

        # Improved length-based adjustments
        if word_count >= 50:  # Reasonable content length
            length_factor = min(word_count / 150, 1.2)  # Allow slight bonus for longer content
        else:
            length_factor = word_count / 50  # Penalty for very short content

        # More generous fallback scores for testing
        return EvaluationDimensions(
            relevance=max(0.5, base_score * length_factor),
            completeness=max(0.4, (base_score - 0.1) * length_factor),
            accuracy=base_score * 0.95,  # Less conservative
            clarity=max(0.5, base_score * (0.85 + 0.15 * length_factor)),
            coherence=base_score * 0.9,
            scientific_rigor=base_score * 0.8,
        )

    def build_evaluation_prompt_template(
        self,
        content: str,
        task_context: Dict[str, Any],
        evaluation_aspects: List[str],
        specific_instructions: str = "",
        max_content_length: int = 800,
    ) -> str:
        """Build standardized evaluation prompt template"""

        task_name, task_type = self.extract_task_info(task_context)
        content_preview = content[:max_content_length]
        if len(content) > max_content_length:
            content_preview += "..."

        aspects_text = "\n".join([f"{i+1}. {aspect}" for i, aspect in enumerate(evaluation_aspects)])

        return f"""
作为专业的内容质量评估专家，请对以下内容进行评估。

任务背景："{task_name}"
任务类型：{task_type}

需要评估的内容：
```
{content_preview}
```

请从以下维度评估内容质量：
{aspects_text}

{specific_instructions}

请以JSON格式返回评估结果。
"""


class LLMBasedEvaluator(BaseEvaluator):
    """Base class for LLM-based evaluators with common LLM interaction patterns"""

    def generate_improvement_suggestions(
        self,
        content: str,
        low_scoring_dimensions: List[Tuple[str, float]],
        task_context: Dict[str, Any],
        max_suggestions: int = 5,
    ) -> List[str]:
        """Generate improvement suggestions using LLM"""

        if not low_scoring_dimensions:
            return ["内容质量良好，无需重大修改"]

        task_name, _ = self.extract_task_info(task_context)

        low_scores_text = ", ".join([f"{dim}: {score:.2f}" for dim, score in low_scoring_dimensions])

        suggestion_prompt = f"""
作为内容改进专家，请为以下内容提供具体的改进建议。

任务："{task_name}"

内容片段：
```
{content[:500]}
```

需要改进的维度：
{low_scores_text}

请提供{max_suggestions}个具体、可操作的改进建议，每个建议要明确指出：
1. 需要改进的具体问题
2. 具体的改进方法

请以简洁的列表形式返回建议。
"""

        try:
            response = self.llm_client.chat([{"role": "user", "content": suggestion_prompt}])

            # Handle both dict and string responses
            if isinstance(response, dict):
                suggestions_text = response.get("content", "").strip()
            else:
                suggestions_text = str(response).strip()

            # Parse suggestions from response
            suggestions = []
            for line in suggestions_text.split("\n"):
                line = line.strip()
                if line and (line.startswith("-") or line.startswith("•") or line[0].isdigit()):
                    clean_line = line.lstrip("-•0123456789. ").strip()
                    if clean_line:
                        suggestions.append(clean_line)

            return suggestions[:max_suggestions] if suggestions else [f"建议改进评分较低的维度: {low_scores_text}"]

        except Exception as e:
            logger.error(f"Suggestion generation failed in {self.evaluator_name}: {e}")
            return [f"建议改进评分较低的维度: {low_scores_text}"]
