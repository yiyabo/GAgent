"""
Enhanced Task Executor with Iterative Evaluation

Extends the basic executor with content evaluation and iterative improvement capabilities.
Supports both legacy single-pass execution and new evaluation-driven execution.
"""

from typing import Optional, Dict, Any, Tuple
import time
import logging
from datetime import datetime

from .models import EvaluationConfig, EvaluationResult, TaskExecutionResult
from .interfaces import TaskRepository
from .repository.tasks import default_repo
from .services.context import gather_context
from .services.context_budget import apply_budget
from .services.embeddings import get_embeddings_service
from .services.content_evaluator import get_evaluator
from .llm import get_default_client

logger = logging.getLogger(__name__)


def execute_task_with_evaluation(
    task,
    repo: Optional[TaskRepository] = None,
    max_iterations: int = 3,
    quality_threshold: float = 0.8,
    evaluation_config: Optional[EvaluationConfig] = None,
    use_context: bool = False,
    context_options: Optional[Dict[str, Any]] = None,
) -> TaskExecutionResult:
    """
    Execute task with iterative evaluation and improvement
    
    Args:
        task: Task to execute (dict or row object)
        repo: Task repository
        max_iterations: Maximum improvement iterations
        quality_threshold: Minimum acceptable quality score
        evaluation_config: Custom evaluation configuration
        use_context: Whether to use context gathering
        context_options: Context gathering options
        
    Returns:
        TaskExecutionResult with evaluation details
    """
    start_time = time.time()
    repo = repo or default_repo
    task_id, name = _get_task_id_and_name(task)
    
    # Setup evaluation
    config = evaluation_config or EvaluationConfig(
        quality_threshold=quality_threshold,
        max_iterations=max_iterations
    )
    evaluator = get_evaluator(config)
    
    # Store evaluation config for this task
    repo.store_evaluation_config(
        task_id=task_id,
        quality_threshold=config.quality_threshold,
        max_iterations=config.max_iterations,
        evaluation_dimensions=config.evaluation_dimensions,
        domain_specific=config.domain_specific,
        strict_mode=config.strict_mode,
        custom_weights=config.custom_weights
    )
    
    # Build task context for evaluation
    task_context = {
        "name": name,
        "task_id": task_id,
        "task_type": getattr(task, 'task_type', 'atomic') if hasattr(task, 'task_type') else task.get('task_type', 'atomic')
    }
    
    # Initial prompt
    default_prompt = (
        f"Write a concise, clear section that fulfills the following task.\n"
        f"Task: {name}.\n"
        f"Length: ~200 words. Use a neutral, professional tone. Avoid domain-specific assumptions unless explicitly provided."
    )
    prompt = _fetch_prompt(task_id, default_prompt, repo)
    
    # Iterative execution loop
    best_content = ""
    best_evaluation = None
    final_status = "failed"
    
    for iteration in range(max_iterations):
        try:
            logger.info(f"Task {task_id} iteration {iteration + 1}/{max_iterations}")
            
            # Gather context if requested
            current_prompt = prompt
            if use_context:
                current_prompt = _build_context_prompt(
                    prompt, task_id, repo, context_options or {}
                )
            
            # Generate content
            logger.debug(f"Generating content for task {task_id}, iteration {iteration + 1}")
            content = _glm_chat(current_prompt)
            
            if not content or not content.strip():
                logger.warning(f"Empty content generated for task {task_id}, iteration {iteration + 1}")
                continue
            
            # Evaluate content
            logger.debug(f"Evaluating content for task {task_id}, iteration {iteration + 1}")
            evaluation = evaluator.evaluate_content(
                content=content,
                task_context=task_context,
                iteration=iteration + 1
            )
            
            # Store evaluation history
            repo.store_evaluation_history(
                task_id=task_id,
                iteration=iteration + 1,
                content=content,
                overall_score=evaluation.overall_score,
                dimension_scores=evaluation.dimensions.model_dump(),
                suggestions=evaluation.suggestions,
                needs_revision=evaluation.needs_revision,
                metadata=evaluation.metadata
            )
            
            # Update best result
            if not best_evaluation or evaluation.overall_score > best_evaluation.overall_score:
                best_content = content
                best_evaluation = evaluation
            
            # Check if quality threshold is met
            if evaluation.overall_score >= config.quality_threshold:
                logger.info(f"Task {task_id} reached quality threshold ({evaluation.overall_score:.3f}) in iteration {iteration + 1}")
                final_status = "done"
                break
            
            # Generate improvement prompt for next iteration
            if iteration + 1 < max_iterations:
                prompt = _build_revision_prompt(prompt, content, evaluation)
                logger.debug(f"Generated revision prompt for task {task_id}, iteration {iteration + 1}")
        
        except Exception as e:
            logger.error(f"Error in task {task_id} iteration {iteration + 1}: {e}")
            continue
    
    # Final processing
    execution_time = time.time() - start_time
    
    if best_content and best_evaluation:
        # Store final content
        repo.upsert_task_output(task_id, best_content)
        
        # Generate embedding asynchronously
        try:
            if context_options and context_options.get("generate_embeddings", True):
                _generate_task_embedding_async(task_id, best_content, repo)
        except Exception as e:
            logger.warning(f"Failed to generate embedding for task {task_id}: {e}")
        
        # Determine final status
        if final_status != "done":
            if best_evaluation.overall_score >= 0.7:
                final_status = "needs_review"  # Good enough but below threshold
            else:
                final_status = "failed"  # Poor quality
        
        logger.info(f"Task {task_id} completed: {final_status} (score: {best_evaluation.overall_score:.3f}, iterations: {best_evaluation.iteration})")
    
    else:
        logger.error(f"Task {task_id} failed to generate any valid content")
        final_status = "failed"
    
    return TaskExecutionResult(
        task_id=task_id,
        status=final_status,
        content=best_content or None,
        evaluation=best_evaluation,
        iterations=best_evaluation.iteration if best_evaluation else max_iterations,
        execution_time=execution_time
    )


