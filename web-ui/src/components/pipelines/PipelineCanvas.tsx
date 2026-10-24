import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Background,
  Controls,
  ReactFlow,
  applyNodeChanges,
  type Connection,
  type Edge,
  type FinalConnectionState,
  type InternalNode,
  type NodeChange,
  type ReactFlowInstance,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import type { NodeCatalog, NodeRun, PipelineGraph } from '@api/pipelines';
import { useThemeStore } from '@/theme/themeMode';
import { useT } from '@/i18n/useT';
import PipelineNodeView from './PipelineNode';
import { StatusLegend } from './PipelineStatus';
import {
  connect,
  connectionProblem,
  disconnect,
  moveNode,
  parseEdgeId,
  removeNode,
  specIndex,
  toFlow,
  type GraphUpdate,
  type PipelineFlowNode,
} from './graphModel';

const NODE_TYPES = { pipeline: PipelineNodeView };

export type ConnectionProblem = NonNullable<ReturnType<typeof connectionProblem>>;
export type PipelineFlowInstance = ReactFlowInstance<PipelineFlowNode, Edge>;

interface Props {
  catalog: NodeCatalog;
  graph: PipelineGraph;
  readOnly?: boolean;
  runNodes?: Record<string, NodeRun>;
  issues?: Map<string, string[]>;
  issueLevels?: Map<string, 'error' | 'warning'>;
  selectedNode: string | null;
  onSelectNode: (id: string | null) => void;
  onChange?: (update: GraphUpdate) => void;
  onRejectConnection?: (problem: ConnectionProblem) => void;
  onInit?: (instance: PipelineFlowInstance) => void;
}

const PipelineCanvas: React.FC<Props> = ({
  catalog,
  graph,
  readOnly = false,
  runNodes,
  issues,
  issueLevels,
  selectedNode,
  onSelectNode,
  onChange,
  onRejectConnection,
  onInit,
}) => {
  const t = useT();
  const colorMode = useThemeStore((state) => state.mode);
  const specs = useMemo(() => specIndex(catalog), [catalog]);
  const flow = useMemo(() => toFlow(graph, specs, runNodes, issues, issueLevels), [graph, specs, runNodes, issues, issueLevels]);
  const [nodes, setNodes] = useState<PipelineFlowNode[]>(flow.nodes);

  useEffect(() => {
    // Keep what React Flow measured: a node without `measured` is hidden and measured again.
    setNodes((current) => {
      const measured = new Map(current.map((node) => [node.id, node.measured]));
      return flow.nodes.map((node) => ({ ...node, measured: measured.get(node.id), selected: node.id === selectedNode }));
    });
  }, [flow.nodes, selectedNode]);

  const onNodesChange = useCallback((changes: NodeChange<PipelineFlowNode>[]) => {
    // Removal goes through onNodesDelete so the graph (not only the canvas) loses the node.
    setNodes((current) => applyNodeChanges(changes.filter((change) => change.type !== 'remove'), current));
  }, []);

  const problemOf = useCallback(
    (connection: Connection | Edge) => {
      if (!connection.source || !connection.target || !connection.sourceHandle || !connection.targetHandle) return 'unknown' as const;
      return connectionProblem(
        graph,
        specs,
        catalog.any_type,
        { node: connection.source, port: connection.sourceHandle },
        { node: connection.target, port: connection.targetHandle },
      );
    },
    [graph, specs, catalog.any_type],
  );

  const onConnect = useCallback(
    (connection: Connection) => {
      if (readOnly || !onChange || problemOf(connection)) return;
      const source = { node: connection.source, port: connection.sourceHandle as string };
      const target = { node: connection.target, port: connection.targetHandle as string };
      onChange((current) => connect(current, specs, source, target));
    },
    [readOnly, onChange, problemOf, specs],
  );

  // A refused drop never reaches onConnect; say why instead of silently snapping back.
  const onConnectEnd = useCallback(
    (_event: MouseEvent | TouchEvent, state: FinalConnectionState<InternalNode<PipelineFlowNode>>) => {
      if (readOnly || state.isValid !== false || !state.fromHandle || !state.toHandle) return;
      const [source, target] = state.fromHandle.type === 'source' ? [state.fromHandle, state.toHandle] : [state.toHandle, state.fromHandle];
      if (source.type !== 'source' || target.type !== 'target' || !source.id || !target.id) return;
      const problem = connectionProblem(
        graph,
        specs,
        catalog.any_type,
        { node: source.nodeId, port: source.id },
        { node: target.nodeId, port: target.id },
      );
      if (problem) onRejectConnection?.(problem);
    },
    [readOnly, graph, specs, catalog.any_type, onRejectConnection],
  );

  const onEdgesDelete = useCallback(
    (edges: Edge[]) => {
      if (readOnly || !onChange) return;
      const links = edges.map((edge) => parseEdgeId(edge.id)).filter((link): link is NonNullable<typeof link> => link !== null);
      onChange((current) => links.reduce((next, link) => disconnect(next, link.source, link.target), current));
    },
    [readOnly, onChange],
  );

  const onNodesDelete = useCallback(
    (deleted: PipelineFlowNode[]) => {
      if (readOnly || !onChange) return;
      onChange((current) => deleted.reduce((next, node) => removeNode(next, node.id), current));
      if (deleted.some((node) => node.id === selectedNode)) onSelectNode(null);
    },
    [readOnly, onChange, selectedNode, onSelectNode],
  );

  const onNodeDragStop = useCallback(
    (_event: MouseEvent | TouchEvent, node: PipelineFlowNode, dragged: PipelineFlowNode[]) => {
      if (readOnly || !onChange) return;
      const moved = dragged.length ? dragged : [node];
      onChange((current) => moved.reduce((next, item) => moveNode(next, item.id, item.position), current));
    },
    [readOnly, onChange],
  );

  return (
    <div className="pl-canvas" aria-label={t('pipelines.canvasAria')}>
      <ReactFlow<PipelineFlowNode>
        nodes={nodes}
        edges={flow.edges}
        nodeTypes={NODE_TYPES}
        colorMode={colorMode}
        onInit={onInit}
        onNodesChange={onNodesChange}
        onConnect={onConnect}
        onConnectEnd={onConnectEnd}
        onEdgesDelete={onEdgesDelete}
        onNodesDelete={onNodesDelete}
        onNodeDragStop={onNodeDragStop}
        onNodeClick={(_event, node) => onSelectNode(node.id)}
        onPaneClick={() => onSelectNode(null)}
        isValidConnection={(connection) => !readOnly && !problemOf(connection)}
        nodesDraggable={!readOnly}
        nodesConnectable={!readOnly}
        deleteKeyCode={readOnly ? null : ['Backspace', 'Delete']}
        fitView
        fitViewOptions={{ padding: 0.15, maxZoom: 1 }}
        minZoom={0.2}
      >
        <Background gap={20} size={1} />
        <Controls showInteractive={false} />
      </ReactFlow>
      <StatusLegend />
    </div>
  );
};

export default PipelineCanvas;
