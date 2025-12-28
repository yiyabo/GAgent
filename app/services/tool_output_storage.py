"""Persist full tool outputs and metadata under the session directory."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from app.services.upload_storage import ensure_session_dir


MAX_OUTPUT_BYTES = 200 * 1024 * 1024
MAX_SCHEMA_KEYS = 80
MAX_SCHEMA_ITEMS = 50
MAX_PREVIEW_ITEMS = 5
MAX_PREVIEW_STRING = 2000
MAX_ARTIFACTS = 200
MAX_SCAN_DEPTH = 5
MAX_SCAN_ITEMS = 200


@dataclass(frozen=True)
class StoredToolOutput:
    output_dir: str
    result_path: str
    manifest_path: str
    preview_path: Optional[str]
    size_bytes: int
    too_large_for_llm: bool


def store_tool_output(
    *,
    session_id: Optional[str],
    job_id: Optional[str],
    action: Dict[str, Any],
    tool_name: str,
    raw_result: Any,
    summary: Optional[str],
) -> Optional[StoredToolOutput]:
    if not session_id:
        return None

    session_root = ensure_session_dir(session_id)
    outputs_root = session_root / "tool_outputs"
    job_label = f"job_{job_id}" if job_id else "job_unknown"
    step_label = _build_step_label(action, tool_name)
    output_dir = outputs_root / job_label / step_label
    output_dir.mkdir(parents=True, exist_ok=True)

    cleaned_result = _drop_callables(raw_result)
    result_path = output_dir / "result.json"
    _write_json(result_path, cleaned_result, compact=True)

    size_bytes = result_path.stat().st_size
    too_large = size_bytes > MAX_OUTPUT_BYTES
    result_hash = _hash_file(result_path)

    schema = _infer_schema(cleaned_result)
    artifacts = _collect_artifacts(cleaned_result)
    preview = _build_preview(cleaned_result)
    preview_path = None
    if preview is not None:
        preview_path = output_dir / "preview.json"
        _write_json(preview_path, preview, compact=False)

    manifest_path = output_dir / "manifest.json"
    manifest = {
        "session_id": session_id,
        "job_id": job_id,
        "tool": tool_name,
        "action": _clean_action(action),
        "summary": summary,
        "stored_at": _utc_now(),
        "result": {
            "path": _rel_path(result_path, session_root),
            "size_bytes": size_bytes,
            "sha256": result_hash,
            "too_large_for_llm": too_large,
            "max_llm_bytes": MAX_OUTPUT_BYTES,
        },
        "data_schema": schema,
        "artifacts": artifacts,
        "preview_path": _rel_path(preview_path, session_root) if preview_path else None,
    }
    _write_json(manifest_path, manifest, compact=False)

    return StoredToolOutput(
        output_dir=_rel_path(output_dir, session_root),
        result_path=_rel_path(result_path, session_root),
        manifest_path=_rel_path(manifest_path, session_root),
        preview_path=_rel_path(preview_path, session_root) if preview_path else None,
        size_bytes=size_bytes,
        too_large_for_llm=too_large,
    )


def _build_step_label(action: Dict[str, Any], tool_name: str) -> str:
    order = action.get("order")
    order_label = f"{order}" if isinstance(order, int) else "x"
    unique = uuid4().hex[:6]
    return f"step_{order_label}_{tool_name}_{unique}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rel_path(path: Optional[Path], root: Path) -> Optional[str]:
    if path is None:
        return None
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _write_json(path: Path, payload: Any, *, compact: bool) -> None:
    if compact:
        with path.open("w", encoding="utf-8") as handle:
            json.dump(
                payload,
                handle,
                ensure_ascii=False,
                separators=(",", ":"),
                default=_json_default,
            )
    else:
        with path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, default=_json_default)


def _hash_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _json_default(value: Any) -> str:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", errors="ignore")
        except Exception:
            return repr(value)
    return str(value)


def _drop_callables(value: Any) -> Any:
    if callable(value):
        return None
    if isinstance(value, dict):
        cleaned: Dict[str, Any] = {}
        for key, item in value.items():
            if callable(item):
                continue
            cleaned[key] = _drop_callables(item)
        return cleaned
    if isinstance(value, list):
        return [_drop_callables(item) for item in value if not callable(item)]
    if isinstance(value, tuple):
        return [_drop_callables(item) for item in value if not callable(item)]
    return value


def _clean_action(action: Dict[str, Any]) -> Dict[str, Any]:
    cleaned = dict(action)
    if "parameters" in cleaned:
        cleaned["parameters"] = _drop_callables(cleaned["parameters"])
    return cleaned


def _infer_schema(value: Any, depth: int = MAX_SCAN_DEPTH) -> Dict[str, Any]:
    if depth <= 0:
        return {"type": "unknown"}
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string", "length": len(value)}
    if isinstance(value, dict):
        keys = list(value.keys())
        field_schema: Dict[str, Any] = {}
        for key in keys[:MAX_SCHEMA_KEYS]:
            field_schema[key] = _infer_schema(value[key], depth=depth - 1)
        return {
            "type": "object",
            "key_count": len(keys),
            "keys": keys[:MAX_SCHEMA_KEYS],
            "field_schema": field_schema,
        }
    if isinstance(value, list):
        sample = value[:MAX_SCHEMA_ITEMS]
        item_types = sorted({type(item).__name__ for item in sample})
        schema: Dict[str, Any] = {
            "type": "array",
            "length": len(value),
            "item_types": item_types,
        }
        if sample and all(isinstance(item, dict) for item in sample):
            keys: List[str] = []
            for item in sample:
                for key in item.keys():
                    if key not in keys:
                        keys.append(key)
                        if len(keys) >= MAX_SCHEMA_KEYS:
                            break
                if len(keys) >= MAX_SCHEMA_KEYS:
                    break
            field_schema: Dict[str, Any] = {}
            for key in keys:
                for item in sample:
                    if key in item:
                        field_schema[key] = _infer_schema(
                            item.get(key), depth=depth - 1
                        )
                        break
            schema["item_schema"] = {
                "type": "object",
                "key_count": len(keys),
                "keys": keys,
                "field_schema": field_schema,
            }
        return schema
    return {"type": type(value).__name__}


def _build_preview(value: Any, depth: int = MAX_SCAN_DEPTH) -> Optional[Any]:
    if depth <= 0:
        return None
    if value is None:
        return None
    if isinstance(value, str):
        if len(value) > MAX_PREVIEW_STRING:
            return value[: MAX_PREVIEW_STRING - 3] + "..."
        return value
    if isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, dict):
        preview: Dict[str, Any] = {}
        for key in list(value.keys())[:MAX_PREVIEW_ITEMS]:
            preview[key] = _build_preview(value[key], depth=depth - 1)
        return preview
    if isinstance(value, list):
        return [_build_preview(item, depth=depth - 1) for item in value[:MAX_PREVIEW_ITEMS]]
    return str(value)


def _collect_artifacts(value: Any) -> List[str]:
    artifacts: List[str] = []
    _scan_for_artifacts(value, artifacts, depth=0)
    return artifacts[:MAX_ARTIFACTS]


def _scan_for_artifacts(value: Any, artifacts: List[str], *, depth: int) -> None:
    if depth >= MAX_SCAN_DEPTH or len(artifacts) >= MAX_ARTIFACTS:
        return
    if isinstance(value, str):
        if _looks_like_path_value(value) and value not in artifacts:
            artifacts.append(value)
        return
    if isinstance(value, dict):
        for key, item in list(value.items())[:MAX_SCAN_ITEMS]:
            key_lower = str(key).lower()
            if isinstance(item, str) and _looks_like_path_key(key_lower):
                if item not in artifacts:
                    artifacts.append(item)
                continue
            _scan_for_artifacts(item, artifacts, depth=depth + 1)
        return
    if isinstance(value, list):
        for item in value[:MAX_SCAN_ITEMS]:
            _scan_for_artifacts(item, artifacts, depth=depth + 1)
        return


def _looks_like_path_key(key: str) -> bool:
    if "path" in key:
        return True
    if key.endswith("file") or key.endswith("files"):
        return True
    return False


def _looks_like_path_value(value: str) -> bool:
    if value.startswith(("http://", "https://")):
        return False
    if value.startswith(("s3://", "gs://")):
        return False
    if value.startswith(("/", "./", "../")):
        return True
    if "/" in value or "\\" in value:
        return True
    extensions = (
        ".csv",
        ".tsv",
        ".json",
        ".txt",
        ".md",
        ".png",
        ".jpg",
        ".jpeg",
        ".pdf",
        ".zip",
        ".fasta",
        ".fa",
        ".fastq",
        ".gz",
    )
    return value.lower().endswith(extensions)
