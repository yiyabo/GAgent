"""
Enhanced Task Executor with Iterative Evaluation

Extends the basic executor with content evaluation and iterative improvement capabilities.
Supports both legacy single-pass execution and new evaluation-driven execution.
"""

import logging
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .interfaces import TaskRepository
from .llm import get_default_client
from .models import EvaluationConfig, EvaluationResult, TaskExecutionResult
from .repository.tasks import default_repo
from .services.content_evaluator import get_evaluator
from .services.context import gather_context
from .services.context_budget import apply_budget
from .services.embeddings import get_embeddings_service
from .services.llm_evaluator import get_llm_evaluator
from .services.adversarial_evaluator import get_adversarial_evaluator
from .services.expert_evaluator import get_multi_expert_evaluator
from .services.evaluation_supervisor import get_evaluation_supervisor, monitor_evaluation

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


def execute_task_with_llm_evaluation(
    task,
    repo: Optional[TaskRepository] = None,
    max_iterations: int = 3,
    quality_threshold: float = 0.8,
    evaluation_config: Optional[EvaluationConfig] = None,
    use_context: bool = False,
    context_options: Optional[Dict[str, Any]] = None,
) -> TaskExecutionResult:
    """
    Execute task with LLM-based intelligent evaluation and improvement
    
    Args:
        task: Task to execute (dict or row object)
        repo: Task repository
        max_iterations: Maximum improvement iterations
        quality_threshold: Minimum acceptable quality score
        evaluation_config: Custom evaluation configuration
        use_context: Whether to use context gathering
        context_options: Context gathering options
        
    Returns:
        TaskExecutionResult with intelligent evaluation details
    """
    start_time = time.time()
    repo = repo or default_repo
    task_id, name = _get_task_id_and_name(task)
    
    # Configure evaluation
    config = evaluation_config or EvaluationConfig(
        quality_threshold=quality_threshold,
        max_iterations=max_iterations,
        strict_mode=True,
        evaluation_dimensions=["relevance", "completeness", "accuracy", "clarity", "coherence", "scientific_rigor"]
    )
    
    # Initialize LLM evaluator
    llm_evaluator = get_llm_evaluator(config)
    
    # Build initial prompt
    default_prompt = (
        f"请写一个清晰、简洁的部分来完成以下任务。\n"
        f"任务: {name}\n"
        f"长度: ~200词。使用中性、专业的语调。避免领域特定假设，除非明确提供。"
    )
    
    prompt = _fetch_prompt(task_id, default_prompt, repo)
    if use_context:
        prompt = _build_context_prompt(prompt, task_id, repo, context_options or {})
    
    # Iterative execution with LLM evaluation
    best_content = None
    best_evaluation = None
    final_status = "max_iterations_reached"
    
    task_context = {
        "task_id": task_id,
        "name": name,
        "task_type": "content_generation"
    }
    
    logger.info(f"Starting LLM-evaluated execution for task {task_id} (max_iterations={max_iterations}, threshold={config.quality_threshold})")
    
    for iteration in range(max_iterations):
        try:
            logger.debug(f"Task {task_id} iteration {iteration + 1}/{max_iterations}")
            
            # Generate content
            content = _glm_chat(prompt)
            
            # Intelligent LLM evaluation
            evaluation = llm_evaluator.evaluate_content_intelligent(content, task_context, iteration + 1)
            
            logger.info(f"Task {task_id} iteration {iteration + 1}: LLM score = {evaluation.overall_score:.3f}")
            
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
                logger.info(f"Task {task_id} reached quality threshold ({evaluation.overall_score:.3f}) with LLM evaluation in iteration {iteration + 1}")
                final_status = "done"
                break
            
            # Generate intelligent improvement prompt for next iteration
            if iteration + 1 < max_iterations:
                prompt = _build_llm_revision_prompt(prompt, content, evaluation)
                logger.debug(f"Generated LLM-based revision prompt for task {task_id}, iteration {iteration + 1}")
        
        except Exception as e:
            logger.error(f"Error in task {task_id} LLM iteration {iteration + 1}: {e}")
            continue
    
    # Use best result
    final_content = best_content or "Error: No content generated"
    final_evaluation = best_evaluation or llm_evaluator.evaluate_content_intelligent("", task_context, 0)
    
    # Store final result
    repo.update_task_status(task_id, final_status)
    repo.upsert_task_output(task_id, final_content)
    
    # Generate embedding asynchronously
    _generate_task_embedding_async(task_id, final_content, repo)
    
    execution_time = time.time() - start_time
    
    result = TaskExecutionResult(
        task_id=task_id,
        content=final_content,
        status=final_status,
        evaluation=final_evaluation,
        iterations_completed=min(max_iterations, (iteration + 1) if 'iteration' in locals() else max_iterations),
        execution_time=execution_time,
        metadata={
            "evaluation_method": "llm_intelligent",
            "config": config.model_dump(),
            "final_score": final_evaluation.overall_score
        }
    )
    
    logger.info(f"Completed LLM evaluation for task {task_id}: {final_status}, score={final_evaluation.overall_score:.3f}, time={execution_time:.2f}s")
    return result


