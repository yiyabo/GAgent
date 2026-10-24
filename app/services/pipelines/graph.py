"""Pipeline graphs: the stored format, its validation and the order nodes run in.

A graph is ComfyUI-like JSON::

    {"schema": 1,
     "nodes": {"n1": {"type": "input.genome", "label": "...", "params": {"path": "..."}},
               "n2": {"type": "prodigal.predict", "params": {"mode": "meta"},
                      "inputs": {"genome": {"node": "n1", "port": "file"}}}},
     "layout": {"n1": {"x": 0, "y": 0}}}

``inputs`` maps an input port to the upstream ``{"node", "port"}`` it reads, or to a list of them for a
port that takes several. Validation reports every problem it finds with the node, port or parameter it
concerns, so the editor (and later an agent) can point at it.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .node_specs import NodeSpec, Param, accepts, get_spec

SCHEMA_VERSION = 1
MAX_NODES = 60
MAX_LABEL_CHARS = 80
MAX_ACCESSIONS = 50

_NODE_ID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_ACCESSION = re.compile(r"^[A-Za-z0-9_.]{1,40}$")


@dataclass
class Issue:
    code: str
    message: str
    node: Optional[str] = None
    port: Optional[str] = None
    param: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {key: value for key, value in self.__dict__.items() if value is not None}


@dataclass
class Validation:
    graph: Dict[str, Any]
    errors: List[Issue] = field(default_factory=list)
    warnings: List[Issue] = field(default_factory=list)
    order: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": [issue.as_dict() for issue in self.errors],
            "warnings": [issue.as_dict() for issue in self.warnings],
            "order": list(self.order),
        }


def empty_graph() -> Dict[str, Any]:
    return {"schema": SCHEMA_VERSION, "nodes": {}, "layout": {}}


def refs_of(value: Any) -> List[Dict[str, str]]:
    """The upstream references an input port holds (one, several, or none)."""
    items = value if isinstance(value, list) else [value]
    refs = []
    for item in items:
        if isinstance(item, dict) and item.get("node") and item.get("port"):
            refs.append({"node": str(item["node"]), "port": str(item["port"])})
    return refs


def upstream_nodes(node: Dict[str, Any]) -> List[str]:
    found: List[str] = []
    for value in (node.get("inputs") or {}).values():
        for ref in refs_of(value):
            if ref["node"] not in found:
                found.append(ref["node"])
    return found


def split_accessions(value: Any) -> List[str]:
    text = ",".join(value) if isinstance(value, (list, tuple)) else str(value or "")
    return [part for part in re.split(r"[\s,;]+", text) if part]


def _coerce_param(param: Param, raw: Any) -> Tuple[Any, Optional[str]]:
    """The cleaned value, or an error message."""
    kind = param.kind
    if kind in {"string", "text", "path"}:
        text = str(raw).strip() if raw is not None else ""
        if any(ord(char) < 32 and char not in "\n\t" for char in text):
            return None, "contains control characters"
        if len(text) > param.max_length:
            return None, f"is longer than {param.max_length} characters"
        return text, None
    if kind == "accessions":
        accessions = split_accessions(raw)
        if len(accessions) > MAX_ACCESSIONS:
            return None, f"lists more than {MAX_ACCESSIONS} accessions"
        bad = [item for item in accessions if not _ACCESSION.match(item)]
        if bad:
            return None, f"has invalid accessions: {', '.join(bad[:5])}"
        return ", ".join(accessions), None
    if kind == "enum":
        text = str(raw).strip()
        if text not in param.choices:
            return None, f"must be one of {', '.join(param.choices)}"
        return text, None
    if kind in {"integer", "number"}:
        if isinstance(raw, bool):
            return None, "must be a number"
        try:
            number = int(raw) if kind == "integer" and not isinstance(raw, float) else float(raw)
        except (TypeError, ValueError):
            return None, "must be a number"
        if kind == "integer":
            if isinstance(number, float):
                if not number.is_integer():
                    return None, "must be a whole number"
                number = int(number)
        elif not math.isfinite(number):
            return None, "must be a finite number"
        if param.minimum is not None and number < param.minimum:
            return None, f"must be at least {param.minimum:g}"
        if param.maximum is not None and number > param.maximum:
            return None, f"must be at most {param.maximum:g}"
        return number, None
    return None, f"has an unsupported kind {kind}"


def _empty(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def clean_params(spec: NodeSpec, raw: Any, node_id: str, issues: List[Issue], *, for_run: bool) -> Dict[str, Any]:
    """Defaults applied, values coerced; unknown or invalid values become issues."""
    given = raw if isinstance(raw, dict) else {}
    cleaned: Dict[str, Any] = {}
    for name in given:
        if spec.param(name) is None:
            issues.append(Issue("unknown_param", f"{spec.type} has no parameter {name}", node=node_id, param=name))
    for param in spec.params:
        value = given.get(param.name)
        if _empty(value):
            if param.default is not None:
                cleaned[param.name] = param.default
            elif param.required or (param.kind == "path" and for_run):
                issues.append(Issue("missing_param", f"{param.name} is required", node=node_id, param=param.name))
            continue
        coerced, error = _coerce_param(param, value)
        if error:
            issues.append(Issue("invalid_param", f"{param.name} {error}", node=node_id, param=param.name))
            continue
        cleaned[param.name] = coerced
    return cleaned


def _find_cycle(nodes: Dict[str, Dict[str, Any]]) -> Tuple[List[str], List[str]]:
    """Kahn's order over the known nodes, and the nodes left over (on or behind a cycle)."""
    indegree = {node_id: 0 for node_id in nodes}
    children: Dict[str, List[str]] = {node_id: [] for node_id in nodes}
    for node_id, node in nodes.items():
        for parent in upstream_nodes(node):
            if parent in nodes:
                indegree[node_id] += 1
                children[parent].append(node_id)
    ready = sorted(node_id for node_id, degree in indegree.items() if degree == 0)
    order: List[str] = []
    while ready:
        current = ready.pop(0)
        order.append(current)
        for child in sorted(children[current]):
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
        ready.sort()
    leftover = sorted(node_id for node_id in nodes if node_id not in order)
    return order, leftover


