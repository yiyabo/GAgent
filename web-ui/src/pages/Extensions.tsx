import React from 'react';
import { Tabs, Typography } from 'antd';
import McpTab from '@components/extensions/McpTab';
import SkillsTab from '@components/extensions/SkillsTab';
import { useT } from '@/i18n/useT';

const { Title, Paragraph } = Typography;

const Extensions: React.FC = () => {
  const t = useT();
  return (
  <div>
    <div className="content-header">
      <Title level={3} style={{ margin: 0 }}>{t('extensions.title')}</Title>
      <Paragraph type="secondary" style={{ marginBottom: 0 }}>
        {t('extensions.subtitle')}
      </Paragraph>
    </div>
    <div className="content-body">
      <Tabs
        defaultActiveKey="mcp"
        items={[
          { key: 'mcp', label: t('extensions.tabMcp'), children: <McpTab /> },
          { key: 'skills', label: t('extensions.tabSkills'), children: <SkillsTab /> },
        ]}
      />
    </div>
  </div>
  );
};

export default Extensions;
