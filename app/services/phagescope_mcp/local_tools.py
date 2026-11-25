"""Local-analysis tools: Docker images and run plans for the user's own machine.

The catalog is ``tool_box/bio_tools/tools_config.json`` (the images PhageAgent
runs itself) merged with ``local_tools.json`` (local-only packages, overrides,
database setup and ready-made workflows). Nothing runs on the server: the
client agent runs the returned commands on its own machine.
"""

from __future__ import annotations

import json
import re
import shlex
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from string import Formatter
from typing import Any, Awaitable, Callable, Optional

from .tools import ToolInputError

_ROOT = Path(__file__).resolve().parents[3]
BIO_TOOLS_CONFIG = _ROOT / "tool_box" / "bio_tools" / "tools_config.json"
LOCAL_CONFIG = Path(__file__).with_name("local_tools.json")
SOURCE = "PhageAgent local tool catalog"
DB_ROOT_VAR = "PHAGEAGENT_DB"
DOCKER_INSTALL_URL = "https://docs.docker.com/get-started/get-docker/"
_SHELL_TOKENS = ("|", ">", "<", "&&", "||", ";", "$(")
_QUOTED_PLACEHOLDER = re.compile(r"""(['"])\{([a-zA-Z_][a-zA-Z0-9_]*)\}\1""")
_FASTX_SUFFIXES = (".fasta", ".fna", ".fa", ".fastq", ".fq", ".faa", ".ffn", ".gb", ".gbk")
_PREFLIGHT = ["docker version", "nproc", "free -g", "df -h .", "nvidia-smi  # only needed for GPU tools"]


@dataclass(frozen=True)
class LocalToolSpec:
    name: str
    title: str
    description: str
    input_schema: dict[str, Any]
    run: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


@lru_cache(maxsize=1)
def catalog() -> dict[str, Any]:
    """Merged catalog: ``{"tools": {name: entry}, "workflows": {name: workflow}}``."""
    base = json.loads(BIO_TOOLS_CONFIG.read_text(encoding="utf-8"))
    local = json.loads(LOCAL_CONFIG.read_text(encoding="utf-8"))
    tools: dict[str, Any] = {}
    for name, entry in base.items():
        if entry.get("native_only") or not entry.get("image"):
            continue
        tools[name] = {**deepcopy(entry), "source": "phageagent"}
    for name, entry in (local.get("packages") or {}).items():
        tools[name] = {**deepcopy(entry), "source": entry.get("source") or "phageagent_package"}
    for name, override in (local.get("overrides") or {}).items():
        if name in tools:
            tools[name].update(deepcopy(override))
    return {"tools": tools, "workflows": deepcopy(local.get("workflows") or {})}


def _tool(name: str) -> dict[str, Any]:
    entry = catalog()["tools"].get(str(name or "").strip())
    if entry is None:
        raise ToolInputError(f"Unknown local tool: {name}. Call list_local_tools for the catalog.")
    return entry


def _resources(entry: dict[str, Any]) -> dict[str, Any]:
    native = entry.get("native") or {}
    out: dict[str, Any] = {}
    if native.get("cpus"):
        out["cpus"] = native["cpus"]
    if native.get("mem_mb"):
        out["memory_gb"] = round(native["mem_mb"] / 1024)
    if entry.get("gpu"):
        out["gpu"] = True
    return out


def _summary(name: str, entry: dict[str, Any]) -> dict[str, Any]:
    row = {
        "tool": name,
        "description": entry.get("description"),
        "category": entry.get("category"),
        "image": entry["image"],
        "operations": {op: cfg.get("description") for op, cfg in (entry.get("operations") or {}).items()},
        "source": entry["source"],
    }
    if entry.get("database"):
        row["database"] = {k: v for k, v in entry["database"].items() if k in ("size", "note")} or True
    resources = _resources(entry)
    if resources:
        row["recommended"] = resources
    return row


def _detail(name: str, entry: dict[str, Any]) -> dict[str, Any]:
    row = _summary(name, entry)
    row["operations"] = {
        op: {
            "description": cfg.get("description"),
            "command": cfg.get("command"),
            "params": cfg.get("extra_params") or {},
            **({"notes": cfg["notes"]} if cfg.get("notes") else {}),
        }
        for op, cfg in (entry.get("operations") or {}).items()
    }
    if entry.get("notes"):
        row["notes"] = entry["notes"]
    if entry.get("database"):
        row["database"] = _database_setup(name, entry)
    return row


