import React, { useEffect, useMemo, useState } from 'react';
import { Card, Typography, Button, Space, Badge, Tooltip, Select, Empty } from 'antd';
import {
  NodeIndexOutlined,
  FullscreenOutlined,
  SettingOutlined,
  ReloadOutlined,
  EyeOutlined,
  EyeInvisibleOutlined,
} from '@ant-design/icons';
import { usePlanTasks } from '@hooks/usePlans';
import PlanTreeVisualization from '@components/dag/PlanTreeVisualization';
import type { PlanSyncEventDetail, PlanTaskNode } from '@/types';
import { useTasksStore } from '@store/tasks';
import { useChatStore } from '@store/chat';
import { shouldHandlePlanSyncEvent } from '@utils/planSyncEvents';

const { Title, Text } = Typography;

const DAGSidebar: React.FC = () => {
  const { setCurrentPlan, setTasks, openTaskDrawer, closeTaskDrawer, selectedTaskId } = useTasksStore((state) => ({
    setCurrentPlan: state.setCurrentPlan,
    setTasks: state.setTasks,
    openTaskDrawer: state.openTaskDrawer,
    closeTaskDrawer: state.closeTaskDrawer,
    selectedTaskId: state.selectedTaskId,
  }));
  const { setChatContext, currentWorkflowId, currentSession, currentPlanId, currentPlanTitle } =
    useChatStore((state) => ({
      setChatContext: state.setChatContext,
      currentWorkflowId: state.currentWorkflowId,
      currentSession: state.currentSession,
      currentPlanId: state.currentPlanId,
      currentPlanTitle: state.currentPlanTitle,
    }));
  const [dagVisible, setDagVisible] = useState(true);
  const [rootTaskId, setRootTaskId] = useState<number | null>(null);
  const [selectedPlanTitle, setSelectedPlanTitle] = useState<string | undefined>(
    currentPlanTitle ?? undefined
  );

  // 稳定化session_id以避免无限循环
  const sessionId = currentSession?.session_id;
  
  const {
    data: planTasks = [],
    isFetching: planTasksLoading,
    refetch: refetchTasks,
  } = usePlanTasks({ planId: currentPlanId ?? undefined });

  // 移除错误的useCallback包装

  // 监听全局任务更新事件，自动刷新侧栏DAG数据
  useEffect(() => {
    const handleTasksUpdated = (event: CustomEvent<PlanSyncEventDetail>) => {
      const detail = event.detail;
      if (
        detail?.type === 'plan_deleted' &&
        detail.plan_id != null &&
        detail.plan_id === (currentPlanId ?? null)
      ) {
        setTasks([]);
        closeTaskDrawer();
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
      refetchTasks();
      window.setTimeout(() => {
        refetchTasks();
      }, 800);
    };
    window.addEventListener('tasksUpdated', handleTasksUpdated as EventListener);
    return () => window.removeEventListener('tasksUpdated', handleTasksUpdated as EventListener);
  }, [closeTaskDrawer, currentPlanId, refetchTasks, setTasks]);

  useEffect(() => {
    setTasks(planTasks);
  }, [planTasks, setTasks]);

  useEffect(() => {
    if (planTasks.length > 0) {
      const rootTask = planTasks.find((task) => task.task_type === 'root');
      if (rootTask) {
        if (rootTaskId !== rootTask.id) {
          setRootTaskId(rootTask.id);
          setCurrentPlan(rootTask.name);
          setChatContext({
            planId: currentPlanId ?? undefined,
            planTitle: rootTask.name,
            taskId: rootTask.id,
            taskName: rootTask.name,
          });
        }
        setSelectedPlanTitle(rootTask.name);
      }
    } else if (rootTaskId !== null) {
      setRootTaskId(null);
      setSelectedPlanTitle(undefined);
      setCurrentPlan(null);
      setChatContext({
        planId: null,
        planTitle: null,
        taskId: null,
        taskName: null,
      });
      closeTaskDrawer();
    }
  }, [planTasks, rootTaskId, setCurrentPlan, setChatContext, currentPlanId, closeTaskDrawer]);

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
              color: selectedPlanTitle ? '#262626' : '#8c8c8c'
            }}
          >
            {selectedPlanTitle || '暂无ROOT任务'}
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
            <PlanTreeVisualization
              tasks={planTasks}
              loading={planTasksLoading}
              onSelectTask={(task) => {
                if (task) {
                  openTaskDrawer(task);
                  const rootName =
                    selectedPlanTitle ||
                    planTasks.find((t) => t.task_type === 'root')?.name ||
                    null;
                  setChatContext({
                    planTitle: rootName,
                    taskId: task.id,
                    taskName: task.name,
                  });
                } else {
                  closeTaskDrawer();
                  setChatContext({ taskId: null, taskName: null });
                }
              }}
              selectedTaskId={selectedTaskId ?? undefined}
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
