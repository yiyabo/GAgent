import React, { useState, useEffect } from 'react';
import { Card, Typography, Button, Space, Badge, Tooltip, Divider, Switch, Spin } from 'antd';
import {
  NodeIndexOutlined,
  FullscreenOutlined,
  SettingOutlined,
  ReloadOutlined,
  EyeOutlined,
  EyeInvisibleOutlined,
} from '@ant-design/icons';
import { useTasksStore } from '@store/tasks';
import { useAllTasks, useTaskStats } from '@hooks/useTasks';
import DAGVisualization from '@components/dag/DAGVisualization';

const { Title, Text } = Typography;

const DAGSidebar: React.FC = () => {
  const { 
    dagNodes, 
    selectedTask,
    currentPlan 
  } = useTasksStore();

  const [dagVisible, setDagVisible] = useState(true);
  
  // 使用真实的任务数据
  const { isLoading: tasksLoading, refetch } = useAllTasks();
  const { data: statsData } = useTaskStats();
  
  // 处理不同的统计数据格式
  const stats = statsData ? {
    total: statsData.total,
    pending: (statsData as any).by_status?.pending || (statsData as any).pending || 0,
    running: (statsData as any).by_status?.running || (statsData as any).running || 0,
    completed: (statsData as any).by_status?.completed || (statsData as any).completed || 0,
    failed: (statsData as any).by_status?.failed || (statsData as any).failed || 0,
  } : {
    total: 0,
    pending: 0,
    running: 0,
    completed: 0,
    failed: 0,
  };

  const handleRefresh = () => {
    refetch();
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

        {currentPlan && (
          <div style={{ marginTop: 8 }}>
            <Text type="secondary" style={{ fontSize: 11 }}>
              当前计划: {currentPlan}
            </Text>
          </div>
        )}
      </div>

      {/* DAG可视化区域 */}
      {dagVisible && (
        <div style={{ 
          flex: 1,
          padding: '8px',
          overflow: 'hidden',
        }}>
          {dagNodes.length > 0 ? (
            <DAGVisualization 
              height="100%" 
              interactive={true}
              showToolbar={false}
            />
          ) : (
            <div style={{
              height: '100%',
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'center',
              color: '#999',
              padding: '20px',
              textAlign: 'center',
            }}>
              <NodeIndexOutlined style={{ fontSize: 48, marginBottom: 16, color: '#d9d9d9' }} />
              <Text type="secondary" style={{ fontSize: 14, marginBottom: 8 }}>
                暂无任务数据
              </Text>
              <Text type="secondary" style={{ fontSize: 12, lineHeight: 1.5 }}>
                在聊天中创建计划后<br />这里将显示任务结构图
              </Text>
            </div>
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
            loading={tasksLoading}
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