def _database_setup(name: str, entry: dict[str, Any]) -> dict[str, Any]:
    db = entry.get("database") or {}
    host_dir = f"${DB_ROOT_VAR}/{db.get('dir') or name}"
    info: dict[str, Any] = {"host_dir": host_dir}
    for key in ("size", "note"):
        if db.get(key):
            info[key] = db[key]
    top = f"${DB_ROOT_VAR}/{(db.get('dir') or name).split('/')[0]}"
    if db.get("setup"):
        info["setup"] = (
            f'mkdir -p "{top}" && docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp '
            f'-v "{top}":/db {entry["image"]} sh -c {shlex.quote(db["setup"])}'
        )
    elif db.get("host_setup"):
        info["setup"] = db["host_setup"]
    return info


def _stem(path: str) -> str:
    name = Path(str(path)).name
    if name.endswith(".gz"):
        name = name[:-3]
    for suffix in _FASTX_SUFFIXES:
        if name.lower().endswith(suffix):
            return name[: -len(suffix)]
    return Path(name).stem or name


def _fill(value: Any, variables: dict[str, str]) -> Any:
    if not isinstance(value, str):
        return value
    for key, sub in variables.items():
        value = value.replace("{" + key + "}", sub)
    return value


def _render_step(index: int, step: dict[str, Any], *, cpus: Optional[int]) -> tuple[dict[str, Any], list[str]]:
    name = str(step.get("tool") or "")
    entry = _tool(name)
    operation = str(step.get("operation") or "")
    op_cfg = (entry.get("operations") or {}).get(operation)
    if op_cfg is None:
        raise ToolInputError(
            f"Unknown operation {operation!r} for {name}; available: {', '.join(entry.get('operations') or {})}."
        )
    declared = op_cfg.get("extra_params") or {}
    given = {str(k): v for k, v in (step.get("params") or {}).items()}
    unknown = sorted(set(given) - set(declared) - {"input"})
    if unknown:
        raise ToolInputError(f"{name}.{operation} has no parameter(s): {', '.join(unknown)}.")
    template = _QUOTED_PLACEHOLDER.sub(r"{\2}", op_cfg["command"])
    fields = [f for _, f, _, _ in Formatter().parse(template) if f]
    values: dict[str, Any] = {"output_dir": "/work"}
    for key, spec in declared.items():
        if isinstance(spec, dict) and spec.get("default") is not None:
            values[key] = spec["default"]
    for key in ("threads", "cores"):
        if key in fields and key not in given and cpus:
            values[key] = cpus
    if "threads" in fields and "threads" not in values:
        values["threads"] = 4
    values.update(given)
    if step.get("input"):
        values["input"] = step["input"]
    if "query" in fields and "query" not in values and values.get("input"):
        values["query"] = values["input"]
    if "output" in fields and "output" not in values:
        values["output"] = f"{name}_{operation}_out"
    missing = [f for f in fields if values.get(f) in (None, "")]
    rendered = template.format(**{f: (f"<{f}>" if f in missing else shlex.quote(str(values[f]))) for f in fields})
    args = f"sh -c {shlex.quote(rendered)}" if any(t in template for t in _SHELL_TOKENS) else rendered
    parts = ["docker run --rm", '-u "$(id -u):$(id -g)"', "-e HOME=/work"]
    if entry.get("gpu"):
        parts.append("--gpus all")
    parts += [f"-e {shlex.quote(env)}" for env in entry.get("env") or []]
    parts.append('-v "$PWD":/work')
    if entry.get("database"):
        parts.append(f'-v "{_database_setup(name, entry)["host_dir"]}":/work/database')
    parts += ["-w /work", entry["image"], args]
    row: dict[str, Any] = {
        "step": index,
        "tool": name,
        "operation": operation,
        "description": op_cfg.get("description"),
        "command": " ".join(parts),
    }
    notes = []
    if missing:
        row["fill_in"] = {f: (declared.get(f) or {}).get("description") or f for f in missing}
        notes.append(
            f"Step {index} ({name}.{operation}): replace {', '.join('<' + f + '>' for f in missing)} before running."
        )
    if op_cfg.get("notes"):
        row["notes"] = op_cfg["notes"]
    return row, notes


