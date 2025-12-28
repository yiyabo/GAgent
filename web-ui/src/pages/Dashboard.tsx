import React from 'react';
import { Row, Col, Card, Statistic, Progress, Space, Typography, Button, message } from 'antd';
import {
  PlayCircleOutlined,
  CheckCircleOutlined,
  ExclamationCircleOutlined,
  ClockCircleOutlined,
  RobotOutlined,
  DatabaseOutlined,
} from '@ant-design/icons';
import { useSystemStore } from '@store/system';
import { useTasksStore } from '@store/tasks';
import { usePlanTasks } from '@hooks/usePlans';
import { useChatStore } from '@store/chat';
import TreeVisualization from '@components/dag/TreeVisualization';
import { ENV } from '@/config/env';

const { Title, Text } = Typography;

const Dashboard: React.FC = () => {
  const { systemStatus } = useSystemStore();
  const { tasks, getTaskStats } = useTasksStore();
  const { currentWorkflowId, currentSession, currentPlanId } = useChatStore((state) => ({
    currentWorkflowId: state.currentWorkflowId,
    currentSession: state.currentSession,
    currentPlanId: state.currentPlanId,
  }));

  const { data: planTasks = [] } = usePlanTasks({ planId: currentPlanId ?? undefined });

  // Process stats format differences
  const processStats = (rawStats: any) => {
    if (!rawStats) return { total: 0, pending: 0, running: 0, completed: 0, failed: 0 };
    
    // New format (from backend API)
    if (rawStats.by_status) {
      return {
        total: rawStats.total || 0,
        pending: rawStats.by_status.pending || 0,
        running: rawStats.by_status.running || 0,
        completed: rawStats.by_status.done || rawStats.by_status.completed || 0,
        failed: rawStats.by_status.failed || 0,
      };
    }
    
    // Old format
    return {
      total: rawStats.total || 0,
      pending: rawStats.pending || 0,
      running: rawStats.running || 0,
      completed: rawStats.completed || 0,
      failed: rawStats.failed || 0,
    };
  };

  const statsSource = planTasks.length > 0
    ? {
        total: planTasks.length,
        pending: planTasks.filter((task) => task.status === 'pending').length,
        running: planTasks.filter((task) => task.status === 'running').length,
        completed: planTasks.filter((task) => task.status === 'completed').length,
        failed: planTasks.filter((task) => task.status === 'failed').length,
      }
    : getTaskStats();
  const stats = processStats(statsSource);

  return (
    <div>
      {/* Page title */}
      <div className="content-header">
        <Title level={3} style={{ margin: 0, fontSize: 18, color: 'var(--text-primary)' }}>
          Dashboard
        </Title>
        <Text type="secondary" style={{ fontSize: 12 }}>
          System Overview
        </Text>
      </div>

      <div className="content-body">
        {/* System status cards - minimalist design */}
        <Row gutter={[16, 16]} style={{ marginBottom: 20 }}>
          <Col xs={24} sm={12} md={6}>
            <Card
              size="small"
              style={{
                borderRadius: 'var(--radius-md)',
                border: '1px solid var(--border-color)',
              }}
              bodyStyle={{ padding: '14px 16px' }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <DatabaseOutlined style={{ color: 'var(--text-secondary)', fontSize: 16 }} />
                <div>
                  <Text type="secondary" style={{ fontSize: 11 }}>Tasks</Text>
                  <div style={{ fontSize: 20, fontWeight: 500, color: 'var(--text-primary)' }}>
                    {stats.total}
                  </div>
                </div>
              </div>
            </Card>
          </Col>
          
          <Col xs={24} sm={12} md={6}>
            <Card
              size="small"
              style={{
                borderRadius: 'var(--radius-md)',
                border: '1px solid var(--border-color)',
              }}
              bodyStyle={{ padding: '14px 16px' }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <ClockCircleOutlined style={{ color: 'var(--warning-color)', fontSize: 16 }} />
                <div>
                  <Text type="secondary" style={{ fontSize: 11 }}>Pending</Text>
                  <div style={{ fontSize: 20, fontWeight: 500, color: 'var(--warning-color)' }}>
                    {stats.pending}
                  </div>
                </div>
              </div>
            </Card>
          </Col>
          
          <Col xs={24} sm={12} md={6}>
            <Card
              size="small"
              style={{
                borderRadius: 'var(--radius-md)',
                border: '1px solid var(--border-color)',
              }}
              bodyStyle={{ padding: '14px 16px' }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <PlayCircleOutlined style={{ color: 'var(--info-color)', fontSize: 16 }} />
                <div>
                  <Text type="secondary" style={{ fontSize: 11 }}>Running</Text>
                  <div style={{ fontSize: 20, fontWeight: 500, color: 'var(--info-color)' }}>
                    {stats.running}
                  </div>
                </div>
              </div>
            </Card>
          </Col>
          
          <Col xs={24} sm={12} md={6}>
            <Card
              size="small"
              style={{
                borderRadius: 'var(--radius-md)',
                border: '1px solid var(--border-color)',
              }}
              bodyStyle={{ padding: '14px 16px' }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <CheckCircleOutlined style={{ color: 'var(--success-color)', fontSize: 16 }} />
                <div>
                  <Text type="secondary" style={{ fontSize: 11 }}>Completed</Text>
                  <div style={{ fontSize: 20, fontWeight: 500, color: 'var(--success-color)' }}>
                    {stats.completed}
                  </div>
                  {stats.failed > 0 && (
                    <Text type="danger" style={{ fontSize: 11, marginTop: 2 }}>
                      {stats.failed} Failed
                    </Text>
                  )}
                </div>
              </div>
            </Card>
          </Col>
        </Row>

        {/* System monitoring */}
        <Row gutter={[16, 16]} style={{ marginBottom: 20 }}>
          <Col xs={24} lg={12}>
            <Card
              title="System Status"
              size="small"
              style={{
                borderRadius: 'var(--radius-md)',
                border: '1px solid var(--border-color)',
              }}
              bodyStyle={{ padding: '16px' }}
            >
              <Space direction="vertical" style={{ width: '100%' }} size="middle">
                <div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
                    <Text>API Connection</Text>
                    <Text strong style={{ color: systemStatus.api_connected ? '#52c41a' : '#ff4d4f' }}>
                      {systemStatus.api_connected ? 'Connected' : 'Disconnected'}
                    </Text>
                  </div>
                  <Progress
                    percent={systemStatus.api_connected ? 100 : 0}
                    status={systemStatus.api_connected ? 'success' : 'exception'}
                    showInfo={false}
                    size="small"
                  />
                </div>

                <div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
                    <Text>Database Status</Text>
                    <Text strong style={{
                      color: systemStatus.database_status === 'connected' ? '#52c41a' : '#ff4d4f'
                    }}>
                      {systemStatus.database_status === 'connected' ? 'Normal' : 'Error'}
                    </Text>
                  </div>
                  <Progress
                    percent={systemStatus.database_status === 'connected' ? 100 : 0}
                    status={systemStatus.database_status === 'connected' ? 'success' : 'exception'}
                    showInfo={false}
                    size="small"
                  />
                </div>

                <div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
                    <Text>System Load</Text>
                    <Text strong>
                      {systemStatus.system_load.cpu}% CPU
                    </Text>
                  </div>
                  <Progress
                    percent={systemStatus.system_load.cpu}
                    status={systemStatus.system_load.cpu > 80 ? 'exception' : 'success'}
                    showInfo={false}
                    size="small"
                  />
                </div>
              </Space>
            </Card>
          </Col>

          <Col xs={24} lg={12}>
            <Card
              title={
                <span style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  fontSize: 15,
                }}>
                  <span>📈</span>
                  <span style={{
                    background: 'var(--primary-gradient)',
                    WebkitBackgroundClip: 'text',
                    WebkitTextFillColor: 'transparent',
                  }}>API Call Statistics</span>
                </span>
              }
              size="small"
              style={{
                borderRadius: 'var(--radius-lg)',
                border: '1px solid var(--border-light)',
              }}
              bodyStyle={{ padding: '20px' }}
            >
              <Space direction="vertical" style={{ width: '100%' }} size="middle">
                <Statistic
                  title="Calls per Minute"
                  value={systemStatus.system_load.api_calls_per_minute}
                  suffix="calls/min"
                  prefix={<RobotOutlined />}
                />
                
                <div>
                  <Text type="secondary">
                    System is using real GLM API, no Mock mode
                  </Text>
                </div>

                <div>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    Memory Usage: {systemStatus.system_load.memory}%
                  </Text>
                  <Progress
                    percent={systemStatus.system_load.memory}
                    size="small"
                    showInfo={false}
                    style={{ marginTop: 4 }}
                  />
                </div>
              </Space>
            </Card>
          </Col>
        </Row>

        {/* DAG Visualization */}
        <Row gutter={[20, 20]}>
          <Col span={24}>
            <Card
              title={
                <span style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  fontSize: 15,
                }}>
                  <span>🎯</span>
                  <span style={{
                    background: 'var(--primary-gradient)',
                    WebkitBackgroundClip: 'text',
                    WebkitTextFillColor: 'transparent',
                  }}>Task Orchestration Graph</span>
                </span>
              }
              size="small"
              style={{
                borderRadius: 'var(--radius-lg)',
                border: '1px solid var(--border-light)',
              }}
              bodyStyle={{ padding: 0 }}
              extra={
                <Button
                  onClick={async () => {
                    console.log('Testing PlanTree API...');
                    if (!currentPlanId) {
                      message.warning('No plan bound yet, cannot request PlanTree data.');
                      return;
                    }
                    try {
                      const response = await fetch(`${ENV.API_BASE_URL}/plans/${currentPlanId}/tree`);
                      if (!response.ok) {
                        throw new Error(`PlanTree request failed: ${response.status}`);
                      }
                      const data = await response.json();
                      console.log('PlanTree node count:', Object.keys(data.nodes || {}).length);
                    } catch (error) {
                      console.error('PlanTree API debug failed:', error);
                      message.error('PlanTree API debug failed, please check backend service.');
                    }
                  }}
                >
                  Debug API
                </Button>
              }
            >
              <TreeVisualization />
            
            </Card>
          </Col>
        </Row>
      </div>
    </div>
  );
};

export default Dashboard;
