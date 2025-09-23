import React from 'react';
import { Row, Col, Card, Statistic, Progress, Space, Typography, Button } from 'antd';
import {
  PlayCircleOutlined,
  CheckCircleOutlined,
  ExclamationCircleOutlined,
  ClockCircleOutlined,
  RobotOutlined,
  DatabaseOutlined,
} from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import { useSystemStore } from '@store/system';
import { useTasksStore } from '@store/tasks';
import { tasksApi } from '@api/tasks';
import DAGVisualization from '@components/dag/DAGVisualization';

const { Title, Text } = Typography;

const Dashboard: React.FC = () => {
  const { systemStatus } = useSystemStore();
  const { tasks, getTaskStats } = useTasksStore();

  // 获取任务统计数据
  const { data: taskStats, isLoading: statsLoading } = useQuery({
    queryKey: ['task-stats'],
    queryFn: tasksApi.getTaskStats,
    refetchInterval: 10000, // 10秒刷新一次
  });

  const stats = taskStats || getTaskStats();

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
            <Card title="🎯 任务编排图" size="small">
              {tasks.length > 0 ? (
                <DAGVisualization height={500} />
              ) : (
                <div style={{ 
                  height: 400, 
                  display: 'flex', 
                  flexDirection: 'column', 
                  alignItems: 'center', 
                  justifyContent: 'center',
                  color: '#999'
                }}>
                  <DatabaseOutlined style={{ fontSize: 48, marginBottom: 16 }} />
                  <Title level={4} type="secondary">
                    暂无任务数据
                  </Title>
                  <Text type="secondary">
                    创建您的第一个计划来开始任务编排
                  </Text>
                  <Button 
                    type="primary" 
                    style={{ marginTop: 16 }}
                    onClick={() => {
                      // 这里可以跳转到计划创建页面
                      console.log('Navigate to plans page');
                    }}
                  >
                    创建计划
                  </Button>
                </div>
              )}
            </Card>
          </Col>
        </Row>
      </div>
    </div>
  );
};

export default Dashboard;
