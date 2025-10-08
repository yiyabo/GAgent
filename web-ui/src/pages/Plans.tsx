import React, { useEffect, useMemo, useState } from 'react';
import { Typography, Card, Space, Select, Button, Empty, Tag, Descriptions, Tooltip, Badge } from 'antd';
import { ReloadOutlined, ApartmentOutlined } from '@ant-design/icons';
import { usePlanTitles, usePlanTasks } from '@hooks/usePlans';
import { useChatStore } from '@store/chat';
import PlanDagVisualization from '@components/dag/PlanDagVisualization';
import type { PlanTaskNode } from '@/types';

const { Title, Text } = Typography;

const PlansPage: React.FC = () => {
  const { currentWorkflowId, currentSession } = useChatStore((state) => ({
    currentWorkflowId: state.currentWorkflowId,
    currentSession: state.currentSession,
  }));

  const sessionIdentifier = currentSession?.session_id ?? undefined;

  const { data: titles = [], isLoading: titlesLoading, refetch: refetchTitles } = usePlanTitles({
    workflowId: currentWorkflowId ?? undefined,
    sessionId: sessionIdentifier ?? undefined,
  });
  const [selectedTitle, setSelectedTitle] = useState<string | undefined>();
  const [selectedTask, setSelectedTask] = useState<PlanTaskNode | null>(null);

  useEffect(() => {
    if (!selectedTitle && titles.length > 0) {
      setSelectedTitle(titles[0]);
    }
  }, [titles, selectedTitle]);

  const {
    data: planTasks = [],
    isFetching: tasksLoading,
    refetch: refetchTasks,
  } = usePlanTasks({
    planTitle: selectedTitle,
    workflowId: currentWorkflowId ?? undefined,
    sessionId: sessionIdentifier ?? undefined,
  });

  // 统计信息
  const planStats = useMemo(() => {
    if (!planTasks || planTasks.length === 0) {
      return null;
    }
    return {
      total: planTasks.length,
      pending: planTasks.filter((task) => task.status === 'pending').length,
      running: planTasks.filter((task) => task.status === 'running').length,
      completed: planTasks.filter((task) => task.status === 'completed').length,
      failed: planTasks.filter((task) => task.status === 'failed').length,
      root: planTasks.filter((task) => task.task_type === 'root').length,
      composite: planTasks.filter((task) => task.task_type === 'composite').length,
      atomic: planTasks.filter((task) => task.task_type === 'atomic').length,
    };
  }, [planTasks]);

  const handlePlanChange = (value: string) => {
    setSelectedTitle(value);
    setSelectedTask(null);
  };

  const handleRefresh = () => {
    Promise.all([refetchTitles(), refetchTasks()]);
  };

  return (
    <div>
      <div className="content-header">
        <Title level={3} style={{ margin: 0, display: 'flex', alignItems: 'center', gap: 8 }}>
          <ApartmentOutlined />
          计划与DAG可视化
        </Title>
        <Space>
          <Tooltip title="刷新计划列表与任务">
            <Button icon={<ReloadOutlined />} onClick={handleRefresh}>
              刷新
            </Button>
          </Tooltip>
        </Space>
      </div>

      <div className="content-body" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        <Card>
          <Space direction="vertical" style={{ width: '100%' }} size="large">
            <Space wrap>
              <Text strong>选择计划：</Text>
              <Select
                style={{ minWidth: 260 }}
                placeholder={titlesLoading ? '加载计划中...' : '请选择计划'}
                loading={titlesLoading}
                value={selectedTitle}
                onChange={handlePlanChange}
                options={titles.map((title) => ({ label: title, value: title }))}
              />
              {planStats && (
                <Space size="middle">
                  <Tag color="blue">任务数 {planStats.total}</Tag>
                  <Tag color="green">已完成 {planStats.completed}</Tag>
                  <Tag color="gold">待处理 {planStats.pending}</Tag>
                  {planStats.running > 0 && <Tag color="cyan">进行中 {planStats.running}</Tag>}
                  {planStats.failed > 0 && <Tag color="red">失败 {planStats.failed}</Tag>}
                </Space>
              )}
            </Space>

            {selectedTitle ? (
              <PlanDagVisualization
                tasks={planTasks}
                loading={tasksLoading}
                onSelectTask={setSelectedTask}
                height={520}
              />
            ) : (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description={titlesLoading ? '加载中...' : '暂无可用计划，请先通过聊天或CLI创建计划'}
              />
            )}
          </Space>
        </Card>

        <Card title="任务详情" bordered={false}>
          {selectedTask ? (
            <Descriptions column={2} size="small" bordered>
              <Descriptions.Item label="任务名称" span={2}>
                {selectedTask.name}
              </Descriptions.Item>
              <Descriptions.Item label="任务类型">
                <Tag color={
                  selectedTask.task_type === 'root'
                    ? 'purple'
                    : selectedTask.task_type === 'composite'
                    ? 'blue'
                    : 'green'
                }>
                  {selectedTask.task_type === 'root'
                    ? '根任务'
                    : selectedTask.task_type === 'composite'
                    ? '复合任务'
                    : '原子任务'}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="状态">
                <Badge
                  status={
                    selectedTask.status === 'completed'
                      ? 'success'
                      : selectedTask.status === 'running'
                      ? 'processing'
                      : selectedTask.status === 'failed'
                      ? 'error'
                      : 'default'
                  }
                  text={selectedTask.status || '未知'}
                />
              </Descriptions.Item>
              <Descriptions.Item label="优先级">
                {selectedTask.priority ?? '未设置'}
              </Descriptions.Item>
              <Descriptions.Item label="层级">
                {selectedTask.depth ?? 0}
              </Descriptions.Item>
              <Descriptions.Item label="任务ID">{selectedTask.id}</Descriptions.Item>
              <Descriptions.Item label="父任务">
                {selectedTask.parent_id ? `#${selectedTask.parent_id}` : '无'}
              </Descriptions.Item>
            </Descriptions>
          ) : (
            <Text type="secondary">在图中选择任务节点，可查看详细信息。</Text>
          )}
        </Card>
      </div>
    </div>
  );
};

export default PlansPage;
