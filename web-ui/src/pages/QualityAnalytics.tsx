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
import { qualityApi, type QualityCase, type QualitySummary, type SatisfactionLevel } from '@api/quality';

const { Title, Text, Paragraph } = Typography;

const LEVEL_META: Record<string, { label: string; color: string }> = {
  satisfied: { label: 'Satisfied', color: 'green' },
  acceptable: { label: 'Acceptable', color: 'blue' },
  negative: { label: 'Negative', color: 'orange' },
  angry: { label: 'Angry', color: 'red' },
};

const WINDOW_OPTIONS = [
  { value: 24, label: 'Last 24 hours' },
  { value: 168, label: 'Last 7 days' },
  { value: 720, label: 'Last 30 days' },
];

function LevelTag({ level }: { level?: SatisfactionLevel | null }) {
  if (!level) return <Tag>Pending</Tag>;
  const meta = LEVEL_META[level] || { label: level, color: 'default' };
  return <Tag color={meta.color}>{meta.label}</Tag>;
}

function Breakdown({ title, data }: { title: string; data: QualitySummary['failure_modes'] }) {
  return (
    <Card size="small" title={title} style={{ height: '100%' }}>
      {data.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="No data" /> : (
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
        if (active) setError(reason instanceof Error ? reason.message : 'Unable to load quality analytics.');
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
      title: 'Assessment',
      key: 'assessment',
      width: 140,
      render: (_, item) => (
        <Space direction="vertical" size={2}>
          <LevelTag level={item.satisfaction_level} />
          {item.confidence !== null && item.confidence !== undefined && (
            <Text type="secondary">{Math.round(item.confidence * 100)}% confidence</Text>
          )}
        </Space>
      ),
    },
    {
      title: 'User goal',
      dataIndex: 'user_goal',
      key: 'user_goal',
      ellipsis: true,
      render: (goal: string) => <Text>{goal || 'No captured goal'}</Text>,
    },
    {
      title: 'Evidence',
      key: 'evidence',
      render: (_, item) => item.evidence.length ? (
        <Space direction="vertical" size={2}>
          {item.evidence.slice(0, 2).map((evidence, index) => (
            <Text key={`${item.id}-${index}`} type="secondary" ellipsis={{ tooltip: evidence.quote }}>
              {evidence.quote}
            </Text>
          ))}
        </Space>
      ) : <Text type="secondary">Awaiting observation</Text>,
    },
    {
      title: 'Attribution',
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
          <Title level={3} style={{ margin: 0 }}>Conversation Quality</Title>
          <Text type="secondary">Evidence-based, asynchronous assessments of completed chat runs.</Text>
        </div>
        <Select value={hours} options={WINDOW_OPTIONS} onChange={setHours} style={{ minWidth: 150 }} />
      </div>

      <div className="content-body" style={{ marginTop: 20 }}>
        {error && <Alert type="error" message="Quality analytics unavailable" description={error} showIcon style={{ marginBottom: 16 }} />}
        {loading ? <div style={{ textAlign: 'center', padding: 80 }}><Spin size="large" /></div> : (
          <>
            <Alert
              type="info"
              showIcon
              message="Observation-only mode"
              description="No-response assessments are provisional and low confidence. They are not explicit user feedback."
              style={{ marginBottom: 16 }}
            />
            <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
              <Col xs={24} sm={12} lg={6}><Card><Statistic title="Captured runs" value={summary?.total || 0} /></Card></Col>
              <Col xs={24} sm={12} lg={6}><Card><Statistic title="Awaiting observation" value={summary?.pending || 0} /></Card></Col>
              <Col xs={24} sm={12} lg={6}><Card><Statistic title="Evaluated" value={evaluated} /></Card></Col>
              <Col xs={24} sm={12} lg={6}><Card><Statistic title="Average confidence" value={Math.round((summary?.average_confidence || 0) * 100)} suffix="%" /></Card></Col>
            </Row>
            <Card title="Satisfaction distribution" style={{ marginBottom: 16 }}>
              <Row gutter={[16, 16]}>
                {(['satisfied', 'acceptable', 'negative', 'angry'] as SatisfactionLevel[]).map((level) => {
                  const count = levelCounts.get(level) || 0;
                  const meta = LEVEL_META[level];
                  return (
                    <Col xs={24} sm={12} lg={6} key={level}>
                      <Space direction="vertical" style={{ width: '100%' }} size={4}>
                        <LevelTag level={level} />
                        <Progress percent={evaluated ? Math.round((count / evaluated) * 100) : 0} strokeColor={meta.color} />
                        <Text type="secondary">{count} assessments</Text>
                      </Space>
                    </Col>
                  );
                })}
              </Row>
            </Card>
            <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
              <Col xs={24} lg={8}><Breakdown title="Failure modes" data={summary?.failure_modes || []} /></Col>
              <Col xs={24} lg={8}><Breakdown title="Responsible stages" data={summary?.responsible_stages || []} /></Col>
              <Col xs={24} lg={8}><Breakdown title="Request tiers" data={summary?.request_tiers || []} /></Col>
            </Row>
            <Card title="Recent assessments">
              <Table<QualityCase>
                rowKey="id"
                columns={columns}
                dataSource={cases}
                pagination={{ pageSize: 10, showSizeChanger: false }}
                locale={{ emptyText: 'No quality snapshots have been captured in this window.' }}
                expandable={{
                  expandedRowRender: (item) => (
                    <Descriptions size="small" column={1}>
                      <Descriptions.Item label="Status">{item.status}</Descriptions.Item>
                      <Descriptions.Item label="Evaluation basis">{item.evaluation_basis || 'pending'}</Descriptions.Item>
                      <Descriptions.Item label="Evidence explanation">
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
