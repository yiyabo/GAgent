"""What the pipeline agent can do to a graph: add, remove, connect and configure nodes.

Every operation works on a private copy of the graph and answers with a small JSON-able dict, ``{"ok": True,
...}`` or ``{"ok": False, "error": "..."}``. A mistake of the model (an unknown node type, a port that does
not fit) is an answer it can read and correct, never an exception. The same checks as the editor apply:
data types must fit, a report must stay last, nothing may loop.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Optional, Tuple

from .graph import MAX_LABEL_CHARS, MAX_NODES, _coerce_param, refs_of, validate_graph
from .node_specs import NodeSpec, accepts, get_spec

_ID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")

COLUMN_GAP = 300
ROW_GAP = 150
ORIGIN = 40


def _fail(message: str, **extra: Any) -> Dict[str, Any]:
    return {"ok": False, "error": message, **extra}


def split_ref(text: Any) -> Optional[Tuple[str, str]]:
    """``"node.port"`` to ``("node", "port")``."""
    node, dot, port = str(text or "").strip().partition(".")
    return (node, port) if dot and node and port else None


def _parents(node: Dict[str, Any]) -> List[str]:
    found: List[str] = []
    for value in (node.get("inputs") or {}).values():
        for ref in refs_of(value):
            if ref["node"] not in found:
                found.append(ref["node"])
    return found


def layered_layout(graph: Dict[str, Any]) -> Dict[str, Dict[str, float]]:
    """Columns by distance from the inputs, rows ordered by where their parents sit."""
    nodes = graph.get("nodes") or {}
    depth: Dict[str, int] = {}

    def depth_of(node_id: str, trail: Tuple[str, ...] = ()) -> int:
        if node_id in depth:
            return depth[node_id]
        if node_id in trail:
            return 0
        parents = [parent for parent in _parents(nodes[node_id]) if parent in nodes]
        value = max((depth_of(parent, trail + (node_id,)) for parent in parents), default=-1) + 1
        depth[node_id] = value
        return value

    columns: Dict[int, List[str]] = {}
    for node_id in nodes:
        columns.setdefault(depth_of(node_id), []).append(node_id)
    row: Dict[str, int] = {}
    layout: Dict[str, Dict[str, float]] = {}
    for column in sorted(columns):
        members = columns[column]

        def weight(node_id: str) -> float:
            placed = [row[parent] for parent in _parents(nodes[node_id]) if parent in row]
            return sum(placed) / len(placed) if placed else float("inf")

        ordered = members if column == 0 else sorted(members, key=weight)
        for index, node_id in enumerate(ordered):
            row[node_id] = index
            layout[node_id] = {"x": ORIGIN + column * COLUMN_GAP, "y": ORIGIN + index * ROW_GAP}
    return layout


class GraphEditor:
    """A working copy of a pipeline graph and the operations the agent may apply to it."""

    def __init__(self, graph: Optional[Dict[str, Any]], files: Optional[List[str]] = None) -> None:
        source = graph if isinstance(graph, dict) else {}
        self.graph: Dict[str, Any] = {
            "schema": 1,
            "nodes": copy.deepcopy(source.get("nodes") or {}),
            "layout": copy.deepcopy(source.get("layout") or {}),
        }
        self.files = files or []  # uploaded files, as paths relative to the pipeline's folder
        self.changed = False

    # ---------------------------------------------------------------- helpers
    @property
    def nodes(self) -> Dict[str, Dict[str, Any]]:
        return self.graph["nodes"]

    def _node(self, node_id: Any) -> Tuple[Optional[Dict[str, Any]], Optional[NodeSpec], Optional[Dict[str, Any]]]:
        node = self.nodes.get(str(node_id))
        if node is None:
            known = ", ".join(self.nodes) or "none yet"
            return None, None, _fail(f"there is no node {node_id!r}; nodes: {known}")
        spec = get_spec(node.get("type"))
        if spec is None:
            return None, None, _fail(f"node {node_id} has an unknown type {node.get('type')!r}")
        return node, spec, None

    def _next_id(self, spec: NodeSpec) -> str:
        base = spec.type.replace(".", "_")
        index = 1
        while f"{base}_{index}" in self.nodes:
            index += 1
        return f"{base}_{index}"

    def _reaches(self, start: str, goal: str) -> bool:
        seen, stack = set(), [start]
        while stack:
            current = stack.pop()
            if current == goal:
                return True
            if current in seen or current not in self.nodes:
                continue
            seen.add(current)
            stack.extend(self._children(current))
        return False

    def _children(self, node_id: str) -> List[str]:
        return [other for other, node in self.nodes.items() if node_id in _parents(node)]

    def _free_spot(self) -> Dict[str, float]:
        taken = list((self.graph.get("layout") or {}).values())
        column = 0
        while True:
            for row in range(40):
                spot = {"x": ORIGIN + column * COLUMN_GAP, "y": ORIGIN + row * ROW_GAP}
                if all(abs(other.get("x", 0) - spot["x"]) > 240 or abs(other.get("y", 0) - spot["y"]) > 130 for other in taken):
                    return spot
            column += 1

    def _resolve_file(self, value: str) -> Optional[str]:
        text = str(value or "").strip()
        for item in self.files:
            if text in (item, item.split("/", 1)[-1]) or item.split("/", 1)[-1].endswith("_" + text):
                return item
        return None

    # ---------------------------------------------------------------- operations
    def add_node(
        self,
        type: Any,  # noqa: A002 - the tool's argument name
        id: Any = None,  # noqa: A002
        label: Any = None,
        params: Any = None,
        inputs: Any = None,
    ) -> Dict[str, Any]:
        spec = get_spec(type)
        if spec is None:
            return _fail(f"unknown node type {type!r}; use one of the types in the catalog")
        if len(self.nodes) >= MAX_NODES:
            return _fail(f"a pipeline can have at most {MAX_NODES} nodes")
        node_id = str(id).strip() if id else self._next_id(spec)
        if not _ID.match(node_id):
            return _fail("a node id uses letters, digits, _ and - only (at most 40 characters)")
        if node_id in self.nodes:
            return _fail(f"there is already a node {node_id}")
        defaults = {param.name: param.default for param in spec.params if param.default is not None}
        self.nodes[node_id] = {"type": spec.type, "params": defaults, "inputs": {}}
        self.graph["layout"][node_id] = self._free_spot()
        self.changed = True
        problems: List[str] = []
        if label:
            self.nodes[node_id]["label"] = str(label).strip()[:MAX_LABEL_CHARS]
        if isinstance(params, dict) and params:
            result = self.set_params(node_id, params)
            if not result["ok"]:
                problems.append(result["error"])
        if isinstance(inputs, dict):
            for port, source in inputs.items():
                for item in source if isinstance(source, list) else [source]:
                    result = self.connect(item, f"{node_id}.{port}")
                    if not result["ok"]:
                        problems.append(result["error"])
        answer: Dict[str, Any] = {"ok": True, "node": node_id, "type": spec.type}
        if problems:
            answer["problems"] = problems
        return answer

    def remove_node(self, node: Any) -> Dict[str, Any]:
        found, _, error = self._node(node)
        if error:
            return error
        node_id = str(node)
        del self.nodes[node_id]
        self.graph["layout"].pop(node_id, None)
        for other in self.nodes.values():
            kept_inputs: Dict[str, Any] = {}
            for port, value in (other.get("inputs") or {}).items():
                kept = [ref for ref in refs_of(value) if ref["node"] != node_id]
                if kept:
                    kept_inputs[port] = kept if isinstance(value, list) else kept[0]
            other["inputs"] = kept_inputs
        self.changed = True
        return {"ok": True, "removed": node_id}

    def connect(self, source: Any, target: Any) -> Dict[str, Any]:
        out_ref, in_ref = split_ref(source), split_ref(target)
        if not out_ref:
            return _fail(f"'from' must look like node.port, got {source!r}")
        if not in_ref:
            return _fail(f"'to' must look like node.port, got {target!r}")
        from_node, from_spec, error = self._node(out_ref[0])
        if error:
            return error
        to_node, to_spec, error = self._node(in_ref[0])
        if error:
            return error
        out_port = from_spec.output_port(out_ref[1])
        if out_port is None:
            names = ", ".join(port.name for port in from_spec.outputs)
            return _fail(f"{from_spec.type} has no output {out_ref[1]!r}; its outputs: {names}")
        in_port = to_spec.input_port(in_ref[1])
        if in_port is None:
            names = ", ".join(port.name for port in to_spec.inputs) or "none"
            return _fail(f"{to_spec.type} has no input {in_ref[1]!r}; its inputs: {names}")
        if out_ref[0] == in_ref[0]:
            return _fail("a node cannot read its own output")
        if not accepts(in_port, out_port.type):
            return _fail(
                f"type mismatch: {from_spec.type}.{out_port.name} gives {out_port.type}, "
                f"but {to_spec.type}.{in_port.name} takes {', '.join(in_port.types)}"
            )
        if from_spec.terminal:
            return _fail("the report node must be last; nothing can read its output")
        if self._reaches(in_ref[0], out_ref[0]):
            return _fail("that link would make the pipeline loop")
        new_ref = {"node": out_ref[0], "port": out_ref[1]}
        inputs = to_node.setdefault("inputs", {})
        existing = refs_of(inputs.get(in_port.name))
        replaced: Optional[str] = None
        if in_port.multiple:
            if new_ref in existing:
                return {"ok": True, "note": "already connected"}
            inputs[in_port.name] = existing + [new_ref]
        else:
            if existing:
                replaced = f"{existing[0]['node']}.{existing[0]['port']}"
            inputs[in_port.name] = new_ref
        self.changed = True
        answer: Dict[str, Any] = {"ok": True, "link": f"{source} -> {target}"}
        if replaced:
            answer["replaced"] = replaced
        return answer

    def disconnect(self, target: Any, source: Any = None) -> Dict[str, Any]:
        in_ref = split_ref(target)
        if not in_ref:
            return _fail(f"'to' must look like node.port, got {target!r}")
        node, spec, error = self._node(in_ref[0])
        if error:
            return error
        existing = refs_of((node.get("inputs") or {}).get(in_ref[1]))
        if not existing:
            return _fail(f"{target} has no connection")
        drop_node = str(source).split(".", 1)[0] if source else None
        kept = [ref for ref in existing if drop_node and ref["node"] != drop_node]
        if source and len(kept) == len(existing):
            return _fail(f"{target} is not connected to {source}")
        port = spec.input_port(in_ref[1])
        if kept:
            node["inputs"][in_ref[1]] = kept if port and port.multiple else kept[0]
        else:
            node["inputs"].pop(in_ref[1], None)
        self.changed = True
        return {"ok": True, "disconnected": target}

    def set_params(self, node: Any, params: Any) -> Dict[str, Any]:
        found, spec, error = self._node(node)
        if error:
            return error
        if not isinstance(params, dict) or not params:
            return _fail("params must be an object such as {\"evalue\": 1e-5}")
        applied: Dict[str, Any] = {}
        problems: List[str] = []
        for name, raw in params.items():
            param = spec.param(str(name))
            if param is None:
                names = ", ".join(item.name for item in spec.params) or "none"
                problems.append(f"{spec.type} has no parameter {name!r}; its parameters: {names}")
                continue
            if raw is None or (isinstance(raw, str) and not raw.strip()):
                found.setdefault("params", {}).pop(param.name, None)
                if param.default is not None:
                    found["params"][param.name] = param.default
                applied[param.name] = param.default
                continue
            if param.kind == "path":
                relative = self._resolve_file(str(raw))
                if relative is None:
                    listing = ", ".join(self.files) or "no files uploaded yet"
                    problems.append(f"no uploaded file {raw!r}; the user must upload it first (uploaded: {listing})")
                    continue
                raw = relative
            value, message = _coerce_param(param, raw)
            if message:
                problems.append(f"{param.name} {message}")
                continue
            found.setdefault("params", {})[param.name] = value
            applied[param.name] = value
        if applied:
            self.changed = True
        if problems and not applied:
            return _fail("; ".join(problems))
        answer: Dict[str, Any] = {"ok": True, "node": str(node), "set": applied}
        if problems:
            answer["problems"] = problems
        return answer

    def set_label(self, node: Any, label: Any) -> Dict[str, Any]:
        found, _, error = self._node(node)
        if error:
            return error
        text = str(label or "").strip()[:MAX_LABEL_CHARS]
        if text:
            found["label"] = text
        else:
            found.pop("label", None)
        self.changed = True
        return {"ok": True, "node": str(node), "label": text}

    def tidy(self) -> Dict[str, Any]:
        self.graph["layout"] = layered_layout(self.graph)
        self.changed = True
        return {"ok": True}

    def validate(self) -> Dict[str, Any]:
        result = validate_graph(self.graph, for_run=True)
        return {
            "ok": True,
            "ready_to_run": result.ok,
            "errors": [issue.as_dict() for issue in result.errors],
            "warnings": [issue.as_dict() for issue in result.warnings],
        }


def tool_schemas() -> List[Dict[str, Any]]:
    """The function definitions offered to the model."""

    def function(name: str, description: str, properties: Dict[str, Any], required: List[str]) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {"type": "object", "properties": properties, "required": required},
            },
        }

    ref = {"type": "string", "description": "An output or input port written as node_id.port_name"}
    return [
        function(
            "add_node",
            "Add a node. Choose a short readable id (e.g. lambda_genes) so later calls can refer to it. "
            "'inputs' maps input port names to the output they read, e.g. {\"genome\": \"lambda.genome\"}.",
            {
                "type": {"type": "string", "description": "A node type from the catalog, e.g. prodigal.predict"},
                "id": {"type": "string", "description": "Optional id: letters, digits, _ and - only"},
                "label": {"type": "string", "description": "Optional short label shown on the node, e.g. 'lambda' or 'T7'"},
                "params": {"type": "object", "description": "Parameter values to set; the rest keep their defaults"},
                "inputs": {"type": "object", "description": "Input port name -> 'node_id.port_name' (or a list for a port that takes several)"},
            },
            ["type"],
        ),
        function("remove_node", "Remove a node and every link to it.", {"node": {"type": "string"}}, ["node"]),
        function(
            "connect",
            "Link an output port to an input port. The data types must fit. Linking to an input that already has a "
            "link replaces it, except inputs that take several results (the report's).",
            {"from": {**ref, "description": "The output: node_id.port_name"}, "to": {**ref, "description": "The input: node_id.port_name"}},
            ["from", "to"],
        ),
        function(
            "disconnect",
            "Remove the link into an input port (or just the one from a given node).",
            {"to": {**ref, "description": "The input: node_id.port_name"}, "from_node": {"type": "string", "description": "Only remove the link from this node"}},
            ["to"],
        ),
        function(
            "set_params",
            "Set parameters of a node (use null to restore a default). Choose values from the catalog's ranges and help.",
            {"node": {"type": "string"}, "params": {"type": "object"}},
            ["node", "params"],
        ),
        function("set_label", "Change the short label shown on a node.", {"node": {"type": "string"}, "label": {"type": "string"}}, ["node", "label"]),
        function("tidy_layout", "Arrange all nodes in tidy columns from inputs to results.", {}, []),
        function(
            "validate",
            "Check the pipeline the way the Run button does. Call it after building or changing a pipeline.",
            {},
            [],
        ),
        function(
            "read_output",
            "Read a result of the latest run (a table or text file, or the files of a folder). Use it to explain or "
            "interpret what a node produced.",
            {"node": {"type": "string"}, "port": {"type": "string", "description": "Output port name"}},
            ["node", "port"],
        ),
        function(
            "request_run",
            "Ask the user to run the pipeline. This only shows a confirmation card; nothing runs until the user "
            "agrees. Use it when the pipeline is ready and the user wants results.",
            {"reason": {"type": "string", "description": "One sentence: what the run will do and roughly how long it takes"}},
            ["reason"],
        ),
    ]