def _resource_warnings(tools_used: dict[str, dict[str, Any]], resources: dict[str, Any]) -> list[str]:
    warnings = []
    for name, entry in tools_used.items():
        want = _resources(entry)
        if (
            resources.get("memory_gb") is not None
            and want.get("memory_gb")
            and resources["memory_gb"] < want["memory_gb"]
        ):
            warnings.append(
                f"{name} is sized for about {want['memory_gb']} GB RAM; this machine has {resources['memory_gb']} GB, so large inputs may fail."
            )
        if resources.get("cpus") is not None and want.get("cpus") and resources["cpus"] < want["cpus"]:
            warnings.append(f"{name} is sized for {want['cpus']} CPUs; it will run slower on {resources['cpus']}.")
        if want.get("gpu") and resources.get("gpu") is False:
            warnings.append(f"{name} needs an NVIDIA GPU with the NVIDIA Container Toolkit.")
    if resources.get("docker") is False:
        warnings.append(
            f"Docker is required: install it from {DOCKER_INSTALL_URL} (Podman or Apptainer can run the same images)."
        )
    return warnings


async def _list_local_tools(args: dict[str, Any]) -> dict[str, Any]:
    cat = catalog()
    if args.get("tool"):
        name = str(args["tool"]).strip()
        return {"tool": _detail(name, _tool(name))}
    category = str(args.get("category") or "").strip().lower()
    query = str(args.get("query") or "").strip().lower()
    rows = []
    for name, entry in cat["tools"].items():
        if category and str(entry.get("category") or "").lower() != category:
            continue
        if query and query not in json.dumps(_summary(name, entry), ensure_ascii=False).lower():
            continue
        rows.append(_summary(name, entry))
    return {
        "count": len(rows),
        "tools": rows,
        "categories": sorted({str(e.get("category")) for e in cat["tools"].values()}),
        "workflows": {k: {"title": w.get("title"), "input": w.get("input")} for k, w in cat["workflows"].items()},
        "notes": [
            "Every tool is a Docker image run on the user's own machine; nothing is uploaded to PhageAgent.",
            "Pass tool=<name> for operations, parameters and database setup; plan_local_analysis turns a workflow or steps into commands.",
        ],
    }


async def _plan_local_analysis(args: dict[str, Any]) -> dict[str, Any]:
    cat = catalog()
    workflow_name = str(args.get("workflow") or "").strip()
    steps = args.get("steps")
    if bool(workflow_name) == bool(steps):
        raise ToolInputError("Give either workflow or steps.")
    workflow: dict[str, Any] = {}
    if workflow_name:
        workflow = cat["workflows"].get(workflow_name) or {}
        if not workflow:
            raise ToolInputError(f"Unknown workflow: {workflow_name}; available: {', '.join(cat['workflows'])}.")
        steps = workflow["steps"]
    if not isinstance(steps, list) or not all(isinstance(s, dict) for s in steps) or len(steps) > 20:
        raise ToolInputError("steps must be a list of at most 20 {tool, operation, input?, params?} objects.")
    resources = dict(args.get("resources") or {})
    cpus = resources.get("cpus")
    input_file = str(args.get("input") or "").strip()
    first_params = dict(args.get("params") or {})
    variables = {"sample": _stem(input_file) if input_file else "sample"}
    for key, mate in (("r1", 1), ("r2", 2)):
        if first_params.get(key):
            variables[f"{key}_trimmed"] = f"{_stem(first_params[key])}_val_{mate}.fq.gz"

    rendered, notes, used = [], [], {}
    for index, raw in enumerate(steps, start=1):
        step = {
            "tool": raw.get("tool"),
            "operation": raw.get("operation"),
            "input": _fill(raw.get("input"), variables),
            "params": {k: _fill(v, variables) for k, v in (raw.get("params") or {}).items()},
        }
        step["input"] = step["input"] or input_file or None
        if index == 1:
            step["params"].update(first_params)
        row, step_notes = _render_step(index, step, cpus=cpus)
        rendered.append(row)
        notes.extend(step_notes)
        used[row["tool"]] = _tool(row["tool"])

    images = list(dict.fromkeys(entry["image"] for entry in used.values()))
    databases = {name: _database_setup(name, entry) for name, entry in used.items() if entry.get("database")}
    setup = [f"docker pull {image}" for image in images]
    if databases:
        setup.insert(0, f'export {DB_ROOT_VAR}="${{{DB_ROOT_VAR}:-$HOME/phageagent-db}}"')
        setup += [db["setup"] for db in databases.values() if db.get("setup")]
    plan: dict[str, Any] = {
        "workflow": workflow_name or None,
        "title": workflow.get("title"),
        "run_in": "The folder that holds the input files; it is mounted at /work and outputs are written there.",
        "setup": setup,
        "steps": rendered,
    }
    if databases:
        plan["databases"] = databases
    if not resources:
        plan["check_first"] = _PREFLIGHT
        notes.append("Run check_first and call plan_local_analysis again with resources for sizing warnings.")
    warnings = _resource_warnings(used, resources)
    if warnings:
        plan["warnings"] = warnings
    if workflow.get("follow_up"):
        plan["follow_up"] = workflow["follow_up"]
    if not input_file and index_needs_input(rendered):
        notes.append("Pass input (the first step's file, relative to the run folder) to fill <input>.")
    notes.append("Show the plan to the user and get their consent before downloading images or databases.")
    plan["notes"] = notes
    return plan


