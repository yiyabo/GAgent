"""Pipeline operations behind the HTTP routes: drafts, frozen versions, runs, files and previews.

A draft is stored as the editor sent it (a half-built pipeline must survive a save); freezing and running
need a graph that validates. Each pipeline has its own workspace session, ``pipeline_<id>``: uploads go to
its ``uploads/`` folder and run outputs to ``pipeline_runs/<run id>/``. That session is not a chat, so it
never appears in the chat list.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.database_pool import get_db
from app.repository import pipelines as repo
from app.services.session_paths import get_runtime_session_dir

from . import engine, templates
from .graph import empty_graph, graph_hash, validate_graph
from .node_specs import get_spec, public_catalog

logger = logging.getLogger(__name__)

MAX_NAME_CHARS = 120
MAX_DESCRIPTION_CHARS = 2000
MAX_DRAFT_BYTES = 256 * 1024
MAX_ACTIVE_RUNS_PER_OWNER = 3
PREVIEW_BYTES = 64 * 1024
MAX_LISTING = 500
UPLOAD_EXTENSIONS = (".fasta", ".fa", ".fna", ".faa", ".ffn", ".fas", ".fsa", ".hmm")
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class PipelineError(Exception):
    def __init__(self, status: int, message: str, extra: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.status = status
        self.extra = extra or {}


def enabled() -> bool:
    try:
        from app.services.foundation.settings import get_settings

        return bool(getattr(get_settings(), "pipelines_enabled", False))
    except Exception:  # noqa: BLE001
        return False


def _max_upload_bytes() -> int:
    try:
        from app.services.foundation.settings import get_settings

        megabytes = int(getattr(get_settings(), "pipelines_max_upload_mb", 1024) or 1024)
    except Exception:  # noqa: BLE001
        megabytes = 1024
    return max(1, megabytes) * 1024 * 1024


def session_dir(pipeline_id: str) -> Path:
    return Path(get_runtime_session_dir(engine.pipeline_session_id(pipeline_id), create=True))


def _clean_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    return "".join(char for char in text if ord(char) >= 32 or char in "\n\t")[:limit]


def _draft_from(raw: Any) -> Dict[str, Any]:
    if raw is None:
        return empty_graph()
    if not isinstance(raw, dict):
        raise PipelineError(422, "graph must be a JSON object")
    if len(json.dumps(raw, ensure_ascii=False).encode("utf-8")) > MAX_DRAFT_BYTES:
        raise PipelineError(413, "the pipeline is too large to save")
    return raw


# ------------------------------------------------------------------ pipelines
def catalog() -> Dict[str, Any]:
    return {**public_catalog(), "templates": templates.public_list()}


def list_for(owner_id: str) -> List[Dict[str, Any]]:
    with get_db() as conn:
        return repo.list_pipelines(conn, owner_id)


def create(owner_id: str, name: Any, description: Any, graph: Any, template: Any = None) -> Dict[str, Any]:
    title = _clean_text(name, MAX_NAME_CHARS)
    if not title:
        raise PipelineError(422, "name is required")
    if template:
        if graph is not None:
            raise PipelineError(422, "give a graph or a template, not both")
        graph = templates.graph_for(str(template))
        if graph is None:
            raise PipelineError(422, f"unknown template {template!r}")
    draft = _draft_from(graph)
    with get_db() as conn:
        pipeline_id = repo.create_pipeline(conn, owner_id, title, _clean_text(description, MAX_DESCRIPTION_CHARS), draft)
    return detail(pipeline_id)


def load(pipeline_id: str) -> Dict[str, Any]:
    with get_db() as conn:
        pipeline = repo.get_pipeline(conn, pipeline_id)
    if pipeline is None:
        raise PipelineError(404, "pipeline not found")
    return pipeline


def detail(pipeline_id: str) -> Dict[str, Any]:
    with get_db() as conn:
        pipeline = repo.get_pipeline(conn, pipeline_id)
        if pipeline is None:
            raise PipelineError(404, "pipeline not found")
        versions = repo.list_versions(conn, pipeline_id)
        active = repo.count_active_runs(conn, pipeline_id)
    pipeline["versions"] = versions
    pipeline["active_runs"] = active
    # Checked as for a run, so the editor also flags input nodes that have no file yet.
    pipeline["validation"] = validate_graph(pipeline["draft"], for_run=True).as_dict()
    return pipeline


def update(pipeline_id: str, *, name: Any = None, description: Any = None, graph: Any = None) -> Dict[str, Any]:
    load(pipeline_id)
    title = None if name is None else _clean_text(name, MAX_NAME_CHARS)
    if title is not None and not title:
        raise PipelineError(422, "name cannot be empty")
    with get_db() as conn:
        repo.update_pipeline(
            conn,
            pipeline_id,
            name=title,
            description=None if description is None else _clean_text(description, MAX_DESCRIPTION_CHARS),
            draft=None if graph is None else _draft_from(graph),
        )
    return detail(pipeline_id)


def delete(pipeline_id: str) -> None:
    load(pipeline_id)
    with get_db() as conn:
        if repo.count_active_runs(conn, pipeline_id):
            raise PipelineError(409, "the pipeline has a run in progress; cancel it first")
        repo.archive_pipeline(conn, pipeline_id)


def freeze(pipeline_id: str) -> Dict[str, Any]:
    """Freeze the current draft as a version; an unchanged draft returns the latest version."""
    pipeline = load(pipeline_id)
    validation = validate_graph(pipeline["draft"])
    if not validation.ok:
        raise PipelineError(422, "the pipeline has errors", {"validation": validation.as_dict()})
    with get_db() as conn:
        version = repo.freeze_version(conn, pipeline_id, validation.graph, graph_hash(validation.graph))
    return {"pipeline_id": pipeline_id, "version": version, "validation": validation.as_dict()}


# ------------------------------------------------------------------ runs
def _input_paths(graph: Dict[str, Any]) -> Dict[str, str]:
    paths = {}
    for node_id, node in (graph.get("nodes") or {}).items():
        spec = get_spec(node.get("type"))
        if spec is not None and spec.kind == "input":
            paths[node_id] = str((node.get("params") or {}).get("path") or "")
    return paths


def _check_input_files(pipeline_id: str, inputs: Dict[str, str]) -> List[Dict[str, str]]:
    root = session_dir(pipeline_id).resolve()
    problems = []
    for node_id, raw in inputs.items():
        path = Path(raw)
        if not path.is_absolute():
            path = root / raw
        try:
            resolved = path.resolve()
            resolved.relative_to(root)
        except (OSError, ValueError):
            problems.append({"node": node_id, "code": "foreign_file", "message": "the file is not one of this pipeline's files"})
            continue
        if not resolved.is_file():
            problems.append({"node": node_id, "code": "missing_file", "message": f"{path.name} does not exist"})
    return problems


def start_run(pipeline_id: str, owner_id: str, inputs: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    pipeline = load(pipeline_id)
    overrides = {str(key): str(value) for key, value in (inputs or {}).items() if value}
    frozen = freeze(pipeline_id)
    version = frozen["version"]
    with get_db() as conn:
        graph = repo.get_version(conn, pipeline_id, version)["graph"]
    effective = _input_paths(graph)
    unknown = sorted(set(overrides) - set(effective))
    if unknown:
        raise PipelineError(422, "inputs name nodes that are not input nodes: " + ", ".join(unknown))
    effective.update(overrides)
    patched = json.loads(json.dumps(graph))
    for node_id, path in effective.items():
        patched["nodes"][node_id].setdefault("params", {})["path"] = path
    validation = validate_graph(patched, for_run=True)
    if not validation.ok:
        raise PipelineError(422, "the pipeline cannot run yet", {"validation": validation.as_dict()})
    problems = _check_input_files(pipeline_id, effective)
    if problems:
        raise PipelineError(422, "some input files are not usable", {"problems": problems})
    with get_db() as conn:
        active = conn.execute(
            "SELECT COUNT(*) FROM pipeline_runs WHERE owner_id=? AND status IN ('queued','running')", (owner_id,)
        ).fetchone()[0]
        if active >= MAX_ACTIVE_RUNS_PER_OWNER:
            raise PipelineError(429, f"you already have {active} pipeline runs in progress; wait for one to finish")
        run_id = repo.create_run(
            conn,
            pipeline_id=pipeline_id,
            version=version,
            owner_id=owner_id,
            session_id=engine.pipeline_session_id(pipeline_id),
            inputs=effective,
        )
    logger.info("pipeline %s (%s) v%s: run %s queued", pipeline_id, pipeline["name"], version, run_id)
    launch(run_id)
    return run_detail(run_id)


def launch(run_id: str) -> None:
    thread = threading.Thread(target=_run_in_thread, args=(run_id,), name=f"pipeline-run-{run_id}", daemon=True)
    thread.start()


def _run_in_thread(run_id: str) -> None:
    async def _main() -> None:
        try:
            await engine.execute_run(run_id)
        finally:
            try:
                from app.llm import close_current_loop_async_client

                await close_current_loop_async_client()
            except Exception:  # noqa: BLE001
                pass

    try:
        asyncio.run(_main())
    except Exception as exc:  # noqa: BLE001
        logger.exception("pipeline run %s crashed outside the engine", run_id)
        with get_db() as conn:
            repo.finish_run(conn, run_id, "failed", f"internal error: {type(exc).__name__}: {exc}")


def load_run(run_id: str) -> Dict[str, Any]:
    with get_db() as conn:
        run = repo.get_run(conn, run_id)
    if run is None:
        raise PipelineError(404, "run not found")
    return run


def run_detail(run_id: str) -> Dict[str, Any]:
    with get_db() as conn:
        run = repo.get_run(conn, run_id)
        if run is None:
            raise PipelineError(404, "run not found")
        version = repo.get_version(conn, run["pipeline_id"], run["version"])
        nodes = repo.list_node_runs(conn, run_id)
    run["graph"] = version["graph"] if version else empty_graph()
    run["nodes"] = {item["node_id"]: _public_node_run(item) for item in nodes}
    return run


def _public_node_run(item: Dict[str, Any]) -> Dict[str, Any]:
    outputs = {}
    for name, entry in (item.get("outputs") or {}).items():
        outputs[name] = {key: entry.get(key) for key in ("type", "kind", "size") if entry.get(key) is not None}
        outputs[name]["name"] = Path(str(entry.get("path") or "")).name
    detail = dict(item.get("detail") or {})
    detail.pop("work_dir", None)
    return {
        "status": item.get("status"),
        "error": item.get("error"),
        "started_at": item.get("started_at"),
        "finished_at": item.get("finished_at"),
        "outputs": outputs,
        "detail": detail,
    }


def list_runs(pipeline_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    with get_db() as conn:
        return repo.list_runs(conn, pipeline_id, limit)


def cancel_run(run_id: str) -> Dict[str, Any]:
    load_run(run_id)
    with get_db() as conn:
        changed = repo.request_cancel(conn, run_id)
    if not changed:
        raise PipelineError(409, "the run is not in progress")
    return run_detail(run_id)


def recover_on_startup() -> int:
    with get_db() as conn:
        return repo.fail_interrupted_runs(conn)


# ------------------------------------------------------------------ files
# Paths are relative to the pipeline's session, which is how input nodes store them: no server path in the
# graph, and the engine resolves them inside that session only.
def list_files(pipeline_id: str) -> List[Dict[str, Any]]:
    uploads = session_dir(pipeline_id) / "uploads"
    if not uploads.is_dir():
        return []
    files = []
    for item in sorted(uploads.iterdir(), key=lambda entry: entry.stat().st_mtime, reverse=True):
        if item.is_file():
            stat = item.stat()
            files.append({"name": item.name, "path": f"uploads/{item.name}", "size": stat.st_size, "modified": stat.st_mtime})
    return files


async def save_upload(pipeline_id: str, upload: Any) -> Dict[str, Any]:
    original = os.path.basename(str(getattr(upload, "filename", "") or ""))
    safe = _SAFE_NAME.sub("_", original).strip("._")[:150]
    if not safe or not safe.lower().endswith(UPLOAD_EXTENSIONS):
        raise PipelineError(415, "upload a FASTA (.fasta .fa .fna .faa .ffn .fas .fsa) or HMM (.hmm) file")
    uploads = session_dir(pipeline_id) / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    target = uploads / f"{uuid.uuid4().hex[:8]}_{safe}"
    limit = _max_upload_bytes()
    size = 0
    try:
        with open(target, "wb") as handle:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise PipelineError(413, f"the file is larger than {limit // (1024 * 1024)} MB")
                handle.write(chunk)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return {"name": target.name, "path": f"uploads/{target.name}", "size": size, "original_name": original}


def _output_entry(run_id: str, node_id: str, port: str) -> Dict[str, Any]:
    with get_db() as conn:
        node = repo.get_node_run(conn, run_id, node_id)
    if node is None:
        raise PipelineError(404, "node not found in this run")
    entry = (node.get("outputs") or {}).get(port)
    if not entry or not entry.get("path"):
        raise PipelineError(404, "this output does not exist")
    return entry


def output_file(run_id: str, node_id: str, port: str, rel: Optional[str] = None) -> Path:
    """The file behind an output (or inside an output folder), checked to belong to that output."""
    entry = _output_entry(run_id, node_id, port)
    base = Path(str(entry["path"]))
    if entry.get("kind") == "prefix":
        raise PipelineError(415, "a database is not a single file")
    if entry.get("kind") == "dir" or base.is_dir():
        if not rel:
            raise PipelineError(422, "choose a file inside this folder")
        target = (base / rel).resolve()
        try:
            target.relative_to(base.resolve())
        except ValueError:
            raise PipelineError(403, "the path is outside this output") from None
    else:
        target = base
    if not target.is_file():
        raise PipelineError(404, "the file no longer exists")
    return target


def preview(run_id: str, node_id: str, port: str, rel: Optional[str] = None) -> Dict[str, Any]:
    entry = _output_entry(run_id, node_id, port)
    base = Path(str(entry["path"]))
    if (entry.get("kind") == "dir" or base.is_dir()) and not rel:
        if not base.is_dir():
            raise PipelineError(404, "the folder no longer exists")
        items = []
        for item in sorted(base.rglob("*")):
            if item.is_file():
                items.append({"path": str(item.relative_to(base)), "size": item.stat().st_size})
                if len(items) >= MAX_LISTING:
                    break
        return {"kind": "dir", "entries": items, "truncated": len(items) >= MAX_LISTING}
    if entry.get("kind") == "prefix":
        parent = base.parent
        files = sorted(child.name for child in parent.iterdir() if child.name.startswith(base.name + ".")) if parent.is_dir() else []
        return {"kind": "prefix", "entries": [{"path": name} for name in files], "truncated": False}
    target = output_file(run_id, node_id, port, rel)
    with open(target, "rb") as handle:
        data = handle.read(PREVIEW_BYTES + 1)
    if b"\x00" in data[:8192]:
        return {"kind": "binary", "name": target.name, "size": target.stat().st_size}
    text = data[:PREVIEW_BYTES].decode("utf-8", errors="replace")
    return {
        "kind": "text",
        "name": target.name,
        "size": target.stat().st_size,
        "text": text,
        "truncated": len(data) > PREVIEW_BYTES,
    }
