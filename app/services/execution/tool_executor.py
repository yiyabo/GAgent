from __future__ import annotations

import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional

from app.services.deliverables import get_deliverable_publisher

logger = logging.getLogger(__name__)


@dataclass
class ToolExecutionContext:
    plan_id: Optional[int] = None
    task_id: Optional[int] = None
    task_name: Optional[str] = None
    task_instruction: Optional[str] = None
    session_id: Optional[str] = None
    current_job_id: Optional[str] = None
    channel: str = "plan_executor"
    mode: str = "task_execution"
    on_stdout: Optional[Callable[[str], Awaitable[None]]] = None
    on_stderr: Optional[Callable[[str], Awaitable[None]]] = None


class UnifiedToolExecutor:
    DEFAULT_TIMEOUT_SECONDS = 60
    TOOL_TIMEOUTS = {
        "claude_code": 1200,
        "web_search": 180,
        "sequence_fetch": 120,
        "document_reader": 200,
        "graph_rag": 600,
        "file_operations": 90,
        "vision_reader": 1200,
        "bio_tools": 86400,
        "phagescope": 60,
        "deeppl": 1800,
        "result_interpreter": 300,
        "plan_operation": 1200,
        "manuscript_writer": 600,
    }

    def __init__(self, *, default_timeout: int = DEFAULT_TIMEOUT_SECONDS) -> None:
        self._default_timeout = max(1, int(default_timeout))
        self._deliverable_publisher = get_deliverable_publisher()

    async def execute(
        self,
        tool_name: str,
        params: Dict[str, Any],
        *,
        context: Optional[ToolExecutionContext] = None,
        on_tool_start: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
        on_tool_result: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
    ) -> Dict[str, Any]:
        context = context or ToolExecutionContext()
        tool_name = str(tool_name or "").strip()
        safe_params = self._normalize_params(tool_name, params or {}, context)

        if on_tool_start:
            await self._safe_callback(on_tool_start, tool_name, dict(safe_params))

        timeout = int(self.TOOL_TIMEOUTS.get(tool_name, self._default_timeout))
        try:
            from tool_box import execute_tool

            result = await asyncio.wait_for(
                execute_tool(tool_name, **safe_params),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            payload = {
                "success": False,
                "error": f"Tool '{tool_name}' execution timed out after {timeout}s",
                "result": {
                    "success": False,
                    "error": "timeout",
                    "timeout_seconds": timeout,
                },
                "summary": f"{tool_name} timed out after {timeout}s",
            }
            if on_tool_result:
                await self._safe_callback(on_tool_result, tool_name, dict(payload))
            return payload
        except Exception as exc:
            logger.exception("Tool %s execution failed: %s", tool_name, exc)
            payload = {
                "success": False,
                "error": str(exc),
                "result": {"success": False, "error": str(exc)},
                "summary": f"{tool_name} failed: {exc}",
            }
            if on_tool_result:
                await self._safe_callback(on_tool_result, tool_name, dict(payload))
            return payload

        summary = self._summarize_tool_result(tool_name, result)
        if result is None:
            tool_success = False
        elif isinstance(result, dict):
            tool_success = result.get("success") is not False
        else:
            # Non-dict, non-None results (e.g. strings) are treated as success.
            tool_success = True
        payload: Dict[str, Any] = {
            "success": tool_success,
            "result": result,
            "summary": summary,
        }
        if not tool_success:
            payload["error"] = self._build_tool_failure_error(tool_name, result)

        if context.session_id:
            try:
                report = self._deliverable_publisher.publish_from_tool_result(
                    session_id=context.session_id,
                    tool_name=tool_name,
                    raw_result=result,
                    summary=summary,
                    source={"channel": context.channel, "mode": context.mode},
                    job_id=context.current_job_id,
                    plan_id=context.plan_id,
                    task_id=context.task_id,
                    task_name=context.task_name,
                    task_instruction=context.task_instruction,
                    publish_status="final" if tool_success else "draft",
                )
                payload["deliverables"] = report.to_dict()
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Deliverable publishing failed: %s", exc)
                payload["deliverable_error"] = str(exc)

        if on_tool_result:
            await self._safe_callback(on_tool_result, tool_name, dict(payload))
        return payload

    def execute_sync(
        self,
        tool_name: str,
        params: Dict[str, Any],
        *,
        context: Optional[ToolExecutionContext] = None,
        on_tool_start: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
        on_tool_result: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
    ) -> Dict[str, Any]:
        async def _run() -> Dict[str, Any]:
            return await self.execute(
                tool_name,
                params,
                context=context,
                on_tool_start=on_tool_start,
                on_tool_result=on_tool_result,
            )

        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        if running_loop and running_loop.is_running():
            with ThreadPoolExecutor(max_workers=1) as executor:
                return executor.submit(lambda: asyncio.run(_run())).result()
        return asyncio.run(_run())

    async def _safe_callback(self, callback: Callable[..., Any], *args: Any) -> None:
        try:
            if asyncio.iscoroutinefunction(callback):
                await callback(*args)
                return
            maybe = callback(*args)
            if asyncio.iscoroutine(maybe):
                await maybe
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Tool executor callback failed: %s", exc)

    def _normalize_params(
        self,
        tool_name: str,
        params: Dict[str, Any],
        context: ToolExecutionContext,
    ) -> Dict[str, Any]:
        safe_params = dict(params or {})

        if tool_name == "claude_code":
            for key in (
                "require_task_context",
                "skip_permissions",
                "output_format",
                "model",
                "setting_sources",
                "auth_mode",
            ):
                safe_params.pop(key, None)
            legacy_target_task_id = safe_params.pop("target_task_id", None)
            if safe_params.get("task_id") is None:
                safe_params["task_id"] = (
                    legacy_target_task_id
                    if legacy_target_task_id is not None
                    else context.task_id
                )
            if safe_params.get("plan_id") is None:
                safe_params["plan_id"] = context.plan_id
            safe_params["require_task_context"] = True
            safe_params["auth_mode"] = "api_env"
            safe_params["setting_sources"] = "project"
            if context.on_stdout:
                safe_params["on_stdout"] = context.on_stdout
            if context.on_stderr:
                safe_params["on_stderr"] = context.on_stderr
        else:
            safe_params.pop("target_task_id", None)

        if context.session_id:
            safe_params["session_id"] = context.session_id

        return safe_params

    @staticmethod
    def _clip_tool_text(value: Any, *, limit: int = 320) -> str:
        if value is None:
            return ""
        text = " ".join(str(value).split()).strip()
        if not text:
            return ""
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 3)] + "..."

    def _build_tool_failure_error(self, tool_name: str, result: Any) -> str:
        if not isinstance(result, dict):
            return f"{tool_name} failed: Tool execution returned success=false."

        direct_error = self._clip_tool_text(
            result.get("error") or result.get("message"),
            limit=600,
        )
        if direct_error:
            return direct_error

        parts = []
        exit_code = result.get("exit_code")
        if exit_code is not None:
            parts.append(f"exit_code={exit_code}")

        blocked_reason = self._clip_tool_text(result.get("blocked_reason"), limit=200)
        if blocked_reason:
            parts.append(f"blocked_reason={blocked_reason}")

        stderr = self._clip_tool_text(result.get("stderr"), limit=320)
        if stderr:
            parts.append(f"stderr={stderr}")

        stdout = self._clip_tool_text(result.get("stdout"), limit=220)
        if stdout:
            parts.append(f"stdout={stdout}")

        nested_result = result.get("result")
        if isinstance(nested_result, dict):
            nested_error = self._clip_tool_text(
                nested_result.get("error") or nested_result.get("message"),
                limit=600,
            )
            if nested_error:
                parts.append(f"detail={nested_error}")

        if not parts:
            return "Tool execution returned success=false."
        return f"{tool_name} failed: {'; '.join(parts)}"

    def _summarize_tool_result(self, tool_name: str, result: Any) -> str:
        if result is None:
            return "(no result)"

        if tool_name == "phagescope" and isinstance(result, dict):
            action = str(result.get("action") or "phagescope").strip().lower()
            if result.get("success") is False:
                return (
                    f"PhageScope {action} failed: "
                    f"{result.get('error') or result.get('message') or 'unknown error'}"
                )
            if action == "submit":
                taskid = result.get("taskid")
                if taskid is None and isinstance(result.get("data"), dict):
                    taskid = result["data"].get("taskid")
                return f"PhageScope submit succeeded: taskid={taskid}; running in background."
            if action == "task_detail":
                status = "unknown"
                data = result.get("data")
                if isinstance(data, dict):
                    parsed = data.get("parsed_task_detail")
                    if isinstance(parsed, dict):
                        status = str(parsed.get("task_status") or status)
                    results = data.get("results")
                    if isinstance(results, dict):
                        status = str(results.get("status") or status)
                return f"PhageScope task_detail succeeded: status={status}."
            if action == "save_all":
                out_dir = result.get("output_directory") or result.get("output_directory_rel")
                if out_dir:
                    return f"PhageScope save_all completed: {out_dir}"
                return "PhageScope save_all completed."
            return f"PhageScope {action} succeeded."

        if tool_name == "deeppl" and isinstance(result, dict):
            action = str(result.get("action") or "deeppl").strip().lower()
            if result.get("success") is False:
                return (
                    f"DeepPL {action} failed: "
                    f"{result.get('error') or result.get('message') or 'unknown error'}"
                )
            if action == "predict":
                lifestyle = result.get("predicted_lifestyle") or "unknown"
                label = result.get("predicted_label") or "unknown"
                score = result.get("positive_window_fraction")
                if isinstance(score, (int, float)):
                    return (
                        f"DeepPL predict succeeded: label={label}, "
                        f"lifestyle={lifestyle}, positive_window_fraction={score:.4f}."
                    )
                return f"DeepPL predict succeeded: label={label}, lifestyle={lifestyle}."
            if action == "job_status":
                status = result.get("status") or "unknown"
                return f"DeepPL job_status succeeded: status={status}."
            return f"DeepPL {action} succeeded."

        if isinstance(result, dict):
            if "summary" in result:
                return str(result["summary"])[:1000]
            if "result" in result:
                return str(result["result"])[:1000]
            if "output" in result:
                return str(result["output"])[:1000]
            try:
                return json.dumps(result, ensure_ascii=False)[:1000]
            except (TypeError, ValueError):
                return str(result)[:1000]

        return str(result)[:1000]
