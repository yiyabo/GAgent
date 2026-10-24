"""Runs one frozen pipeline: every node in dependency order, independent nodes in parallel.

Each tool node is one ``bio_tools`` call (one Slurm job in native mode). Its outputs are read from the
run's own work directory, which holds everything the tool wrote; the session copy that ``bio_tools``
publishes stops at 60 files, too few for tools that write a folder. Later tool nodes may read those
directories because ``bio_tools`` treats its run directories as readable by every session.

A node is skipped when something it reads did not finish. Results are reused (status ``cached``) when the
same owner already ran the same node type with the same parameters on the same inputs and the files are
still there.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from app.database_pool import get_db
from app.repository import pipelines as repo
from app.services.session_paths import get_runtime_session_dir

from . import report
from .graph import refs_of, split_accessions, upstream_nodes, validate_graph
from .node_specs import SPEC_VERSION, NodeSpec, get_spec

logger = logging.getLogger(__name__)

POLL_SECONDS = 2.0
STDERR_TAIL_CHARS = 4000
DONE = ("succeeded", "cached")
BLOCKING = ("failed", "skipped", "cancelled")
_VERSIONED = re.compile(r"^[A-Za-z0-9_]+\.\d+$")


class NodeFailure(Exception):
    """A node that ran and did not produce what its outputs promise."""

    def __init__(self, message: str, detail: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.detail = detail or {}


@dataclass
class NodeOutcome:
    status: str
    outputs: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    cache_key: Optional[str] = None
    detail: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RunContext:
    run_id: str
    owner_id: str
    session_id: str
    session_dir: Path
    run_dir: Path
    graph: Dict[str, Any]
    max_parallel: int
    outcomes: Dict[str, NodeOutcome] = field(default_factory=dict)


# ------------------------------------------------------------------ helpers
def pipeline_session_id(pipeline_id: str) -> str:
    return f"pipeline_{pipeline_id}"


def _settings_max_parallel() -> int:
    try:
        from app.services.foundation.settings import get_settings

        return max(1, int(getattr(get_settings(), "pipelines_max_parallel_nodes", 4) or 4))
    except Exception:  # noqa: BLE001 - a settings problem must not stop a run
        return 4


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tool_fingerprint(spec: NodeSpec) -> str:
    """Changes when the bio_tools definition of the operation changes."""
    try:
        from tool_box.bio_tools import get_tools_config

        operation = (get_tools_config().get(spec.tool) or {}).get("operations", {}).get(spec.operation) or {}
    except Exception:  # noqa: BLE001
        operation = {}
    text = json.dumps(operation, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def cache_key_for(spec: NodeSpec, params: Dict[str, Any], input_keys: Dict[str, List[Optional[str]]]) -> Optional[str]:
    if not spec.cacheable or any(key is None for keys in input_keys.values() for key in keys):
        return None
    payload = {
        "spec_version": SPEC_VERSION,
        "type": spec.type,
        "tool": _tool_fingerprint(spec) if spec.kind == "tool" else None,
        "params": params,
        "fixed": dict(spec.fixed_params),
        "inputs": input_keys,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _output_exists(entry: Dict[str, Any]) -> bool:
    path = Path(str(entry.get("path") or ""))
    kind = entry.get("kind")
    if kind == "prefix":
        parent = path.parent
        return parent.is_dir() and any(child.name.startswith(path.name + ".") for child in parent.iterdir())
    if kind == "dir":
        return path.is_dir()
    return path.is_file()


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


# ------------------------------------------------------------------ node executors
def _resolve_input_file(ctx: RunContext, node_id: str, spec: NodeSpec, params: Dict[str, Any]) -> NodeOutcome:
    raw = str(params.get("path") or "").strip()
    if not raw:
        raise NodeFailure("no file is selected for this input")
    path = Path(raw)
    if not path.is_absolute():
        path = ctx.session_dir / raw
    if not _inside(path, ctx.session_dir):
        raise NodeFailure("the selected file is not one of this pipeline's files")
    if not path.is_file():
        raise NodeFailure(f"the selected file no longer exists: {path.name}")
    port = spec.outputs[0]
    outputs = {port.name: {"path": str(path.resolve()), "type": port.type, "kind": "file", "size": path.stat().st_size}}
    digest = _sha256_file(path)
    return NodeOutcome("succeeded", outputs, cache_key="file:" + digest)


async def _run_ncbi(ctx: RunContext, node_id: str, spec: NodeSpec, params: Dict[str, Any]) -> NodeOutcome:
    from tool_box.tools_impl.sequence_fetch import sequence_fetch_handler

    accessions = split_accessions(params.get("accessions"))
    if not accessions:
        raise NodeFailure("no accessions given")
    result = await sequence_fetch_handler(
        accessions=accessions,
        database=spec.fixed_params.get("database", "nuccore"),
        format=spec.fixed_params.get("format", "fasta"),
        session_id=ctx.session_id,
        output_name=f"{ctx.run_id}_{node_id}",
    )
    if not result.get("success") or not result.get("output_file"):
        raise NodeFailure(
            str(result.get("error") or "the download failed"),
            {"error_code": result.get("error_code")},
        )
    port = spec.outputs[0]
    path = Path(str(result["output_file"]))
    outputs = {port.name: {"path": str(path), "type": port.type, "kind": "file", "size": path.stat().st_size}}
    detail = {"record_count": result.get("record_count"), "provider": result.get("provider")}
    return NodeOutcome("succeeded", outputs, detail=detail)


def _tool_call(ctx: RunContext, node: Dict[str, Any], spec: NodeSpec, params: Dict[str, Any]) -> Dict[str, Any]:
    call_params: Dict[str, Any] = dict(spec.fixed_params)
    call_params.update({key: value for key, value in params.items()})
    input_file: Optional[str] = None
    for port in spec.inputs:
        refs = refs_of((node.get("inputs") or {}).get(port.name))
        if not refs:
            continue
        upstream = ctx.outcomes[refs[0]["node"]].outputs.get(refs[0]["port"])
        if not upstream:
            raise NodeFailure(f"input {port.name}: the upstream node did not produce {refs[0]['port']}")
        if port.param:
            call_params[port.param] = upstream["path"]
        else:
            input_file = upstream["path"]
    return {"tool_name": spec.tool, "operation": spec.operation, "input_file": input_file, "params": call_params}


def _resolve_tool_outputs(ctx: RunContext, node_id: str, spec: NodeSpec, result: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    base_text = str(result.get("work_dir") or result.get("session_output_dir") or "")
    base = Path(base_text) if base_text else None
    outputs: Dict[str, Dict[str, Any]] = {}
    for port in spec.outputs:
        entry: Optional[Dict[str, Any]] = None
        if port.kind == "stdout":
            target = ctx.run_dir / node_id / port.source
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(result.get("stdout") or ""), encoding="utf-8")
            entry = {"path": str(target), "type": port.type, "kind": "file"}
        elif base is not None:
            if port.kind == "glob":
                matches = sorted(base.glob(port.source))
                if matches:
                    entry = {"path": str(matches[0]), "type": port.type, "kind": "file"}
            else:
                entry = {"path": str(base / port.source), "type": port.type, "kind": port.kind}
                if not _output_exists(entry):
                    entry = None
        if entry is None:
            if port.optional:
                continue
            where = "the run directory" if base is not None else "the tool result (no run directory reported)"
            raise NodeFailure(f"the tool finished but {port.source or port.name} is missing from {where}")
        if entry["kind"] == "file":
            try:
                entry["size"] = Path(entry["path"]).stat().st_size
            except OSError:
                pass
        outputs[port.name] = entry
    return outputs


async def _run_tool(ctx: RunContext, node_id: str, node: Dict[str, Any], spec: NodeSpec, params: Dict[str, Any]) -> NodeOutcome:
    from tool_box.bio_tools import bio_tools_handler
    from tool_box.context import ToolContext

    call = _tool_call(ctx, node, spec, params)
    result = await bio_tools_handler(
        **call,
        timeout=0,  # each tool's own Slurm time limit applies
        session_id=ctx.session_id,
        tool_context=ToolContext(session_id=ctx.session_id, owner_id=ctx.owner_id, job_id=ctx.run_id),
    )
    detail = {
        key: result.get(key)
        for key in (
            "execution_mode",
            "run_id",
            "slurm_job_id",
            "exit_code",
            "duration_seconds",
            "command",
            "error_code",
            "work_dir",
        )
        if result.get(key) is not None
    }
    stderr = str(result.get("stderr") or "")
    if stderr:
        detail["stderr_tail"] = stderr[-STDERR_TAIL_CHARS:]
    if not result.get("success"):
        raise NodeFailure(str(result.get("error") or "the tool failed"), detail)
    outputs = _resolve_tool_outputs(ctx, node_id, spec, result)
    return NodeOutcome("succeeded", outputs, detail=detail)


async def _run_report(ctx: RunContext, node_id: str, node: Dict[str, Any], spec: NodeSpec, params: Dict[str, Any]) -> NodeOutcome:
    evidence: List[Dict[str, Any]] = []
    for ref in refs_of((node.get("inputs") or {}).get("evidence")):
        upstream_node = ctx.graph["nodes"].get(ref["node"]) or {}
        upstream_spec = get_spec(upstream_node.get("type"))
        output = ctx.outcomes[ref["node"]].outputs.get(ref["port"])
        if not output or upstream_spec is None:
            continue
        port = upstream_spec.output_port(ref["port"])
        evidence.append(
            {
                "node": ref["node"],
                "label": upstream_node.get("label") or upstream_spec.title.get("en"),
                "node_type": upstream_spec.type,
                "port": ref["port"],
                "port_title": port.title.get("en") if port else ref["port"],
                "type": output.get("type"),
                "kind": output.get("kind"),
                "path": output.get("path"),
            }
        )
    if not evidence:
        raise NodeFailure("none of the connected results is available")
    target = ctx.run_dir / node_id / spec.outputs[0].source
    detail = await report.write_report(
        target=target,
        evidence=evidence,
        instructions=str(params.get("instructions") or ""),
        language=str(params.get("language") or "zh"),
        session_id=ctx.session_id,
        run_id=ctx.run_id,
    )
    port = spec.outputs[0]
    outputs = {port.name: {"path": str(target), "type": port.type, "kind": "file", "size": target.stat().st_size}}
    return NodeOutcome("succeeded", outputs, detail=detail)


# ------------------------------------------------------------------ scheduling
def _input_keys(ctx: RunContext, node: Dict[str, Any]) -> Dict[str, List[Optional[str]]]:
    keys: Dict[str, List[Optional[str]]] = {}
    for port_name, value in sorted((node.get("inputs") or {}).items()):
        keys[port_name] = [
            f"{ctx.outcomes[ref['node']].cache_key}:{ref['port']}" if ctx.outcomes[ref["node"]].cache_key else None
            for ref in refs_of(value)
        ]
    return keys


def _ncbi_key(spec: NodeSpec, params: Dict[str, Any]) -> Optional[str]:
    accessions = split_accessions(params.get("accessions"))
    if not accessions or not all(_VERSIONED.match(item) for item in accessions):
        return None  # an unversioned accession may point at a newer record next time
    return cache_key_for(spec, {"accessions": sorted(accessions)}, {})


def _lookup_cache(ctx: RunContext, cache_key: Optional[str]) -> Optional[Dict[str, Any]]:
    if not cache_key:
        return None
    with get_db() as conn:
        candidates = repo.find_cached_outputs(conn, ctx.owner_id, cache_key)
    for candidate in candidates:
        outputs = candidate["outputs"]
        if outputs and all(_output_exists(entry) for entry in outputs.values()):
            return candidate
    return None


async def _run_node(ctx: RunContext, node_id: str, semaphore: asyncio.Semaphore) -> NodeOutcome:
    node = ctx.graph["nodes"][node_id]
    spec = get_spec(node["type"])
    assert spec is not None  # the graph was validated before the run started
    params = dict(node.get("params") or {})
    cache_key: Optional[str] = None
    try:
        if spec.kind == "input":
            with get_db() as conn:
                repo.set_node_running(conn, ctx.run_id, node_id, None)
            outcome = await asyncio.to_thread(_resolve_input_file, ctx, node_id, spec, params)
            with get_db() as conn:
                repo.finish_node(conn, ctx.run_id, node_id, "succeeded", outputs=outcome.outputs, cache_key=outcome.cache_key)
            return outcome

        if spec.kind == "ncbi":
            cache_key = _ncbi_key(spec, params)
        elif spec.kind == "tool":
            cache_key = cache_key_for(spec, params, _input_keys(ctx, node))

        async with semaphore:
            # Looked up only once a slot is free, so a duplicate of a node that just finished reuses it.
            cached = await asyncio.to_thread(_lookup_cache, ctx, cache_key)
            if cached is not None:
                outputs = cached["outputs"]
                detail = {"cached_from": {"run_id": cached["run_id"], "node_id": cached["node_id"]}}
                with get_db() as conn:
                    repo.finish_node(conn, ctx.run_id, node_id, "cached", outputs=outputs, detail=detail, cache_key=cache_key)
                return NodeOutcome("cached", outputs, cache_key)
            with get_db() as conn:
                repo.set_node_running(conn, ctx.run_id, node_id, cache_key)
            if spec.kind == "ncbi":
                outcome = await _run_ncbi(ctx, node_id, spec, params)
            elif spec.kind == "tool":
                outcome = await _run_tool(ctx, node_id, node, spec, params)
            else:
                outcome = await _run_report(ctx, node_id, node, spec, params)
        outcome.cache_key = cache_key
        with get_db() as conn:
            repo.finish_node(
                conn, ctx.run_id, node_id, "succeeded", outputs=outcome.outputs, detail=outcome.detail, cache_key=cache_key
            )
        return outcome
    except asyncio.CancelledError:
        with get_db() as conn:
            repo.finish_node(conn, ctx.run_id, node_id, "cancelled", error="the run was cancelled")
        raise
    except NodeFailure as exc:
        with get_db() as conn:
            repo.finish_node(conn, ctx.run_id, node_id, "failed", detail=exc.detail, error=str(exc))
        return NodeOutcome("failed")
    except Exception as exc:  # noqa: BLE001 - one node's bug must not take the run's bookkeeping with it
        logger.exception("pipeline node %s of run %s crashed", node_id, ctx.run_id)
        with get_db() as conn:
            repo.finish_node(conn, ctx.run_id, node_id, "failed", error=f"internal error: {type(exc).__name__}: {exc}")
        return NodeOutcome("failed")


def _apply_inputs(graph: Dict[str, Any], inputs: Dict[str, Any]) -> Dict[str, Any]:
    patched = json.loads(json.dumps(graph))
    for node_id, path in (inputs or {}).items():
        node = patched.get("nodes", {}).get(node_id)
        if node is not None:
            node.setdefault("params", {})["path"] = path
    return patched


async def execute_run(run_id: str, *, is_cancelled: Optional[Callable[[], bool]] = None) -> str:
    """Run every node of ``run_id`` and return the run's final status."""
    with get_db() as conn:
        run = repo.get_run(conn, run_id)
        version = repo.get_version(conn, run["pipeline_id"], run["version"]) if run else None
    if run is None or version is None:
        logger.error("pipeline run %s or its version is missing", run_id)
        return "failed"

    validation = validate_graph(_apply_inputs(version["graph"], run.get("inputs") or {}), for_run=True)
    if not validation.ok:
        message = "; ".join(issue.message for issue in validation.errors[:5])
        with get_db() as conn:
            repo.finish_run(conn, run_id, "failed", f"the pipeline is not valid: {message}")
        return "failed"

    session_dir = Path(get_runtime_session_dir(run["session_id"], create=True))
    ctx = RunContext(
        run_id=run_id,
        owner_id=run["owner_id"],
        session_id=run["session_id"],
        session_dir=session_dir,
        run_dir=session_dir / "pipeline_runs" / run_id,
        graph=validation.graph,
        max_parallel=_settings_max_parallel(),
    )
    ctx.run_dir.mkdir(parents=True, exist_ok=True)
    order = validation.order
    nodes = ctx.graph["nodes"]
    with get_db() as conn:
        repo.init_node_runs(conn, run_id, order)
        repo.mark_run_started(conn, run_id)

    def cancelled() -> bool:
        if is_cancelled is not None and is_cancelled():
            return True
        with get_db() as conn:
            return repo.cancel_requested(conn, run_id)

    states: Dict[str, str] = {node_id: "pending" for node_id in order}
    tasks: Dict[asyncio.Task, str] = {}
    semaphore = asyncio.Semaphore(ctx.max_parallel)
    final_status = "failed"
    error: Optional[str] = None
    try:
        while True:
            if cancelled():
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                with get_db() as conn:
                    for node_id, state in states.items():
                        if state == "pending":
                            repo.finish_node(conn, run_id, node_id, "cancelled", error="the run was cancelled")
                final_status, error = "cancelled", "cancelled by the user"
                break
            for node_id in order:
                if states[node_id] != "pending":
                    continue
                parents = upstream_nodes(nodes[node_id])
                if any(states[parent] in BLOCKING for parent in parents):
                    states[node_id] = "skipped"
                    with get_db() as conn:
                        repo.finish_node(conn, run_id, node_id, "skipped", error="a node it reads from did not finish")
                elif all(states[parent] in DONE for parent in parents):
                    states[node_id] = "running"
                    task = asyncio.create_task(_run_node(ctx, node_id, semaphore))
                    tasks[task] = node_id
            if not tasks:
                break
            done, _ = await asyncio.wait(list(tasks), timeout=POLL_SECONDS, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                node_id = tasks.pop(task)
                outcome = task.result()
                states[node_id] = outcome.status
                ctx.outcomes[node_id] = outcome
        if final_status != "cancelled":
            failed = [node_id for node_id, state in states.items() if state in BLOCKING]
            if failed:
                final_status = "failed"
                error = "not finished: " + ", ".join(failed)
            else:
                final_status = "succeeded"
    except Exception as exc:  # noqa: BLE001
        logger.exception("pipeline run %s crashed", run_id)
        for task in tasks:
            task.cancel()
        final_status, error = "failed", f"internal error: {type(exc).__name__}: {exc}"
    with get_db() as conn:
        repo.finish_run(conn, run_id, final_status, error)
    return final_status


__all__ = ["NodeFailure", "NodeOutcome", "cache_key_for", "execute_run", "pipeline_session_id"]
