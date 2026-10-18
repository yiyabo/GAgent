import React from 'react';
import { Tabs, Typography } from 'antd';
import McpTab from '@components/extensions/McpTab';
import SkillsTab from '@components/extensions/SkillsTab';

const { Title, Paragraph } = Typography;

const Extensions: React.FC = () => (
  <div>
    <div className="content-header">
      <Title level={3} style={{ margin: 0 }}>扩展</Title>
      <Paragraph type="secondary" style={{ marginBottom: 0 }}>
        查看智能体可用的 MCP 服务器与技能，以及它们的来源、审核状态与隔离方式。
      </Paragraph>
    </div>
    <div className="content-body">
      <Tabs
        defaultActiveKey="mcp"
        items={[
          { key: 'mcp', label: 'MCP 服务器', children: <McpTab /> },
          { key: 'skills', label: '技能 (Skills)', children: <SkillsTab /> },
        ]}
      />
    </div>
  </div>
);

export default Extensions;
