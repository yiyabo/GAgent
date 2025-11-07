import React, { useEffect, useMemo, useState } from 'react';
import { Spin, Tooltip } from 'antd';
import { PlanTaskNode } from '@/types';
import './PlanTreeVisualization.css';

export interface PlanTreeVisualizationProps {
  tasks: PlanTaskNode[];
  loading?: boolean;
  height?: number | string;
  onSelectTask?: (task: PlanTaskNode | null) => void;
  selectedTaskId?: number | null;
}

interface TreeNode {
  task: PlanTaskNode;
  children: TreeNode[];
}

const getOrderKey = (task: PlanTaskNode): number =>
  typeof task.position === 'number' ? task.position : task.id;

const compareTaskOrder = (a: PlanTaskNode, b: PlanTaskNode): number => {
  const posDiff = getOrderKey(a) - getOrderKey(b);
  if (posDiff !== 0) {
    return posDiff;
  }
  return a.id - b.id;
};

const PlanTreeVisualization: React.FC<PlanTreeVisualizationProps> = ({
  tasks,
  loading,
  height = '480px',
  onSelectTask,
  selectedTaskId,
}) => {
  const [collapsed, setCollapsed] = useState<Set<number>>(new Set());
  const [internalSelectedId, setInternalSelectedId] = useState<number | null>(null);

  const effectiveSelectedId =
    selectedTaskId !== undefined ? selectedTaskId ?? null : internalSelectedId;

  useEffect(() => {
    if (selectedTaskId !== undefined) {
      setInternalSelectedId(selectedTaskId ?? null);
    }
  }, [selectedTaskId]);

  // 状态图标
  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'completed':
        return '✅';
      case 'running':
        return '⚡';
      case 'pending':
        return '⏳';
      case 'failed':
        return '❌';
      default:
        return '⭕';
    }
  };

  // 类型图标
  const getTypeIcon = (taskType?: string) => {
    if (!taskType) return '📄';
    switch (taskType.toLowerCase()) {
      case 'root':
        return '⭐';
      case 'composite':
        return '📦';
      case 'atomic':
        return '⚙️';
      default:
        return '📄';
    }
  };

  // 状态颜色
  const getStatusColor = (status: string) => {
    switch (status) {
      case 'completed':
        return '#52c41a';
      case 'running':
        return '#1890ff';
      case 'pending':
        return '#faad14';
      case 'failed':
        return '#ff4d4f';
      default:
        return '#d9d9d9';
    }
  };

  // 构建树形结构
  const buildTree = useMemo((): TreeNode[] => {
    if (!tasks || tasks.length === 0) return [];

    const roots = tasks
      .filter(task => !task.parent_id || task.task_type?.toLowerCase() === 'root')
      .sort(compareTaskOrder);

    const buildNode = (task: PlanTaskNode): TreeNode => {
      const children = tasks
        .filter(t => t.parent_id === task.id)
        .map(child => buildNode(child))
        .sort((a, b) => compareTaskOrder(a.task, b.task));

      return { task, children };
    };

    return roots.map(root => buildNode(root));
  }, [tasks]);

  // 切换折叠
  const toggleCollapse = (taskId: number, e: React.MouseEvent) => {
    e.stopPropagation();
    setCollapsed(prev => {
      const newSet = new Set(prev);
      if (newSet.has(taskId)) {
        newSet.delete(taskId);
      } else {
        newSet.add(taskId);
      }
      return newSet;
    });
  };

  // 选择任务
  const handleSelectTask = (task: PlanTaskNode) => {
    if (selectedTaskId === undefined) {
      setInternalSelectedId(task.id);
    }
    onSelectTask?.(task);
  };

  // 渲染树节点
  const renderTreeNode = (
    node: TreeNode,
    isLast: boolean,
    prefix: string = '',
    isRoot: boolean = false
  ): React.ReactNode => {
    const { task, children } = node;
    const hasChildren = children.length > 0;
    const isCollapsed = collapsed.has(task.id);
    const isSelected = effectiveSelectedId === task.id;
    
    const cleanName = (task.short_name || task.name || '').replace(/^(ROOT|COMPOSITE|ATOMIC):\s*/i, '');
    const displayName = cleanName.length > 30 ? cleanName.substring(0, 30) + '...' : cleanName;
    
    const connector = isRoot ? '' : (isLast ? '└── ' : '├── ');
    const childPrefix = isRoot ? '' : (isLast ? '    ' : '│   ');

    return (
      <div key={task.id} className="plan-tree-node">
        <div 
          className={`plan-tree-node-content task-type-${task.task_type?.toLowerCase()} ${isSelected ? 'selected' : ''}`}
          onClick={() => handleSelectTask(task)}
        >
          <span className="plan-tree-connector">{prefix}{connector}</span>
          
          {hasChildren && (
            <span 
              className="plan-tree-collapse-btn"
              onClick={(e) => toggleCollapse(task.id, e)}
            >
              {isCollapsed ? '▶' : '▼'}
            </span>
          )}
          
          <Tooltip 
            title={`ID: ${task.id} | 状态: ${task.status} | 类型: ${task.task_type}`}
            placement="right"
          >
            <span className="plan-tree-node-info">
              <span className="plan-node-type-icon">{getTypeIcon(task.task_type)}</span>
              <span className="plan-node-status-icon">{getStatusIcon(task.status)}</span>
              <span 
                className="plan-node-name"
                style={{ 
                  color: getStatusColor(task.status),
                  fontWeight: task.task_type?.toLowerCase() === 'root' ? 'bold' : 'normal',
                }}
              >
                {displayName}
              </span>
            </span>
          </Tooltip>
        </div>

        {hasChildren && !isCollapsed && (
          <div className="plan-tree-children">
            {children.map((child, index) =>
              renderTreeNode(
                child,
                index === children.length - 1,
                prefix + childPrefix,
                false
              )
            )}
          </div>
        )}
      </div>
    );
  };

  if (loading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height }}>
        <Spin tip="加载任务中..." />
      </div>
    );
  }

  if (buildTree.length === 0) {
    return (
      <div style={{ 
        display: 'flex', 
        flexDirection: 'column',
        justifyContent: 'center', 
        alignItems: 'center', 
        height,
        color: '#999',
        fontSize: '12px'
      }}>
        <div style={{ fontSize: '32px', marginBottom: '8px' }}>🌳</div>
        <div>暂无任务</div>
      </div>
    );
  }

  return (
    <div className="plan-tree-visualization-container" style={{ height }}>
      <div className="plan-tree-content">
        {buildTree.map(rootNode => renderTreeNode(rootNode, true, '', true))}
      </div>
    </div>
  );
};

export default PlanTreeVisualization;
