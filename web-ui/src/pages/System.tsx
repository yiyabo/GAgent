import React from 'react';
import { Typography, Card } from 'antd';

const { Title, Text } = Typography;

const SystemPage: React.FC = () => {
  return (
    <div>
      <div className="content-header">
        <Title level={3} style={{ margin: 0 }}>
          ⚙️ 系统设置
        </Title>
      </div>
      <div className="content-body">
        <Card>
          <Text>系统设置功能正在开发中...</Text>
        </Card>
      </div>
    </div>
  );
};

export default SystemPage;
