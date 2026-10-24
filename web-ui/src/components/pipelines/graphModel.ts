import type { Edge, Node } from '@xyflow/react';
import type {
  Bilingual,
  GraphNode,
  NodeCatalog,
  NodeRun,
  NodeTypeSpec,
  PipelineGraph,
  PortRef,
} from '@api/pipelines';

/** Data each canvas node carries; the canvas only renders it. */
export interface PipelineNodeData extends Record<string, unknown> {
  spec: NodeTypeSpec;
  node: GraphNode;
  run?: NodeRun;
  issues: string[];
  issueLevel?: 'error' | 'warning';
  readyInputs?: string[];
}

export type PipelineFlowNode = Node<PipelineNodeData, 'pipeline'>;

/** A change applied to the newest graph, so two changes made in one tick cannot overwrite each other. */
export type GraphUpdate = (current: PipelineGraph) => PipelineGraph;

export const pick = (text: Bilingual | undefined, language: string): string =>
  !text ? '' : language.startsWith('zh') ? text.zh || text.en : text.en || text.zh;

export function specIndex(catalog: NodeCatalog | undefined): Map<string, NodeTypeSpec> {
  return new Map((catalog?.node_types || []).map((spec) => [spec.type, spec]));
}

export function refsOf(value: PortRef | PortRef[] | undefined): PortRef[] {
  if (!value) return [];
  return Array.isArray(value) ? value : [value];
}

export function emptyGraph(): PipelineGraph {
  return { schema: 1, nodes: {}, layout: {} };
}

const isRecord = (value: unknown): value is Record<string, any> => Boolean(value) && typeof value === 'object' && !Array.isArray(value);

/** A saved draft in the shape the editor works on; drafts are stored as sent, so they may be partial. */
export function normalizeGraph(raw: unknown): PipelineGraph {
  const value = isRecord(raw) ? raw : {};
  return { schema: 1, nodes: isRecord(value.nodes) ? value.nodes : {}, layout: isRecord(value.layout) ? value.layout : {} };
}

/** The nodes a node reads from, each once. */
export function parentsOf(graph: PipelineGraph, id: string): string[] {
  const found: string[] = [];
  for (const value of Object.values(graph.nodes[id]?.inputs || {})) {
    for (const ref of refsOf(value)) if (!found.includes(ref.node)) found.push(ref.node);
  }
  return found;
}

const COLUMN_GAP = 290;
const ROW_GAP = 170;
const ORIGIN = 40;

/** Positions by dependency depth: one column per step, each column ordered to follow its parents. */
export function autoLayout(graph: PipelineGraph): Record<string, { x: number; y: number }> {
  const depth = new Map<string, number>();
  const visiting = new Set<string>();
  const depthOf = (id: string): number => {
    const known = depth.get(id);
    if (known !== undefined) return known;
    if (visiting.has(id)) return 0; // a loop: the server reports it, the layout just has to terminate
    visiting.add(id);
    const parents = parentsOf(graph, id).filter((parent) => graph.nodes[parent]);
    const value = parents.length ? Math.max(...parents.map(depthOf)) + 1 : 0;
    visiting.delete(id);
    depth.set(id, value);
    return value;
  };
  const columns: string[][] = [];
  for (const id of Object.keys(graph.nodes)) (columns[depthOf(id)] ||= []).push(id);
  const row = new Map<string, number>();
  const layout: Record<string, { x: number; y: number }> = {};
  columns.forEach((members, column) => {
    const weight = (id: string) => {
      const placed = parentsOf(graph, id).filter((parent) => row.has(parent));
      return placed.length ? placed.reduce((sum, parent) => sum + (row.get(parent) as number), 0) / placed.length : Number.MAX_SAFE_INTEGER;
    };
    const ordered = column === 0 ? members : [...members].sort((a, b) => weight(a) - weight(b));
    ordered.forEach((id, index) => {
      row.set(id, index);
      layout[id] = { x: ORIGIN + column * COLUMN_GAP, y: ORIGIN + index * ROW_GAP };
    });
  });
  return layout;
}

/** Every node's position: the saved one, or the automatic one for nodes that have none. */
export function positionsOf(graph: PipelineGraph): Record<string, { x: number; y: number }> {
  const saved = graph.layout || {};
  const missing = Object.keys(graph.nodes).some((id) => !saved[id]);
  return missing ? { ...autoLayout(graph), ...saved } : saved;
}

