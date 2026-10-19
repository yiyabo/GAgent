import React from 'react';
import { Alert, Card, Collapse, Space, Switch, Tag, Tooltip, Typography } from 'antd';
import { CheckCircleOutlined, CloseCircleOutlined } from '@ant-design/icons';
import type { McpServer } from '@api/extensions';
import {
  ExternalLink,
  IsolationBadge,
  SignoffBadge,
  VerificationBadge,
  shortCommit,
} from './extensionMeta';

const { Text, Paragraph } = Typography;

interface McpServerCardProps {
  server: McpServer;
  /** null = the switch is usable; otherwise the explanation shown in its tooltip. */
  blockReason: string | null;
  pending: boolean;
  onToggle: (server: McpServer, next: boolean) => void;
}

const McpServerCard: React.FC<McpServerCardProps> = ({ server, blockReason, pending, onToggle }) => {
  const { verification } = server;
  const passedCount = verification.checks.filter((check) => check.passed).length;
  // An "error" server is configured on but failing; keep the switch on so it can be turned off.
  const checked = server.state === 'enabled' || server.state === 'error';
  const stateTag =
    server.state === 'enabled' ? (
      <Tag color={server.connected ? 'green' : 'default'}>{server.connected ? '已连接' : '已启用'}</Tag>
    ) : server.state === 'error' ? (
      <Tag color="red">出错</Tag>
    ) : server.state === 'unavailable' ? (
      <Tag>不可用</Tag>
    ) : (
      <Tag>未启用</Tag>
    );

  return (
    <Card
      size="small"
      data-testid={`mcp-server-${server.id}`}
      title={
        <Space size={8} wrap>
          <Text strong>{server.title}</Text>
          <Tag style={{ marginInlineEnd: 0 }}>{server.category}</Tag>
          {stateTag}
        </Space>
      }
      extra={
        <Tooltip title={blockReason ?? undefined}>
          <span style={{ display: 'inline-flex' }}>
            <Switch
              checked={checked}
              loading={pending}
              disabled={pending || blockReason !== null}
              aria-label={`${checked ? '停用' : '启用'} MCP 服务器 ${server.title}`}
              checkedChildren="开"
              unCheckedChildren="关"
              onChange={(next) => onToggle(server, next)}
            />
          </span>
        </Tooltip>
      }
    >
      <Space direction="vertical" size={10} style={{ width: '100%' }}>
        <Paragraph style={{ marginBottom: 0 }}>{server.description}</Paragraph>

        <Space size={[8, 6]} wrap>
          <VerificationBadge status={verification.status} />
          <SignoffBadge humanSignoff={verification.human_signoff} />
          <IsolationBadge isolation={server.isolation} />
          <Text type="secondary">
            v{server.version} · {server.license}
          </Text>
        </Space>

        <Text type="secondary" style={{ fontSize: 12 }}>
          来源：<ExternalLink href={server.source.repo}>{server.source.repo}</ExternalLink>
          {server.source.commit ? <> @ <Text code>{shortCommit(server.source.commit)}</Text></> : null}
          {server.source.package ? <> · 包：<Text code>{server.source.package}</Text></> : null}
          {server.homepage ? <> · <ExternalLink href={server.homepage}>主页</ExternalLink></> : null}
          {verification.reviewer || verification.reviewed_at ? (
            <> · 审核：{[verification.reviewer, verification.reviewed_at].filter(Boolean).join(' ')}</>
          ) : null}
        </Text>

        <Space size={[6, 6]} wrap align="center">
          <Text type="secondary">网络出口：</Text>
          {server.egress.length === 0 ? (
            <Tag color="green">无网络</Tag>
          ) : (
            server.egress.map((host) => (
              <Tag key={host}>{host}</Tag>
            ))
          )}
        </Space>

        <Space size={[6, 6]} wrap align="center">
          <Text type="secondary">工具：</Text>
          {server.tools.length === 0 ? <Text type="secondary">无</Text> : null}
          {server.tools.map((tool) => (
            <Tag
              key={tool.name}
              color={tool.read_only ? 'blue' : 'volcano'}
              aria-label={`${tool.name}：${tool.read_only ? '只读' : '有副作用'}`}
            >
              {tool.name} · {tool.read_only ? '只读' : '有副作用'}
            </Tag>
          ))}
        </Space>

        {server.state === 'unavailable' && server.unavailable_reason ? (
          <Text type="secondary">不可用：{server.unavailable_reason}</Text>
        ) : null}

        {server.error ? (
          <Alert type="error" showIcon message="服务器报告错误" description={server.error} />
        ) : null}

        {server.live_tools.length > 0 ? (
          <Text type="secondary" style={{ fontSize: 12 }}>
            当前已挂载的工具：{server.live_tools.join(', ')}
          </Text>
        ) : null}

        {verification.checks.length > 0 ? (
          <Collapse
            ghost
            size="small"
            items={[
              {
                key: 'checks',
                label: `验证检查（${passedCount}/${verification.checks.length} 通过）`,
                children: (
                  <ul style={{ margin: 0, paddingLeft: 0, listStyle: 'none' }}>
                    {verification.checks.map((check) => (
                      <li key={check.name} style={{ marginBottom: 4 }}>
                        {check.passed ? (
                          <CheckCircleOutlined style={{ color: 'var(--status-success-fg)' }} role="img" aria-label="通过" />
                        ) : (
                          <CloseCircleOutlined style={{ color: 'var(--status-error-fg)' }} role="img" aria-label="未通过" />
                        )}{' '}
                        <Text strong>{check.name}</Text>
                        {check.detail ? <Text type="secondary"> — {check.detail}</Text> : null}
                      </li>
                    ))}
                  </ul>
                ),
              },
            ]}
          />
        ) : null}
      </Space>
    </Card>
  );
};

export default McpServerCard;
