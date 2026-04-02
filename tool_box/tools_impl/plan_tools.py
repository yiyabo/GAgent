"""
Plan Operation Tool Implementation

This module provides plan creation and optimization functionality for DeepThink Agent.
Supports creating, reviewing, optimizing, and querying plans with iterative improvement.
"""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


async def plan_operation_handler(
    operation: str,
    title: Optional[str] = None,
    description: Optional[str] = None,
    tasks: Optional[List[Dict[str, Any]]] = None,
    plan_id: Optional[int] = None,
    changes: Optional[List[Dict[str, Any]]] = None,
    tool_context: Optional[Any] = None,
    **kwargs,
) -> Dict[str, Any]:
    """
    Plan operation tool handler for DeepThink Agent.

    Supports creating plans, reviewing them for issues, and optimizing based on feedback.

    Args:
        operation: Operation type - "create", "review", "optimize", "get"
        title: Plan title (for create)
        description: Plan description (for create)
        tasks: List of task definitions [{name, instruction, dependencies?}] (for create)
        plan_id: Plan ID (for review, optimize, get)
        changes: List of changes to apply [{task_id, action, ...}] (for optimize)
        tool_context: Execution context with session/plan binding info

    Returns:
        Dict containing operation result
    """
    # Session-plan isolation: if the session is bound to a plan, only allow
    # operations on that plan (except create, which creates a new one).
    if operation != "create" and plan_id is not None and tool_context is not None:
        bound_plan_id = getattr(tool_context, "plan_id", None)
        if bound_plan_id is not None and plan_id != bound_plan_id:
            logger.warning(
                "[PLAN_OP] Blocked cross-plan access: requested plan_id=%s but session bound to plan_id=%s",
                plan_id, bound_plan_id,
            )
            return {
                "success": False,
                "error": (
                    f"Cannot access plan {plan_id}: this session is bound to plan {bound_plan_id}. "
                    f"Use plan_id={bound_plan_id} or start a new session for a different plan."
                ),
                "operation": operation,
                "requested_plan_id": plan_id,
                "bound_plan_id": bound_plan_id,
            }

    try:
        if operation == "create":
            return await _create_plan(title, description, tasks)
        elif operation == "review":
            return await _review_plan(plan_id)
        elif operation == "optimize":
            return await _optimize_plan(plan_id, changes)
        elif operation == "get":
            return await _get_plan(plan_id)
        else:
            return {
                "success": False,
                "error": f"Unknown operation: {operation}. Supported: create, review, optimize, get",
            }
    except Exception as e:
        logger.exception(f"Plan operation '{operation}' failed: {e}")
        return {"success": False, "error": str(e), "operation": operation}


