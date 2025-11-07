import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Typography,
  Card,
  Space,
  Select,
  Button,
  Empty,
  Tag,
  Descriptions,
  Tooltip,
  Badge,
  Row,
  Col,
  List,
  Spin,
  Switch,
  Statistic,
} from 'antd';
import { ReloadOutlined, ApartmentOutlined } from '@ant-design/icons';
import { usePlanSummaries, usePlanTasks, usePlanExecutionSummary, usePlanResults } from '@hooks/usePlans';
import { useChatStore } from '@store/chat';
import PlanDagVisualization from '@components/dag/PlanDagVisualization';
import type { PlanResultItem, PlanSyncEventDetail, PlanTaskNode } from '@/types';
import { isPlanSyncEventDetail } from '@utils/planSyncEvents';

const { Title, Text, Paragraph } = Typography;

const PlansPage: React.FC = () => {
  const { currentWorkflowId, currentSession, currentPlanId, setChatContext } = useChatStore((state) => ({
    currentWorkflowId: state.currentWorkflowId,
    currentSession: state.currentSession,
    currentPlanId: state.currentPlanId,
    setChatContext: state.setChatContext,
  }));

  const sessionIdentifier = currentSession?.session_id ?? undefined;

  const {
    data: planSummaries = [],
    isLoading: summariesLoading,
    refetch: refetchSummaries,
  } = usePlanSummaries();
  const [selectedPlanId, setSelectedPlanId] = useState<number | undefined>(currentPlanId ?? undefined);
  const [selectedTask, setSelectedTask] = useState<PlanTaskNode | null>(null);
  const [onlyWithOutput, setOnlyWithOutput] = useState(true);
  const [selectedResultTaskId, setSelectedResultTaskId] = useState<number | null>(null);

  useEffect(() => {
    if (currentPlanId && currentPlanId !== selectedPlanId) {
      setSelectedPlanId(currentPlanId);
      return;
    }
    if (!selectedPlanId && planSummaries.length > 0) {
      setSelectedPlanId(planSummaries[0]?.id);
    }
  }, [planSummaries, selectedPlanId, currentPlanId]);

  const {
    data: planTasks = [],
    isFetching: tasksLoading,
    refetch: refetchTasks,
  } = usePlanTasks({
    planId: selectedPlanId ?? undefined,
  });

  const {
    data: executionSummary,
    isFetching: summaryLoading,
    refetch: refetchExecutionSummary,
  } = usePlanExecutionSummary(selectedPlanId ?? null);

  const {
    data: planResultsResponse,
    isFetching: resultsLoading,
    refetch: refetchPlanResults,
  } = usePlanResults({
    planId: selectedPlanId ?? undefined,
    onlyWithOutput,
  });

  const planResults: PlanResultItem[] = planResultsResponse?.items ?? [];

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
      skipped: planTasks.filter((task) => task.status === 'skipped').length,
      root: planTasks.filter((task) => task.task_type === 'root').length,
      composite: planTasks.filter((task) => task.task_type === 'composite').length,
      atomic: planTasks.filter((task) => task.task_type === 'atomic').length,
    };
  }, [planTasks]);

  const statusSummary = useMemo(() => {
    if (executionSummary) {
      return {
        total: executionSummary.total_tasks,
        pending: executionSummary.pending,
        running: executionSummary.running,
        completed: executionSummary.completed,
        failed: executionSummary.failed,
        skipped: executionSummary.skipped,
      };
    }
    if (planStats) {
      return {
        total: planStats.total,
        pending: planStats.pending,
        running: planStats.running,
        completed: planStats.completed,
        failed: planStats.failed,
        skipped: planStats.skipped ?? 0,
      };
    }
    return null;
  }, [executionSummary, planStats]);

  const selectedResult = useMemo(() => {
    if (selectedResultTaskId == null) {
      return null;
    }
    return planResults.find((item) => item.task_id === selectedResultTaskId) ?? null;
  }, [planResults, selectedResultTaskId]);

  const selectedResultTask = useMemo(() => {
    if (selectedResultTaskId == null) {
      return null;
    }
    return planTasks.find((task) => task.id === selectedResultTaskId) ?? null;
  }, [planTasks, selectedResultTaskId]);

  const renderStatusBadge = useCallback((status?: string | null) => {
    const normalized = (status ?? 'pending').toLowerCase();
    let badgeStatus: 'success' | 'processing' | 'error' | 'warning' | 'default' = 'default';
    switch (normalized) {
      case 'completed':
      case 'success':
        badgeStatus = 'success';
        break;
      case 'running':
        badgeStatus = 'processing';
        break;
      case 'failed':
        badgeStatus = 'error';
        break;
      case 'skipped':
        badgeStatus = 'warning';
        break;
      default:
        badgeStatus = 'default';
    }
    const label = status ?? 'pending';
    return <Badge status={badgeStatus} text={label} />;
  }, []);

  const handleResultsRefresh = useCallback(() => {
    void Promise.all([refetchPlanResults(), refetchExecutionSummary()]);
  }, [refetchExecutionSummary, refetchPlanResults]);

  const handleToggleOutput = useCallback((checked: boolean) => {
    setOnlyWithOutput(checked);
  }, []);

  useEffect(() => {
    const items = planResults;
    if (!items || items.length === 0) {
      if (selectedResultTaskId !== null) {
        setSelectedResultTaskId(null);
      }
      return;
    }
    if (
      selectedResultTaskId == null ||
      !items.some((item) => item.task_id === selectedResultTaskId)
    ) {
      setSelectedResultTaskId(items[0].task_id);
    }
  }, [planResults, selectedResultTaskId]);

  useEffect(() => {
    setSelectedResultTaskId(null);
  }, [selectedPlanId, onlyWithOutput]);

  const triggerPlanRefresh = useCallback(() => {
    void Promise.all([
      refetchTasks(),
      refetchExecutionSummary(),
      refetchPlanResults(),
    ]);
    window.setTimeout(() => {
      void Promise.all([
        refetchTasks(),
        refetchExecutionSummary(),
        refetchPlanResults(),
      ]);
    }, 800);
  }, [refetchExecutionSummary, refetchPlanResults, refetchTasks]);

  useEffect(() => {
    const handler = (event: Event) => {
      const detail = (event as CustomEvent<PlanSyncEventDetail>).detail;
      if (!isPlanSyncEventDetail(detail)) {
        return;
      }
      switch (detail.type) {
        case 'plan_created': {
          void refetchSummaries();
          if (detail.plan_id) {
            setSelectedPlanId(detail.plan_id);
            setSelectedTask(null);
            setSelectedResultTaskId(null);
            setChatContext({
              planId: detail.plan_id,
              planTitle: detail.plan_title ?? null,
            });
          }
          break;
        }
        case 'plan_deleted': {
          void refetchSummaries();
          if (detail.plan_id != null && detail.plan_id === selectedPlanId) {
            setSelectedPlanId(undefined);
            setSelectedTask(null);
            setSelectedResultTaskId(null);
            setChatContext({
              planId: null,
              planTitle: null,
            });
          }
          break;
        }
        case 'plan_updated': {
          void refetchSummaries();
          if (detail.plan_id == null || detail.plan_id === selectedPlanId) {
            triggerPlanRefresh();
          }
          break;
        }
        case 'task_changed':
        case 'plan_jobs_completed': {
          if (
            detail.plan_id != null &&
            selectedPlanId != null &&
            detail.plan_id !== selectedPlanId
          ) {
            return;
          }
          triggerPlanRefresh();
          break;
        }
        default:
          break;
      }
    };
    window.addEventListener('tasksUpdated', handler as EventListener);
    return () => {
      window.removeEventListener('tasksUpdated', handler as EventListener);
    };
  }, [
    refetchExecutionSummary,
    refetchPlanResults,
    refetchSummaries,
    refetchTasks,
    selectedPlanId,
    setChatContext,
    setSelectedResultTaskId,
    setSelectedTask,
    setSelectedPlanId,
    triggerPlanRefresh,
  ]);

  const handlePlanChange = (value: number) => {
    setSelectedPlanId(value);
    setSelectedTask(null);
    setSelectedResultTaskId(null);

    const picked = planSummaries.find((plan) => plan.id === value);
    setChatContext({
      planId: value,
      planTitle: picked?.title ?? null,
    });
  };

  useEffect(() => {
    if (selectedPlanId && planSummaries.length > 0) {
      const summary = planSummaries.find((plan) => plan.id === selectedPlanId);
      setChatContext({
        planId: selectedPlanId,
        planTitle: summary?.title ?? null,
      });
    }
  }, [selectedPlanId, planSummaries, setChatContext]);

  const handleRefresh = () => {
    void refetchSummaries();
    triggerPlanRefresh();
    window.setTimeout(() => {
      void refetchSummaries();
    }, 800);
  };

  const planOptions = planSummaries.map((plan) => ({
    label: plan.title,
    value: plan.id,
  }));

  const selectedPlanSummary = planSummaries.find((plan) => plan.id === selectedPlanId);

  const executionInfo = useMemo(() => {
    if (!selectedTask?.execution_result) {
      return null;
    }
    try {
      const parsed = JSON.parse(selectedTask.execution_result);
      if (parsed && typeof parsed === 'object') {
        return {
          status: typeof parsed.status === 'string' ? parsed.status : selectedTask.status,
          content: typeof parsed.content === 'string' ? parsed.content : '',
          notes: Array.isArray(parsed.notes)
            ? parsed.notes.map((note) => String(note)).filter((note) => note.trim().length > 0)
            : [],
          metadata: parsed.metadata && typeof parsed.metadata === 'object' ? parsed.metadata : {},
        };
      }
    } catch (error) {
      return {
        status: selectedTask.status,
        content: selectedTask.execution_result,
        notes: [],
        metadata: {},
        rawFallback: true,
      };
    }
    return null;
  }, [selectedTask]);

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
                placeholder={summariesLoading ? '加载计划中...' : '请选择计划'}
                loading={summariesLoading}
                value={selectedPlanId}
                onChange={handlePlanChange}
                options={planOptions}
              />
              {statusSummary && (
                <Space size="middle">
                  <Tag color="blue">任务数 {statusSummary.total}</Tag>
                  <Tag color="green">已完成 {statusSummary.completed}</Tag>
                  <Tag color="gold">待处理 {statusSummary.pending}</Tag>
                  {statusSummary.running > 0 && <Tag color="cyan">进行中 {statusSummary.running}</Tag>}
                  {statusSummary.failed > 0 && <Tag color="red">失败 {statusSummary.failed}</Tag>}
                  {statusSummary.skipped > 0 && <Tag color="volcano">已跳过 {statusSummary.skipped}</Tag>}
                </Space>
              )}
              {planStats && (
                <Space size="middle">
                  <Tag color="purple">根任务 {planStats.root}</Tag>
                  <Tag color="geekblue">复合任务 {planStats.composite}</Tag>
                  <Tag color="green">原子任务 {planStats.atomic}</Tag>
                </Space>
              )}
            </Space>

            {selectedPlanId ? (
              <PlanDagVisualization
                tasks={planTasks}
                loading={tasksLoading}
                onSelectTask={setSelectedTask}
                height={520}
              />
            ) : (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description={summariesLoading ? '加载中...' : '暂无可用计划，请先通过聊天或CLI创建计划'}
              />
            )}
          </Space>
        </Card>

        <Card
          title="计划执行结果"
          bordered={false}
          extra={
            <Space>
              <Tooltip title={onlyWithOutput ? '仅显示有输出的任务' : '显示全部任务'}>
                <Switch
                  size="small"
                  checked={onlyWithOutput}
                  onChange={handleToggleOutput}
                  checkedChildren="仅输出"
                  unCheckedChildren="全部"
                />
              </Tooltip>
              <Tooltip title="刷新执行结果">
                <Button icon={<ReloadOutlined />} onClick={handleResultsRefresh} />
              </Tooltip>
            </Space>
          }
        >
          {selectedPlanId ? (
            <Space direction="vertical" style={{ width: '100%' }} size="large">
              <Spin spinning={summaryLoading && !executionSummary}>
                {statusSummary ? (
                  <Row gutter={[16, 16]}>
                    <Col xs={12} sm={8} lg={4}>
                      <Statistic title="任务总数" value={statusSummary.total} />
                    </Col>
                    <Col xs={12} sm={8} lg={4}>
                      <Statistic title="已完成" value={statusSummary.completed} />
                    </Col>
                    <Col xs={12} sm={8} lg={4}>
                      <Statistic title="进行中" value={statusSummary.running} />
                    </Col>
                    <Col xs={12} sm={8} lg={4}>
                      <Statistic title="待处理" value={statusSummary.pending} />
                    </Col>
                    <Col xs={12} sm={8} lg={4}>
                      <Statistic title="失败" value={statusSummary.failed} valueStyle={{ color: statusSummary.failed > 0 ? '#ff4d4f' : undefined }} />
                    </Col>
                    <Col xs={12} sm={8} lg={4}>
                      <Statistic title="已跳过" value={statusSummary.skipped} />
                    </Col>
                  </Row>
                ) : (
                  <Text type="secondary">暂无统计信息，执行计划后再试。</Text>
                )}
              </Spin>
              <Row gutter={[16, 16]}>
                <Col xs={24} lg={12}>
                  <Spin spinning={resultsLoading}>
                    {planResults.length > 0 ? (
                      <List
                        size="small"
                        split={false}
                        dataSource={planResults}
                        rowKey={(item) => item.task_id}
                        renderItem={(item) => (
                          <List.Item
                            key={item.task_id}
                            onClick={() => setSelectedResultTaskId(item.task_id)}
                            style={{
                              cursor: 'pointer',
                              borderRadius: 8,
                              padding: 16,
                              marginBottom: 8,
                              border:
                                item.task_id === selectedResultTaskId
                                  ? '1px solid rgba(24, 144, 255, 0.6)'
                                  : '1px solid transparent',
                              backgroundColor:
                                item.task_id === selectedResultTaskId
                                  ? 'rgba(24, 144, 255, 0.08)'
                                  : 'transparent',
                              transition: 'background-color 0.2s ease',
                            }}
                          >
                            <List.Item.Meta
                              title={
                                <Space size={8} wrap>
                                  <Text strong>{item.name || `任务 #${item.task_id}`}</Text>
                                  {renderStatusBadge(item.status)}
                                </Space>
                              }
                              description={
                                <Space direction="vertical" size={2} style={{ width: '100%' }}>
                                  {item.content ? (
                                    <Text ellipsis={{ tooltip: item.content }}>{item.content}</Text>
                                  ) : (
                                    <Text type="secondary">暂无输出内容</Text>
                                  )}
                                  {item.notes && item.notes.length > 0 && (
                                    <Text type="secondary">
                                      {item.notes.map((note) => `· ${note}`).join(' ')}
                                    </Text>
                                  )}
                                </Space>
                              }
                            />
                          </List.Item>
                        )}
                      />
                    ) : (
                      <Empty
                        image={Empty.PRESENTED_IMAGE_SIMPLE}
                        description={
                          resultsLoading
                            ? '加载执行输出中...'
                            : onlyWithOutput
                            ? '暂无任务执行输出，尝试执行计划或显示全部任务'
                            : '当前计划尚无任务'
                        }
                      />
                    )}
                  </Spin>
                </Col>
                <Col xs={24} lg={12}>
                  <Spin spinning={resultsLoading && !selectedResult}>
                    {selectedResult ? (
                      <Descriptions column={1} size="small" bordered>
                        <Descriptions.Item label="任务名称">
                          {selectedResult.name ||
                            selectedResultTask?.name ||
                            `任务 #${selectedResult.task_id}`}
                        </Descriptions.Item>
                        <Descriptions.Item label="状态">
                          {renderStatusBadge(selectedResult.status ?? selectedResultTask?.status)}
                        </Descriptions.Item>
                        <Descriptions.Item label="任务ID">
                          #{selectedResult.task_id}
                        </Descriptions.Item>
                        {selectedResultTask?.parent_id && (
                          <Descriptions.Item label="父任务">
                            #{selectedResultTask.parent_id}
                          </Descriptions.Item>
                        )}
                        {selectedResultTask?.instruction && (
                          <Descriptions.Item label="任务指令">
                            <Paragraph
                              style={{ whiteSpace: 'pre-wrap', marginBottom: 0 }}
                              ellipsis={{ rows: 4, expandable: true, symbol: '展开' }}
                            >
                              {selectedResultTask.instruction}
                            </Paragraph>
                          </Descriptions.Item>
                        )}
                        <Descriptions.Item label="输出内容">
                          {selectedResult.content ? (
                            <Paragraph
                              style={{ whiteSpace: 'pre-wrap', marginBottom: 0 }}
                              copyable={{ text: selectedResult.content }}
                              ellipsis={{ rows: 6, expandable: true, symbol: '展开' }}
                            >
                              {selectedResult.content}
                            </Paragraph>
                          ) : (
                            <Text type="secondary">暂无输出内容</Text>
                          )}
                        </Descriptions.Item>
                        {selectedResult.notes && selectedResult.notes.length > 0 && (
                          <Descriptions.Item label="备注">
                            <Space direction="vertical" size={0}>
                              {selectedResult.notes.map((note, index) => (
                                <Text key={index} type="secondary">
                                  · {note}
                                </Text>
                              ))}
                            </Space>
                          </Descriptions.Item>
                        )}
                        {selectedResultTask?.dependencies &&
                          selectedResultTask.dependencies.length > 0 && (
                            <Descriptions.Item label="依赖任务">
                              {selectedResultTask.dependencies.map((dep) => `#${dep}`).join(', ')}
                            </Descriptions.Item>
                          )}
                        {selectedResult.metadata &&
                          Object.keys(selectedResult.metadata).length > 0 && (
                            <Descriptions.Item label="元数据">
                              <Space direction="vertical" size={0}>
                                {Object.entries(selectedResult.metadata).map(([key, value]) => (
                                  <Text key={key} type="secondary">
                                    {key}: {String(value)}
                                  </Text>
                                ))}
                              </Space>
                            </Descriptions.Item>
                          )}
                        {selectedResult.raw && (
                          <Descriptions.Item label="原始载荷">
                            <pre
                              style={{
                                background: '#f5f5f5',
                                padding: 12,
                                borderRadius: 6,
                                margin: 0,
                                maxHeight: 240,
                                overflow: 'auto',
                              }}
                            >
                              {JSON.stringify(selectedResult.raw, null, 2)}
                            </pre>
                          </Descriptions.Item>
                        )}
                      </Descriptions>
                    ) : (
                      <Empty
                        image={Empty.PRESENTED_IMAGE_SIMPLE}
                        description={
                          planResults.length === 0
                            ? '暂无执行输出'
                            : '请选择左侧任务查看执行详情'
                        }
                      />
                    )}
                  </Spin>
                </Col>
              </Row>
            </Space>
          ) : (
            <Empty description="请先选择计划" />
          )}
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
              <Descriptions.Item label="层级">
                {selectedTask.depth ?? 0}
              </Descriptions.Item>
              <Descriptions.Item label="任务ID">{selectedTask.id}</Descriptions.Item>
              <Descriptions.Item label="父任务">
                {selectedTask.parent_id ? `#${selectedTask.parent_id}` : '无'}
              </Descriptions.Item>
              {selectedTask.instruction && (
                <Descriptions.Item label="任务指令" span={2}>
                  <Text>{selectedTask.instruction}</Text>
                </Descriptions.Item>
              )}
              <Descriptions.Item label="执行结果" span={2}>
                {executionInfo ? (
                  <Space direction="vertical" size="small">
                    <Badge
                      status={
                        executionInfo.status === 'completed'
                          ? 'success'
                          : executionInfo.status === 'running'
                          ? 'processing'
                          : executionInfo.status === 'failed'
                          ? 'error'
                          : executionInfo.status === 'skipped'
                          ? 'warning'
                          : 'default'
                      }
                      text={executionInfo.status ?? '未知'}
                    />
                    {executionInfo.content && <Text>{executionInfo.content}</Text>}
                    {executionInfo.notes && executionInfo.notes.length > 0 && (
                      <Space direction="vertical" size={0}>
                        {executionInfo.notes.map((note, index) => (
                          <Text key={index} type="secondary">
                            · {note}
                          </Text>
                        ))}
                      </Space>
                    )}
                    {executionInfo.metadata && Object.keys(executionInfo.metadata).length > 0 && (
                      <Text type="secondary">
                        元数据：
                        {Object.entries(executionInfo.metadata)
                          .map(([key, value]) => `${key}=${String(value)}`)
                          .join(', ')}
                      </Text>
                    )}
                  </Space>
                ) : (
                  <Text type="secondary">尚未执行</Text>
                )}
              </Descriptions.Item>
              {selectedPlanSummary?.description && (
                <Descriptions.Item label="计划描述" span={2}>
                  {selectedPlanSummary.description}
                </Descriptions.Item>
              )}
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
