"""
Tool-Enhanced Executor

This module provides a tool-enhanced executor that integrates Tool Box capabilities
with the existing task execution system for superior performance.
"""

import asyncio
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from ...interfaces import TaskRepository
from ...repository.tasks import default_repo
from .base import execute_task as base_execute_task
from ...utils import split_prefix
from ...utils.task_path_generator import get_task_file_path, ensure_task_directory
from ..assemblers import CompositeAssembler, RootAssembler

logger = logging.getLogger(__name__)


class ToolEnhancedExecutor:
    """Tool-enhanced executor with intelligent tool integration"""

    def __init__(self, repo: Optional[TaskRepository] = None):
        self.repo = repo or default_repo
        self.tool_router = None
        self._initialized = False
        # Workspace base directory for file operations (relative paths)
        self.base_dir = os.environ.get("TOOL_WORKSPACE_DIR", "workspace")
        try:
            Path(self.base_dir).mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    async def initialize(self):
        """Initialize the tool-enhanced executor"""
        if self._initialized:
            return

        try:
            from tool_box import get_smart_router

            # Reuse the global router instance to avoid repeated initialisation
            if self.tool_router is None:
                self.tool_router = await get_smart_router()
            self._initialized = True
            logger.info("Tool-enhanced executor initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize tool-enhanced executor: {e}")
            raise

    def _slugify(self, text: str) -> str:
        try:
            s = re.sub(r"\s+", "_", text.strip())
            s = re.sub(r"[^0-9A-Za-z_\-]+", "_", s)
            return s.strip("_") or "plan"
        except Exception:
            return "plan"

    def _default_report_path(self, task_name: str, fallback_filename: str = "report.md", task=None) -> str:
        """Build a path based on task hierarchy (ROOT → COMPOSITE → ATOMIC)"""
        # When a task object is provided, derive the hierarchical path
        if task:
            try:
                from app.utils.task_path_generator import get_task_file_path, ensure_task_directory
                file_path = get_task_file_path(task, self.repo)
                ensure_task_directory(file_path)
                return file_path
            except Exception as e:
                logger.warning(f"Failed to generate hierarchical path: {e}, falling back to default")
        
        # Fallback to original logic
        try:
            plan_title, _short = split_prefix(task_name or "")
        except Exception:
            plan_title = ""
        slug = self._slugify(plan_title or (task_name or "report"))
        base = Path("results") / "reports" / slug
        try:
            base.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        # normalize provided filename
        fname = fallback_filename or "report.md"
        if not os.path.splitext(fname)[1]:
            fname = f"{fname}.md"
        return str(base / fname)

    async def execute_task(
        self, task, use_context: bool = True, context_options: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Execute task with tool enhancement

        Args:
            task: Task to execute
            use_context: Whether to use context
            context_options: Context configuration options

        Returns:
            Execution status
        """
        if not self._initialized:
            await self.initialize()

        try:
            # Get task information
            task_id = task.get("id") if isinstance(task, dict) else task[0]
            task_name = task.get("name") if isinstance(task, dict) else task[1]

            # Get task prompt for tool analysis
            task_prompt = self.repo.get_task_input_prompt(task_id)
            if not task_prompt:
                task_prompt = f"Complete the task: {task_name}"

            logger.info(f"Analyzing task {task_id} for tool requirements")

            # Phase 1: Analyze if task needs external tools
            needs_tools, tool_analysis = await self._analyze_tool_requirements(task_prompt, task)

            if needs_tools and self.tool_router:
                logger.info(f"Task {task_id} requires external tools, proceeding with tool-enhanced execution")

                # Phase 2: Intelligent tool routing
                routing_context = await self._build_routing_context(task, context_options)
                logger.info(
                    "Tool routing start: task_id=%s, context_enabled=%s",
                    task_id,
                    routing_context.get("context_enabled"),
                )
                routing_result = await self.tool_router.route_request(task_prompt, routing_context)

                # Phase 3: Execute INFORMATION GATHERING tools only (not output tools)
                info_gathering_tools = []
                output_tools = []

                for call in routing_result.get("tool_calls", []):
                    tool = call.get("tool_name")
                    params = call.get("parameters", {}) or {}
                    if tool in ["web_search", "database_query"]:
                        info_gathering_tools.append(call)
                    elif tool == "file_operations":
                        op = str(params.get("operation", "")).lower()
                        # Read-only ops as information gathering; mutating ops as post-generation
                        if op in {"read", "list", "exists", "info"}:
                            info_gathering_tools.append(call)
                        else:
                            output_tools.append(call)

                # Heuristic/Control: ensure we save generated doc if requested or likely needed
                def _looks_like_doc_generation(name: str, prompt: str) -> bool:
                    text = f"{name}\n{prompt}".lower()
                    return any(k in text for k in [
                        "report", "markdown", ".md", "write", "generate", "save", "document", "draft"
                    ])

                force_save = bool((context_options or {}).get("force_save_output"))
                custom_filename = (context_options or {}).get("output_filename")
                
                # Auto-save ATOMIC task output into the hierarchical file structure
                task_type = task.get("task_type") if isinstance(task, dict) else (task[7] if len(task) > 7 else "atomic")
                is_atomic = task_type == "atomic"
                
                if not output_tools and (force_save or is_atomic or _looks_like_doc_generation(task_name or "", task_prompt or "")):
                    # Build unified path using task hierarchy
                    default_path = self._default_report_path(task_name or "report", custom_filename or "", task=task)
                    logger.info(f"🗂️ Auto-save enabled for {task_type} task → {default_path}")
                    output_tools.append(
                        {
                            "tool_name": "file_operations",
                            "parameters": {"operation": "write", "path": default_path, "content": ""},
                            "reasoning": f"Auto-save {task_type} task output to hierarchical structure",
                            "execution_order": 999,
                        }
                    )

                # Execute information gathering tools first
                tool_outputs = []
                if info_gathering_tools:
                    tool_outputs = await self._execute_tool_calls(info_gathering_tools, task)
                    logger.info("Tool info calls executed: %d", len(tool_outputs))

                # Phase 4: Enhance context with tool results
                enhanced_context_options = await self._enhance_context_with_tools(
                    context_options, tool_outputs, tool_analysis
                )

                # Phase 5: Execute LLM with enhanced context to generate content
                logger.info(f"Generating content with enhanced context for task {task_id}")
                if enhanced_context_options.get("tool_enhanced"):
                    logger.info(f"Tool context available: {len(enhanced_context_options.get('combined', ''))} chars")

                # Phase 5: Execute LLM with enhanced context using the evaluation-driven executor
                # This ensures that the context gathered by tools is actually used for generation.
                from .enhanced import execute_task_with_evaluation
                result_obj = execute_task_with_evaluation(
                    task,
                    repo=self.repo,
                    use_context=True,
                    context_options=enhanced_context_options
                )
                status = result_obj.status

                # Phase 5.5: One-time theme consistency check and correction (if needed)
                if status in ("done", "completed"):
                    try:
                        corrected = await self._check_and_correct_theme(task, task_id)
                        if corrected:
                            logger.info(f"✅ Theme correction applied for task {task_id}")
                    except Exception as e:
                        logger.warning(f"Theme consistency check failed: {e}")

                # Phase 6: Execute output tools AFTER content generation
                if output_tools and status in ("done", "completed"):
                    saved = await self._execute_post_generation_tools(output_tools, task, task_id)
                    if saved:
                        logger.info("Saved generated artifacts: %s", ", ".join(saved))

                # Phase 7: Record tool usage for learning
                await self._record_tool_usage(task, routing_result, tool_outputs, status)

                # Phase 8: Ensure status and auto-assemble upwards when eligible
                if status in ("done", "completed"):
                    try:
                        self.repo.update_task_status(task_id, "done")
                    except Exception:
                        pass
                    # Trigger composite/root assembly if conditions are met
                    await self._maybe_assemble_upstream(task_id)

                logger.info(f"Tool-enhanced execution completed for task {task_id}")
                return status
            else:
                # Fallback to standard execution, but still materialize ATOMIC output
                logger.info(f"Task {task_id} does not require tools, using standard execution with materialization")
                status = base_execute_task(task, use_context=use_context, context_options=context_options)
                try:
                    await self._materialize_atomic_output(task)
                except Exception:
                    pass
                return status

        except Exception as e:
            logger.error(f"Tool-enhanced execution failed for task {task_id}: {e}")
            # Fallback to standard execution on error, still materialize ATOMIC output
            status = base_execute_task(task, use_context=use_context, context_options=context_options)
            try:
                await self._materialize_atomic_output(task)
            except Exception:
                pass
            return status

    async def _analyze_tool_requirements(self, task_prompt: str, task) -> tuple[bool, Dict[str, Any]]:
        """Always use LLM SmartRouter for routing; no keyword/regex heuristics."""
        try:
            return True, {"llm_routing": True}
        except Exception as e:
            logger.warning(f"Tool requirement analysis failed: {e}")
            return True, {"llm_routing": True, "warning": str(e)}

    async def _build_routing_context(self, task, context_options) -> Dict[str, Any]:
        """Build context for tool routing"""
        task_id = task.get("id") if isinstance(task, dict) else task[0]
        task_name = task.get("name") if isinstance(task, dict) else task[1]

        routing_context = {
            "task_id": task_id,
            "task_name": task_name,
            "task_type": task.get("task_type", "atomic"),
            "depth": task.get("depth", 0),
            "context_enabled": bool(context_options),
        }

        # Add dependency information if available
        try:
            dependencies = self.repo.list_dependencies(task_id)
            if dependencies:
                routing_context["dependencies"] = [
                    {"id": dep.get("id"), "name": dep.get("name"), "kind": dep.get("kind")}
                    for dep in dependencies[:3]  # Limit to avoid context overflow
                ]
        except Exception:
            pass

        return routing_context

    async def _execute_tool_calls(self, tool_calls: List[Dict[str, Any]], task) -> List[Dict[str, Any]]:
        """Execute tool calls and collect results"""
        from tool_box import execute_tool

        tool_outputs = []
        task_id = task.get("id") if isinstance(task, dict) else task[0]

        for call in tool_calls:
            try:
                logger.info(f"Executing tool {call['tool_name']} for task {task_id}")
                # Normalize file paths for file_operations
                params = dict(call.get("parameters", {}) or {})
                if call.get("tool_name") == "file_operations":
                    op = str(params.get("operation", "").lower())

                    # Path normalization for relative paths
                    def _norm_path(p: Optional[str]) -> Optional[str]:
                        if not p:
                            return None
                        try:
                            if os.path.isabs(p):
                                return p
                            # Keep project results/ paths relative to project root
                            raw = str(p).replace("\\", "/")
                            if raw.startswith("results/") or raw.startswith("./results/"):
                                return raw
                            # Otherwise, route into workspace sandbox
                            return str(Path(self.base_dir, p))
                        except Exception:
                            return p

                    if "path" in params:
                        params["path"] = _norm_path(params.get("path")) or str(Path(self.base_dir))
                    if "destination" in params and params.get("destination"):
                        params["destination"] = _norm_path(params.get("destination"))
                    # For list op with empty path, default to workspace dir
                    if op == "list" and not params.get("path"):
                        params["path"] = str(Path(self.base_dir))

                result = await execute_tool(call["tool_name"], **params)

                tool_outputs.append(
                    {
                        "tool_name": call["tool_name"],
                        "parameters": params,
                        "result": result,
                        "reasoning": call.get("reasoning", ""),
                        "execution_order": call.get("execution_order", 999),
                        "success": True,
                    }
                )

                logger.info(f"Tool {call['tool_name']} executed successfully for task {task_id}")

            except Exception as e:
                logger.error(f"Tool {call['tool_name']} execution failed for task {task_id}: {e}")

                tool_outputs.append(
                    {
                        "tool_name": call["tool_name"],
                        "parameters": call["parameters"],
                        "error": str(e),
                        "success": False,
                    }
                )

        return tool_outputs

    async def _enhance_context_with_tools(
        self,
        context_options: Optional[Dict[str, Any]],
        tool_outputs: List[Dict[str, Any]],
        tool_analysis: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Enhance context options with tool results"""
        enhanced_options = dict(context_options) if context_options else {}

        if not tool_outputs:
            return enhanced_options

        # Format tool results for context
        tool_context_parts = []

        for output in tool_outputs:
            if output.get("success"):
                tool_name = output["tool_name"]
                result = output["result"]

                if tool_name == "web_search":
                    # Format search results
                    if isinstance(result, dict) and result.get("results"):
                        search_summary = f"Search query: {result.get('query', 'N/A')}\n"
                        search_summary += f"Found {len(result['results'])} results:\n"

                        for i, item in enumerate(result["results"][:3], 1):
                            search_summary += f"{i}. {item.get('title', 'No Title')}\n"
                            search_summary += f"   {item.get('snippet', 'No snippet')[:100]}...\n"

                        tool_context_parts.append(f"## Web search results\n\n{search_summary}")

                elif tool_name == "database_query":
                    # Format database results
                    if isinstance(result, dict) and result.get("success"):
                        db_summary = f"Database query: {result.get('sql', 'N/A')}\n"
                        db_summary += f"Returned {result.get('row_count', 0)} rows\n"

                        rows = result.get("rows", [])
                        if rows:
                            db_summary += "Sample data:\n"
                            for row in rows[:3]:
                                db_summary += f"  {str(row)}\n"

                        tool_context_parts.append(f"## Database query results\n\n{db_summary}")

                elif tool_name == "file_operations":
                    # Format file operation results
                    if isinstance(result, dict) and result.get("success"):
                        op = result.get("operation", "unknown")
                        path = result.get("path", "N/A")

                        if op == "read":
                            content = result.get("content", "")[:500]  # Limit content length
                            file_summary = f"File read ({path}):\n{content}..."
                        else:
                            file_summary = f"File operation {op} succeeded on {path}"

                        tool_context_parts.append(f"## File operation results\n\n{file_summary}")

        # Add tool context to existing context options
        if tool_context_parts:
            tool_context = "\n\n".join(tool_context_parts)

            # CRITICAL FIX: Properly integrate tool context into the context system
            # The base executor expects context in the bundle format
            enhanced_options["tool_enhanced"] = True

            # Add tool context as additional context sections
            existing_sections = enhanced_options.get("sections", [])

            # Create tool context sections
            tool_sections = []
            for i, part in enumerate(tool_context_parts):
                tool_sections.append(
                    {
                        "task_id": f"tool_{i}",
                        "name": f"Tool Result {i+1}",
                        "short_name": f"Tool_{i+1}",
                        "kind": "tool_result",
                        "content": part,
                    }
                )

            # Merge with existing sections
            all_sections = existing_sections + tool_sections
            enhanced_options["sections"] = all_sections

            # Rebuild combined context
            combined_parts = []
            for section in all_sections:
                header = section.get("short_name") or section.get("name", "Unknown")
                content = section.get("content", "")
                combined_parts.append(f"## {header}\n\n{content}")

            # Add tool context to combined
            enhanced_options["combined"] = "\n\n".join(combined_parts)

            enhanced_options["tool_summary"] = {
                "total_tools_used": len(tool_outputs),
                "successful_tools": len([o for o in tool_outputs if o.get("success")]),
                "analysis": tool_analysis,
            }

        return enhanced_options

    async def _record_tool_usage(
        self, task, routing_result: Dict[str, Any], tool_outputs: List[Dict[str, Any]], status: str
    ):
        """Record tool usage for future learning and analysis"""
        try:
            task_id = task.get("id") if isinstance(task, dict) else task[0]

            # Create tool usage record
            usage_record = {
                "task_id": task_id,
                "routing_method": routing_result.get("routing_method", "pure_llm"),
                "confidence": routing_result.get("confidence", 0.0),
                "tools_used": len(tool_outputs),
                "successful_tools": len([o for o in tool_outputs if o.get("success")]),
                "execution_status": status,
                "tool_effectiveness": self._calculate_tool_effectiveness(tool_outputs, status),
            }

            # Log for analysis (could be stored in database for learning)
            logger.info(f"Tool usage recorded for task {task_id}: {usage_record}")

            # Store in database if repo supports it
            if hasattr(self.repo, "store_tool_usage_log"):
                try:
                    self.repo.store_tool_usage_log(task_id, usage_record)
                except Exception as e:
                    logger.warning(f"Failed to store tool usage log: {e}")

        except Exception as e:
            logger.warning(f"Failed to record tool usage: {e}")

    def _calculate_tool_effectiveness(self, tool_outputs: List[Dict[str, Any]], status: str) -> float:
        """Calculate tool usage effectiveness score"""
        if not tool_outputs:
            return 0.0

        successful_tools = len([o for o in tool_outputs if o.get("success")])
        total_tools = len(tool_outputs)

        # Base effectiveness on tool success rate and task completion
        tool_success_rate = successful_tools / total_tools
        task_success_bonus = 0.2 if status == "done" else 0.0

        return min(tool_success_rate + task_success_bonus, 1.0)

    async def _execute_post_generation_tools(self, output_tools: List[Dict[str, Any]], task, task_id: int) -> List[str]:
        """Execute tools that need the generated content (like file_operations). Returns list of saved file paths."""
        try:
            # Get the generated content
            generated_content = self.repo.get_task_output_content(task_id)
            if not generated_content:
                logger.warning(f"No content available for post-generation tools in task {task_id}")
                return []

            logger.info(f"Executing {len(output_tools)} post-generation tools for task {task_id}")

            from tool_box import execute_tool

            saved_paths: List[str] = []
            for call in output_tools:
                try:
                    tool_name = call["tool_name"]
                    parameters = call["parameters"].copy()  # Don't modify original

                    if tool_name == "file_operations":
                        # For file operations, use the generated content
                        if parameters.get("operation") == "write":
                            # Update content with actual generated content
                            parameters["content"] = generated_content

                            # Improve file path if needed: unify under results/reports/{plan-slug}/...
                            path_in = (parameters.get("path") or "").strip()
                            if not path_in or path_in in ["report.txt", "report.md", ""]:
                                fallback = f"report_{task_id}.md"
                                parameters["path"] = self._default_report_path(task.get("name", "report"), fallback)
                            else:
                                # If given a bare filename or relative simple path, prefix our unified directory
                                dn = os.path.dirname(path_in)
                                if not dn or dn in {".", "./"}:
                                    parameters["path"] = self._default_report_path(task.get("name", "report"), os.path.basename(path_in))

                    logger.info(f"Executing post-generation tool {tool_name} for task {task_id}")
                    result = await execute_tool(tool_name, **parameters)

                    if isinstance(result, dict) and result.get("success"):
                        logger.info(f"✅ Post-generation tool {tool_name} executed successfully")
                        if tool_name == "file_operations" and result.get("operation") == "write":
                            path = result.get("path", "unknown")
                            saved_paths.append(path)
                            logger.info(f"📁 Report saved to: {path}")
                    else:
                        logger.warning(f"⚠️ Post-generation tool {tool_name} had issues: {result}")

                except Exception as e:
                    logger.error(f"❌ Post-generation tool {call['tool_name']} failed: {e}")

            return saved_paths

        except Exception as e:
            logger.error(f"Failed to execute post-generation tools: {e}")
            return []

    async def _materialize_atomic_output(self, task) -> None:
        """Ensure ATOMIC task output is written to its .md path from DB output, even if base executor was used."""
        try:
            task_id = task.get("id") if isinstance(task, dict) else task[0]
            task_info = task if isinstance(task, dict) else self.repo.get_task_info(task_id)
            if not task_info:
                return
            task_type = task_info.get("task_type") if isinstance(task_info, dict) else None
            if task_type != "atomic":
                return
            content = self.repo.get_task_output_content(task_id)
            if not content:
                return
            file_path = get_task_file_path(task_info, self.repo)
            ensure_task_directory(file_path)
            try:
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(content)
                logger.info(f"Materialized atomic output to {file_path}")
            except Exception as io_err:
                logger.warning(f"Failed to write atomic output to {file_path}: {io_err}")
        except Exception as e:
            logger.warning(f"_materialize_atomic_output failed: {e}")

    async def _maybe_assemble_upstream(self, task_id: int) -> None:
        """If an ATOMIC task just finished, assemble its composite and possibly root when eligible."""
        try:
            task = self.repo.get_task_info(task_id)
            if not task or (task.get("task_type") != "atomic"):
                return

            # Check composite parent
            parent = self.repo.get_parent(task_id)
            if not parent or parent.get("task_type") != "composite":
                return

            # All atomic children done/completed?
            children = self.repo.get_children(parent["id"])
            if not children:
                return
            all_atomic_done = True
            for ch in children:
                if ch.get("task_type") == "atomic" and ch.get("status") not in {"done", "completed"}:
                    all_atomic_done = False
                    break
            if not all_atomic_done:
                return

            # Assemble composite summary (always re-assemble to capture latest changes)
            # This ensures that if any atomic task is re-executed, the composite summary is updated
            try:
                CompositeAssembler(self.repo).assemble(parent["id"], strategy="llm", force_real=True)
                logger.info("Composite %s (re-)assembled from all atomic children", parent.get("id"))
            except Exception as e:
                logger.warning("Composite assembly failed for %s: %s", parent.get("id"), e)
                return

            # Check root readiness
            root_parent = self.repo.get_parent(parent["id"])
            if not root_parent or root_parent.get("task_type") != "root":
                return
            comps = self.repo.get_children(root_parent["id"])
            if comps and all((c.get("task_type") != "composite" or c.get("status") == "completed") for c in comps):
                try:
                    RootAssembler(self.repo).assemble(root_parent["id"], strategy="llm", force_real=True)
                    logger.info("Root %s assembled from all composite children", root_parent.get("id"))
                except Exception as e:
                    logger.warning("Root assembly failed for %s: %s", root_parent.get("id"), e)
        except Exception as e:
            logger.warning("maybe_assemble_upstream failed: %s", e)

    async def _check_and_correct_theme(self, task, task_id: int) -> bool:
        """Check theme consistency and perform one-time correction if needed. Returns True if corrected."""
        try:
            # Get generated content
            content = self.repo.get_task_output_content(task_id)
            if not content or len(content) < 100:
                return False
            
            # Get root task info
            root = self._find_root_for_correction(task_id)
            if not root:
                return False
            
            root_name = root.get("name", "")
            root_prompt = self.repo.get_task_input_prompt(root.get("id")) or ""
            
            # Build consistency check prompt
            check_prompt = f"""Evaluate whether the following content stays aligned with the ROOT topic. Provide a score (0-10) and a brief explanation.

[ROOT TOPIC] {root_name}
[Primary Goal] {root_prompt[:500]}

[Generated Content]
{content[:1500]}

Respond in JSON:
{{
  "score": <integer 0-10>,
  "on_topic": <true/false>,
  "reasoning": "<brief explanation>",
  "suggestions": "<correction advice if off-topic>"
}}"""
            
            # Call LLM for consistency check
            from app.services.llm.llm_service import get_llm_service
            llm = get_llm_service()
            check_result = await llm.generate_async(check_prompt, temperature=0.1, max_tokens=500)
            
            # Parse result
            import json
            import re
            json_match = re.search(r'\{[^{}]*"score"[^{}]*\}', check_result, re.DOTALL)
            if not json_match:
                return False
            
            result = json.loads(json_match.group())
            score = result.get("score", 10)
            on_topic = result.get("on_topic", True)
            
            # If score < 7 or explicitly off-topic, perform one-time correction
            if score < 7 or not on_topic:
                logger.warning(f"Task {task_id} theme drift detected (score={score}), performing correction")
                suggestions = result.get("suggestions", "")
                
                # Build correction prompt
                correction_prompt = f"""The content below drifts away from the ROOT topic. Rewrite it using the suggestions so it stays on topic.

[ROOT TOPIC] {root_name}
[Primary Goal] {root_prompt[:500]}

[Original Content]
{content[:1500]}

[Suggestions]
{suggestions}

When rewriting, make sure to:
1. Anchor the narrative to the ROOT topic and primary goal.
2. Keep the existing structure and format.
3. Add any missing topic-relevant points.
4. Remove unrelated content.

Rewritten content:"""
                
                # Generate corrected content
                corrected = await llm.generate_async(correction_prompt, temperature=0.3, max_tokens=2000)
                
                # Update task output
                self.repo.upsert_task_output(task_id, corrected)
                logger.info(f"Task {task_id} content corrected (original score: {score})")
                return True
            
            return False
            
        except Exception as e:
            logger.warning(f"Theme consistency check failed for task {task_id}: {e}")
            return False
    
    def _find_root_for_correction(self, task_id: int) -> Optional[Dict[str, Any]]:
        """Find root task for theme correction"""
        try:
            current = self.repo.get_task_info(task_id)
            guard = 0
            while current and guard < 100:
                if current.get("task_type") == "root":
                    return current
                parent = self.repo.get_parent(current.get("id"))
                if not parent:
                    break
                current = parent
                guard += 1
        except Exception:
            pass
        return None


# Convenience function for enhanced execution
async def execute_task_with_tools(
    task,
    repo: Optional[TaskRepository] = None,
    use_context: bool = True,
    context_options: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Execute task with tool enhancement

    This is the main entry point for tool-enhanced task execution.
    """
    executor = ToolEnhancedExecutor(repo)
    return await executor.execute_task(task, use_context, context_options)


# Integration helper for existing codebase
def create_tool_enhanced_context_options(base_options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Create enhanced context options that work well with tools"""
    options = dict(base_options) if base_options else {}

    # Optimize for tool integration
    options.update(
        {
            "include_deps": True,  # Tools can benefit from dependency context
            "include_plan": True,  # Plan context helps tool selection
            "semantic_k": 3,  # Reduce semantic retrieval to save space for tool results
            "max_chars": 9000,  # Increase limit to accommodate tool results
            "per_section_max": 1500,  # Allow larger sections for tool outputs
            "strategy": "sentence",  # Better summarization for tool integration
        }
    )

    return options


# Backward compatibility wrapper
def execute_task_enhanced(task, repo=None, use_context=True, context_options=None):
    """Synchronous wrapper for backward compatibility"""
    try:
        # Always run in new event loop for compatibility with existing sync code
        try:
            # Check if there's an existing loop
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No event loop exists, we can create one
            loop = None

        if loop and loop.is_running():
            # We're in an async context - this is problematic for sync compatibility
            # Fall back to base execution to avoid blocking
            logger.warning("Cannot run async tool execution in running event loop, using base execution")
            return base_execute_task(task, repo, use_context, context_options)
        else:
            # We're not in an async context, safe to run
            return asyncio.run(execute_task_with_tools(task, repo, use_context, context_options))
    except Exception as e:
        logger.error(f"Enhanced execution failed, falling back to base execution: {e}")
        # Fallback to base execution
        return base_execute_task(task, repo, use_context, context_options)


# New: combined path — tool-enhanced context + evaluation-driven execution
async def execute_task_with_tools_and_evaluation(
    task,
    repo: Optional[TaskRepository] = None,
    evaluation_mode: str = "llm",  # 'llm' | 'multi_expert' | 'adversarial'
    max_iterations: int = 3,
    quality_threshold: float = 0.8,
    use_context: bool = True,
    context_options: Optional[Dict[str, Any]] = None,
):
    """
    Execute with tool-enhanced context, then run evaluation-driven generation.

    Steps:
    1) Analyze tool needs and run info-gathering tools
    2) Enhance context options with tool outputs
    3) Run evaluation loop (LLM/multi-expert/adversarial)
    4) Execute post-generation output tools (e.g., write file)
    """
    repo = repo or default_repo

    # Initialize router and prepare tool-enhanced context
    executor = ToolEnhancedExecutor(repo)
    await executor.initialize()

    # Get task basic info
    task_id = task.get("id") if isinstance(task, dict) else task[0]
    task_name = task.get("name") if isinstance(task, dict) else task[1]

    # Build task prompt
    task_prompt = repo.get_task_input_prompt(task_id)
    if not task_prompt:
        task_prompt = f"Complete the task: {task_name}"

    # Phase 1: Analyze and route
    try:
        needs_tools, tool_analysis = await executor._analyze_tool_requirements(task_prompt, task)
    except Exception:
        needs_tools, tool_analysis = (False, {})

    info_gathering_tools = []
    output_tools = []
    routing_result = {"tool_calls": []}
    routing_failed = False

    if needs_tools and executor.tool_router:
        routing_context = await executor._build_routing_context(task, context_options)
        try:
            routing_result = await executor.tool_router.route_request(task_prompt, routing_context)
        except Exception as e:
            logger.warning(f"Tool routing failed, continue without tools: {e}")
            routing_result = {"tool_calls": []}
            routing_failed = True

        for call in routing_result.get("tool_calls", []):
            if call.get("tool_name") in ["web_search", "database_query"]:
                info_gathering_tools.append(call)
            elif call.get("tool_name") == "file_operations":
                output_tools.append(call)

    # Heuristic: if user intent looks like "generate report/document/markdown", ensure we save output even
    # when router didn't plan a file write.
    def _looks_like_doc_generation(name: str, prompt: str) -> bool:
        text = f"{name}\n{prompt}".lower()
        keywords = [
            "report", "markdown", ".md", "write", "generate", "document", "save"
        ]
        return any(k in text for k in keywords)

    if not output_tools:
        tp_lower = (task_prompt or "").lower()
        force_save = bool((context_options or {}).get("force_save_output"))
        custom_filename = (context_options or {}).get("output_filename")
        
        # Auto-save ATOMIC tasks
        task_type = task.get("task_type") if isinstance(task, dict) else (task[7] if len(task) > 7 else "atomic")
        is_atomic = task_type == "atomic"
        
        if force_save or is_atomic or _looks_like_doc_generation(task_name or "", tp_lower):
            # Create a synthetic file write action to persist the generated content with hierarchical path
            default_path = executor._default_report_path(task_name or "report", custom_filename or "", task=task)
            logger.info(f"🗂️ Auto-save enabled for {task_type} task → {default_path}")
            output_tools.append(
                {
                    "tool_name": "file_operations",
                    "parameters": {"operation": "write", "path": default_path, "content": ""},
                    "reasoning": f"Auto-save {task_type} task output to hierarchical structure",
                    "execution_order": 999,
                }
            )

    # Phase 2: Execute info-gathering tools
    tool_outputs = []
    if info_gathering_tools:
        tool_outputs = await executor._execute_tool_calls(info_gathering_tools, task)

    # Phase 3: Enhance context with tool results
    enhanced_context_options = await executor._enhance_context_with_tools(context_options, tool_outputs, tool_analysis)

    # Phase 4: Evaluation-driven generation
    from .enhanced import execute_task_with_adversarial_evaluation as _exec_adv
    from .enhanced import execute_task_with_evaluation as _exec_eval
    from .enhanced import execute_task_with_multi_expert_evaluation as _exec_multi

    mode = (evaluation_mode or "llm").strip().lower()
    if mode == "multi_expert":
        result = _exec_multi(
            task=task,
            repo=repo,
            max_iterations=max_iterations,
            quality_threshold=quality_threshold,
            use_context=use_context,
            context_options=enhanced_context_options,
        )
    elif mode == "adversarial":
        result = _exec_adv(
            task=task,
            repo=repo,
            max_iterations=max_iterations,
            quality_threshold=quality_threshold,
            use_context=use_context,
            context_options=enhanced_context_options,
        )
    else:  # default LLM evaluation
        result = _exec_eval(
            task=task,
            repo=repo,
            max_iterations=max_iterations,
            quality_threshold=quality_threshold,
            use_context=use_context,
            context_options=enhanced_context_options,
        )

    # Phase 5: Post-generation output tools
    saved_files: List[str] = []
    if output_tools and getattr(result, "status", None) in ("done", "completed"):
        try:
            saved_files = await executor._execute_post_generation_tools(output_tools, task, task_id)
        except Exception as e:
            logger.warning(f"Post-generation tools failed: {e}")
    # Attach tool usage metadata for summary
    try:
        result.metadata = result.metadata or {}
        result.metadata.update(
            {
                "tool_enhanced": True,
                "tool_routing_failed": routing_failed,
                "tool_calls": {
                    "info_planned": len(info_gathering_tools),
                    "output_planned": len(output_tools),
                    "workspace": executor.base_dir,
                },
                "saved_files": saved_files,
            }
        )
    except Exception:
        pass
    return result