async def _create_plan(
    title: Optional[str],
    description: Optional[str],
    tasks: Optional[List[Dict[str, Any]]],
) -> Dict[str, Any]:
    """
    Create a new plan with initial tasks.
    
    Args:
        title: Plan title
        description: Plan description/goal
        tasks: List of task definitions [{name, instruction, dependencies?}]
        
    Returns:
        Created plan info with plan_id
    """
    if not title:
        return {"success": False, "error": "Plan title is required"}
    
    if not tasks or not isinstance(tasks, list):
        return {"success": False, "error": "Tasks list is required and must be non-empty"}
    
    try:
        from app.repository.plan_repository import PlanRepository
        
        repo = PlanRepository()
        
        # Create the plan
        plan_tree = repo.create_plan(
            title=title,
            description=description,
            metadata={"created_by": "deepthink_agent"},
        )
        plan_id = plan_tree.id
        
        # Create ROOT task first
        root_node = repo.create_task(
            plan_id,
            name=title,
            status="pending",
            instruction=description or f"Root task for plan: {title}",
            parent_id=None,
            metadata={"is_root": True, "task_type": "root"},
        )
        root_task_id = root_node.id
        
        # Create task ID mapping for dependency resolution
        task_name_to_id: Dict[str, int] = {}
        created_tasks: List[Dict[str, Any]] = []
        
        # First pass: create all tasks
        for idx, task_def in enumerate(tasks):
            task_name = task_def.get("name") or f"Task {idx + 1}"
            task_instruction = task_def.get("instruction") or task_def.get("description") or ""
            
            node = repo.create_task(
                plan_id,
                name=task_name,
                status="pending",
                instruction=task_instruction,
                parent_id=root_task_id,  # All top-level tasks are children of ROOT
                metadata={"task_type": "composite"},
            )
            
            task_name_to_id[task_name] = node.id
            created_tasks.append({
                "id": node.id,
                "name": task_name,
                "instruction": task_instruction[:100] + "..." if len(task_instruction) > 100 else task_instruction,
            })
        
        # Second pass: set up dependencies
        for task_def in tasks:
            task_name = task_def.get("name")
            dep_names = task_def.get("dependencies", [])
            
            if task_name and dep_names and task_name in task_name_to_id:
                task_id = task_name_to_id[task_name]
                dep_ids = [task_name_to_id[dep] for dep in dep_names if dep in task_name_to_id]
                
                if dep_ids:
                    repo.update_task(plan_id, task_id, dependencies=dep_ids)
        
        logger.info(f"Created plan {plan_id} with {len(created_tasks)} tasks")
        
        return {
            "success": True,
            "operation": "create",
            "plan_id": plan_id,
            "title": title,
            "root_task_id": root_task_id,
            "task_count": len(created_tasks),
            "created_tasks": created_tasks,
            "message": f"Successfully created plan '{title}' with {len(created_tasks)} tasks. Use review operation to check for issues.",
        }
        
    except Exception as e:
        logger.exception(f"Failed to create plan: {e}")
        return {"success": False, "error": f"Failed to create plan: {str(e)}"}