def execute_task(
    task,
    repo: Optional[TaskRepository] = None,
    use_context: bool = False,
    context_options: Optional[Dict[str, Any]] = None,
    enable_evaluation: bool = False,
    **evaluation_kwargs
):
    """
    Backward-compatible task execution function
    
    Args:
        task: Task to execute
        repo: Task repository 
        use_context: Whether to use context gathering
        context_options: Context gathering options
        enable_evaluation: Whether to enable iterative evaluation
        **evaluation_kwargs: Additional evaluation parameters
        
    Returns:
        Status string for backward compatibility, or TaskExecutionResult if enable_evaluation=True
    """
    repo = repo or default_repo
    task_id, name = _get_task_id_and_name(task)
    
    if enable_evaluation:
        # Use new evaluation-driven execution
        result = execute_task_with_evaluation(
            task=task,
            repo=repo,
            use_context=use_context,
            context_options=context_options,
            **evaluation_kwargs
        )
        return result
    else:
        # Legacy single-pass execution
        return _execute_task_legacy(
            task=task,
            repo=repo,
            use_context=use_context,
            context_options=context_options
        )


def _execute_task_legacy(
    task,
    repo: TaskRepository,
    use_context: bool = False,
    context_options: Optional[Dict[str, Any]] = None,
) -> str:
    """Legacy single-pass task execution for backward compatibility"""
    task_id, name = _get_task_id_and_name(task)

    default_prompt = (
        f"Write a concise, clear section that fulfills the following task.\n"
        f"Task: {name}.\n"
        f"Length: ~200 words. Use a neutral, professional tone. Avoid domain-specific assumptions unless explicitly provided."
    )
    prompt = _fetch_prompt(task_id, default_prompt, repo)

    # Optionally gather and prepend contextual information
    if use_context:
        prompt = _build_context_prompt(prompt, task_id, repo, context_options or {})

    try:
        content = _glm_chat(prompt)
        repo.upsert_task_output(task_id, content)
        print(f"Task {task_id} ({name}) done.")
        
        # Asynchronously generate embedding (optional)
        try:
            generate_embeddings = True  # Default enabled
            
            # Check if there's embedding configuration in context_options
            if context_options and isinstance(context_options, dict):
                generate_embeddings = context_options.get("generate_embeddings", True)
            
            if generate_embeddings:
                _generate_task_embedding_async(task_id, content, repo)
        except Exception as embed_error:
            logger.warning(f"Failed to trigger embedding generation (task {task_id}): {embed_error}")
        
        return "done"
    except Exception as e:
        print(f"Task {task_id} ({name}) failed: {e}")
        return "failed"