def _build_llm_revision_prompt(
    original_prompt: str,
    previous_content: str,
    evaluation: EvaluationResult
) -> str:
    """Build intelligent revision prompt based on LLM evaluation feedback"""
    
    suggestions_text = "\n".join(f"- {suggestion}" for suggestion in evaluation.suggestions)
    
    revision_prompt = f"""
请根据专业评估反馈改进以下内容。

原始任务要求：
{original_prompt}

之前生成的内容：
```
{previous_content}
```

专业评估结果：
- 总体评分: {evaluation.overall_score:.2f}/1.0
- 相关性: {evaluation.dimensions.relevance:.2f}
- 完整性: {evaluation.dimensions.completeness:.2f}
- 准确性: {evaluation.dimensions.accuracy:.2f}
- 清晰度: {evaluation.dimensions.clarity:.2f}
- 连贯性: {evaluation.dimensions.coherence:.2f}
- 科学严谨性: {evaluation.dimensions.scientific_rigor:.2f}

具体改进建议：
{suggestions_text}

请根据以上评估反馈，重新写一个更高质量的版本。重点改进评分较低的维度，确保内容更加相关、完整、准确、清晰、连贯且科学严谨。
"""
    
    return revision_prompt


def execute_task_with_adversarial_evaluation(
    task,
    repo: Optional[TaskRepository] = None,
    max_rounds: int = 3,
    improvement_threshold: float = 0.1,
    evaluation_config: Optional[EvaluationConfig] = None,
    use_context: bool = False,
    context_options: Optional[Dict[str, Any]] = None,
) -> TaskExecutionResult:
    """
    Execute task with adversarial evaluation (Generator vs Critic)
    
    Args:
        task: Task to execute (dict or row object)
        repo: Task repository
        max_rounds: Maximum adversarial rounds
        improvement_threshold: Minimum improvement to continue
        evaluation_config: Custom evaluation configuration
        use_context: Whether to use context gathering
        context_options: Context gathering options
        
    Returns:
        TaskExecutionResult with adversarial evaluation details
    """
    start_time = time.time()
    repo = repo or default_repo
    task_id, name = _get_task_id_and_name(task)
    
    # Configure evaluation
    config = evaluation_config or EvaluationConfig(
        quality_threshold=0.7,  # Lower threshold for adversarial
        max_iterations=max_rounds,
        strict_mode=True
    )
    
    # Initialize adversarial evaluator
    adversarial_evaluator = get_adversarial_evaluator(config)
    
    # Build initial prompt
    default_prompt = (
        f"请为以下任务创建高质量、详细的内容。\n"
        f"任务: {name}\n"
        f"要求: 内容要准确、完整、有条理，包含必要的细节和解释。"
    )
    
    prompt = _fetch_prompt(task_id, default_prompt, repo)
    if use_context:
        prompt = _build_context_prompt(prompt, task_id, repo, context_options or {})
    
    task_context = {
        "task_id": task_id,
        "name": name,
        "task_type": "adversarial_generation"
    }
    
    logger.info(f"Starting adversarial evaluation for task {task_id} (max_rounds={max_rounds})")
    
    try:
        # Generate initial content
        initial_content = _glm_chat(prompt)
        
        # Run adversarial evaluation
        eval_start_time = time.time()
        adversarial_result = adversarial_evaluator.adversarial_evaluate(
            content=initial_content,
            task_context=task_context,
            max_rounds=max_rounds,
            improvement_threshold=improvement_threshold
        )
        eval_execution_time = time.time() - eval_start_time
        
        final_content = adversarial_result["best_content"]
        final_robustness_score = adversarial_result["best_robustness_score"]
        
        # Create evaluation result compatible with existing system
        evaluation = EvaluationResult(
            overall_score=final_robustness_score,
            dimensions=EvaluationDimensions(
                relevance=min(final_robustness_score + 0.1, 1.0),
                completeness=final_robustness_score,
                accuracy=final_robustness_score,
                clarity=final_robustness_score,
                coherence=final_robustness_score,
                scientific_rigor=max(final_robustness_score - 0.1, 0.0)
            ),
            suggestions=[
                f"通过{adversarial_result['rounds_completed']}轮对抗性改进",
                f"发现并解决了{adversarial_result['metadata']['total_criticisms']}个问题",
                adversarial_result['final_assessment']['recommendation']
            ],
            needs_revision=final_robustness_score < config.quality_threshold,
            iteration=adversarial_result['rounds_completed'],
            timestamp=datetime.now(),
            metadata={
                "evaluation_method": "adversarial",
                "adversarial_rounds": adversarial_result['rounds_completed'],
                "total_criticisms": adversarial_result['metadata']['total_criticisms'],
                "average_robustness": adversarial_result['metadata']['average_robustness'],
                "convergence_achieved": adversarial_result['final_assessment']['convergence_achieved']
            }
        )
        
        # Monitor adversarial evaluation with supervision system
        try:
            monitoring_report = monitor_evaluation(
                evaluation_result=evaluation,
                evaluation_method="adversarial",
                execution_time=eval_execution_time,
                content=final_content,
                task_context=task_context
            )
            logger.debug(f"Task {task_id} adversarial monitoring: {len(monitoring_report.get('alerts', []))} alerts")
        except Exception as e:
            logger.warning(f"Adversarial evaluation monitoring failed for task {task_id}: {e}")
        
        # Store final result
        status = "done" if final_robustness_score >= config.quality_threshold else "needs_revision"
        repo.update_task_status(task_id, status)
        repo.upsert_task_output(task_id, final_content)
        
        # Store adversarial evaluation history
        repo.store_evaluation_history(
            task_id=task_id,
            iteration=adversarial_result['rounds_completed'],
            content=final_content,
            overall_score=final_robustness_score,
            dimension_scores=evaluation.dimensions.model_dump(),
            suggestions=evaluation.suggestions,
            needs_revision=evaluation.needs_revision,
            metadata={
                "adversarial_data": adversarial_result,
                "evaluation_method": "adversarial"
            }
        )
        
        # Generate embedding asynchronously
        _generate_task_embedding_async(task_id, final_content, repo)
        
        execution_time = time.time() - start_time
        
        result = TaskExecutionResult(
            task_id=task_id,
            content=final_content,
            status=status,
            evaluation=evaluation,
            iterations_completed=adversarial_result['rounds_completed'],
            execution_time=execution_time,
            metadata={
                "evaluation_method": "adversarial",
                "adversarial_effectiveness": adversarial_result['final_assessment']['adversarial_effectiveness'],
                "robustness_score": final_robustness_score,
                "config": config.model_dump()
            }
        )
        
        logger.info(f"Adversarial evaluation completed for task {task_id}: {status}, robustness={final_robustness_score:.3f}, rounds={adversarial_result['rounds_completed']}")
        return result
        
    except Exception as e:
        logger.error(f"Adversarial evaluation failed for task {task_id}: {e}")
        
        # Fallback to basic execution
        basic_result = _execute_task_legacy(task, repo, use_context, context_options)
        
        # Create minimal evaluation result
        fallback_evaluation = EvaluationResult(
            overall_score=0.5,
            dimensions=EvaluationDimensions(),
            suggestions=[f"对抗性评估失败，使用基础执行: {str(e)}"],
            needs_revision=True,
            iteration=1,
            timestamp=datetime.now(),
            metadata={"evaluation_method": "adversarial_fallback", "error": str(e)}
        )
        
        return TaskExecutionResult(
            task_id=task_id,
            content=basic_result if isinstance(basic_result, str) else "Error generating content",
            status="error",
            evaluation=fallback_evaluation,
            iterations_completed=1,
            execution_time=time.time() - start_time,
            metadata={"evaluation_method": "adversarial_fallback", "error": str(e)}
        )


