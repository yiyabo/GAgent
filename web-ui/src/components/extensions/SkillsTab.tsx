import React, { useMemo, useState } from 'react';
import { App as AntdApp, Button, Empty, Input, Popover, Result, Space, Spin, Switch, Table, Tag, Tooltip, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { extensionsApi, type SkillEntry, type SkillListResponse } from '@api/extensions';
import { ExternalLink, ScannerBadge, SeverityIcon, SignoffBadge, skillSwitchBlockReason } from './extensionMeta';

const { Text } = Typography;

export const SKILLS_QUERY_KEY = ['extensions', 'skills'] as const;

function formatSize(chars: number): string {
  if (chars >= 10_000) return `${(chars / 1000).toFixed(1)}k 字符`;
  return `${chars} 字符`;
}

const FindingsList: React.FC<{ skill: SkillEntry }> = ({ skill }) => {
  if (skill.findings.length === 0) {
    return (
      <Text type="secondary">
        没有扫描发现。{skill.review.notes ? `备注：${skill.review.notes}` : ''}
      </Text>
    );
  }
  return (
    <div>
      {skill.review.notes ? <div style={{ marginBottom: 8 }}><Text type="secondary">备注：{skill.review.notes}</Text></div> : null}
      <ul style={{ margin: 0, paddingLeft: 0, listStyle: 'none' }}>
        {skill.findings.map((finding, index) => (
          <li key={`${finding.rule}-${finding.file}-${finding.line ?? 'n'}-${index}`} style={{ marginBottom: 4 }}>
            <SeverityIcon severity={finding.severity} /> <Text code>{finding.rule}</Text>{' '}
            <Text type="secondary">
              {finding.file}
              {finding.line !== null && finding.line !== undefined ? `:${finding.line}` : ''}
            </Text>{' '}
            {finding.detail}
          </li>
        ))}
      </ul>
    </div>
  );
};

const SkillsTab: React.FC = () => {
  const { message } = AntdApp.useApp();
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState('');
  const [pendingNames, setPendingNames] = useState<Record<string, boolean>>({});

  const query = useQuery<SkillListResponse>({
    queryKey: SKILLS_QUERY_KEY,
    queryFn: () => extensionsApi.listSkills(),
    retry: false,
    staleTime: 0,
  });

  const mutation = useMutation({
    mutationFn: ({ name, enable }: { name: string; enable: boolean }) =>
      enable ? extensionsApi.enableSkill(name) : extensionsApi.disableSkill(name),
    retry: 0,
  });

  const handleToggle = async (skill: SkillEntry, enable: boolean) => {
    setPendingNames((prev) => ({ ...prev, [skill.name]: true }));
    try {
      const updated = await mutation.mutateAsync({ name: skill.name, enable });
      queryClient.setQueryData<SkillListResponse>(SKILLS_QUERY_KEY, (current) =>
        current
          ? { ...current, skills: current.skills.map((item) => (item.name === updated.name ? updated : item)) }
          : current,
      );
      message.success(`${skill.name} 已${enable ? '启用' : '停用'}`);
    } catch (error) {
      message.error(error instanceof Error && error.message ? error.message : '操作失败');
    } finally {
      setPendingNames((prev) => {
        const next = { ...prev };
        delete next[skill.name];
        return next;
      });
    }
  };

  const data = query.data;
  const visible = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    const skills = data?.skills ?? [];
    if (!needle) return skills;
    return skills.filter((skill) =>
      [skill.name, skill.description, skill.origin.source, skill.origin.kind, skill.origin.license]
        .join(' ')
        .toLowerCase()
        .includes(needle),
    );
  }, [data, filter]);

  const columns: ColumnsType<SkillEntry> = useMemo(
    () => [
      {
        title: '名称',
        dataIndex: 'name',
        key: 'name',
        width: 180,
        render: (name: string) => <Text strong>{name}</Text>,
      },
      {
        title: '描述',
        dataIndex: 'description',
        key: 'description',
        width: 260,
        render: (description: string) => (
          <Text ellipsis={{ tooltip: description }} style={{ maxWidth: 240 }}>{description}</Text>
        ),
      },
      {
        title: '来源',
        key: 'origin',
        width: 240,
        render: (_: unknown, skill) => (
          <Space size={[4, 4]} wrap>
            <Tag color={skill.origin.kind === 'internal' ? 'geekblue' : 'purple'} style={{ marginInlineEnd: 0 }}>
              {skill.origin.kind === 'internal' ? '内置' : '第三方'}
            </Tag>
            <Text type="secondary"><ExternalLink href={skill.origin.source}>{skill.origin.source}</ExternalLink></Text>
            {skill.origin.license ? <Text type="secondary">· {skill.origin.license}</Text> : null}
            {skill.origin.modified ? (
              <Popover
                title="本地修改"
                content={
                  skill.origin.modifications.length > 0 ? (
                    <ul style={{ margin: 0, paddingLeft: 18 }}>
                      {skill.origin.modifications.map((item) => <li key={item}>{item}</li>)}
                    </ul>
                  ) : '未列出具体修改'
                }
              >
                <Tag color="gold" style={{ cursor: 'pointer', marginInlineEnd: 0 }} tabIndex={0} aria-label="已修改：查看修改内容">
                  已修改
                </Tag>
              </Popover>
            ) : null}
          </Space>
        ),
      },
      {
        title: '扫描 / 确认',
        key: 'review',
        width: 200,
        render: (_: unknown, skill) => (
          <Space size={[4, 4]} wrap>
            <ScannerBadge scanner={skill.review.scanner} />
            <SignoffBadge humanSignoff={skill.review.human_signoff} />
          </Space>
        ),
      },
      {
        title: '大小',
        key: 'size',
        width: 150,
        render: (_: unknown, skill) => (
          <Text type="secondary">
            {formatSize(skill.size_chars)} · {skill.scripts} 脚本 · {skill.references} 引用
          </Text>
        ),
      },
      {
        title: '启用',
        key: 'enabled',
        width: 90,
        fixed: 'right',
        render: (_: unknown, skill) => {
          const reason = skillSwitchBlockReason(Boolean(data?.can_manage));
          const pending = Boolean(pendingNames[skill.name]);
          return (
            <Tooltip title={reason ?? undefined}>
              <span style={{ display: 'inline-flex' }}>
                <Switch
                  checked={skill.enabled}
                  loading={pending}
                  disabled={pending || reason !== null}
                  aria-label={`${skill.enabled ? '停用' : '启用'}技能 ${skill.name}`}
                  onChange={(next) => void handleToggle(skill, next)}
                />
              </span>
            </Tooltip>
          );
        },
      },
    ],
    // handleToggle only closes over stable setters/mutation; data/pending drive the switch state.
    [data?.can_manage, pendingNames],
  );

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
        title="无法加载技能列表"
        subTitle={reason}
        extra={<Button type="primary" onClick={() => void query.refetch()}>重试</Button>}
      />
    );
  }

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Space wrap>
        <Input.Search
          allowClear
          placeholder="按名称、描述或来源过滤"
          aria-label="过滤技能"
          style={{ width: 280 }}
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
        />
        <Text type="secondary">共 {visible.length} / {data.skills.length} 个</Text>
      </Space>
      <Table<SkillEntry>
        rowKey="name"
        size="small"
        columns={columns}
        dataSource={visible}
        pagination={visible.length > 20 ? { pageSize: 20 } : false}
        scroll={{ x: 1100 }}
        locale={{
          emptyText: <Empty description={data.skills.length === 0 ? '暂无技能' : '没有符合过滤条件的技能'} />,
        }}
        expandable={{
          expandedRowRender: (skill) => <FindingsList skill={skill} />,
          rowExpandable: () => true,
        }}
      />
    </Space>
  );
};

export default SkillsTab;
