import { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Card,
  Col,
  Descriptions,
  Empty,
  Progress,
  Row,
  Select,
  Space,
  Spin,
  Statistic,
  Table,
  Tag,
  Typography,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { statusLabel } from '@/i18n/statusLabels';
import { translate } from '@/i18n/useT';
import { qualityApi, type QualityCase, type QualitySummary, type SatisfactionLevel } from '@api/quality';

import { useT } from '@/i18n/useT';
const { Title, Text, Paragraph } = Typography;

const LEVEL_META: Record<string, { color: string }> = {
  satisfied: { color: 'green' },
  acceptable: { color: 'blue' },
  negative: { color: 'orange' },
  angry: { color: 'red' },
};

const WINDOW_HOURS = [24, 168, 720] as const;

function LevelTag({ level }: { level?: SatisfactionLevel | null }) {
  const t = useT();
  if (!level) return <Tag>{t('quality.pending')}</Tag>;
  const meta = LEVEL_META[level] || { color: 'default' };
  const label = level in LEVEL_META ? t(`quality.level.${level}` as 'quality.level.satisfied') : level;
  return <Tag color={meta.color}>{label}</Tag>;
}

function Breakdown({ title, data }: { title: string; data: QualitySummary['failure_modes'] }) {
  const t = useT();
  return (
    <Card size="small" title={title} style={{ height: '100%' }}>
      {data.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('quality.noData')} /> : (
        <Space direction="vertical" size="small" style={{ width: '100%' }}>
          {data.slice(0, 6).map((item) => (
            <div key={item.name} style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
              <Text ellipsis={{ tooltip: item.name }}>{item.name.replace(/_/g, ' ')}</Text>
              <Text strong>{item.count}</Text>
            </div>
          ))}
        </Space>
      )}
    </Card>
  );
}

