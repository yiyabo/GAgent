"""
High-level analysis orchestration.

Main flow:
1. Build a plan from the analysis request.
2. Decompose the plan into executable tasks.
3. Execute tasks and collect artifacts.

Example:
    from app.services.interpreter.interpreter import run_analysis

    result = run_analysis(
        description="Analyze sales trends and produce figures.",
        data_paths=["data/sales.csv"],
    )
"""

import asyncio
import os
import logging
from typing import List, Optional
from dataclasses import dataclass
from pathlib import Path

from app.database import init_db
from app.repository.plan_repository import PlanRepository
from app.services.plans.plan_decomposer import PlanDecomposer
from app.services.plans.tree_simplifier import TreeSimplifier
from app.services.plans.similarity_matcher import LLMSimilarityMatcher
from app.services.llm.llm_service import get_llm_service
from .plan_execute import PlanExecutorInterpreter, PlanExecutionResult
from .metadata import DataProcessor
from .prompts.experiment_design import EXPERIMENT_DESIGN_SYSTEM, EXPERIMENT_DESIGN_USER

logger = logging.getLogger(__name__)


@dataclass
class AnalysisResult:
    """Aggregated result returned by analysis orchestration."""
    plan_id: int
    success: bool
    total_tasks: int
    completed_tasks: int
    failed_tasks: int
    generated_files: List[str]
    report_path: Optional[str] = None
    error: Optional[str] = None