def execute_task_with_multi_expert_evaluation(
    task,
    repo: Optional[TaskRepository] = None,
    max_iterations: int = 3,
    quality_threshold: float = 0.8,
    selected_experts: Optional[List[str]] = None,
    evaluation_config: Optional[EvaluationConfig] = None,
    use_context: bool = False,
    context_options: Optional[Dict[str, Any]] = None,
) -> TaskExecutionResult:
    """
    Execute task with multi-expert evaluation system
    
    Args:
        task: Task to execute (dict or row object)
        repo: Task repository
        max_iterations: Maximum improvement iterations
        quality_threshold: Minimum acceptable quality score
        selected_experts: List of expert names to use (default: all)
        evaluation_config: Custom evaluation configuration
        use_context: Whether to use context gathering
        context_options: Context gathering options
        
    Returns:
        TaskExecutionResult with multi-expert evaluation details
    """
    start_time = time.time()
    repo = repo or default_repo
    task_id, name = _get_task_id_and_name(task)
    
    # Configure evaluation
    config = evaluation_config or EvaluationConfig(
        quality_threshold=quality_threshold,
        max_iterations=max_iterations,
        strict_mode=True,
        evaluation_dimensions=["relevance", "completeness", "accuracy", "clarity", "coherence", "scientific_rigor"]
    )
    
    # Initialize multi-expert evaluator
    multi_expert_evaluator = get_multi_expert_evaluator(config)
    
    # Build initial prompt
    default_prompt = (
        f"请为以下任务创建专业、高质量的内容。\n"
        f"任务: {name}\n"
        f"要求: 内容应该准确、完整、清晰，符合专业标准。请考虑不同专家的视角和要求。"
    )
    
    prompt = _fetch_prompt(task_id, default_prompt, repo)
    if use_context:
        prompt = _build_context_prompt(prompt, task_id, repo, context_options or {})
    
    task_context = {
        "task_id": task_id,
        "name": name,
        "task_type": "multi_expert_evaluation"
    }
    
    # Iterative execution with multi-expert evaluation
    best_content = None
    best_consensus_score = 0.0
    best_expert_result = None
    final_status = "max_iterations_reached"
    
    experts_text = ", ".join(selected_experts) if selected_experts else "all experts"
    logger.info(f"Starting multi-expert evaluation for task {task_id} with {experts_text} (max_iterations={max_iterations}, threshold={config.quality_threshold})")
    
    for iteration in range(max_iterations):
        try:
            logger.debug(f"Task {task_id} multi-expert iteration {iteration + 1}/{max_iterations}")
            
            # Generate content
            content = _glm_chat(prompt)
            
            # Multi-expert evaluation
            eval_start_time = time.time()
            expert_result = multi_expert_evaluator.evaluate_with_multiple_experts(
                content=content,
                task_context=task_context,
                selected_experts=selected_experts,
                iteration=iteration + 1
            )
            eval_execution_time = time.time() - eval_start_time
            
            consensus_score = expert_result["consensus"].get("overall_score", 0.0)
            expert_count = expert_result["metadata"]["successful_experts"]
            
            # Create evaluation result for monitoring
            temp_evaluation = EvaluationResult(
                overall_score=consensus_score,
                dimensions=EvaluationDimensions(),
                suggestions=expert_result["consensus"].get("specific_suggestions", []),
                needs_revision=consensus_score < config.quality_threshold,
                iteration=iteration + 1,
                timestamp=datetime.now(),
                metadata={"evaluation_method": "multi_expert", "expert_count": expert_count}
            )
            
            # Monitor evaluation with supervision system
            try:
                monitoring_report = monitor_evaluation(
                    evaluation_result=temp_evaluation,
                    evaluation_method="multi_expert",
                    execution_time=eval_execution_time,
                    content=content,
                    task_context=task_context
                )
                logger.debug(f"Task {task_id} multi-expert monitoring: {len(monitoring_report.get('alerts', []))} alerts")
            except Exception as e:
                logger.warning(f"Multi-expert evaluation monitoring failed for task {task_id}: {e}")
            
            logger.info(f"Task {task_id} iteration {iteration + 1}: Multi-expert consensus score = {consensus_score:.3f} ({expert_count} experts)")
            
            # Store evaluation history with expert details
            repo.store_evaluation_history(
                task_id=task_id,
                iteration=iteration + 1,
                content=content,
                overall_score=consensus_score,
                dimension_scores=expert_result["consensus"],
                suggestions=expert_result["consensus"].get("specific_suggestions", []),
                needs_revision=consensus_score < config.quality_threshold,
                metadata={
                    "evaluation_method": "multi_expert",
                    "expert_evaluations": expert_result["expert_evaluations"],
                    "disagreements": expert_result["disagreements"],
                    "consensus_confidence": expert_result["consensus"].get("consensus_confidence", 0.0),
                    "successful_experts": expert_count
                }
            )
            
            # Update best result
            if consensus_score > best_consensus_score:
                best_content = content
                best_consensus_score = consensus_score
                best_expert_result = expert_result
            
            # Check if quality threshold is met
            if consensus_score >= config.quality_threshold:
                logger.info(f"Task {task_id} reached quality threshold ({consensus_score:.3f}) with multi-expert evaluation in iteration {iteration + 1}")
                final_status = "done"
                break
            
            # Generate improvement prompt based on expert feedback
            if iteration + 1 < max_iterations:
                prompt = _build_multi_expert_revision_prompt(prompt, content, expert_result)
                logger.debug(f"Generated multi-expert revision prompt for task {task_id}, iteration {iteration + 1}")
        
        except Exception as e:
            logger.error(f"Error in task {task_id} multi-expert iteration {iteration + 1}: {e}")
            continue
    
    # Use best result
    final_content = best_content or "Error: No content generated"
    final_expert_result = best_expert_result or {"consensus": {"overall_score": 0.0}, "expert_evaluations": {}, "disagreements": []}
    
    # Create evaluation result compatible with existing system
    consensus = final_expert_result["consensus"]
    evaluation = EvaluationResult(
        overall_score=best_consensus_score,
        dimensions=EvaluationDimensions(
            relevance=consensus.get("relevance", 0.5),
            completeness=consensus.get("completeness", 0.5),
            accuracy=consensus.get("accuracy", 0.5),
            clarity=consensus.get("clarity", 0.5),
            coherence=consensus.get("coherence", 0.5),
            scientific_rigor=consensus.get("scientific_rigor", 0.5)
        ),
        suggestions=consensus.get("specific_suggestions", ["多专家评估完成"]),
        needs_revision=best_consensus_score < config.quality_threshold,
        iteration=iteration + 1 if 'iteration' in locals() else max_iterations,
        timestamp=datetime.now(),
        metadata={
            "evaluation_method": "multi_expert",
            "expert_count": final_expert_result.get("metadata", {}).get("successful_experts", 0),
            "consensus_confidence": consensus.get("consensus_confidence", 0.0),
            "disagreements_count": len(final_expert_result.get("disagreements", []))
        }
    )
    
    # Store final result
    repo.update_task_status(task_id, final_status)
    repo.upsert_task_output(task_id, final_content)
    
    # Generate embedding asynchronously
    _generate_task_embedding_async(task_id, final_content, repo)
    
    execution_time = time.time() - start_time
    
    result = TaskExecutionResult(
        task_id=task_id,
        content=final_content,
        status=final_status,
        evaluation=evaluation,
        iterations_completed=min(max_iterations, (iteration + 1) if 'iteration' in locals() else max_iterations),
        execution_time=execution_time,
        metadata={
            "evaluation_method": "multi_expert",
            "expert_evaluations": final_expert_result.get("expert_evaluations", {}),
            "disagreements": final_expert_result.get("disagreements", []),
            "consensus_confidence": consensus.get("consensus_confidence", 0.0),
            "config": config.model_dump()
        }
    )
    
    logger.info(f"Multi-expert evaluation completed for task {task_id}: {final_status}, consensus_score={best_consensus_score:.3f}, time={execution_time:.2f}s")
    return result


