"""
Async Task Executor

This module provides true asynchronous task execution capabilities,
enabling concurrent processing of multiple tasks for improved performance.
"""

import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from ..interfaces import TaskRepository
from ..models import TaskExecutionResult, EvaluationResult, EvaluationDimensions
from ..repository.tasks import default_repo
from ..services.embeddings import get_embeddings_service
from ..services.llm.llm_service import get_llm_service, TaskPromptBuilder, AsyncLLMContext
from ..services.evaluation.content_evaluator import ContentEvaluator

logger = logging.getLogger(__name__)


class AsyncTaskExecutor:
    """Asynchronous task executor with concurrent execution capabilities"""
    
    def __init__(self, repo: Optional[TaskRepository] = None, max_concurrent: int = 3):
        """
        Initialize async task executor
        
        Args:
            repo: Task repository instance
            max_concurrent: Maximum number of concurrent task executions
        """
        self.repo = repo or default_repo
        self.llm_service = get_llm_service()
        self.embeddings_service = get_embeddings_service()
        self.prompt_builder = TaskPromptBuilder()
        self.max_concurrent = max_concurrent
        self.semaphore = asyncio.Semaphore(max_concurrent)
        
    async def execute_task(
        self,
        task,
        use_context: bool = False,
        context_options: Optional[Dict[str, Any]] = None
    ) -> TaskExecutionResult:
        """
        Execute a single task asynchronously
        
        Args:
            task: Task to execute
            use_context: Whether to use context
            context_options: Context configuration
            
        Returns:
            TaskExecutionResult with execution details
        """
        async with self.semaphore:
            start_time = time.time()
            task_id, task_name = self._extract_task_info(task)
            
            logger.info(f"Starting async execution of task {task_id}: {task_name}")
            
            try:
                # Build prompt with context if requested
                prompt = await self._build_task_prompt(task_id, task_name, use_context, context_options)
                
                # Execute LLM chat asynchronously
                content = await self.llm_service.chat_async(prompt)
                
                # Store results asynchronously
                await self._store_results(task_id, content)
                
                # Generate embeddings asynchronously (fire-and-forget)
                asyncio.create_task(self._generate_embedding(task_id, content))
                
                execution_time = time.time() - start_time
                logger.info(f"Task {task_id} completed in {execution_time:.2f}s")
                
                return TaskExecutionResult(
                    task_id=task_id,
                    status="done",
                    content=content,
                    iterations=1,
                    execution_time=execution_time
                )
                
            except Exception as e:
                logger.error(f"Async task execution failed for {task_id}: {e}")
                await self._update_task_status(task_id, "failed")
                
                return TaskExecutionResult(
                    task_id=task_id,
                    status="failed",
                    content=None,
                    iterations=1,
                    execution_time=time.time() - start_time
                )
    
    async def execute_tasks_batch(
        self,
        tasks: List,
        use_context: bool = False,
        context_options: Optional[Dict[str, Any]] = None
    ) -> List[TaskExecutionResult]:
        """
        Execute multiple tasks concurrently
        
        Args:
            tasks: List of tasks to execute
            use_context: Whether to use context
            context_options: Context configuration
            
        Returns:
            List of TaskExecutionResult objects
        """
        logger.info(f"Starting batch execution of {len(tasks)} tasks")
        
        # Create coroutines for all tasks
        coroutines = [
            self.execute_task(task, use_context, context_options)
            for task in tasks
        ]
        
        # Execute all tasks concurrently
        results = await asyncio.gather(*coroutines, return_exceptions=True)
        
        # Handle exceptions in results
        processed_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                task_id = self._extract_task_info(tasks[i])[0]
                logger.error(f"Task {task_id} failed with exception: {result}")
                processed_results.append(
                    TaskExecutionResult(
                        task_id=task_id,
                        status="failed",
                        content=None,
                        iterations=1,
                        execution_time=0
                    )
                )
            else:
                processed_results.append(result)
        
        return processed_results
    
    async def execute_with_evaluation(
        self,
        task,
        max_iterations: int = 3,
        quality_threshold: float = 0.8,
        use_context: bool = False,
        context_options: Optional[Dict[str, Any]] = None
    ) -> TaskExecutionResult:
        """
        Execute task with iterative evaluation and improvement
        
        Args:
            task: Task to execute
            max_iterations: Maximum improvement iterations
            quality_threshold: Minimum quality score
            use_context: Whether to use context
            context_options: Context configuration
            
        Returns:
            TaskExecutionResult with evaluation details
        """
        async with self.semaphore:
            start_time = time.time()
            task_id, task_name = self._extract_task_info(task)
            
            logger.info(f"Starting async execution with evaluation for task {task_id}")
            
            evaluator = ContentEvaluator()
            current_content = None
            evaluation_result = None
            
            for iteration in range(max_iterations):
                try:
                    # Build prompt based on iteration
                    if iteration == 0:
                        prompt = await self._build_task_prompt(task_id, task_name, use_context, context_options)
                    else:
                        # Build revision prompt with feedback
                        feedback = evaluation_result.suggestions if evaluation_result else []
                        prompt = self.prompt_builder.build_revision_prompt(
                            task_name=task_name,
                            current_content=current_content,
                            feedback=feedback
                        )
                    
                    # Execute LLM asynchronously
                    current_content = await self.llm_service.chat_async(prompt)
                    
                    # Evaluate content asynchronously
                    evaluation_result = await self._evaluate_content_async(
                        current_content, task_name, evaluator, iteration
                    )
                    
                    # Check if quality threshold is met
                    if evaluation_result.overall_score >= quality_threshold:
                        logger.info(f"Task {task_id} reached quality threshold at iteration {iteration + 1}")
                        break
                        
                except Exception as e:
                    logger.error(f"Iteration {iteration + 1} failed for task {task_id}: {e}")
                    continue
            
            # Store final results
            await self._store_results(task_id, current_content)
            
            # Generate embeddings asynchronously
            asyncio.create_task(self._generate_embedding(task_id, current_content))
            
            execution_time = time.time() - start_time
            
            return TaskExecutionResult(
                task_id=task_id,
                status="done",
                content=current_content,
                evaluation=evaluation_result,
                iterations=iteration + 1,
                execution_time=execution_time
            )
    
    async def _build_task_prompt(
        self,
        task_id: int,
        task_name: str,
        use_context: bool,
        context_options: Optional[Dict[str, Any]]
    ) -> str:
        """Build task prompt with optional context"""
        # Get stored prompt or build default
        stored_prompt = await self._get_task_prompt(task_id)
        
        if stored_prompt:
            return stored_prompt
        
        # Build context if requested
        context = None
        if use_context and context_options:
            context = await self._build_context(task_id, context_options)
        
        return self.prompt_builder.build_initial_prompt(
            task_name=task_name,
            context=context
        )
    
    async def _build_context(self, task_id: int, context_options: Dict[str, Any]) -> str:
        """Build context from options (simplified for now)"""
        # This is a simplified version - full implementation would integrate
        # with the existing context building system
        context_parts = []
        
        if context_options.get("include_deps"):
            # Would fetch dependencies here
            context_parts.append("Dependencies: [simplified context]")
        
        if context_options.get("include_plan"):
            # Would fetch plan context here
            context_parts.append("Plan context: [simplified context]")
        
        return "\n".join(context_parts) if context_parts else None
    
    async def _evaluate_content_async(
        self,
        content: str,
        task_name: str,
        evaluator: ContentEvaluator,
        iteration: int
    ) -> EvaluationResult:
        """Evaluate content asynchronously"""
        # Run evaluation in executor to avoid blocking
        loop = asyncio.get_event_loop()
        
        def evaluate():
            return evaluator.evaluate_content(
                content=content,
                task_context={"name": task_name},
                iteration=iteration
            )
        
        result = await loop.run_in_executor(None, evaluate)
        
        # Ensure result is in expected format
        if isinstance(result, dict):
            return EvaluationResult(
                overall_score=result.get("overall_score", 0.0),
                dimensions=EvaluationDimensions(**result.get("dimensions", {})),
                suggestions=result.get("suggestions", []),
                needs_revision=result.get("needs_revision", False),
                iteration=iteration
            )
        return result
    
    async def _store_results(self, task_id: int, content: str):
        """Store task results asynchronously"""
        loop = asyncio.get_event_loop()
        
        def store():
            self.repo.upsert_task_output(task_id, content)
            self.repo.update_task_status(task_id, "done")
        
        await loop.run_in_executor(None, store)
    
    async def _update_task_status(self, task_id: int, status: str):
        """Update task status asynchronously"""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None, 
            self.repo.update_task_status, 
            task_id, 
            status
        )
    
    async def _get_task_prompt(self, task_id: int) -> Optional[str]:
        """Get task prompt asynchronously"""
        loop = asyncio.get_event_loop()
        
        def get_prompt():
            try:
                return self.repo.get_task_input_prompt(task_id)
            except Exception:
                return None
        
        return await loop.run_in_executor(None, get_prompt)
    
    async def _generate_embedding(self, task_id: int, content: str):
        """Generate and store embedding asynchronously"""
        try:
            loop = asyncio.get_event_loop()
            
            def generate_and_store():
                try:
                    embedding = self.embeddings_service.get_single_embedding(content)
                    if embedding:
                        import json
                        embedding_str = json.dumps(embedding) if isinstance(embedding, list) else str(embedding)
                        self.repo.store_task_embedding(task_id, embedding_str)
                        logger.debug(f"Generated embedding for task {task_id}")
                except Exception as e:
                    logger.warning(f"Failed to generate embedding for task {task_id}: {e}")
            
            await loop.run_in_executor(None, generate_and_store)
            
        except Exception as e:
            logger.warning(f"Failed to generate embedding for task {task_id}: {e}")
    
    def _extract_task_info(self, task) -> Tuple[int, str]:
        """Extract task ID and name from task object"""
        if hasattr(task, "id") and hasattr(task, "name"):
            return task.id, task.name
        elif isinstance(task, dict):
            return task.get("id"), task.get("name", "Untitled")
        else:
            raise ValueError(f"Invalid task format: {type(task)}")


