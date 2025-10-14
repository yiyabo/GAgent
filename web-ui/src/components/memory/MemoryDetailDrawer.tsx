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

  // 获取类型标签样式
  const getMemoryTypeInfo = (type: Memory['memory_type']) => {
    const typeMap = {
      conversation: { color: 'blue', label: '对话', desc: '重要的对话内容' },
      experience: { color: 'green', label: '经验', desc: '操作经验和学习成果' },
      knowledge: { color: 'purple', label: '知识', desc: '领域知识和概念' },
      context: { color: 'orange', label: '上下文', desc: '环境和背景信息' },
    };
    return typeMap[type] || { color: 'default', label: type, desc: '' };
  };

  // 获取重要性标签样式
  const getImportanceInfo = (importance: Memory['importance']) => {
    const importanceMap = {
      critical: { color: 'red', label: '关键', desc: '永久保存' },
      high: { color: 'orange', label: '高', desc: '长期保存' },
      medium: { color: 'blue', label: '中', desc: '定期清理' },
      low: { color: 'default', label: '低', desc: '短期保存' },
      temporary: { color: 'gray', label: '临时', desc: '自动清理' },
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
          <span>记忆详情</span>
        </Space>
      }
      placement="right"
      onClose={onClose}
      open={open}
      width={600}
      destroyOnClose
    >
      {/* 统计信息卡片 */}
      <Row gutter={16} style={{ marginBottom: 24 }}>
        <Col span={8}>
          <Card>
            <Statistic
              title="检索次数"
              value={memory.retrieval_count}
              prefix={<EyeOutlined />}
              valueStyle={{ color: '#1890ff', fontSize: '20px' }}
            />
          </Card>
        </Col>
        <Col span={8}>
          <Card>
            <Statistic
              title="相似度"
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
              title="连接数"
              value={memory.links?.length || 0}
              prefix={<LinkOutlined />}
              valueStyle={{ color: '#722ed1', fontSize: '20px' }}
            />
          </Card>
        </Col>
      </Row>

      {/* 基础信息 */}
      <Card title="📋 基础信息" size="small" style={{ marginBottom: 16 }}>
        <Descriptions column={1} size="small">
          <Descriptions.Item label="记忆ID">
            <Text code copyable style={{ fontSize: '12px' }}>
              {memory.id}
            </Text>
          </Descriptions.Item>

          <Descriptions.Item label="记忆类型">
            <Tag color={typeInfo.color} style={{ marginRight: 8 }}>
              {typeInfo.label}
            </Tag>
            <Text type="secondary" style={{ fontSize: '12px' }}>
              {typeInfo.desc}
            </Text>
          </Descriptions.Item>

          <Descriptions.Item label="重要性">
            <Tag color={importanceInfo.color} style={{ marginRight: 8 }}>
              {importanceInfo.label}
            </Tag>
            <Text type="secondary" style={{ fontSize: '12px' }}>
              {importanceInfo.desc}
            </Text>
          </Descriptions.Item>

          <Descriptions.Item label="上下文">
            <Text>{memory.context || 'General'}</Text>
          </Descriptions.Item>

          {memory.related_task_id && (
            <Descriptions.Item label="关联任务">
              <Tag color="blue">Task #{memory.related_task_id}</Tag>
            </Descriptions.Item>
          )}
        </Descriptions>
      </Card>

      {/* 记忆内容 */}
      <Card title="📝 记忆内容" size="small" style={{ marginBottom: 16 }}>
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

      {/* 标签 */}
      {memory.tags && memory.tags.length > 0 && (
        <Card title={<Space><TagsOutlined /> 标签</Space>} size="small" style={{ marginBottom: 16 }}>
          <Space size={[0, 8]} wrap>
            {memory.tags.map((tag, index) => (
              <Tag key={index} color="blue">
                {tag}
              </Tag>
            ))}
          </Space>
        </Card>
      )}

      {/* 关键词 */}
      {memory.keywords && memory.keywords.length > 0 && (
        <Card title={<Space><KeyOutlined /> 关键词</Space>} size="small" style={{ marginBottom: 16 }}>
          <Space size={[0, 8]} wrap>
            {memory.keywords.map((keyword, index) => (
              <Tag key={index} color="purple">
                {keyword}
              </Tag>
            ))}
          </Space>
        </Card>
      )}

      {/* 记忆连接 */}
      {memory.links && memory.links.length > 0 && (
        <Card title={<Space><LinkOutlined /> 记忆连接</Space>} size="small" style={{ marginBottom: 16 }}>
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
                  相似度: {(link.similarity * 100).toFixed(1)}%
                </Tag>
              </div>
            ))}
          </Space>
        </Card>
      )}

      {/* 时间信息 */}
      <Card title={<Space><CalendarOutlined /> 时间信息</Space>} size="small">
        <Descriptions column={1} size="small">
          <Descriptions.Item label="创建时间">
            <Space>
              <ClockCircleOutlined />
              <Text>{dayjs(memory.created_at).format('YYYY-MM-DD HH:mm:ss')}</Text>
              <Text type="secondary">({dayjs(memory.created_at).fromNow()})</Text>
            </Space>
          </Descriptions.Item>

          {memory.last_accessed && (
            <Descriptions.Item label="最后访问">
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

      {/* 底部提示 */}
      <div style={{
        padding: '12px',
        background: '#f0f5ff',
        borderRadius: '6px',
        fontSize: '12px',
        color: '#666'
      }}>
        <div>💡 <strong>提示:</strong></div>
        <ul style={{ marginTop: '8px', marginBottom: 0, paddingLeft: '20px' }}>
          <li>检索次数表示这条记忆被搜索命中的次数</li>
          <li>相似度表示当前搜索与记忆内容的匹配度</li>
          <li>记忆连接显示与其他记忆的语义关联</li>
        </ul>
      </div>
    </Drawer>
  );
};

export default MemoryDetailDrawer;
