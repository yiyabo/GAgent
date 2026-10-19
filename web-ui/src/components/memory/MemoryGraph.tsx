import React, { useEffect, useRef, useState, useCallback, useMemo } from 'react';
import { Network } from 'vis-network';
import { DataSet } from 'vis-data';
import { Card, Spin, Button, Space, Select, Input, message, Badge, Radio, Slider, Tooltip, Empty, Alert } from 'antd';
import { ReloadOutlined, ExpandOutlined, FullscreenOutlined, SearchOutlined, InboxOutlined } from '@ant-design/icons';
import { useMemoryStore } from '@store/memory';
import type { Memory } from '@/types';
import { useCanvasPalette, withAlpha, type CanvasPalette } from '@/theme/canvasPalette';

type GraphNode = {
  id: string;
  label: string;
  title: string;
  shape: string;
  size: number;
  color: Record<string, any>;
  font: Record<string, any>;
  borderWidth: number;
  shadow: Record<string, any>;
  memoryData?: Memory;
};

interface NetworkData {
  nodes: DataSet<GraphNode>;
  edges: DataSet<Record<string, any>>;
}

interface MemoryGraphProps {
  onNodeClick?: (memory: Memory) => void;
  height?: string;
}

const MemoryGraph: React.FC<MemoryGraphProps> = ({ onNodeClick, height = '600px' }) => {
  const networkRef = useRef<HTMLDivElement>(null);
  const networkInstance = useRef<Network | null>(null);
  const { memories, getFilteredMemories } = useMemoryStore();
  const palette = useCanvasPalette();

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [layout, setLayout] = useState<'force' | 'hierarchical' | 'circular'>('force');
  const [searchText, setSearchText] = useState('');
  const [minSimilarity, setMinSimilarity] = useState(0.5);
  const [showLabels, setShowLabels] = useState(true);

  const getNodeSize = (importance: Memory['importance'], retrievalCount: number) => {
  const baseSize = {
  critical: 50,
  high: 40,
  medium: 30,
  low: 25,
  temporary: 20,
  }[importance] || 30;

  const bonus = Math.min(retrievalCount * 2, baseSize * 0.5);
  return baseSize + bonus;
  };

  /** Importance ramps cool to hot; the fill is a tint of the status colour so labels stay legible on both themes. */
  const getNodeColor = (importance: Memory['importance'], p: CanvasPalette) => {
  const tone = {
  critical: p.error,
  high: p.warning,
  medium: p.action,
  low: p.success,
  temporary: p.textTertiary,
  }[importance] || p.action;
  return { background: withAlpha(tone, 0.22), border: tone, highlight: { background: withAlpha(tone, 0.4), border: tone } };
  };

  const getNodeShape = (type: Memory['memory_type']) => {
  const shapes = {
  conversation: 'box',
  experience: 'ellipse',
  knowledge: 'diamond',
  context: 'star',
  };
  return shapes[type] || 'dot';
  };

  const getEdgeStyle = (similarity: number, p: CanvasPalette) => {
  let color, width;

  if (similarity > 0.8) {
  color = p.success;
  width = 5;
  } else if (similarity > 0.6) {
  color = p.action;
  width = 3;
  } else {
  color = p.borderStrong;
  width = 2;
  }

  return {
  color: { color, highlight: color, hover: color },
  width,
  };
  };

  const buildNetworkData = useCallback<() => NetworkData>(() => {
  setLoading(true);
  setError(null);

  try {
  let filteredMemories = getFilteredMemories();

  if (filteredMemories.length === 0) {
  setLoading(false);
  return {
  nodes: new DataSet<GraphNode>([]),
  edges: new DataSet<Record<string, any>>([]),
  };
  }

  if (searchText) {
  filteredMemories = filteredMemories.filter(m =>
  m.content.toLowerCase().includes(searchText.toLowerCase()) ||
  m.keywords.some(k => k.toLowerCase().includes(searchText.toLowerCase())) ||
  m.tags.some(t => t.toLowerCase().includes(searchText.toLowerCase()))
  );
  }

  const nodes: GraphNode[] = filteredMemories.map((memory) => {
  const size = getNodeSize(memory.importance, memory.retrieval_count);
  const colors = getNodeColor(memory.importance, palette);
  const shape = getNodeShape(memory.memory_type);

  const label = showLabels
  ? memory.keywords.length > 0
  ? memory.keywords.slice(0, 2).join(', ')
  : memory.content.substring(0, 30) + (memory.content.length > 30 ? '...' : '')
  : '';

  const title = `
🧠 memory
━━━━━━━━━━━━━━━━
📋 ID: ${memory.id.substring(0, 8)}...
📝 content: ${memory.content.substring(0, 100)}${memory.content.length > 100 ? '...' : ''}
🏷️ type: ${memory.memory_type}
⭐ : ${memory.importance}
🔑 : ${memory.keywords.join(', ') || ''}
👁️ : ${memory.retrieval_count}
🔗 connection: ${memory.links?.length || 0}
  `.trim();

  return {
  id: memory.id,
  label,
  title,
  shape,
  size,
  color: colors,
  font: {
  size: 12,
  color: palette.textPrimary,
  face: palette.fontSans,
  strokeWidth: 3,
  strokeColor: palette.card,
  },
  borderWidth: 2,
  shadow: {
  enabled: true,
  color: palette.shadow,
  size: 10,
  x: 2,
  y: 2,
  },
  memoryData: memory,
  };
  });

  const edges: any[] = [];
  const edgeSet = new Set<string>(); // 

  filteredMemories.forEach(memory => {
  if (memory.links && memory.links.length > 0) {
  memory.links.forEach(link => {
  const targetExists = filteredMemories.some(m => m.id === link.memory_id);
  if (targetExists && link.similarity >= minSimilarity) {
  const edgeId = [memory.id, link.memory_id].sort().join('-');
  if (!edgeSet.has(edgeId)) {
  edgeSet.add(edgeId);

  const edgeStyle = getEdgeStyle(link.similarity, palette);

  edges.push({
  from: memory.id,
  to: link.memory_id,
  ...edgeStyle,
  smooth: {
  type: 'continuous',
  roundness: 0.5,
  },
  label: `${(link.similarity * 100).toFixed(0)}%`,
  font: {
  size: 12,
  color: palette.textSecondary,
  face: palette.fontSans,
  strokeWidth: 0,
  },
  labelHighlightBold: false,
  arrows: {
  to: {
  enabled: false,
  },
  },
  });
  }
  }
  });
  }
  });

  return {
  nodes: new DataSet<GraphNode>(nodes),
  edges: new DataSet<Record<string, any>>(edges),
  };
  } catch (err: any) {
  console.error('❌ Failed to build network data:', err);
  setError(err.message || 'failed');
  message.error(`failed: ${err.message || 'error'}`);
  return {
  nodes: new DataSet<GraphNode>([]),
  edges: new DataSet<Record<string, any>>([]),
  };
  } finally {
  setLoading(false);
  }
  }, [getFilteredMemories, searchText, minSimilarity, showLabels, palette]);

  const getLayoutOptions = () => {
  switch (layout) {
  case 'hierarchical':
  return {
  hierarchical: {
  direction: 'UD',
  sortMethod: 'directed',
  nodeSpacing: 150,
  levelSeparation: 200,
  },
  };
  case 'circular':
  return {
  hierarchical: false,
  };
  case 'force':
  default:
  return {
  hierarchical: false,
  };
  }
  };

  useEffect(() => {
  if (networkRef.current && memories.length > 0) {
  console.log('🎨 Building memory graph with', memories.length, 'memories');

  const data = buildNetworkData();

  const options: any = {
  layout: getLayoutOptions(),
  nodes: {
  borderWidth: 2,
  shadow: true,
  font: {
  size: 12,
  color: palette.textPrimary,
  face: palette.fontSans,
  },
  chosen: {
  node: (values: any) => {
  values.borderWidth = 4;
  values.shadow = true;
  values.shadowSize = 15;
  },
  },
  },
  edges: {
  smooth: {
  type: 'continuous',
  roundness: 0.5,
  },
  chosen: {
  edge: (values: any) => {
  values.width = values.width * 1.5;
  },
  },
  },
  physics: {
  enabled: layout === 'force',
  stabilization: {
  enabled: true,
  iterations: 200,
  },
  barnesHut: {
  gravitationalConstant: -9000,
  centralGravity: 0.3,
  springLength: 150,
  springConstant: 0.04,
  damping: 0.09,
  avoidOverlap: 0.5,
  },
  },
  interaction: {
  hover: true,
  selectConnectedEdges: true,
  hoverConnectedEdges: true,
  tooltipDelay: 300,
  zoomView: true,
  dragView: true,
  dragNodes: true,
  },
  };

  if (networkInstance.current) {
  networkInstance.current.destroy();
  }

  networkInstance.current = new Network(networkRef.current, data, options);

  networkInstance.current.on('click', (params) => {
  if (params.nodes.length > 0) {
  const nodeId = params.nodes[0];
  const rawNode = data.nodes.get(nodeId);
  const node = Array.isArray(rawNode)
  ? ((rawNode[0] as GraphNode | undefined) ?? null)
  : ((rawNode as GraphNode | undefined) ?? null);
  const memory = node?.memoryData;

  if (memory && onNodeClick) {
  onNodeClick(memory);
  }
  }
  });

  setTimeout(() => {
  networkInstance.current?.fit({
  animation: {
  duration: 1000,
  easingFunction: 'easeInOutQuad',
  },
  });
  }, 500);
  }

  return () => {
  if (networkInstance.current) {
  networkInstance.current.destroy();
  networkInstance.current = null;
  }
  };
  }, [memories, layout, searchText, minSimilarity, showLabels, onNodeClick, palette]);

  const handleRefresh = () => {
  if (networkInstance.current) {
  const data = buildNetworkData();
  networkInstance.current.setData(data);
  networkInstance.current.fit();
  }
  };

  const handleFitView = () => {
  networkInstance.current?.fit({
  animation: {
  duration: 1000,
  easingFunction: 'easeInOutQuad',
  },
  });
  };

  const handleFullscreen = () => {
  if (networkRef.current) {
  if (document.fullscreenElement) {
  document.exitFullscreen();
  } else {
  networkRef.current.requestFullscreen();
  }
  }
  };

  const Legend = () => (
  <div
  style={{
  position: 'absolute',
  top: 10,
  left: 10,
  background: 'var(--surface-raised)',
  padding: '12px',
  borderRadius: '8px',
  border: '1px solid var(--border-default)',
  fontSize: '12px',
  zIndex: 1000,
  maxWidth: '250px',
  }}
  >
  <div style={{ fontWeight: 'bold', marginBottom: '8px', color: 'var(--color-action)' }}>
  🗺️ Memory graph legend
  </div>

  <div style={{ marginBottom: '8px' }}>
  <div style={{ fontWeight: 'bold', fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '4px' }}>
  Node size = importance + retrievals
  </div>
  </div>

  <div style={{ marginBottom: '8px' }}>
  <div style={{ fontWeight: 'bold', fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '4px' }}>
  Importance colour:
  </div>
  <div style={{ marginLeft: '8px' }}>
  <div style={{ marginBottom: '2px' }}>
  <span style={{ color: 'var(--status-error-fg)' }}>●</span> critical
  </div>
  <div style={{ marginBottom: '2px' }}>
  <span style={{ color: 'var(--status-warning-fg)' }}>●</span> high
  </div>
  <div style={{ marginBottom: '2px' }}>
  <span style={{ color: 'var(--color-action)' }}>●</span> medium
  </div>
  <div style={{ marginBottom: '2px' }}>
  <span style={{ color: 'var(--status-success-fg)' }}>●</span> low
  </div>
  <div>
  <span style={{ color: 'var(--text-tertiary)' }}>●</span> temporary
  </div>
  </div>
  </div>

  <div style={{ marginBottom: '8px' }}>
  <div style={{ fontWeight: 'bold', fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '4px' }}>
  Type shape:
  </div>
  <div style={{ marginLeft: '8px' }}>
  <div>■ conversation | ● experience | ◆ knowledge | ★ context</div>
  </div>
  </div>

  <div>
  <div style={{ fontWeight: 'bold', fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '4px' }}>
  Connection line:
  </div>
  <div style={{ marginLeft: '8px' }}>
  <div style={{ marginBottom: '2px' }}>
  <span aria-hidden style={{ display: 'inline-block', width: 20, height: 3, borderRadius: 2, verticalAlign: 'middle', marginRight: 6, background: 'var(--status-success-fg)' }} /> high similarity (&gt;80%)
  </div>
  <div style={{ marginBottom: '2px' }}>
  <span aria-hidden style={{ display: 'inline-block', width: 20, height: 3, borderRadius: 2, verticalAlign: 'middle', marginRight: 6, background: 'var(--color-action)' }} /> medium similarity (60-80%)
  </div>
  <div>
  <span aria-hidden style={{ display: 'inline-block', width: 20, height: 3, borderRadius: 2, verticalAlign: 'middle', marginRight: 6, background: 'var(--border-strong)' }} /> low similarity (&lt;60%)
  </div>
  </div>
  </div>

  <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginTop: '8px', borderTop: '1px solid var(--border-subtle)', paddingTop: '6px' }}>
  💡 Click a node for details<br />
  🔍 Drag to move nodes<br />
  🖱️ Scroll to zoom
  </div>
  </div>
  );

  const filteredMemories = getFilteredMemories();
  const memoryCount = filteredMemories.length;

  const totalConnections = filteredMemories.reduce((sum, m) => sum + (m.links?.length || 0), 0);
  const avgConnections = memoryCount > 0 ? (totalConnections / memoryCount).toFixed(1) : 0;

  return (
  <Card
  title={
  <Space>
  <span>🗺️ Memory graph</span>
  <Badge count={memoryCount} style={{ backgroundColor: 'var(--status-success-bg)', color: 'var(--status-success-fg)', boxShadow: '0 0 0 1px var(--status-success-border)' }} />
  <span style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>
  (avg {avgConnections} links)
  </span>
  </Space>
  }
  style={{ height: '100%', position: 'relative' }}
  extra={
  <Space wrap>
  <Input.Search
  placeholder="Search"
  style={{ width: 200 }}
  value={searchText}
  onChange={(e) => setSearchText(e.target.value)}
  allowClear
  prefix={<SearchOutlined />}
  size="small"
  />

  <Tooltip title="Similarity threshold">
  <div style={{ width: 150, display: 'inline-block' }}>
  <Slider
  min={0}
  max={1}
  step={0.1}
  value={minSimilarity}
  onChange={setMinSimilarity}
  marks={{ 0: '0%', 0.5: '50%', 1: '100%' }}
  tooltip={{ formatter: (v) => `${(v! * 100).toFixed(0)}%` }}
  />
  </div>
  </Tooltip>

  <Radio.Group
  size="small"
  value={layout}
  onChange={(e) => setLayout(e.target.value)}
  options={[
  { label: 'Force', value: 'force' },
  { label: 'Hierarchy', value: 'hierarchical' },
  { label: 'Circular', value: 'circular' },
  ]}
  />

  <Button
  size="small"
  type={showLabels ? 'primary' : 'default'}
  onClick={() => setShowLabels(!showLabels)}
  >
  {showLabels ? 'Hide labels' : 'Show labels'}
  </Button>

  <Button size="small" icon={<ExpandOutlined />} onClick={handleFitView} title="Fit to view" aria-label="Fit to view" />
  <Button size="small" icon={<FullscreenOutlined />} onClick={handleFullscreen} title="Fullscreen" aria-label="Fullscreen" />
  <Button size="small" icon={<ReloadOutlined />} onClick={handleRefresh} loading={loading}>
  refresh
  </Button>
  </Space>
  }
  >
  <Spin spinning={loading} tip="Loading memory graph...">
  {}
  {error && (
  <Alert
  message="Load failed"
  description={error}
  type="error"
  showIcon
  closable
  onClose={() => setError(null)}
  style={{ marginBottom: 16 }}
  />
  )}

  {}
  {memoryCount === 0 && !loading ? (
  <div style={{ height, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
  <Empty
  image={<InboxOutlined style={{ fontSize: 64, color: 'var(--border-strong)' }} />}
  imageStyle={{ height: 80 }}
  description={
  <span style={{ color: 'var(--text-tertiary)' }}>
  No memories yet
  <br />
  <span style={{ fontSize: '12px' }}>
  Save memory entries to visualize the graph.
  </span>
  </span>
  }
  >
  <Button type="primary" icon={<ReloadOutlined />} onClick={handleRefresh}>
  Refresh
  </Button>
  </Empty>
  </div>
  ) : (
  <div style={{ position: 'relative' }}>
  <div
  ref={networkRef}
  style={{
  height,
  width: '100%',
  border: '1px solid var(--border-default)',
  borderRadius: 'var(--radius-md)',
  backgroundColor: 'var(--surface-sunken)',
  }}
  />
  <Legend />
  </div>
  )}
  </Spin>
  </Card>
  );
};

export default MemoryGraph;
