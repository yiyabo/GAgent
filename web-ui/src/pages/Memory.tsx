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

// 防抖hook - 用于搜索输入优化
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

  // 搜索防抖 - 500ms延迟
  const debouncedSearchQuery = useDebounce(searchInput, 500);

  // 同步防抖后的搜索值到store
  useEffect(() => {
    if (debouncedSearchQuery !== filters.search_query) {
      setFilters({ search_query: debouncedSearchQuery });
    }
  }, [debouncedSearchQuery]);

  // 获取统计信息
  const { data: stats, error: statsError, isError: isStatsError } = useQuery({
    queryKey: ['memory-stats'],
    queryFn: () => memoryApi.getStats(),
    refetchInterval: 30000,
    retry: 2,
    retryDelay: (attemptIndex) => Math.min(1000 * 2 ** attemptIndex, 5000),
    onError: (error: any) => {
      message.error(`获取统计信息失败: ${error.message || '网络错误'}`);
    },
  });

  useEffect(() => {
    if (stats) {
      setStats(stats);
    }
  }, [stats, setStats]);

  // 获取记忆列表
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
      message.error(`获取记忆列表失败: ${error.message || '网络错误'}`);
    },
  });

  useEffect(() => {
    if (queryResult?.memories) {
      setMemories(queryResult.memories);
    }
  }, [queryResult, setMemories]);

  // 处理刷新操作
  const handleRefresh = useCallback(async () => {
    try {
      await refetch();
      message.success('刷新成功');
    } catch (error: any) {
      message.error(`刷新失败: ${error.message || '网络错误'}`);
    }
  }, [refetch]);

  // 处理搜索输入
  const handleSearchChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    setSearchInput(e.target.value);
  }, []);

  // 处理搜索提交
  const handleSearchSubmit = useCallback(() => {
    setFilters({ search_query: searchInput });
    refetch();
  }, [searchInput, setFilters, refetch]);

  // 处理清除筛选
  const handleClearFilters = useCallback(() => {
    setSearchInput('');
    clearFilters();
    refetch();
  }, [clearFilters, refetch]);

  // 获取类型标签颜色
  const getMemoryTypeColor = (type: Memory['memory_type']) => {
    const colors = {
      conversation: 'blue',
      experience: 'green',
      knowledge: 'purple',
      context: 'orange',
    };
    return colors[type] || 'default';
  };

  // 获取重要性标签颜色
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

  // 表格列定义
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
      title: '内容',
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
      title: '类型',
      dataIndex: 'memory_type',
      key: 'memory_type',
      width: 120,
      filters: [
        { text: '对话', value: 'conversation' },
        { text: '经验', value: 'experience' },
        { text: '知识', value: 'knowledge' },
        { text: '上下文', value: 'context' },
      ],
      onFilter: (value: any, record: Memory) => record.memory_type === value,
      render: (type: Memory['memory_type']) => {
        const labels = {
          conversation: '对话',
          experience: '经验',
          knowledge: '知识',
          context: '上下文',
        };
        return <Tag color={getMemoryTypeColor(type)}>{labels[type]}</Tag>;
      },
    },
    {
      title: '重要性',
      dataIndex: 'importance',
      key: 'importance',
      width: 100,
      filters: [
        { text: '关键', value: 'critical' },
        { text: '高', value: 'high' },
        { text: '中', value: 'medium' },
        { text: '低', value: 'low' },
        { text: '临时', value: 'temporary' },
      ],
      onFilter: (value: any, record: Memory) => record.importance === value,
      render: (importance: Memory['importance']) => {
        const labels = {
          critical: '关键',
          high: '高',
          medium: '中',
          low: '低',
          temporary: '临时',
        };
        return <Tag color={getImportanceColor(importance)}>{labels[importance]}</Tag>;
      },
    },
    {
      title: '关键词',
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
      title: '相似度',
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
      title: '检索次数',
      dataIndex: 'retrieval_count',
      key: 'retrieval_count',
      width: 100,
      sorter: (a: Memory, b: Memory) => a.retrieval_count - b.retrieval_count,
    },
    {
      title: '创建时间',
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
      title: '操作',
      key: 'action',
      width: 120,
      fixed: 'right' as const,
      render: (_: any, record: Memory) => (
        <Space size="small">
          <Tooltip title="查看详情">
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
      {/* 页面标题 */}
      <div className="content-header">
        <h2 style={{ margin: 0, display: 'flex', alignItems: 'center', gap: '8px' }}>
          <DatabaseOutlined style={{ color: '#1890ff' }} />
          记忆管理
        </h2>
        <p style={{ margin: '8px 0 0 0', color: '#666' }}>
          Memory-MCP 智能记忆系统 - 存储、检索和管理AI记忆
        </p>
      </div>

      <div className="content-body">
        {/* 错误提示 - 统计信息 */}
        {isStatsError && (
          <Alert
            message="统计信息加载失败"
            description={`无法获取统计数据: ${(statsError as any)?.message || '网络错误'}。请检查后端服务是否正常运行。`}
            type="error"
            showIcon
            closable
            icon={<CloseCircleOutlined />}
            style={{ marginBottom: 16 }}
            action={
              <Button size="small" danger onClick={handleRefresh}>
                重试
              </Button>
            }
          />
        )}

        {/* 错误提示 - 记忆列表 */}
        {isQueryError && (
          <Alert
            message="记忆列表加载失败"
            description={`无法获取记忆数据: ${(queryError as any)?.message || '网络错误'}。请检查后端服务是否正常运行。`}
            type="error"
            showIcon
            closable
            icon={<CloseCircleOutlined />}
            style={{ marginBottom: 16 }}
            action={
              <Button size="small" danger onClick={handleRefresh}>
                重试
              </Button>
            }
          />
        )}

        {/* 统计看板 */}
        {stats && !isStatsError && (
          <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
            <Col xs={24} sm={12} md={6}>
              <Card>
                <Statistic
                  title="总记忆数"
                  value={stats.total_memories}
                  prefix={<DatabaseOutlined />}
                  valueStyle={{ color: '#1890ff' }}
                />
              </Card>
            </Col>
            <Col xs={24} sm={12} md={6}>
              <Card>
                <Statistic
                  title="平均连接数"
                  value={stats.average_connections}
                  precision={1}
                  valueStyle={{ color: '#52c41a' }}
                  suffix="条"
                />
              </Card>
            </Col>
            <Col xs={24} sm={12} md={6}>
              <Card>
                <Statistic
                  title="嵌入覆盖率"
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
                  title="进化次数"
                  value={stats.evolution_count || 0}
                  valueStyle={{ color: '#722ed1' }}
                />
              </Card>
            </Col>
          </Row>
        )}

        {/* 工具栏 */}
        <Card style={{ marginBottom: 16 }}>
          <Space wrap style={{ width: '100%', justifyContent: 'space-between' }}>
            <Space wrap>
              <Search
                placeholder="搜索记忆内容、关键词、标签..."
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
                placeholder="记忆类型"
                style={{ minWidth: 180 }}
                value={filters.memory_types}
                onChange={(value) => setFilters({ memory_types: value })}
                options={[
                  { label: '对话', value: 'conversation' },
                  { label: '经验', value: 'experience' },
                  { label: '知识', value: 'knowledge' },
                  { label: '上下文', value: 'context' },
                ]}
                maxTagCount="responsive"
                disabled={isLoading}
              />

              <Select
                mode="multiple"
                placeholder="重要性"
                style={{ minWidth: 150 }}
                value={filters.importance_levels}
                onChange={(value) => setFilters({ importance_levels: value })}
                options={[
                  { label: '关键', value: 'critical' },
                  { label: '高', value: 'high' },
                  { label: '中', value: 'medium' },
                  { label: '低', value: 'low' },
                  { label: '临时', value: 'temporary' },
                ]}
                maxTagCount="responsive"
                disabled={isLoading}
              />

              <Button
                icon={<FilterOutlined />}
                onClick={handleClearFilters}
                disabled={isLoading}
              >
                清除筛选
              </Button>
            </Space>

            <Space>
              <Button
                icon={<ReloadOutlined />}
                onClick={handleRefresh}
                loading={isLoading}
              >
                刷新
              </Button>
              <Button
                type="primary"
                icon={<PlusOutlined />}
                onClick={() => setIsModalOpen(true)}
              >
                保存新记忆
              </Button>
            </Space>
          </Space>
        </Card>

        {/* 视图切换标签 */}
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
                    列表视图
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
                      showTotal: (total) => `共 ${total} 条记忆`,
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
                    图谱视图
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

      {/* 保存记忆Modal */}
      <SaveMemoryModal
        open={isModalOpen}
        onCancel={() => setIsModalOpen(false)}
      />

      {/* 记忆详情Drawer */}
      <MemoryDetailDrawer
        open={isDetailDrawerOpen}
        onClose={() => setIsDetailDrawerOpen(false)}
      />
    </div>
  );
};

export default MemoryPage;
