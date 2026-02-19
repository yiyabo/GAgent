/**
 * DAG 可视化组件 - 便签纸风格
 * 特点：柔和阴影、略带倾斜、手绘感、轻松氛围
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
      <DAGToolbar
        onFitView={handleFitView}
        onResetView={handleResetView}
        onRefresh={handleRefresh}
        onExport={handleExport}
        onExitFullscreen={exitFullscreen}
      />

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
      <DAGLegend />

      {/* 统计栏 */}
      <DAGStatsBar stats={stats} />

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
