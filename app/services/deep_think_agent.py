import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from app.services.execution.tool_executor import UnifiedToolExecutor

logger = logging.getLogger(__name__)


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
    FINAL_STREAM_CHUNK_CHARS = 1
    FINAL_STREAM_DELAY_SEC = 0.01

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
        self._pause_event = asyncio.Event()
        self._pause_event.set()
        self._skip_current_step = False

    def pause(self) -> None:
        self._pause_event.clear()

    def resume(self) -> None:
        self._pause_event.set()

    def skip_step(self) -> None:
        self._skip_current_step = True

    async def think(
        self,
        user_query: str,
        context: Optional[Dict[str, Any]] = None,
        task_context: Optional[TaskExecutionContext] = None,
    ) -> DeepThinkResult:
        """
        Executes the deep thinking loop with streaming output.

        Args:
            user_query: The user's question/request
            context: Optional context including chat_history, session_id, etc.

        Returns:
            DeepThinkResult with final answer and thinking steps

        Raises:
            ValueError: If user_query is empty or too long
        """
        # Input validation
        if not user_query or not user_query.strip():
            raise ValueError("User query cannot be empty")
        if len(user_query) > 10000:
            raise ValueError("User query too long (max 10000 chars)")

        user_query = user_query.strip()
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

    @staticmethod
    def _build_tool_callback_summary(result: Any) -> str:
        if isinstance(result, dict):
            if "summary" in result:
                return str(result["summary"])[:600]
            if "error" in result and result.get("error"):
                return str(result.get("error"))[:600]
        return str(result)[:600]

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
            "claude_code": "Execute Python/shell code. FALLBACK TOOL: Use this ONLY when bio_tools cannot handle the task (e.g., custom analysis scripts, complex data processing). For FASTA/FASTQ sequence stats or standard bioinformatics tasks, ALWAYS try bio_tools first. Params: {\"task\": \"description\"}",
            "web_search": "Search the internet for information. USE THIS ONLY for web-based queries, NOT for local files. Params: {\"query\": \"search query\"}",
            "graph_rag": "Query knowledge graph for structured information. Params: {\"query\": \"your question\", \"mode\": \"global|local|hybrid\"}",
            "file_operations": "File system operations: list directories, read/write files, copy/move/delete. USE THIS for quick directory listing or file reading. Params: {\"operation\": \"list|read|write|copy|move|delete\", \"path\": \"/path\"}",
            "document_reader": "Read local documents with format-aware parsing. Use this first for .docx/.pdf/.txt content extraction. Params: {\"operation\": \"read_any|read_pdf|read_text\", \"file_path\": \"/abs/path\"}",
            "vision_reader": "Read PDFs and images using vision model. Use for visual OCR/figures/equations, not for DOCX. Params: {\"operation\": \"read_pdf|read_image|ocr_page\", \"file_path\": \"/path/to/file\"}",
            "bio_tools": "PREFERRED for bioinformatics: Execute Docker-based tools for FASTA/FASTQ/sequence analysis. For sequence stats, use seqkit. Example: {\"tool_name\": \"seqkit\", \"operation\": \"stats\", \"input_file\": \"/absolute/path/to/file.fasta\"}. NOTE: input_file MUST be absolute path. Available tools: seqkit (stats, grep, seq), blast (blastn, blastp), prodigal (predict genes), hmmer (hmmscan), checkv (virus quality). Use operation='help' to see tool usage.",
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

=== CORE PRINCIPLES ===
1. You have UNLIMITED tokens and API calls - DO NOT worry about costs.
2. Use MULTIPLE TOOLS to gather complete information from different angles.
3. Be THOROUGH - it's better to call 5 tools than to give an incomplete answer.
4. Each tool provides unique insights - combine them for best results.

=== THINKING PROCESS ===
1. Break down the user's question into sub-problems.
2. For EACH sub-problem, determine which tool(s) can help.
3. Call tools ONE BY ONE, analyze results, then decide what else is needed.
4. Synthesize information from ALL tool results into a comprehensive answer.
5. Only provide final_answer when you have gathered enough information.

=== AVAILABLE TOOLS ===
{tools_text}

=== MULTI-TOOL STRATEGY ===
For comprehensive analysis, consider this workflow:
1. **Explore**: Use file_operations(list) to see what files exist
2. **Read**: Use document_reader or file_operations(read) to read key files
3. **Analyze**: Use claude_code for complex data analysis or code execution
4. **Visualize**: Use claude_code to create charts/visualizations if needed
5. **Research**: Use web_search for external context or comparison
6. **Vision**: Use vision_reader for images, figures, or scanned documents
7. **Knowledge**: Use graph_rag for structured knowledge queries

=== TOOL SELECTION GUIDE ===
- LOCAL DIRECTORY LISTING: file_operations(operation="list", path="...")
- READ TEXT FILES: file_operations(operation="read") or document_reader
- **BIOINFORMATICS (FASTA/FASTQ/sequences)**: bio_tools - MUST TRY FIRST for any .fasta, .fa, .fq, .fastq files. Use seqkit for stats, blast for alignment, prodigal for gene prediction.
- COMPLEX ANALYSIS/CODE: claude_code - ONLY use after bio_tools fails or for custom analysis not supported by bio_tools
- IMAGES/FIGURES/PDF: vision_reader - for OCR, figure description, equation reading
- WEB INFORMATION: web_search - for internet queries, background research
- KNOWLEDGE QUERIES: graph_rag - for structured knowledge retrieval
- **PLAN CREATION**: plan_operation - for creating and optimizing structured task plans

=== PLAN CREATION STRATEGY ===
When user asks to create a plan, research project, or structured task breakdown:

**Step 1: Research First (CRITICAL!)**
- Use web_search to gather information about:
  - Latest technologies and best practices for the domain
  - Similar successful projects or methodologies
  - Potential challenges and proven solutions
