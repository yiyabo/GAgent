"""
Prompt Builder

Handles construction of various prompts for different execution scenarios.
Extracted from executor_enhanced.py to separate prompt logic.
"""

import logging
from typing import Any, Dict, List, Optional

from ..models import EvaluationResult
from ..services.context.context import gather_context
from ..services.context.context_budget import apply_budget

logger = logging.getLogger(__name__)


class PromptBuilder:
    """Builder class for constructing various types of prompts."""

    def __init__(self, repo):
        self.repo = repo

    def build_context_prompt(
        self,
        task_id: int,
        task_name: str,
        base_prompt: str,
        use_context: bool = False,
        context_options: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Build prompt with optional context information."""
        if not use_context:
            return base_prompt

        try:
            # Default context options
            ctx_opts = context_options or {}
            include_deps = ctx_opts.get("include_deps", True)
            include_plan = ctx_opts.get("include_plan", True)
            semantic_k = ctx_opts.get("semantic_k", 5)
            min_similarity = ctx_opts.get("min_similarity", 0.1)

            # Gather context
            context_bundle = gather_context(
                task_id=task_id,
                repo=self.repo,
                include_deps=include_deps,
                include_plan=include_plan,
                semantic_k=semantic_k,
                min_similarity=min_similarity,
            )

            # Apply budget constraints
            max_chars = ctx_opts.get("max_chars")
            per_section_max = ctx_opts.get("per_section_max")
            strategy = ctx_opts.get("strategy", "truncate")

            if max_chars or per_section_max:
                context_bundle = apply_budget(
                    context_bundle, max_chars=max_chars, per_section_max=per_section_max, strategy=strategy
                )

            # Build contextual prompt
            context_text = context_bundle.get("combined", "")
            if context_text.strip():
                contextual_prompt = f"""Below is context from related tasks and documents:

{context_text}

---

Now, {base_prompt}

Please use the context above to inform your response, but focus specifically on the current task."""
                return contextual_prompt

        except Exception as e:
            logger.warning(f"Failed to build context for task {task_id}: {e}")

        return base_prompt

    def build_revision_prompt(
        self, original_prompt: str, previous_content: str, evaluation: EvaluationResult, iteration: int
    ) -> str:
        """Build prompt for content revision based on evaluation feedback."""
        suggestions_text = "\\n".join([f"• {s}" for s in evaluation.suggestions])

        revision_prompt = f"""Your previous response needs improvement. Here's what you wrote:

--- PREVIOUS RESPONSE ---
{previous_content}
--- END PREVIOUS RESPONSE ---

EVALUATION FEEDBACK (Overall Score: {evaluation.overall_score:.2f}):
{suggestions_text}

ORIGINAL TASK: {original_prompt}

Please provide an improved version that addresses the feedback above. Focus on:
- Addressing each specific suggestion
- Improving the overall quality and relevance
- Maintaining the requested length and tone
- This is revision attempt {iteration + 1}

IMPROVED RESPONSE:"""

        return revision_prompt

    def build_llm_revision_prompt(
        self, original_prompt: str, previous_content: str, evaluation: EvaluationResult, iteration: int
    ) -> str:
        """Build LLM-specific revision prompt with detailed feedback."""
        # Format dimension scores
        dimensions = evaluation.dimensions
        dimension_feedback = []

        if dimensions.relevance < 0.7:
            dimension_feedback.append(
                f"• Relevance ({dimensions.relevance:.2f}): Better align content with the task requirements"
            )
        if dimensions.completeness < 0.7:
            dimension_feedback.append(
                f"• Completeness ({dimensions.completeness:.2f}): Add missing information or expand on key points"
            )
        if dimensions.accuracy < 0.7:
            dimension_feedback.append(f"• Accuracy ({dimensions.accuracy:.2f}): Verify and correct factual information")
        if dimensions.clarity < 0.7:
            dimension_feedback.append(f"• Clarity ({dimensions.clarity:.2f}): Improve readability and explanation")
        if dimensions.coherence < 0.7:
            dimension_feedback.append(
                f"• Coherence ({dimensions.coherence:.2f}): Better organize ideas and improve flow"
            )
        if dimensions.scientific_rigor < 0.7:
            dimension_feedback.append(
                f"• Scientific Rigor ({dimensions.scientific_rigor:.2f}): Strengthen methodology and evidence"
            )

        dimension_text = (
            "\\n".join(dimension_feedback)
            if dimension_feedback
            else "• Continue to maintain quality across all dimensions"
        )
        suggestions_text = "\\n".join([f"• {s}" for s in evaluation.suggestions])

        revision_prompt = f"""I need you to revise your previous response based on detailed evaluation feedback.

ORIGINAL TASK:
{original_prompt}

YOUR PREVIOUS RESPONSE:
{previous_content}

EVALUATION RESULTS (Overall Score: {evaluation.overall_score:.2f}/1.0):

DIMENSION-SPECIFIC FEEDBACK:
{dimension_text}

SPECIFIC SUGGESTIONS:
{suggestions_text}

REVISION REQUIREMENTS:
- This is revision attempt {iteration + 1}
- Address ALL feedback points above
- Maintain the original task requirements
- Improve overall quality to exceed 0.8 score
- Keep appropriate length and professional tone

Please provide your revised response:"""

        return revision_prompt

    def build_multi_expert_revision_prompt(
        self, original_prompt: str, previous_content: str, evaluation: EvaluationResult, iteration: int
    ) -> str:
        """Build revision prompt incorporating multi-expert feedback."""
        expert_feedback = evaluation.metadata.get("expert_feedback", {}) if evaluation.metadata else {}
        consensus_info = evaluation.metadata.get("consensus_info", {}) if evaluation.metadata else {}

        # Format expert feedback
        expert_text = []
        for expert, feedback in expert_feedback.items():
            score = feedback.get("score", "N/A")
            comments = feedback.get("comments", "No specific comments")
            expert_text.append(f"**{expert.replace('_', ' ').title()}** (Score: {score}):\\n{comments}")

        expert_section = "\\n\\n".join(expert_text) if expert_text else "Expert feedback not available"

        # Consensus information
        consensus_score = consensus_info.get("final_score", evaluation.overall_score)
        disagreement_level = consensus_info.get("disagreement_level", "low")

        revision_prompt = f"""Your content has been evaluated by multiple domain experts. Please revise based on their collective feedback.

ORIGINAL TASK:
{original_prompt}

YOUR PREVIOUS RESPONSE:
{previous_content}

MULTI-EXPERT EVALUATION RESULTS:
Overall Consensus Score: {consensus_score:.2f}/1.0
Expert Disagreement Level: {disagreement_level}

EXPERT FEEDBACK:
{expert_section}

KEY IMPROVEMENT SUGGESTIONS:
{chr(10).join([f"• {s}" for s in evaluation.suggestions])}

REVISION GUIDELINES (Attempt {iteration + 1}):
- Address feedback from ALL experts
- Pay special attention to areas with high expert disagreement
- Ensure content meets professional standards for each expert domain
- Maintain scientific accuracy and clinical relevance
- Target score > 0.8 for final approval

Please provide your expert-informed revision:"""

        return revision_prompt