async def _review_plan(plan_id: Optional[int]) -> Dict[str, Any]:
    """
    Review a plan for potential issues.
    
    Checks:
    - Circular dependencies
    - Task granularity (too coarse or too fine)
    - Missing steps
    - Logical consistency
    
    Args:
        plan_id: ID of the plan to review
        
    Returns:
        Review report with issues and suggestions
    """
    if plan_id is None:
        return {"success": False, "error": "plan_id is required"}
    
    try:
        from app.repository.plan_repository import PlanRepository
        from app.services.plans.plan_rubric_evaluator import (
            evaluate_plan_rubric,
            is_rubric_evaluation_unavailable,
        )
        
        repo = PlanRepository()
        plan_tree = repo.get_plan_tree(plan_id)
        
        issues: List[Dict[str, Any]] = []
        suggestions: List[str] = []
        
        nodes = plan_tree.nodes
        
        # Check 1: Circular dependencies
        circular_deps = _detect_circular_dependencies(nodes)
        if circular_deps:
            for cycle in circular_deps:
                issues.append({
                    "type": "circular_dependency",
                    "severity": "high",
                    "description": f"Circular dependency detected: {' -> '.join(map(str, cycle))}",
                    "affected_tasks": cycle,
                })
        
        # Check 2: Task count and granularity
        root_tasks = [n for n in nodes.values() if n.parent_id is None]
        composite_tasks = [n for n in nodes.values() if n.parent_id is not None]
        leaf_tasks = [n for n in nodes.values() if not any(c.parent_id == n.id for c in nodes.values())]
        
        if len(composite_tasks) < 3:
            issues.append({
                "type": "too_few_tasks",
                "severity": "medium",
                "description": f"Plan has only {len(composite_tasks)} tasks, which may be too coarse-grained",
            })
            suggestions.append("Consider breaking down tasks into smaller, more specific subtasks")
        
        if len(composite_tasks) > 20:
            issues.append({
                "type": "too_many_tasks",
                "severity": "low",
                "description": f"Plan has {len(composite_tasks)} tasks, which may be too fine-grained",
            })
            suggestions.append("Consider grouping related tasks into composite tasks")
        
        # Check 3: Tasks without instructions
        tasks_without_instructions = [
            n for n in nodes.values() 
            if n.parent_id is not None and (not n.instruction or len(n.instruction.strip()) < 10)
        ]
        if tasks_without_instructions:
            issues.append({
                "type": "missing_instructions",
                "severity": "medium",
                "description": f"{len(tasks_without_instructions)} tasks have missing or very short instructions",
                "affected_tasks": [t.id for t in tasks_without_instructions],
            })
            suggestions.append("Add detailed instructions to each task explaining what needs to be done")
        
        # Check 4: Orphan dependencies (referencing non-existent tasks)
        all_task_ids = set(nodes.keys())
        for node in nodes.values():
            for dep_id in (node.dependencies or []):
                if dep_id not in all_task_ids:
                    issues.append({
                        "type": "orphan_dependency",
                        "severity": "high",
                        "description": f"Task {node.id} depends on non-existent task {dep_id}",
                        "affected_tasks": [node.id],
                    })
        
        # Check 5: Self-dependencies
        for node in nodes.values():
            if node.id in (node.dependencies or []):
                issues.append({
                    "type": "self_dependency",
                    "severity": "high",
                    "description": f"Task {node.id} ({node.name}) depends on itself",
                    "affected_tasks": [node.id],
                })
        
        # Calculate structural health score
        high_issues = len([i for i in issues if i.get("severity") == "high"])
        medium_issues = len([i for i in issues if i.get("severity") == "medium"])
        low_issues = len([i for i in issues if i.get("severity") == "low"])

        health_score = max(0, 100 - high_issues * 30 - medium_issues * 10 - low_issues * 5)

        # Generate structural review summary
        if high_issues > 0:
            structural_status = "critical"
            structural_summary = f"Plan has {high_issues} critical issues that must be fixed"
        elif medium_issues > 0:
            structural_status = "needs_improvement"
            structural_summary = f"Plan has {medium_issues} issues that should be addressed"
        elif low_issues > 0:
            structural_status = "acceptable"
            structural_summary = f"Plan is acceptable but has {low_issues} minor issues"
        else:
            structural_status = "good"
            structural_summary = "Plan looks well-structured with no detected issues"

        # -----------------------------
        # Rubric evaluation (strict, English)
        # -----------------------------
        rubric_result = evaluate_plan_rubric(
            plan_tree,
            evaluator_provider="qwen",
            evaluator_model="qwen3.5-plus",
        )

        # Persist evaluation into plan metadata (merge, do not overwrite).
        merged_meta: Dict[str, Any] = dict(getattr(plan_tree, "metadata", None) or {})
        created_by = merged_meta.get("created_by")
        if "plan_origin" not in merged_meta:
            merged_meta["plan_origin"] = (
                "deepthink" if created_by == "deepthink_agent" else "standard"
            )
        merged_meta["plan_evaluation"] = rubric_result.to_dict()
        try:
            repo.update_plan_metadata(plan_id, merged_meta)
        except Exception as meta_exc:  # noqa: BLE001 - best effort persistence
            logger.warning("Failed to persist plan rubric evaluation: %s", meta_exc)

        rubric_unavailable = is_rubric_evaluation_unavailable(rubric_result)
        status = "evaluation_unavailable" if rubric_unavailable else structural_status
        summary = (
            f"{structural_summary} Rubric evaluation is unavailable."
            if rubric_unavailable
            else structural_summary
        )
        message = (
            f"Review completed with degraded evaluator. Structural status: {structural_status}. "
            f"Health score: {health_score}/100. Rubric evaluation unavailable."
            if rubric_unavailable
            else (
                f"Review complete. Status: {structural_status}. "
                f"Health score: {health_score}/100. "
                f"Rubric score: {rubric_result.overall_score}/100."
            )
        )

        return {
            "success": True,
            "operation": "review",
            "plan_id": plan_id,
            "plan_title": plan_tree.title,
            "status": status,
            "structural_status": structural_status,
            "health_score": health_score,
            "structural_health_score": health_score,
            "rubric_score": rubric_result.overall_score,
            "rubric_dimension_scores": rubric_result.dimension_scores,
            "rubric_subcriteria_scores": rubric_result.subcriteria_scores,
            "rubric_feedback": rubric_result.feedback,
            "rubric_evaluator": {
                "provider": rubric_result.evaluator_provider,
                "model": rubric_result.evaluator_model,
                "rubric_version": rubric_result.rubric_version,
                "evaluated_at": rubric_result.evaluated_at,
            },
            "degraded": rubric_unavailable,
            "summary": summary,
            "total_tasks": len(nodes),
            "issues": issues,
            "suggestions": suggestions,
            "message": message,
        }
        
    except Exception as e:
        logger.exception(f"Failed to review plan: {e}")
        return {"success": False, "error": f"Failed to review plan: {str(e)}"}


