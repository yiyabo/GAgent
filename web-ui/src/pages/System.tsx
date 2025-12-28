import React from 'react';
import { Typography, Card } from 'antd';

const { Title, Text } = Typography;

const SystemPage: React.FC = () => {
  return (
    <div>
      <div className="content-header">
        <Title level={3} style={{ margin: 0 }}>
          ⚙️ System Settings
        </Title>
      </div>
      <div className="content-body">
        <Card>
          <Text>System settings feature is under development...</Text>
        </Card>
      </div>
    </div>
  );
};

export default SystemPage;
