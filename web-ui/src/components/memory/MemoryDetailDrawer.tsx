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

const { Title, Text, Paragraph } = Typography;

interface MemoryDetailDrawerProps {
  open: boolean;
  onClose: () => void;
}

const MemoryDetailDrawer: React.FC<MemoryDetailDrawerProps> = ({ open, onClose }) => {
  const { selectedMemory } = useMemoryStore();

  if (!selectedMemory) {
  return null;
  }

  const memory: Memory = selectedMemory;

  const getMemoryTypeInfo = (type: Memory['memory_type']) => {
  const typeMap = {
  conversation: { color: 'blue', label: 'Conversation', desc: 'Dialogue memory' },
  experience: { color: 'green', label: 'Experience', desc: 'Execution experience' },
  knowledge: { color: 'purple', label: 'Knowledge', desc: 'Fact or insight memory' },
  context: { color: 'orange', label: 'Context', desc: 'Session context snapshot' },
  };
  return typeMap[type] || { color: 'default', label: type, desc: '' };
  };

  const getImportanceInfo = (importance: Memory['importance']) => {
  const importanceMap = {
  critical: { color: 'red', label: 'Critical', desc: 'Highest retention priority' },
  high: { color: 'orange', label: 'High', desc: 'High retention priority' },
  medium: { color: 'blue', label: 'Medium', desc: 'Moderate retention priority' },
  low: { color: 'default', label: 'Low', desc: 'Low retention priority' },
  temporary: { color: 'gray', label: 'Temporary', desc: 'Short-lived memory' },
  };
  return importanceMap[importance] || { color: 'default', label: importance, desc: '' };
  };

  const typeInfo = getMemoryTypeInfo(memory.memory_type);
  const importanceInfo = getImportanceInfo(memory.importance);

  return (
  <Drawer
  title={
  <Space>
  <FileTextOutlined style={{ color: '#1890ff' }} />
  <span>Memory Detail</span>
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
  title="Retrieval Count"
  value={memory.retrieval_count}
  prefix={<EyeOutlined />}
  valueStyle={{ color: '#1890ff', fontSize: '20px' }}
  />
  </Card>
  </Col>
  <Col span={8}>
  <Card>
  <Statistic
  title="Similarity"
  value={memory.similarity ? (memory.similarity * 100).toFixed(1) : '-'}
  suffix={memory.similarity ? '%' : ''}
  valueStyle={{
  color: memory.similarity
  ? memory.similarity > 0.8 ? '#52c41a' : memory.similarity > 0.6 ? '#1890ff' : '#faad14'
  : '#999',
  fontSize: '20px'
  }}
  />
  </Card>
  </Col>
  <Col span={8}>
  <Card>
  <Statistic
  title="Connections"
  value={memory.links?.length || 0}
  prefix={<LinkOutlined />}
  valueStyle={{ color: '#722ed1', fontSize: '20px' }}
  />
  </Card>
  </Col>
  </Row>

  {}
  <Card title="Overview" size="small" style={{ marginBottom: 16 }}>
  <Descriptions column={1} size="small">
  <Descriptions.Item label="Memory ID">
  <Text code copyable style={{ fontSize: '12px' }}>
  {memory.id}
  </Text>
  </Descriptions.Item>

  <Descriptions.Item label="Memory Type">
  <Tag color={typeInfo.color} style={{ marginRight: 8 }}>
  {typeInfo.label}
  </Tag>
  <Text type="secondary" style={{ fontSize: '12px' }}>
  {typeInfo.desc}
  </Text>
  </Descriptions.Item>

  <Descriptions.Item label="Importance">
  <Tag color={importanceInfo.color} style={{ marginRight: 8 }}>
  {importanceInfo.label}
  </Tag>
  <Text type="secondary" style={{ fontSize: '12px' }}>
  {importanceInfo.desc}
  </Text>
  </Descriptions.Item>

  <Descriptions.Item label="Context">
  <Text>{memory.context || 'General'}</Text>
  </Descriptions.Item>

  {memory.related_task_id && (
  <Descriptions.Item label="Related Task">
  <Tag color="blue">Task #{memory.related_task_id}</Tag>
  </Descriptions.Item>
  )}
  </Descriptions>
  </Card>

  {}
  <Card title="Memory Content" size="small" style={{ marginBottom: 16 }}>
  <Paragraph
  style={{
  background: '#fafafa',
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
  <Card title={<Space><TagsOutlined /> Tags</Space>} size="small" style={{ marginBottom: 16 }}>
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
  <Card title={<Space><KeyOutlined /> Keywords</Space>} size="small" style={{ marginBottom: 16 }}>
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
  <Card title={<Space><LinkOutlined /> Memory Connections</Space>} size="small" style={{ marginBottom: 16 }}>
  <Space direction="vertical" style={{ width: '100%' }}>
  {memory.links.map((link, index) => (
  <div
  key={index}
  style={{
  padding: '8px 12px',
  background: '#f5f5f5',
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
  Similarity: {(link.similarity * 100).toFixed(1)}%
  </Tag>
  </div>
  ))}
  </Space>
  </Card>
  )}

  {}
  <Card title={<Space><CalendarOutlined /> Timeline</Space>} size="small">
  <Descriptions column={1} size="small">
  <Descriptions.Item label="Created">
  <Space>
  <ClockCircleOutlined />
  <Text>{dayjs(memory.created_at).format('YYYY-MM-DD HH:mm:ss')}</Text>
  <Text type="secondary">({dayjs(memory.created_at).fromNow()})</Text>
  </Space>
  </Descriptions.Item>

  {memory.last_accessed && (
  <Descriptions.Item label="Last Accessed">
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
  background: '#f0f5ff',
  borderRadius: '6px',
  fontSize: '12px',
  color: '#666'
  }}>
  <div><strong>Hint:</strong></div>
  <ul style={{ marginTop: '8px', marginBottom: 0, paddingLeft: '20px' }}>
  <li>Use keywords and tags to improve search precision.</li>
  <li>Review content and context together for better interpretation.</li>
  <li>Connected memories provide useful related background.</li>
  </ul>
  </div>
  </Drawer>
  );
};

export default MemoryDetailDrawer;
