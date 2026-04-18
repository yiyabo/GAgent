import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Network } from 'vis-network';
import { DataSet } from 'vis-data';
import { Card, Spin, Button, Space, Select, Input, message, Badge } from 'antd';
import { ReloadOutlined, ExpandOutlined, FullscreenOutlined } from '@ant-design/icons';
import { planTreeApi } from '@api/planTree';
import { planTreeToTasks } from '@utils/planTree';
import type { PlanSyncEventDetail, Task as TaskType } from '@/types';
import { useChatStore } from '@store/chat';
import { useTasksStore } from '@store/tasks';
import { shouldHandlePlanSyncEvent } from '@utils/planSyncEvents';

interface DAGVisualizationProps {
  onNodeClick?: (taskId: number, taskData: any) => void;
  onNodeDoubleClick?: (taskId: number, taskData: any) => void;
  onFullscreenRequest?: () => void;
  height?: string;
  interactive?: boolean;
  showToolbar?: boolean;
  showFullscreenButton?: boolean;
}

const DAGVisualization: React.FC<DAGVisualizationProps> = ({
  onNodeClick,
  onNodeDoubleClick,
  onFullscreenRequest,
  showFullscreenButton = true,
}) => {
  const networkRef = useRef<HTMLDivElement>(null);
  const networkInstance = useRef<Network | null>(null);
  const nodesDataset = useRef<DataSet<any> | null>(null);
  const edgesDataset = useRef<DataSet<any> | null>(null);
  const [tasks, setTasks] = useState<TaskType[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchText, setSearchText] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [stats, setStats] = useState<any>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<number | null>(null);
  const currentPlanId = useChatStore((state) => state.currentPlanId);
  const { setTasks: updateStoreTasks, setTaskStats } = useTasksStore((state) => ({
  setTasks: state.setTasks,
  setTaskStats: state.setTaskStats,
  }));

  const getStatusColor = (status: string) => {
  switch (status) {
  case 'completed':
  case 'done':
  return '#22c55e'; //  - success-color
  case 'running':
  case 'executing':
  return '#3b82f6'; //  - info-color
  case 'pending':
  return '#f59e0b'; //  - warning-color
  case 'failed':
  case 'error':
  return '#ef4444'; //  - error-color
  case 'blocked':
  return '#f97316';
  default:
  return 'var(--text-tertiary)';
  }
  };

  const getNodeShape = (taskType?: string) => {
  if (!taskType) return 'dot';
  
  switch (taskType.toUpperCase()) {
  case 'ROOT':
  return 'star';  // ROOT node
  case 'COMPOSITE':
  return 'box';  // COMPOSITE node
  case 'ATOMIC':
  return 'ellipse';  // ATOMIC node
  default:
  return 'dot';
  }
  };

  const getNodeSize = (taskType?: string, hasChildren: boolean = false) => {
  if (!taskType) return 15;
  
  switch (taskType.toUpperCase()) {
  case 'ROOT':
  return 50;  // ROOT nodes are emphasized
  case 'COMPOSITE':
  return hasChildren ? 35 : 30;  // COMPOSITE nodes
  case 'ATOMIC':
  return 25;  // ATOMIC nodes
  default:
  return 15;
  }
  };

  const getFontSize = (taskType?: string) => {
  if (!taskType) return 12;
  
  switch (taskType.toUpperCase()) {
  case 'ROOT':
  return 16;  // ROOT
  case 'COMPOSITE':
  return 13;  // COMPOSITE
  case 'ATOMIC':
  return 11;  // ATOMIC
  default:
  return 12;
  }
  };

  const highlightConnected = useCallback((nodeId: number | null) => {
  if (!nodesDataset.current || !edgesDataset.current) return;

  const allNodes = nodesDataset.current.get();
  const allEdges = edgesDataset.current.get();

  if (nodeId === null) {
  nodesDataset.current.update(
  allNodes.map((node: any) => ({
  id: node.id,
  opacity: 1,
  borderWidth: node.taskData?.task_type?.toUpperCase() === 'ROOT' ? 3 : 2,
  font: { ...node.font, color: '#ffffff' },
  }))
  );
  edgesDataset.current.update(
  allEdges.map((edge: any) => ({
  id: edge.id,
  hidden: false,
  width: edge.originalWidth || 2,
  color: { ...edge.color, opacity: 1 },
  }))
  );
  setSelectedNodeId(null);
  return;
  }

  const connectedNodes = new Set<number>([nodeId]);
  const connectedEdges = new Set<string>();

  allEdges.forEach((edge: any) => {
  if (edge.from === nodeId || edge.to === nodeId) {
  connectedNodes.add(edge.from);
  connectedNodes.add(edge.to);
  connectedEdges.add(edge.id);
  }
  });

  const findAncestors = (id: number) => {
  allEdges.forEach((edge: any) => {
  if (edge.to === id && !connectedNodes.has(edge.from)) {
  connectedNodes.add(edge.from);
  connectedEdges.add(edge.id);
  findAncestors(edge.from);
  }
  });
  };
  findAncestors(nodeId);

  nodesDataset.current.update(
  allNodes.map((node: any) => ({
  id: node.id,
  opacity: connectedNodes.has(node.id) ? 1 : 0.2,
  borderWidth: node.id === nodeId ? 5 : (connectedNodes.has(node.id) ? 3 : 2),
  font: {
  ...node.font,
  color: connectedNodes.has(node.id) ? '#ffffff' : 'rgba(255,255,255,0.3)',
  },
  }))
  );

  edgesDataset.current.update(
  allEdges.map((edge: any) => ({
  id: edge.id,
  hidden: !connectedEdges.has(edge.id),
  width: connectedEdges.has(edge.id) ? (edge.originalWidth || 2) * 1.5 : 1,
  color: {
  ...edge.color,
  opacity: connectedEdges.has(edge.id) ? 1 : 0.1,
  },
  }))
  );

  setSelectedNodeId(nodeId);
  }, []);

  const loadTasks = useCallback(async () => {
  try {
  setLoading(true);
  console.log('🔄 Loading tasks for DAG visualization...');

  if (!currentPlanId) {
  console.warn('No plan selected; skipping task load');
  setTasks([]);
  setStats(null);
  updateStoreTasks([]);
  setTaskStats(null);
  return;
  }

  const tree = await planTreeApi.getPlanTree(currentPlanId);
  const allTasks = planTreeToTasks(tree);
  console.log('📊 PlanTree → tasks:', allTasks.length);

  setTasks(allTasks);
  updateStoreTasks(allTasks);

  const normalizedStats = {
  total: allTasks.length,
  pending: allTasks.filter((task) => task.status === 'pending').length,
  running: allTasks.filter((task) => task.status === 'running').length,
  completed: allTasks.filter((task) => task.status === 'completed').length,
  failed: allTasks.filter((task) => task.status === 'failed' || task.status === 'blocked').length,
  };
  setStats(normalizedStats);
  setTaskStats(normalizedStats);
  } catch (error: any) {
  console.error('❌ Failed to load tasks:', error);
  message.error(`Failed to load tasks: ${error.message}`);
  } finally {
  setLoading(false);
  }
  }, [currentPlanId, setTaskStats, updateStoreTasks]);

  const buildNetworkData = () => {
  let filteredTasks = tasks;

  if (searchText) {
  filteredTasks = filteredTasks.filter(task =>
  task.name.toLowerCase().includes(searchText.toLowerCase())
  );
  }

  if (statusFilter !== 'all') {
  filteredTasks = filteredTasks.filter(task => task.status === statusFilter);
  }

  const nodes = filteredTasks.map(task => {
  const hasChildren = filteredTasks.some(t => t.parent_id === task.id);
  
  let displayName = task.name;
  if (task.name.length > 50) {
  const cleanName = task.name.replace(/^(ROOT|COMPOSITE|ATOMIC):\s*/i, '');
  displayName = cleanName.length > 45 
  ? cleanName.substring(0, 45) + '...' 
  : cleanName;
  }
  
  const getBorderColor = (taskType?: string) => {
  if (!taskType) return 'var(--text-tertiary)';
  
  switch (taskType.toUpperCase()) {
  case 'ROOT': return 'var(--primary-color)';  //  - ROOT
  case 'COMPOSITE': return '#3b82f6'; //  - COMPOSITE
  case 'ATOMIC': return '#22c55e';  //  - ATOMIC
  default: return 'var(--text-tertiary)';
  }
  };
  
  return {
  id: task.id,
  label: displayName,
  title: `Task Details\n━━━━━━━━━━━━━━━━\nID: ${task.id}\nName: ${task.name}\nStatus: ${task.status}\nType: ${task.task_type}\nDepth: ${task.depth}\n${hasChildren ? 'Has child tasks' : ''}`,
  color: {
  background: getStatusColor(task.status),
  border: getBorderColor(task.task_type),
  },
  shape: getNodeShape(task.task_type),
  size: getNodeSize(task.task_type, hasChildren),
  level: task.depth,
  font: { 
  size: getFontSize(task.task_type), 
  color: '#ffffff',
  face: 'Arial',
  strokeWidth: 2,
  strokeColor: '#000000'
  },
  borderWidth: task.task_type?.toUpperCase() === 'ROOT' ? 3 : 2,
  shadow: {
  enabled: true,
  color: 'rgba(0,0,0,0.3)',
  size: task.task_type?.toUpperCase() === 'ROOT' ? 15 : 10,
  x: 2,
  y: 2
  },
  taskData: task,
  };
  });

  const edges: any[] = [];
  filteredTasks.forEach(task => {
  if (task.parent_id) {
  const parentTask = filteredTasks.find(t => t.id === task.parent_id);
  if (parentTask) {
  const getEdgeStyle = (fromType: string, toType: string) => {
  if (fromType.toUpperCase() === 'ROOT' && toType.toUpperCase() === 'COMPOSITE') {
  return {
  color: { color: 'var(--primary-color)', highlight: 'var(--primary-color)', hover: 'var(--primary-color)' },
  width: 4,
  dashes: false,
  shadow: { enabled: true, color: 'rgba(201, 100, 66, 0.3)', size: 8 }
  };
  } else if (fromType.toUpperCase() === 'COMPOSITE' && toType.toUpperCase() === 'ATOMIC') {
  return {
  color: { color: '#3b82f6', highlight: '#3b82f6', hover: '#3b82f6' },
  width: 3,
  dashes: false,
  shadow: { enabled: true, color: 'rgba(59, 130, 246, 0.3)', size: 6 }
  };
  } else {
  return {
  color: { color: '#22c55e', highlight: '#22c55e', hover: '#22c55e' },
  width: 2,
  dashes: false,
  shadow: { enabled: true, color: 'rgba(34, 197, 94, 0.3)', size: 4 }
  };
  }
  };
  
  const edgeStyle = getEdgeStyle(parentTask.task_type, task.task_type);
  
  edges.push({
  from: task.parent_id,
  to: task.id,
  arrows: { 
  to: { 
  enabled: true, 
  scaleFactor: 1.2,
  type: 'arrow'
  } 
  },
  ...edgeStyle,
  smooth: { 
  type: 'cubicBezier',
  forceDirection: 'vertical',
  roundness: 0.4
  },
  label: `${parentTask.task_type} → ${task.task_type}`,
  font: { size: 10, color: 'var(--text-secondary)', strokeWidth: 0 },
  labelHighlightBold: false,
  });
  }
  }
  });

  nodesDataset.current = new DataSet(nodes);
  edgesDataset.current = new DataSet(edges.map(e => ({ ...e, originalWidth: e.width })));

  return {
  nodes: nodesDataset.current,
  edges: edgesDataset.current,
  };
  };

  useEffect(() => {
  if (networkRef.current && tasks.length > 0) {
  console.log('🎨 Building network visualization with', tasks.length, 'tasks');
  
  const data = buildNetworkData();
  
  const options: any = {
  layout: {
  hierarchical: {
  direction: 'UD',  // 
  sortMethod: 'directed',  // 
  nodeSpacing: 150,  // 
  levelSeparation: 180,  // ()
  treeSpacing: 200,  // 
  blockShifting: true,  // 
  edgeMinimization: true,  // 
  parentCentralization: true, // medium
  shakeTowards: 'roots'  // 
  },
  },
  nodes: {
  borderWidth: 2,
  shadow: {
  enabled: true,
  color: 'rgba(0,0,0,0.3)',
  size: 10,
  x: 2,
  y: 2
  },
  chosen: {
  node: (values: any, _id: any, _selected: any, _hovering: any) => {
  values.borderWidth = 4;
  values.shadow = true;
  values.shadowColor = 'rgba(0,0,0,0.5)';
  values.shadowSize = 15;
  }
  }
  },
  edges: {
  width: 2,
  arrows: { to: { enabled: true, scaleFactor: 1.2 } },
  smooth: { 
  type: 'cubicBezier',
  forceDirection: 'vertical',
  roundness: 0.4
  },
  chosen: {
  edge: (values: any, _id: any, _selected: any, _hovering: any) => {
  values.width = 4;
  values.color = '#ff6b6b';
  }
  }
  },
  physics: {
  enabled: false,  // , 
  },
  interaction: {
  hover: true,  // 
  selectConnectedEdges: true, // 
  hoverConnectedEdges: true, // high
  tooltipDelay: 300,  // hint
  zoomView: true,  // 
  dragView: true,  // 
  dragNodes: false,  // ()
  },
  configure: {
  enabled: false
  },
  locale: 'zh',
  };

  if (networkInstance.current) {
  networkInstance.current.destroy();
  }

  networkInstance.current = new Network(networkRef.current, data, options);

  networkInstance.current.on('click', (params) => {
  if (params.nodes.length > 0) {
  const nodeId = params.nodes[0];
  const task = tasks.find(t => t.id === nodeId);
  console.log('🖱️ Node clicked:', nodeId, task);
  
  highlightConnected(nodeId);
  
  if (task && onNodeClick) {
  onNodeClick(nodeId, task);
  }
  } else {
  highlightConnected(null);
  }
  });

  networkInstance.current.on('doubleClick', (params) => {
  if (params.nodes.length > 0) {
  const nodeId = params.nodes[0];
  const task = tasks.find(t => t.id === nodeId);
  console.log('🖱️ Node double-clicked:', nodeId, task);
  
  networkInstance.current?.focus(nodeId, {
  scale: 1.5,
  animation: { duration: 500, easingFunction: 'easeInOutQuad' },
  });
  
  if (task && onNodeDoubleClick) {
  onNodeDoubleClick(nodeId, task);
  }
  }
  });

  setTimeout(() => {
  networkInstance.current?.fit();
  }, 500);
  }

  return () => {
  if (networkInstance.current) {
  networkInstance.current.destroy();
  networkInstance.current = null;
  }
  };
  }, [tasks, searchText, statusFilter, onNodeClick, onNodeDoubleClick, highlightConnected]);

  useEffect(() => {
  loadTasks();
  }, [currentPlanId, loadTasks]);

  useEffect(() => {
  const handleTasksUpdated = (event: CustomEvent<PlanSyncEventDetail>) => {
  const detail = event.detail;
  if (
  detail?.type === 'plan_deleted' &&
  detail.plan_id != null &&
  detail.plan_id === (currentPlanId ?? null)
  ) {
  setTasks([]);
  setStats(null);
  updateStoreTasks([]);
  setTaskStats(null);
  return;
  }
  if (
  !shouldHandlePlanSyncEvent(detail, currentPlanId ?? null, [
  'task_changed',
  'plan_jobs_completed',
  'plan_updated',
  ])
  ) {
  return;
  }
  loadTasks();
  window.setTimeout(() => {
  loadTasks();
  }, 800);
  };

  window.addEventListener('tasksUpdated', handleTasksUpdated as EventListener);

  return () => {
  window.removeEventListener('tasksUpdated', handleTasksUpdated as EventListener);
  };
  }, [currentPlanId, loadTasks, setTaskStats, updateStoreTasks]);

  const handleRefresh = () => {
  loadTasks();
  };

  const handleFitView = () => {
  networkInstance.current?.fit();
  };

  const AgentLegend = () => (
  <div style={{
  position: 'absolute',
  top: 10,
  left: 10,
  background: 'var(--bg-secondary)',
  padding: '12px',
  borderRadius: '8px',
  border: '1px solid var(--border-color)',
  fontSize: '12px',
  zIndex: 1000,
  maxWidth: '200px'
  }}>
  <div style={{ fontWeight: 'bold', marginBottom: '8px', color: 'var(--primary-color)' }}>
  🤖 Agent Workflow
  </div>
  <div style={{ marginBottom: '4px' }}>
  <span style={{ color: 'var(--primary-color)' }}>⭐</span> ROOT - goal-level task
  </div>
  <div style={{ marginBottom: '4px' }}>
  <span style={{ color: '#3b82f6' }}>📦</span> COMPOSITE - decomposable task
  </div>
  <div style={{ marginBottom: '4px' }}>
  <span style={{ color: '#22c55e' }}>⚪</span> ATOMIC - executable subtask
  </div>
  <div style={{ fontSize: '10px', color: 'var(--text-secondary)', marginTop: '6px' }}>
  💡 Highlighted edges show active paths
  </div>
  </div>
  );

  const FullscreenButton = () => (
  showFullscreenButton && onFullscreenRequest ? (
  <Button
  type="text"
  icon={<FullscreenOutlined />}
  onClick={onFullscreenRequest}
  style={{
  position: 'absolute',
  top: 10,
  right: 10,
  zIndex: 1000,
  background: 'var(--bg-secondary)',
  border: '1px solid var(--border-color)',
  }}
  title="Open fullscreen view"
  />
  ) : null
  );

  return (
  <div style={{ height: '100%', position: 'relative', background: 'var(--bg-secondary)' }}>
  <div
  ref={networkRef}
  style={{
  height: '100%',
  width: '100%',
  border: '1px solid var(--border-color)',
  borderRadius: 'var(--radius-md)',
  backgroundColor: 'var(--bg-tertiary)',
  }}
  />
  {}
  <AgentLegend />
  {}
  <FullscreenButton />
  {}
  {selectedNodeId && (
  <div style={{
  position: 'absolute',
  bottom: 10,
  left: '50%',
  transform: 'translateX(-50%)',
  background: 'var(--bg-secondary)',
  padding: '6px 12px',
  borderRadius: '16px',
  border: '1px solid var(--border-color)',
  fontSize: '12px',
  color: 'var(--text-secondary)',
  zIndex: 1000,
  }}>
  cancelhigh
  </div>
  )}
  </div>
  );
};

export default DAGVisualization;
