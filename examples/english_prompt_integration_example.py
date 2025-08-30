"""
Example: Integrating English Prompt System with Existing Evaluators

This example shows how to migrate existing evaluators to use the new 
centralized English prompt management system.
"""

from app.prompts import prompt_manager
from app.services.llm_cache import get_llm_cache
from typing import Dict, Any, Optional
import json
import logging

logger = logging.getLogger(__name__)


class EnglishLLMEvaluator:
    """
    Example of LLM Evaluator using English prompts.
    
    This demonstrates how to refactor existing evaluators to use:
    1. Centralized English prompt management
    2. LLM response caching
    3. Cleaner, more maintainable code
    """
    
    def __init__(self, llm_client, use_cache: bool = True):
        """
        Initialize evaluator with English prompts.
        
        Args:
            llm_client: LLM client for API calls
            use_cache: Whether to use response caching
        """
        self.llm_client = llm_client
        self.cache = get_llm_cache() if use_cache else None
        
    def evaluate(self, content: str, task_background: str = "") -> Dict[str, Any]:
        """
        Evaluate content using English prompts.
        
        Args:
            content: Content to evaluate
            task_background: Background context for the task
            
        Returns:
            Evaluation results with scores and suggestions
        """
        try:
            # Build prompt using centralized English templates
            prompt = self._build_evaluation_prompt(content, task_background)
            
            # Use cached LLM call if available
            if self.cache:
                cache_key = f"eval_{hash(prompt)}"
                response = self.cache.get(cache_key, "evaluation")
                if response is None:
                    response = self._call_llm(prompt)
                    self.cache.set(cache_key, response, "evaluation", ttl=3600)
                    was_cached = False
                else:
                    was_cached = True
                if was_cached:
                    logger.debug("Used cached evaluation result")
            else:
                response = self._call_llm(prompt)
            
            # Parse and return results
            return self._parse_evaluation_response(response)
            
        except Exception as e:
            logger.error(f"Evaluation error: {e}")
            return self._fallback_evaluation(content)
    
    def _build_evaluation_prompt(self, content: str, task_background: str) -> str:
        """Build evaluation prompt using English templates."""
        
        # Get dimension descriptions
        dimensions = prompt_manager.get_category("evaluation")["dimensions"]
        dimension_descriptions = []
        
        for dim_key, dim_data in dimensions.items():
            name = dim_data["name"]
            desc = dim_data["description"]
            dimension_descriptions.append(f"**{name}**: {desc}")
        
        # Get instructions
        json_instruction = prompt_manager.get("evaluation.instructions.json_format")
        explain_instruction = prompt_manager.get("evaluation.instructions.explain_scores")
        
        # Build complete prompt
        prompt_parts = [
            "Please evaluate the following content across multiple quality dimensions.",
            "",
            f"Task Background: {task_background}" if task_background else "",
            f"Content to Evaluate: {content}",
            "",
            "Evaluation Dimensions:",
            *dimension_descriptions,
            "",
            json_instruction,
            explain_instruction,
            "",
            'Format: {"scores": {"relevance": 0.8, "completeness": 0.7, ...}, "reasoning": {"relevance": "...", ...}, "suggestions": ["...", "..."]}'
        ]
        
        return "\n".join(filter(None, prompt_parts))
    
    def _call_llm(self, prompt: str) -> str:
        """Call LLM with error handling."""
        try:
            return self.llm_client.chat(prompt)
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            raise
    
    def _parse_evaluation_response(self, response: str) -> Dict[str, Any]:
        """Parse LLM response into structured evaluation results."""
        try:
            # Try to extract JSON from response
            result = json.loads(response)
            
            # Validate required fields
            if "scores" not in result:
                raise ValueError("Missing 'scores' field in response")
            
            # Add metadata
            result["evaluation_method"] = "llm_english"
            result["cached"] = False  # Will be updated by cache if applicable
            
            return result
            
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Failed to parse LLM response: {e}")
            return self._fallback_evaluation(response)
    
    def _fallback_evaluation(self, content: str) -> Dict[str, Any]:
        """Fallback evaluation using English error messages."""
        return {
            "scores": {
                "relevance": 0.5,
                "completeness": 0.5,
                "accuracy": 0.5,
                "clarity": 0.5,
                "coherence": 0.5,
                "scientific_rigor": 0.5
            },
            "reasoning": {
                "relevance": prompt_manager.get("common.errors.evaluation_error", error="LLM parsing failed"),
                "completeness": "Using fallback evaluation",
                "accuracy": "Manual review recommended",
                "clarity": "Assessment unavailable",
                "coherence": "Default score assigned",
                "scientific_rigor": "Requires expert review"
            },
            "suggestions": [
                "Manual evaluation recommended due to processing error",
                "Consider reviewing content formatting",
                "Retry evaluation with different approach"
            ],
            "evaluation_method": "fallback_english",
            "error": True
        }


