"""
Plan execution engine.

This module executes a decomposed plan DAG, persists task-level execution
results, and writes a Markdown analysis report.
"""

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum

from app.services.llm.llm_service import LLMService, get_llm_service
from app.repository.plan_repository import PlanRepository
from app.services.plans.plan_models import PlanNode, PlanTree
from app.services.plans.tree_simplifier import TreeSimplifier
from app.services.plans.dag_models import DAG
from .task_executer import TaskExecutor, TaskExecutionResult, TaskType

logger = logging.getLogger(__name__)


class NodeExecutionStatus(str, Enum):
    """Execution status for an individual plan node."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class NodeExecutionRecord:
    """Execution record for a single node."""
    node_id: int
    node_name: str
    status: NodeExecutionStatus
    task_type: Optional[TaskType] = None

    code: Optional[str] = None
    code_output: Optional[str] = None
    code_description: Optional[str] = None

    has_visualization: bool = False
    visualization_purpose: Optional[str] = None
    visualization_analysis: Optional[str] = None

    text_response: Optional[str] = None

    generated_files: List[str] = field(default_factory=list)

    error_message: Optional[str] = None

    started_at: Optional[str] = None
    completed_at: Optional[str] = None


@dataclass
class PlanExecutionResult:
    """Aggregated execution result for a full plan run."""
    plan_id: int
    plan_title: str
    success: bool
    total_nodes: int
    completed_nodes: int
    failed_nodes: int
    skipped_nodes: int

    node_records: Dict[int, NodeExecutionRecord] = field(default_factory=dict)

    all_generated_files: List[str] = field(default_factory=list)

    report_path: Optional[str] = None

    started_at: Optional[str] = None
    completed_at: Optional[str] = None


class PlanExecutorInterpreter:
    """
    Execute a plan DAG and collect node-level outputs.

    Flow:
    1. Load plan tree and derive DAG.
    2. Execute runnable nodes in dependency-safe order.
    3. Persist node execution results to storage.
    4. Collect generated artifacts.
    5. Write execution summary report.

    Example:
        executor = PlanExecutorInterpreter(
            plan_id=1,
            data_file_paths=["/path/to/data1.csv", "/path/to/data2.csv"],
            output_dir="./results"
        )
        result = executor.execute()
    """

    def __init__(
        self,
        plan_id: int,
        data_file_paths: List[str],
        output_dir: str = "./results",
        llm_service: Optional[LLMService] = None,
        docker_image: str = "agent-plotter",
        docker_timeout: int = 120,
        repo: Optional[PlanRepository] = None
    ):
        """
        Initialize plan executor.

        Args:
            plan_id: Plan ID to execute.
            data_file_paths: Input dataset paths.
            output_dir: Output directory for execution artifacts.
            llm_service: Optional LLM service instance.
            docker_image: Docker image for code-required tasks.
            docker_timeout: Timeout for code-required tasks.
            repo: Optional repository instance. If omitted, a default repository is created.
        """
        self.plan_id = plan_id
        if isinstance(data_file_paths, str):
            data_file_paths = [data_file_paths]
        self.data_file_paths = data_file_paths
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.repo = repo or PlanRepository()

        logger.info(f"Loading plan: plan_id={plan_id}")
        self.tree: PlanTree = self.repo.get_plan_tree(plan_id)
        logger.info(f"Plan loaded: {self.tree.title}, nodes={len(self.tree.nodes)}")

        simplifier = TreeSimplifier()
        self.dag: DAG = simplifier.tree_to_dag(self.tree)
        try:
            self._topo_order: List[int] = self.dag.topological_sort(reverse=True)
            logger.info(f"DAG topological order (leaf -> root): {self._topo_order}")
        except ValueError as e:
            logger.warning(f"DAG topological sort failed ({e}); fallback to node ID order")
            self._topo_order = sorted(self.dag.nodes.keys())

        self.llm_service = llm_service or get_llm_service()
        self.task_executor = TaskExecutor(
            data_file_paths=data_file_paths,
            llm_service=self.llm_service,
            docker_image=docker_image,
            docker_timeout=docker_timeout,
            output_dir=str(self.output_dir),
        )

        self._node_status: Dict[int, NodeExecutionStatus] = {}
        self._node_records: Dict[int, NodeExecutionRecord] = {}
        self._all_generated_files: List[str] = []

        self._analysis_report_path = self._init_analysis_report()

    def _init_analysis_report(self) -> Path:
        """Initialize the Markdown report file for this execution run."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_filename = f"analysis_report_plan{self.plan_id}_{timestamp}.md"
        report_path = self.output_dir / report_filename

        header = f"""# Analysis Report

**Plan ID**: {self.plan_id}
**Plan Title**: {self.tree.title}
**Generated At**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

---

"""
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(header)

        logger.info(f"Analysis report created: {report_path}")
        return report_path

    def _append_visualization_to_report(self, record: NodeExecutionRecord, new_files: List[str]):
        """
        Append visualization output for one node to the report.

        Args:
            record: Node execution record.
            new_files: Newly generated files for this node.
        """
        image_extensions = {'.png', '.jpg', '.jpeg', '.svg', '.pdf'}
        image_files = [f for f in new_files if Path(f).suffix.lower() in image_extensions]

        if not image_files and not record.visualization_purpose and not record.visualization_analysis:
            logger.info(f"Node [{record.node_id}] has no visualization content to append")
            return

        content_parts = []
        content_parts.append(f"\n## Task: {record.node_name}\n")
        content_parts.append(f"**Task ID**: {record.node_id}\n")
        content_parts.append(f"**Completed At**: {record.completed_at}\n\n")

        if record.visualization_purpose:
            content_parts.append("### Purpose\n\n")
            content_parts.append(f"{record.visualization_purpose}\n\n")

        if image_files:
            content_parts.append("### Generated Visuals\n\n")
            for img_path in image_files:
                img_name = Path(img_path).name
                content_parts.append(f"![{img_name}]({img_path})\n\n")

        if record.visualization_analysis:
            content_parts.append("### Interpretation\n\n")
            content_parts.append(f"{record.visualization_analysis}\n\n")

        content_parts.append("---\n")

        with open(self._analysis_report_path, 'a', encoding='utf-8') as f:
            f.write(''.join(content_parts))

        logger.info(f"Appended visualization section for node [{record.node_id}]")

    def _get_children_ids(self, node_id: int) -> List[int]:
        """Return direct child IDs for the given node."""
        return self.tree.children_ids(node_id)

    def _is_leaf_node(self, node_id: int) -> bool:
        """Return True if the node has no children."""
        return len(self._get_children_ids(node_id)) == 0

    def _all_children_completed(self, node_id: int) -> bool:
        """Return True when all child nodes are completed."""
        children = self._get_children_ids(node_id)
        if not children:
            return True
        return all(
            self._node_status.get(child_id) == NodeExecutionStatus.COMPLETED
            for child_id in children
        )

    def _all_children_done(self, node_id: int) -> bool:
        """
        Return True when all child nodes are in terminal states.

        Terminal states: COMPLETED, FAILED, SKIPPED.
        """
        children = self._get_children_ids(node_id)
        if not children:
            return True
        done_statuses = {NodeExecutionStatus.COMPLETED, NodeExecutionStatus.FAILED, NodeExecutionStatus.SKIPPED}
        return all(
            self._node_status.get(child_id) in done_statuses
            for child_id in children
        )

    def _all_dependencies_completed(self, node: PlanNode) -> bool:
        """Return True when all dependency nodes are completed."""
        if not node.dependencies:
            return True
        return all(
            self._node_status.get(dep_id) == NodeExecutionStatus.COMPLETED
            for dep_id in node.dependencies
        )

    def _all_dependencies_done(self, node: PlanNode) -> bool:
        """Return True when all dependency nodes are in terminal states."""
        if not node.dependencies:
            return True
        done_statuses = {NodeExecutionStatus.COMPLETED, NodeExecutionStatus.FAILED, NodeExecutionStatus.SKIPPED}
        return all(
            self._node_status.get(dep_id) in done_statuses
            for dep_id in node.dependencies
        )

    def _can_execute_node(self, node_id: int) -> bool:
        """
        Check whether a node is eligible for execution.

        Conditions:
        1. Node status is PENDING.
        2. All child nodes are COMPLETED.
        3. All dependency nodes are COMPLETED.
        """
        if self._node_status.get(node_id) != NodeExecutionStatus.PENDING:
            return False

        dag_node = self.dag.nodes.get(node_id)
        if not dag_node:
            return False

        if not self._all_children_completed(node_id):
            return False

        node = self.tree.nodes.get(node_id)
        if node and not self._all_dependencies_completed(node):
            return False

        return True

    def _get_executable_nodes(self) -> List[int]:
        """Collect all currently executable nodes under DAG constraints."""
        executable = []
        for node_id in self.tree.nodes:
            if self._can_execute_node(node_id):
                executable.append(node_id)

        if not executable:
            pending_nodes = [nid for nid, s in self._node_status.items() if s == NodeExecutionStatus.PENDING]
            if pending_nodes:
                logger.warning(f"[DAG] {len(pending_nodes)} nodes are still PENDING but not executable")
                for nid in pending_nodes[:10]:  # 10
                    node = self.tree.nodes.get(nid)
                    if node:
                        deps = node.dependencies or []
                        deps_status = {dep: self._node_status.get(dep).value if self._node_status.get(dep) else "unknown" for dep in deps}
                        pending_deps = [dep for dep in deps if self._node_status.get(dep) == NodeExecutionStatus.PENDING]
                        logger.warning(
                            f"  [{nid}] {node.name}: "
                            f"deps={deps}, deps_status={deps_status}, pending_deps={pending_deps}"
                        )

        return executable

    def _collect_dependency_context(self, node_id: int) -> str:
        """
        Collect dependency and child execution outputs as context for node execution.

        Includes:
        1. Completed child node outputs.
        2. Completed dependency node outputs.
        """
        context_parts = []
        collected_ids = set()

        dag_node = self.dag.nodes.get(node_id)
        if not dag_node:
            return ""

        tree_node = self.tree.nodes.get(node_id)

        for child_id in dag_node.child_ids:
            if child_id in collected_ids:
                continue
            record = self._node_records.get(child_id)
            if record and record.status == NodeExecutionStatus.COMPLETED:
                collected_ids.add(child_id)
                child_context = f"\n### subtask [{child_id}] {record.node_name}\n"
                if record.code_description:
                    child_context += f"**analysis content**: {record.code_description}\n"
                if record.code_output:
                    child_context += f"**execution output**:\n```\n{record.code_output[:2000]}\n```\n"
                if record.text_response:
                    child_context += f"**result**: {record.text_response[:2000]}\n"
                if record.generated_files:
                    child_context += f"**files**: {', '.join(record.generated_files)}\n"
                context_parts.append(child_context)

        if tree_node:
            for dep_id in (tree_node.dependencies or []):
                if dep_id in collected_ids:
                    continue
                record = self._node_records.get(dep_id)
                if record and record.status == NodeExecutionStatus.COMPLETED:
                    collected_ids.add(dep_id)
                    dep_context = f"\n### task [{dep_id}] {record.node_name}\n"
                    if record.code_description:
                        dep_context += f"**analysis content**: {record.code_description}\n"
                    if record.code_output:
                        dep_context += f"**execution output**:\n```\n{record.code_output[:2000]}\n```\n"
                    if record.text_response:
                        dep_context += f"**result**: {record.text_response[:2000]}\n"
                    if record.generated_files:
                        dep_context += f"**files**: {', '.join(record.generated_files)}\n"
                    context_parts.append(dep_context)

        return "\n".join(context_parts)

    def _scan_generated_files(self) -> List[str]:
        """
        Scan generated files under output directories.

        Includes:
        1. Files directly under `output_dir`.
        2. Files under `results/`, `code/`, `data/`, and `docs/`.

        Returns:
            Relative file paths under the output workspace.
        """
        files = []

        for f in self.output_dir.iterdir():
            if f.is_file():
                files.append(f.name)

        subdirs_to_scan = ["results", "code", "data", "docs"]
        for subdir in subdirs_to_scan:
            subdir_path = self.output_dir / subdir
            if subdir_path.exists():
                for f in subdir_path.iterdir():
                    if f.is_file():
                        relative_path = f"{subdir}/{f.name}"
                        files.append(relative_path)

        return files

    async def _execute_single_node(self, node_id: int) -> NodeExecutionRecord:
        """Execute a single node and persist its execution result."""
        node = self.tree.nodes[node_id]
        logger.info(f"Executing node [{node_id}] {node.name}")

        dag_node = self.dag.nodes.get(node_id)
        if dag_node:
            failed_children = [
                cid for cid in dag_node.child_ids
                if self._node_status.get(cid) == NodeExecutionStatus.FAILED
            ]
            if failed_children:
                failed_names = [self.tree.nodes[cid].name for cid in failed_children if cid in self.tree.nodes]
                logger.warning(
                    f"Node [{node_id}] has {len(failed_children)} failed children: {failed_names}; proceeding"
                )

        self._node_status[node_id] = NodeExecutionStatus.RUNNING

        record = NodeExecutionRecord(
            node_id=node_id,
            node_name=node.name,
            status=NodeExecutionStatus.RUNNING,
            started_at=datetime.now().isoformat()
        )

        task_description = node.instruction or node.name

        dependency_context = self._collect_dependency_context(node_id)

        files_before = set(self._scan_generated_files())

        is_visualization = node.metadata.get("is_visualization", True)

        result: TaskExecutionResult = await self.task_executor.execute(
            task_title=node.name,
            task_description=task_description,
            subtask_results=dependency_context,
            is_visualization=is_visualization,
            task_id=node_id,
        )

        files_after = set(self._scan_generated_files())
        new_files = list(files_after - files_before)

        record.task_type = result.task_type
        record.generated_files = new_files
        self._all_generated_files.extend(new_files)

        if result.success:
            record.status = NodeExecutionStatus.COMPLETED
            self._node_status[node_id] = NodeExecutionStatus.COMPLETED

            if result.task_type == TaskType.CODE_REQUIRED:
                record.code = result.final_code
                record.code_output = result.code_output
                record.code_description = result.code_description
                record.has_visualization = result.has_visualization
                record.visualization_purpose = result.visualization_purpose
                record.visualization_analysis = result.visualization_analysis
            else:
                record.text_response = result.text_response

            logger.info(f"Node [{node_id}] execution succeeded")

            image_extensions = {'.png', '.jpg', '.jpeg', '.svg', '.pdf'}
            has_image_files = any(Path(f).suffix.lower() in image_extensions for f in new_files)

            if record.has_visualization or has_image_files:
                logger.info(
                    f"Visualization content detected: has_visualization={record.has_visualization}, "
                    f"has_image_files={has_image_files}"
                )
                self._append_visualization_to_report(record, new_files)
        else:
            record.status = NodeExecutionStatus.FAILED
            self._node_status[node_id] = NodeExecutionStatus.FAILED
            record.error_message = result.error_message or result.code_error
            logger.error(f"Node [{node_id}] execution failed: {record.error_message}")

        record.completed_at = datetime.now().isoformat()
        self._node_records[node_id] = record

        self.repo.update_task(
            plan_id=self.plan_id,
            task_id=node_id,
            status=record.status.value,
            execution_result=json.dumps({
                "task_type": record.task_type.value if record.task_type else None,
                "code": record.code,
                "code_description": record.code_description,
                "code_output": record.code_output,
                "text_response": record.text_response,
                "generated_files": record.generated_files,
                "has_visualization": record.has_visualization,
                "visualization_purpose": record.visualization_purpose,
                "visualization_analysis": record.visualization_analysis,
                "error": record.error_message
            }, ensure_ascii=False)
        )

        return record

    def _map_db_status_to_execution_status(self, db_status: str) -> NodeExecutionStatus:
        """
        Map database status strings to `NodeExecutionStatus`.

        Args:
            db_status: Raw task status from storage.

        Returns:
            Mapped execution status.

        Notes:
            - Persisted "running" states are treated as PENDING on resume.
            - Unknown states default to PENDING.
        """
        status_lower = db_status.lower() if db_status else "pending"

        if status_lower == "running":
            logger.info("Database status 'running' mapped to PENDING (resume-safe)")
            return NodeExecutionStatus.PENDING

        status_mapping = {
            "pending": NodeExecutionStatus.PENDING,
            "completed": NodeExecutionStatus.COMPLETED,
            "failed": NodeExecutionStatus.FAILED,
            "skipped": NodeExecutionStatus.SKIPPED,
        }
        return status_mapping.get(status_lower, NodeExecutionStatus.PENDING)

    def _load_node_record_from_db(self, node: PlanNode) -> Optional[NodeExecutionRecord]:
        """
        Load node execution record from stored JSON payload.

        Args:
            node: Plan node.

        Returns:
            NodeExecutionRecord, or `None` if no stored execution result exists.
        """
        if not node.execution_result:
            return None

        try:
            exec_data = json.loads(node.execution_result)

            task_type = None
            if exec_data.get("task_type"):
                try:
                    task_type = TaskType(exec_data["task_type"])
                except ValueError:
                    pass

            record = NodeExecutionRecord(
                node_id=node.id,
                node_name=node.name,
                status=self._map_db_status_to_execution_status(node.status),
                task_type=task_type,
                code=exec_data.get("code"),
                code_output=exec_data.get("code_output"),
                code_description=exec_data.get("code_description"),
                has_visualization=exec_data.get("has_visualization", False),
                visualization_purpose=exec_data.get("visualization_purpose"),
                visualization_analysis=exec_data.get("visualization_analysis"),
                text_response=exec_data.get("text_response"),
                generated_files=exec_data.get("generated_files", []),
                error_message=exec_data.get("error"),
            )
            return record
        except Exception as e:
            logger.warning(f"Failed to load execution record for node [{node.id}]: {e}")
            return None

    def _initialize_node_states(self):
        """
        Initialize in-memory node states from persisted task statuses.
        """
        pending_count = 0
        completed_count = 0
        failed_count = 0
        other_count = 0

        for node_id, node in self.tree.nodes.items():
            status = self._map_db_status_to_execution_status(node.status)
            self._node_status[node_id] = status

            if status == NodeExecutionStatus.PENDING:
                pending_count += 1
            elif status == NodeExecutionStatus.COMPLETED:
                completed_count += 1
                record = self._load_node_record_from_db(node)
                if record:
                    self._node_records[node_id] = record
                    logger.debug(f"Node [{node_id}] loaded execution record from storage")
            elif status == NodeExecutionStatus.FAILED:
                failed_count += 1
            else:
                other_count += 1

            logger.debug(f"Node [{node_id}] {node.name}: status={status.value}")

        logger.info(
            "State initialization completed: pending=%s, completed=%s, failed=%s, other=%s",
            pending_count,
            completed_count,
            failed_count,
            other_count,
        )

    async def execute(self) -> PlanExecutionResult:
        """
        Execute the full plan DAG.

        Steps:
        1. Initialize in-memory state from persisted statuses.
        2. Repeatedly execute nodes that satisfy DAG/dependency constraints.
        3. Mark blocked remaining nodes as SKIPPED.
        4. Build and return execution summary.
        5. Finalize analysis report.

        Returns:
            PlanExecutionResult for this run.
        """
        logger.info(f"Executing plan DAG: {self.tree.title} (ID: {self.plan_id})")
        logger.info(f"Topological order: {self._topo_order}")
        started_at = datetime.now().isoformat()

        try:
            self._initialize_node_states()

            total_nodes = len(self._topo_order)
            pending = [
                node_id
                for node_id in self._topo_order
                if self._node_status.get(node_id) == NodeExecutionStatus.PENDING
            ]
            executed = 0

            while pending:
                progress = False
                for node_id in list(pending):
                    if not self._can_execute_node(node_id):
                        continue

                    executed += 1
                    logger.info(f"[{executed}/{total_nodes}] execute [{node_id}]")
                    await self._execute_single_node(node_id)
                    pending.remove(node_id)
                    progress = True

                if not progress:
                    logger.warning(
                        "No further executable nodes; marking remaining nodes as SKIPPED: %s",
                        pending,
                    )
                    for node_id in pending:
                        if self._node_status.get(node_id) == NodeExecutionStatus.PENDING:
                            self._node_status[node_id] = NodeExecutionStatus.SKIPPED
                            node = self.tree.nodes.get(node_id)
                            node_name = node.name if node else f"node_{node_id}"
                            self.repo.update_task(
                                plan_id=self.plan_id,
                                task_id=node_id,
                                status=NodeExecutionStatus.SKIPPED.value,
                                execution_result=json.dumps({
                                    "task_type": None,
                                    "code": None,
                                    "code_description": None,
                                    "code_output": None,
                                    "text_response": None,
                                    "generated_files": [],
                                    "has_visualization": False,
                                    "visualization_purpose": None,
                                    "visualization_analysis": None,
                                    "error": "Skipped: dependency or child constraints not satisfied",
                                }, ensure_ascii=False)
                            )
                            self._node_records[node_id] = NodeExecutionRecord(
                                node_id=node_id,
                                node_name=node_name,
                                status=NodeExecutionStatus.SKIPPED,
                                error_message="Skipped: dependency or child constraints not satisfied",
                            )
                    break

            completed_count = sum(1 for s in self._node_status.values() if s == NodeExecutionStatus.COMPLETED)
            failed_count = sum(1 for s in self._node_status.values() if s == NodeExecutionStatus.FAILED)
            skipped_count = sum(1 for s in self._node_status.values() if s == NodeExecutionStatus.SKIPPED)

            completed_at = datetime.now().isoformat()

            self._finalize_analysis_report(completed_count, failed_count, skipped_count)

            result = PlanExecutionResult(
                plan_id=self.plan_id,
                plan_title=self.tree.title,
                success=(failed_count == 0),
                total_nodes=len(self.tree.nodes),
                completed_nodes=completed_count,
                failed_nodes=failed_count,
                skipped_nodes=skipped_count,
                node_records=self._node_records,
                all_generated_files=self._all_generated_files,
                report_path=str(self._analysis_report_path),
                started_at=started_at,
                completed_at=completed_at
            )

            logger.info(
                "Plan execution completed: success=%s, completed=%s, failed=%s",
                result.success,
                completed_count,
                failed_count,
            )
            logger.info(f"Analysis report saved: {self._analysis_report_path}")

            return result

        finally:
            self.task_executor.cleanup()

    def _finalize_analysis_report(self, completed: int, failed: int, skipped: int):
        """Append final summary statistics to the analysis report."""
        summary = f"""

| Metric | Value |
|------|------|
| Total tasks | {len(self.tree.nodes)} |
| Completed | {completed} |
| Failed | {failed} |
| Skipped | {skipped} |

**Completed At**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
"""
        with open(self._analysis_report_path, 'a', encoding='utf-8') as f:
            f.write(summary)