def _build_multi_expert_revision_prompt(
    original_prompt: str,
    previous_content: str,
    expert_result: Dict[str, Any]
) -> str:
    """Build revision prompt based on multi-expert feedback"""
    
    consensus = expert_result["consensus"]
    disagreements = expert_result["disagreements"]
    
    # Collect expert feedback
    expert_feedback = []
    for expert_name, evaluation in expert_result["expert_evaluations"].items():
        score = evaluation.get("overall_score", 0.0)
        concerns = evaluation.get("major_concerns", [])
        suggestions = evaluation.get("specific_suggestions", [])
        
        feedback_text = f"**{expert_name}** (评分: {score:.2f})"
        if concerns:
            feedback_text += f"\n  主要关切: {'; '.join(concerns)}"
        if suggestions:
            feedback_text += f"\n  建议: {'; '.join(suggestions[:2])}"  # Limit to 2 suggestions per expert
        
        expert_feedback.append(feedback_text)
    
    # Highlight disagreements
    disagreement_text = ""
    if disagreements:
        disagreement_text = "\n专家分歧点:\n"
        for disagreement in disagreements[:3]:  # Limit to 3 disagreements
            field = disagreement["field"]
            low_expert = disagreement["lowest_scorer"]
            high_expert = disagreement["highest_scorer"]
            disagreement_text += f"- {field}: {low_expert}({disagreement['lowest_score']:.2f}) vs {high_expert}({disagreement['highest_score']:.2f})\n"
    
    # Build comprehensive revision prompt
    revision_prompt = f"""
请根据多位专家的评估反馈改进以下内容。

原始任务要求：
{original_prompt}

之前生成的内容：
```
{previous_content}
```

多专家评估结果：
- 专家共识评分: {consensus.get('overall_score', 0.0):.2f}/1.0
- 共识置信度: {consensus.get('consensus_confidence', 0.0):.2f}
- 参与专家数: {expert_result.get('metadata', {}).get('successful_experts', 0)}

各专家详细反馈：
{chr(10).join(expert_feedback)}

{disagreement_text}

综合改进建议：
{chr(10).join(f"- {suggestion}" for suggestion in consensus.get('specific_suggestions', [])[:5])}

请根据以上多专家反馈，重新创建一个更高质量的版本。特别注意：
1. 解决专家们提出的主要关切
2. 平衡不同专家的观点和建议
3. 在有分歧的地方寻找合理的中间立场
4. 确保内容符合各专家领域的专业标准
"""
    
    return revision_prompt