import React, { useMemo, useState } from 'react';
import { App as AntdApp, Button, Empty, Input, Popover, Result, Space, Spin, Switch, Table, Tag, Tooltip, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { extensionsApi, type SkillEntry, type SkillListResponse } from '@api/extensions';
import { ExternalLink, ScannerBadge, SeverityIcon, SignoffBadge, skillSwitchBlockReason } from './extensionMeta';
import { useT, type TFn } from '@/i18n/useT';

const { Text } = Typography;

export const SKILLS_QUERY_KEY = ['extensions', 'skills'] as const;

function formatSize(t: TFn, chars: number): string {
  if (chars >= 10_000) return t('extensions.sizeK', { n: (chars / 1000).toFixed(1) });
  return t('extensions.sizeChars', { n: chars });
}

const FindingsList: React.FC<{ skill: SkillEntry }> = ({ skill }) => {
  const t = useT();
  if (skill.findings.length === 0) {
    return (
      <Text type="secondary">
        {skill.review.notes ? t('extensions.noFindingsWithNotes', { notes: skill.review.notes }) : t('extensions.noFindings')}
      </Text>
    );
  }
  return (
    <div>
      {skill.review.notes ? <div style={{ marginBottom: 8 }}><Text type="secondary">{t('extensions.notes', { notes: skill.review.notes })}</Text></div> : null}
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
  const t = useT();
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
      message.success(t(enable ? 'extensions.toggledOn' : 'extensions.toggledOff', { name: skill.name }));
    } catch (error) {
      message.error(error instanceof Error && error.message ? error.message : t('extensions.actionFailed'));
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
        title: t('extensions.colName'),
        dataIndex: 'name',
        key: 'name',
        width: 180,
        render: (name: string) => <Text strong>{name}</Text>,
      },
      {
        title: t('extensions.colDescription'),
        dataIndex: 'description',
        key: 'description',
        width: 260,
        render: (description: string) => (
          <Text ellipsis={{ tooltip: description }} style={{ maxWidth: 240 }}>{description}</Text>
        ),
      },
      {
        title: t('extensions.colSource'),
        key: 'origin',
        width: 240,
        render: (_: unknown, skill) => (
          <Space size={[4, 4]} wrap>
            <Tag color={skill.origin.kind === 'internal' ? 'geekblue' : 'purple'} style={{ marginInlineEnd: 0 }}>
              {skill.origin.kind === 'internal' ? t('extensions.builtIn') : t('extensions.thirdParty')}
            </Tag>
            <Text type="secondary"><ExternalLink href={skill.origin.source}>{skill.origin.source}</ExternalLink></Text>
            {skill.origin.license ? <Text type="secondary">· {skill.origin.license}</Text> : null}
            {skill.origin.modified ? (
              <Popover
                title={t('extensions.localChanges')}
                content={
                  skill.origin.modifications.length > 0 ? (
                    <ul style={{ margin: 0, paddingLeft: 18 }}>
                      {skill.origin.modifications.map((item) => <li key={item}>{item}</li>)}
                    </ul>
                  ) : t('extensions.noChangesListed')
                }
              >
                <Tag color="gold" style={{ cursor: 'pointer', marginInlineEnd: 0 }} tabIndex={0} aria-label={t('extensions.modifiedAria')}>
                  {t('extensions.modified')}
                </Tag>
              </Popover>
            ) : null}
          </Space>
        ),
      },
      {
        title: t('extensions.colReview'),
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
        title: t('extensions.colSize'),
        key: 'size',
        width: 150,
        render: (_: unknown, skill) => (
          <Text type="secondary">
            {t('extensions.sizeLine', { size: formatSize(t, skill.size_chars), scripts: skill.scripts, refs: skill.references })}
          </Text>
        ),
      },
      {
        title: t('extensions.colEnabled'),
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
                  aria-label={t(skill.enabled ? 'extensions.skillDisableAria' : 'extensions.skillEnableAria', { name: skill.name })}
                  onChange={(next) => void handleToggle(skill, next)}
                />
              </span>
            </Tooltip>
          );
        },
      },
    ],
    // handleToggle only closes over stable setters/mutation; data/pending drive the switch state.
    [data?.can_manage, pendingNames, t],
  );

  if (query.isLoading) {
    return (
      <div style={{ textAlign: 'center', padding: 48 }}>
        <Spin tip={t('extensions.loading')}><div style={{ padding: 24 }} /></Spin>
      </div>
    );
  }

  if (query.isError || !data) {
    const reason = query.error instanceof Error ? query.error.message : t('extensions.unknownError');
    return (
      <Result
        status="error"
        title={t('extensions.skillsLoadFailed')}
        subTitle={reason}
        extra={<Button type="primary" onClick={() => void query.refetch()}>{t('common.retry')}</Button>}
      />
    );
  }

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Space wrap>
        <Input.Search
          allowClear
          placeholder={t('extensions.skillsFilterPlaceholder')}
          aria-label={t('extensions.skillsFilterAria')}
          style={{ width: 280 }}
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
        />
        <Text type="secondary">{t('extensions.count', { visible: visible.length, total: data.skills.length })}</Text>
      </Space>
      <Table<SkillEntry>
        rowKey="name"
        size="small"
        columns={columns}
        dataSource={visible}
        pagination={visible.length > 20 ? { pageSize: 20 } : false}
        scroll={{ x: 1100 }}
        locale={{
          emptyText: <Empty description={data.skills.length === 0 ? t('extensions.skillsEmpty') : t('extensions.skillsNoMatch')} />,
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