def index_needs_input(rendered: list[dict[str, Any]]) -> bool:
    return bool(rendered) and "input" in (rendered[0].get("fill_in") or {})


_STEP_SCHEMA = {
    "type": "object",
    "properties": {
        "tool": {"type": "string", "description": "Tool name from list_local_tools."},
        "operation": {"type": "string", "description": "Operation of that tool."},
        "input": {"type": "string", "description": "Input file, relative to the run folder."},
        "params": {"type": "object", "description": "Operation parameters (see list_local_tools with tool=<name>)."},
    },
    "required": ["tool", "operation"],
    "additionalProperties": False,
}
_RESOURCES_SCHEMA = {
    "type": "object",
    "description": "The user's machine, as far as known.",
    "properties": {
        "cpus": {"type": "integer", "minimum": 1},
        "memory_gb": {"type": "number", "minimum": 0},
        "disk_gb": {"type": "number", "minimum": 0},
        "gpu": {"type": "boolean", "description": "An NVIDIA GPU usable from Docker."},
        "docker": {"type": "boolean", "description": "Docker (or a compatible runtime) is installed."},
    },
    "additionalProperties": False,
}

LOCAL_TOOLS: tuple[LocalToolSpec, ...] = (
    LocalToolSpec(
        "list_local_tools",
        "List tools for local analysis",
        "Docker images of bioinformatics tools (phage identification, quality, annotation, host prediction, "
        "assembly, alignment...) that the user can run on their own machine, plus ready-made workflows. "
        "Pass tool=<name> for its operations, parameters and database setup.",
        {
            "type": "object",
            "properties": {
                "tool": {"type": "string", "description": "One tool name: return its full detail."},
                "category": {"type": "string", "description": "Only this category, e.g. phage, assembly."},
                "query": {"type": "string", "description": "Case-insensitive text to match in names and descriptions."},
            },
            "additionalProperties": False,
        },
        _list_local_tools,
    ),
    LocalToolSpec(
        "plan_local_analysis",
        "Plan an analysis on the user's machine",
        "Turn a workflow (from list_local_tools) or explicit steps into docker pull, database setup and docker run "
        "commands for the user's own machine, with sizing warnings for the given resources. Nothing runs on "
        "PhageAgent; show the plan to the user before running it.",
        {
            "type": "object",
            "properties": {
                "workflow": {"type": "string", "description": "A workflow name from list_local_tools."},
                "steps": {
                    "type": "array",
                    "items": _STEP_SCHEMA,
                    "minItems": 1,
                    "maxItems": 20,
                    "description": "Explicit steps in run order, instead of a workflow.",
                },
                "input": {"type": "string", "description": "Input file of the first step, relative to the run folder."},
                "params": {
                    "type": "object",
                    "description": "Extra parameters for the first step (e.g. r1/r2 for paired reads).",
                },
                "resources": _RESOURCES_SCHEMA,
            },
            "additionalProperties": False,
        },
        _plan_local_analysis,
    ),
)

LOCAL_TOOLS_BY_NAME: dict[str, LocalToolSpec] = {spec.name: spec for spec in LOCAL_TOOLS}
