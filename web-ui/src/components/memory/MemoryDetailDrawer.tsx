import React from 'react';
import { Drawer, Descriptions, Tag, Space, Typography, Divider, Card, Statistic, Row, Col } from 'antd';
import {
  ClockCircleOutlined,
  TagsOutlined,
  KeyOutlined,
  LinkOutlined,
  FileTextOutlined,
  CalendarOutlined,
  EyeOutlined,
} from '@ant-design/icons';
import { useMemoryStore } from '@store/memory';
import type { Memory } from '@/types';
import dayjs from 'dayjs';

import { useT } from '@/i18n/useT';
import { memoryTypeLabel, memoryImportanceLabel } from '@/i18n/memoryLabels';
const { Title, Text, Paragraph } = Typography;

interface MemoryDetailDrawerProps {
  open: boolean;
  onClose: () => void;
}

const MemoryDetailDrawer: React.FC<MemoryDetailDrawerProps> = ({ open, onClose }) => {
  const { selectedMemory } = useMemoryStore();
  const t = useT();

  if (!selectedMemory) {
  return null;
  }

  const memory: Memory = selectedMemory;

  const getMemoryTypeInfo = (type: Memory['memory_type']) => {
  const typeMap = {
  conversation: { color: 'blue', label: memoryTypeLabel(t, 'conversation'), desc: t('memory.typeDesc.conversation') },
  experience: { color: 'green', label: memoryTypeLabel(t, 'experience'), desc: t('memory.typeDesc.experience') },
  knowledge: { color: 'purple', label: memoryTypeLabel(t, 'knowledge'), desc: t('memory.typeDesc.knowledge') },
  context: { color: 'orange', label: memoryTypeLabel(t, 'context'), desc: t('memory.typeDesc.context') },
  };
  return typeMap[type] || { color: 'default', label: type, desc: '' };
  };

  const getImportanceInfo = (importance: Memory['importance']) => {
  const importanceMap = {
  critical: { color: 'red', label: memoryImportanceLabel(t, 'critical'), desc: t('memory.importanceDesc.critical') },
  high: { color: 'orange', label: memoryImportanceLabel(t, 'high'), desc: t('memory.importanceDesc.high') },
  medium: { color: 'blue', label: memoryImportanceLabel(t, 'medium'), desc: t('memory.importanceDesc.medium') },
  low: { color: 'default', label: memoryImportanceLabel(t, 'low'), desc: t('memory.importanceDesc.low') },
  temporary: { color: 'gray', label: memoryImportanceLabel(t, 'temporary'), desc: t('memory.importanceDesc.temporary') },
  };
  return importanceMap[importance] || { color: 'default', label: importance, desc: '' };
  };

  const typeInfo = getMemoryTypeInfo(memory.memory_type);
  const importanceInfo = getImportanceInfo(memory.importance);

  return (
  <Drawer
  title={
  <Space>
  <FileTextOutlined style={{ color: 'var(--color-action)' }} />
  <span>{t('memory.detail.title')}</span>
  </Space>
  }
  placement="right"
  onClose={onClose}
  open={open}
  width={600}
  destroyOnClose
  >
  {}
  <Row gutter={16} style={{ marginBottom: 24 }}>
  <Col span={8}>
  <Card>
  <Statistic
  title={t('memory.detail.retrievalCount')}
  value={memory.retrieval_count}
  prefix={<EyeOutlined />}
  valueStyle={{ color: 'var(--color-action)', fontSize: '20px' }}
  />
  </Card>
  </Col>
  <Col span={8}>
  <Card>
  <Statistic
  title={t('memory.detail.similarity')}
  value={memory.similarity ? (memory.similarity * 100).toFixed(1) : '-'}
  suffix={memory.similarity ? '%' : ''}
  valueStyle={{
  color: memory.similarity
  ? memory.similarity > 0.8 ? 'var(--status-success-fg)' : memory.similarity > 0.6 ? 'var(--color-action)' : 'var(--status-warning-fg)'
  : 'var(--text-tertiary)',
  fontSize: '20px'
  }}
  />
  </Card>
  </Col>
  <Col span={8}>
  <Card>
  <Statistic
  title={t('memory.detail.connections')}
  value={memory.links?.length || 0}
  prefix={<LinkOutlined />}
  valueStyle={{ color: 'var(--status-blocked-fg)', fontSize: '20px' }}
  />
  </Card>
  </Col>
  </Row>

  {}
  <Card title={t('memory.detail.overview')} size="small" style={{ marginBottom: 16 }}>
  <Descriptions column={1} size="small">
  <Descriptions.Item label={t('memory.detail.memoryId')}>
  <Text code copyable style={{ fontSize: '12px' }}>
  {memory.id}
  </Text>
  </Descriptions.Item>

  <Descriptions.Item label={t('memory.detail.memoryType')}>
  <Tag color={typeInfo.color} style={{ marginRight: 8 }}>
  {typeInfo.label}
  </Tag>
  <Text type="secondary" style={{ fontSize: '12px' }}>
  {typeInfo.desc}
  </Text>
  </Descriptions.Item>

  <Descriptions.Item label={t('memory.detail.importance')}>
  <Tag color={importanceInfo.color} style={{ marginRight: 8 }}>
  {importanceInfo.label}
  </Tag>
  <Text type="secondary" style={{ fontSize: '12px' }}>
  {importanceInfo.desc}
  </Text>
  </Descriptions.Item>

  <Descriptions.Item label={t('memory.detail.context')}>
  <Text>{memory.context || 'General'}</Text>
  </Descriptions.Item>

  {memory.related_task_id && (
  <Descriptions.Item label={t('memory.detail.relatedTask')}>
  <Tag color="blue">Task #{memory.related_task_id}</Tag>
  </Descriptions.Item>
  )}
  </Descriptions>
  </Card>

  {}
  <Card title={t('memory.detail.content')} size="small" style={{ marginBottom: 16 }}>
  <Paragraph
  style={{
  background: 'var(--surface-sunken)',
  padding: '12px',
  borderRadius: '6px',
  whiteSpace: 'pre-wrap',
  wordBreak: 'break-word',
  fontSize: '14px',
  lineHeight: '1.6',
  }}
  >
  {memory.content}
  </Paragraph>
  </Card>

  {}
  {memory.tags && memory.tags.length > 0 && (
  <Card title={<Space><TagsOutlined /> {t('memory.detail.tags')}</Space>} size="small" style={{ marginBottom: 16 }}>
  <Space size={[0, 8]} wrap>
  {memory.tags.map((tag, index) => (
  <Tag key={index} color="blue">
  {tag}
  </Tag>
  ))}
  </Space>
  </Card>
  )}

  {}
  {memory.keywords && memory.keywords.length > 0 && (
  <Card title={<Space><KeyOutlined /> {t('memory.detail.keywords')}</Space>} size="small" style={{ marginBottom: 16 }}>
  <Space size={[0, 8]} wrap>
  {memory.keywords.map((keyword, index) => (
  <Tag key={index} color="purple">
  {keyword}
  </Tag>
  ))}
  </Space>
  </Card>
  )}

  {}
  {memory.links && memory.links.length > 0 && (
  <Card title={<Space><LinkOutlined /> {t('memory.detail.links')}</Space>} size="small" style={{ marginBottom: 16 }}>
  <Space direction="vertical" style={{ width: '100%' }}>
  {memory.links.map((link, index) => (
  <div
  key={index}
  style={{
  padding: '8px 12px',
  background: 'var(--surface-sunken)',
  borderRadius: '4px',
  display: 'flex',
  justifyContent: 'space-between',
  alignItems: 'center',
  }}
  >
  <Text
  code
  style={{ fontSize: '12px', flex: 1 }}
  ellipsis={{ tooltip: link.memory_id }}
  >
  {link.memory_id}
  </Text>
  <Tag color={link.similarity > 0.8 ? 'green' : link.similarity > 0.6 ? 'blue' : 'default'}>
  {t('memory.detail.linkSimilarity', { percent: (link.similarity * 100).toFixed(1) })}
  </Tag>
  </div>
  ))}
  </Space>
  </Card>
  )}

  {}
  <Card title={<Space><CalendarOutlined /> {t('memory.detail.timeline')}</Space>} size="small">
  <Descriptions column={1} size="small">
  <Descriptions.Item label={t('memory.detail.created')}>
  <Space>
  <ClockCircleOutlined />
  <Text>{dayjs(memory.created_at).format('YYYY-MM-DD HH:mm:ss')}</Text>
  <Text type="secondary">({dayjs(memory.created_at).fromNow()})</Text>
  </Space>
  </Descriptions.Item>

  {memory.last_accessed && (
  <Descriptions.Item label={t('memory.detail.lastAccessed')}>
  <Space>
  <EyeOutlined />
  <Text>{dayjs(memory.last_accessed).format('YYYY-MM-DD HH:mm:ss')}</Text>
  <Text type="secondary">({dayjs(memory.last_accessed).fromNow()})</Text>
  </Space>
  </Descriptions.Item>
  )}
  </Descriptions>
  </Card>

  <Divider />

  {}
  <div style={{
  padding: '12px',
  background: 'var(--color-action-bg)',
  borderRadius: '6px',
  fontSize: '12px',
  color: 'var(--text-secondary)'
  }}>
  <div><strong>{t('memory.detail.hint')}</strong></div>
  <ul style={{ marginTop: '8px', marginBottom: 0, paddingLeft: '20px' }}>
  <li>{t('memory.detail.hint1')}</li>
  <li>{t('memory.detail.hint2')}</li>
  <li>{t('memory.detail.hint3')}</li>
  </ul>
  </div>
  </Drawer>
  );
};

export default MemoryDetailDrawer;
