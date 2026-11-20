import React, { useState } from 'react';
import { App as AntdApp, Alert, Button, Card, Empty, Input, Popconfirm, Result, Space, Spin, Table, Tabs, Tag, Typography } from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { extensionsApi, type McpTokenCreated, type McpTokenListResponse, type McpTokenRecord } from '@api/extensions';
import { useT } from '@/i18n/useT';

const { Paragraph, Text } = Typography;

export const MCP_TOKENS_QUERY_KEY = ['extensions', 'mcp-tokens'] as const;
const TOKEN_PLACEHOLDER = '<your-token>';
const CLIENT_NAMES = { claude: 'Claude Code', codex: 'Codex' } as const;

function mcpEndpoint(): string {
  return `${window.location.origin}/mcp`;
}

function clientSnippets(endpoint: string): Record<string, string> {
  return {
    claude: [
      'claude plugin marketplace add yiyabo/phageagent-mcp',
      'claude plugin install phageagent@phageagent',
      '# or without the plugin:',
      `claude mcp add --transport http phageagent ${endpoint} --header "Authorization: Bearer ${TOKEN_PLACEHOLDER}"`,
    ].join('\n'),
    codex: [
      'codex plugin marketplace add yiyabo/phageagent-mcp',
      'codex plugin add phageagent@phageagent',
      `export PHAGEAGENT_MCP_TOKEN=${TOKEN_PLACEHOLDER}`,
    ].join('\n'),
    generic: JSON.stringify(
      { mcpServers: { phageagent: { type: 'http', url: endpoint, headers: { Authorization: `Bearer ${TOKEN_PLACEHOLDER}` } } } },
      null,
      2,
    ),
  };
}

function formatTime(value: string | null): string {
  return value ? new Date(value).toLocaleString() : '—';
}

