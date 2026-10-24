"""HTTP surface of node pipelines (``/pipelines``).

Off unless ``PIPELINES_ENABLED`` is set: every route except ``/pipelines/status`` then answers 404, and the
editor hides its entry point. Every pipeline and run belongs to one owner; other users get 403.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse

from ..services.pipelines import service
from ..services.pipelines.graph import validate_graph
from ..services.request_principal import ensure_owner_access, get_request_owner_id
from . import register_router

router = APIRouter(prefix="/pipelines", tags=["pipelines"])

register_router(
    namespace="pipelines",
    version="v1",
    path="/pipelines",
    router=router,
    tags=["pipelines"],
    description="Node pipelines: typed tool graphs saved as versions and run as background jobs",
)


def _enabled() -> None:
    if not service.enabled():
        raise HTTPException(status_code=404, detail={"message": "pipelines are not enabled on this server"})


def _call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except service.PipelineError as exc:
        raise HTTPException(status_code=exc.status, detail={"message": str(exc), **exc.extra}) from None


def _owned_pipeline(request: Request, pipeline_id: str) -> Dict[str, Any]:
    pipeline = _call(service.load, pipeline_id)
    ensure_owner_access(request, pipeline.get("owner_id"), detail="pipeline owner mismatch")
    return pipeline


def _owned_run(request: Request, run_id: str) -> Dict[str, Any]:
    run = _call(service.load_run, run_id)
    ensure_owner_access(request, run.get("owner_id"), detail="run owner mismatch")
    return run


@router.get("/status", summary="Whether node pipelines are enabled here")
async def pipelines_status() -> Dict[str, Any]:
    return {"enabled": service.enabled()}


@router.get("/node-types", summary="Node types, data types and categories for the editor")
async def node_types() -> Dict[str, Any]:
    _enabled()
    return service.catalog()


@router.post("/validate", summary="Validate a graph without saving it")
async def validate(payload: Dict[str, Any] = Body(...), for_run: bool = Query(default=False)) -> Dict[str, Any]:
    _enabled()
    result = validate_graph(payload.get("graph"), for_run=for_run).as_dict()
    return result


# The collection lives at /pipelines/items: the gateway serves the bare /pipelines path to the editor page.
@router.get("/items", summary="Your pipelines")
async def list_pipelines(request: Request) -> Dict[str, Any]:
    _enabled()
    return {"pipelines": service.list_for(get_request_owner_id(request))}


@router.post("/items", summary="Create a pipeline")
async def create_pipeline(request: Request, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    _enabled()
    return _call(
        service.create,
        get_request_owner_id(request),
        payload.get("name"),
        payload.get("description"),
        payload.get("graph"),
        payload.get("template"),
    )


@router.get("/runs/{run_id}", summary="A run with every node's status and outputs")
async def get_run(request: Request, run_id: str) -> Dict[str, Any]:
    _enabled()
    _owned_run(request, run_id)
    return _call(service.run_detail, run_id)


@router.post("/runs/{run_id}/cancel", summary="Cancel a run in progress")
async def cancel_run(request: Request, run_id: str) -> Dict[str, Any]:
    _enabled()
    _owned_run(request, run_id)
    return _call(service.cancel_run, run_id)


@router.get("/runs/{run_id}/nodes/{node_id}/outputs/{port}/preview", summary="Preview a node output")
async def preview_output(
    request: Request, run_id: str, node_id: str, port: str, path: Optional[str] = Query(default=None)
) -> Dict[str, Any]:
    _enabled()
    _owned_run(request, run_id)
    return _call(service.preview, run_id, node_id, port, path)


@router.get("/runs/{run_id}/nodes/{node_id}/outputs/{port}/download", summary="Download a node output file")
async def download_output(
    request: Request, run_id: str, node_id: str, port: str, path: Optional[str] = Query(default=None)
):
    _enabled()
    _owned_run(request, run_id)
    target = _call(service.output_file, run_id, node_id, port, path)
    return FileResponse(str(target), filename=target.name)


@router.get("/{pipeline_id}", summary="A pipeline with its draft, versions and draft validation")
async def get_pipeline(request: Request, pipeline_id: str) -> Dict[str, Any]:
    _enabled()
    _owned_pipeline(request, pipeline_id)
    return _call(service.detail, pipeline_id)


@router.put("/{pipeline_id}", summary="Save the draft, name or description")
async def update_pipeline(request: Request, pipeline_id: str, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    _enabled()
    _owned_pipeline(request, pipeline_id)
    return _call(
        service.update,
        pipeline_id,
        name=payload.get("name"),
        description=payload.get("description"),
        graph=payload.get("graph"),
    )


@router.delete("/{pipeline_id}", summary="Delete a pipeline")
async def delete_pipeline(request: Request, pipeline_id: str) -> Dict[str, Any]:
    _enabled()
    _owned_pipeline(request, pipeline_id)
    _call(service.delete, pipeline_id)
    return {"deleted": pipeline_id}


@router.post("/{pipeline_id}/versions", summary="Freeze the draft as a version")
async def freeze_pipeline(request: Request, pipeline_id: str) -> Dict[str, Any]:
    _enabled()
    _owned_pipeline(request, pipeline_id)
    return _call(service.freeze, pipeline_id)


@router.get("/{pipeline_id}/files", summary="Files uploaded to this pipeline")
async def list_files(request: Request, pipeline_id: str) -> Dict[str, Any]:
    _enabled()
    _owned_pipeline(request, pipeline_id)
    return {"files": _call(service.list_files, pipeline_id)}


@router.post("/{pipeline_id}/files", summary="Upload an input file")
async def upload_file(request: Request, pipeline_id: str, file: UploadFile = File(...)) -> Dict[str, Any]:
    _enabled()
    _owned_pipeline(request, pipeline_id)
    try:
        return await service.save_upload(pipeline_id, file)
    except service.PipelineError as exc:
        raise HTTPException(status_code=exc.status, detail={"message": str(exc), **exc.extra}) from None


@router.get("/{pipeline_id}/runs", summary="Recent runs of a pipeline")
async def list_runs(request: Request, pipeline_id: str, limit: int = Query(default=20, ge=1, le=100)) -> Dict[str, Any]:
    _enabled()
    _owned_pipeline(request, pipeline_id)
    return {"runs": _call(service.list_runs, pipeline_id, limit)}


@router.post("/{pipeline_id}/runs", summary="Freeze the draft and run it")
async def start_run(request: Request, pipeline_id: str, payload: Dict[str, Any] = Body(default={})) -> Dict[str, Any]:
    _enabled()
    pipeline = _owned_pipeline(request, pipeline_id)
    inputs = payload.get("inputs") if isinstance(payload, dict) else None
    if inputs is not None and not isinstance(inputs, dict):
        raise HTTPException(status_code=422, detail={"message": "inputs must map input node ids to files"})
    return _call(service.start_run, pipeline_id, pipeline["owner_id"], inputs)