def run_analysis(
    description: str,
    data_paths: List[str],
    *,
    title: Optional[str] = None,
    output_dir: str = "./results",
    max_depth: int = 5,
    node_budget: int = 50,
    docker_image: str = "agent-plotter",
    docker_timeout: int = 7200,
    llm_service=None,
) -> AnalysisResult:
    """
    Run the end-to-end analysis pipeline (sync wrapper).

    Flow: create plan -> decompose tasks -> execute plan.

    Args:
        description: Analysis task description.
        data_paths: Input dataset paths.
        title: Optional plan title. Defaults to first data file stem + " analysis".
        output_dir: Directory for generated artifacts.
        max_depth: Maximum decomposition depth.
        node_budget: Maximum number of decomposed nodes.
        docker_image: Docker image used for code execution tasks.
        docker_timeout: Per-task execution timeout in seconds.
        llm_service: Optional LLM service instance.

    Returns:
        AnalysisResult with task counts and generated artifact paths.

    Example:
        >>> result = run_analysis(
        ...  description="Analyze quarterly sales trends",
        ...  data_paths=["sales_2024.csv"],
        ...  max_depth=2
        ... )
        >>> print(f"success: {result.success}, files: {result.generated_files}")
    """
    data_paths = [os.path.abspath(p) for p in data_paths]
    for path in data_paths:
        if not os.path.exists(path):
            return AnalysisResult(
                plan_id=-1,
                success=False,
                total_tasks=0,
                completed_tasks=0,
                failed_tasks=0,
                generated_files=[],
                error=f"File does not exist: {path}",
            )

    init_db()
    repo = PlanRepository()

    if llm_service is None:
        llm_service = get_llm_service()

    if not title:
        title = Path(data_paths[0]).stem + " analysis"

    try:
        logger.info("[1/5] Collecting dataset metadata and generating experiment design...")

        data_info_parts = []
        for path in data_paths:
            try:
                meta = DataProcessor.get_metadata(path)
                meta_text = f"File: {meta.filename}\n"
                meta_text += f"Format: {meta.file_format}, Size: {meta.file_size_bytes} bytes\n"
                meta_text += f"Rows: {meta.total_rows}, Columns: {meta.total_columns}\n"
                if meta.columns:
                    meta_text += "Columns:\n"
                    for col in meta.columns[:10]:  # show top 10 columns
                        meta_text += f"  - {col.name}: {col.dtype}, samples: {col.sample_values[:3]}\n"
                data_info_parts.append(meta_text)
            except Exception as e:
                data_info_parts.append(f"File: {Path(path).name} (metadata read failed: {e})")
        data_info = "\n\n".join(data_info_parts)

        experiment_prompt = f"{EXPERIMENT_DESIGN_SYSTEM}\n\n{EXPERIMENT_DESIGN_USER.format(description=description, data_info=data_info)}"
        experiment_design = llm_service.chat(prompt=experiment_prompt)

        enhanced_description = f"""{description}

---

{experiment_design}
"""
        logger.info("  Experiment design generated successfully.")
        print("\n" + "="*60)
        print("Experiment Design:")
        print("="*60)
        print(experiment_design)
        print("="*60 + "\n")

        logger.info(f"[2/5] Creating plan: {title}")
        plan = repo.create_plan(title=title, description=enhanced_description)
        plan_id = plan.id
        logger.info(f"  Plan ID: {plan_id}")

        logger.info(f"[3/5] Decomposing plan (max_depth={max_depth}, budget={node_budget})")
        decomposer = PlanDecomposer(repo=repo)
        decomp_result = decomposer.run_plan(
            plan_id,
            max_depth=max_depth,
            node_budget=node_budget
        )
        logger.info(f"  Created {len(decomp_result.created_tasks)} tasks")
        if decomp_result.stopped_reason:
            logger.info(f"  Stop reason: {decomp_result.stopped_reason}")

        print_plan_tree(repo, plan_id)

        logger.info("[4/5] Simplifying plan graph...")
        simplifier = TreeSimplifier(matcher=LLMSimilarityMatcher(threshold=0.9))
        dag, simplified_plan_id = simplifier.simplify_and_save(plan_id, repo)

        merge_count = len(dag.merge_map)
        if merge_count > 0:
            logger.info(f"  Merged {merge_count} nodes")
            logger.info(f"  Simplified DAG nodes: {dag.node_count()}")
            logger.info(f"  Simplified plan ID: {simplified_plan_id}")
            execution_plan_id = simplified_plan_id
        else:
            logger.info("  No merge applied; using original plan for execution")
            execution_plan_id = plan_id

        logger.info(f"[5/5] Executing plan (plan_id={execution_plan_id})...")
        os.makedirs(output_dir, exist_ok=True)

        executor = PlanExecutorInterpreter(
            plan_id=execution_plan_id,
            data_file_paths=data_paths,
            output_dir=output_dir,
            docker_image=docker_image,
            docker_timeout=docker_timeout,
            repo=repo,
            llm_service=llm_service
        )
        exec_result: PlanExecutionResult = asyncio.run(executor.execute())

        logger.info(f"Execution completed. success={exec_result.success}")

        return AnalysisResult(
            plan_id=execution_plan_id,  # ID of the executed plan (may be simplified plan ID).
            success=exec_result.success,
            total_tasks=exec_result.total_nodes,
            completed_tasks=exec_result.completed_nodes,
            failed_tasks=exec_result.failed_nodes,
            generated_files=exec_result.all_generated_files,
            report_path=exec_result.report_path
        )

    except Exception as e:
        logger.exception(f"Execution failed: {e}")
        return AnalysisResult(
            plan_id=plan_id if 'plan_id' in locals() else -1,
            success=False,
            total_tasks=0,
            completed_tasks=0,
            failed_tasks=0,
            generated_files=[],
            error=str(e)
        )


def create_and_decompose_plan(
    description: str,
    data_paths: List[str],
    **kwargs
) -> int:
    """
    Create a plan and decompose it without executing.

    Args:
        description: Analysis task description.
        data_paths: Input dataset paths.
        **kwargs: Decomposition options, such as `max_depth` and `node_budget`.

    Returns:
        Created plan ID, ready for `execute_plan()`.
    """
    init_db()
    repo = PlanRepository()

    title = kwargs.get('title') or Path(data_paths[0]).stem + " analysis"
    max_depth = kwargs.get('max_depth', 3)
    node_budget = kwargs.get('node_budget', 10)

    plan = repo.create_plan(title=title, description=description)

    decomposer = PlanDecomposer(repo=repo)
    decomposer.run_plan(plan.id, max_depth=max_depth, node_budget=node_budget)

    return plan.id


