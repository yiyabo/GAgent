import React, { useEffect, useState, useCallback, useMemo } from 'react';
import { Row, Col, Card, Table, Tag, Button, Space, Input, Select, message, Tooltip, Popconfirm, Statistic, Progress, Tabs, Alert, Empty } from 'antd';
import {
  PlusOutlined,
  SearchOutlined,
  FilterOutlined,
  DeleteOutlined,
  EyeOutlined,
  ReloadOutlined,
  DatabaseOutlined,
  UnorderedListOutlined,
  ApartmentOutlined,
  ExclamationCircleOutlined,
  CloseCircleOutlined,
} from '@ant-design/icons';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { memoryApi } from '@api/memory';
import { useMemoryStore } from '@store/memory';
import SaveMemoryModal from '@components/memory/SaveMemoryModal';
import MemoryDetailDrawer from '@components/memory/MemoryDetailDrawer';
import MemoryGraph from '@components/memory/MemoryGraph';
import type { Memory } from '@/types';
import dayjs from 'dayjs';
import relativeTime from 'dayjs/plugin/relativeTime';
import 'dayjs/locale/zh-cn';

dayjs.extend(relativeTime);
dayjs.locale('zh-cn');

const { Search } = Input;

const useDebounce = <T,>(value: T, delay: number): T => {
  const [debouncedValue, setDebouncedValue] = useState<T>(value);

  useEffect(() => {
  const handler = setTimeout(() => {
  setDebouncedValue(value);
  }, delay);

  return () => {
  clearTimeout(handler);
  };
  }, [value, delay]);

  return debouncedValue;
};