class EnglishExpertEvaluator:
    """
    Example of Expert Evaluator using English prompts.
    
    Shows how to use expert role templates with English prompts.
    """
    
    def __init__(self, expert_role: str, llm_client, use_cache: bool = True):
        """
        Initialize expert evaluator.
        
        Args:
            expert_role: Expert role key (e.g., 'theoretical_biologist')
            llm_client: LLM client
            use_cache: Whether to use caching
        """
        self.expert_role = expert_role
        self.llm_client = llm_client
        self.cache = get_llm_cache() if use_cache else None
        
        # Load expert information
        self.expert_info = prompt_manager.get_category("expert_roles")[expert_role]
    
    def evaluate(self, content: str, task_background: str = "") -> Dict[str, Any]:
        """Evaluate content from expert perspective."""
        try:
            prompt = self._build_expert_prompt(content, task_background)
            
            # Use cached response if available
            cache_key = f"expert_{self.expert_role}_{hash(prompt)}"
            
            if self.cache:
                response = self.cache.get(cache_key, f"expert_{self.expert_role}")
                if response is None:
                    response = self._call_llm(prompt)
                    self.cache.set(cache_key, response, f"expert_{self.expert_role}")
            else:
                response = self._call_llm(prompt)
            
            return self._parse_expert_response(response)
            
        except Exception as e:
            logger.error(f"Expert evaluation error: {e}")
            return self._fallback_expert_evaluation()
    
    def _build_expert_prompt(self, content: str, task_background: str) -> str:
        """Build expert evaluation prompt."""
        
        # Get expert-specific templates
        intro_template = prompt_manager.get("expert_evaluation.intro")
        
        # Get expert info
        expert_name = self.expert_info["name"]
        expert_description = self.expert_info["description"]
        focus_areas = ", ".join(self.expert_info["focus_areas"])
        
        # Build prompt
        prompt_parts = [
            intro_template.format(role_description=expert_description),
            "",
            prompt_manager.get("expert_evaluation.task_background") + f" {task_background}",
            prompt_manager.get("expert_evaluation.content_to_evaluate") + f" {content}",
            "",
            prompt_manager.get("expert_evaluation.focus_statement").format(role_name=expert_name) + f" {focus_areas}",
            "",
            prompt_manager.get("expert_evaluation.evaluation_instruction"),
        ]
        
        # Add dimensions
        dimensions = prompt_manager.get_category("expert_evaluation")["dimensions"]
        for dim in dimensions.values():
            prompt_parts.append(dim)
        
        prompt_parts.extend([
            "",
            'Please provide your evaluation in JSON format with scores (0-1), strengths, issues, and suggestions.'
        ])
        
        return "\n".join(prompt_parts)
    
    def _call_llm(self, prompt: str) -> str:
        """Call LLM API."""
        return self.llm_client.chat(prompt)
    
    def _parse_expert_response(self, response: str) -> Dict[str, Any]:
        """Parse expert evaluation response."""
        try:
            result = json.loads(response)
            result["expert_role"] = self.expert_role
            result["expert_name"] = self.expert_info["name"]
            result["evaluation_method"] = "expert_english"
            return result
        except json.JSONDecodeError:
            return self._fallback_expert_evaluation()
    
    def _fallback_expert_evaluation(self) -> Dict[str, Any]:
        """Fallback expert evaluation."""
        fallback_msg = prompt_manager.get(
            "expert_evaluation.fallback_messages.llm_unavailable"
        )
        
        return {
            "scores": {"overall": 0.5},
            "strengths": [fallback_msg],
            "issues": ["Evaluation system unavailable"],
            "suggestions": ["Manual expert review recommended"],
            "expert_role": self.expert_role,
            "expert_name": self.expert_info["name"],
            "evaluation_method": "fallback_english",
            "error": True
        }


