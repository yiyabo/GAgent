"""Persist gateway receipts and returned files in the active task workspace."""
import hashlib
import json
from pathlib import Path
import uuid

from .phagescope_artifacts import _attach_local_bundle_artifact_fields
from .phagescope_gateway_client import GatewayError


def workspace(context):
    from app.services.session_paths import get_runtime_session_dir
    from app.services.tool_output_resolver import get_tool_output_resolver

    if context and context.work_dir:
        root = Path(context.work_dir).resolve()
    elif context and context.session_id:
        root = get_runtime_session_dir(context.session_id, create=True)
    else:
        root = get_tool_output_resolver().resolve(session_id=None, tool_name="phagescope", create=True)
    return root / "work" / "phagescope" / "gateway"


def input_file(name, context):
    path = Path(name).expanduser()
    if not path.is_absolute():
        if not context or not context.work_dir:
            raise GatewayError("invalid_parameter", "A relative file_path requires an active task workspace.")
        path = Path(context.work_dir) / path
    path = path.resolve()
    roots = []
    if context and context.work_dir:
        roots.append(Path(context.work_dir).resolve())
    if context and context.session_id:
        from app.services.session_paths import get_runtime_session_dir
        roots.append(get_runtime_session_dir(context.session_id).resolve())
    if roots and not any(path == root or root in path.parents for root in roots):
        raise GatewayError("invalid_parameter", "file_path must be inside the current session or task workspace.")
    if not path.is_file():
        raise GatewayError("input_not_found", "FASTA input file was not found.", 404)
    if path.stat().st_size > 20 * 1024**2:
        raise GatewayError("payload_too_large", "FASTA input exceeds the gateway's 20 MB limit.")
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeError:
        raise GatewayError("invalid_fasta", "FASTA input must be UTF-8 text.")


class Output:
    def __init__(self, context, kind):
        self.context = context
        self.directory = workspace(context) / kind / uuid.uuid4().hex
        self.files = {}
        self.truncated = []
        self.missing = []

    def write(self, name, content):
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or "\\" in name:
            raise GatewayError("invalid_gateway_response", "Gateway returned an invalid result filename.", 502)
        path = self.directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode("utf-8")
        temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
        temp.write_bytes(data)
        temp.replace(path)
        self.files[name] = str(path)
        return path

    def json(self, name, value):
        return self.write(name, json.dumps(value, ensure_ascii=False, indent=2) + "\n")

    def module(self, module, payload):
        if not isinstance(payload, dict) or not isinstance(payload.get("files", {}), dict):
            raise GatewayError("invalid_gateway_response", "Gateway module response must contain a files object.", 502)
        self.json(f"raw_api_responses/{module}.json", payload)
        for name, item in (payload.get("files") or {}).items():
            if not isinstance(name, str) or not isinstance(item, dict):
                raise GatewayError("invalid_gateway_response", "Gateway file entries must be objects.", 502)
            key = f"{module}/{name}"
            if not item.get("present"):
                self.missing.append(key)
                continue
            text = item.get("text")
            if not isinstance(text, str):
                raise GatewayError("invalid_gateway_response", "Gateway file text must be a string.", 502)
            if item.get("truncated"):
                self.truncated.append(key)
                name += ".preview"
            self.write(f"modules/{module}/{name}", text)

    def attach(self, result):
        context = self.context
        result["artifact_scope"] = "gateway_returned_files"
        result = _attach_local_bundle_artifact_fields(
            result, output_dir=self.directory, saved_files=self.files,
            session_id=context.session_id if context else None,
            task_id=context.task_id if context else None,
        )
        result["produced_files"] = result.get("session_artifact_paths") or result.get("artifact_paths") or []
        return result

    def manifest(self, value):
        records = []
        for name, raw in self.files.items():
            path = Path(raw)
            records.append({"path": name, "bytes": path.stat().st_size,
                            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        value = {**value, "files": records, "truncated_files": self.truncated,
                 "missing_files": self.missing, "content_review": "not_requested"}
        self.json("manifest.json", value)
        return value