def _detect_circular_dependencies(nodes: Dict[int, Any]) -> List[List[int]]:
    """Detect circular dependencies using DFS."""
    cycles: List[List[int]] = []
    visited = set()
    rec_stack = set()
    path: List[int] = []
    
    def dfs(node_id: int) -> bool:
        visited.add(node_id)
        rec_stack.add(node_id)
        path.append(node_id)
        
        node = nodes.get(node_id)
        if node:
            for dep_id in (node.dependencies or []):
                if dep_id not in visited:
                    if dfs(dep_id):
                        return True
                elif dep_id in rec_stack:
                    # Found cycle
                    cycle_start = path.index(dep_id)
                    cycles.append(path[cycle_start:] + [dep_id])
                    return True
        
        path.pop()
        rec_stack.remove(node_id)
        return False
    
    for node_id in nodes:
        if node_id not in visited:
            dfs(node_id)
    
    return cycles


_OPTIMIZE_ACTION_ALIASES: Dict[str, str] = {
    "rename": "update_task",
    "update": "update_task",
    "update_description": "update_description",
    "change_description": "update_description",
    "modify_description": "update_description",
    "update_plan_description": "update_description",
    "edit": "update_task",
    "change": "update_task",
    "modify": "update_task",
    "modify_task": "update_task",
    "rename_task": "update_task",
    "update_task_name": "update_task",
    "update_fields": "update_task",
    "change_type": "update_task",
    "add": "add_task",
    "create": "add_task",
    "create_task": "add_task",
    "delete": "delete_task",
    "remove": "delete_task",
    "remove_task": "delete_task",
    "reorder": "reorder_task",
    "move": "reorder_task",
    "move_task": "reorder_task",
}

_OPTIMIZE_VALID_ACTIONS = frozenset(
    {"add_task", "update_task", "update_description", "delete_task", "reorder_task"}
)


