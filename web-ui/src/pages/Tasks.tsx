import React from 'react';
import { Typography } from 'antd';
import TreeVisualization from '@components/dag/TreeVisualization';

const { Title } = Typography;

const Tasks: React.FC = () => {
  return (
    <div className="page-container">
      <div className="page-header">
        <Title level={2}>
          任务管理
        </Title>
      </div>
      <div className="content-body">
        <TreeVisualization />
      </div>
    </div>
  );
};

export default Tasks;
