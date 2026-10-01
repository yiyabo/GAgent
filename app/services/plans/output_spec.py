"""Versioned output declarations shared by Plan and DeepThink.

The envelope retains legacy criteria and artifact contracts, so those continue
to use their existing check/manifest authorities. ``required_outputs`` carries
precise file declarations; its validation never guesses semantic correctness.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .task_metadata_generator import INFERRED_TEXT_SOURCE

OUTPUT_SPEC_VERSION = 1
KIND_EXTENSIONS = {
    "image": (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"),
    "data": (
        ".csv",
        ".tsv",
        ".xlsx",
        ".xls",
        ".json",
        ".jsonl",
        ".parquet",
        ".npz",
        ".npy",
        ".h5",
        ".h5ad",
    ),
    "document": (
        ".md",
        ".markdown",
        ".pdf",
        ".docx",
        ".html",
        ".htm",
        ".txt",
        ".tex",
        ".bib",
    ),
}
ALLOWED_EXTENSIONS = {ext for exts in KIND_EXTENSIONS.values() for ext in exts} | {
    ".yaml",
    ".yml",
    ".toml",
    ".xml",
    ".py",
    ".r",
    ".js",
    ".ts",
    ".zip",
    ".gz",
    ".fasta",
    ".fa",
    ".fastq",
    ".fq",
    ".bed",
    ".bam",
    ".vcf",
    ".log",
}
KIND_ALIASES = {
    **{
        key: "image"
        for key in ("image", "figure", "plot", "chart", "picture", "图", "图片", "图表")
    },
    **{
        key: "data"
        for key in ("data", "table", "spreadsheet", "csv", "dataset", "表格", "数据表")
    },
    **{
        key: "document"
        for key in ("document", "doc", "report", "markdown", "md", "报告", "文档")
    },
}


@dataclass
class RequiredOutput:
    kind: str
    min_count: int = 1
    extensions: List[str] = field(default_factory=list)
    constraints: str = ""
    target_path: Optional[str] = None
    in_place: bool = False


@dataclass
class OutputSpec:
    required_outputs: List[RequiredOutput] = field(default_factory=list)
    source: str = "v2_llm"
    fallback_used: bool = False
    raw_error: Optional[str] = None
    schema_version: int = OUTPUT_SPEC_VERSION
    blocking: bool = True
    acceptance_criteria: Optional[Dict[str, Any]] = None
    artifact_contract: Optional[Dict[str, Any]] = None

    @property
    def authoritative(self) -> bool:
        return self.blocking and self.source != INFERRED_TEXT_SOURCE

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class InvalidOutputSpec(ValueError):
    """An authoritative declaration cannot be interpreted without weakening it."""


def parse_output_spec(raw: Any, *, strict: bool = False) -> Optional[OutputSpec]:
    def invalid(message: str) -> None:
        if strict:
            raise InvalidOutputSpec("invalid_output_spec: " + message)
        return None

    if isinstance(raw, OutputSpec):
        raw = raw.to_dict()
    if isinstance(raw, str):
        text = raw.strip()
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return invalid("expected a JSON object")
        try:
            raw = json.loads(text[start : end + 1])
        except (ValueError, TypeError):
            return invalid("invalid JSON")
    if not isinstance(raw, dict):
        return invalid("expected an object")
    version = raw.get("schema_version", OUTPUT_SPEC_VERSION)
    if type(version) is not int or version != OUTPUT_SPEC_VERSION:
        return invalid("unsupported schema version")
    items = raw.get("required_outputs", [])
    if not isinstance(items, list) or len(items) > 64:
        return invalid("required_outputs must contain at most 64 declarations")
    outputs = []
    for item in items:
        if not isinstance(item, dict):
            return invalid("each required output must be an object")
        count = item.get("min_count", 1)
        if type(count) is not int or not 1 <= count <= 10000:
            return invalid("min_count must be an integer from 1 to 10000")
        raw_extensions = item.get("extensions", [])
        if not isinstance(raw_extensions, list) or len(raw_extensions) > 32:
            return invalid("extensions must contain at most 32 declarations")
        extensions = []
        for value in raw_extensions:
            if not isinstance(value, str):
                return invalid("extensions must be strings")
            ext = "." + value.strip().lower().lstrip(".")
            if (
                not re.fullmatch(r"\.[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*", ext)
                or len(ext) > 64
            ):
                return invalid("unsupported extension syntax")
            if ext not in extensions:
                extensions.append(ext)
        path = item.get("target_path")
        if path is not None and not isinstance(path, str):
            return invalid("target_path must be a string")
        if path and (len(path) > 4096 or ".." in Path(path).parts or "\\" in path):
            return invalid("unsupported target_path; exact paths are never truncated")
        constraints = item.get("constraints", "")
        if not isinstance(constraints, str) or len(constraints) > 8000:
            return invalid("constraints must be text of at most 8000 characters")
        in_place = item.get("in_place", False)
        if not isinstance(in_place, bool):
            return invalid("in_place must be a boolean")
        if in_place and not path:
            return invalid("in_place requires an exact target_path")
        outputs.append(
            RequiredOutput(
                kind=KIND_ALIASES.get(
                    str(item.get("kind", "other")).strip().lower(), "other"
                ),
                min_count=count,
                extensions=extensions,
                constraints=constraints,
                target_path=path or None,
                in_place=in_place,
            )
        )
    criteria, contract = raw.get("acceptance_criteria"), raw.get("artifact_contract")
    for name, value in (
        ("acceptance_criteria", criteria),
        ("artifact_contract", contract),
    ):
        if value is not None and not isinstance(value, dict):
            return invalid(name + " must be an object")
    if "required_outputs" not in raw and criteria is None and contract is None:
        return invalid("no output declaration or legacy contract")
    blocking = raw.get("blocking", True)
    if not isinstance(blocking, bool):
        return invalid("blocking must be a boolean")
    return OutputSpec(
        required_outputs=outputs,
        source=str(raw.get("source") or "v2_llm"),
        blocking=blocking,
        fallback_used=bool(raw.get("fallback_used")),
        raw_error=raw.get("raw_error"),
        acceptance_criteria=copy.deepcopy(criteria),
        artifact_contract=copy.deepcopy(contract),
    )


def output_spec_from_metadata(metadata: Any) -> Optional[OutputSpec]:
    """Adapt legacy fields into one envelope, including legacy API updates.

    Unmarked legacy structured criteria/contracts keep their explicit meaning.
    Their path checks retain established basename/alias resolution rather than
    being silently reinterpreted as a new exact ``target_path`` declaration.
    """
    if not isinstance(metadata, dict):
        return None
    spec = None
    for key in ("output_spec", "v2_spec", "acceptance_spec"):
        if metadata.get(key) is None:
            continue
        spec = parse_output_spec(metadata[key], strict=True)
        if spec is not None:
            break
    if spec is None:
        spec = parse_output_spec(
            {
                "source": INFERRED_TEXT_SOURCE,
                "acceptance_criteria": metadata.get("acceptance_criteria"),
                "artifact_contract": metadata.get("artifact_contract"),
            }
        )
    if spec is not None:
        if isinstance(metadata.get("acceptance_criteria"), dict):
            spec.acceptance_criteria = copy.deepcopy(metadata["acceptance_criteria"])
        if isinstance(metadata.get("artifact_contract"), dict):
            spec.artifact_contract = copy.deepcopy(metadata["artifact_contract"])
    return spec


def spec_metadata_view(
    metadata: Dict[str, Any], spec: Optional[OutputSpec]
) -> Dict[str, Any]:
    """Legacy consumers read their original rules from the canonical envelope."""
    view = dict(metadata)
    if spec:
        for key in ("acceptance_criteria", "artifact_contract"):
            value = getattr(spec, key)
            if value is not None:
                view[key] = copy.deepcopy(value)
    return view


def _check_cancelled() -> None:
    import asyncio
    from app.services.cancellation import current_cancel_token
    from app.services.run_budget import (
        DEADLINE_REASON,
        RunDeadlineExceeded,
        current_run_budget,
    )

    budget = current_run_budget()
    token = budget.cancel_token if budget else current_cancel_token()
    if token and token.cancelled:
        if token.reason == DEADLINE_REASON:
            raise RunDeadlineExceeded("Deadline reached while checking output evidence")
        raise asyncio.CancelledError(token.reason or "Run cancelled")


def file_snapshot(path: Path) -> Dict[str, Any]:
    try:
        _check_cancelled()
        if not path.is_file():
            return {"exists": False}
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                _check_cancelled()
                digest.update(chunk)
        _check_cancelled()
        return {
            "exists": True,
            "sha256": digest.hexdigest(),
            "size": path.stat().st_size,
        }
    except OSError:
        return {"exists": False}


def capture_output_inputs(
    spec: Optional[OutputSpec], base_dir: str | Path
) -> Dict[str, Any]:
    root = Path(base_dir).resolve()
    return {
        str(
            (
                Path(output.target_path)
                if Path(output.target_path).is_absolute()
                else root / output.target_path
            ).resolve()
        ): file_snapshot(
            (
                Path(output.target_path)
                if Path(output.target_path).is_absolute()
                else root / output.target_path
            ).resolve()
        )
        for output in (spec.required_outputs if spec else [])
        if output.in_place and output.target_path
    }


def seed_task_output_snapshot(
    node: Any, base_dir: str | Path, *, preserve_existing: bool = False
) -> Optional[OutputSpec]:
    """Called before task side effects; caller persists the updated metadata."""
    metadata = dict(getattr(node, "metadata", None) or {})
    if (
        preserve_existing
        and metadata.get("output_spec") is not None
        and isinstance(metadata.get("output_input_snapshot"), dict)
        and metadata.get("output_spec_base_dir")
    ):
        return parse_output_spec(metadata["output_spec"], strict=True)
    spec = output_spec_from_metadata(metadata)
    if spec is not None:
        metadata["output_spec"] = spec.to_dict()
        metadata["output_spec_base_dir"] = str(Path(base_dir).resolve())
        metadata["output_input_snapshot"] = capture_output_inputs(spec, base_dir)
        node.metadata = metadata
    return spec


def is_readonly_output_probe(tool_name: str, parameters: Any) -> bool:
    """Use the existing tool-role metadata and the mixed file operation mode."""
    tool_name = str(tool_name or "").strip().lower()
    if tool_name == "file_operations":
        parameters = parameters if isinstance(parameters, dict) else {}
        return str(parameters.get("operation") or "").lower() in {
            "read",
            "list",
            "profile",
            "census",
            "exists",
            "info",
        }
    from tool_box.tools import get_tool_registry

    definition = get_tool_registry().get_tool(tool_name)
    if definition is not None:
        return bool(definition.is_read_only)
    from tool_box.tool_registry import get_tool_orchestration_metadata

    return bool(get_tool_orchestration_metadata(tool_name).get("is_read_only"))


def accepted_output_paths(
    manifest: Any,
    task_id: Optional[int],
    spec: Optional[OutputSpec],
    base_dir: str | Path,
    current_paths: Iterable[str] = (),
) -> List[str]:
    """Reuse accepted task outputs through the canonical manifest, once per origin."""
    if not isinstance(manifest, dict) or not task_id:
        return []
    from .artifact_contracts import resolve_manifest_aliases

    entries = manifest.get("artifacts")
    entries = entries if isinstance(entries, dict) else {}
    resolved = resolve_manifest_aliases(manifest, entries.keys())
    root = Path(base_dir).resolve()
    current = {
        (Path(path) if Path(path).is_absolute() else root / path).resolve()
        for path in current_paths
    }
    results, seen = [], set()
    for alias, entry in entries.items():
        if (
            not isinstance(entry, dict)
            or str(entry.get("producer_task_id")) != str(task_id)
            or alias not in resolved
        ):
            continue
        canonical = Path(resolved[alias]).resolve()
        source = Path(entry.get("source_path") or str(canonical)).resolve()
        if source in seen or source in current:
            continue
        seen.add(source)
        preferred = canonical
        exact_source_target = any(
            output.target_path
            and source
            == (
                Path(output.target_path)
                if Path(output.target_path).is_absolute()
                else root / output.target_path
            ).resolve()
            for output in (spec.required_outputs if spec else [])
        )
        if exact_source_target and source.is_file():
            if file_snapshot(source).get("sha256") == file_snapshot(canonical).get(
                "sha256"
            ):
                preferred = source
        results.append(str(preferred))
    return results


def output_origin_map(
    artifact_manifest: Any = None,
    deliverable_manifest: Any = None,
    *,
    project_root: Optional[str | Path] = None,
    deliverables_root: Optional[str | Path] = None,
) -> Dict[str, str]:
    """Resolve confirmed copy provenance without guessing a relative-path base.

    Plan publish entries describe copies and store absolute paths. Deliverable
    rows also describe derived outputs, so only same-name/same-format rows are
    copy evidence; their two relative fields require their own explicit roots.
    """
    proposals: Dict[str, set[str]] = {}

    def resolve(
        value: Any, relative_root: Optional[str | Path] = None
    ) -> Optional[Path]:
        if not isinstance(value, str) or not value:
            return None
        path = Path(value)
        if not path.is_absolute():
            if relative_root is None or ".." in path.parts:
                return None
            root = Path(relative_root).resolve()
            path = (root / path).resolve()
            if not path.is_relative_to(root):
                return None
        return path.resolve()

    def record(
        source: Optional[Path], mirror: Optional[Path], *, same_name: bool = False
    ) -> None:
        if source is None or mirror is None or source == mirror:
            return

        def suffix(path: Path) -> str:
            return ".jpg" if path.suffix.lower() == ".jpeg" else path.suffix.lower()

        if suffix(source) != suffix(mirror) or (
            same_name and source.name != mirror.name
        ):
            return
        proposals.setdefault(str(mirror), set()).add(str(source))

    entries = (
        artifact_manifest.get("artifacts")
        if isinstance(artifact_manifest, dict)
        else None
    )
    if isinstance(entries, dict):
        for entry in entries.values():
            if isinstance(entry, dict):
                record(resolve(entry.get("source_path")), resolve(entry.get("path")))
    rows = (
        deliverable_manifest.get("items")
        if isinstance(deliverable_manifest, dict)
        else None
    )
    if isinstance(rows, list):
        for entry in rows:
            if isinstance(entry, dict):
                record(
                    resolve(entry.get("source_path"), project_root),
                    resolve(entry.get("path"), deliverables_root),
                    same_name=True,
                )
    # Contradictory aliases are not proof that unrelated sources are one asset.
    return {
        mirror: next(iter(sources))
        for mirror, sources in proposals.items()
        if len(sources) == 1
    }


def session_output_origin_map(
    session_id: Optional[str], artifact_manifest: Any = None
) -> Dict[str, str]:
    """Read the existing publisher's actual session/project-root convention."""
    origins = output_origin_map(artifact_manifest)
    if not session_id:
        return origins
    from app.services.deliverables.publisher import get_deliverable_publisher

    publisher = get_deliverable_publisher()
    session_dir = publisher.get_session_dir(str(session_id), create=False)
    try:
        document = json.loads(
            (session_dir / "deliverables" / "manifest_latest.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, ValueError):
        return origins
    return output_origin_map(
        artifact_manifest,
        document,
        project_root=publisher._project_root,
        deliverables_root=session_dir / "deliverables" / "latest",
    )


def _format_error(path: Path) -> Optional[str]:
    """Basic readability only; no quality, row-count or semantic assertions."""
    suffix = path.suffix.lower()
    _check_cancelled()
    try:
        if suffix == ".json":
            with path.open(encoding="utf-8-sig") as handle:
                json.load(handle)
        elif suffix == ".pdf":
            from pypdf import PdfReader

            if not PdfReader(str(path)).pages:
                return "PDF has no readable pages"
        elif suffix == ".png":
            from PIL import Image

            with Image.open(path) as image:
                if image.format != "PNG":
                    return "Declared PNG has a different image format"
                image.verify()
        elif suffix in {".xlsx", ".docx"}:
            from zipfile import ZipFile
            from xml.etree.ElementTree import fromstring

            document = "xl/workbook.xml" if suffix == ".xlsx" else "word/document.xml"
            with ZipFile(path) as package:
                fromstring(package.read("[Content_Types].xml"))
                fromstring(package.read(document))
    except Exception as exc:
        return f"Declared {suffix} file is not readable: {type(exc).__name__}"
    finally:
        _check_cancelled()
    return None


def validate_output_spec(
    spec: OutputSpec,
    artifact_paths: Iterable[str],
    *,
    base_dir: str | Path,
    input_snapshot: Optional[Dict[str, Any]] = None,
    artifact_manifest: Any = None,
    origin_map: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Verify real files, exact declared paths, unique counts and overwrite evidence."""
    root = Path(base_dir).resolve()
    # A supplied map may already combine both manifests and reject conflicting
    # sources; rebuilding one side would reintroduce those ambiguous edges.
    links = (
        dict(origin_map)
        if origin_map is not None
        else output_origin_map(artifact_manifest)
    )
    parents: Dict[Path, Path] = {}

    def find(path: Path) -> Path:
        trail = []
        while path in parents:
            trail.append(path)
            path = parents[path]
        for child in trail:
            parents[child] = path
        return path

    for mirror, source in links.items():
        mirror_path, source_path = Path(mirror), Path(source)
        if not mirror_path.is_absolute() or not source_path.is_absolute():
            continue
        left, right = find(mirror_path.resolve()), find(source_path.resolve())
        if left != right:
            parents[left] = right
    files: List[Path] = []
    for raw in artifact_paths:
        path = Path(raw).expanduser()
        path = (path if path.is_absolute() else root / path).resolve()
        try:
            if path.is_file() and path.stat().st_size > 0 and path not in files:
                files.append(path)
        except OSError:
            continue
    candidates: List[List[int]] = []
    origin_ids: Dict[Path, int] = {}
    file_origins = []
    for path in files:
        origin = find(path)
        file_origins.append(origin_ids.setdefault(origin, len(origin_ids)))
    failures = []
    format_errors: Dict[Path, Optional[str]] = {}
    for index, output in enumerate(spec.required_outputs):
        _check_cancelled()
        extensions = output.extensions or list(KIND_EXTENSIONS.get(output.kind, ()))
        target = Path(output.target_path) if output.target_path else None
        if target is not None:
            target = (target if target.is_absolute() else root / target).resolve()
        directory_target = bool(
            target
            and (
                str(output.target_path).endswith("/")
                or (not target.suffix and target.is_dir())
            )
        )
        matching = [
            i
            for i, path in enumerate(files)
            if (
                not extensions
                or any(path.name.lower().endswith(ext) for ext in extensions)
            )
            and (
                target is None
                or path == target
                or (directory_target and path.is_relative_to(target))
            )
        ]
        if spec.source != INFERRED_TEXT_SOURCE and (
            output.extensions or output.target_path
        ):
            readable = []
            rejected = []
            for candidate in matching:
                path = files[candidate]
                if path not in format_errors:
                    format_errors[path] = _format_error(path)
                if format_errors[path]:
                    rejected.append(path)
                else:
                    readable.append(candidate)
            matching = readable
            if rejected and len(matching) < output.min_count:
                failures.append(
                    {
                        "type": "output_spec",
                        "output_index": index,
                        "success": False,
                        "path": str(rejected[0]),
                        "failure_kind": "invalid_format",
                        "message": format_errors[rejected[0]],
                    }
                )
        if output.in_place:
            before = (
                (input_snapshot or {}).get(str(target)) if target is not None else None
            )
            reason = None
            if target is None:
                reason = "in_place requires target_path"
            elif (
                not isinstance(before, dict)
                or not before.get("exists")
                or not before.get("sha256")
            ):
                reason = "No pre-execution snapshot proves the existing target"
            elif file_snapshot(target).get("sha256") == before["sha256"]:
                reason = "Target content is unchanged from the pre-execution snapshot"
            if reason:
                matching = []
                failures.append(
                    {
                        "type": "output_spec",
                        "output_index": index,
                        "success": False,
                        "path": str(target) if target else None,
                        "message": reason,
                    }
                )
        candidates.append(
            list(dict.fromkeys(file_origins[index] for index in matching))
        )
    # Capacity matching across at most 64 requirement groups. Reassigning a
    # file follows a BFS of groups, never recursion through thousands of slots.
    from collections import deque

    owners: Dict[int, int] = {}
    matched = [0] * len(spec.required_outputs)
    free_cursor = [0] * len(spec.required_outputs)

    def free_candidate(group: int) -> Optional[int]:
        while free_cursor[group] < len(candidates[group]):
            candidate = candidates[group][free_cursor[group]]
            free_cursor[group] += 1
            if candidate not in owners:
                return candidate
        return None

    def augment(root_group: int) -> bool:
        queue = deque([root_group])
        previous: Dict[int, tuple[int, int]] = {}
        visited = {root_group}
        while queue:
            _check_cancelled()
            group = queue.popleft()
            free = free_candidate(group)
            if free is not None:
                owners[free] = group
                while group != root_group:
                    parent, bridge = previous[group]
                    owners[bridge] = parent
                    group = parent
                matched[root_group] += 1
                return True
            for candidate in candidates[group]:
                owner = owners.get(candidate)
                if owner is not None and owner not in visited:
                    visited.add(owner)
                    previous[owner] = (group, candidate)
                    queue.append(owner)
        return False

    for group in sorted(
        range(len(candidates)), key=lambda index: len(candidates[index])
    ):
        while matched[group] < spec.required_outputs[group].min_count:
            _check_cancelled()
            if not augment(group):
                break
    for index, output in enumerate(spec.required_outputs):
        if matched[index] < output.min_count and not any(
            f["output_index"] == index for f in failures
        ):
            failures.append(
                {
                    "type": "output_spec",
                    "output_index": index,
                    "success": False,
                    "path": output.target_path,
                    "required_count": output.min_count,
                    "actual_count": matched[index],
                    "extensions": output.extensions,
                    "message": "Declared output format, path or count is not satisfied",
                }
            )
    _check_cancelled()
    return {
        "status": "failed" if failures else "passed",
        "authoritative": spec.authoritative,
        "failures": failures,
        "matched_counts": matched,
        "artifact_paths": [str(path) for path in files],
        "unchecked_constraints": [
            output.constraints for output in spec.required_outputs if output.constraints
        ],
    }
