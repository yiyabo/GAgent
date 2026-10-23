import React from 'react';
import { Alert, Card, Collapse, Space, Switch, Tag, Tooltip, Typography } from 'antd';
import { CheckCircleOutlined, CloseCircleOutlined } from '@ant-design/icons';
import type { McpServer } from '@api/extensions';
import {
  ExternalLink,
  IsolationBadge,
  SignoffBadge,
  VerificationBadge,
  mountedToolLabel,
  shortCommit,
} from './extensionMeta';
import { useT } from '@/i18n/useT';

const { Text, Paragraph } = Typography;

interface McpServerCardProps {
  server: McpServer;
  /** null = the switch is usable; otherwise the explanation shown in its tooltip. */
  blockReason: string | null;
  pending: boolean;
  onToggle: (server: McpServer, next: boolean) => void;
}

const McpServerCard: React.FC<McpServerCardProps> = ({ server, blockReason, pending, onToggle }) => {
  const t = useT();
  const { verification } = server;
  const passedCount = verification.checks.filter((check) => check.passed).length;
  // An "error" server is configured on but failing; keep the switch on so it can be turned off.
  const checked = server.state === 'enabled' || server.state === 'error';
  const ready = server.state === 'enabled' && server.connected;
  // Starting a container takes a couple of seconds; say so instead of leaving the old state on screen.
  const stateTag = pending ? (
    <Tag color="processing">{t(checked ? 'extensions.stopping' : 'extensions.starting')}</Tag>
  ) : server.state === 'enabled' ? (
    <Tag color={server.connected ? 'green' : 'default'}>{server.connected ? t('extensions.connected') : t('extensions.enabled')}</Tag>
  ) : server.state === 'error' ? (
    <Tag color="red">{t('extensions.error')}</Tag>
  ) : server.state === 'unavailable' ? (
    <Tag>{t('extensions.unavailable')}</Tag>
  ) : (
    <Tag>{t('extensions.off')}</Tag>
  );

  return (
    <Card
      size="small"
      data-testid={`mcp-server-${server.id}`}
      style={ready ? { borderInlineStart: '3px solid var(--status-success-fg)' } : undefined}
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
              aria-label={t(checked ? 'extensions.switchDisableAria' : 'extensions.switchEnableAria', { title: server.title })}
              checkedChildren={t('extensions.switchOn')}
              unCheckedChildren={t('extensions.switchOff')}
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
          {t('extensions.sourceLabel')}<ExternalLink href={server.source.repo}>{server.source.repo}</ExternalLink>
          {server.source.commit ? <> @ <Text code>{shortCommit(server.source.commit)}</Text></> : null}
          {server.source.package ? <> · {t('extensions.packageLabel')}<Text code>{server.source.package}</Text></> : null}
          {server.homepage ? <> · <ExternalLink href={server.homepage}>{t('extensions.homepage')}</ExternalLink></> : null}
          {verification.reviewer || verification.reviewed_at ? (
            <> · {t('extensions.reviewLabel')}{[verification.reviewer, verification.reviewed_at].filter(Boolean).join(' ')}</>
          ) : null}
        </Text>

        <Space size={[6, 6]} wrap align="center">
          <Text type="secondary">{t('extensions.egressLabel')}</Text>
          {server.egress.length === 0 ? (
            <Tag color="green">{t('extensions.noNetwork')}</Tag>
          ) : (
            server.egress.map((host) => (
              <Tag key={host}>{host}</Tag>
            ))
          )}
        </Space>

        <Space size={[6, 6]} wrap align="center">
          <Text type="secondary">{t('extensions.toolsLabel')}</Text>
          {server.tools.length === 0 ? <Text type="secondary">{t('extensions.none')}</Text> : null}
          {server.tools.map((tool) => (
            <Tag
              key={tool.name}
              color={tool.read_only ? 'blue' : 'volcano'}
              aria-label={`${tool.name}：${tool.read_only ? t('extensions.readOnly') : t('extensions.sideEffects')}`}
            >
              {tool.name} · {tool.read_only ? t('extensions.readOnly') : t('extensions.sideEffects')}
            </Tag>
          ))}
        </Space>

        {server.state === 'unavailable' && server.unavailable_reason ? (
          <Text type="secondary">{t('extensions.unavailableLine', { reason: server.unavailable_reason })}</Text>
        ) : null}

        {server.error ? (
          <Alert
            type="error"
            showIcon
            message={t('extensions.serverError')}
            description={
              <>
                <div>{server.error}</div>
                {server.state === 'error' ? <div>{t('extensions.errorRetryHint')}</div> : null}
              </>
            }
          />
        ) : null}

        {ready && server.live_tools.length > 0 ? (
          <Alert
            type="success"
            showIcon
            message={t('extensions.readyTitle')}
            description={
              <Space size={[6, 6]} wrap>
                {server.live_tools.map((name) => (
                  <Tag key={name} style={{ marginInlineEnd: 0 }}>
                    {mountedToolLabel(name)}
                  </Tag>
                ))}
              </Space>
            }
          />
        ) : null}

        {verification.checks.length > 0 ? (
          <Collapse
            ghost
            size="small"
            items={[
              {
                key: 'checks',
                label: t('extensions.checksLabel', { passed: passedCount, total: verification.checks.length }),
                children: (
                  <ul style={{ margin: 0, paddingLeft: 0, listStyle: 'none' }}>
                    {verification.checks.map((check) => (
                      <li key={check.name} style={{ marginBottom: 4 }}>
                        {check.passed ? (
                          <CheckCircleOutlined style={{ color: 'var(--status-success-fg)' }} role="img" aria-label={t('extensions.checkPassed')} />
                        ) : (
                          <CloseCircleOutlined style={{ color: 'var(--status-error-fg)' }} role="img" aria-label={t('extensions.checkFailed')} />
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
