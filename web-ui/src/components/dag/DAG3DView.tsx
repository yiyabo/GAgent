/**
 * DAG 可视化组件 - 便签纸风格
 * 特点：柔和阴影、略带倾斜、手绘感、轻松氛围
 */

import React, { useRef, useState, useEffect, useCallback, useMemo } from 'react';
import ForceGraph2D, { ForceGraphMethods } from 'react-force-graph-2d';
import { message, Tag, Drawer, Tabs, Descriptions, Empty, Typography, Collapse } from 'antd';
import {
  FullscreenExitOutlined,
  ExpandOutlined,
  ReloadOutlined,
  SearchOutlined,
  DownloadOutlined,
  AimOutlined,
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

interface DAGViewProps {
  onClose?: () => void;
  onNodeSelect?: (task: TaskType | null) => void;
}

interface GraphNode {
  id: number;
  name: string;
  task: TaskType;
  x?: number;
  y?: number;
  fx?: number;  // 固定 x 位置
  fy?: number;  // 固定 y 位置
  rotation: number;  // 便签倾斜角度
  noteColor: string; // 便签颜色
}

interface GraphLink {
  source: number;
  target: number;
  type: 'parent' | 'dependency';  // 父子关系 or 依赖关系
}

// 计算紧凑树形布局
function calculateHierarchicalLayout(tasks: TaskType[]): Map<number, { x: number; y: number }> {
  const positions = new Map<number, { x: number; y: number }>();
  if (!tasks.length) return positions;

  // 紧凑布局参数
  const NODE_WIDTH = 140;
  const HORIZONTAL_GAP = 12;
  const VERTICAL_GAP = 56;

  // 构建父子关系
  const childrenMap = new Map<number, TaskType[]>();
  const taskMap = new Map<number, TaskType>();
  
  tasks.forEach(task => {
    taskMap.set(task.id, task);
    if (task.parent_id != null) {
      if (!childrenMap.has(task.parent_id)) {
        childrenMap.set(task.parent_id, []);
      }
      childrenMap.get(task.parent_id)!.push(task);
    }
  });

  // 对每个父节点的子节点按 position 排序
  childrenMap.forEach(children => {
    children.sort((a, b) => (a.position ?? a.id) - (b.position ?? b.id));
  });

  // 找到根节点
  const rootTasks = tasks.filter(t => t.parent_id == null);
  rootTasks.sort((a, b) => (a.position ?? a.id) - (b.position ?? b.id));

  // 缓存子树宽度
  const subtreeWidthCache = new Map<number, number>();

  // 递归计算子树宽度
  function getSubtreeWidth(taskId: number): number {
    if (subtreeWidthCache.has(taskId)) {
      return subtreeWidthCache.get(taskId)!;
    }
    
    const children = childrenMap.get(taskId) || [];
    let width: number;
    
    if (children.length === 0) {
      width = NODE_WIDTH;
    } else {
      width = 0;
      children.forEach((child, i) => {
        width += getSubtreeWidth(child.id);
        if (i < children.length - 1) {
          width += HORIZONTAL_GAP;
        }
      });
      width = Math.max(NODE_WIDTH, width);
    }
    
    subtreeWidthCache.set(taskId, width);
    return width;
  }

  // 递归布局子树
  function layoutSubtree(taskId: number, centerX: number, y: number) {
    positions.set(taskId, { x: centerX, y });
    
    const children = childrenMap.get(taskId) || [];
    if (children.length === 0) return;

    // 计算子节点的总宽度
    const childWidths = children.map(c => getSubtreeWidth(c.id));
    const totalChildrenWidth = childWidths.reduce((sum, w) => sum + w, 0) 
      + (children.length - 1) * HORIZONTAL_GAP;
    
    // 子节点起始位置（使子节点组居中于父节点下方）
    let childX = centerX - totalChildrenWidth / 2;
    const childY = y + VERTICAL_GAP;

    children.forEach((child, i) => {
      const childWidth = childWidths[i];
      const childCenterX = childX + childWidth / 2;
      layoutSubtree(child.id, childCenterX, childY);
      childX += childWidth + HORIZONTAL_GAP;
    });
  }

  // 布局所有根节点
  if (rootTasks.length === 1) {
    layoutSubtree(rootTasks[0].id, 0, 0);
  } else {
    // 多个根节点的情况
    const rootWidths = rootTasks.map(r => getSubtreeWidth(r.id));
    const totalRootWidth = rootWidths.reduce((sum, w) => sum + w, 0) 
      + (rootTasks.length - 1) * HORIZONTAL_GAP;
    let rootX = -totalRootWidth / 2;
    
    rootTasks.forEach((root, i) => {
      const rootWidth = rootWidths[i];
      const rootCenterX = rootX + rootWidth / 2;
      layoutSubtree(root.id, rootCenterX, 0);
      rootX += rootWidth + HORIZONTAL_GAP;
    });
  }

  return positions;
}

// 索引卡片配色 - 温暖的纸质色系
const CARD_COLORS = {
  ROOT: {
    bg: '#fffef8',        // 象牙白
    line: '#c9a87c',      // 金棕色线
    accent: '#b8860b',    // 暗金色
  },
  COMPOSITE: {
    bg: '#fdfcf9',        // 奶油白
    line: '#8fa3b1',      // 灰蓝色线
    accent: '#5c7a8a',    // 石板蓝
  },
  ATOMIC: {
    bg: '#fffffe',        // 纯净白
    line: '#a8b5a0',      // 灰绿色线
    accent: '#6b7c63',    // 橄榄绿
  },
  DEFAULT: {
    bg: '#fafaf8',
    line: '#c0c0c0',
    accent: '#888888',
  },
};

// 状态对应的颜色 - 更柔和
const STATUS_COLORS_MAP: Record<string, { color: string; bg: string }> = {
  completed: { color: '#2d6a4f', bg: '#d8f3dc' },
  done: { color: '#2d6a4f', bg: '#d8f3dc' },
  running: { color: '#1d4e89', bg: '#cfe2f3' },
  executing: { color: '#1d4e89', bg: '#cfe2f3' },
  pending: { color: '#9a6b00', bg: '#fff3cd' },
  failed: { color: '#9c2230', bg: '#f8d7da' },
  error: { color: '#9c2230', bg: '#f8d7da' },
  default: { color: '#666666', bg: '#f0f0f0' },
};

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

  // 进入全屏
  const enterFullscreen = useCallback(async () => {
    try {
      if (containerRef.current && !document.fullscreenElement) {
        await containerRef.current.requestFullscreen();
      }
    } catch (err) {
      console.error('进入全屏失败:', err);
      message.warning('浏览器不支持全屏模式');
    }
  }, []);

  // 退出全屏
  const exitFullscreen = useCallback(async () => {
    try {
      if (document.fullscreenElement) {
        await document.exitFullscreen();
      }
    } catch (err) {
      console.error('退出全屏失败:', err);
    }
    onClose?.();
  }, [onClose]);

  // 监听全屏状态变化
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

  // 组件挂载时自动进入全屏
  useEffect(() => {
    enterFullscreen();
  }, [enterFullscreen]);

  // 监听容器尺寸变化
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

  // 加载任务数据
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
        failed: allTasks.filter(t => t.status === 'failed').length,
      });
    } catch (error: any) {
      console.error('加载任务失败:', error);
      message.error(`加载任务数据失败: ${error.message}`);
    } finally {
      setLoading(false);
    }
  }, [currentPlanId]);

  useEffect(() => {
    loadTasks();
  }, [loadTasks]);

  // 计算高亮的节点
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

  // 为每个节点生成随机倾斜角度（带种子确保一致性）
  const getRotation = useCallback((id: number) => {
    const seed = id * 9301 + 49297;
    return ((seed % 233280) / 233280 - 0.5) * 6; // -3° 到 3°
  }, []);

  // 获取卡片颜色配置
  const getCardColors = useCallback((taskType?: string) => {
    const type = taskType?.toUpperCase();
    switch (type) {
      case 'ROOT': return CARD_COLORS.ROOT;
      case 'COMPOSITE': return CARD_COLORS.COMPOSITE;
      case 'ATOMIC': return CARD_COLORS.ATOMIC;
      default: return CARD_COLORS.DEFAULT;
    }
  }, []);

  // 构建图数据
  const graphData = useMemo(() => {
    let filteredTasks = tasks;
    
    if (searchText) {
      filteredTasks = filteredTasks.filter(task =>
        task.name.toLowerCase().includes(searchText.toLowerCase())
      );
    }

    // 计算分层布局位置
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
        rotation: 0, // 索引卡片风格不倾斜
        noteColor: colors.bg,
      };
    });

    const links: GraphLink[] = [];
    const taskIdSet = new Set(filteredTasks.map(t => t.id));

    filteredTasks.forEach(task => {
      // 父子关系 - 实线
      if (task.parent_id != null && taskIdSet.has(task.parent_id)) {
        links.push({
          source: task.parent_id,
          target: task.id,
          type: 'parent',
        });
      }

      // 依赖关系 - 虚线
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

  // 紧凑卡片尺寸
  const CARD_WIDTH = 130;
  const CARD_HEIGHT = 48;
  const CARD_RADIUS = 3;

  // 绘制紧凑索引卡片
  const drawNote = useCallback((
    ctx: CanvasRenderingContext2D,
    node: GraphNode,
    isSelected: boolean,
    isHovered: boolean,
    isHighlighted: boolean
  ) => {
    const x = node.x || 0;
    const y = node.y || 0;
    const colors = getCardColors(node.task.task_type);
    const statusInfo = STATUS_COLORS_MAP[node.task.status] || STATUS_COLORS_MAP.default;
    
    ctx.save();
    ctx.translate(x, y);

    const opacity = isHighlighted ? 1 : 0.25;
    const isActive = isSelected || isHovered;

    // 悬停/选中时的浮起效果
    if (isHighlighted) {
      ctx.shadowColor = isActive ? 'rgba(0, 0, 0, 0.15)' : 'rgba(0, 0, 0, 0.06)';
      ctx.shadowBlur = isActive ? 10 : 4;
      ctx.shadowOffsetX = 0;
      ctx.shadowOffsetY = isActive ? 4 : 2;
    }

    // 卡片主体
    ctx.beginPath();
    ctx.roundRect(-CARD_WIDTH / 2, -CARD_HEIGHT / 2, CARD_WIDTH, CARD_HEIGHT, CARD_RADIUS);
    ctx.fillStyle = colors.bg;
    ctx.globalAlpha = opacity;
    ctx.fill();

    // 重置阴影
    ctx.shadowColor = 'transparent';
    ctx.shadowBlur = 0;

    // 左侧色条（类型指示）
    ctx.beginPath();
    ctx.roundRect(-CARD_WIDTH / 2, -CARD_HEIGHT / 2, 4, CARD_HEIGHT, [CARD_RADIUS, 0, 0, CARD_RADIUS]);
    ctx.fillStyle = colors.accent;
    ctx.globalAlpha = opacity * 0.9;
    ctx.fill();

    // 边框
    ctx.beginPath();
    ctx.roundRect(-CARD_WIDTH / 2, -CARD_HEIGHT / 2, CARD_WIDTH, CARD_HEIGHT, CARD_RADIUS);
    ctx.strokeStyle = isActive ? colors.accent : 'rgba(0, 0, 0, 0.08)';
    ctx.lineWidth = isActive ? 1.5 : 0.5;
    ctx.globalAlpha = opacity;
    ctx.stroke();

    // 状态小点 - 右上角
    ctx.beginPath();
    ctx.arc(CARD_WIDTH / 2 - 8, -CARD_HEIGHT / 2 + 8, 4, 0, Math.PI * 2);
    ctx.fillStyle = statusInfo.color;
    ctx.globalAlpha = opacity;
    ctx.fill();

    // 任务名称
    ctx.globalAlpha = opacity;
    ctx.fillStyle = '#2c2c2c';
    ctx.font = node.task.task_type?.toUpperCase() === 'ROOT' 
      ? 'bold 10px Georgia, "Times New Roman", serif'
      : '10px Georgia, "Times New Roman", serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    
    // 多行文本处理
    const words = node.name.split('');
    let line = '';
    let lines: string[] = [];
    const maxWidth = CARD_WIDTH - 20;
    
    for (const char of words) {
      const testLine = line + char;
      const metrics = ctx.measureText(testLine);
      if (metrics.width > maxWidth && line) {
        lines.push(line);
        line = char;
      } else {
        line = testLine;
      }
    }
    lines.push(line);
    
    lines = lines.slice(0, 2);
    if (lines.length === 2 && node.name.length > lines.join('').length) {
      lines[1] = lines[1].slice(0, -2) + '..';
    }

    const lineHeight = 12;
    const textStartY = lines.length === 1 ? 0 : -lineHeight / 2;
    lines.forEach((l, i) => {
      ctx.fillText(l, 2, textStartY + i * lineHeight);
    });

    // 选中时的高亮边框
    if (isSelected && isHighlighted) {
      ctx.strokeStyle = colors.accent;
      ctx.lineWidth = 2;
      ctx.globalAlpha = 1;
      ctx.beginPath();
      ctx.roundRect(-CARD_WIDTH / 2 - 2, -CARD_HEIGHT / 2 - 2, CARD_WIDTH + 4, CARD_HEIGHT + 4, CARD_RADIUS + 1);
      ctx.stroke();
    }

    ctx.restore();
  }, [getCardColors]);

  // 绘制连接线 - 父子关系实线，依赖关系虚线
  const drawLink = useCallback((
    ctx: CanvasRenderingContext2D,
    link: any,
    isHighlighted: boolean
  ) => {
    const source = link.source;
    const target = link.target;
    const linkType: 'parent' | 'dependency' = link.type || 'parent';
    
    if (!source.x || !target.x) return;

    const isDependency = linkType === 'dependency';
    
    // 计算起点和终点
    let sourceX = source.x || 0;
    let sourceY = (source.y || 0) + CARD_HEIGHT / 2;
    let targetX = target.x || 0;
    let targetY = (target.y || 0) - CARD_HEIGHT / 2;

    // 依赖关系：从右侧连接到左侧
    if (isDependency) {
      sourceX = (source.x || 0) + CARD_WIDTH / 2;
      sourceY = source.y || 0;
      targetX = (target.x || 0) - CARD_WIDTH / 2;
      targetY = target.y || 0;
    }

    ctx.save();
    
    if (isDependency) {
      // 依赖关系 - 橙色虚线，更明显
      ctx.globalAlpha = 0.8;
      ctx.strokeStyle = '#d4956a';
      ctx.lineWidth = 1.5;
      ctx.setLineDash([5, 3]);
    } else {
      // 父子关系 - 灰色细线
      ctx.globalAlpha = isHighlighted ? 0.4 : 0.12;
      ctx.strokeStyle = '#8a9099';
      ctx.lineWidth = isHighlighted ? 1 : 0.6;
    }

    ctx.beginPath();
    ctx.moveTo(sourceX, sourceY);
    
    if (isDependency) {
      // 依赖关系：优雅的 S 形曲线
      const dx = targetX - sourceX;
      const dy = targetY - sourceY;
      const controlOffset = Math.min(Math.abs(dx) * 0.4, 60);
      ctx.bezierCurveTo(
        sourceX + controlOffset, sourceY,
        targetX - controlOffset, targetY,
        targetX, targetY
      );
    } else {
      // 父子关系：简洁的直角线
      const midY = sourceY + (targetY - sourceY) * 0.5;
      ctx.lineTo(sourceX, midY);
      ctx.lineTo(targetX, midY);
      ctx.lineTo(targetX, targetY);
    }
    
    ctx.stroke();

    // 依赖线箭头
    if (isDependency) {
      const arrowSize = 4;
      ctx.globalAlpha = 0.9;
      ctx.fillStyle = '#d4956a';
      ctx.setLineDash([]);
      
      ctx.beginPath();
      ctx.moveTo(targetX, targetY);
      ctx.lineTo(targetX - arrowSize * 1.8, targetY - arrowSize);
      ctx.lineTo(targetX - arrowSize * 1.8, targetY + arrowSize);
      ctx.closePath();
      ctx.fill();
    }

    ctx.restore();
  }, []);

  // 节点点击
  const handleNodeClick = useCallback((node: GraphNode) => {
    if (selectedNodeId === node.id) {
      setSelectedNodeId(null);
      onNodeSelect?.(null);
    } else {
      setSelectedNodeId(node.id);
      onNodeSelect?.(node.task);
    }
  }, [selectedNodeId, onNodeSelect]);

  // 节点悬停
  const handleNodeHover = useCallback((node: GraphNode | null) => {
    setHoveredNodeId(node?.id ?? null);
    if (containerRef.current) {
      containerRef.current.style.cursor = node ? 'pointer' : 'default';
    }
  }, []);

  // 背景点击
  const handleBackgroundClick = useCallback(() => {
    setSelectedNodeId(null);
    onNodeSelect?.(null);
  }, [onNodeSelect]);

  // 工具栏操作
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
      message.success('图片已导出');
    }
  };

  // 选中节点信息
  const selectedTask = useMemo(() => {
    if (!selectedNodeId) return null;
    return tasks.find(t => t.id === selectedNodeId) || null;
  }, [selectedNodeId, tasks]);

  // 关闭抽屉
  const closeDrawer = useCallback(() => {
    setSelectedNodeId(null);
    onNodeSelect?.(null);
  }, [onNodeSelect]);

  // 格式化 JSON 显示
  const formatJson = (data: any) => {
    if (!data) return null;
    try {
      return JSON.stringify(data, null, 2);
    } catch {
      return String(data);
    }
  };

  // 渲染任务详情抽屉内容
  const renderDrawerContent = () => {
    if (!selectedTask) return null;

    const tabItems = [
      {
        key: 'basic',
        label: <span><FileTextOutlined /> 基本信息</span>,
        children: (
          <div className="task-detail-section">
            <Descriptions column={1} size="small" bordered>
              <Descriptions.Item label="任务ID">{selectedTask.id}</Descriptions.Item>
              <Descriptions.Item label="名称">{selectedTask.name}</Descriptions.Item>
              <Descriptions.Item label="类型">
                <Tag color={getTaskTypeColors(selectedTask.task_type).primary}>
                  {getTaskTypeName(selectedTask.task_type)}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="状态">
                <Tag color={getStatusColor(selectedTask.status)}>
                  {getStatusName(selectedTask.status)}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="层级">{selectedTask.depth ?? '-'}</Descriptions.Item>
              <Descriptions.Item label="路径">{selectedTask.path || '-'}</Descriptions.Item>
              <Descriptions.Item label="父任务ID">{selectedTask.parent_id ?? '无'}</Descriptions.Item>
              {selectedTask.dependencies && selectedTask.dependencies.length > 0 && (
                <Descriptions.Item label="依赖任务">
                  {selectedTask.dependencies.map(d => (
                    <Tag key={d} style={{ marginRight: 4 }}>{d}</Tag>
                  ))}
                </Descriptions.Item>
              )}
            </Descriptions>

            {selectedTask.instruction && (
              <div style={{ marginTop: 16 }}>
                <Text strong style={{ display: 'block', marginBottom: 8 }}>任务指令</Text>
                <div className="task-instruction-box">
                  <Paragraph 
                    style={{ margin: 0, whiteSpace: 'pre-wrap' }}
                    ellipsis={{ rows: 6, expandable: true, symbol: '展开' }}
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
        label: <span><BranchesOutlined /> 上下文</span>,
        children: (
          <div className="task-detail-section">
            {selectedTask.context_combined ? (
              <>
                <div className="task-context-box">
                  <Paragraph 
                    style={{ margin: 0, whiteSpace: 'pre-wrap', fontSize: 12 }}
                    ellipsis={{ rows: 10, expandable: true, symbol: '展开' }}
                  >
                    {selectedTask.context_combined}
                  </Paragraph>
                </div>
                {selectedTask.context_updated_at && (
                  <Text type="secondary" style={{ fontSize: 11, marginTop: 8, display: 'block' }}>
                    更新时间: {new Date(selectedTask.context_updated_at).toLocaleString()}
                  </Text>
                )}
              </>
            ) : (
              <Empty description="暂无上下文信息" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            )}

            {selectedTask.context_sections && selectedTask.context_sections.length > 0 && (
              <div style={{ marginTop: 16 }}>
                <Text strong style={{ display: 'block', marginBottom: 8 }}>上下文分段</Text>
                <Collapse size="small" ghost>
                  {selectedTask.context_sections.map((section, idx) => (
                    <Panel 
                      header={section.name || `分段 ${idx + 1}`} 
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
        label: <span><HistoryOutlined /> 执行结果</span>,
        children: (
          <div className="task-detail-section">
            {selectedTask.execution_result ? (
              <div className="task-result-box">
                <Paragraph 
                  style={{ margin: 0, whiteSpace: 'pre-wrap', fontSize: 12 }}
                  ellipsis={{ rows: 15, expandable: true, symbol: '展开' }}
                >
                  {selectedTask.execution_result}
                </Paragraph>
              </div>
            ) : (
              <Empty description="暂无执行结果" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            )}
          </div>
        ),
      },
      {
        key: 'metadata',
        label: <span><DatabaseOutlined /> 元数据</span>,
        children: (
          <div className="task-detail-section">
            {selectedTask.metadata && Object.keys(selectedTask.metadata).length > 0 ? (
              <pre className="task-metadata-box">
                {formatJson(selectedTask.metadata)}
              </pre>
            ) : (
              <Empty description="暂无元数据" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            )}

            <Descriptions 
              column={1} 
              size="small" 
              style={{ marginTop: 16 }}
              title={<Text type="secondary" style={{ fontSize: 12 }}>系统信息</Text>}
            >
              {selectedTask.created_at && (
                <Descriptions.Item label="创建时间">
                  {new Date(selectedTask.created_at).toLocaleString()}
                </Descriptions.Item>
              )}
              {selectedTask.updated_at && (
                <Descriptions.Item label="更新时间">
                  {new Date(selectedTask.updated_at).toLocaleString()}
                </Descriptions.Item>
              )}
              {selectedTask.session_id && (
                <Descriptions.Item label="会话ID">
                  <Text copyable style={{ fontSize: 11 }}>{selectedTask.session_id}</Text>
                </Descriptions.Item>
              )}
              {selectedTask.workflow_id && (
                <Descriptions.Item label="工作流ID">
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
      {/* 2D 图 */}
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
          
          // 依赖线：只在相关节点激活时显示
          if (linkType === 'dependency') {
            if (!activeNodeId || (sourceId !== activeNodeId && targetId !== activeNodeId)) {
              return; // 隐藏不相关的依赖线
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

      {/* 工具栏 */}
      <div className="dag-toolbar">
        <button className="dag-toolbar-btn" onClick={handleFitView} title="缩放适应">
          <ExpandOutlined />
        </button>
        <button className="dag-toolbar-btn" onClick={handleResetView} title="重置视角">
          <AimOutlined />
        </button>
        <div className="dag-toolbar-divider" />
        <button className="dag-toolbar-btn" onClick={handleRefresh} title="刷新">
          <ReloadOutlined />
        </button>
        <button className="dag-toolbar-btn" onClick={handleExport} title="导出图片">
          <DownloadOutlined />
        </button>
        <div className="dag-toolbar-divider" />
        <button className="dag-toolbar-btn" onClick={exitFullscreen} title="退出全屏">
          <FullscreenExitOutlined />
        </button>
      </div>

      {/* 搜索栏 */}
      <div className="dag-search-bar">
        <SearchOutlined className="dag-search-icon" />
        <input
          className="dag-search-input"
          type="text"
          placeholder="搜索任务..."
          value={searchText}
          onChange={(e) => setSearchText(e.target.value)}
        />
      </div>

      {/* 图例面板 */}
      <div className="dag-legend-panel dag-card-legend">
        <div className="dag-legend-title">Task Types</div>
        <div className="dag-legend-item">
          <span className="dag-legend-card" style={{ 
            background: CARD_COLORS.ROOT.bg,
            borderLeft: `3px solid ${CARD_COLORS.ROOT.accent}`
          }} />
          <span style={{ fontStyle: 'italic' }}>Root</span>
        </div>
        <div className="dag-legend-item">
          <span className="dag-legend-card" style={{ 
            background: CARD_COLORS.COMPOSITE.bg,
            borderLeft: `3px solid ${CARD_COLORS.COMPOSITE.accent}`
          }} />
          <span style={{ fontStyle: 'italic' }}>Composite</span>
        </div>
        <div className="dag-legend-item">
          <span className="dag-legend-card" style={{ 
            background: CARD_COLORS.ATOMIC.bg,
            borderLeft: `3px solid ${CARD_COLORS.ATOMIC.accent}`
          }} />
          <span style={{ fontStyle: 'italic' }}>Atomic</span>
        </div>
        
        <div className="dag-legend-divider" />
        <div className="dag-legend-title">Connections</div>
        <div className="dag-legend-item">
          <span className="dag-legend-line dag-legend-line-solid" />
          <span>Hierarchy</span>
        </div>
        <div className="dag-legend-item">
          <span className="dag-legend-line dag-legend-line-dashed" />
          <span>Dependency <small style={{ opacity: 0.6 }}>(hover)</small></span>
        </div>
        
        <div className="dag-legend-hint">
          Scroll to zoom · Click to select
        </div>
      </div>

      {/* 统计栏 */}
      <div className="dag-stats-bar">
        <div className="dag-stats-item">
          <span className="dag-stats-count">{stats.total}</span>
          <span>总计</span>
        </div>
        {stats.pending > 0 && (
          <div className="dag-stats-item">
            <span className="dag-stats-dot pending" />
            <span>{stats.pending} 待处理</span>
          </div>
        )}
        {stats.running > 0 && (
          <div className="dag-stats-item">
            <span className="dag-stats-dot running" />
            <span>{stats.running} 运行中</span>
          </div>
        )}
        {stats.completed > 0 && (
          <div className="dag-stats-item">
            <span className="dag-stats-dot completed" />
            <span>{stats.completed} 已完成</span>
          </div>
        )}
        {stats.failed > 0 && (
          <div className="dag-stats-item">
            <span className="dag-stats-dot failed" />
            <span>{stats.failed} 失败</span>
          </div>
        )}
      </div>

      {/* 任务详情抽屉 */}
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
              {selectedTask?.name || '任务详情'}
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

      {/* 全屏提示 */}
      <div className="dag-fullscreen-hint">
        ESC 退出 · 拖拽移动 · 滚轮缩放 · 点击选择
      </div>
    </div>
  );
};

export default DAG3DView;
