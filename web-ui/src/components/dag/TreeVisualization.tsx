import React, { useCallback, useEffect, useState } from 'react';
import { Card, Spin, Button, Space, Select, Input, message, Badge, Tooltip } from 'antd';
import { ReloadOutlined, ExpandOutlined, CompressOutlined } from '@ant-design/icons';
import { planTreeApi } from '@api/planTree';
import { planTreeToTasks } from '@utils/planTree';
import type { PlanSyncEventDetail, Task as TaskType } from '@/types';
import { useChatStore } from '@store/chat';
import { useTasksStore } from '@store/tasks';
import { shouldHandlePlanSyncEvent } from '@utils/planSyncEvents';
import './TreeVisualization.css';

interface TreeVisualizationProps {
  onNodeClick?: (taskId: number, taskData: any) => void;
  onNodeDoubleClick?: (taskId: number, taskData: any) => void;
}

interface TreeNode {
  task: TaskType;
  children: TreeNode[];
}

const getOrderKey = (task: TaskType): number =>
  typeof task.position === 'number' ? task.position : task.id;

const compareTaskOrder = (a: TaskType, b: TaskType): number => {
  const diff = getOrderKey(a) - getOrderKey(b);
  if (diff !== 0) {
    return diff;
  }
  return a.id - b.id;
};