/** The free spot nearest to `start` for a new node, so it does not land on top of another one. */
export function freeSpot(graph: PipelineGraph, start: { x: number; y: number }): { x: number; y: number } {
  const taken = Object.values(positionsOf(graph));
  const free = (spot: { x: number; y: number }) => taken.every((other) => Math.abs(other.x - spot.x) > 240 || Math.abs(other.y - spot.y) > 130);
  for (let ring = 0; ring < 6; ring += 1) {
    for (const [dx, dy] of [[0, 1], [1, 0], [0, -1], [-1, 0], [1, 1], [-1, 1], [1, -1], [-1, -1]]) {
      const spot = { x: Math.round(start.x + dx * ring * COLUMN_GAP), y: Math.round(start.y + dy * ring * (ROW_GAP - 20)) };
      if (free(spot)) return spot;
    }
  }
  return { x: Math.round(start.x), y: Math.round(start.y) };
}

export function nextNodeId(graph: PipelineGraph, type: string): string {
  const base = type.replace(/[^A-Za-z0-9_-]/g, '_').slice(0, 32) || 'node';
  let index = 1;
  while (graph.nodes[`${base}_${index}`]) index += 1;
  return `${base}_${index}`;
}

export function defaultParams(spec: NodeTypeSpec): Record<string, unknown> {
  const params: Record<string, unknown> = {};
  for (const param of spec.params) {
    if (param.default !== null && param.default !== undefined) params[param.name] = param.default;
  }
  return params;
}

export function addNode(graph: PipelineGraph, spec: NodeTypeSpec, position: { x: number; y: number }): { graph: PipelineGraph; id: string } {
  const id = nextNodeId(graph, spec.type);
  return {
    id,
    graph: {
      ...graph,
      nodes: { ...graph.nodes, [id]: { type: spec.type, params: defaultParams(spec), inputs: {} } },
      layout: { ...(graph.layout || {}), [id]: position },
    },
  };
}

export function removeNode(graph: PipelineGraph, id: string): PipelineGraph {
  const nodes: Record<string, GraphNode> = {};
  for (const [nodeId, node] of Object.entries(graph.nodes)) {
    if (nodeId === id) continue;
    const inputs: Record<string, PortRef | PortRef[]> = {};
    for (const [port, value] of Object.entries(node.inputs || {})) {
      const kept = refsOf(value).filter((ref) => ref.node !== id);
      if (!kept.length) continue;
      inputs[port] = Array.isArray(value) ? kept : kept[0];
    }
    nodes[nodeId] = { ...node, inputs };
  }
  const layout = { ...(graph.layout || {}) };
  delete layout[id];
  return { ...graph, nodes, layout };
}

export function updateNode(graph: PipelineGraph, id: string, change: Partial<GraphNode>): PipelineGraph {
  const current = graph.nodes[id];
  if (!current) return graph;
  return { ...graph, nodes: { ...graph.nodes, [id]: { ...current, ...change } } };
}

export function moveNode(graph: PipelineGraph, id: string, position: { x: number; y: number }): PipelineGraph {
  if (!graph.nodes[id]) return graph;
  return { ...graph, layout: { ...(graph.layout || {}), [id]: { x: Math.round(position.x), y: Math.round(position.y) } } };
}

/** The data type an output port produces, or null when the node or port is unknown. */
export function outputType(graph: PipelineGraph, specs: Map<string, NodeTypeSpec>, ref: PortRef): string | null {
  const spec = specs.get(graph.nodes[ref.node]?.type || '');
  return spec?.outputs.find((port) => port.name === ref.port)?.type ?? null;
}

/** Why a connection is not allowed, or null when it is. Mirrors the server's checks. */
export function connectionProblem(
  graph: PipelineGraph,
  specs: Map<string, NodeTypeSpec>,
  anyType: string,
  source: PortRef,
  target: PortRef,
): 'self' | 'type' | 'report' | 'unknown' | 'loop' | null {
  if (source.node === target.node) return 'self';
  const sourceSpec = specs.get(graph.nodes[source.node]?.type || '');
  const targetSpec = specs.get(graph.nodes[target.node]?.type || '');
  if (!sourceSpec || !targetSpec) return 'unknown';
  if (sourceSpec.terminal) return 'report';
  const produced = sourceSpec.outputs.find((port) => port.name === source.port)?.type;
  const port = targetSpec.inputs.find((item) => item.name === target.port);
  if (!produced || !port) return 'unknown';
  if (!port.types.includes(anyType) && !port.types.includes(produced)) return 'type';
  if (reaches(graph, target.node, source.node)) return 'loop';
  return null;
}

