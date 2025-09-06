"""
Adversarial Evaluation System.

Implements Generator vs Critic mechanism for robust content evaluation.
The generator tries to improve content while the critic finds flaws,
creating an adversarial training-like dynamic.
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from ...models import EvaluationConfig, EvaluationDimensions, EvaluationResult
from ...prompts import prompt_manager
from .base_evaluator import LLMBasedEvaluator
from app.services.llm.llm_cache import get_llm_cache

logger = logging.getLogger(__name__)


class ContentGenerator:
    """Generator component that creates and improves content"""

    def __init__(self, llm_client, use_cache: bool = True):
        self.llm_client = llm_client
        self.cache = get_llm_cache() if use_cache else None
        self.generation_history = []

    def generate_initial_content(self, task_context: Dict[str, Any]) -> str:
        """Generate initial content for the task"""

        task_name = task_context.get("name", "")
        task_type = task_context.get("task_type", "content_generation")

        # Use English templates from prompt manager
        intro = prompt_manager.get("adversarial.generator.intro")
        task_label = prompt_manager.get("adversarial.generator.task_label")
        task_type_label = prompt_manager.get("adversarial.generator.task_type_label")
        requirements_label = prompt_manager.get("adversarial.generator.requirements_label")
        requirements = prompt_manager.get_category("adversarial")["generator"]["requirements"]
        generate_prompt_text = prompt_manager.get("adversarial.generator.generate_prompt")

        requirements_text = "\n".join(requirements)

        generation_prompt = f"""{intro}

{task_label} "{task_name}"
{task_type_label} {task_type}

{requirements_label}
{requirements_text}

{generate_prompt_text}
"""

        try:
            response = self.llm_client.chat([{"role": "user", "content": generation_prompt}])

            content = response.get("content", "").strip()
            self.generation_history.append({"type": "initial", "content": content, "timestamp": datetime.now()})

            return content

        except Exception as e:
            logger.error(f"Initial content generation failed: {e}")
            error_msg = prompt_manager.get("adversarial.generator.error_message")
            return f"{error_msg} {str(e)}"

    def improve_content(
        self, original_content: str, criticisms: List[Dict[str, Any]], task_context: Dict[str, Any]
    ) -> str:
        """Improve content based on critic's feedback"""

        if not criticisms:
            return original_content

        # Format criticisms for the improvement prompt
        criticism_text = "\n".join(
            [f"- {criticism.get('issue', '')}: {criticism.get('suggestion', '')}" for criticism in criticisms]
        )

        # Use English templates from prompt manager
        intro = prompt_manager.get("adversarial.improver.intro")
        original_task_label = prompt_manager.get("adversarial.improver.original_task")
        original_content_label = prompt_manager.get("adversarial.improver.original_content")
        criticism_label = prompt_manager.get("adversarial.improver.criticism")
        improvement_instruction = prompt_manager.get("adversarial.improver.improvement_instruction")
        requirements = prompt_manager.get_category("adversarial")["improver"]["requirements"]
        improved_content_label = prompt_manager.get("adversarial.improver.improved_content")

        requirements_text = "\n".join(requirements)

        improvement_prompt = f"""{intro}

{original_task_label} "{task_context.get('name', '')}"

{original_content_label}
```
{original_content}
```

{criticism_label}
{criticism_text}

{improvement_instruction}
{requirements_text}

{improved_content_label}
"""

        try:
            response = self.llm_client.chat([{"role": "user", "content": improvement_prompt}])

            improved_content = response.get("content", "").strip()
            self.generation_history.append(
                {
                    "type": "improvement",
                    "original": original_content,
                    "improved": improved_content,
                    "criticisms_addressed": len(criticisms),
                    "timestamp": datetime.now(),
                }
            )

            return improved_content

        except Exception as e:
            logger.error(f"Content improvement failed: {e}")
            return original_content  # Fallback to original