# ============================================================
# ============================================================

async def execute_plan_async(
    plan_id: int,
    data_file_paths: List[str],
    output_dir: str = "./results",
    **kwargs
) -> PlanExecutionResult:
    """
    Async convenience wrapper for `PlanExecutorInterpreter.execute()`.

    Args:
        plan_id: Plan ID.
        data_file_paths: Input dataset paths.
        output_dir: Output directory for generated artifacts.
        **kwargs: Additional `PlanExecutorInterpreter` parameters.
    """
    executor = PlanExecutorInterpreter(
        plan_id=plan_id,
        data_file_paths=data_file_paths,
        output_dir=output_dir,
        **kwargs
    )
    return await executor.execute()


def execute_plan(
    plan_id: int,
    data_file_paths: List[str],
    output_dir: str = "./results",
    **kwargs
) -> PlanExecutionResult:
    """
    Sync convenience wrapper for `execute_plan_async`.

    Args:
        plan_id: Plan ID.
        data_file_paths: Input dataset paths.
        output_dir: Output directory for generated artifacts.
        **kwargs: Additional `PlanExecutorInterpreter` parameters.
    """
    return asyncio.run(execute_plan_async(
        plan_id=plan_id,
        data_file_paths=data_file_paths,
        output_dir=output_dir,
        **kwargs
    ))