/** Whether `from` feeds (directly or not) into `to`. */
function reaches(graph: PipelineGraph, from: string, to: string): boolean {
  const children = new Map<string, string[]>();
  for (const [nodeId, node] of Object.entries(graph.nodes)) {
    for (const value of Object.values(node.inputs || {})) {
      for (const ref of refsOf(value)) children.set(ref.node, [...(children.get(ref.node) || []), nodeId]);
    }
  }
  const stack = [from];
  const seen = new Set<string>();
  while (stack.length) {
    const current = stack.pop()!;
    if (current === to) return true;
    if (seen.has(current)) continue;
    seen.add(current);
    stack.push(...(children.get(current) || []));
  }
  return false;
}

export function connect(graph: PipelineGraph, specs: Map<string, NodeTypeSpec>, source: PortRef, target: PortRef): PipelineGraph {
  const node = graph.nodes[target.node];
  const port = specs.get(node?.type || '')?.inputs.find((item) => item.name === target.port);
  if (!node || !port) return graph;
  const existing = refsOf(node.inputs?.[target.port]);
  const ref = { node: source.node, port: source.port };
  const value = port.multiple
    ? [...existing.filter((item) => !(item.node === ref.node && item.port === ref.port)), ref]
    : ref;
  return updateNode(graph, target.node, { inputs: { ...(node.inputs || {}), [target.port]: value } });
}

export function disconnect(graph: PipelineGraph, source: PortRef, target: PortRef): PipelineGraph {
  const node = graph.nodes[target.node];
  if (!node) return graph;
  const current = node.inputs?.[target.port];
  const kept = refsOf(current).filter((ref) => !(ref.node === source.node && ref.port === source.port));
  const inputs = { ...(node.inputs || {}) };
  if (kept.length) inputs[target.port] = Array.isArray(current) ? kept : kept[0];
  else delete inputs[target.port];
  return updateNode(graph, target.node, { inputs });
}

export const edgeId = (source: PortRef, target: PortRef) => `${source.node}:${source.port}->${target.node}:${target.port}`;

export function parseEdgeId(id: string): { source: PortRef; target: PortRef } | null {
  const match = /^([^:]+):([^-]+)->([^:]+):(.+)$/.exec(id);
  return match ? { source: { node: match[1], port: match[2] }, target: { node: match[3], port: match[4] } } : null;
}

export function toFlow(
  graph: PipelineGraph,
  specs: Map<string, NodeTypeSpec>,
  runNodes?: Record<string, NodeRun>,
  issuesByNode?: Map<string, string[]>,
  issueLevels?: Map<string, 'error' | 'warning'>,
): { nodes: PipelineFlowNode[]; edges: Edge[] } {
  const nodes: PipelineFlowNode[] = [];
  const edges: Edge[] = [];
  const positions = positionsOf(graph);
  for (const [id, node] of Object.entries(graph.nodes)) {
    const spec = specs.get(node.type);
    if (!spec) continue;
    const position = positions[id] || { x: ORIGIN, y: ORIGIN };
    nodes.push({
      id,
      type: 'pipeline',
      position,
      data: {
        spec, node, run: runNodes?.[id], issues: issuesByNode?.get(id) || [],
        issueLevel: issueLevels?.get(id),
        readyInputs: Object.entries(node.inputs || {}).filter(([, value]) =>
          refsOf(value).length > 0 && refsOf(value).every((ref) => runNodes?.[ref.node]?.outputs?.[ref.port]),
        ).map(([port]) => port),
      },
    });
    for (const [port, value] of Object.entries(node.inputs || {})) {
      for (const ref of refsOf(value)) {
        const source = { node: ref.node, port: ref.port };
        const target = { node: id, port };
        edges.push({
          id: edgeId(source, target),
          source: ref.node,
          sourceHandle: ref.port,
          target: id,
          targetHandle: port,
          data: { type: outputType(graph, specs, source) },
          className: `pl-edge pl-edge-${runNodes?.[id]?.status === 'running' ? 'running' : runNodes?.[ref.node]?.outputs?.[ref.port] ? 'ready' : 'pending'}`,
        });
      }
    }
  }
  return { nodes, edges };
}