def _build_context_prompt(
    base_prompt: str,
    task_id: int, 
    repo: TaskRepository,
    context_options: Dict[str, Any]
) -> str:
    """Build prompt with context information"""
    try:
        # Selection options
        include_deps = bool(context_options.get("include_deps", True))
        include_plan = bool(context_options.get("include_plan", True))
        k = int(context_options.get("k", 5)) if context_options.get("k") is not None else 5
        
        # GLM semantic retrieval options
        semantic_k = int(context_options.get("semantic_k")) if context_options.get("semantic_k") is not None else None
        min_similarity = float(context_options.get("min_similarity")) if context_options.get("min_similarity") is not None else None
        
        # Manual task IDs
        manual = None
        mids = context_options.get("manual")
        if isinstance(mids, list):
            try:
                manual = [int(x) for x in mids]
            except Exception:
                manual = None
        
        bundle = gather_context(
            task_id,
            repo=repo,
            include_deps=include_deps,
            include_plan=include_plan,
            k=k,
            manual=manual,
            semantic_k=semantic_k,
            min_similarity=min_similarity,
        )
        
        # Budget options
        max_chars = context_options.get("max_chars")
        per_section_max = context_options.get("per_section_max")
        strategy = context_options.get("strategy") if isinstance(context_options.get("strategy"), str) else None
        
        if (max_chars is not None) or (per_section_max is not None):
            try:
                max_chars_i = int(max_chars) if max_chars is not None else None
            except Exception:
                max_chars_i = None
            try:
                per_section_max_i = int(per_section_max) if per_section_max is not None else None
            except Exception:
                per_section_max_i = None
            bundle = apply_budget(
                bundle,
                max_chars=max_chars_i,
                per_section_max=per_section_max_i,
                strategy=strategy or "truncate",
            )

        ctx = bundle.get("combined") if isinstance(bundle, dict) else None
        if ctx:
            return f"[Context]\n\n{ctx}\n\n[Task Instruction]\n\n{base_prompt}"
    except Exception as e:
        logger.warning(f"Failed to build context prompt: {e}")
    
    return base_prompt


def _build_revision_prompt(
    original_prompt: str,
    previous_content: str,
    evaluation: EvaluationResult
) -> str:
    """Build prompt for content revision based on evaluation feedback"""
    
    # Extract key issues and suggestions
    suggestions_text = "\n".join([f"- {s}" for s in evaluation.suggestions])
    
    # Identify weakest dimensions
    weak_dimensions = []
    for dim_name, score in evaluation.dimensions.model_dump().items():
        if score < 0.7:  # Below good threshold
            weak_dimensions.append(f"{dim_name} (score: {score:.2f})")
    
    weak_dims_text = ", ".join(weak_dimensions) if weak_dimensions else "general quality"
    
    revision_prompt = f"""Your previous response needs improvement. Current quality score: {evaluation.overall_score:.2f}/1.0

AREAS TO IMPROVE: {weak_dims_text}

SPECIFIC FEEDBACK:
{suggestions_text}

ORIGINAL TASK:
{original_prompt}

PREVIOUS ATTEMPT:
{previous_content}

Please revise the content to address the feedback above. Focus on:
1. Improving the weakest areas identified
2. Following the specific suggestions provided
3. Maintaining the original task requirements
4. Enhancing overall quality and clarity

Provide a complete, improved version:"""

    return revision_prompt


# Helper functions (imported from original executor)
def _get_task_id_and_name(task):
    """Support both sqlite3.Row (mapping) and tuple-style rows."""
    try:
        task_id = task["id"]  # sqlite3.Row mapping
        name = task["name"]
    except Exception:
        task_id = task[0]
        name = task[1]
    return task_id, name


def _fetch_prompt(task_id, default_prompt, repo: TaskRepository):
    prompt = repo.get_task_input_prompt(task_id)
    return prompt if (isinstance(prompt, str) and prompt.strip()) else default_prompt


def _glm_chat(prompt: str) -> str:
    # Delegate to default LLM client (Phase 1 abstraction)
    client = get_default_client()
    return client.chat(prompt)


def _generate_task_embedding_async(task_id: int, content: str, repo: TaskRepository):
    """Asynchronously generate and store task embedding"""
    import threading
    
    def _background_embedding():
        try:
            if not content or not content.strip():
                logger.debug(f"Task {task_id} content is empty, skipping embedding generation")
                return
            
            # Check if embedding already exists
            existing_embedding = repo.get_task_embedding(task_id)
            if existing_embedding:
                logger.debug(f"Task {task_id} already has embedding, skipping generation")
                return
            
            embeddings_service = get_embeddings_service()
            
            # Generate embedding
            logger.debug(f"Generating embedding for task {task_id}")
            embedding = embeddings_service.get_single_embedding(content)
            
            if embedding:
                # Store embedding
                embedding_json = embeddings_service.embedding_to_json(embedding)
                repo.store_task_embedding(task_id, embedding_json)
                logger.debug(f"Successfully stored embedding for task {task_id}")
            else:
                logger.warning(f"Failed to generate embedding for task {task_id}")
                
        except Exception as e:
            logger.error(f"Error generating embedding for task {task_id}: {e}")
    
    # Execute in background thread to avoid blocking main process
    thread = threading.Thread(target=_background_embedding, daemon=True)
    thread.start()