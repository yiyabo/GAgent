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

  // 处理统计数据格式差异
  const processStats = (rawStats: any) => {
    if (!rawStats) return { total: 0, pending: 0, running: 0, completed: 0, failed: 0 };
    
    // 如果是新格式 (后端API返回的格式)
    if (rawStats.by_status) {
      return {
        total: rawStats.total || 0,
        pending: rawStats.by_status.pending || 0,
        running: rawStats.by_status.running || 0,
        completed: rawStats.by_status.done || rawStats.by_status.completed || 0,
        failed: rawStats.by_status.failed || 0,
      };
    }
    
    // 如果是旧格式
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
      {/* 页面标题 */}
      <div className="content-header">
        <Title level={3} style={{ margin: 0 }}>
          📊 控制台
        </Title>
        <Text type="secondary">
          AI 智能任务编排系统 - 实时监控和管理
        </Text>
      </div>

      <div className="content-body">
        {/* 系统状态卡片 */}
        <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
          <Col xs={24} sm={12} md={6}>
            <Card>
              <Statistic
                title="任务总数"
                value={stats.total}
                prefix={<DatabaseOutlined />}
                valueStyle={{ color: '#1890ff' }}
              />
            </Card>
          </Col>
          
          <Col xs={24} sm={12} md={6}>
            <Card>
              <Statistic
                title="等待执行"
                value={stats.pending}
                prefix={<ClockCircleOutlined />}
                valueStyle={{ color: '#faad14' }}
              />
            </Card>
          </Col>
          
          <Col xs={24} sm={12} md={6}>
            <Card>
              <Statistic
                title="正在执行"
                value={stats.running}
                prefix={<PlayCircleOutlined />}
                valueStyle={{ color: '#52c41a' }}
              />
            </Card>
          </Col>
          
          <Col xs={24} sm={12} md={6}>
            <Card>
              <Statistic
                title="已完成"
                value={stats.completed}
                prefix={<CheckCircleOutlined />}
                valueStyle={{ color: '#52c41a' }}
              />
              {stats.failed > 0 && (
                <div style={{ marginTop: 8 }}>
                  <Text type="danger">
                    <ExclamationCircleOutlined /> {stats.failed} 个失败
                  </Text>
                </div>
              )}
            </Card>
          </Col>
        </Row>

        {/* 系统监控 */}
        <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
          <Col xs={24} lg={12}>
            <Card title="🔥 系统状态" size="small">
              <Space direction="vertical" style={{ width: '100%' }} size="middle">
                <div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
                    <Text>API 连接状态</Text>
                    <Text strong style={{ color: systemStatus.api_connected ? '#52c41a' : '#ff4d4f' }}>
                      {systemStatus.api_connected ? '已连接' : '断开'}
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
                    <Text>数据库状态</Text>
                    <Text strong style={{ 
                      color: systemStatus.database_status === 'connected' ? '#52c41a' : '#ff4d4f' 
                    }}>
                      {systemStatus.database_status === 'connected' ? '正常' : '异常'}
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
                    <Text>系统负载</Text>
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
            <Card title="📈 API 调用统计" size="small">
              <Space direction="vertical" style={{ width: '100%' }} size="middle">
                <Statistic
                  title="每分钟调用次数"
                  value={systemStatus.system_load.api_calls_per_minute}
                  suffix="次/分钟"
                  prefix={<RobotOutlined />}
                />
                
                <div>
                  <Text type="secondary">
                    💡 系统正在使用真实的 GLM API，无 Mock 模式
                  </Text>
                </div>

                <div>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    内存使用: {systemStatus.system_load.memory}%
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

        {/* DAG 可视化 */}
        <Row gutter={[16, 16]}>
          <Col span={24}>
            <Card 
              title="🎯 任务编排图" 
              size="small"
              extra={
                <Button
                  onClick={async () => {
                    console.log('🔄 手动测试 PlanTree API...');
                    if (!currentPlanId) {
                      message.warning('当前尚未绑定计划，无法请求 PlanTree 数据。');
                      return;
                    }
                    try {
                      const response = await fetch(`${ENV.API_BASE_URL}/plans/${currentPlanId}/tree`);
                      if (!response.ok) {
                        throw new Error(`PlanTree 请求失败: ${response.status}`);
                      }
                      const data = await response.json();
                      console.log('✅ PlanTree 节点数:', Object.keys(data.nodes || {}).length);
                    } catch (error) {
                      console.error('❌ PlanTree API 调试失败:', error);
                      message.error('PlanTree API 调试失败，请检查后端服务。');
                    }
                  }}
                >
                  调试API
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
