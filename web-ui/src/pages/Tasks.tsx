import React from 'react';
import { Typography } from 'antd';
import { useT } from '@/i18n/useT';
import TreeVisualization from '@components/dag/TreeVisualization';

const { Title } = Typography;

const Tasks: React.FC = () => {
  const t = useT();
  return (
    <div className="page-container">
      <div className="page-header">
        <Title level={2}>
          {t('tasks.title')}
        </Title>
      </div>
      <div className="content-body">
        <TreeVisualization />
      </div>
    </div>
  );
};

export default Tasks;