async def run_analysis_async(
    description: str,
    data_paths: List[str],
    *,
    title: Optional[str] = None,
    output_dir: str = "./results",
    max_depth: int = 5,
    node_budget: int = 50,
    docker_image: str = "agent-plotter",
    docker_timeout: int = 7200,
    llm_service=None,
) -> AnalysisResult:
    """
    Run the end-to-end analysis pipeline asynchronously.

    Flow: create plan -> decompose tasks -> execute plan.

    Args:
        description: Analysis task description.
        data_paths: Input dataset paths.
        title: Optional plan title.
        output_dir: Directory for generated artifacts.
        max_depth: Maximum decomposition depth.
        node_budget: Maximum number of decomposed nodes.
        docker_image: Docker image used for code execution tasks.
        docker_timeout: Per-task execution timeout in seconds.
        llm_service: Optional LLM service instance.

    Returns:
        AnalysisResult with task counts and generated artifact paths.
    """
    data_paths = [os.path.abspath(p) for p in data_paths]
    for path in data_paths:
        if not os.path.exists(path):
            return AnalysisResult(
                plan_id=-1,
                success=False,
                total_tasks=0,
                completed_tasks=0,
                failed_tasks=0,
                generated_files=[],
                error=f"File does not exist: {path}",
            )

    init_db()
    repo = PlanRepository()

    if llm_service is None:
        llm_service = get_llm_service()

    if not title:
        title = Path(data_paths[0]).stem + " analysis"

    try:
        logger.info("[1/5] Collecting dataset metadata and generating experiment design...")

        data_info_parts = []
        for path in data_paths:
            try:
                meta = DataProcessor.get_metadata(path)
                meta_text = f"File: {meta.filename}\n"
                meta_text += f"Format: {meta.file_format}, Size: {meta.file_size_bytes} bytes\n"
                meta_text += f"Rows: {meta.total_rows}, Columns: {meta.total_columns}\n"
                if meta.columns:
                    meta_text += "Columns:\n"
                    for col in meta.columns[:10]:  # show top 10 columns
                        meta_text += f"  - {col.name}: {col.dtype}, samples: {col.sample_values[:3]}\n"
                data_info_parts.append(meta_text)
            except Exception as e:
                data_info_parts.append(f"File: {Path(path).name} (metadata read failed: {e})")
        data_info = "\n\n".join(data_info_parts)

        experiment_prompt = f"{EXPERIMENT_DESIGN_SYSTEM}\n\n{EXPERIMENT_DESIGN_USER.format(description=description, data_info=data_info)}"
        experiment_design = llm_service.chat(prompt=experiment_prompt)

        enhanced_description = f"""{description}

---

{experiment_design}
"""
        logger.info("  Experiment design generated successfully.")
        print("\n" + "="*60)
        print("Experiment Design:")
        print("="*60)
        print(experiment_design)
        print("="*60 + "\n")

        logger.info(f"[2/5] Creating plan: {title}")
        plan = repo.create_plan(title=title, description=enhanced_description)
        plan_id = plan.id
        logger.info(f"  Plan ID: {plan_id}")

        logger.info(f"[3/5] Decomposing plan (max_depth={max_depth}, budget={node_budget})")
        decomposer = PlanDecomposer(repo=repo)
        decomp_result = decomposer.run_plan(
            plan_id,
            max_depth=max_depth,
            node_budget=node_budget
        )
        logger.info(f"  Created {len(decomp_result.created_tasks)} tasks")
        if decomp_result.stopped_reason:
            logger.info(f"  Stop reason: {decomp_result.stopped_reason}")

        print_plan_tree(repo, plan_id)

        logger.info("[4/5] Simplifying plan graph...")
        simplifier = TreeSimplifier(matcher=LLMSimilarityMatcher(threshold=0.9))
        dag, simplified_plan_id = simplifier.simplify_and_save(plan_id, repo)

        merge_count = len(dag.merge_map)
        if merge_count > 0:
            logger.info(f"  Merged {merge_count} nodes")
            logger.info(f"  Simplified DAG nodes: {dag.node_count()}")
            logger.info(f"  Simplified plan ID: {simplified_plan_id}")
            execution_plan_id = simplified_plan_id
        else:
            logger.info("  No merge applied; using original plan for execution")
            execution_plan_id = plan_id

        logger.info(f"[5/5] Executing plan (plan_id={execution_plan_id})...")
        os.makedirs(output_dir, exist_ok=True)

        executor = PlanExecutorInterpreter(
            plan_id=execution_plan_id,
            data_file_paths=data_paths,
            output_dir=output_dir,
            docker_image=docker_image,
            docker_timeout=docker_timeout,
            repo=repo,
            llm_service=llm_service
        )
        exec_result: PlanExecutionResult = await executor.execute()

        logger.info(f"Execution completed. success={exec_result.success}")

        return AnalysisResult(
            plan_id=execution_plan_id,  # ID of the executed plan (may be simplified plan ID).
            success=exec_result.success,
            total_tasks=exec_result.total_nodes,
            completed_tasks=exec_result.completed_nodes,
            failed_tasks=exec_result.failed_nodes,
            generated_files=exec_result.all_generated_files,
            report_path=exec_result.report_path
        )

    except Exception as e:
        logger.exception(f"Execution failed: {e}")
        return AnalysisResult(
            plan_id=plan_id if 'plan_id' in locals() else -1,
            success=False,
            total_tasks=0,
            completed_tasks=0,
            failed_tasks=0,
            generated_files=[],
            error=str(e)
        )