class AsyncExecutionOrchestrator:
    """Orchestrator for complex async execution workflows"""
    
    def __init__(self, repo: Optional[TaskRepository] = None, max_concurrent: int = 5):
        """
        Initialize execution orchestrator
        
        Args:
            repo: Task repository
            max_concurrent: Maximum concurrent executions
        """
        self.executor = AsyncTaskExecutor(repo, max_concurrent)
        self.repo = repo or default_repo
        
    async def execute_plan(
        self,
        plan_title: str,
        schedule: str = "dag",
        use_context: bool = True,
        enable_evaluation: bool = False,
        evaluation_options: Optional[Dict[str, Any]] = None,
        context_options: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Execute entire plan with async processing
        
        Args:
            plan_title: Title of the plan
            schedule: Scheduling strategy (bfs, dag, postorder)
            use_context: Whether to use context
            enable_evaluation: Whether to enable evaluation
            evaluation_options: Evaluation configuration
            context_options: Context configuration
            
        Returns:
            Execution summary
        """
        logger.info(f"Starting async execution of plan: {plan_title}")
        start_time = time.time()
        
        # Get tasks based on schedule
        tasks = await self._get_scheduled_tasks(plan_title, schedule)
        
        if not tasks:
            logger.warning(f"No tasks found for plan: {plan_title}")
            return {"status": "no_tasks", "plan": plan_title}
        
        # Execute tasks based on configuration
        if enable_evaluation:
            eval_options = evaluation_options or {}
            results = await self._execute_with_evaluation_batch(
                tasks, 
                eval_options.get("max_iterations", 3),
                eval_options.get("quality_threshold", 0.8),
                use_context,
                context_options
            )
        else:
            results = await self.executor.execute_tasks_batch(
                tasks,
                use_context,
                context_options
            )
        
        # Calculate statistics
        total_time = time.time() - start_time
        successful = len([r for r in results if r.status == "done"])
        failed = len([r for r in results if r.status == "failed"])
        
        return {
            "status": "completed",
            "plan": plan_title,
            "tasks_total": len(tasks),
            "tasks_successful": successful,
            "tasks_failed": failed,
            "execution_time": total_time,
            "average_time_per_task": total_time / len(tasks) if tasks else 0,
            "results": results
        }
    
    async def _get_scheduled_tasks(self, plan_title: str, schedule: str) -> List:
        """Get tasks based on scheduling strategy"""
        loop = asyncio.get_event_loop()
        
        def get_tasks():
            from ..scheduler import bfs_schedule, requires_dag_schedule, postorder_schedule
            
            if schedule == "dag":
                return list(requires_dag_schedule(plan_title))
            elif schedule == "postorder":
                return list(postorder_schedule(plan_title))
            else:  # default to BFS
                return list(bfs_schedule(plan_title))
        
        return await loop.run_in_executor(None, get_tasks)
    
    async def _execute_with_evaluation_batch(
        self,
        tasks: List,
        max_iterations: int,
        quality_threshold: float,
        use_context: bool,
        context_options: Optional[Dict[str, Any]]
    ) -> List[TaskExecutionResult]:
        """Execute batch of tasks with evaluation"""
        coroutines = [
            self.executor.execute_with_evaluation(
                task,
                max_iterations,
                quality_threshold,
                use_context,
                context_options
            )
            for task in tasks
        ]
        
        return await asyncio.gather(*coroutines, return_exceptions=True)


# Convenience functions for integration
async def execute_task_async(
    task,
    repo: Optional[TaskRepository] = None,
    use_context: bool = False,
    context_options: Optional[Dict[str, Any]] = None
) -> TaskExecutionResult:
    """
    Convenience function for async task execution
    
    Args:
        task: Task to execute
        repo: Task repository
        use_context: Whether to use context
        context_options: Context configuration
        
    Returns:
        TaskExecutionResult
    """
    executor = AsyncTaskExecutor(repo)
    return await executor.execute_task(task, use_context, context_options)


async def execute_plan_async(
    plan_title: str,
    **kwargs
) -> Dict[str, Any]:
    """
    Convenience function for async plan execution
    
    Args:
        plan_title: Title of the plan
        **kwargs: Additional execution options
        
    Returns:
        Execution summary
    """
    orchestrator = AsyncExecutionOrchestrator()
    return await orchestrator.execute_plan(plan_title, **kwargs)
