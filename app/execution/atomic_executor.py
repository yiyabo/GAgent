"""Atomic task executor that respects context references and logs execution metadata."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from typing import Any, Dict, List, Optional

from tool_box import execute_tool

from .assemblers import AssemblyStrategy, CompositeAssembler, RootAssembler
from ..repository.tasks import default_repo
from ..services.llm.llm_service import get_llm_service

logger = logging.getLogger(__name__)


class AtomicExecutor:
    """Execute atomic tasks by gathering context, invoking LLM/tooling, and logging output."""

    def __init__(self, repo=None, llm_service=None, *, retry_attempts: int = 1, retry_backoff: float = 1.0):
        self.repo = repo or default_repo
        self.llm_service = llm_service or get_llm_service()
        self.retry_attempts = max(int(retry_attempts), 0)
        self.retry_backoff = max(float(retry_backoff), 0.0)

    def _parse_context_refs(self, raw_refs: Optional[str]) -> List[Dict[str, Any]]:
        if not raw_refs:
            return []
        try:
            parsed = json.loads(raw_refs)
            if isinstance(parsed, list):
                return [ref for ref in parsed if isinstance(ref, dict)]
        except json.JSONDecodeError:
            logger.warning("Invalid context_refs JSON: %s", raw_refs)
        return []

    def _load_dependency_outputs(self, references: List[Dict[str, Any]]) -> Dict[str, Any]:
        collected: Dict[str, Any] = {}
        for ref in references:
            task_id = ref.get("task_id") if isinstance(ref, dict) else None
            if not task_id:
                continue
            output = self.repo.get_task_output_content(task_id)
            if output:
                key = ref.get("label") or str(task_id)
                collected[key] = {
                    "task_id": task_id,
                    "content": output,
                    "metadata": {k: v for k, v in ref.items() if k not in {"task_id", "label"}},
                }
        return collected

    def _extract_tool_calls(self, references: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        calls: List[Dict[str, Any]] = []
        for ref in references:
            if not isinstance(ref, dict):
                continue
            tool_name = ref.get("tool_name") or ref.get("tool") or ref.get("name")
            if not tool_name:
                continue
            parameters = ref.get("parameters") or ref.get("params") or {}
            if not isinstance(parameters, dict):
                try:
                    parameters = dict(parameters)
                except Exception:
                    parameters = {"value": parameters}
            label = ref.get("label") or ref.get("alias") or tool_name
            calls.append(
                {
                    "tool_name": str(tool_name),
                    "parameters": parameters,
                    "label": str(label),
                }
            )
        return calls

    def _run_async(self, coro):
        """Execute async coroutine from sync context, falling back to a helper thread."""

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)

        result_box: Dict[str, Any] = {}
        error_box: Dict[str, Exception] = {}

        def _runner():
            try:
                result_box["result"] = asyncio.run(coro)
            except Exception as exc:  # pragma: no cover - defensive path
                error_box["error"] = exc

        thread = threading.Thread(target=_runner, daemon=True)
        thread.start()
        thread.join()

        if error_box:
            raise error_box["error"]
        return result_box.get("result")

    def _format_tool_result(self, tool_name: str, raw_result: Any) -> str:
        if raw_result is None:
            return f"Tool {tool_name} did not return any result."
        if isinstance(raw_result, (str, bytes)):
            text = raw_result.decode("utf-8", errors="ignore") if isinstance(raw_result, bytes) else raw_result
            return text if len(text) <= 2000 else text[:2000] + "..."
        try:
            serialized = json.dumps(raw_result, ensure_ascii=False, indent=2)
            return serialized if len(serialized) <= 2000 else serialized[:2000] + "..."
        except (TypeError, ValueError):
            return str(raw_result)

    def _execute_tool_calls(self, context_refs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        tool_calls = self._extract_tool_calls(context_refs)
        if not tool_calls:
            return []

        results: List[Dict[str, Any]] = []
        for call in tool_calls:
            tool_name = call["tool_name"]
            params = call.get("parameters", {})
            try:
                logger.info("Executing tool '%s' with parameters: %s", tool_name, params)
                raw = self._run_async(execute_tool(tool_name, **params))
                content = self._format_tool_result(tool_name, raw)
                results.append(
                    {
                        "tool_name": tool_name,
                        "label": call["label"],
                        "parameters": params,
                        "success": True,
                        "content": content,
                    }
                )
            except Exception as exc:
                logger.warning("Tool '%s' execution failed: %s", tool_name, exc)
                results.append(
                    {
                        "tool_name": tool_name,
                        "label": call["label"],
                        "parameters": params,
                        "success": False,
                        "error": str(exc),
                        "content": f"Tool execution failed: {exc}",
                    }
                )
        return results

    def _build_prompt(self, task: Dict[str, Any], dependencies: Dict[str, Any]) -> str:
        context_lines: List[str] = []
        for label, payload in dependencies.items():
            content = payload.get("content")
            if content:
                context_lines.append(f"### {label}\n{content.strip()}\n")

        context_block = "\n".join(context_lines)
        instructions = (
            "You are an expert executor for an atomic task."
            " Provide a concise, actionable result focused on the task goal."
        )
        task_name = task.get("name", "Unnamed Task")
        base_prompt = (
            f"{instructions}\n\n"
            f"[Task]\n{task_name}\n\n"
            f"[Expected Output]\nDeliver a direct response or artifact supporting the composite objective."
        )
        if context_block:
            return f"[Context]\n{context_block}\n\n{base_prompt}"
        return base_prompt

    def _call_llm_with_retries(self, prompt: str, *, force_real: bool = True) -> Dict[str, Any]:
        attempts = max(1, self.retry_attempts + 1)
        last_error: Optional[Exception] = None
        response_text: Optional[str] = None

        for attempt in range(1, attempts + 1):
            try:
                response_text = self.llm_service.chat(prompt, force_real=force_real)
                if response_text and response_text.strip():
                    return {"response": response_text, "attempts": attempt}
                logger.warning("LLM returned empty output on attempt %s/%s", attempt, attempts)
            except Exception as exc:  # pragma: no cover - defensive path
                last_error = exc
                logger.warning("LLM call failed on attempt %s/%s: %s", attempt, attempts, exc)
            time.sleep(self.retry_backoff * attempt if self.retry_backoff else 0.0)

        if last_error:
            logger.error("LLM execution failed after %s attempts: %s", attempts, last_error)
            raise last_error

        fallback = "Unable to generate a response after multiple attempts. Please review the task context and retry."
        logger.error("LLM produced empty output after %s attempts; using fallback text", attempts)
        return {"response": fallback, "attempts": attempts}

    def _auto_assemble_upstream(self, task: Dict[str, Any], *, force_real: bool) -> List[Dict[str, Any]]:
        assemblies: List[Dict[str, Any]] = []
        parent_id = task.get("parent_id")
        if not parent_id:
            return assemblies

        composite_assembler = CompositeAssembler(self.repo, llm_service=self.llm_service)
        root_assembler = RootAssembler(self.repo, llm_service=self.llm_service)

        current_parent = parent_id
        visited: set[int] = set()

        while current_parent and current_parent not in visited:
            visited.add(current_parent)
            parent_task = self.repo.get_task_info(current_parent)
            if not parent_task:
                break

            task_type = (parent_task.get("task_type") or "").lower()

            if task_type == "composite":
                children = self.repo.get_children(current_parent)
                atomic_children = [child for child in children if child.get("task_type") == "atomic"]
                if atomic_children and all(child.get("status") == "completed" for child in atomic_children):
                    try:
                        composite_assembler.assemble(
                            current_parent,
                            strategy=AssemblyStrategy.LLM.value,
                            force_real=force_real,
                        )
                        assemblies.append({"task_id": current_parent, "task_type": "composite", "assembled": True})
                    except Exception as exc:  # pragma: no cover - defensive path
                        logger.warning("Composite assembly failed for %s: %s", current_parent, exc)
                        assemblies.append({"task_id": current_parent, "task_type": "composite", "assembled": False, "error": str(exc)})
                else:
                    logger.debug(
                        "Composite task %s not ready for assembly (children completed: %s/%s)",
                        current_parent,
                        sum(child.get("status") == "completed" for child in atomic_children),
                        len(atomic_children),
                    )

            elif task_type == "root":
                composite_children = [child for child in self.repo.get_children(current_parent) if child.get("task_type") == "composite"]
                if composite_children and all(child.get("status") == "completed" for child in composite_children):
                    try:
                        root_assembler.assemble(
                            current_parent,
                            strategy=AssemblyStrategy.LLM.value,
                            force_real=force_real,
                        )
                        assemblies.append({"task_id": current_parent, "task_type": "root", "assembled": True})
                    except Exception as exc:  # pragma: no cover - defensive path
                        logger.warning("Root assembly failed for %s: %s", current_parent, exc)
                        assemblies.append({"task_id": current_parent, "task_type": "root", "assembled": False, "error": str(exc)})
                    break  # Reached root
                else:
                    logger.debug(
                        "Root task %s not ready for assembly (composites completed: %s/%s)",
                        current_parent,
                        sum(child.get("status") == "completed" for child in composite_children),
                        len(composite_children),
                    )

            current_parent = parent_task.get("parent_id")

        return assemblies

    def execute(self, task_id: int, *, force_real: bool = True) -> Dict[str, Any]:
        task = self.repo.get_task_info(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")

        context_refs = self._parse_context_refs(task.get("context_refs"))
        dependency_outputs = self._load_dependency_outputs(context_refs)
        tool_results = self._execute_tool_calls(context_refs)
        for tool_result in tool_results:
            label = tool_result["label"] or tool_result["tool_name"]
            prefix = "Tool:" + label
            if not tool_result.get("success"):
                prefix += " (failed)"
            dependency_outputs[prefix] = {
                "content": tool_result.get("content", ""),
                "metadata": {
                    "tool_name": tool_result.get("tool_name"),
                    "success": tool_result.get("success", False),
                },
            }

        prompt = self._build_prompt(task, dependency_outputs)

        logger.info("Executing atomic task %s with %d context refs", task_id, len(context_refs))
        llm_result = self._call_llm_with_retries(prompt, force_real=force_real)
        response = llm_result["response"]

        self.repo.upsert_task_output(task_id, response)
        self.repo.update_task_status(task_id, "completed")

        assemblies = self._auto_assemble_upstream(task, force_real=force_real)

        log_metadata = {
            "prompt": prompt,
            "references": context_refs,
            "dependencies": list(dependency_outputs.keys()),
            "tool_calls": tool_results,
            "retry_attempts": llm_result.get("attempts"),
            "assemblies": assemblies,
        }
        workflow_id = task.get("workflow_id") if isinstance(task, dict) else None
        log_id = self.repo.append_execution_log(
            task_id,
            workflow_id=workflow_id,
            step_type="atomic_execution",
            content=response,
            metadata=log_metadata,
        )

        logger.debug("Atomic execution complete for task %s (log_id=%s)", task_id, log_id)
        return {
            "task_id": task_id,
            "output": response,
            "log_id": log_id,
            "workflow_id": workflow_id,
            "context_refs": context_refs,
            "tool_calls": tool_results,
            "retry_attempts": llm_result.get("attempts"),
            "assemblies": assemblies,
        }


def execute_atomic_task(task_id: int, *, force_real: bool = True) -> Dict[str, Any]:
    executor = AtomicExecutor()
    return executor.execute(task_id, force_real=force_real)