def execute_plan(
    plan_id: int,
    data_paths: List[str],
    *,
    output_dir: str = "./results",
    docker_image: str = "agent-plotter",
    docker_timeout: int = 300,
    llm_service=None,
) -> AnalysisResult:
    """
    Execute an existing plan against the provided datasets.

    Args:
        plan_id: Plan ID.
        data_paths: Input dataset paths.
        output_dir: Directory for generated artifacts.
        docker_image: Docker image used for code execution tasks.
        docker_timeout: Per-task execution timeout in seconds.
        llm_service: Optional LLM service instance.

    Returns:
        AnalysisResult with aggregated execution output.
    """
    init_db()
    repo = PlanRepository()

    if llm_service is None:
        llm_service = get_llm_service()

    print_plan_tree(repo, plan_id=plan_id)

    executor = PlanExecutorInterpreter(
        plan_id=plan_id,
        data_file_paths=data_paths,
        output_dir=output_dir,
        docker_image=docker_image,
        docker_timeout=docker_timeout,
        repo=repo,
        llm_service=llm_service
    )
    exec_result: PlanExecutionResult = asyncio.run(executor.execute())

    logger.info(f"Execution completed. success={exec_result.success}")

    return AnalysisResult(
        plan_id=plan_id,
        success=exec_result.success,
        total_tasks=exec_result.total_nodes,
        completed_tasks=exec_result.completed_nodes,
        failed_tasks=exec_result.failed_nodes,
        generated_files=exec_result.all_generated_files,
        report_path=exec_result.report_path
    )


def print_plan_tree(repo: PlanRepository, plan_id: int):
    """Print a readable tree view of plan tasks for debugging."""
    tree = repo.get_plan_tree(plan_id)

    print(f"\n{'='*60}")
    print(f"Plan #{tree.id}: {tree.title}")
    print(f"{'='*60}")
    print(f"Total tasks: {len(tree.nodes)}")
    print("\nTasks:")

    def print_node(node_id, indent=0):
        node = tree.nodes.get(node_id)
        if not node:
            return
        prefix = "  " * indent
        deps = f" [depends on: {','.join(map(str, node.dependencies))}]" if node.dependencies else ""
        print(f"{prefix}├─ [{node.id}] {node.name}{deps}")
        if node.instruction:
            instr = node.instruction.strip()[:60]
            if len(node.instruction.strip()) > 60:
                instr += "..."
            print(f"{prefix}│  > {instr}")

        children = tree.adjacency.get(node_id, [])
        for child_id in sorted(children, key=lambda x: tree.nodes[x].position):
            print_node(child_id, indent + 1)

    roots = tree.adjacency.get(None, [])
    for root_id in sorted(roots, key=lambda x: tree.nodes[x].position):
        print_node(root_id)

    print()
