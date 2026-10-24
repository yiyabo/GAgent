"""What the pipeline agent is told: the whole node catalog, the current graph, the run and the selection.

The catalog part never changes between requests, so it comes first and the model can reuse its cache. The
state part is rebuilt for every message.
"""

from __future__ import annotations

import functools
from typing import Any, Dict, List, Optional

from .graph import refs_of, validate_graph
from .node_specs import CATEGORIES, DATA_TYPES, NODE_SPECS, NodeSpec, Param, get_spec

GUIDE = """You are the assistant inside a node-based pipeline editor for phage genomics, a bit like ComfyUI. The user
builds an analysis by placing nodes on a canvas and linking their ports. Each tool node runs one bioinformatics tool as
a job on the server; an Agent report node at the end reads the results and writes a report. You can answer questions
about every node and tool, advise on how to build a workflow, and edit the canvas yourself with your tools.

How to work
- Users often do not know how to structure an analysis. Ask one short question when the goal is unclear; otherwise
  propose a sensible pipeline and build it. Prefer small pipelines that answer the question over big ones.
- To change the canvas use the tools (add_node, connect, set_params, ...). You may call several in one step. Give
  nodes readable ids (lambda, lambda_genes, t7_blastp) and short labels so the user can follow the graph. Changes apply
  immediately and the user can undo the whole turn, so act when the user asks for a change; do not ask for permission
  to place nodes. After changing the graph call validate and fix what it reports.
- The CURRENT STATE section below is the truth about the canvas. Earlier messages and the user's own edits may have
  changed it (the user can undo your changes or edit nodes by hand), so never answer from what you remember doing.
- Never claim you ran something. You cannot run the pipeline yourself: when it is ready and the user wants results,
  call request_run, which shows the user a confirmation card. Only do that when the user asked to run it or agreed
  to your offer; otherwise just say it is ready and offer to ask.
- You cannot upload files. If a pipeline needs the user's own sequences, build it with an input node, tell the user to
  upload the file in the node panel and pick it, or use an NCBI download node when they give accession numbers.
- Keep a first pipeline small and focused on the question; the user can ask for more nodes. Do not add nodes the
  question does not need.
- Do not state from memory what a run will find (specific genes, hits, scores, taxonomy). Describe what each result
  will show and how to read it, and leave the numbers to the run. If you are unsure of a biological fact, say so.
- Explain choices briefly: why this tool, why this threshold, what the result will mean. Say when a parameter is a
  trade-off (E-value, sensitivity). Mention limits honestly: there is no nr/nt database on the server, so BLAST and
  MMseqs2 search databases built from sequences in the pipeline; an empty result file is a valid result (no hits), not an
  error; short phage genomes can give empty FastANI or MMseqs2 results.
- Reference nodes the way the user sees them: by title and label, with the id in brackets when it helps. Answer in the
  language of the user's latest message (an English message gets an English answer, a Chinese one a Chinese answer),
  whatever language the node titles shown to you are in.
- To interpret results use read_output on the latest run, then explain what the numbers mean for the question asked.
  Do not invent values you have not read.

Connections
- A link goes from an output port to an input port, written node_id.port_name. The data types must fit ("any" accepts
  everything). An input takes one link, except the report's evidence input, which takes many. Linking to an input that
  already has a link replaces it. The report node must be last. Nothing may loop.
"""


def _type_names() -> str:
    return "; ".join(f"{key} = {value['en']}" for key, value in DATA_TYPES.items())


def _param_line(param: Param) -> str:
    bits = [param.kind]
    if param.choices:
        bits.append("one of " + "/".join(param.choices))
    if param.minimum is not None or param.maximum is not None:
        bits.append(f"range {param.minimum if param.minimum is not None else ''}..{param.maximum if param.maximum is not None else ''}")
    if param.default is not None:
        bits.append(f"default {param.default}")
    if param.required:
        bits.append("required")
    help_text = (param.help or {}).get("en", "")
    return f"    - {param.name} ({', '.join(bits)}): {param.title['en']}. {help_text}".rstrip()


