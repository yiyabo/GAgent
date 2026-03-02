import asyncio
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from app.services.execution.tool_executor import UnifiedToolExecutor
from app.services.tool_schemas import build_tool_schemas

logger = logging.getLogger(__name__)


_BIO_TOOLS_FALLBACK_CATALOG: Dict[str, List[str]] = {
    "seqkit": ["stats", "grep", "seq", "head"],
    "blast": ["blastn", "blastp", "makeblastdb"],
    "prodigal": ["predict", "meta"],
    "hmmer": ["hmmscan", "hmmsearch", "hmmpress", "hmmbuild"],
    "checkv": ["end_to_end", "completeness", "complete_genomes"],
}


def _load_bio_tools_catalog() -> Dict[str, List[str]]:
    config_path = Path(__file__).resolve().parents[2] / "tool_box" / "bio_tools" / "tools_config.json"
    try:
        if not config_path.exists():
            return dict(_BIO_TOOLS_FALLBACK_CATALOG)
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        catalog: Dict[str, List[str]] = {}
        for tool_name, info in raw.items():
            ops = sorted((info or {}).get("operations", {}).keys())
            catalog[str(tool_name)] = [str(op) for op in ops]
        if not catalog:
            return dict(_BIO_TOOLS_FALLBACK_CATALOG)
        return catalog
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to load bio tools catalog for DeepThink prompt: %s", exc)
        return dict(_BIO_TOOLS_FALLBACK_CATALOG)


def _format_bio_tools_catalog(catalog: Dict[str, List[str]]) -> str:
    return "; ".join(
        f"{tool} ({', '.join(ops) if ops else 'no operations'})"
        for tool, ops in sorted(catalog.items())
    )


_BIO_TOOLS_CATALOG = _load_bio_tools_catalog()
_BIO_TOOLS_NAMES = sorted(_BIO_TOOLS_CATALOG.keys())
_BIO_TOOLS_CATALOG_TEXT = _format_bio_tools_catalog(_BIO_TOOLS_CATALOG)


@dataclass
class ThinkingStep:
    """Represents a single step in the thinking process."""
    iteration: int
    thought: str
    action: Optional[str]
    action_result: Optional[str]
    self_correction: Optional[str]
    timestamp: datetime = field(default_factory=datetime.now)
    status: str = "thinking"  # thinking, calling_tool, analyzing, done, error
    evidence: List[Dict[str, str]] = field(default_factory=list)
    started_at: datetime = field(default_factory=datetime.now)
    finished_at: Optional[datetime] = None


@dataclass
class DeepThinkResult:
    """The final result of the deep thinking process."""
    final_answer: str
    thinking_steps: List[ThinkingStep]
    total_iterations: int
    tools_used: List[str]
    confidence: float  # 0.0 to 1.0
    thinking_summary: str  # A concise summary for the user


@dataclass
class TaskExecutionContext:
    task_id: Optional[int] = None
    task_name: Optional[str] = None
    task_instruction: Optional[str] = None
    dependency_outputs: List[Dict[str, Any]] = field(default_factory=list)
    plan_outline: Optional[str] = None
    constraints: List[str] = field(default_factory=list)


class DeepThinkProtocolError(RuntimeError):
    """Raised when DeepThink output violates the required JSON protocol."""