- This ensures your plan is based on current, accurate information

**Step 2: Create Initial Plan**
- Use plan_operation(operation="create") with:
  - Clear, descriptive title
  - Specific goal/description
  - Well-structured tasks with research-grade instructions
  - Proper dependencies between tasks

Research-grade instruction requirements (CRITICAL):
- Each task must specify: Objective; Rationale (why); Methods & Tools; Data/Inputs; Outputs/Artifacts;
  Baselines/Controls; Metrics & QC; Acceptance criteria; Reproducibility notes (parameters/versions/seeds).
- Avoid shallow or slogan-like steps.

**Step 3: Review Plan**
- Use plan_operation(operation="review") to check:
  - No circular dependencies
  - Task granularity is appropriate (not too coarse or too fine)
  - All necessary steps are included
  - Health score is acceptable (aim for 80+)
  - Rubric score is acceptable (aim for 80+), especially scientific_rigor and reproducibility

**Step 4: Iterate if Needed**
- If review finds issues, use plan_operation(operation="optimize") to:
  - Add missing tasks
  - Update unclear instructions
  - Fix dependency issues
  - Adjust task granularity
  - Add baselines/controls, explicit metrics/QC, and acceptance thresholds
  - Add tool/I-O/parameterization details for reproducibility
- Then review again until satisfied

**Step 5: Report Final Plan**
- Use plan_operation(operation="get") to show final structure
- Summarize the plan to the user with key tasks and timeline

=== BIOINFORMATICS PRIORITY RULE ===
When user asks about FASTA, FASTQ, or sequence files:
1. FIRST: Call bio_tools with tool_name="seqkit", operation="stats" to get basic stats
2. THEN: Decide if additional analysis is needed
3. ONLY IF bio_tools cannot do it: Fall back to claude_code for custom Python analysis

IMPORTANT - bio_tools operations (do NOT guess, use these exact names):
- seqkit: stats, grep, seq, head
- blast: blastn, blastp, makeblastdb (requires database param)
- prodigal: predict, meta (requires protein_output param)
- hmmer: hmmscan, hmmsearch (requires database param)
- checkv: end_to_end, completeness (requires database param)

=== BIO_TOOLS SELF-CORRECTION PROTOCOL (CRITICAL!) ===
When bio_tools fails, follow this recovery sequence. NEVER give up after first failure!

**Step 1: Check Help**
- Call bio_tools(tool_name="xxx", operation="help") to see exact parameters
- Read the output carefully for required vs optional params

**Step 2: Web Search (if help is unclear)**
- Call web_search(query="<tool_name> bioinformatics usage parameters example")
- Look for official documentation or tutorials

**Step 3: Inspect via Shell (if still failing)**
- Call claude_code with task: "Run <tool> --help to see command options"
- Check if input file format is correct with: seqkit stats <file>

**Step 4: Try Alternative Tools**
- seqkit failed → try biopython via claude_code
- blast failed → check database path, try diamond as alternative
- prodigal failed → check input format with seqkit first
- assembly tools → verify FASTA format, check sequence lengths

**Error Pattern Recognition:**
- "Permission denied" → check file paths and permissions
- "File not found" → verify absolute path, check if file exists with ls
- "Invalid parameter" → call help, check parameter spelling/format
- "Database not found" → verify database param path is correct
- "Memory error" → try smaller input or request chunked processing
- "Docker error" → report to user, may need admin intervention

**Example Recovery Flow:**
1. Error: "Execution failed: 'protein_output'"
2. Action: bio_tools(tool_name="prodigal", operation="help")
3. Learn: protein_output is required, should be a file path
4. Retry: bio_tools(tool_name="prodigal", operation="predict", input_file="...", params={{"protein_output": "output.faa"}})
5. Still failing? → web_search("prodigal protein prediction parameters")
6. Still failing? → claude_code to run prodigal directly and debug

Try at least 3 different approaches before reporting failure to user!

DO NOT use vision_reader for BIO files - it's only for PDFs and images!

=== IMPORTANT ===
- DO NOT hesitate to use multiple tools - resources are unlimited!
- Call different tools to get different perspectives on the same data.
- If one tool's result is incomplete, try another tool for more info.
- For file analysis: first LIST the directory, then READ interesting files, then ANALYZE with code.

=== OUTPUT FORMAT ===
Respond with valid JSON only (no markdown fences):

{{
  "thinking": "Describe what you're analyzing and WHY you're choosing this tool...",
  "action": {{"tool": "tool_name", "params": {{...}}}},
  "final_answer": null
}}

When ready to answer (after using multiple tools):
{{
  "thinking": "Synthesizing information from [tool1], [tool2], [tool3]...",
  "action": null,
  "final_answer": {{"answer": "Comprehensive answer here", "confidence": 0.9}}
}}

=== RULES ===
1. Output valid JSON only.
2. Use "action" to call tools, one at a time per response.
3. Call MULTIPLE tools before providing final_answer.
4. Include which tools you used in your final answer's thinking.
5. Be thorough - incomplete answers are worse than using more tools.
6. For PhageScope submit, prioritize non-blocking backend execution over immediate result fetching.
"""

        # Append recent conversation context
        if context:
            chat_history = context.get("chat_history", [])
            if chat_history:
                history_lines = []
                recent = chat_history[-30:] if len(chat_history) > 30 else chat_history
                for msg in recent:
                    role = msg.get("role", "unknown")
                    content = msg.get("content", "")
                    if len(content) > 500:
                        content = content[:500] + "..."
                    history_lines.append(f"[{role}]: {content}")
                base_prompt += f"\n=== RECENT CONVERSATION ===\n" + "\n".join(history_lines)

        return base_prompt

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
