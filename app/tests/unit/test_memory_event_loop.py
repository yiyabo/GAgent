"""Memory saves must not block the chat event loop.

Memory saves run as tasks on the chat event loop (session_helpers starts them with asyncio.create_task), so a
synchronous LLM or embedding call inside an async memory function stalls every stream, and the run lease
heartbeat, for as long as the provider takes. A slow embedding request (60 s read timeout) once outlasted the
30 s lease and got a healthy run reaped as worker_lease_expired.
"""

import ast
import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

from app.services.memory.memory_service import IntegratedMemoryService

STALL_SECONDS = 0.4
MAX_TICK_GAP_SECONDS = 0.2
MEMORY_PACKAGE = Path(__file__).resolve().parents[2] / "services" / "memory"
BLOCKING_METHODS = {"chat", "get_single_embedding", "get_embeddings"}


class _SlowLLM:
    def chat(self, prompt):
        time.sleep(STALL_SECONDS)
        return '{"keywords": ["k"], "context": "c", "tags": ["t"]}'


class _SlowEmbeddings:
    def get_single_embedding(self, text):
        time.sleep(STALL_SECONDS)
        return []


def _service() -> IntegratedMemoryService:
    service = IntegratedMemoryService.__new__(IntegratedMemoryService)
    service.llm_client = _SlowLLM()
    service.embeddings_service = _SlowEmbeddings()
    return service


async def _longest_tick_gap_while(coro):
    """Run coro next to a ticker task and return (its result, the longest gap between two ticks)."""
    gaps = []
    stop = asyncio.Event()

    async def ticker():
        last = time.monotonic()
        while not stop.is_set():
            await asyncio.sleep(0.01)
            now = time.monotonic()
            gaps.append(now - last)
            last = now

    task = asyncio.create_task(ticker())
    await asyncio.sleep(0.05)
    try:
        result = await coro
    finally:
        stop.set()
        await task
    return result, max(gaps)


async def test_analyze_content_does_not_block_the_event_loop():
    result, gap = await _longest_tick_gap_while(_service()._analyze_content("a memory worth keeping"))
    assert result["keywords"] == ["k"]
    assert gap < MAX_TICK_GAP_SECONDS, f"event loop stalled for {gap:.2f} s"


async def test_generate_embedding_does_not_block_the_event_loop():
    note = SimpleNamespace(id="m1", content="content", keywords=[], context="General", tags=[])
    saved, gap = await _longest_tick_gap_while(_service()._generate_embedding(note))
    assert saved is False  # the stub returns no vector, so nothing is written
    assert gap < MAX_TICK_GAP_SECONDS, f"event loop stalled for {gap:.2f} s"


async def test_semantic_search_does_not_block_the_event_loop():
    service = _service()

    async def no_text_hits(*args, **kwargs):
        return []

    service._text_search = no_text_hits
    results, gap = await _longest_tick_gap_while(service._semantic_search("query", [], [], 5, 0.0))
    assert results == []
    assert gap < MAX_TICK_GAP_SECONDS, f"event loop stalled for {gap:.2f} s"


def _direct_blocking_calls(source: str, name: str) -> list:
    """Calls to a synchronous LLM or embedding method made directly inside an async function.

    Closures and lambdas are skipped (they are handed to a thread), and so is a call that is itself awaited.
    """
    tree = ast.parse(source)
    found = []

    def visit(node, in_async):
        if isinstance(node, (ast.FunctionDef, ast.Lambda)):
            in_async = False
        elif isinstance(node, ast.AsyncFunctionDef):
            in_async = True
        if isinstance(node, ast.Await) and isinstance(node.value, ast.Call):
            for child in ast.iter_child_nodes(node.value):
                visit(child, in_async)
            return
        if (
            in_async
            and isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in BLOCKING_METHODS
        ):
            found.append(f"{name}:{node.lineno} {ast.unparse(node.func)}")
        for child in ast.iter_child_nodes(node):
            visit(child, in_async)

    visit(tree, False)
    return found


def test_the_checker_flags_a_direct_call_and_accepts_the_threaded_forms():
    bad = "class A:\n    async def f(self):\n        return self.llm_client.chat('x')\n"
    assert _direct_blocking_calls(bad, "bad.py") == ["bad.py:3 self.llm_client.chat"]
    threaded = (
        "import asyncio\n"
        "class A:\n"
        "    async def f(self, loop):\n"
        "        a = await asyncio.to_thread(self.llm_client.chat, 'x')\n"
        "        b = await loop.run_in_executor(None, lambda: self.embeddings_service.get_single_embedding('y'))\n"
        "        return a, b\n"
        "    def sync_ok(self):\n"
        "        return self.llm_client.chat('z')\n"
    )
    assert _direct_blocking_calls(threaded, "ok.py") == []


def test_no_synchronous_llm_or_embedding_calls_in_async_memory_code():
    offenders = []
    for path in sorted(MEMORY_PACKAGE.glob("*.py")):
        offenders += _direct_blocking_calls(path.read_text(encoding="utf-8"), path.name)
    assert not offenders, "blocking calls on the event loop: " + ", ".join(offenders)
