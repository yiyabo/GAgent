import React from 'react';
import { Typography, Card } from 'antd';

const { Title, Text } = Typography;

const PlansPage: React.FC = () => {
  return (
    <div>
      <div className="content-header">
        <Title level={3} style={{ margin: 0 }}>
          📋 计划管理
        </Title>
      </div>
      <div className="content-body">
        <Card>
          <Text>计划管理功能正在开发中...</Text>
        </Card>
      </div>
    </div>
  );
};

export default PlansPage;
