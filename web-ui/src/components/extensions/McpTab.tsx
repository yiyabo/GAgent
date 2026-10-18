import React, { useMemo, useState } from 'react';
import { App as AntdApp, Alert, Button, Card, Empty, Input, Result, Select, Space, Spin, Tag, Typography } from 'antd';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { extensionsApi, type McpListResponse, type McpServer } from '@api/extensions';
import McpServerCard from './McpServerCard';
import { IsolationBadge, mcpSwitchBlockReason } from './extensionMeta';

const { Text } = Typography;

export const MCP_QUERY_KEY = ['extensions', 'mcp'] as const;

const ALL = '__all__';

const McpTab: React.FC = () => {
  const { message } = AntdApp.useApp();
  const queryClient = useQueryClient();
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState<string>(ALL);
  const [pendingIds, setPendingIds] = useState<Record<string, boolean>>({});

  const query = useQuery<McpListResponse>({
    queryKey: MCP_QUERY_KEY,
    queryFn: () => extensionsApi.listMcp(),
    retry: false,
    staleTime: 0,
  });

  const mutation = useMutation({
    mutationFn: ({ id, enable }: { id: string; enable: boolean }) =>
      enable ? extensionsApi.enableMcp(id) : extensionsApi.disableMcp(id),
    retry: 0,
  });

  const handleToggle = async (server: McpServer, enable: boolean) => {
    setPendingIds((prev) => ({ ...prev, [server.id]: true }));
    try {
      const updated = await mutation.mutateAsync({ id: server.id, enable });
      queryClient.setQueryData<McpListResponse>(MCP_QUERY_KEY, (current) =>
        current
          ? { ...current, servers: current.servers.map((item) => (item.id === updated.id ? updated : item)) }
          : current,
      );
      message.success(`${server.title} 已${enable ? '启用' : '停用'}`);
    } catch (error) {
      message.error(error instanceof Error && error.message ? error.message : '操作失败');
    } finally {
      setPendingIds((prev) => {
        const next = { ...prev };
        delete next[server.id];
        return next;
      });
    }
  };

  const data = query.data;
  const categories = useMemo(
    () => Array.from(new Set((data?.servers ?? []).map((server) => server.category))).sort(),
    [data],
  );
  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return (data?.servers ?? []).filter((server) => {
      if (category !== ALL && server.category !== category) return false;
      if (!needle) return true;
      return [server.id, server.title, server.description, server.category, ...server.tools.map((tool) => tool.name)]
        .join(' ')
        .toLowerCase()
        .includes(needle);
    });
  }, [data, search, category]);

  if (query.isLoading) {
    return (
      <div style={{ textAlign: 'center', padding: 48 }}>
        <Spin tip="加载中…"><div style={{ padding: 24 }} /></Spin>
      </div>
    );
  }

  if (query.isError || !data) {
    const reason = query.error instanceof Error ? query.error.message : '未知错误';
    return (
      <Result
        status="error"
        title="无法加载 MCP 服务器列表"
        subTitle={reason}
        extra={<Button type="primary" onClick={() => void query.refetch()}>重试</Button>}
      />
    );
  }

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      {!data.configured ? (
        <Alert
          type="warning"
          showIcon
          message="服务端未配置 MCP 挂载文件"
          description="目前只能查看目录，无法启用或停用任何 MCP 服务器。请联系管理员配置挂载文件。"
        />
      ) : null}

      <Space wrap>
        <Input.Search
          allowClear
          placeholder="搜索名称、描述或工具"
          aria-label="搜索 MCP 服务器"
          style={{ width: 280 }}
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
        <Select
          aria-label="按类别筛选"
          style={{ width: 160 }}
          value={category}
          onChange={setCategory}
          options={[{ value: ALL, label: '全部类别' }, ...categories.map((item) => ({ value: item, label: item }))]}
        />
        <Text type="secondary">共 {visible.length} / {data.servers.length} 个</Text>
      </Space>

      {data.servers.length === 0 ? (
        <Empty description="暂无可用的 MCP 服务器" />
      ) : visible.length === 0 ? (
        <Empty description="没有符合筛选条件的 MCP 服务器" />
      ) : (
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          {visible.map((server) => (
            <McpServerCard
              key={server.id}
              server={server}
              pending={Boolean(pendingIds[server.id])}
              blockReason={mcpSwitchBlockReason({
                canManage: data.can_manage,
                configured: data.configured,
                state: server.state,
                unavailableReason: server.unavailable_reason,
              })}
              onToggle={handleToggle}
            />
          ))}
        </Space>
      )}

      {data.custom.length > 0 ? (
        <Card size="small" title="自定义条目（只读）" data-testid="mcp-custom">
          <Space direction="vertical" size={8} style={{ width: '100%' }}>
            {data.custom.map((entry) => (
              <div key={entry.name}>
                <Space size={8} wrap>
                  <Text strong>{entry.name}</Text>
                  <Tag style={{ marginInlineEnd: 0 }}>{entry.transport}</Tag>
                  <IsolationBadge isolation={entry.isolation} />
                  <Tag color={entry.connected ? 'green' : 'default'}>{entry.connected ? '已连接' : '未连接'}</Tag>
                  {entry.tools.length > 0 ? <Text type="secondary">{entry.tools.join(', ')}</Text> : null}
                </Space>
                {entry.error ? <div><Text type="danger">{entry.error}</Text></div> : null}
              </div>
            ))}
          </Space>
        </Card>
      ) : null}
    </Space>
  );
};

export default McpTab;
