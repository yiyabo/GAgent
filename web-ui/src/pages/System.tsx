import React from 'react';
import { Typography, Card } from 'antd';
import { useT } from '@/i18n/useT';

const { Title, Text } = Typography;

const SystemPage: React.FC = () => {
  const t = useT();
  return (
    <div>
      <div className="content-header">
        <Title level={3} style={{ margin: 0 }}>
          ⚙️ {t('system.title')}
        </Title>
      </div>
      <div className="content-body">
        <Card>
          <Text>{t('system.underDevelopment')}</Text>
        </Card>
      </div>
    </div>
  );
};

export default SystemPage;
