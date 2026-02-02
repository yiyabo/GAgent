import React, { useEffect, useMemo, useState } from 'react';
import { Spin } from 'antd';
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
  depth: number;
}

const getOrderKey = (task: PlanTaskNode): number =>
  typeof task.position === 'number' ? task.position : task.id;

const compareTaskOrder = (a: PlanTaskNode, b: PlanTaskNode): number => {
  const posDiff = getOrderKey(a) - getOrderKey(b);
  if (posDiff !== 0) return posDiff;
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

  // 构建树形结构
  const buildTree = useMemo((): TreeNode[] => {
    if (!tasks || tasks.length === 0) return [];

    const roots = tasks
      .filter(task => !task.parent_id || task.task_type?.toLowerCase() === 'root')
      .sort(compareTaskOrder);

    const buildNode = (task: PlanTaskNode, depth: number): TreeNode => {
      const children = tasks
        .filter(t => t.parent_id === task.id)
        .map(child => buildNode(child, depth + 1))
        .sort((a, b) => compareTaskOrder(a.task, b.task));

      return { task, children, depth };
    };

    return roots.map(root => buildNode(root, 0));
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

  // 扁平化树以便渲染
  const flattenTree = (nodes: TreeNode[], parentCollapsed = false): TreeNode[] => {
    const result: TreeNode[] = [];
    for (const node of nodes) {
      result.push(node);
      if (!collapsed.has(node.task.id) && node.children.length > 0) {
        result.push(...flattenTree(node.children));
      }
    }
    return result;
  };

  const flatNodes = useMemo(() => flattenTree(buildTree), [buildTree, collapsed]);

  if (loading) {
    return (
      <div className="tree-loading">
        <Spin size="small" />
        <span>加载中...</span>
      </div>
    );
  }

  if (buildTree.length === 0) {
    return (
      <div className="tree-empty">
        <div className="tree-empty-dot" />
        <span>暂无任务</span>
      </div>
    );
  }

  return (
    <div className="tree-container" style={{ height }}>
      <div className="tree-list">
        {flatNodes.map((node) => {
          const { task, children, depth } = node;
          const hasChildren = children.length > 0;
          const isCollapsed = collapsed.has(task.id);
          const isSelected = effectiveSelectedId === task.id;
          const isRoot = task.task_type?.toLowerCase() === 'root';
          
          const cleanName = (task.short_name || task.name || '').replace(/^(ROOT|COMPOSITE|ATOMIC):\s*/i, '');
          const displayName = cleanName.length > 36 ? cleanName.substring(0, 36) + '…' : cleanName;

          return (
            <div
              key={task.id}
              className={`tree-item ${isSelected ? 'selected' : ''} ${isRoot ? 'root' : ''}`}
              style={{ '--depth': depth } as React.CSSProperties}
              onClick={() => handleSelectTask(task)}
            >
              {/* 缩进区域 */}
              <div className="tree-indent">
                {Array.from({ length: depth }).map((_, i) => (
                  <span key={i} className="tree-indent-guide" />
                ))}
              </div>

              {/* 展开/折叠按钮 */}
              <div 
                className={`tree-toggle ${hasChildren ? 'has-children' : ''}`}
                onClick={(e) => hasChildren && toggleCollapse(task.id, e)}
              >
                {hasChildren && (
                  <svg 
                    viewBox="0 0 12 12" 
                    className={`tree-chevron ${isCollapsed ? '' : 'expanded'}`}
                  >
                    <path d="M4.5 2L8.5 6L4.5 10" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                  </svg>
                )}
              </div>

              {/* 状态指示器 */}
              <div className={`tree-status status-${task.status}`} title={task.status} />

              {/* 任务名称 */}
              <span className="tree-name" title={cleanName}>
                {displayName}
              </span>

              {/* 任务类型标签（仅root显示） */}
              {isRoot && <span className="tree-badge">ROOT</span>}
            </div>
          );
        })}
      </div>
    </div>
  );
};

export default PlanTreeVisualization;