class DeepThinkAgent:
    """
    Agent that performs multi-step reasoning and tool calling before answering.
    Supports streaming output for real-time display of thinking process.
    """

    DEFAULT_TOOL_TIMEOUT = 60
    FINAL_STREAM_CHUNK_CHARS = 24
    FINAL_STREAM_DELAY_SEC = 0.01
    MAX_IDENTICAL_TOOL_CALL_CYCLES = 4

    ARTIFACT_PATH_RE = re.compile(
        r'(?:saved?|writ(?:ten|e)|created?|generated?|output|produced?|exported?)\s+'
        r'(?:to|at|in|as|file)?\s*[:\-]?\s*'
        r'[`"\']?(/[^\s`"\'<>]+\.\w{1,6})[`"\']?',
        re.IGNORECASE,
    )
    BARE_PATH_RE = re.compile(
        r'(/(?:[\w._-]+/)+[\w._-]+\.(?:csv|tsv|xlsx|png|jpg|jpeg|pdf|svg|html|json|txt|fasta|fa|fq|fastq|gff|bed))\b'
    )
    URL_RE = re.compile(r"https?://[^\s`\"'<>]+", re.IGNORECASE)

    def __init__(
        self,
        llm_client: Any,
        available_tools: List[str],
        tool_executor: Callable[[str, Dict[str, Any]], Any],
        max_iterations: int = 10,
        tool_timeout: int = DEFAULT_TOOL_TIMEOUT,
        cancel_event: Optional[asyncio.Event] = None,
        on_thinking: Optional[Callable[[ThinkingStep], Any]] = None,
        on_thinking_delta: Optional[Callable[[int, str], Any]] = None,
        on_final_delta: Optional[Callable[[str], Any]] = None,
        on_tool_start: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
        on_tool_result: Optional[Callable[[str, Dict[str, Any]], Any]] = None,
        on_artifact: Optional[Callable[[Dict[str, Any]], Any]] = None,
    ):
        self.llm_client = llm_client
        self.available_tools = available_tools
        self.tool_executor = tool_executor
        self.max_iterations = max_iterations
        self.tool_timeout = tool_timeout
        self.cancel_event = cancel_event
        self.on_thinking = on_thinking
        self.on_thinking_delta = on_thinking_delta
        self.on_final_delta = on_final_delta
        self.on_tool_start = on_tool_start
        self.on_tool_result = on_tool_result
        self.on_artifact = on_artifact
        self._pause_event = asyncio.Event()
        self._pause_event.set()
        self._skip_current_step = False

    def pause(self) -> None:
        self._pause_event.clear()

    def resume(self) -> None:
        self._pause_event.set()

    def skip_step(self) -> None:
        self._skip_current_step = True

    def _supports_native_tools(self) -> bool:
        return hasattr(self.llm_client, "stream_chat_with_tools_async") and callable(
            getattr(self.llm_client, "stream_chat_with_tools_async")
        )

    def _build_shared_strategy_block(self) -> str:
        return (
            "=== CORE PRINCIPLES ===\n"
            "1. You have UNLIMITED tokens and API calls.\n"
            "2. Use MULTIPLE tools to gather complementary evidence.\n"
            "3. Be THOROUGH - it is better to call 5 tools than to provide an incomplete answer.\n"
            "4. Do not finish early; only conclude when evidence is sufficient.\n\n"
            "=== TOOL PRIORITY ===\n"
            "- For accession-based FASTA downloads, call sequence_fetch first.\n"
            "- For FASTA/FASTQ/sequence work, ALWAYS try bio_tools first before claude_code.\n"
            "- If the user provides inline sequence text (not a file), pass it as bio_tools(sequence_text=...).\n"
            "- If bio_tools routing is uncertain, call bio_tools(operation='help') first; use web_search only when help is insufficient.\n"
            "- For complex custom analysis not covered by bio_tools, then use claude_code.\n"
            "- Never use claude_code as fallback for sequence_fetch failures.\n"
            "- Never use claude_code as fallback for bio_tools input-conversion/parsing failures.\n"
            "- For status polling tools, if state is unchanged across several checks, stop active polling and summarize current status.\n"
            "- For plan creation, research first (web_search), then use plan_operation.\n"
        )

    def _build_protocol_boundary_block(self, mode: str) -> str:
        if mode == "native":
            return (
                "=== PROTOCOL BOUNDARY (NATIVE TOOL CALLING) ===\n"
                "- Use native tool calls for actions and submit_final_answer to finish.\n"
                "- Do NOT output legacy JSON keys like thinking/action/final_answer in plain text.\n"
                "- Do NOT output structured-agent JSON keys like llm_reply/actions.\n"
            )
        return (
            "=== PROTOCOL BOUNDARY (LEGACY JSON) ===\n"
            "- Respond with valid JSON only using keys: thinking, action, final_answer.\n"
            "- Do NOT output structured-agent JSON keys like llm_reply/actions.\n"
            "- action is null or an object: {\"tool\": \"name\", \"params\": {...}}.\n"
            "- final_answer is null until you are ready to conclude.\n"
        )

    @staticmethod
    def _append_recent_chat_history(prompt: str, context: Optional[Dict[str, Any]]) -> str:
        if not context:
            return prompt
        history = context.get("chat_history", [])
        if not history:
            return prompt
        recent = history[-30:] if len(history) > 30 else history
        lines = []
        for msg in recent:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if len(content) > 500:
                content = content[:500] + "..."
            lines.append(f"[{role}]: {content}")
        return prompt + "\n=== RECENT CONVERSATION ===\n" + "\n".join(lines)

    async def think(
        self,
        user_query: str,
        context: Optional[Dict[str, Any]] = None,
        task_context: Optional[TaskExecutionContext] = None,
    ) -> DeepThinkResult:
        """
        Executes the deep thinking loop with streaming output.

        Automatically uses native tool calling when the LLM client supports it,
        falling back to prompt-based JSON parsing otherwise.
        """
        if not user_query or not user_query.strip():
            raise ValueError("User query cannot be empty")
        if len(user_query) > 10000:
            raise ValueError("User query too long (max 10000 chars)")

        if self._supports_native_tools():
            logger.info("[DEEP_THINK] Using native tool calling path")
            return await self._think_native(user_query.strip(), context, task_context)

        return await self._think_prompt_based(user_query.strip(), context, task_context)

    # ------------------------------------------------------------------ #
    #  Native tool calling path                                           #
    # ------------------------------------------------------------------ #

    async def _think_native(
        self,
        user_query: str,
        context: Optional[Dict[str, Any]] = None,
        task_context: Optional[TaskExecutionContext] = None,
    ) -> DeepThinkResult:
        context = dict(context or {})
        thinking_steps: List[ThinkingStep] = []
        tools_used: List[str] = []
        tool_schemas = build_tool_schemas(self.available_tools)

        system_prompt = self._build_native_system_prompt(context, task_context)
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_query},
        ]

        iteration = 0
        final_answer = ""
        confidence = 0.0
        last_tool_cycle_signature: Optional[str] = None
        identical_tool_cycle_count = 0

        logger.info("[DEEP_THINK_NATIVE] Starting for: %s", user_query[:50])

        while iteration < self.max_iterations:
            await self._pause_event.wait()
            if self.cancel_event and self.cancel_event.is_set():
                logger.info("[DEEP_THINK_NATIVE] Cancelled by user")
                break

            iteration += 1
            current_step = ThinkingStep(
                iteration=iteration,
                thought="",
                action=None,
                action_result=None,
                self_correction=None,
                timestamp=datetime.now(),
                status="thinking",
            )
            if self.on_thinking:
                await self._safe_callback(current_step)

            try:
                async def _on_delta(chunk: str) -> None:
                    if self.on_thinking_delta:
                        await self._safe_delta_callback(iteration, chunk)

                result = await self.llm_client.stream_chat_with_tools_async(
                    messages=messages,
                    tools=tool_schemas,
                    tool_choice="auto",
                    on_content_delta=_on_delta,
                )
            except Exception as exc:
                logger.exception("[DEEP_THINK_NATIVE] LLM call failed at iteration %d", iteration)
                current_step.status = "error"
                current_step.thought = f"Error: {exc}"
                current_step.finished_at = datetime.now()
                thinking_steps.append(current_step)
                if self.on_thinking:
                    await self._safe_callback(current_step)
                continue

            current_step.thought = result.content or ""

            if result.tool_calls:
                tool_calls = list(result.tool_calls)
                final_call = next((tc for tc in tool_calls if tc.name == "submit_final_answer"), None)
                executable_calls = [tc for tc in tool_calls if tc.name != "submit_final_answer"]

                if executable_calls:
                    action_payload = {
                        "tools": [
                            {
                                "tool": tc.name,
                                "params": tc.arguments,
                                "tool_call_id": tc.id or f"native_{iteration}_{idx}",
                            }
                            for idx, tc in enumerate(executable_calls)
                        ]
                    }
                    current_step.action = json.dumps(action_payload, ensure_ascii=False)
                    current_step.status = "calling_tool"
                    thinking_steps.append(current_step)
                    if self.on_thinking:
                        await self._safe_callback(current_step)

                    tool_results = await asyncio.gather(
                        *[
                            self._execute_native_tool_call(tc=tc, iteration=iteration, index=idx)
                            for idx, tc in enumerate(executable_calls)
                        ]
                    )
                    tool_results.sort(
                        key=lambda item: (
                            item.get("index", 0),
                            str(item.get("tool_call_id") or ""),
                        )
                    )

                    for item in tool_results:
                        tool_name = str(item.get("tool_name") or "")
                        if tool_name and tool_name not in tools_used:
                            tools_used.append(tool_name)

                    assistant_msg: Dict[str, Any] = {"role": "assistant", "content": result.content or ""}
                    assistant_msg["tool_calls"] = [
                        {
                            "id": item["tool_call_id"],
                            "type": "function",
                            "function": {
                                "name": item["tool_name"],
                                "arguments": json.dumps(item.get("tool_params") or {}, ensure_ascii=False),
                            },
                        }
                        for item in tool_results
                    ]
                    messages.append(assistant_msg)
                    for item in tool_results:
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": item["tool_call_id"],
                                "content": item["tool_result_text"],
                            }
                        )

                    per_tool_text = [
                        f"[{item['tool_name']}] {item['tool_result_text']}"
                        for item in tool_results
                    ]
                    current_step.action_result = "\n\n".join(per_tool_text)
                    merged_evidence: List[Dict[str, str]] = []
                    for item in tool_results:
                        merged_evidence.extend(item.get("evidence") or [])
                    current_step.evidence = merged_evidence
                    current_step.finished_at = datetime.now()

                    tool_cycle_signature = self._build_tool_cycle_signature(tool_results)
                    if tool_cycle_signature and tool_cycle_signature == last_tool_cycle_signature:
                        identical_tool_cycle_count += 1
                    else:
                        last_tool_cycle_signature = tool_cycle_signature
                        identical_tool_cycle_count = 0

                    if identical_tool_cycle_count >= self.MAX_IDENTICAL_TOOL_CALL_CYCLES:
                        repeated_cycles = identical_tool_cycle_count + 1
                        current_step.status = "done"
                        current_step.self_correction = (
                            "Stopped repeated identical tool polling to avoid an unproductive loop."
                        )
                        if self.on_thinking:
                            await self._safe_callback(current_step)
                        final_answer = self._build_repetition_stop_answer(
                            tool_results=tool_results,
                            repeated_cycles=repeated_cycles,
                        )
                        confidence = max(
                            confidence,
                            0.75 if self._contains_tool(tool_results, "phagescope") else 0.5,
                        )
                        logger.warning(
                            "[DEEP_THINK_NATIVE] Stopped repeated tool loop at iteration=%s repeated_cycles=%s tools=%s",
                            iteration,
                            repeated_cycles,
                            ",".join(
                                sorted(
                                    {
                                        str(item.get("tool_name") or "")
                                        for item in tool_results
                                        if item.get("tool_name")
                                    }
                                )
                            ),
                        )
                        if self.on_final_delta and final_answer:
                            await self._stream_final_answer(final_answer)
                        break

                    current_step.status = "analyzing"
                    if self.on_thinking:
                        await self._safe_callback(current_step)
                    continue

                if final_call:
                    final_answer = final_call.arguments.get("answer", "")
                    raw_conf = final_call.arguments.get("confidence", 0.8)
                    try:
                        confidence = max(0.0, min(1.0, float(raw_conf)))
                    except (TypeError, ValueError):
                        confidence = 0.8
                    current_step.status = "done"
                    current_step.finished_at = datetime.now()
                    thinking_steps.append(current_step)
                    if self.on_thinking:
                        await self._safe_callback(current_step)
                    if self.on_final_delta and final_answer:
                        await self._stream_final_answer(final_answer)
                    break
            else:
                # No tool calls – pure thinking text
                current_step.finished_at = datetime.now()
                thinking_steps.append(current_step)
                if self.on_thinking:
                    await self._safe_callback(current_step)
                messages.append({"role": "assistant", "content": result.content or ""})
                messages.append({"role": "user", "content": self._get_next_step_prompt(iteration)})

            if self._skip_current_step:
                self._skip_current_step = False
                messages.append({"role": "user", "content": "Skip current branch and continue with the next reasoning step."})

        if not final_answer:
            final_answer = self._fallback_answer_from_steps(thinking_steps)
            confidence = max(confidence, 0.3)
            if self.on_final_delta and final_answer:
                await self._stream_final_answer(final_answer)

        try:
            summary = await self._generate_summary(thinking_steps, user_query)
        except Exception:
            summary = "DeepThink completed via native tool calling."

        return DeepThinkResult(
            final_answer=final_answer,
            thinking_steps=thinking_steps,
            total_iterations=iteration,
            tools_used=tools_used,
            confidence=confidence,
            thinking_summary=summary,
        )

    def _build_native_system_prompt(
        self,
        context: Optional[Dict[str, Any]] = None,
        task_context: Optional[TaskExecutionContext] = None,
    ) -> str:
        """System prompt for native tool calling mode (no JSON formatting rules)."""
        task_preamble = ""
        if task_context and task_context.task_instruction:
            lines = [
                "You are a task execution engine in DeepThink mode.",
                "Focus on completing the specific task with verifiable outputs.",
                "",
                "=== TASK CONTEXT ===",
            ]
            if task_context.task_id is not None:
                lines.append(f"Task ID: {task_context.task_id}")
            if task_context.task_name:
                lines.append(f"Task Name: {task_context.task_name}")
            lines.append(f"Instruction: {task_context.task_instruction}")
            if task_context.constraints:
                lines.append("Constraints:")
                for c in task_context.constraints:
                    lines.append(f"- {c}")
            if task_context.plan_outline:
                lines.append(f"Plan Outline (truncated):\n{task_context.plan_outline}")
            if task_context.dependency_outputs:
                lines.append("Dependency Outputs:")
                for dep in task_context.dependency_outputs[:6]:
                    lines.append(f"- {json.dumps(dep, ensure_ascii=False)[:600]}")
            lines.append("")
            task_preamble = "\n".join(lines) + "\n"

        prompt = task_preamble + (
            "You are a Deep Thinking AI Assistant with UNLIMITED resources.\n"
            "Your goal is to provide the MOST COMPREHENSIVE answer by using available tools aggressively.\n\n"
            + self._build_shared_strategy_block()
            + "\n=== WORKFLOW ===\n"
            "1. Think step by step and provide concise reasoning text.\n"
            "2. Call tools whenever needed; multiple tool calls across iterations are expected.\n"
            "3. Call submit_final_answer only when evidence is sufficient.\n"
            "4. Keep iterative reasoning visible to the user.\n\n"
            + self._build_protocol_boundary_block("native")
            + "\n=== RULES ===\n"
            "- Do NOT call submit_final_answer prematurely.\n"
            "- Keep quick checks synchronous; use background workflows only for clearly long-running operations.\n"
            "- Prioritize evidence-backed conclusions over speculation.\n"
        )
        return self._append_recent_chat_history(prompt, context)

    # ------------------------------------------------------------------ #
    #  Prompt-based (legacy) path                                         #
    # ------------------------------------------------------------------ #

    async def _think_prompt_based(
        self,
        user_query: str,
        context: Optional[Dict[str, Any]] = None,
        task_context: Optional[TaskExecutionContext] = None,
    ) -> DeepThinkResult:
        context = dict(context or {})
        thinking_steps: List[ThinkingStep] = []
        tools_used: List[str] = []

        system_prompt = self._build_system_prompt(context, task_context=task_context)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"User Query: {user_query}"}
        ]

        iteration = 0
        final_answer = ""
        confidence = 0.0

        logger.info(f"Starting DeepThink for query: {user_query[:50]}...")

        while iteration < self.max_iterations:
            await self._pause_event.wait()
            if self.cancel_event and self.cancel_event.is_set():
                logger.info("DeepThink cancelled by user")
                break

            iteration += 1

            try:
                current_step = ThinkingStep(
                    iteration=iteration,
                    thought="",
                    action=None,
                    action_result=None,
                    self_correction=None,
                    timestamp=datetime.now(),
                    status="thinking"
                )

                if self.on_thinking:
                    await self._safe_callback(current_step)

                response_text = ""

                if not hasattr(self.llm_client, "stream_chat_async"):
                    raise DeepThinkProtocolError(
                        "DeepThink requires LLM client support for stream_chat_async in strict mode."
                    )

                logger.info("[DEEP_THINK] Using streaming LLM call")
                async for delta in self.llm_client.stream_chat_async(prompt="", messages=messages):
                    response_text += delta
                    if self.on_thinking_delta:
                        await self._safe_delta_callback(iteration, delta)

                parsed, parse_error = self._parse_llm_response_safe(response_text)
                if parse_error:
                    logger.warning(
                        "DeepThink parse error at iteration %s: %s",
                        iteration,
                        parse_error,
                    )
                    current_step.status = "error"
                    current_step.thought = f"Protocol recovery: {parse_error}"
                    current_step.self_correction = "Requesting corrected JSON schema output."
                    thinking_steps.append(current_step)
                    if self.on_thinking:
                        await self._safe_callback(current_step)
                    messages.append({"role": "assistant", "content": response_text})
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "Your previous output violated protocol. "
                                "Return ONLY valid JSON with keys: thinking, action, final_answer."
                            ),
                        }
                    )
                    continue

                current_step.thought = parsed.get("thought", "")
                current_step.action = parsed.get("action_str", None)

                if parsed.get("is_final"):
                    final_answer = parsed.get("final_answer", "")
                    confidence = parsed.get("confidence", 0.8)
                    current_step.status = "done"
                    thinking_steps.append(current_step)
                    if self.on_thinking:
                        await self._safe_callback(current_step)

                    # Stream final answer if callback provided
                    if self.on_final_delta and final_answer:
                        await self._stream_final_answer(final_answer)
                    break

                if current_step.action:
                    current_step.status = "calling_tool"
                    thinking_steps.append(current_step)
                    if self.on_thinking:
                        await self._safe_callback(current_step)

                    tool_name = parsed.get("tool_name")
                    tool_params = parsed.get("tool_params")

                    if tool_name not in self.available_tools:
                        current_step.action_result = f"Error: Tool '{tool_name}' is not available. Available: {self.available_tools}"
                    elif tool_params is not None and not isinstance(tool_params, dict):
                        current_step.action_result = f"Error: Tool params must be a dict, got {type(tool_params).__name__}"
                    else:
                        if tool_name not in tools_used:
                            tools_used.append(tool_name)
                        try:
                            timeout = UnifiedToolExecutor.TOOL_TIMEOUTS.get(
                                str(tool_name),
                                self.tool_timeout,
                            )
                            if self.on_tool_start:
                                await self._safe_generic_callback(
                                    self.on_tool_start,
                                    str(tool_name),
                                    dict(tool_params or {}),
                                )
                            result = await asyncio.wait_for(
                                self.tool_executor(tool_name, tool_params or {}),
                                timeout=timeout
                            )
                            current_step.action_result = str(result)
                            await self._emit_artifacts(str(tool_name), result, iteration)
                            if self.on_tool_result:
                                callback_success, callback_error = self._normalize_tool_callback_outcome(result)
                                await self._safe_generic_callback(
                                    self.on_tool_result,
                                    str(tool_name),
                                    {
                                        "success": callback_success,
                                        "error": callback_error,
                                        "result": result,
                                        "summary": self._build_tool_callback_summary(result),
                                        "iteration": iteration,
                                    },
                                )
                        except asyncio.TimeoutError:
                            current_step.action_result = f"Error: Tool '{tool_name}' execution timed out after {timeout}s"
                            logger.warning(f"Tool {tool_name} timed out after {timeout}s")
                            if self.on_tool_result:
                                await self._safe_generic_callback(
                                    self.on_tool_result,
                                    str(tool_name),
                                    {
                                        "success": False,
                                        "error": "timeout",
                                        "summary": current_step.action_result,
                                        "iteration": iteration,
                                    },
                                )
                        except Exception as e:
                            current_step.action_result = f"Error executing tool: {str(e)}"
                            logger.exception(f"Tool {tool_name} execution failed")
                            if self.on_tool_result:
                                await self._safe_generic_callback(
                                    self.on_tool_result,
                                    str(tool_name),
                                    {
                                        "success": False,
                                        "error": str(e),
                                        "summary": current_step.action_result,
                                        "iteration": iteration,
                                    },
                                )

                    messages.append({"role": "assistant", "content": response_text})
                    messages.append({"role": "user", "content": f"Tool Output: {current_step.action_result}"})

                    current_step.status = "analyzing"
                    if self.on_thinking:
                        await self._safe_callback(current_step)

                else:
                    thinking_steps.append(current_step)
                    if self.on_thinking:
                        await self._safe_callback(current_step)
                    messages.append({"role": "assistant", "content": response_text})
                    messages.append({"role": "user", "content": self._get_next_step_prompt(iteration)})

            except Exception as e:
                logger.exception("Error in deep thinking loop")
                current_step.status = "error"
                current_step.thought = f"Error: {str(e)}"
                thinking_steps.append(current_step)
                if self.on_thinking:
                    await self._safe_callback(current_step)
                messages.append(
                    {
                        "role": "user",
                        "content": "Continue with a robust fallback and provide valid JSON only.",
                    }
                )
                continue

            if self._skip_current_step:
                self._skip_current_step = False
                messages.append(
                    {
                        "role": "user",
                        "content": "Skip current branch and continue with the next reasoning step.",
                    }
                )

        if not final_answer and thinking_steps:
            logger.info("[DEEP_THINK] Iterations exhausted, requesting strict final conclusion")

            conclusion_prompt = """You have reached the thinking limit. Based on all the information you've gathered, 
you MUST now provide a final answer. Synthesize everything you've learned into a comprehensive response.

Respond with ONLY a JSON object:
{
  "thinking": "I've gathered the following key information: [summarize key findings]",
  "action": null,
  "final_answer": {"answer": "Your comprehensive answer based on all gathered information", "confidence": 0.7}
}"""

            messages.append({"role": "user", "content": conclusion_prompt})

            try:
                if not hasattr(self.llm_client, "stream_chat_async"):
                    raise DeepThinkProtocolError(
                        "DeepThink requires stream_chat_async for forced conclusion in strict mode."
                    )

                response_text = ""
                async for delta in self.llm_client.stream_chat_async(prompt="", messages=messages):
                    response_text += delta
                    if self.on_thinking_delta:
                        await self._safe_delta_callback(iteration + 1, delta)

                parsed, parse_error = self._parse_llm_response_safe(response_text)
                if parse_error:
                    logger.warning("Forced conclusion parse fallback triggered: %s", parse_error)
                    parsed = {}
                if parsed.get("is_final"):
                    final_answer = parsed.get("final_answer", "")
                    confidence = parsed.get("confidence", 0.7)

                    # Stream final answer
                    if self.on_final_delta and final_answer:
                        await self._stream_final_answer(final_answer)
                else:
                    final_answer = self._fallback_answer_from_steps(thinking_steps)
                    confidence = 0.5
                    if self.on_final_delta and final_answer:
                        await self._stream_final_answer(final_answer)
            except Exception as e:
                logger.exception("Failed to generate strict forced conclusion")
                final_answer = self._fallback_answer_from_steps(thinking_steps)
                confidence = 0.4

        if not final_answer:
            final_answer = self._fallback_answer_from_steps(thinking_steps)
            confidence = max(confidence, 0.3)

        try:
            summary = await self._generate_summary(thinking_steps, user_query)
        except Exception:
            summary = "DeepThink completed with protocol-tolerant fallback summarization."

        return DeepThinkResult(
            final_answer=final_answer,
            thinking_steps=thinking_steps,
            total_iterations=iteration,
            tools_used=tools_used,
            confidence=confidence,
            thinking_summary=summary
        )

    async def _safe_callback(self, step: ThinkingStep):
        if self.on_thinking:
            try:
                if asyncio.iscoroutinefunction(self.on_thinking):
                    await self.on_thinking(step)
                else:
                    self.on_thinking(step)
            except Exception as e:
                logger.error(f"Error in on_thinking callback: {e}")

    async def _safe_generic_callback(self, callback: Callable[..., Any], *args: Any) -> None:
        try:
            if asyncio.iscoroutinefunction(callback):
                await callback(*args)
            else:
                ret = callback(*args)
                if asyncio.iscoroutine(ret):
                    await ret
        except Exception as e:
            logger.error("Error in callback: %s", e)

    async def _safe_delta_callback(self, iteration: int, delta: str):
        if self.on_thinking_delta:
            try:
                # Truncate very long deltas (e.g., FASTA file contents in JSON)
                # to prevent UI from being overwhelmed
                MAX_DELTA_LENGTH = 2000
                if len(delta) > MAX_DELTA_LENGTH:
                    # Find a reasonable truncation point
                    truncated = delta[:MAX_DELTA_LENGTH]
                    # Try to truncate at a newline or space for cleaner display
                    last_newline = truncated.rfind('\\n')
                    last_space = truncated.rfind(' ')
                    cut_point = max(last_newline, last_space, MAX_DELTA_LENGTH - 200)
                    if cut_point > MAX_DELTA_LENGTH - 500:
                        truncated = delta[:cut_point]
                    delta = truncated + f"... [truncated, {len(delta) - len(truncated)} chars hidden]"

                if asyncio.iscoroutinefunction(self.on_thinking_delta):
                    await self.on_thinking_delta(iteration, delta)
                else:
                    self.on_thinking_delta(iteration, delta)
            except Exception as e:
                logger.error(f"Error in on_thinking_delta callback: {e}")

    async def _safe_final_delta_callback(self, delta: str):
        if self.on_final_delta:
            try:
                if asyncio.iscoroutinefunction(self.on_final_delta):
                    await self.on_final_delta(delta)
                else:
                    self.on_final_delta(delta)
            except Exception as e:
                logger.error(f"Error in on_final_delta callback: {e}")

    def _normalize_tool_callback_outcome(self, result: Any) -> tuple[bool, Optional[str]]:
        if isinstance(result, dict):
            if "success" in result:
                success = bool(result.get("success"))
                error_val = result.get("error")
                error = str(error_val).strip() if error_val is not None else None
                return success, error
            nested = result.get("result")
            if isinstance(nested, dict) and nested.get("success") is False:
                nested_error = nested.get("error")
                if nested_error is not None:
                    return False, str(nested_error)
                return False, None
        return True, None

    def _extract_artifact_paths(self, tool_name: str, result: Any) -> List[str]:
        """Extract file paths from tool results that look like produced artifacts."""
        text = str(result) if result is not None else ""
        if not text:
            return []
        paths: List[str] = []
        for m in self.ARTIFACT_PATH_RE.finditer(text):
            paths.append(m.group(1))
        for m in self.BARE_PATH_RE.finditer(text):
            candidate = m.group(1)
            if candidate not in paths:
                paths.append(candidate)
        return list(dict.fromkeys(paths))

    async def _emit_artifacts(self, tool_name: str, result: Any, iteration: int) -> None:
        if not self.on_artifact:
            return
        paths = self._extract_artifact_paths(tool_name, result)
        for path in paths:
            ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
            await self._safe_generic_callback(
                self.on_artifact,
                {
                    "path": path,
                    "extension": ext,
                    "source_tool": tool_name,
                    "iteration": iteration,
                },
            )

    async def _execute_native_tool_call(
        self,
        tc: Any,
        iteration: int,
        index: int,
    ) -> Dict[str, Any]:
        tool_name = str(getattr(tc, "name", "") or "")
        tool_params = getattr(tc, "arguments", {}) or {}
        tool_call_id = str(getattr(tc, "id", "") or f"native_{iteration}_{index}")
        timeout = UnifiedToolExecutor.TOOL_TIMEOUTS.get(tool_name, self.tool_timeout)

        if self.on_tool_start and tool_name:
            await self._safe_generic_callback(self.on_tool_start, tool_name, tool_params)

        if tool_name not in self.available_tools:
            error_payload = {
                "success": False,
                "error": f"tool_not_available:{tool_name}",
                "summary": f"Tool '{tool_name}' is not available.",
                "iteration": iteration,
            }
            if self.on_tool_result and tool_name:
                await self._safe_generic_callback(self.on_tool_result, tool_name, error_payload)
            tool_result_text = json.dumps(error_payload, ensure_ascii=False)
            return {
                "index": index,
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "tool_params": tool_params,
                "tool_result_text": tool_result_text,
                "evidence": [],
            }

        try:
            tool_result = await asyncio.wait_for(
                self.tool_executor(tool_name, tool_params),
                timeout=timeout,
            )
            callback_success, callback_error = self._normalize_tool_callback_outcome(tool_result)
            callback_payload = {
                "success": callback_success,
                "error": callback_error,
                "result": tool_result,
                "summary": self._build_tool_callback_summary(tool_result),
                "iteration": iteration,
            }
            if not callback_success:
                logger.warning(
                    "[DEEP_THINK_NATIVE] Tool returned success=false: tool=%s tool_call_id=%s summary=%s error=%s",
                    tool_name,
                    tool_call_id,
                    self._clip_log_text(callback_payload.get("summary"), limit=360),
                    self._clip_log_text(callback_error, limit=240),
                )
            if self.on_tool_result:
                await self._safe_generic_callback(self.on_tool_result, tool_name, callback_payload)
            await self._emit_artifacts(tool_name, tool_result, iteration)
            normalized_payload = {
                "success": callback_success,
                "tool": tool_name,
                "result": tool_result,
                "error": callback_error,
            }
            tool_result_text = json.dumps(normalized_payload, ensure_ascii=False, default=str)
            evidence = self._extract_evidence(tool_name, tool_params, tool_result)
            return {
                "index": index,
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "tool_params": tool_params,
                "tool_result_text": tool_result_text,
                "evidence": evidence,
            }
        except asyncio.TimeoutError:
            timeout_payload = {
                "success": False,
                "tool": tool_name,
                "error": "timeout",
                "summary": f"Tool '{tool_name}' timed out after {timeout}s",
            }
            if self.on_tool_result:
                await self._safe_generic_callback(
                    self.on_tool_result,
                    tool_name,
                    {
                        "success": False,
                        "error": "timeout",
                        "summary": timeout_payload["summary"],
                        "iteration": iteration,
                    },
                )
            return {
                "index": index,
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "tool_params": tool_params,
                "tool_result_text": json.dumps(timeout_payload, ensure_ascii=False),
                "evidence": [],
            }
        except Exception as exc:
            logger.exception(
                "Tool %s failed (tool_call_id=%s, params=%s)",
                tool_name,
                tool_call_id,
                self._sanitize_tool_params_for_log(tool_params),
            )
            failure_payload = {
                "success": False,
                "tool": tool_name,
                "error": str(exc),
                "summary": f"Error executing tool: {exc}",
            }
            if self.on_tool_result:
                await self._safe_generic_callback(
                    self.on_tool_result,
                    tool_name,
                    {
                        "success": False,
                        "error": str(exc),
                        "summary": failure_payload["summary"],
                        "iteration": iteration,
                    },
                )
            return {
                "index": index,
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "tool_params": tool_params,
                "tool_result_text": json.dumps(failure_payload, ensure_ascii=False),
                "evidence": [],
            }

    def _extract_evidence(
        self,
        tool_name: str,
        tool_params: Dict[str, Any],
        tool_result: Any,
    ) -> List[Dict[str, str]]:
        text = str(tool_result or "")
        evidence: List[Dict[str, str]] = []

        for path in self._extract_artifact_paths(tool_name, tool_result):
            evidence.append(
                {
                    "type": "file",
                    "title": "Generated file",
                    "ref": path,
                    "snippet": f"{tool_name} produced {path}",
                }
            )
        for m in self.URL_RE.finditer(text):
            url = m.group(0)
            evidence.append(
                {
                    "type": "url",
                    "title": "External source",
                    "ref": url,
                    "snippet": f"{tool_name} referenced {url}",
                }
            )
        task_id = None
        job_id = None
        if isinstance(tool_result, dict):
            for key in ("taskid", "task_id", "remote_taskid", "remote_task_id"):
                val = tool_result.get(key)
                if isinstance(val, (str, int)) and str(val).strip():
                    task_id = str(val).strip()
                    break
            job_val = tool_result.get("job_id")
            if isinstance(job_val, (str, int)) and str(job_val).strip():
                job_id = str(job_val).strip()
        if task_id:
            evidence.append(
                {
                    "type": "task",
                    "title": "Background task",
                    "ref": task_id,
                    "snippet": f"{tool_name} created task {task_id}",
                }
            )
        if job_id and job_id != task_id:
            evidence.append(
                {
                    "type": "job",
                    "title": "Background job",
                    "ref": job_id,
                    "snippet": f"{tool_name} created job {job_id}",
                }
            )
        if not evidence:
            snippet = (text or "").strip().replace("\n", " ")
            if len(snippet) > 240:
                snippet = snippet[:240] + "..."
            if snippet:
                evidence.append(
                    {
                        "type": "output",
                        "title": "Tool output",
                        "ref": tool_name,
                        "snippet": snippet,
                    }
                )
        return evidence[:8]

    @staticmethod
    def _build_tool_callback_summary(result: Any) -> str:
        if isinstance(result, dict):
            if "summary" in result:
                return str(result["summary"])[:600]
            if "error" in result and result.get("error"):
                return str(result.get("error"))[:600]
        return str(result)[:600]

    @staticmethod
    def _contains_tool(tool_results: List[Dict[str, Any]], tool_name: str) -> bool:
        for item in tool_results:
            if str(item.get("tool_name") or "").strip().lower() == tool_name:
                return True
        return False

    @classmethod
    def _build_tool_cycle_signature(cls, tool_results: List[Dict[str, Any]]) -> str:
        signature_parts: List[str] = []
        for item in tool_results:
            tool_name = str(item.get("tool_name") or "").strip().lower()
            tool_params = item.get("tool_params") or {}
            result_marker = cls._extract_tool_result_marker(tool_name, item.get("tool_result_text"))
            try:
                params_text = json.dumps(tool_params, ensure_ascii=False, sort_keys=True, default=str)
            except Exception:
                params_text = str(tool_params)
            signature_parts.append(f"{tool_name}|{params_text}|{result_marker}")
        raw = "\n".join(signature_parts)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    @classmethod
    def _extract_tool_result_marker(cls, tool_name: str, tool_result_text: Any) -> str:
        raw_text = str(tool_result_text or "")
        if tool_name == "phagescope":
            state = cls._extract_phagescope_state(raw_text)
            if state:
                return (
                    f"task={state.get('task_id')};status={state.get('status')};"
                    f"task_status={state.get('task_status')};progress={state.get('progress')};"
                    f"waiting={state.get('waiting')};running={state.get('running')};failed={state.get('failed')}"
                )
        normalized = cls._normalize_marker_text(raw_text)
        return hashlib.sha1(normalized.encode("utf-8")).hexdigest()

    @staticmethod
    def _normalize_marker_text(raw_text: str) -> str:
        text = raw_text or ""
        # Remove timestamp-like values to make stability detection resilient.
        text = re.sub(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?", "<ts>", text)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) > 600:
            text = text[:600]
        return text

    @classmethod
    def _extract_phagescope_state(cls, tool_result_text: str) -> Optional[Dict[str, Any]]:
        try:
            payload = json.loads(tool_result_text)
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        result = payload.get("result")
        if not isinstance(result, dict):
            return None

        data = result.get("data")
        results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(results, dict):
            return None

        task_id = results.get("id") or result.get("taskid") or result.get("task_id") or ""
        status = str(results.get("status") or "").strip()
        task_status = ""
        progress = ""
        waiting = 0
        running = 0
        failed = 0
        completed = 0
        total = 0

        detail_raw = results.get("task_detail")
        detail: Optional[Dict[str, Any]] = None
        if isinstance(detail_raw, dict):
            detail = detail_raw
        elif isinstance(detail_raw, str):
            stripped = detail_raw.strip()
            if stripped.startswith("{") and stripped.endswith("}"):
                try:
                    parsed = json.loads(stripped)
                    if isinstance(parsed, dict):
                        detail = parsed
                except Exception:
                    detail = None

        if isinstance(detail, dict):
            task_status = str(detail.get("task_status") or "").strip()
            queue = detail.get("task_que")
            if isinstance(queue, list):
                total = len(queue)
                for module_item in queue:
                    if not isinstance(module_item, dict):
                        continue
                    module_status = str(module_item.get("module_satus") or "").strip().lower()
                    if module_status == "completed":
                        completed += 1
                    elif module_status in {"waiting", "wait"}:
                        waiting += 1
                    elif module_status in {"running", "create", "queued"}:
                        running += 1
                    elif module_status in {"failed", "error"}:
                        failed += 1
            if total > 0:
                progress = f"{completed}/{total}"

        return {
            "task_id": str(task_id),
            "status": status,
            "task_status": task_status,
            "progress": progress,
            "waiting": waiting,
            "running": running,
            "failed": failed,
        }

    @classmethod
    def _build_repetition_stop_answer(
        cls,
        tool_results: List[Dict[str, Any]],
        repeated_cycles: int,
    ) -> str:
        phagescope_state: Optional[Dict[str, Any]] = None
        for item in tool_results:
            if str(item.get("tool_name") or "").strip().lower() != "phagescope":
                continue
            phagescope_state = cls._extract_phagescope_state(str(item.get("tool_result_text") or ""))
            if phagescope_state:
                break

        if phagescope_state:
            task_id = phagescope_state.get("task_id") or "unknown"
            status = phagescope_state.get("status") or "unknown"
            task_status = phagescope_state.get("task_status") or "unknown"
            progress = phagescope_state.get("progress") or "unknown"
            return (
                f"PhageScope task {task_id} is still unchanged after {repeated_cycles} polling cycles "
                f"(status={status}, task_status={task_status}, module_progress={progress}).\n\n"
                "DeepThink stopped active polling to avoid an infinite loop. "
                "Please retry status check later, or continue once the remote task state changes."
            )

        return (
            "Tool outputs remained unchanged across repeated cycles, so DeepThink stopped active polling "
            f"after {repeated_cycles} repeats to avoid an infinite loop. "
            "Please retry later or provide new constraints."
        )

    @staticmethod
    def _clip_log_text(value: Any, *, limit: int = 400) -> str:
        text = " ".join(str(value or "").split()).strip()
        if len(text) <= limit:
            return text
        return text[: max(0, limit - 3)] + "..."

    @classmethod
    def _sanitize_tool_params_for_log(cls, params: Any) -> str:
        redact_tokens = ("password", "passwd", "secret", "token", "api_key", "apikey", "authorization")

        def _sanitize(value: Any, depth: int = 0) -> Any:
            if depth >= 4:
                return "<truncated>"
            if isinstance(value, dict):
                sanitized: Dict[str, Any] = {}
                for key, item in value.items():
                    key_text = str(key)
                    key_lower = key_text.lower()
                    if any(token in key_lower for token in redact_tokens):
                        sanitized[key_text] = "<redacted>"
                        continue
                    sanitized[key_text] = _sanitize(item, depth + 1)
                return sanitized
            if isinstance(value, list):
                return [_sanitize(item, depth + 1) for item in value[:20]]
            if isinstance(value, tuple):
                return [_sanitize(item, depth + 1) for item in value[:20]]
            if isinstance(value, str):
                return cls._clip_log_text(value, limit=240)
            return value

        try:
            sanitized_params = _sanitize(params)
            raw = json.dumps(sanitized_params, ensure_ascii=False, default=str)
        except Exception:
            raw = str(params)
        return cls._clip_log_text(raw, limit=800)

    @classmethod
    def _chunk_final_answer(cls, text: str) -> List[str]:
        text = text or ""
        if not text:
            return []

        chunks: List[str] = []
        buffer: List[str] = []
        max_chars = max(8, int(cls.FINAL_STREAM_CHUNK_CHARS))
        split_chars = {".", "!", "?", "\n", ",", ";", ":", "，", "。", "！", "？"}

        for ch in text:
            buffer.append(ch)
            if len(buffer) >= max_chars or (ch in split_chars and len(buffer) >= max_chars // 2):
                chunks.append("".join(buffer))
                buffer = []

        if buffer:
            chunks.append("".join(buffer))
        return chunks

    async def _stream_final_answer(self, final_answer: str) -> None:
        if not self.on_final_delta or not final_answer:
            return
        for chunk in self._chunk_final_answer(final_answer):
            await self._safe_final_delta_callback(chunk)
            if self.FINAL_STREAM_DELAY_SEC > 0:
                await asyncio.sleep(self.FINAL_STREAM_DELAY_SEC)

    def _build_system_prompt(
        self,
        context: Optional[Dict[str, Any]] = None,
        task_context: Optional[TaskExecutionContext] = None,
    ) -> str:
        """Construct the system prompt for DeepThink, task-aware when available."""
        # Build detailed tool descriptions
        tool_descriptions = {
            "sequence_fetch": (
                "Deterministic accession-to-FASTA downloader. "
                "Use for FASTA downloads by accession IDs before analysis. "
                "Params: {\"accession\": \"NC_001416.1\"} or "
                "{\"accessions\": [\"NC_001416.1\", \"NC_001417.1\"], "
                "\"database\": \"nuccore|protein\", \"format\": \"fasta\"}. "
                "Do not use claude_code as fallback when sequence_fetch fails."
            ),
            "claude_code": "Execute Python/shell code. FALLBACK TOOL: Use this ONLY when bio_tools cannot handle the task (e.g., custom analysis scripts, complex data processing). For FASTA/FASTQ sequence stats or standard bioinformatics tasks, ALWAYS try bio_tools first. Params: {\"task\": \"description\"}",
            "web_search": "Search the internet for information. USE THIS ONLY for web-based queries, NOT for local files. Params: {\"query\": \"search query\"}",
            "graph_rag": "Query knowledge graph for structured information. Params: {\"query\": \"your question\", \"mode\": \"global|local|hybrid\"}",
            "file_operations": "File system operations: list directories, read/write files, copy/move/delete. USE THIS for quick directory listing or file reading. Params: {\"operation\": \"list|read|write|copy|move|delete\", \"path\": \"/path\"}",
            "document_reader": "Read local documents with format-aware parsing. Use this first for .docx/.pdf/.txt content extraction. Params: {\"operation\": \"read_any|read_pdf|read_text\", \"file_path\": \"/abs/path\"}",
            "vision_reader": "Read PDFs and images using vision model. Use for visual OCR/figures/equations, not for DOCX. Params: {\"operation\": \"read_pdf|read_image|ocr_page\", \"file_path\": \"/path/to/file\"}",
            "bio_tools": (
                "PREFERRED for bioinformatics: Execute Docker-based tools for FASTA/FASTQ/sequence analysis. "
                "Example: {\"tool_name\": \"seqkit\", \"operation\": \"stats\", \"input_file\": \"/absolute/path/to/file.fasta\"}. "
                "For inline sequence content, pass sequence_text instead of input_file. "
                "NOTE: input_file SHOULD be absolute path. "
                f"Available tools (synced from tools_config.json): {', '.join(_BIO_TOOLS_NAMES)}. "
                "Use operation='help' first for exact params and prefer operations verified in bio_tool_list.md. "
                "Background policy: use background=true ONLY for long-running bio_tools operations when "
                "the current turn does not require immediate result-dependent reasoning. "
                "Keep short/interactive checks synchronous."
            ),
            "phagescope": """PhageScope cloud platform for phage genome analysis.
IMPORTANT: This is an ASYNC service - tasks run remotely and take minutes to hours.

Workflow:
1. submit: Submit sequences → Returns taskid immediately (DO NOT wait)
2. task_list: Check all your submitted tasks
3. task_detail: Check specific task status
4. result: Get results ONLY when task is COMPLETED

After submit, stop PhageScope result retrieval in this turn unless user explicitly asks to query status only.
After submit, ALWAYS tell user with 3 parts:
- Completed now: submit + taskid
- Running in background: current status/module progress if known
- Next step: refresh status later, then fetch result/save_all/download after completion
DO NOT use wait=True, it will block too long.

Parameter rules (CRITICAL):
- Use `phageid` or `phageids`; do NOT use `sequence` for accession IDs.
- `submit` requires `userid` + `modulelist` + `phageid/phageids`.
- `input_check` requires `phageid/phageids`.
- `result` requires `taskid` + `result_kind` (quality/proteins/phage_detail/modules/tree/phagefasta).
- `taskid` must be the numeric remote task id (e.g., 37468), not a local job id like `act_xxx`.

Params: {"action": "submit|task_list|task_detail|result|save_all|download", "userid": "...", "phageid": "...", "phageids": "...", "taskid": "...", "result_kind": "..."}""",
            "result_interpreter": """Data analysis and result interpretation tool.
Analyzes CSV, TSV, MAT, NPY data files by generating and executing Python code via Claude Code.

Operations:
- metadata: Extract dataset metadata (columns, types, samples)
- generate: Generate Python analysis code based on task description
- execute: Execute Python code via Claude Code
- analyze: Full pipeline (metadata → generate → execute with auto-fix)

Params for analyze (recommended):
{"operation": "analyze", "file_paths": ["/path/to/data.csv"], "task_title": "Analysis Title", "task_description": "What to analyze"}

Params for metadata:
{"operation": "metadata", "file_path": "/path/to/data.csv"}

Use this for data exploration, statistical analysis, and visualization tasks on structured data files.""",
            "plan_operation": """Plan creation and optimization tool for structured task planning.

Operations:
- create: Create a new plan with tasks. Params: {"operation": "create", "title": "Plan Title", "description": "Goal", "tasks": [{"name": "Task 1", "instruction": "Details...", "dependencies": ["Task 0"]}]}
- review: Review plan quality and structure. Returns BOTH: (1) structural health_score and (2) a strict research-plan rubric_score with detailed breakdown. Params: {"operation": "review", "plan_id": 123}
- optimize: Apply changes to improve the plan. Params: {"operation": "optimize", "plan_id": 123, "changes": [{"action": "add_task|update_task|delete_task", ...}]}
- get: Get plan details. Params: {"operation": "get", "plan_id": 123}

WORKFLOW for Plan Creation:
1. First use web_search to research relevant technologies/best practices
2. Create initial plan with 'create' operation
3. Use 'review' to check dependency issues AND research-plan rubric quality
4. If issues found, use 'optimize' to fix them
5. Repeat review-optimize until BOTH health_score and rubric_score are strong
6. Report final plan to user with summary

IMPORTANT: When creating plans, ensure each task has clear, actionable instructions!""",
        }

        tools_desc = []
        for t in self.available_tools:
            if t in tool_descriptions:
                tools_desc.append(f"- {t}: {tool_descriptions[t]}")
            else:
                tools_desc.append(f"- {t}")
        tools_text = "\n".join(tools_desc)

        if task_context and task_context.task_instruction:
            task_lines = [
                "You are a task execution engine in DeepThink mode.",
                "Focus on completing the specific task with verifiable outputs.",
                "Be concise, deterministic, and robust.",
                "",
                "=== TASK EXECUTION CONTEXT ===",
            ]
            if task_context.task_id is not None:
                task_lines.append(f"Task ID: {task_context.task_id}")
            if task_context.task_name:
                task_lines.append(f"Task Name: {task_context.task_name}")
            task_lines.append(f"Instruction: {task_context.task_instruction}")
            if task_context.constraints:
                task_lines.append("Constraints:")
                for c in task_context.constraints:
                    task_lines.append(f"- {c}")
            if task_context.plan_outline:
                task_lines.append("Plan Outline (truncated):")
                task_lines.append(task_context.plan_outline)
            if task_context.dependency_outputs:
                task_lines.append("Dependency Outputs:")
                for dep in task_context.dependency_outputs[:6]:
                    task_lines.append(f"- {json.dumps(dep, ensure_ascii=False)[:600]}")
            task_lines.append("")
            base_prompt = "\n".join(task_lines) + "\n"
        else:
            base_prompt = ""

        base_prompt += f"""You are a Deep Thinking AI Assistant with UNLIMITED resources.
Your goal is to provide the MOST COMPREHENSIVE answer by using available tools aggressively.

{self._build_shared_strategy_block()}
=== THINKING WORKFLOW ===
1. Break the query into sub-problems.
2. Choose tools for each sub-problem.
3. Execute tools iteratively and synthesize evidence.
4. Provide final_answer only when evidence is sufficient.

=== AVAILABLE TOOLS ===
{tools_text}

=== BIO_TOOLS OPERATING RULES ===
- For accession-based FASTA download, call sequence_fetch first and use its output_file for downstream steps.
- For FASTA/FASTQ/sequence tasks, start with bio_tools (typically seqkit stats for first-pass diagnostics).
- Use operation="help" before first use of uncertain operations; do not guess parameters.
- If routing remains uncertain after help, use focused web_search and retry bio_tools.
- Keep quick checks synchronous; use background=true only for clearly long-running jobs and return job_id for job_status follow-ups.
- Bio_tools catalog (synced from tools_config.json): {_BIO_TOOLS_CATALOG_TEXT}

=== BIO_TOOLS RECOVERY PROTOCOL (MANDATORY) ===
1. Call bio_tools(..., operation="help") and inspect required parameters.
2. Retry bio_tools with corrected parameters and verified absolute paths.
3. If still failing, run targeted web_search for operation/parameter mapping.
4. If still failing for reasons other than input parsing/conversion, use claude_code for minimal shell-level diagnostics.
Try at least 3 different recovery attempts before reporting failure.

=== PLAN CREATION RULE ===
When creating a plan, research first with web_search, then use plan_operation create/review/optimize/get.

{self._build_protocol_boundary_block("legacy")}
=== OUTPUT FORMAT ===
Respond with valid JSON only (no markdown fences):

{{
  "thinking": "What you are analyzing and why...",
  "action": {{"tool": "tool_name", "params": {{...}}}},
  "final_answer": null
}}

When ready to answer:
{{
  "thinking": "Synthesis based on tool evidence...",
  "action": null,
  "final_answer": {{"answer": "Comprehensive answer here", "confidence": 0.9}}
}}

=== RULES ===
1. Output valid JSON only.
2. Use action to call one tool per response.
3. Call MULTIPLE tools before providing final_answer when evidence gathering is required.
4. Include key tool evidence before concluding.
5. For PhageScope submit, prioritize non-blocking backend execution over immediate result fetching.
"""
        return self._append_recent_chat_history(base_prompt, context)

    def _get_next_step_prompt(self, iteration: int) -> str:
        """Generate prompt for the next step, encouraging completion if steps are getting long."""
        if iteration > 8:
            return 'You have taken many steps. Please consolidate your thinking and provide the final answer using "final_answer" in your JSON response.'
        elif iteration > 5:
            return 'Scan your previous thoughts. If you have sufficient information, please output "final_answer" now. Otherwise, continue thinking.'
        else:
            return 'Please continue thinking. If you are ready to answer, include "final_answer" in your JSON response.'

    def _parse_llm_response_safe(self, response: str) -> tuple[Dict[str, Any], Optional[str]]:
        try:
            return self._parse_llm_response(response), None
        except Exception as exc:
            return {}, str(exc)

    def _parse_llm_response(self, response: str) -> Dict[str, Any]:
        json_str = self._extract_json(response)
        parsed: Optional[Dict[str, Any]] = None
        parse_errors: List[str] = []

        for candidate in (
            json_str,
            self._repair_json_text(json_str),
        ):
            if not candidate:
                continue
            try:
                payload = json.loads(candidate)
            except json.JSONDecodeError as exc:
                parse_errors.append(str(exc))
                continue
            if isinstance(payload, dict):
                parsed = payload
                break

        if parsed is None:
            parsed = self._regex_parse_fallback(json_str)
            if parsed is None:
                raise DeepThinkProtocolError(
                    "LLM output is not valid JSON; parse errors: "
                    + "; ".join(parse_errors[:2])
                )

        thinking = parsed.get("thinking", "")
        if thinking is None:
            thinking = ""
        if not isinstance(thinking, str):
            thinking = str(thinking)

        result = {
            "thought": thinking,
            "is_final": False,
            "final_answer": "",
            "confidence": 0.0,
            "tool_name": None,
            "tool_params": None,
            "action_str": None,
        }

        final_ans = parsed.get("final_answer")
        if final_ans is not None:
            if isinstance(final_ans, str):
                final_ans = {"answer": final_ans, "confidence": 0.7}
            if not isinstance(final_ans, dict):
                raise DeepThinkProtocolError("final_answer must be an object or null.")
            answer = final_ans.get("answer")
            if not isinstance(answer, str) or not answer.strip():
                raise DeepThinkProtocolError("final_answer.answer must be a non-empty string.")
            confidence_raw = final_ans.get("confidence", 0.8)
            try:
                confidence = float(confidence_raw)
            except (TypeError, ValueError):
                confidence = 0.8
            result["is_final"] = True
            result["final_answer"] = answer
            result["confidence"] = min(max(confidence, 0.0), 1.0)
            return result

        action = parsed.get("action")
        if action is not None:
            if isinstance(action, str):
                action = {"tool": action, "params": {}}
            if not isinstance(action, dict):
                raise DeepThinkProtocolError("action must be an object or null.")
            tool_name = action.get("tool")
            if not isinstance(tool_name, str) or not tool_name.strip():
                raise DeepThinkProtocolError("action.tool must be a non-empty string.")
            tool_params = action.get("params", {})
            if tool_params is None:
                tool_params = {}
            if not isinstance(tool_params, dict):
                tool_params = {}
            result["tool_name"] = tool_name.strip()
            result["tool_params"] = tool_params
            result["action_str"] = json.dumps(
                {"tool": result["tool_name"], "params": tool_params},
                ensure_ascii=False,
            )

        return result

    def _repair_json_text(self, text: str) -> str:
        repaired = (text or "").strip()
        if not repaired:
            return repaired
        repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
        repaired = repaired.replace("None", "null")
        if "'" in repaired and '"' not in repaired:
            repaired = repaired.replace("'", '"')
        return repaired

    def _regex_parse_fallback(self, text: str) -> Optional[Dict[str, Any]]:
        body = (text or "").strip()
        if not body:
            return None
        thinking_match = re.search(r'"thinking"\s*:\s*"([^"]*)"', body, re.DOTALL)
        final_match = re.search(r'"answer"\s*:\s*"([^"]+)"', body, re.DOTALL)
        tool_match = re.search(r'"tool"\s*:\s*"([^"]+)"', body)

        payload: Dict[str, Any] = {}
        if thinking_match:
            payload["thinking"] = thinking_match.group(1)
        if final_match:
            payload["final_answer"] = {"answer": final_match.group(1), "confidence": 0.6}
        if tool_match:
            payload["action"] = {"tool": tool_match.group(1), "params": {}}
        return payload or None

    def _extract_json(self, text: str) -> str:
        """Extract the first complete top-level JSON object."""
        text = (text or "").strip()
        if not text:
            raise DeepThinkProtocolError("LLM output is empty.")

        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].lstrip().startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        brace_count = 0
        start_idx = -1
        for i, char in enumerate(text):
            if char == '{':
                if brace_count == 0:
                    start_idx = i
                brace_count += 1
            elif char == '}':
                if brace_count == 0:
                    continue
                brace_count -= 1
                if brace_count == 0 and start_idx >= 0:
                    return text[start_idx:i+1]

        raise DeepThinkProtocolError("No complete JSON object found in LLM output.")

    def _fallback_answer_from_steps(self, steps: List[ThinkingStep]) -> str:
        if not steps:
            return "DeepThink completed without structured final answer."
        useful = [s.thought for s in steps if isinstance(s.thought, str) and s.thought.strip()]
        if not useful:
            return "DeepThink completed but no reliable answer was produced."
        return useful[-1].strip()

    async def _generate_summary(self, steps: List[ThinkingStep], user_query: str) -> str:
        """Generate a concise DeepThink summary via LLM in strict mode."""
        if not steps:
            raise DeepThinkProtocolError(
                "DeepThink summary generation requires at least one thinking step."
            )

        if not hasattr(self.llm_client, "chat_async"):
            raise DeepThinkProtocolError(
                "LLM client does not support chat_async required for summary generation."
            )

        tool_calls = [s for s in steps if s.action]
        tools_used = []
        for step in tool_calls:
            action_raw = step.action or ""
            try:
                payload = json.loads(action_raw)
            except Exception:
                continue
            tool_name = payload.get("tool")
            if isinstance(tool_name, str) and tool_name.strip():
                tools_used.append(tool_name.strip())
        tools_used = list(dict.fromkeys(tools_used))

        steps_lines: List[str] = []
        for step in steps[:5]:
            thought_preview = (
                f"{step.thought[:150]}..." if len(step.thought) > 150 else step.thought
            )
            steps_lines.append(f"- Step {step.iteration}: {thought_preview}")
        steps_text = "\n".join(steps_lines)

        prompt = f"""Summarize this thinking process in 1-2 concise English sentences:
User Question: {user_query[:200]}
Thinking Steps:
{steps_text}
Tools Used: {", ".join(tools_used) if tools_used else "None"}

Summary:"""

        try:
            summary = await asyncio.wait_for(
                self.llm_client.chat_async(prompt=prompt, max_tokens=150),
                timeout=10,
            )
        except Exception as exc:
            raise DeepThinkProtocolError(
                "LLM summary generation failed in strict mode."
            ) from exc

        cleaned = str(summary or "").strip()
        if len(cleaned) <= 10:
            raise DeepThinkProtocolError(
                "LLM summary output is empty or too short in strict mode."
            )
        return cleaned
