/**
 * DAG component - 
 * : , , , 
 */

import React, { useRef, useState, useEffect, useCallback, useMemo } from 'react';
import ForceGraph2D, { ForceGraphMethods } from 'react-force-graph-2d';
import { message, Tag, Drawer, Tabs, Descriptions, Empty, Typography, Collapse } from 'antd';
import {
  SearchOutlined,
  FileTextOutlined,
  DatabaseOutlined,
  HistoryOutlined,
  BranchesOutlined,
} from '@ant-design/icons';

const { Text, Paragraph } = Typography;
const { Panel } = Collapse;
import { planTreeApi } from '@api/planTree';
import { planTreeToTasks } from '@utils/planTree';
import { useChatStore } from '@store/chat';
import type { Task as TaskType } from '@/types';
import {
  getTaskTypeColors,
  getStatusColor,
  truncateText,
  getTaskTypeName,
  getStatusName,
  TASK_TYPE_COLORS,
} from './dag3d-utils';
import '@/styles/dag-fullscreen.css';
import type { GraphNode, GraphLink } from './dag-constants';
import { CARD_WIDTH, CARD_HEIGHT, CARD_RADIUS, getCardColors } from './dag-constants';
import { calculateHierarchicalLayout } from './dag-layout';
import { drawNote, drawLink } from './dag-canvas-renderers';
import { DAGToolbar } from './DAGToolbar';
import { DAGLegend } from './DAGLegend';
import { DAGStatsBar } from './DAGStatsBar';

interface DAGViewProps {
  onClose?: () => void;
  onNodeSelect?: (task: TaskType | null) => void;
}