class ContentCritic:
    """Critic component that finds flaws and provides feedback"""

    def __init__(self, llm_client, use_cache: bool = True):
        self.llm_client = llm_client
        self.cache = get_llm_cache() if use_cache else None
        self.criticism_history = []

    def critique_content(self, content: str, task_context: Dict[str, Any], iteration: int = 1) -> List[Dict[str, Any]]:
        """Generate detailed criticisms of the content"""

        task_name = task_context.get("name", "")

        # Use English templates from prompt manager
        intro = prompt_manager.get("adversarial.critic.intro")
        task_bg = prompt_manager.get("adversarial.critic.task_background")
        content_label = prompt_manager.get("adversarial.critic.content_to_critique")
        critique_instruction = prompt_manager.get("adversarial.critic.critique_instruction")
        critique_angles = prompt_manager.get_category("adversarial")["critic"]["critique_angles"]
        output_requirements = prompt_manager.get_category("adversarial")["critic"]["output_requirements"]
        output_format = prompt_manager.get_category("adversarial")["critic"]["output_format"]

        angles_text = "\n".join(critique_angles)
        requirements_text = "\n".join(output_requirements)

        critique_prompt = f"""{intro}

{task_bg} "{task_name}"

{content_label}
```
{content}
```

{critique_instruction}
{angles_text}

{requirements_text}

Please return critique results in JSON format:
{{
    "overall_assessment": "{output_format['overall_assessment']}",
    "major_flaws": [
        {{
            "category": "{output_format['problem_category']}",
            "issue": "{output_format['problem_description']}", 
            "severity": "{output_format['severity']}",
            "suggestion": "{output_format['improvement_suggestion']}",
            "evidence": "{output_format['evidence']}"
        }}
    ],
    "minor_issues": [
        {{
            "issue": "{output_format['minor_issues']}",
            "suggestion": "Improvement suggestion"
        }}
    ],
    "strengths": {output_format['strengths']},
    "critic_confidence": 0.9
}}
"""

        try:
            response = self.llm_client.chat([{"role": "user", "content": critique_prompt}])

            result_text = response.get("content", "").strip()

            # Parse JSON response
            json_start = result_text.find("{")
            json_end = result_text.rfind("}") + 1

            if json_start >= 0 and json_end > json_start:
                json_text = result_text[json_start:json_end]
                critique_data = json.loads(json_text)

                # Extract all criticisms
                criticisms = []

                # Add major flaws
                for flaw in critique_data.get("major_flaws", []):
                    criticisms.append(
                        {
                            "type": "major",
                            "category": flaw.get(
                                "category", prompt_manager.get("adversarial.problem_categories.uncategorized")
                            ),
                            "issue": flaw.get("issue", ""),
                            "severity": flaw.get("severity", prompt_manager.get("adversarial.severity_levels.medium")),
                            "suggestion": flaw.get("suggestion", ""),
                            "evidence": flaw.get("evidence", ""),
                        }
                    )

                # Add minor issues
                for issue in critique_data.get("minor_issues", []):
                    criticisms.append(
                        {
                            "type": "minor",
                            "issue": issue.get("issue", ""),
                            "suggestion": issue.get("suggestion", ""),
                            "severity": "低",
                        }
                    )

                self.criticism_history.append(
                    {
                        "content_preview": content[:100] + "..." if len(content) > 100 else content,
                        "criticisms_count": len(criticisms),
                        "overall_assessment": critique_data.get("overall_assessment", ""),
                        "critic_confidence": critique_data.get("critic_confidence", 0.5),
                        "iteration": iteration,
                        "timestamp": datetime.now(),
                    }
                )

                return criticisms
            else:
                logger.warning("Could not parse critic JSON response")
                return self._fallback_criticism(content, task_context)

        except json.JSONDecodeError as e:
            logger.error(f"JSON parsing error in criticism: {e}")
            return self._fallback_criticism(content, task_context)
        except Exception as e:
            logger.error(f"Content criticism failed: {e}")
            return self._fallback_criticism(content, task_context)

    def _fallback_criticism(self, content: str, task_context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Fallback criticism when LLM fails"""

        word_count = len(content.split())

        criticisms = []

        # Basic length criticism
        if word_count < 100:
            criticisms.append(
                {
                    "type": "major",
                    "category": "完整性",
                    "issue": "内容过于简短",
                    "severity": "高",
                    "suggestion": "增加更多详细信息和解释",
                    "evidence": f"当前仅有{word_count}词",
                }
            )
        elif word_count > 500:
            criticisms.append(
                {"type": "minor", "issue": "内容可能过于冗长", "suggestion": "考虑精简内容，突出重点", "severity": "低"}
            )

        # Basic structure criticism
        if content.count("\n\n") < 1:
            criticisms.append(
                {
                    "type": "minor",
                    "issue": "缺乏段落结构",
                    "suggestion": "将内容分成多个段落以提高可读性",
                    "severity": "中",
                }
            )

        return criticisms


class AdversarialEvaluator(LLMBasedEvaluator):
    """Main adversarial evaluation system combining generator and critic"""

    def __init__(self, config: Optional[EvaluationConfig] = None):
        super().__init__(config)
        self.generator = ContentGenerator(self.llm_client)
        self.critic = ContentCritic(self.llm_client)

    def get_evaluation_method_name(self) -> str:
        return "adversarial"

    def adversarial_evaluate(
        self, content: str, task_context: Dict[str, Any], max_rounds: int = 3, improvement_threshold: float = 0.1
    ) -> Dict[str, Any]:
        """
        Run adversarial evaluation with multiple rounds of generator vs critic

        Args:
            content: Initial content to evaluate
            task_context: Task context information
            max_rounds: Maximum number of adversarial rounds
            improvement_threshold: Minimum improvement required to continue

        Returns:
            Dict with adversarial evaluation results
        """

        logger.info(f"Starting adversarial evaluation with {max_rounds} max rounds")

        rounds = []
        current_content = content
        best_content = content
        best_robustness_score = 0.0

        for round_num in range(max_rounds):
            logger.debug(f"Adversarial round {round_num + 1}/{max_rounds}")

            # Critic attacks the content
            criticisms = self.critic.critique_content(current_content, task_context, iteration=round_num + 1)

            # Calculate robustness score
            robustness_score = self._calculate_robustness_score(criticisms)

            # Generator defends by improving content
            if criticisms and round_num < max_rounds - 1:
                improved_content = self.generator.improve_content(current_content, criticisms, task_context)
            else:
                improved_content = current_content

            round_data = {
                "round": round_num + 1,
                "original_content": current_content,
                "criticisms": criticisms,
                "criticism_count": len(criticisms),
                "robustness_score": robustness_score,
                "improved_content": improved_content,
                "improvement_made": improved_content != current_content,
                "timestamp": datetime.now().isoformat(),
            }

            rounds.append(round_data)

            # Update best content if this version is more robust
            if robustness_score > best_robustness_score:
                best_robustness_score = robustness_score
                best_content = current_content

            # Check if improvement is significant enough to continue
            if round_num > 0:
                prev_score = rounds[round_num - 1]["robustness_score"]
                improvement = robustness_score - prev_score

                if improvement < improvement_threshold:
                    logger.info(
                        f"Improvement below threshold ({improvement:.3f} < {improvement_threshold}), stopping early"
                    )
                    break

            current_content = improved_content

        # Generate final assessment
        final_assessment = self._generate_final_assessment(rounds, best_content, task_context)

        result = {
            "best_content": best_content,
            "best_robustness_score": best_robustness_score,
            "rounds_completed": len(rounds),
            "total_rounds": max_rounds,
            "adversarial_rounds": rounds,
            "final_assessment": final_assessment,
            "metadata": {
                "evaluation_method": "adversarial",
                "generator_generations": len(self.generator.generation_history),
                "critic_analyses": len(self.critic.criticism_history),
                "total_criticisms": sum(r["criticism_count"] for r in rounds),
                "average_robustness": sum(r["robustness_score"] for r in rounds) / len(rounds) if rounds else 0.0,
            },
        }

        logger.info(f"Adversarial evaluation completed: {len(rounds)} rounds, best score: {best_robustness_score:.3f}")

        return result

    def _calculate_robustness_score(self, criticisms: List[Dict[str, Any]]) -> float:
        """Calculate robustness score based on criticisms"""

        if not criticisms:
            return 1.0  # Perfect score if no criticisms

        # Weight criticisms by severity
        severity_weights = {
            "高": 0.3,  # High severity has more impact
            "中": 0.1,  # Medium severity
            "低": 0.05,  # Low severity
        }

        total_penalty = 0.0
        for criticism in criticisms:
            severity = criticism.get("severity", "中")
            penalty = severity_weights.get(severity, 0.1)
            total_penalty += penalty

        # Convert penalty to robustness score (0-1)
        robustness_score = max(0.0, 1.0 - total_penalty)

        return robustness_score

    def _generate_final_assessment(
        self, rounds: List[Dict[str, Any]], best_content: str, task_context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Generate final assessment of adversarial evaluation"""

        if not rounds:
            return {"error": "No rounds completed"}

        total_criticisms = sum(r["criticism_count"] for r in rounds)
        final_score = rounds[-1]["robustness_score"]
        improvement_trend = []

        # Calculate improvement trend
        for i in range(1, len(rounds)):
            improvement = rounds[i]["robustness_score"] - rounds[i - 1]["robustness_score"]
            improvement_trend.append(improvement)

        # Categorize most common criticism types
        all_criticisms = []
        for round_data in rounds:
            all_criticisms.extend(round_data["criticisms"])

        criticism_categories = {}
        for criticism in all_criticisms:
            category = criticism.get("category", "其他")
            criticism_categories[category] = criticism_categories.get(category, 0) + 1

        assessment = {
            "final_robustness_score": final_score,
            "total_criticisms_found": total_criticisms,
            "average_criticisms_per_round": total_criticisms / len(rounds),
            "improvement_trend": improvement_trend,
            "most_common_issues": sorted(criticism_categories.items(), key=lambda x: x[1], reverse=True)[:3],
            "adversarial_effectiveness": len(rounds) / max(1, total_criticisms) * 10,  # Higher is better
            "convergence_achieved": len(improvement_trend) > 0
            and all(abs(imp) < 0.05 for imp in improvement_trend[-2:]),
            "recommendation": self._generate_recommendation(final_score, total_criticisms, len(rounds)),
        }

        return assessment

    def _generate_recommendation(self, final_score: float, total_criticisms: int, rounds: int) -> str:
        """Generate recommendation based on adversarial results"""

        if final_score >= 0.9:
            return "内容质量优秀，通过了严格的对抗性测试"
        elif final_score >= 0.7:
            return "内容质量良好，但仍有改进空间"
        elif final_score >= 0.5:
            return "内容质量中等，需要重点改进主要问题"
        else:
            return "内容质量不足，建议重新设计和编写"


def get_adversarial_evaluator(config: Optional[EvaluationConfig] = None) -> AdversarialEvaluator:
    """Factory function to get adversarial evaluator instance"""
    return AdversarialEvaluator(config)