def _normalize_optimize_change(change: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize an optimize change dict so that common LLM format variations are accepted."""
    change = dict(change)

    # Flatten nested update payloads into top-level keys.
    for nested_key in ("updates", "fields", "updated_fields"):
        nested = change.pop(nested_key, None)
        if isinstance(nested, dict):
            for k, v in nested.items():
                change.setdefault(k, v)

    # Accept common aliases emitted by older prompts / models.
    alias_map = {
        "new_name": "name",
        "value": "name",
        "task_name": "name",
        "task_title": "name",
        "new_instruction": "instruction",
        "task_instruction": "instruction",
        "new_description": "description",
        "plan_description": "description",
    }
    for alias, canonical in alias_map.items():
        if alias in change and canonical not in change:
            change[canonical] = change.pop(alias)

    # Resolve action from "action", "type", or "change_type" fields.
    raw_action = change.get("action") or change.get("type") or change.get("change_type")
    if isinstance(raw_action, str):
        raw_action = raw_action.strip().lower()
    else:
        raw_action = None

    action = _OPTIMIZE_ACTION_ALIASES.get(raw_action, raw_action) if raw_action else None

    # Auto-infer action when still unknown.
    if action not in _OPTIMIZE_VALID_ACTIONS:
        has_task_id = change.get("task_id") is not None
        has_update_fields = any(k in change for k in ("name", "instruction", "dependencies"))
        has_new_position = change.get("new_position") is not None
        has_plan_description = change.get("description") is not None

        if has_task_id and has_new_position:
            action = "reorder_task"
        elif has_task_id and has_update_fields:
            action = "update_task"
        elif not has_task_id and has_plan_description and "name" not in change:
            action = "update_description"
        elif not has_task_id and "name" in change:
            action = "add_task"

    change["action"] = action
    return change


def _apply_plan_description_update(repo: Any, plan_id: int, description: str) -> None:
    """Persist plan.description and keep the synthetic ROOT task instruction in sync."""
    plan_tree = repo.get_plan_tree(plan_id)
    plan_tree.description = description

    for node in plan_tree.nodes.values():
        if node.parent_id is None:
            node.instruction = description
            break

    plan_tree.rebuild_adjacency()
    repo.upsert_plan_tree(plan_tree, note="optimize_plan_description")


async def _optimize_plan(
    plan_id: Optional[int],
    changes: Optional[List[Dict[str, Any]]],
) -> Dict[str, Any]:
    """
    Optimize a plan by applying changes.
    
    Supported change actions:
    - add_task: {action: "add_task", name, instruction, parent_id?, dependencies?}
    - update_task: {action: "update_task", task_id, name?, instruction?, dependencies?}
    - update_description: {action: "update_description", description}
    - delete_task: {action: "delete_task", task_id}
    - reorder_task: {action: "reorder_task", task_id, new_position}
    
    Args:
        plan_id: ID of the plan to optimize
        changes: List of changes to apply
        
    Returns:
        Optimization result
    """
    if plan_id is None:
        return {"success": False, "error": "plan_id is required"}
    
    if not changes or not isinstance(changes, list):
        return {"success": False, "error": "changes list is required"}
    
    try:
        from app.repository.plan_repository import PlanRepository
        
        repo = PlanRepository()
        
        # Verify plan exists
        plan_tree = repo.get_plan_tree(plan_id)
        
        applied_changes: List[Dict[str, Any]] = []
        failed_changes: List[Dict[str, Any]] = []
        
        for change in changes:
            change = _normalize_optimize_change(change)
            action = change.get("action")
            
            try:
                if action == "add_task":
                    task_name = str(change.get("name") or "").strip()
                    task_instruction = str(change.get("instruction") or "").strip()
                    if not task_name:
                        failed_changes.append({"change": change, "error": "name required for add_task"})
                        continue
                    if not task_instruction:
                        failed_changes.append({"change": change, "error": "instruction required for add_task"})
                        continue

                    # Find parent_id (default to root)
                    parent_id = change.get("parent_id")
                    if parent_id is None:
                        # Find root task
                        for node in plan_tree.nodes.values():
                            if node.parent_id is None:
                                parent_id = node.id
                                break
                    
                    node = repo.create_task(
                        plan_id,
                        name=task_name,
                        status="pending",
                        instruction=task_instruction,
                        parent_id=parent_id,
                        dependencies=change.get("dependencies"),
                    )
                    applied_changes.append({
                        "action": "add_task",
                        "task_id": node.id,
                        "name": node.name,
                    })
                    
                elif action == "update_task":
                    task_id = change.get("task_id")
                    if task_id is None:
                        failed_changes.append({"change": change, "error": "task_id required"})
                        continue
                    
                    update_kwargs = {}
                    if "name" in change:
                        update_kwargs["name"] = change["name"]
                    if "instruction" in change:
                        update_kwargs["instruction"] = change["instruction"]
                    if "dependencies" in change:
                        update_kwargs["dependencies"] = change["dependencies"]
                    
                    if update_kwargs:
                        repo.update_task(plan_id, task_id, **update_kwargs)
                        applied_changes.append({
                            "action": "update_task",
                            "task_id": task_id,
                            "updated_fields": list(update_kwargs.keys()),
                        })
                    else:
                        failed_changes.append({
                            "change": change,
                            "error": (
                                "No supported update fields provided for update_task. "
                                "Use top-level name/instruction/dependencies or nested updated_fields. "
                                "Do not use plan_operation optimize/update_task to mark the currently executing task completed/failed; "
                                "current task status is auto-synced from tool execution."
                            ),
                        })

                elif action == "update_description":
                    new_description = str(change.get("description") or "").strip()
                    if not new_description:
                        failed_changes.append({"change": change, "error": "description required for update_description"})
                        continue

                    _apply_plan_description_update(repo, plan_id, new_description)
                    applied_changes.append({
                        "action": "update_description",
                        "updated_fields": ["description"],
                    })
                    
                elif action == "delete_task":
                    task_id = change.get("task_id")
                    if task_id is None:
                        failed_changes.append({"change": change, "error": "task_id required"})
                        continue
                    
                    repo.delete_task(plan_id, task_id)
                    applied_changes.append({
                        "action": "delete_task",
                        "task_id": task_id,
                    })
                    
                elif action == "reorder_task":
                    task_id = change.get("task_id")
                    new_position = change.get("new_position")
                    if task_id is None or new_position is None:
                        failed_changes.append({"change": change, "error": "task_id and new_position required"})
                        continue
                    
                    repo.move_task(plan_id, task_id, new_position=new_position)
                    applied_changes.append({
                        "action": "reorder_task",
                        "task_id": task_id,
                        "new_position": new_position,
                    })
                    
                else:
                    failed_changes.append({
                        "change": change,
                        "error": (
                            f"Unknown action: {action}. "
                            f"Valid actions: add_task, update_task, update_description, delete_task, reorder_task. "
                            f'Example: {{"action": "update_task", "task_id": 4, "name": "New Name"}}'
                        ),
                    })
                    
            except Exception as e:
                failed_changes.append({
                    "change": change,
                    "error": str(e),
                })
        
        # Refresh plan tree
        plan_tree = repo.get_plan_tree(plan_id)
        
        if not applied_changes and not failed_changes:
            failed_changes.append(
                {
                    "change": None,
                    "error": "No valid optimize changes were applied.",
                }
            )

        return {
            "success": len(failed_changes) == 0 and len(applied_changes) > 0,
            "operation": "optimize",
            "plan_id": plan_id,
            "applied_changes": len(applied_changes),
            "failed_changes": len(failed_changes),
            "changes_detail": {
                "applied": applied_changes,
                "failed": failed_changes,
            },
            "current_task_count": len(plan_tree.nodes),
            "message": (
                f"Applied {len(applied_changes)} changes, {len(failed_changes)} failed. "
                + (
                    "Use review to check the result."
                    if applied_changes
                    else "No real plan updates were applied."
                )
            ),
        }
        
    except Exception as e:
        logger.exception(f"Failed to optimize plan: {e}")
        return {"success": False, "error": f"Failed to optimize plan: {str(e)}"}


async def _get_plan(plan_id: Optional[int]) -> Dict[str, Any]:
    """
    Get detailed plan information.
    
    Args:
        plan_id: ID of the plan to retrieve
        
    Returns:
        Plan details including all tasks
    """
    if plan_id is None:
        return {"success": False, "error": "plan_id is required"}
    
    try:
        from app.repository.plan_repository import PlanRepository
        
        repo = PlanRepository()
        plan_tree = repo.get_plan_tree(plan_id)
        
        # Build task hierarchy
        tasks_by_parent: Dict[Optional[int], List[Dict[str, Any]]] = {}
        
        for node in plan_tree.nodes.values():
            task_info = {
                "id": node.id,
                "name": node.name,
                "status": node.status,
                "instruction": node.instruction,
                "parent_id": node.parent_id,
                "dependencies": node.dependencies or [],
                "depth": node.depth,
            }
            
            parent_key = node.parent_id
            if parent_key not in tasks_by_parent:
                tasks_by_parent[parent_key] = []
            tasks_by_parent[parent_key].append(task_info)
        
        # Build tree structure for display
        def build_tree(parent_id: Optional[int], depth: int = 0) -> List[Dict[str, Any]]:
            children = tasks_by_parent.get(parent_id, [])
            result = []
            for child in sorted(children, key=lambda x: x.get("id", 0)):
                child["children"] = build_tree(child["id"], depth + 1)
                result.append(child)
            return result
        
        task_tree = build_tree(None)
        
        # Summary statistics
        total_tasks = len(plan_tree.nodes)
        pending_tasks = len([n for n in plan_tree.nodes.values() if n.status == "pending"])
        completed_tasks = len([n for n in plan_tree.nodes.values() if n.status in ("completed", "success")])
        
        return {
            "success": True,
            "operation": "get",
            "plan_id": plan_id,
            "title": plan_tree.title,
            "description": plan_tree.description,
            "total_tasks": total_tasks,
            "pending_tasks": pending_tasks,
            "completed_tasks": completed_tasks,
            "task_tree": task_tree,
            "message": f"Plan '{plan_tree.title}' has {total_tasks} tasks ({pending_tasks} pending, {completed_tasks} completed).",
        }
        
    except Exception as e:
        logger.exception(f"Failed to get plan: {e}")
        return {"success": False, "error": f"Failed to get plan: {str(e)}"}


# Tool definition for registration
plan_operation_tool = {
    "name": "plan_operation",
    "description": """Plan creation and optimization tool for DeepThink Agent.

Supports creating plans, reviewing them for issues, and optimizing based on feedback.
Use this tool to create well-structured plans with iterative improvement.

WORKFLOW for Plan Creation:
1. Use web_search first only when current external evidence or best practices materially affect the plan
2. Create initial plan with 'create' operation
3. Use 'review' to check dependencies and granularity
4. If issues found, use 'optimize' to fix them
5. Repeat review-optimize until plan is satisfactory

OPTIMIZE CHANGE FORMAT (each change MUST have an "action" field):
- update_task: {"action": "update_task", "task_id": 4, "name": "New Name", "instruction": "New details"}
- update_description: {"action": "update_description", "description": "New plan summary"}
- add_task: {"action": "add_task", "name": "Task Name", "instruction": "Details", "parent_id": 1}
- delete_task: {"action": "delete_task", "task_id": 5}
- reorder_task: {"action": "reorder_task", "task_id": 3, "new_position": 1}

Legacy compatibility:
- Nested `updated_fields` / `updates` / `fields` payloads are accepted and flattened.
- `task_name` / `task_instruction` aliases are accepted for add_task/update_task.""",
    "category": "planning",
    "parameters_schema": {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "description": "Operation type",
                "enum": ["create", "review", "optimize", "get"],
            },
            "title": {
                "type": "string",
                "description": "Plan title (required for create)",
            },
            "description": {
                "type": "string",
                "description": "Plan description/goal (for create)",
            },
            "tasks": {
                "type": "array",
                "description": "List of task definitions [{name, instruction, dependencies?}] (for create)",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Task name"},
                        "instruction": {"type": "string", "description": "Detailed task instruction"},
                        "dependencies": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Names of dependent tasks",
                        },
                    },
                    "required": ["name"],
                },
            },
            "plan_id": {
                "type": "integer",
                "description": "Plan ID (required for review, optimize, get)",
            },
            "changes": {
                "type": "array",
                "description": "List of changes to apply (for optimize)",
                "items": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": [
                                "add_task",
                                "update_task",
                                "update_description",
                                "delete_task",
                                "reorder_task",
                            ],
                        },
                        "task_id": {"type": "integer"},
                        "name": {"type": "string"},
                        "instruction": {"type": "string"},
                        "description": {"type": "string"},
                        "parent_id": {"type": "integer"},
                        "dependencies": {"type": "array", "items": {"type": "integer"}},
                        "new_position": {"type": "integer"},
                    },
                    "required": ["action"],
                },
            },
        },
        "required": ["operation"],
    },
    "handler": plan_operation_handler,
    "tags": ["planning", "task-management", "deepthink", "optimization"],
    "examples": [
        "Create a research plan for phage-host prediction",
        "Review plan #123 for dependency issues",
        "Optimize plan #123 by adding a data collection task",
        "Get details of plan #123",
    ],
}