const DAG3DView: React.FC<DAGViewProps> = ({ onClose, onNodeSelect }) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const graphRef = useRef<ForceGraphMethods | undefined>();
  
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [tasks, setTasks] = useState<TaskType[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchText, setSearchText] = useState('');
  const [selectedNodeId, setSelectedNodeId] = useState<number | null>(null);
  const [hoveredNodeId, setHoveredNodeId] = useState<number | null>(null);
  const [stats, setStats] = useState({ total: 0, pending: 0, running: 0, completed: 0, failed: 0 });
  const [dimensions, setDimensions] = useState({ width: 800, height: 600 });

  const currentPlanId = useChatStore((state) => state.currentPlanId);

  const enterFullscreen = useCallback(async () => {
  try {
  if (containerRef.current && !document.fullscreenElement) {
  await containerRef.current.requestFullscreen();
  }
  } catch (err) {
  console.error('failed:', err);
  message.warning('support');
  }
  }, []);

  const exitFullscreen = useCallback(async () => {
  try {
  if (document.fullscreenElement) {
  await document.exitFullscreen();
  }
  } catch (err) {
  console.error('failed:', err);
  }
  onClose?.();
  }, [onClose]);

  useEffect(() => {
  const handleFullscreenChange = () => {
  const isNowFullscreen = !!document.fullscreenElement;
  setIsFullscreen(isNowFullscreen);
  if (!isNowFullscreen) {
  onClose?.();
  }
  };

  document.addEventListener('fullscreenchange', handleFullscreenChange);
  return () => document.removeEventListener('fullscreenchange', handleFullscreenChange);
  }, [onClose]);

  useEffect(() => {
  enterFullscreen();
  }, [enterFullscreen]);

  useEffect(() => {
  const updateDimensions = () => {
  if (containerRef.current) {
  setDimensions({
  width: containerRef.current.clientWidth || window.innerWidth,
  height: containerRef.current.clientHeight || window.innerHeight,
  });
  }
  };

  updateDimensions();
  window.addEventListener('resize', updateDimensions);
  const timer = setTimeout(updateDimensions, 100);
  
  return () => {
  window.removeEventListener('resize', updateDimensions);
  clearTimeout(timer);
  };
  }, [isFullscreen]);

  const loadTasks = useCallback(async () => {
  if (!currentPlanId) {
  setTasks([]);
  setStats({ total: 0, pending: 0, running: 0, completed: 0, failed: 0 });
  setLoading(false);
  return;
  }

  try {
  setLoading(true);
  const tree = await planTreeApi.getPlanTree(currentPlanId);
  const allTasks = planTreeToTasks(tree);
  setTasks(allTasks);

  setStats({
  total: allTasks.length,
  pending: allTasks.filter(t => t.status === 'pending').length,
  running: allTasks.filter(t => t.status === 'running').length,
  completed: allTasks.filter(t => t.status === 'completed').length,
  failed: allTasks.filter(t => t.status === 'failed' || t.status === 'blocked').length,
  });
  } catch (error: any) {
  console.error('Failed to load tasks:', error);
  message.error(`Failed to load tasks: ${error.message}`);
  } finally {
  setLoading(false);
  }
  }, [currentPlanId]);

  useEffect(() => {
  loadTasks();
  }, [loadTasks]);

  const highlightedNodes = useMemo(() => {
  const nodeId = selectedNodeId ?? hoveredNodeId;
  if (!nodeId || !tasks.length) return new Set<number>();

  const highlighted = new Set<number>([nodeId]);
  
  const findAncestors = (id: number) => {
  const task = tasks.find(t => t.id === id);
  if (task?.parent_id) {
  highlighted.add(task.parent_id);
  findAncestors(task.parent_id);
  }
  };
  findAncestors(nodeId);

  tasks.forEach(task => {
  if (task.parent_id === nodeId) {
  highlighted.add(task.id);
  }
  });

  return highlighted;
  }, [selectedNodeId, hoveredNodeId, tasks]);

  const getRotation = useCallback((id: number) => {
  const seed = id * 9301 + 49297;
  return ((seed % 233280) / 233280 - 0.5) * 6; // -3°  3°
  }, []);

  const graphData = useMemo(() => {
  let filteredTasks = tasks;
  
  if (searchText) {
  filteredTasks = filteredTasks.filter(task =>
  task.name.toLowerCase().includes(searchText.toLowerCase())
  );
  }

  const positions = calculateHierarchicalLayout(filteredTasks);

  const nodes: GraphNode[] = filteredTasks.map(task => {
  const pos = positions.get(task.id) || { x: 0, y: 0 };
  const colors = getCardColors(task.task_type);
  return {
  id: task.id,
  name: truncateText(task.name, 24),
  task,
  x: pos.x,
  y: pos.y,
  fx: pos.x,
  fy: pos.y,
  rotation: 0, // 
  noteColor: colors.bg,
  };
  });

  const links: GraphLink[] = [];
  const taskIdSet = new Set(filteredTasks.map(t => t.id));

  filteredTasks.forEach(task => {
  if (task.parent_id != null && taskIdSet.has(task.parent_id)) {
  links.push({
  source: task.parent_id,
  target: task.id,
  type: 'parent',
  });
  }

  if (task.dependencies && task.dependencies.length > 0) {
  task.dependencies.forEach(depId => {
  if (taskIdSet.has(depId) && depId !== task.parent_id) {
  links.push({
  source: depId,
  target: task.id,
  type: 'dependency',
  });
  }
  });
  }
  });

  return { nodes, links };
  }, [tasks, searchText, getRotation, getCardColors]);

  const handleNodeClick = useCallback((node: GraphNode) => {
  if (selectedNodeId === node.id) {
  setSelectedNodeId(null);
  onNodeSelect?.(null);
  } else {
  setSelectedNodeId(node.id);
  onNodeSelect?.(node.task);
  }
  }, [selectedNodeId, onNodeSelect]);

  const handleNodeHover = useCallback((node: GraphNode | null) => {
  setHoveredNodeId(node?.id ?? null);
  if (containerRef.current) {
  containerRef.current.style.cursor = node ? 'pointer' : 'default';
  }
  }, []);

  const handleBackgroundClick = useCallback(() => {
  setSelectedNodeId(null);
  onNodeSelect?.(null);
  }, [onNodeSelect]);

  const handleFitView = () => graphRef.current?.zoomToFit(400, 60);
  const handleRefresh = () => loadTasks();
  const handleResetView = () => {
  graphRef.current?.centerAt(0, 0, 800);
  graphRef.current?.zoom(1, 800);
  };

  const handleExport = () => {
  const canvas = containerRef.current?.querySelector('canvas');
  if (canvas) {
  const link = document.createElement('a');
  link.download = `dag-sticky-${currentPlanId || 'unknown'}.png`;
  link.href = canvas.toDataURL('image/png');
  link.click();
  message.success('DAG image exported');
  }
  };

  const selectedTask = useMemo(() => {
  if (!selectedNodeId) return null;
  return tasks.find(t => t.id === selectedNodeId) || null;
  }, [selectedNodeId, tasks]);

  const closeDrawer = useCallback(() => {
  setSelectedNodeId(null);
  onNodeSelect?.(null);
  }, [onNodeSelect]);

  const formatJson = (data: any) => {
  if (!data) return null;
  try {
  return JSON.stringify(data, null, 2);
  } catch {
  return String(data);
  }
  };

  const renderDrawerContent = () => {
  if (!selectedTask) return null;

  const tabItems = [
  {
  key: 'basic',
  label: <span><FileTextOutlined /> </span>,
  children: (
  <div className="task-detail-section">
  <Descriptions column={1} size="small" bordered>
  <Descriptions.Item label="Task ID">{selectedTask.id}</Descriptions.Item>
  <Descriptions.Item label="name">{selectedTask.name}</Descriptions.Item>
  <Descriptions.Item label="type">
  <Tag color={getTaskTypeColors(selectedTask.task_type).primary}>
  {getTaskTypeName(selectedTask.task_type)}
  </Tag>
  </Descriptions.Item>
  <Descriptions.Item label="status">
  <Tag color={getStatusColor(selectedTask.status)}>
  {getStatusName(selectedTask.status)}
  </Tag>
  </Descriptions.Item>
  <Descriptions.Item label="depth">{selectedTask.depth ?? '-'}</Descriptions.Item>
  <Descriptions.Item label="path">{selectedTask.path || '-'}</Descriptions.Item>
  <Descriptions.Item label="Parent Task ID">{selectedTask.parent_id ?? ''}</Descriptions.Item>
  {selectedTask.dependencies && selectedTask.dependencies.length > 0 && (
  <Descriptions.Item label="dependencies">
  {selectedTask.dependencies.map(d => (
  <Tag key={d} style={{ marginRight: 4 }}>{d}</Tag>
  ))}
  </Descriptions.Item>
  )}
  </Descriptions>

  {selectedTask.instruction && (
  <div style={{ marginTop: 16 }}>
  <Text strong style={{ display: 'block', marginBottom: 8 }}>Instruction</Text>
  <div className="task-instruction-box">
  <Paragraph 
  style={{ margin: 0, whiteSpace: 'pre-wrap' }}
  ellipsis={{ rows: 6, expandable: true, symbol: 'expand' }}
  >
  {selectedTask.instruction}
  </Paragraph>
  </div>
  </div>
  )}
  </div>
  ),
  },
  {
  key: 'context',
  label: <span><BranchesOutlined /> </span>,
  children: (
  <div className="task-detail-section">
  {selectedTask.context_combined ? (
  <>
  <div className="task-context-box">
  <Paragraph 
  style={{ margin: 0, whiteSpace: 'pre-wrap', fontSize: 12 }}
  ellipsis={{ rows: 10, expandable: true, symbol: 'expand' }}
  >
  {selectedTask.context_combined}
  </Paragraph>
  </div>
  {selectedTask.context_updated_at && (
  <Text type="secondary" style={{ fontSize: 11, marginTop: 8, display: 'block' }}>
  Updated: {new Date(selectedTask.context_updated_at).toLocaleString()}
  </Text>
  )}
  </>
  ) : (
  <Empty description="No context available" image={Empty.PRESENTED_IMAGE_SIMPLE} />
  )}

  {selectedTask.context_sections && selectedTask.context_sections.length > 0 && (
  <div style={{ marginTop: 16 }}>
  <Text strong style={{ display: 'block', marginBottom: 8 }}>Context Sections</Text>
  <Collapse size="small" ghost>
  {selectedTask.context_sections.map((section, idx) => (
  <Panel 
  header={section.name || `Section ${idx + 1}`} 
  key={idx}
  >
  <pre style={{ 
  fontSize: 11, 
  margin: 0, 
  whiteSpace: 'pre-wrap',
  background: '#f5f5f5',
  padding: 8,
  borderRadius: 4,
  }}>
  {formatJson(section)}
  </pre>
  </Panel>
  ))}
  </Collapse>
  </div>
  )}
  </div>
  ),
  },
  {
  key: 'result',
  label: <span><HistoryOutlined /> Execution Result</span>,
  children: (
  <div className="task-detail-section">
  {selectedTask.execution_result ? (
  <div className="task-result-box">
  <Paragraph 
  style={{ margin: 0, whiteSpace: 'pre-wrap', fontSize: 12 }}
  ellipsis={{ rows: 15, expandable: true, symbol: 'expand' }}
  >
  {selectedTask.execution_result}
  </Paragraph>
  </div>
  ) : (
  <Empty description="No execution result available" image={Empty.PRESENTED_IMAGE_SIMPLE} />
  )}
  </div>
  ),
  },
  {
  key: 'metadata',
  label: <span><DatabaseOutlined /> </span>,
  children: (
  <div className="task-detail-section">
  {selectedTask.metadata && Object.keys(selectedTask.metadata).length > 0 ? (
  <pre className="task-metadata-box">
  {formatJson(selectedTask.metadata)}
  </pre>
  ) : (
  <Empty description="No metadata available" image={Empty.PRESENTED_IMAGE_SIMPLE} />
  )}

  <Descriptions 
  column={1} 
  size="small" 
  style={{ marginTop: 16 }}
  title={<Text type="secondary" style={{ fontSize: 12 }}>System</Text>}
  >
  {selectedTask.created_at && (
  <Descriptions.Item label="Created">
  {new Date(selectedTask.created_at).toLocaleString()}
  </Descriptions.Item>
  )}
  {selectedTask.updated_at && (
  <Descriptions.Item label="Updated">
  {new Date(selectedTask.updated_at).toLocaleString()}
  </Descriptions.Item>
  )}
  {selectedTask.session_id && (
  <Descriptions.Item label="Session ID">
  <Text copyable style={{ fontSize: 11 }}>{selectedTask.session_id}</Text>
  </Descriptions.Item>
  )}
  {selectedTask.workflow_id && (
  <Descriptions.Item label="Workflow ID">
  <Text copyable style={{ fontSize: 11 }}>{selectedTask.workflow_id}</Text>
  </Descriptions.Item>
  )}
  </Descriptions>
  </div>
  ),
  },
  ];

  return <Tabs items={tabItems} size="small" />;
  };

  return (
  <div ref={containerRef} className="dag-fullscreen-container dag-sticky-container">
  {}
  <ForceGraph2D
  ref={graphRef}
  width={dimensions.width}
  height={dimensions.height}
  graphData={graphData}
  nodeId="id"
  nodeCanvasObject={(node, ctx, globalScale) => {
  const gNode = node as GraphNode;
  const isSelected = selectedNodeId === gNode.id;
  const isHovered = hoveredNodeId === gNode.id;
  const isHighlighted = highlightedNodes.size === 0 || highlightedNodes.has(gNode.id);
  drawNote(ctx, gNode, isSelected, isHovered, isHighlighted);
  }}
  nodePointerAreaPaint={(node, color, ctx) => {
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.roundRect(
  (node.x || 0) - CARD_WIDTH / 2,
  (node.y || 0) - CARD_HEIGHT / 2,
  CARD_WIDTH,
  CARD_HEIGHT,
  CARD_RADIUS
  );
  ctx.fill();
  }}
  linkCanvasObject={(link, ctx) => {
  const sourceId = (link.source as any).id;
  const targetId = (link.target as any).id;
  const linkType = (link as any).type || 'parent';
  const activeNodeId = selectedNodeId ?? hoveredNodeId;
  
  if (linkType === 'dependency') {
  if (!activeNodeId || (sourceId !== activeNodeId && targetId !== activeNodeId)) {
  return; // related
  }
  }
  
  const isHighlighted = highlightedNodes.size === 0 || 
  (highlightedNodes.has(sourceId) && highlightedNodes.has(targetId));
  drawLink(ctx, link, isHighlighted);
  }}
  linkDirectionalParticles={0}
  backgroundColor="transparent"
  onNodeClick={handleNodeClick}
  onNodeHover={handleNodeHover}
  onBackgroundClick={handleBackgroundClick}
  enableNodeDrag={false}
  cooldownTicks={0}
  d3AlphaDecay={1}
  d3VelocityDecay={1}
  nodeRelSize={1}
  />

  {}
  <DAGToolbar
  onFitView={handleFitView}
  onResetView={handleResetView}
  onRefresh={handleRefresh}
  onExport={handleExport}
  onExitFullscreen={exitFullscreen}
  />

  {}
  <div className="dag-search-bar">
  <SearchOutlined className="dag-search-icon" />
  <input
  className="dag-search-input"
  type="text"
  placeholder="Search tasks..."
  value={searchText}
  onChange={(e) => setSearchText(e.target.value)}
  />
  </div>

  {}
  <DAGLegend />

  {}
  <DAGStatsBar stats={stats} />

  {}
  <Drawer
  title={
  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
  <Tag color={getStatusColor(selectedTask?.status)}>
  {getStatusName(selectedTask?.status)}
  </Tag>
  <span style={{ 
  flex: 1, 
  overflow: 'hidden', 
  textOverflow: 'ellipsis', 
  whiteSpace: 'nowrap' 
  }}>
  {selectedTask?.name || 'Task'}
  </span>
  </div>
  }
  placement="right"
  width={420}
  open={!!selectedTask}
  onClose={closeDrawer}
  mask={false}
  getContainer={containerRef.current || false}
  rootClassName="dag-task-drawer"
  styles={{
  wrapper: {
  position: 'absolute',
  },
  header: { 
  borderBottom: '1px solid #f0f0f0',
  padding: '12px 16px',
  },
  body: { 
  padding: '12px 16px',
  background: '#fafafa',
  },
  }}
  >
  {renderDrawerContent()}
  </Drawer>

  {}
  <div className="dag-fullscreen-hint">
  ESC  ·  ·  · 
  </div>
  </div>
  );
};

export default DAG3DView;