const CodeBlock: React.FC<{ text: string; testId: string }> = ({ text, testId }) => (
  <Paragraph copyable={{ text }} style={{ marginBottom: 0 }} data-testid={testId}>
    <pre style={{ margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>{text}</pre>
  </Paragraph>
);

const ExternalAccessTab: React.FC = () => {
  const t = useT();
  const { message } = AntdApp.useApp();
  const queryClient = useQueryClient();
  const [name, setName] = useState('');
  const [created, setCreated] = useState<McpTokenCreated | null>(null);
  const endpoint = mcpEndpoint();
  const snippets = clientSnippets(endpoint);

  const query = useQuery<McpTokenListResponse>({
    queryKey: MCP_TOKENS_QUERY_KEY,
    queryFn: () => extensionsApi.listMcpTokens(),
    retry: false,
    staleTime: 0,
  });

  const createMutation = useMutation({ mutationFn: (tokenName: string) => extensionsApi.createMcpToken(tokenName), retry: 0 });
  const revokeMutation = useMutation({ mutationFn: (id: string) => extensionsApi.revokeMcpToken(id), retry: 0 });

  const errorText = (error: unknown) => (error instanceof Error && error.message ? error.message : t('extensions.actionFailed'));

  const handleCreate = async () => {
    try {
      const result = await createMutation.mutateAsync(name.trim() || 'my-client');
      setCreated(result);
      setName('');
      await queryClient.invalidateQueries({ queryKey: MCP_TOKENS_QUERY_KEY });
    } catch (error) {
      message.error(errorText(error));
    }
  };

  const handleRevoke = async (token: McpTokenRecord) => {
    try {
      await revokeMutation.mutateAsync(token.id);
      if (created?.record.id === token.id) setCreated(null);
      message.success(t('extensions.externalRevoked', { name: token.name }));
      await queryClient.invalidateQueries({ queryKey: MCP_TOKENS_QUERY_KEY });
    } catch (error) {
      message.error(errorText(error));
    }
  };

  const tokens = (query.data?.tokens ?? []).filter((token) => !token.revoked_at);
  const columns = [
    { title: t('extensions.externalColName'), dataIndex: 'name', key: 'name' },
    {
      title: t('extensions.externalColScopes'),
      key: 'scopes',
      render: (_: unknown, token: McpTokenRecord) => token.scopes.map((scope) => <Tag key={scope}>{scope}</Tag>),
    },
    { title: t('extensions.externalColCreated'), key: 'created', render: (_: unknown, token: McpTokenRecord) => formatTime(token.created_at) },
    { title: t('extensions.externalColLastUsed'), key: 'used', render: (_: unknown, token: McpTokenRecord) => formatTime(token.last_used_at) },
    { title: t('extensions.externalColExpires'), key: 'expires', render: (_: unknown, token: McpTokenRecord) => formatTime(token.expires_at) },
    {
      title: '',
      key: 'actions',
      render: (_: unknown, token: McpTokenRecord) => (
        <Popconfirm title={t('extensions.externalRevokeConfirm', { name: token.name })} onConfirm={() => handleRevoke(token)}>
          <Button danger size="small" data-testid={`mcp-token-revoke-${token.id}`}>{t('extensions.externalRevoke')}</Button>
        </Popconfirm>
      ),
    },
  ];

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }} data-testid="external-access-tab">
      <Card size="small" title={t('extensions.externalIntroTitle')}>
        <Paragraph>{t('extensions.externalIntro')}</Paragraph>
        <Paragraph style={{ marginBottom: 0 }}>
          <Text strong>{t('extensions.externalEndpoint')}</Text>{' '}
          <Text code copyable data-testid="mcp-endpoint">{endpoint}</Text>
        </Paragraph>
      </Card>

      <Card size="small" title={t('extensions.externalTokensTitle')} data-testid="mcp-tokens">
        {query.isLoading ? (
          <Spin />
        ) : query.isError ? (
          <Result status="warning" title={t('extensions.externalLoadFailed')} subTitle={errorText(query.error)} />
        ) : !query.data?.eligible ? (
          <Alert type="info" showIcon message={t('extensions.externalNotEligible')} />
        ) : (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Text type="secondary">{t('extensions.externalTokensHint')}</Text>
            {created && (
              <Alert
                type="success"
                showIcon
                closable
                onClose={() => setCreated(null)}
                message={t('extensions.externalCreatedTitle')}
                description={<Text code copyable data-testid="mcp-token-plaintext">{created.token}</Text>}
              />
            )}
            <Space.Compact style={{ width: '100%', maxWidth: 480 }}>
              <Input
                placeholder={t('extensions.externalNamePlaceholder')}
                value={name}
                maxLength={80}
                onChange={(event) => setName(event.target.value)}
                onPressEnter={handleCreate}
                aria-label={t('extensions.externalNamePlaceholder')}
              />
              <Button type="primary" loading={createMutation.isPending} onClick={handleCreate} data-testid="mcp-token-create">
                {t('extensions.externalCreate')}
              </Button>
            </Space.Compact>
            {tokens.length ? (
              <Table size="small" rowKey="id" pagination={false} columns={columns} dataSource={tokens} scroll={{ x: true }} />
            ) : (
              <Empty description={t('extensions.externalNoTokens')} />
            )}
          </Space>
        )}
      </Card>

      <Card size="small" title={t('extensions.externalSetupTitle')}>
        <Paragraph type="secondary">{t('extensions.externalSetupHint')}</Paragraph>
        <Tabs
          size="small"
          items={[
            { key: 'claude', label: CLIENT_NAMES.claude, children: <CodeBlock text={snippets.claude} testId="snippet-claude" /> },
            { key: 'codex', label: CLIENT_NAMES.codex, children: <CodeBlock text={snippets.codex} testId="snippet-codex" /> },
            { key: 'generic', label: t('extensions.externalGeneric'), children: <CodeBlock text={snippets.generic} testId="snippet-generic" /> },
          ]}
        />
        <Paragraph type="secondary" style={{ marginBottom: 0 }}>
          {t('extensions.externalDocs')}{' '}
          <a href="https://github.com/yiyabo/phageagent-mcp" target="_blank" rel="noreferrer">github.com/yiyabo/phageagent-mcp</a>
        </Paragraph>
      </Card>
    </Space>
  );
};

export default ExternalAccessTab;
