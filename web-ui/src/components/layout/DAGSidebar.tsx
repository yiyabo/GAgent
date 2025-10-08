import React, { useEffect, useMemo, useState } from 'react';
import { Card, Typography, Button, Space, Badge, Tooltip, Divider, Select, Empty } from 'antd';
import {
  NodeIndexOutlined,
  FullscreenOutlined,
  SettingOutlined,
  ReloadOutlined,
  EyeOutlined,
  EyeInvisibleOutlined,
} from '@ant-design/icons';
import { usePlanTitles, usePlanTasks } from '@hooks/usePlans';
import PlanDagVisualization from '@components/dag/PlanDagVisualization';
import type { PlanTaskNode } from '@/types';
import { useTasksStore } from '@store/tasks';
import { useChatStore } from '@store/chat';

const { Title, Text } = Typography;

const DAGSidebar: React.FC = () => {
  const { setCurrentPlan } = useTasksStore((state) => ({
    setCurrentPlan: state.setCurrentPlan,
  }));
  const { setChatContext, currentWorkflowId, currentSession } = useChatStore((state) => ({
    setChatContext: state.setChatContext,
    currentWorkflowId: state.currentWorkflowId,
    currentSession: state.currentSession,
  }));
  const [selectedTask, setSelectedTask] = useState<PlanTaskNode | null>(null);
  const [dagVisible, setDagVisible] = useState(true);

  // 稳定化session_id以避免无限循环
  const sessionId = currentSession?.session_id;
  
  const workflowFilters = useMemo(
    () => ({
      workflowId: currentWorkflowId || undefined,
      sessionId: sessionId || undefined,
    }),
    [currentWorkflowId, sessionId]
  );

  // 不再需要planTitles，因为一个对话只对应一个ROOT任务
  const [selectedPlan, setSelectedPlan] = useState<string | undefined>();
  const {
    data: planTasks = [],
    isFetching: planTasksLoading,
    refetch: refetchTasks,
  } = usePlanTasks(workflowFilters);

  // 移除错误的useCallback包装

  // 监听全局任务更新事件，自动刷新侧栏DAG数据
  useEffect(() => {
    const handleTasksUpdated = (event: CustomEvent) => {
      console.log('📣 DAGSidebar 收到任务更新事件:', event.detail);
      refetchTasks();
    };
    window.addEventListener('tasksUpdated', handleTasksUpdated as EventListener);
    return () => window.removeEventListener('tasksUpdated', handleTasksUpdated as EventListener);
  }, [refetchTasks]);

  useEffect(() => {
    // 核心逻辑：一个对话只对应一个ROOT任务
    if (planTasks.length > 0) {
      // 查找当前会话的ROOT任务（应该只有一个）
      const rootTask = planTasks.find((task) => task.task_type === 'root');
      if (rootTask && rootTask.name !== selectedTask?.name) {
        // 设置为当前会话的唯一ROOT任务
        setSelectedPlan(rootTask.name);
        setSelectedTask(rootTask);
        // 使用setTimeout异步调用，避免同步状态更新冲突
        setTimeout(() => {
          setCurrentPlan(rootTask.name);
          setChatContext({
            planTitle: rootTask.name,
            taskId: rootTask.id,
            taskName: rootTask.name,
          });
        }, 0);
      }
    } else if (selectedTask !== null) {
      setSelectedTask(null);
      setSelectedPlan(undefined);
      setTimeout(() => {
        setCurrentPlan(null);
      }, 0);
    }
  }, [planTasks, selectedTask]); // 只依赖planTasks，不再依赖planTitles

  const stats = useMemo(() => {
    if (!planTasks || planTasks.length === 0) {
      return {
        total: 0,
        pending: 0,
        running: 0,
        completed: 0,
        failed: 0,
      };
    }
    return {
      total: planTasks.length,
      pending: planTasks.filter((task) => task.status === 'pending').length,
      running: planTasks.filter((task) => task.status === 'running').length,
      completed: planTasks.filter((task) => task.status === 'completed').length,
      failed: planTasks.filter((task) => task.status === 'failed').length,
    };
  }, [planTasks]);

  const handleRefresh = () => {
    refetchTasks();
  };

  return (
    <div style={{ 
      height: '100%', 
      display: 'flex', 
      flexDirection: 'column',
      background: 'white',
    }}>
      {/* 头部 */}
      <div style={{ 
        padding: '16px',
        borderBottom: '1px solid #f0f0f0',
        background: 'white',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <NodeIndexOutlined style={{ color: '#1890ff', fontSize: 18 }} />
            <Title level={5} style={{ margin: 0 }}>
              任务图谱
            </Title>
          </div>
          
          <Space size={4}>
            <Tooltip title={dagVisible ? '隐藏图谱' : '显示图谱'}>
              <Button
                type="text"
                size="small"
                icon={dagVisible ? <EyeInvisibleOutlined /> : <EyeOutlined />}
                onClick={() => setDagVisible(!dagVisible)}
              />
            </Tooltip>
            
            <Tooltip title="全屏查看">
              <Button
                type="text"
                size="small"
                icon={<FullscreenOutlined />}
              />
            </Tooltip>
            
            <Tooltip title="设置">
              <Button
                type="text"
                size="small"
                icon={<SettingOutlined />}
              />
            </Tooltip>
          </Space>
        </div>

        {/* 统计信息 */}
        <Space size={16} wrap>
          <Badge count={stats.total} size="small" offset={[8, -2]}>
            <Text type="secondary" style={{ fontSize: 12 }}>总任务</Text>
          </Badge>
          <Badge count={stats.running} size="small" color="blue" offset={[8, -2]}>
            <Text type="secondary" style={{ fontSize: 12 }}>运行中</Text>
          </Badge>
          <Badge count={stats.completed} size="small" color="green" offset={[8, -2]}>
            <Text type="secondary" style={{ fontSize: 12 }}>已完成</Text>
          </Badge>
          {stats.failed > 0 && (
            <Badge count={stats.failed} size="small" color="red" offset={[8, -2]}>
              <Text type="secondary" style={{ fontSize: 12 }}>失败</Text>
            </Badge>
          )}
        </Space>

        <Space direction="vertical" size={8} style={{ width: '100%', marginTop: 12 }}>
          <Text type="secondary" style={{ fontSize: 11 }}>当前ROOT任务：</Text>
          <div 
            style={{ 
              padding: '6px 12px',
              background: '#f5f5f5',
              border: '1px solid #d9d9d9',
              borderRadius: '6px',
              fontSize: '14px',
              color: selectedPlan ? '#262626' : '#8c8c8c'
            }}
          >
            {selectedPlan || '暂无ROOT任务'}
          </div>
          <Text type="secondary" style={{ fontSize: 10, color: '#999' }}>
            💡 一个对话对应一个ROOT任务，所有子任务都从此展开
          </Text>
        </Space>
      </div>

      {/* DAG可视化区域 */}
      {dagVisible && (
        <div style={{ 
          flex: 1,
          padding: '8px',
          overflow: 'hidden',
        }}>
          {planTasks && planTasks.length > 0 ? (
            <PlanDagVisualization
              tasks={planTasks}
              loading={planTasksLoading}
              onSelectTask={(task) => {
                setSelectedTask(task);
                if (task) {
                  const rootName = selectedPlan || planTasks.find((t) => t.task_type === 'root')?.name || null;
                  setChatContext({
                    planTitle: rootName,
                    taskId: task.id,
                    taskName: task.name,
                  });
                } else {
                  setChatContext({ taskId: null, taskName: null });
                }
              }}
              height="100%"
            />
          ) : (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={
                planTasksLoading
                  ? '加载任务中...'
                  : (currentWorkflowId || currentSession?.session_id)
                    ? '当前会话尚无任务'
                    : '请先开始一个对话或创建工作流'
              }
            />
          )}
        </div>
      )}

      {/* 选中任务详情 */}
      {selectedTask && (
        <>
          <Divider style={{ margin: '8px 0' }} />
          <div style={{ 
            padding: '12px 16px',
            background: '#f8f9fa',
            borderTop: '1px solid #f0f0f0',
          }}>
            <Text strong style={{ fontSize: 12, color: '#666' }}>
              选中任务
            </Text>
            <div style={{ marginTop: 8 }}>
              <Text style={{ fontSize: 13, display: 'block', marginBottom: 4 }}>
                {selectedTask.name}
              </Text>
              <Space size={8}>
                <Badge 
                  status={
                    selectedTask.status === 'completed' ? 'success' :
                    selectedTask.status === 'running' ? 'processing' :
                    selectedTask.status === 'failed' ? 'error' : 'default'
                  }
                  text={
                    selectedTask.status === 'completed' ? '已完成' :
                    selectedTask.status === 'running' ? '运行中' :
                    selectedTask.status === 'failed' ? '失败' : '等待中'
                  }
                />
                <Text type="secondary" style={{ fontSize: 11 }}>
                  {selectedTask.task_type === 'root' ? '根任务' :
                   selectedTask.task_type === 'composite' ? '复合任务' : '原子任务'}
                </Text>
              </Space>
            </div>
          </div>
        </>
      )}

      {/* 底部操作 */}
      <div style={{ 
        padding: '12px 16px',
        borderTop: '1px solid #f0f0f0',
        background: '#fafafa',
      }}>
        <Space size={8} wrap style={{ width: '100%', justifyContent: 'center' }}>
          <Button 
            size="small" 
            icon={<ReloadOutlined />}
            onClick={handleRefresh}
            loading={planTasksLoading}
          >
            刷新
          </Button>
          <Button size="small" icon={<FullscreenOutlined />}>
            全屏
          </Button>
        </Space>
        
        <div style={{ textAlign: 'center', marginTop: 8 }}>
          <Text type="secondary" style={{ fontSize: 11 }}>
            实时任务可视化
          </Text>
        </div>
      </div>
    </div>
  );
};

export default DAGSidebar;