const MemoryPage: React.FC = () => {
  const queryClient = useQueryClient();
  const {
  memories,
  filters,
  setMemories,
  setFilters,
  clearFilters,
  removeMemory,
  setSelectedMemory,
  setStats,
  getFilteredMemories,
  } = useMemoryStore();

  const [isModalOpen, setIsModalOpen] = useState(false);
  const [isDetailDrawerOpen, setIsDetailDrawerOpen] = useState(false);
  const [activeTab, setActiveTab] = useState('list');
  const [searchInput, setSearchInput] = useState(filters.search_query);

  const debouncedSearchQuery = useDebounce(searchInput, 500);

  useEffect(() => {
  if (debouncedSearchQuery !== filters.search_query) {
  setFilters({ search_query: debouncedSearchQuery });
  }
  }, [debouncedSearchQuery]);

  const { data: stats, error: statsError, isError: isStatsError } = useQuery({
  queryKey: ['memory-stats'],
  queryFn: () => memoryApi.getStats(),
  refetchInterval: 30000,
  retry: 2,
  retryDelay: (attemptIndex) => Math.min(1000 * 2 ** attemptIndex, 5000),
  onError: (error: any) => {
  message.error(`Failed to load statistics: ${error.message || 'Network error'}`);
  },
  });

  useEffect(() => {
  if (stats) {
  setStats(stats);
  }
  }, [stats, setStats]);

  const {
  data: queryResult,
  isLoading,
  error: queryError,
  isError: isQueryError,
  refetch
  } = useQuery({
  queryKey: ['memories', filters],
  queryFn: () => memoryApi.queryMemory({
  search_text: filters.search_query || ' ',
  memory_types: filters.memory_types as any[],
  importance_levels: filters.importance_levels as any[],
  limit: 100,
  min_similarity: filters.min_similarity,
  }),
  refetchInterval: 30000,
  retry: 2,
  retryDelay: (attemptIndex) => Math.min(1000 * 2 ** attemptIndex, 5000),
  onError: (error: any) => {
  message.error(`Failed to load memory: ${error.message || 'Network error'}`);
  },
  });

  useEffect(() => {
  if (queryResult?.memories) {
  setMemories(queryResult.memories);
  }
  }, [queryResult, setMemories]);

  const handleRefresh = useCallback(async () => {
  try {
  await refetch();
  message.success('Refreshed successfully');
  } catch (error: any) {
  message.error(`Refresh failed: ${error.message || 'Network error'}`);
  }
  }, [refetch]);

  const handleSearchChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
  setSearchInput(e.target.value);
  }, []);

  const handleSearchSubmit = useCallback(() => {
  setFilters({ search_query: searchInput });
  refetch();
  }, [searchInput, setFilters, refetch]);

  const handleClearFilters = useCallback(() => {
  setSearchInput('');
  clearFilters();
  refetch();
  }, [clearFilters, refetch]);

  const getMemoryTypeColor = (type: Memory['memory_type']) => {
  const colors = {
  conversation: 'blue',
  experience: 'green',
  knowledge: 'purple',
  context: 'orange',
  };
  return colors[type] || 'default';
  };

  const getImportanceColor = (importance: Memory['importance']) => {
  const colors = {
  critical: 'red',
  high: 'orange',
  medium: 'blue',
  low: 'default',
  temporary: 'gray',
  };
  return colors[importance] || 'default';
  };

  const columns = [
  {
  title: 'ID',
  dataIndex: 'id',
  key: 'id',
  width: 100,
  ellipsis: true,
  render: (id: string) => (
  <Tooltip title={id}>
  <span style={{ fontFamily: 'monospace', fontSize: '12px' }}>
  {id.substring(0, 8)}...
  </span>
  </Tooltip>
  ),
  },
  {
  title: 'Content',
  dataIndex: 'content',
  key: 'content',
  ellipsis: true,
  render: (content: string) => (
  <Tooltip title={content}>
  <span>{content.length > 100 ? content.substring(0, 100) + '...' : content}</span>
  </Tooltip>
  ),
  },
  {
  title: 'Type',
  dataIndex: 'memory_type',
  key: 'memory_type',
  width: 120,
  filters: [
  { text: 'Conversation', value: 'conversation' },
  { text: 'Experience', value: 'experience' },
  { text: 'Knowledge', value: 'knowledge' },
  { text: 'Context', value: 'context' },
  ],
  onFilter: (value: any, record: Memory) => record.memory_type === value,
  render: (type: Memory['memory_type']) => {
  const labels = {
  conversation: 'Conversation',
  experience: 'Experience',
  knowledge: 'Knowledge',
  context: 'Context',
  };
  return <Tag color={getMemoryTypeColor(type)}>{labels[type]}</Tag>;
  },
  },
  {
  title: 'Importance',
  dataIndex: 'importance',
  key: 'importance',
  width: 100,
  filters: [
  { text: 'Critical', value: 'critical' },
  { text: 'High', value: 'high' },
  { text: 'Medium', value: 'medium' },
  { text: 'Low', value: 'low' },
  { text: 'Temporary', value: 'temporary' },
  ],
  onFilter: (value: any, record: Memory) => record.importance === value,
  render: (importance: Memory['importance']) => {
  const labels = {
  critical: 'Critical',
  high: 'High',
  medium: 'Medium',
  low: 'Low',
  temporary: 'Temporary',
  };
  return <Tag color={getImportanceColor(importance)}>{labels[importance]}</Tag>;
  },
  },
  {
  title: 'Keywords',
  dataIndex: 'keywords',
  key: 'keywords',
  width: 200,
  render: (keywords: string[]) => (
  <Space size={[0, 4]} wrap>
  {keywords.slice(0, 3).map((keyword, index) => (
  <Tag key={index} style={{ fontSize: '11px' }}>{keyword}</Tag>
  ))}
  {keywords.length > 3 && <Tag>+{keywords.length - 3}</Tag>}
  </Space>
  ),
  },
  {
  title: 'Similarity',
  dataIndex: 'similarity',
  key: 'similarity',
  width: 100,
  render: (similarity?: number) => similarity !== undefined ? (
  <Tag color={similarity > 0.8 ? 'green' : similarity > 0.6 ? 'blue' : 'default'}>
  {(similarity * 100).toFixed(1)}%
  </Tag>
  ) : '-',
  sorter: (a: Memory, b: Memory) => (a.similarity || 0) - (b.similarity || 0),
  },
  {
  title: 'Retrievals',
  dataIndex: 'retrieval_count',
  key: 'retrieval_count',
  width: 100,
  sorter: (a: Memory, b: Memory) => a.retrieval_count - b.retrieval_count,
  },
  {
  title: 'Created',
  dataIndex: 'created_at',
  key: 'created_at',
  width: 160,
  render: (created_at: string) => (
  <Tooltip title={dayjs(created_at).format('YYYY-MM-DD HH:mm:ss')}>
  {dayjs(created_at).fromNow()}
  </Tooltip>
  ),
  sorter: (a: Memory, b: Memory) => dayjs(a.created_at).unix() - dayjs(b.created_at).unix(),
  },
  {
  title: 'Actions',
  key: 'action',
  width: 120,
  fixed: 'right' as const,
  render: (_: any, record: Memory) => (
  <Space size="small">
  <Tooltip title="View details">
  <Button
  type="text"
  size="small"
  icon={<EyeOutlined />}
  onClick={() => {
  setSelectedMemory(record);
  setIsDetailDrawerOpen(true);
  }}
  />
  </Tooltip>
  </Space>
  ),
  },
  ];

  return (
  <div>
  {}
  <div className="content-header">
  <h2 style={{ margin: 0, display: 'flex', alignItems: 'center', gap: '8px' }}>
  <DatabaseOutlined style={{ color: '#1890ff' }} />
  Memory
  </h2>
  <p style={{ margin: '8px 0 0 0', color: '#666' }}>
  Memory-MCP system overview and AI memory management
  </p>
  </div>

  <div className="content-body">
  {}
  {isStatsError && (
  <Alert
  message="Failed to load statistics"
  description={`Error: ${(statsError as any)?.message || 'Network error'}. Please check backend service status.`}
  type="error"
  showIcon
  closable
  icon={<CloseCircleOutlined />}
  style={{ marginBottom: 16 }}
  action={
  <Button size="small" danger onClick={handleRefresh}>
  Retry
  </Button>
  }
  />
  )}

  {}
  {isQueryError && (
  <Alert
  message="Failed to load memories"
  description={`Error: ${(queryError as any)?.message || 'Network error'}. Please check backend service status.`}
  type="error"
  showIcon
  closable
  icon={<CloseCircleOutlined />}
  style={{ marginBottom: 16 }}
  action={
  <Button size="small" danger onClick={handleRefresh}>
  Retry
  </Button>
  }
  />
  )}

  {}
  {stats && !isStatsError && (
  <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
  <Col xs={24} sm={12} md={6}>
  <Card>
  <Statistic
  title="Memories"
  value={stats.total_memories}
  prefix={<DatabaseOutlined />}
  valueStyle={{ color: '#1890ff' }}
  />
  </Card>
  </Col>
  <Col xs={24} sm={12} md={6}>
  <Card>
  <Statistic
  title="Avg Connections"
  value={stats.average_connections}
  precision={1}
  valueStyle={{ color: '#52c41a' }}
  suffix="links"
  />
  </Card>
  </Col>
  <Col xs={24} sm={12} md={6}>
  <Card>
  <Statistic
  title="Embedding Coverage"
  value={(stats.embedding_coverage * 100).toFixed(1)}
  valueStyle={{ color: stats.embedding_coverage > 0.8 ? '#52c41a' : '#faad14' }}
  suffix="%"
  />
  <Progress
  percent={stats.embedding_coverage * 100}
  size="small"
  showInfo={false}
  style={{ marginTop: 8 }}
  />
  </Card>
  </Col>
  <Col xs={24} sm={12} md={6}>
  <Card>
  <Statistic
  title="Evolution Count"
  value={stats.evolution_count || 0}
  valueStyle={{ color: '#722ed1' }}
  />
  </Card>
  </Col>
  </Row>
  )}

  {}
  <Card style={{ marginBottom: 16 }}>
  <Space wrap style={{ width: '100%', justifyContent: 'space-between' }}>
  <Space wrap>
  <Search
  placeholder="Search memory content, tags, or context..."
  allowClear
  style={{ width: 300 }}
  value={searchInput}
  onChange={handleSearchChange}
  onSearch={handleSearchSubmit}
  prefix={<SearchOutlined />}
  loading={isLoading}
  />

  <Select
  mode="multiple"
  placeholder="Memory type"
  style={{ minWidth: 180 }}
  value={filters.memory_types}
  onChange={(value) => setFilters({ memory_types: value })}
  options={[
  { label: 'Conversation', value: 'conversation' },
  { label: 'Experience', value: 'experience' },
  { label: 'Knowledge', value: 'knowledge' },
  { label: 'Context', value: 'context' },
  ]}
  maxTagCount="responsive"
  disabled={isLoading}
  />

  <Select
  mode="multiple"
  placeholder="Importance level"
  style={{ minWidth: 150 }}
  value={filters.importance_levels}
  onChange={(value) => setFilters({ importance_levels: value })}
  options={[
  { label: 'Critical', value: 'critical' },
  { label: 'High', value: 'high' },
  { label: 'Medium', value: 'medium' },
  { label: 'Low', value: 'low' },
  { label: 'Temporary', value: 'temporary' },
  ]}
  maxTagCount="responsive"
  disabled={isLoading}
  />

  <Button
  icon={<FilterOutlined />}
  onClick={handleClearFilters}
  disabled={isLoading}
  >
  Clear filters
  </Button>
  </Space>

  <Space>
  <Button
  icon={<ReloadOutlined />}
  onClick={handleRefresh}
  loading={isLoading}
  >
  Refresh
  </Button>
  <Button
  type="primary"
  icon={<PlusOutlined />}
  onClick={() => setIsModalOpen(true)}
  >
  Save Memory
  </Button>
  </Space>
  </Space>
  </Card>

  {}
  <Card>
  <Tabs
  activeKey={activeTab}
  onChange={setActiveTab}
  items={[
  {
  key: 'list',
  label: (
  <span>
  <UnorderedListOutlined />
  List
  </span>
  ),
  children: (
  <Table
  columns={columns}
  dataSource={getFilteredMemories()}
  rowKey="id"
  loading={isLoading}
  pagination={{
  showSizeChanger: true,
  showQuickJumper: true,
  showTotal: (total) => `Total ${total} memories`,
  defaultPageSize: 20,
  pageSizeOptions: ['10', '20', '50', '100'],
  }}
  scroll={{ x: 1400 }}
  />
  ),
  },
  {
  key: 'graph',
  label: (
  <span>
  <ApartmentOutlined />
  Graph
  </span>
  ),
  children: (
  <MemoryGraph
  onNodeClick={(memory) => {
  setSelectedMemory(memory);
  setIsDetailDrawerOpen(true);
  }}
  height="calc(100vh - 450px)"
  />
  ),
  },
  ]}
  />
  </Card>
  </div>

  {}
  <SaveMemoryModal
  open={isModalOpen}
  onCancel={() => setIsModalOpen(false)}
  />

  {}
  <MemoryDetailDrawer
  open={isDetailDrawerOpen}
  onClose={() => setIsDetailDrawerOpen(false)}
  />
  </div>
  );
};

export default MemoryPage;
