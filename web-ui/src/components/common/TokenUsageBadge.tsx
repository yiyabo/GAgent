import React, { useEffect, useState } from 'react';
import { Tooltip, Badge, Popover, Typography, Space, Divider } from 'antd';
import { ThunderboltOutlined, ReloadOutlined } from '@ant-design/icons';
import { statsApi, TokenUsageResponse } from '@/api/stats';

const { Text } = Typography;

function formatTokens(count: number): string {
  if (count >= 1_000_000) {
    return `${(count / 1_000_000).toFixed(1)}M`;
  }
  if (count >= 1_000) {
    return `${(count / 1_000).toFixed(1)}K`;
  }
  return count.toString();
}

const TokenUsageBadge: React.FC = () => {
  const [usage, setUsage] = useState<TokenUsageResponse | null>(null);
  const [loading, setLoading] = useState(false);

  const fetchUsage = async () => {
    setLoading(true);
    try {
      const data = await statsApi.getTokenUsage(24);
      setUsage(data);
    } catch (error) {
      console.error('Failed to fetch token usage:', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchUsage();
    const interval = setInterval(fetchUsage, 60_000);
    return () => clearInterval(interval);
  }, []);

  const content = (
    <div style={{ width: 280, padding: '8px 0' }}>
      <Space direction="vertical" style={{ width: '100%' }} size="small">
        <Space style={{ width: '100%', justifyContent: 'space-between' }}>
          <Text strong>Token Usage (24h)</Text>
          <Tooltip title="Refresh">
            <ReloadOutlined
              spin={loading}
              onClick={fetchUsage}
              style={{ cursor: 'pointer', color: 'var(--text-secondary)' }}
            />
          </Tooltip>
        </Space>
        <Divider style={{ margin: '8px 0' }} />
        <Space direction="vertical" style={{ width: '100%' }} size={4}>
          <Space style={{ width: '100%', justifyContent: 'space-between' }}>
            <Text type="secondary">Total Calls</Text>
            <Text>{usage?.call_count ?? 0}</Text>
          </Space>
          <Space style={{ width: '100%', justifyContent: 'space-between' }}>
            <Text type="secondary">Prompt Tokens</Text>
            <Text>{formatTokens(usage?.total_prompt_tokens ?? 0)}</Text>
          </Space>
          <Space style={{ width: '100%', justifyContent: 'space-between' }}>
            <Text type="secondary">Completion Tokens</Text>
            <Text>{formatTokens(usage?.total_completion_tokens ?? 0)}</Text>
          </Space>
          <Space style={{ width: '100%', justifyContent: 'space-between' }}>
            <Text type="secondary">Total Tokens</Text>
            <Text strong>{formatTokens(usage?.total_tokens ?? 0)}</Text>
          </Space>
        </Space>
        {usage?.by_model && usage.by_model.length > 0 && (
          <>
            <Divider style={{ margin: '8px 0' }} />
            <Text strong style={{ fontSize: 12 }}>By Model</Text>
            <Space direction="vertical" style={{ width: '100%' }} size={2}>
              {usage.by_model.map((m) => (
                <Space key={m.model} style={{ width: '100%', justifyContent: 'space-between' }}>
                  <Text type="secondary" style={{ fontSize: 11 }}>{m.model}</Text>
                  <Text style={{ fontSize: 11 }}>{formatTokens(m.total_tokens)} ({m.call_count})</Text>
                </Space>
              ))}
            </Space>
          </>
        )}
      </Space>
    </div>
  );

  return (
    <Popover content={content} trigger="click" placement="bottomRight">
      <Tooltip title="Token Usage">
        <Badge count={usage ? formatTokens(usage.total_tokens) : '0'} size="small" style={{ backgroundColor: '#1890ff' }}>
          <ThunderboltOutlined style={{ fontSize: 18, color: 'var(--text-secondary)', cursor: 'pointer' }} />
        </Badge>
      </Tooltip>
    </Popover>
  );
};

export default TokenUsageBadge;