def demonstrate_integration():
    """
    Demonstrate how to integrate English prompts with existing code.
    """
    print("=== English Prompt Integration Demo ===")
    
    # Mock LLM client for demonstration
    class MockLLMClient:
        def chat(self, prompt: str) -> str:
            return '''
            {
                "scores": {
                    "relevance": 0.85,
                    "completeness": 0.78,
                    "accuracy": 0.92,
                    "clarity": 0.88,
                    "coherence": 0.81,
                    "scientific_rigor": 0.89
                },
                "reasoning": {
                    "relevance": "Content directly addresses phage therapy mechanisms",
                    "completeness": "Covers most key aspects but could include more examples",
                    "accuracy": "Scientifically sound with current research references",
                    "clarity": "Well-structured and accessible language",
                    "coherence": "Logical flow with clear connections between concepts",
                    "scientific_rigor": "Proper methodology and terminology usage"
                },
                "suggestions": [
                    "Add more specific clinical trial examples",
                    "Include discussion of resistance mechanisms",
                    "Consider regulatory approval pathway details"
                ]
            }
            '''
    
    # Example usage
    llm_client = MockLLMClient()
    
    # 1. Basic LLM Evaluation
    print("\n1. Basic LLM Evaluation with English Prompts:")
    evaluator = EnglishLLMEvaluator(llm_client)
    
    result = evaluator.evaluate(
        content="Phage therapy represents a promising alternative to traditional antibiotics...",
        task_background="Evaluate the scientific accuracy of phage therapy overview"
    )
    
    print(f"Evaluation Method: {result['evaluation_method']}")
    print(f"Average Score: {sum(result['scores'].values()) / len(result['scores']):.2f}")
    print(f"Key Suggestion: {result['suggestions'][0]}")
    
    # 2. Expert Evaluation
    print("\n2. Expert Evaluation with English Prompts:")
    expert_evaluator = EnglishExpertEvaluator("theoretical_biologist", llm_client)
    
    expert_result = expert_evaluator.evaluate(
        content="The lytic cycle of bacteriophages involves...",
        task_background="Assess theoretical foundation of phage biology"
    )
    
    print(f"Expert: {expert_result['expert_name']}")
    print(f"Evaluation Method: {expert_result['evaluation_method']}")
    
    # 3. Show prompt customization
    print("\n3. Prompt Customization Example:")
    custom_prompt = prompt_manager.get(
        "evaluation.dimensions.scientific_rigor.description"
    )
    print(f"Scientific Rigor Definition: {custom_prompt}")
    
    # 4. Show available prompt categories
    print("\n4. Available Prompt Categories:")
    categories = prompt_manager.list_categories()
    print(f"Categories: {', '.join(categories)}")
    
    print("\n=== Integration Complete ===")


if __name__ == "__main__":
    # Run demonstration
    demonstrate_integration()
    
    # Show cache statistics if available
    try:
        from app.services.llm_cache import get_llm_cache
        cache = get_llm_cache()
        stats = cache.get_stats()
        print(f"\nCache Statistics:")
        print(f"Hit Rate: {stats['overall']['hit_rate']:.1%}")
        print(f"Total Calls Saved: {stats['overall']['total_hits']}")
    except Exception as e:
        print(f"Cache stats unavailable: {e}")