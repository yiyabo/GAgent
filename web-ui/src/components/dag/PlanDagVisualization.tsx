import React, { useEffect, useRef } from 'react';
import { Spin } from 'antd';
import { Network } from 'vis-network';
import { DataSet } from 'vis-data';
import { PlanTaskNode } from '@/types';

export interface PlanDagVisualizationProps {
  tasks: PlanTaskNode[];
  loading?: boolean;
  height?: number | string;
  onSelectTask?: (task: PlanTaskNode | null) => void;
}

const statusColorMap: Record<string, string> = {
  pending: '#faad14',
  running: '#1890ff',
  completed: '#52c41a',
  failed: '#ff4d4f',
};

const groupBorderColor: Record<string, string> = {
  root: '#873bf4',
  composite: '#1890ff',
  atomic: '#52c41a',
};

const PlanDagVisualization: React.FC<PlanDagVisualizationProps> = ({
  tasks,
  loading,
  height = '480px',
  onSelectTask,
}) => {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const networkRef = useRef<Network | null>(null);

  useEffect(() => {
    if (!containerRef.current || !tasks) {
      return;
    }

    const nodes = new DataSet(
      tasks.map((task) => {
        const label = (task.short_name || task.name || '').slice(0, 40);
        const status = task.status || 'pending';
        const type = task.task_type || 'atomic';
        return {
          id: task.id,
          label: label.length === (task.short_name || task.name || '').length ? label : `${label}…`,
          level: task.depth ?? 0,
          color: {
            background: statusColorMap[status] || '#d9d9d9',
            border: groupBorderColor[type] || '#d9d9d9',
            highlight: {
              background: '#ffc53d',
              border: '#fa8c16',
            },
          },
          shape:
            type === 'root' ? 'star' : type === 'composite' ? 'box' : 'ellipse',
          font: {
            color: '#ffffff',
            face: 'Inter, Arial, sans-serif',
            size: type === 'root' ? 16 : 14,
          },
          borderWidth: type === 'root' ? 3 : 2,
          shadow: {
            enabled: true,
            size: 8,
            x: 2,
            y: 2,
          },
          title: `任务：${task.name}\n状态：${task.status}\n类型：${task.task_type}`,
        };
      })
    );

    const edges = new DataSet(
      tasks
        .filter((task) => task.parent_id)
        .map((task) => ({
          id: `${task.parent_id}-${task.id}`,
          from: task.parent_id!,
          to: task.id,
          arrows: 'to',
          color: {
            color: '#c4c4c4',
            highlight: '#8c8c8c',
          },
        }))
    );

    if (networkRef.current) {
      networkRef.current.setData({ nodes, edges });
      return;
    }

    const network = new Network(
      containerRef.current,
      { nodes, edges },
      {
        layout: {
          hierarchical: {
            enabled: true,
            levelSeparation: 140,
            nodeSpacing: 220,
            treeSpacing: 120,
            direction: 'UD',
            sortMethod: 'directed',
          },
        },
        edges: {
          smooth: { enabled: true, type: 'cubicBezier', forceDirection: 'vertical', roundness: 0.4 },
        },
        physics: {
          enabled: false,
        },
        interaction: {
          hover: true,
          dragNodes: false,
        },
      }
    );

    networkRef.current = network;

    network.on('selectNode', (params) => {
      const nodeId = params.nodes?.[0];
      if (!nodeId) {
        onSelectTask?.(null);
        return;
      }
      const task = tasks.find((item) => item.id === nodeId) || null;
      onSelectTask?.(task ?? null);
    });

    network.on('deselectNode', () => {
      onSelectTask?.(null);
    });

    return () => {
      network.destroy();
      networkRef.current = null;
    };
  }, [tasks, onSelectTask]);

  return (
    <div style={{ position: 'relative', height }}>
      {loading && (
        <div
          style={{
            position: 'absolute',
            inset: 0,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 2,
            background: 'rgba(255,255,255,0.7)',
          }}
        >
          <Spin size="large" tip="加载DAG中" />
        </div>
      )}
      <div ref={containerRef} style={{ height: '100%' }} />
    </div>
  );
};

export default PlanDagVisualization;
