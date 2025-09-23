import React from 'react';
import { Typography } from 'antd';
import DAGVisualization from '@components/dag/DAGVisualization';

const { Title } = Typography;

const TasksPage: React.FC = () => {
  return (
    <div>
      <div className="content-header">
        <Title level={3} style={{ margin: 0 }}>
          📝 任务管理
        </Title>
      </div>
      <div className="content-body">
        <DAGVisualization height="calc(100vh - 200px)" />
      </div>
    </div>
  );
};

export default TasksPage;