const TreeVisualization: React.FC<TreeVisualizationProps> = ({
  onNodeClick,
  onNodeDoubleClick,
}) => {
  const [tasks, setTasks] = useState<TaskType[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchText, setSearchText] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [stats, setStats] = useState<any>(null);
  const [collapsed, setCollapsed] = useState<Set<number>>(new Set());
  const currentPlanId = useChatStore((state) => state.currentPlanId);
  const { setTasks: updateStoreTasks, setTaskStats } = useTasksStore((state) => ({
    setTasks: state.setTasks,
    setTaskStats: state.setTaskStats,
  }));

  // 状态图标映射
  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'completed':
      case 'done':
        return '✅';
      case 'running':
      case 'executing':
        return '⚡';
      case 'pending':
        return '⏳';
      case 'failed':
      case 'error':
        return '❌';
      default:
        return '⭕';
    }
  };

  // 任务类型图标
  const getTypeIcon = (taskType?: string) => {
    if (!taskType) return '📄';
    
    switch (taskType.toUpperCase()) {
      case 'ROOT':
        return '⭐';
      case 'COMPOSITE':
        return '📦';
      case 'ATOMIC':
        return '⚙️';
      default:
        return '📄';
    }
  };

  // 状态颜色
  const getStatusColor = (status: string) => {
    switch (status) {
      case 'completed':
      case 'done':
        return '#52c41a';
      case 'running':
      case 'executing':
        return '#1890ff';
      case 'pending':
        return '#faad14';
      case 'failed':
      case 'error':
        return '#ff4d4f';
      default:
        return '#d9d9d9';
    }
  };

  // 加载任务数据
  const loadTasks = useCallback(async () => {
    try {
      setLoading(true);
      console.log('🔄 Loading tasks for Tree visualization...');

      if (!currentPlanId) {
        console.warn('⚠️ 当前无绑定计划，跳过任务加载');
        setTasks([]);
        setStats(null);
        updateStoreTasks([]);
        setTaskStats(null);
        return;
      }

      const tree = await planTreeApi.getPlanTree(currentPlanId);
      const allTasks = planTreeToTasks(tree);
      console.log('📊 Raw tasks data:', allTasks);

      setTasks(allTasks);
      updateStoreTasks(allTasks);

      const computedStats = {
        total: allTasks.length,
        pending: allTasks.filter((task) => task.status === 'pending').length,
        running: allTasks.filter((task) => task.status === 'running').length,
        completed: allTasks.filter((task) => task.status === 'completed').length,
        failed: allTasks.filter((task) => task.status === 'failed').length,
      };
      setStats(computedStats);
      setTaskStats(computedStats);
    } catch (error: any) {
      console.error('❌ Failed to load tasks:', error);
      message.error(`加载任务数据失败: ${error.message}`);
    } finally {
      setLoading(false);
    }
  }, [currentPlanId, setTaskStats, updateStoreTasks]);

  // 构建树形结构
  const buildTree = (): TreeNode[] => {
    let filteredTasks = tasks;

    // 应用搜索过滤
    if (searchText) {
      filteredTasks = filteredTasks.filter(task =>
        task.name.toLowerCase().includes(searchText.toLowerCase())
      );
    }

    // 应用状态过滤
    if (statusFilter !== 'all') {
      filteredTasks = filteredTasks.filter(task => task.status === statusFilter);
    }

    // 找到ROOT任务
    const roots = filteredTasks
      .filter(task => !task.parent_id || task.task_type?.toLowerCase() === 'root')
      .sort(compareTaskOrder);

    // 递归构建树
    const buildNode = (task: TaskType): TreeNode => {
      const children = filteredTasks
        .filter(t => t.parent_id === task.id)
        .map(child => buildNode(child))
        .sort((a, b) => compareTaskOrder(a.task, b.task));

      return { task, children };
    };

    return roots.map(root => buildNode(root));
  };

  // 切换节点折叠状态
  const toggleCollapse = (taskId: number) => {
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
    
    // 清理任务名称
    const cleanName = task.name.replace(/^(ROOT|COMPOSITE|ATOMIC):\s*/i, '');
    const displayName = cleanName.length > 60 ? cleanName.substring(0, 60) + '...' : cleanName;
    
    // 树形连接符
    const connector = isRoot ? '' : (isLast ? '└── ' : '├── ');
    const childPrefix = isRoot ? '' : (isLast ? '    ' : '│   ');

    return (
      <div key={task.id} className="tree-node">
        {/* 当前节点 */}
        <div 
          className={`tree-node-content task-type-${task.task_type?.toLowerCase()}`}
          onClick={() => onNodeClick?.(task.id, task)}
          onDoubleClick={() => onNodeDoubleClick?.(task.id, task)}
        >
          <span className="tree-connector">{prefix}{connector}</span>
          
          {/* 折叠按钮 */}
          {hasChildren && (
            <span 
              className="tree-collapse-btn"
              onClick={(e) => {
                e.stopPropagation();
                toggleCollapse(task.id);
              }}
            >
              {isCollapsed ? '▶' : '▼'}
            </span>
          )}
          
          {/* 任务信息 */}
          <Tooltip title={`ID: ${task.id} | 状态: ${task.status} | 类型: ${task.task_type} | 深度: ${task.depth}`}>
            <span className="tree-node-info">
              <span className="node-type-icon">{getTypeIcon(task.task_type)}</span>
              <span className="node-status-icon">{getStatusIcon(task.status)}</span>
              <span 
                className="node-name"
                style={{ 
                  color: getStatusColor(task.status),
                  fontWeight: task.task_type?.toLowerCase() === 'root' ? 'bold' : 'normal',
                  fontSize: task.task_type?.toLowerCase() === 'root' ? '16px' : '14px'
                }}
              >
                {displayName}
              </span>
              <span className="node-id">#{task.id}</span>
            </span>
          </Tooltip>
        </div>

        {/* 子节点 */}
        {hasChildren && !isCollapsed && (
          <div className="tree-children">
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

  useEffect(() => {
    loadTasks();
  }, [currentPlanId]);

  useEffect(() => {
    const handleTasksUpdated = (event: CustomEvent<PlanSyncEventDetail>) => {
      const detail = event.detail;
      if (
        detail?.type === 'plan_deleted' &&
        detail.plan_id != null &&
        detail.plan_id === (currentPlanId ?? null)
      ) {
        setTasks([]);
        updateStoreTasks([]);
        setStats(null);
        setCollapsed(new Set());
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

  const handleExpandAll = () => {
    setCollapsed(new Set());
  };

  const handleCollapseAll = () => {
    const allTaskIds = tasks.map(t => t.id);
    setCollapsed(new Set(allTaskIds));
  };

  const treeData = buildTree();

  return (
    <Card 
      title={
        <Space>
          <span>🌳 任务树形视图</span>
          {stats && (
            <Badge count={stats.total} style={{ backgroundColor: '#52c41a' }} />
          )}
        </Space>
      }
      style={{ height: '100%' }}
      extra={
        <Space wrap>
          <Input.Search
            placeholder="搜索任务"
            style={{ width: 200 }}
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
            allowClear
          />
          <Select
            placeholder="状态筛选"
            style={{ width: 120 }}
            value={statusFilter}
            onChange={setStatusFilter}
            options={[
              { label: '全部', value: 'all' },
              { label: '待执行', value: 'pending' },
              { label: '执行中', value: 'running' },
              { label: '已完成', value: 'completed' },
              { label: '失败', value: 'failed' },
            ]}
          />
          <Button 
            icon={<ExpandOutlined />} 
            onClick={handleExpandAll}
            title="展开全部"
            size="small"
          />
          <Button 
            icon={<CompressOutlined />} 
            onClick={handleCollapseAll}
            title="折叠全部"
            size="small"
          />
          <Button 
            icon={<ReloadOutlined />} 
            onClick={handleRefresh}
            loading={loading}
          >
            刷新
          </Button>
        </Space>
      }
    >
      <Spin spinning={loading} tip="加载任务数据中...">
        <div className="tree-visualization-container">
          {treeData.length > 0 ? (
            <div className="tree-content">
              {treeData.map(rootNode => renderTreeNode(rootNode, true, '', true))}
            </div>
          ) : (
            <div className="tree-empty">
              <div style={{ textAlign: 'center', padding: '60px 20px', color: '#999' }}>
                <div style={{ fontSize: '48px', marginBottom: '16px' }}>🌳</div>
                <div style={{ fontSize: '16px' }}>暂无任务数据</div>
                <div style={{ fontSize: '12px', marginTop: '8px' }}>
                  创建一个ROOT任务开始工作吧！
                </div>
              </div>
            </div>
          )}
        </div>
      </Spin>
    </Card>
  );
};

export default TreeVisualization;