def validate_graph(raw: Any, *, for_run: bool = False) -> Validation:
    """Check a graph. ``for_run`` also requires every input node to have its file chosen."""
    result = Validation(graph=empty_graph())
    if not isinstance(raw, dict):
        result.errors.append(Issue("invalid_graph", "the pipeline must be a JSON object"))
        return result
    schema = raw.get("schema", SCHEMA_VERSION)
    if schema != SCHEMA_VERSION:
        result.errors.append(Issue("unsupported_schema", f"schema {schema!r} is not supported"))
        return result
    raw_nodes = raw.get("nodes")
    if raw_nodes is None:
        raw_nodes = {}
    if not isinstance(raw_nodes, dict):
        result.errors.append(Issue("invalid_graph", "nodes must be an object keyed by node id"))
        return result
    if len(raw_nodes) > MAX_NODES:
        result.errors.append(Issue("too_many_nodes", f"a pipeline can have at most {MAX_NODES} nodes"))
        return result

    nodes: Dict[str, Dict[str, Any]] = {}
    specs: Dict[str, NodeSpec] = {}
    for node_id, node in raw_nodes.items():
        node_id = str(node_id)
        if not _NODE_ID.match(node_id):
            result.errors.append(Issue("invalid_node_id", f"node id {node_id!r} is not allowed", node=node_id))
            continue
        if not isinstance(node, dict):
            result.errors.append(Issue("invalid_node", "a node must be an object", node=node_id))
            continue
        spec = get_spec(node.get("type"))
        if spec is None:
            result.errors.append(Issue("unknown_node_type", f"unknown node type {node.get('type')!r}", node=node_id))
            continue
        label = str(node.get("label") or "").strip()[:MAX_LABEL_CHARS]
        params = clean_params(spec, node.get("params"), node_id, result.errors, for_run=for_run)
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        clean_inputs: Dict[str, Any] = {}
        for port_name, value in inputs.items():
            port = spec.input_port(str(port_name))
            if port is None:
                result.errors.append(
                    Issue("unknown_port", f"{spec.type} has no input {port_name}", node=node_id, port=str(port_name))
                )
                continue
            refs = refs_of(value)
            if not refs:
                continue
            if len(refs) > 1 and not port.multiple:
                result.errors.append(
                    Issue("too_many_links", f"input {port.name} takes one connection", node=node_id, port=port.name)
                )
                refs = refs[:1]
            clean_inputs[port.name] = refs if port.multiple else refs[0]
        entry: Dict[str, Any] = {"type": spec.type, "params": params, "inputs": clean_inputs}
        if label:
            entry["label"] = label
        nodes[node_id] = entry
        specs[node_id] = spec

    # Connections: the upstream node and port exist and the data types fit.
    for node_id, node in nodes.items():
        spec = specs[node_id]
        for port in spec.inputs:
            value = node["inputs"].get(port.name)
            refs = refs_of(value) if value is not None else []
            if not refs and port.required:
                result.errors.append(
                    Issue("missing_input", f"input {port.name} is not connected", node=node_id, port=port.name)
                )
            for ref in refs:
                source_spec = specs.get(ref["node"])
                if source_spec is None:
                    result.errors.append(
                        Issue(
                            "dangling_link",
                            f"input {port.name} reads from a node that does not exist ({ref['node']})",
                            node=node_id,
                            port=port.name,
                        )
                    )
                    continue
                if ref["node"] == node_id:
                    result.errors.append(Issue("self_link", "a node cannot read its own output", node=node_id, port=port.name))
                    continue
                source_port = source_spec.output_port(ref["port"])
                if source_port is None:
                    result.errors.append(
                        Issue(
                            "dangling_link",
                            f"{source_spec.type} has no output {ref['port']}",
                            node=node_id,
                            port=port.name,
                        )
                    )
                    continue
                if not accepts(port, source_port.type):
                    result.errors.append(
                        Issue(
                            "type_mismatch",
                            f"input {port.name} takes {', '.join(port.types)}, not {source_port.type}",
                            node=node_id,
                            port=port.name,
                        )
                    )
                if source_spec.terminal:
                    result.errors.append(
                        Issue(
                            "report_not_last",
                            f"{source_spec.type} must be at the end of the pipeline; nothing can read its output",
                            node=ref["node"],
                        )
                    )

    order, leftover = _find_cycle(nodes)
    if leftover:
        result.errors.append(Issue("cycle", "these nodes form a loop: " + ", ".join(leftover)))
    result.order = order

    consumed = {ref["node"] for node in nodes.values() for value in node["inputs"].values() for ref in refs_of(value)}
    for node_id, spec in specs.items():
        if spec.kind in {"input", "ncbi"} and node_id not in consumed:
            result.warnings.append(Issue("unused_node", "nothing reads this node's output", node=node_id))
    if nodes and not any(spec.kind in {"tool", "agent"} for spec in specs.values()):
        result.warnings.append(Issue("nothing_to_run", "the pipeline has no tool or report node"))
    if not nodes:
        result.warnings.append(Issue("empty", "the pipeline has no nodes"))

    layout = raw.get("layout") if isinstance(raw.get("layout"), dict) else {}
    clean_layout = {}
    for node_id, position in layout.items():
        if node_id in nodes and isinstance(position, dict):
            try:
                clean_layout[node_id] = {"x": float(position.get("x", 0)), "y": float(position.get("y", 0))}
            except (TypeError, ValueError):
                continue
    result.graph = {"schema": SCHEMA_VERSION, "nodes": nodes, "layout": clean_layout}
    return result


def graph_hash(graph: Dict[str, Any]) -> str:
    """Identity of what a graph runs (layout excluded), for spotting an unchanged draft."""
    payload = {"schema": graph.get("schema"), "nodes": graph.get("nodes") or {}}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def iter_refs(node: Dict[str, Any]) -> Iterable[Tuple[str, Dict[str, str]]]:
    for port_name, value in (node.get("inputs") or {}).items():
        for ref in refs_of(value):
            yield port_name, ref
