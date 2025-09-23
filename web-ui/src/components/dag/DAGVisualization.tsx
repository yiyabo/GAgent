import React, { useEffect, useRef, useState } from 'react';
import { Card, Button, Select, Space, Tooltip, Badge } from 'antd';
import {
  FullscreenOutlined,
  ReloadOutlined,
  SettingOutlined,
  PlayCircleOutlined,
  PauseCircleOutlined,
  ZoomInOutlined,
  ZoomOutOutlined,
} from '@ant-design/icons';
import { Network } from 'vis-network';
import { DataSet } from 'vis-data';
import { useTasksStore } from '@store/tasks';
import { DAGNode, DAGEdge } from '@types/index';

interface DAGVisualizationProps {
  height?: string | number;
  interactive?: boolean;
  showToolbar?: boolean;
}

const DAGVisualization: React.FC<DAGVisualizationProps> = ({
  height = '100%',
  interactive = true,
  showToolbar = true,
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const networkRef = useRef<Network | null>(null);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [autoLayout, setAutoLayout] = useState(true);

  const { 
    dagNodes, 
    dagEdges, 
    dagLayout, 
    setDagLayout, 
    selectedTask,
    setSelectedTask,
    tasks 
  } = useTasksStore();

  // 初始化网络图
  useEffect(() => {
    if (!containerRef.current) return;

    // 准备数据
    const nodes = new DataSet(
      dagNodes.map(node => ({
        id: node.id,
        label: node.label,
        group: node.group,
        level: node.level,
        color: getNodeColor(node.status, node.group),
        font: {
          size: 14,
          color: '#333',
          face: 'Arial',
        },
        borderWidth: selectedTask?.id.toString() === node.id ? 3 : 1,
        borderWidthSelected: 3,
        shadow: {
          enabled: true,
          color: 'rgba(0,0,0,0.1)',
          size: 5,
        },
        ...getNodeShape(node.group),
      }))
    );

    const edges = new DataSet(
      dagEdges.map(edge => ({
        from: edge.from,
        to: edge.to,
        label: edge.label,
        color: edge.color || '#1890ff',
        width: 2,
        arrows: {
          to: {
            enabled: true,
            scaleFactor: 1.2,
          },
        },
        font: {
          size: 10,
          color: '#666',
        },
        smooth: {
          type: 'curvedCW',
          roundness: 0.2,
        },
        dashes: edge.dashes || false,
      }))
    );

    // 网络配置
    const options = {
      layout: getLayoutOptions(dagLayout),
      physics: {
        enabled: autoLayout,
        stabilization: { iterations: 100 },
        barnesHut: {
          gravitationalConstant: -2000,
          centralGravity: 0.1,
          springLength: 200,
          springConstant: 0.04,
          damping: 0.4,
        },
      },
      interaction: {
        dragNodes: interactive,
        dragView: true,
        zoomView: true,
        selectConnectedEdges: false,
      },
      nodes: {
        font: {
          size: 14,
          color: '#333',
        },
        margin: 10,
        widthConstraint: {
          maximum: 200,
        },
      },
      edges: {
        font: {
          size: 10,
          align: 'middle',
        },
        arrowStrikethrough: false,
      },
      groups: {
        root: {
          color: { background: '#ff7875', border: '#ff4d4f' },
          shape: 'diamond',
          size: 40,
        },
        composite: {
          color: { background: '#87d068', border: '#52c41a' },
          shape: 'ellipse',
          size: 30,
        },
        atomic: {
          color: { background: '#69c0ff', border: '#1890ff' },
          shape: 'box',
          size: 25,
        },
      },
    };

    // 创建网络
    const network = new Network(containerRef.current, { nodes, edges }, options);
    networkRef.current = network;

    // 事件监听
    network.on('selectNode', (params) => {
      const nodeId = params.nodes[0];
      const task = tasks.find(t => t.id.toString() === nodeId);
      if (task) {
        setSelectedTask(task);
      }
    });

    network.on('deselectNode', () => {
      setSelectedTask(null);
    });

    network.on('doubleClick', (params) => {
      if (params.nodes.length > 0) {
        const nodeId = params.nodes[0];
        const task = tasks.find(t => t.id.toString() === nodeId);
        if (task) {
          // 双击执行任务（这里可以添加执行逻辑）
          console.log('Execute task:', task);
        }
      }
    });

    // 清理函数
    return () => {
      if (networkRef.current) {
        networkRef.current.destroy();
        networkRef.current = null;
      }
    };
  }, [dagNodes, dagEdges, dagLayout, autoLayout, interactive, selectedTask, tasks, setSelectedTask]);

  // 节点颜色配置
  const getNodeColor = (status: string, group: string) => {
    const statusColors = {
      pending: { background: '#f0f0f0', border: '#d9d9d9' },
      running: { background: '#e6f7ff', border: '#1890ff' },
      completed: { background: '#f6ffed', border: '#52c41a' },
      failed: { background: '#fff2f0', border: '#ff4d4f' },
    };

    return statusColors[status as keyof typeof statusColors] || statusColors.pending;
  };

  // 节点形状配置
  const getNodeShape = (group: string) => {
    const shapes = {
      root: { shape: 'diamond', size: 40 },
      composite: { shape: 'ellipse', size: 30 },
      atomic: { shape: 'box', size: 25 },
    };

    return shapes[group as keyof typeof shapes] || shapes.atomic;
  };

  // 布局选项
  const getLayoutOptions = (layout: string) => {
    switch (layout) {
      case 'hierarchical':
        return {
          hierarchical: {
            enabled: true,
            direction: 'UD',
            sortMethod: 'directed',
            nodeSpacing: 200,
            levelSeparation: 150,
          },
        };
      case 'force':
        return {
          randomSeed: 1,
        };
      case 'circular':
        return {
          randomSeed: 1,
        };
      default:
        return {};
    }
  };

  // 工具栏操作
  const handleRefresh = () => {
    if (networkRef.current) {
      networkRef.current.redraw();
      networkRef.current.fit();
    }
  };

  const handleZoomIn = () => {
    if (networkRef.current) {
      const scale = networkRef.current.getScale();
      networkRef.current.moveTo({ scale: scale * 1.2 });
    }
  };

  const handleZoomOut = () => {
    if (networkRef.current) {
      const scale = networkRef.current.getScale();
      networkRef.current.moveTo({ scale: scale * 0.8 });
    }
  };

  const handleFullscreen = () => {
    setIsFullscreen(!isFullscreen);
  };

  const toggleAutoLayout = () => {
    setAutoLayout(!autoLayout);
  };

  return (
    <Card 
      className="dag-container"
      style={{ height }}
      bodyStyle={{ height: '100%', padding: 0 }}
    >
      {showToolbar && (
        <div className="dag-toolbar">
          <Space>
            <Badge count={dagNodes.length} size="small">
              <span>节点</span>
            </Badge>
            <Badge count={dagEdges.length} size="small">
              <span>连接</span>
            </Badge>
          </Space>

          <Space>
            <Select
              value={dagLayout}
              onChange={setDagLayout}
              size="small"
              style={{ width: 120 }}
              options={[
                { label: '层次布局', value: 'hierarchical' },
                { label: '力导向', value: 'force' },
                { label: '环形布局', value: 'circular' },
              ]}
            />

            <Tooltip title="自动布局">
              <Button
                size="small"
                type={autoLayout ? 'primary' : 'default'}
                icon={autoLayout ? <PauseCircleOutlined /> : <PlayCircleOutlined />}
                onClick={toggleAutoLayout}
              />
            </Tooltip>

            <Tooltip title="放大">
              <Button size="small" icon={<ZoomInOutlined />} onClick={handleZoomIn} />
            </Tooltip>

            <Tooltip title="缩小">
              <Button size="small" icon={<ZoomOutOutlined />} onClick={handleZoomOut} />
            </Tooltip>

            <Tooltip title="刷新">
              <Button size="small" icon={<ReloadOutlined />} onClick={handleRefresh} />
            </Tooltip>

            <Tooltip title="全屏">
              <Button size="small" icon={<FullscreenOutlined />} onClick={handleFullscreen} />
            </Tooltip>

            <Tooltip title="设置">
              <Button size="small" icon={<SettingOutlined />} />
            </Tooltip>
          </Space>
        </div>
      )}

      <div 
        ref={containerRef} 
        className="dag-canvas"
        style={{ 
          height: showToolbar ? 'calc(100% - 57px)' : '100%',
          width: '100%',
        }}
      />
    </Card>
  );
};

export default DAGVisualization;