def describe_spec(spec: NodeSpec) -> str:
    lines = [f"### {spec.type}: {spec.title['en']} / {spec.title['zh']}  [{CATEGORIES[spec.category]['en']}]"]
    lines.append(f"  {spec.description['en']}" + ("  (slow: minutes)" if spec.slow else ""))
    if spec.tool:
        lines.append(f"  Runs: {spec.tool} {spec.operation}")
    if spec.inputs:
        lines.append("  Inputs:")
        for port in spec.inputs:
            flags = ["required" if port.required else "optional"] + (["takes several"] if port.multiple else [])
            lines.append(f"    - {port.name} ({'/'.join(port.types)}; {', '.join(flags)}): {port.title['en']}")
    if spec.outputs:
        lines.append("  Outputs:")
        for port in spec.outputs:
            reading = f" How to read it: {port.reading}" if port.reading else ""
            optional = " (not produced when empty)" if port.optional else ""
            lines.append(f"    - {port.name} ({port.type}){optional}: {port.title['en']}.{reading}")
    if spec.params:
        lines.append("  Parameters:")
        lines.extend(_param_line(param) for param in spec.params)
    return "\n".join(lines)


@functools.lru_cache(maxsize=1)
def catalog_text() -> str:
    parts = [GUIDE, "Data types: " + _type_names(), "", "NODE CATALOG"]
    parts.extend(describe_spec(spec) for spec in NODE_SPECS)
    return "\n\n".join(parts)


def _param_text(node: Dict[str, Any], spec: NodeSpec) -> str:
    shown = []
    for param in spec.params:
        value = (node.get("params") or {}).get(param.name)
        if value is None or value == param.default:
            continue
        text = str(value)
        shown.append(f"{param.name}={text if len(text) <= 160 else text[:157] + '...'}")
    return ", ".join(shown)


def describe_graph(graph: Dict[str, Any]) -> str:
    nodes = graph.get("nodes") or {}
    if not nodes:
        return "The canvas is empty."
    lines = []
    for node_id, node in nodes.items():
        spec = get_spec(node.get("type"))
        title = spec.title["en"] if spec else "unknown"
        label = f' "{node["label"]}"' if node.get("label") else ""
        links = []
        for port, value in (node.get("inputs") or {}).items():
            links.append(f"{port}<-" + "+".join(f"{ref['node']}.{ref['port']}" for ref in refs_of(value)))
        params = _param_text(node, spec) if spec else ""
        extra = "; ".join(item for item in (", ".join(links), params and f"set: {params}") if item)
        lines.append(f"- {node_id} [{node.get('type')}] {title}{label}" + (f" ({extra})" if extra else ""))
    return "\n".join(lines)


def describe_run(run: Optional[Dict[str, Any]]) -> str:
    if not run:
        return "No run yet."
    nodes = run.get("nodes") or {}
    counts: Dict[str, int] = {}
    for item in nodes.values():
        counts[item.get("status", "?")] = counts.get(item.get("status", "?"), 0) + 1
    lines = [f"Latest run {run.get('id')}: {run.get('status')} ({', '.join(f'{count} {name}' for name, count in sorted(counts.items()))})."]
    for node_id, item in nodes.items():
        if item.get("status") == "failed":
            lines.append(f"  - {node_id} failed: {str(item.get('error') or '')[:300]}")
    return "\n".join(lines)


def describe_selection(graph: Dict[str, Any], selected: Optional[str], run: Optional[Dict[str, Any]]) -> str:
    node = (graph.get("nodes") or {}).get(selected or "")
    if not node:
        return "No node is selected."
    spec = get_spec(node.get("type"))
    text = [f"The user has selected node {selected} ({spec.title['en'] if spec else node.get('type')}); questions like 'this node' refer to it."]
    result = ((run or {}).get("nodes") or {}).get(selected)
    if result:
        detail = result.get("detail") or {}
        text.append(f"In the latest run it was {result.get('status')}" + (f"; command: {str(detail.get('command'))[:400]}" if detail.get("command") else "") + ".")
        if result.get("error"):
            text.append(f"Its error: {str(result['error'])[:400]}")
    return " ".join(text)


def state_text(
    *,
    name: str,
    graph: Dict[str, Any],
    files: List[str],
    selected: Optional[str],
    run: Optional[Dict[str, Any]],
) -> str:
    validation = validate_graph(graph, for_run=True)
    issues = [f"{issue.node or ''} {issue.message}".strip() for issue in validation.errors[:12]]
    parts = [
        f'CURRENT STATE of the pipeline "{name}"',
        describe_graph(graph),
        "Uploaded files: " + (", ".join(files) if files else "none"),
        "Problems the Run button would report: " + ("; ".join(issues) if issues else "none (ready to run)"),
        describe_run(run),
        describe_selection(graph, selected, run),
    ]
    return "\n".join(parts)