const QualityAnalytics = () => {
  const t = useT();
  const [hours, setHours] = useState(168);
  const [summary, setSummary] = useState<QualitySummary | null>(null);
  const [cases, setCases] = useState<QualityCase[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    Promise.all([qualityApi.getSummary(hours), qualityApi.getCases({ hours, limit: 50 })])
      .then(([nextSummary, nextCases]) => {
        if (!active) return;
        setSummary(nextSummary);
        setCases(nextCases);
      })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : translate('quality.loadFailed'));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => { active = false; };
  }, [hours]);

  const levelCounts = useMemo(() => new Map(
    (summary?.by_satisfaction_level || []).map((item) => [item.name, item.count]),
  ), [summary]);
  const evaluated = summary?.evaluated || 0;

  const columns: ColumnsType<QualityCase> = [
    {
      title: t('quality.col.assessment'),
      key: 'assessment',
      width: 140,
      render: (_, item) => (
        <Space direction="vertical" size={2}>
          <LevelTag level={item.satisfaction_level} />
          {item.confidence !== null && item.confidence !== undefined && (
            <Text type="secondary">{t('quality.confidence', { percent: Math.round(item.confidence * 100) })}</Text>
          )}
        </Space>
      ),
    },
    {
      title: t('quality.col.goal'),
      dataIndex: 'user_goal',
      key: 'user_goal',
      ellipsis: true,
      render: (goal: string) => <Text>{goal || t('quality.noGoal')}</Text>,
    },
    {
      title: t('quality.col.evidence'),
      key: 'evidence',
      render: (_, item) => item.evidence.length ? (
        <Space direction="vertical" size={2}>
          {item.evidence.slice(0, 2).map((evidence, index) => (
            <Text key={`${item.id}-${index}`} type="secondary" ellipsis={{ tooltip: evidence.quote }}>
              {evidence.quote}
            </Text>
          ))}
        </Space>
      ) : <Text type="secondary">{t('quality.awaiting')}</Text>,
    },
    {
      title: t('quality.col.attribution'),
      key: 'attribution',
      width: 240,
      render: (_, item) => (
        <Space wrap size={[4, 4]}>
          {item.failure_modes.map((mode) => <Tag color="orange" key={mode}>{mode}</Tag>)}
          {item.responsible_stages.map((stage) => <Tag color="geekblue" key={stage}>{stage}</Tag>)}
          {!item.failure_modes.length && !item.responsible_stages.length && <Text type="secondary">—</Text>}
        </Space>
      ),
    },
  ];

  return (
    <div>
      <div className="content-header" style={{ display: 'flex', justifyContent: 'space-between', gap: 16 }}>
        <div>
          <Title level={3} style={{ margin: 0 }}>{t('quality.title')}</Title>
          <Text type="secondary">{t('quality.subtitle')}</Text>
        </div>
        <Select value={hours} options={WINDOW_HOURS.map((value) => ({ value, label: t(`quality.window${value}` as 'quality.window24') }))} onChange={setHours} style={{ minWidth: 150 }} />
      </div>

      <div className="content-body" style={{ marginTop: 20 }}>
        {error && <Alert type="error" message={t('quality.unavailable')} description={error} showIcon style={{ marginBottom: 16 }} />}
        {loading ? <div style={{ textAlign: 'center', padding: 80 }}><Spin size="large" /></div> : (
          <>
            <Alert
              type="info"
              showIcon
              message={t('quality.observationOnly')}
              description={t('quality.observationNote')}
              style={{ marginBottom: 16 }}
            />
            <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
              <Col xs={24} sm={12} lg={6}><Card><Statistic title={t('quality.capturedRuns')} value={summary?.total || 0} /></Card></Col>
              <Col xs={24} sm={12} lg={6}><Card><Statistic title={t('quality.awaiting')} value={summary?.pending || 0} /></Card></Col>
              <Col xs={24} sm={12} lg={6}><Card><Statistic title={t('quality.evaluated')} value={evaluated} /></Card></Col>
              <Col xs={24} sm={12} lg={6}><Card><Statistic title={t('quality.avgConfidence')} value={Math.round((summary?.average_confidence || 0) * 100)} suffix="%" /></Card></Col>
            </Row>
            <Card title={t('quality.distribution')} style={{ marginBottom: 16 }}>
              <Row gutter={[16, 16]}>
                {(['satisfied', 'acceptable', 'negative', 'angry'] as SatisfactionLevel[]).map((level) => {
                  const count = levelCounts.get(level) || 0;
                  const meta = LEVEL_META[level];
                  return (
                    <Col xs={24} sm={12} lg={6} key={level}>
                      <Space direction="vertical" style={{ width: '100%' }} size={4}>
                        <LevelTag level={level} />
                        <Progress percent={evaluated ? Math.round((count / evaluated) * 100) : 0} strokeColor={meta.color} />
                        <Text type="secondary">{t('quality.assessmentsCount', { count })}</Text>
                      </Space>
                    </Col>
                  );
                })}
              </Row>
            </Card>
            <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
              <Col xs={24} lg={8}><Breakdown title={t('quality.failureModes')} data={summary?.failure_modes || []} /></Col>
              <Col xs={24} lg={8}><Breakdown title={t('quality.stages')} data={summary?.responsible_stages || []} /></Col>
              <Col xs={24} lg={8}><Breakdown title={t('quality.intents')} data={summary?.intents || []} /></Col>
            </Row>
            <Card title={t('quality.recent')}>
              <Table<QualityCase>
                rowKey="id"
                columns={columns}
                dataSource={cases}
                pagination={{ pageSize: 10, showSizeChanger: false }}
                locale={{ emptyText: t('quality.emptyTable') }}
                expandable={{
                  expandedRowRender: (item) => (
                    <Descriptions size="small" column={1}>
                      <Descriptions.Item label={t('quality.detail.status')}>{item.status ? statusLabel(t, item.status) : ''}</Descriptions.Item>
                      <Descriptions.Item label={t('quality.detail.basis')}>{item.evaluation_basis || t('quality.basisPending')}</Descriptions.Item>
                      <Descriptions.Item label={t('quality.detail.explanation')}>
                        {item.evidence.length ? item.evidence.map((evidence, index) => (
                          <Paragraph key={`${item.id}-detail-${index}`} style={{ marginBottom: 6 }}>
                            <Text strong>{evidence.source}: </Text>{evidence.explanation}
                          </Paragraph>
                        )) : '—'}
                      </Descriptions.Item>
                    </Descriptions>
                  ),
                }}
              />
            </Card>
          </>
        )}
      </div>
    </div>
  );
};

export default QualityAnalytics;
