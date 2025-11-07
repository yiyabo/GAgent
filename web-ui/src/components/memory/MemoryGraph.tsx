import React, { useEffect, useRef, useState, useCallback, useMemo } from 'react';
import { Network } from 'vis-network';
import { DataSet } from 'vis-data';
import { Card, Spin, Button, Space, Select, Input, message, Badge, Radio, Slider, Tooltip, Empty, Alert } from 'antd';
import { ReloadOutlined, ExpandOutlined, FullscreenOutlined, SearchOutlined, InboxOutlined } from '@ant-design/icons';
import { useMemoryStore } from '@store/memory';
import type { Memory } from '@/types';

type GraphNode = {
  id: string;
  label: string;
  title: string;
  shape: string;
  size: number;
  color: Record<string, any>;
  font: Record<string, any>;
  borderWidth: number;
  shadow: Record<string, any>;
  memoryData?: Memory;
};

interface NetworkData {
  nodes: DataSet<GraphNode>;
  edges: DataSet<Record<string, any>>;
}

interface MemoryGraphProps {
  onNodeClick?: (memory: Memory) => void;
  height?: string;
}

const MemoryGraph: React.FC<MemoryGraphProps> = ({ onNodeClick, height = '600px' }) => {
  const networkRef = useRef<HTMLDivElement>(null);
  const networkInstance = useRef<Network | null>(null);
  const { memories, getFilteredMemories } = useMemoryStore();

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [layout, setLayout] = useState<'force' | 'hierarchical' | 'circular'>('force');
  const [searchText, setSearchText] = useState('');
  const [minSimilarity, setMinSimilarity] = useState(0.5);
  const [showLabels, setShowLabels] = useState(true);

  // 获取重要性对应的节点大小
  const getNodeSize = (importance: Memory['importance'], retrievalCount: number) => {
    const baseSize = {
      critical: 50,
      high: 40,
      medium: 30,
      low: 25,
      temporary: 20,
    }[importance] || 30;

    // 根据检索次数调整大小 (最多增加50%)
    const bonus = Math.min(retrievalCount * 2, baseSize * 0.5);
    return baseSize + bonus;
  };

  // 获取重要性对应的颜色
  const getNodeColor = (importance: Memory['importance']) => {
    const colors = {
      critical: { background: '#ff4d4f', border: '#cf1322', highlight: '#ff7875' },
      high: { background: '#fa8c16', border: '#d46b08', highlight: '#ffa940' },
      medium: { background: '#1890ff', border: '#096dd9', highlight: '#40a9ff' },
      low: { background: '#52c41a', border: '#389e0d', highlight: '#73d13d' },
      temporary: { background: '#d9d9d9', border: '#8c8c8c', highlight: '#bfbfbf' },
    };
    return colors[importance] || colors.medium;
  };

  // 获取记忆类型对应的形状
  const getNodeShape = (type: Memory['memory_type']) => {
    const shapes = {
      conversation: 'box',
      experience: 'ellipse',
      knowledge: 'diamond',
      context: 'star',
    };
    return shapes[type] || 'dot';
  };

  // 获取边的颜色和宽度 (基于相似度)
  const getEdgeStyle = (similarity: number) => {
    let color, width;

    if (similarity > 0.8) {
      color = '#52c41a'; // 绿色 - 高相似度
      width = 5;
    } else if (similarity > 0.6) {
      color = '#1890ff'; // 蓝色 - 中相似度
      width = 3;
    } else {
      color = '#d9d9d9'; // 灰色 - 低相似度
      width = 2;
    }

    return {
      color: { color, highlight: color, hover: color },
      width,
    };
  };

  // 构建网络图数据
  const buildNetworkData = useCallback<() => NetworkData>(() => {
    setLoading(true);
    setError(null);

    try {
      let filteredMemories = getFilteredMemories();

      // 检查是否有数据
      if (filteredMemories.length === 0) {
        setLoading(false);
        return {
          nodes: new DataSet<GraphNode>([]),
          edges: new DataSet<Record<string, any>>([]),
        };
      }

      // 应用搜索过滤
      if (searchText) {
        filteredMemories = filteredMemories.filter(m =>
          m.content.toLowerCase().includes(searchText.toLowerCase()) ||
          m.keywords.some(k => k.toLowerCase().includes(searchText.toLowerCase())) ||
          m.tags.some(t => t.toLowerCase().includes(searchText.toLowerCase()))
        );
      }

      // 构建节点
      const nodes: GraphNode[] = filteredMemories.map((memory) => {
        const size = getNodeSize(memory.importance, memory.retrieval_count);
        const colors = getNodeColor(memory.importance);
        const shape = getNodeShape(memory.memory_type);

        // 生成节点标签 (优先使用关键词)
        const label = showLabels
          ? memory.keywords.length > 0
            ? memory.keywords.slice(0, 2).join(', ')
            : memory.content.substring(0, 30) + (memory.content.length > 30 ? '...' : '')
          : '';

        // 生成详细的tooltip
        const title = `
🧠 记忆详情
━━━━━━━━━━━━━━━━
📋 ID: ${memory.id.substring(0, 8)}...
📝 内容: ${memory.content.substring(0, 100)}${memory.content.length > 100 ? '...' : ''}
🏷️ 类型: ${memory.memory_type}
⭐ 重要性: ${memory.importance}
🔑 关键词: ${memory.keywords.join(', ') || '无'}
👁️ 检索次数: ${memory.retrieval_count}
🔗 连接数: ${memory.links?.length || 0}
        `.trim();

        return {
          id: memory.id,
          label,
          title,
          shape,
          size,
          color: colors,
          font: {
            size: 12,
            color: '#ffffff',
            face: 'Arial',
            strokeWidth: 3,
            strokeColor: '#000000',
          },
          borderWidth: 2,
          shadow: {
            enabled: true,
            color: 'rgba(0,0,0,0.3)',
            size: 10,
            x: 2,
            y: 2,
          },
          memoryData: memory,
        };
      });

      // 构建边 (基于记忆连接)
      const edges: any[] = [];
      const edgeSet = new Set<string>(); // 用于去重

      filteredMemories.forEach(memory => {
        if (memory.links && memory.links.length > 0) {
          memory.links.forEach(link => {
            // 检查目标记忆是否在当前过滤结果中
            const targetExists = filteredMemories.some(m => m.id === link.memory_id);
            if (targetExists && link.similarity >= minSimilarity) {
              // 创建唯一的边ID (确保双向边只显示一次)
              const edgeId = [memory.id, link.memory_id].sort().join('-');
              if (!edgeSet.has(edgeId)) {
                edgeSet.add(edgeId);

                const edgeStyle = getEdgeStyle(link.similarity);

                edges.push({
                  from: memory.id,
                  to: link.memory_id,
                  ...edgeStyle,
                  smooth: {
                    type: 'continuous',
                    roundness: 0.5,
                  },
                  label: `${(link.similarity * 100).toFixed(0)}%`,
                  font: {
                    size: 10,
                    color: '#666',
                    strokeWidth: 0,
                  },
                  labelHighlightBold: false,
                  arrows: {
                    to: {
                      enabled: false,
                    },
                  },
                });
              }
            }
          });
        }
      });

      return {
        nodes: new DataSet<GraphNode>(nodes),
        edges: new DataSet<Record<string, any>>(edges),
      };
    } catch (err: any) {
      console.error('❌ Failed to build network data:', err);
      setError(err.message || '构建图谱数据失败');
      message.error(`构建图谱数据失败: ${err.message || '未知错误'}`);
      return {
        nodes: new DataSet<GraphNode>([]),
        edges: new DataSet<Record<string, any>>([]),
      };
    } finally {
      setLoading(false);
    }
  }, [getFilteredMemories, searchText, minSimilarity, showLabels]);

  // 获取布局配置
  const getLayoutOptions = () => {
    switch (layout) {
      case 'hierarchical':
        return {
          hierarchical: {
            direction: 'UD',
            sortMethod: 'directed',
            nodeSpacing: 150,
            levelSeparation: 200,
          },
        };
      case 'circular':
        return {
          hierarchical: false,
        };
      case 'force':
      default:
        return {
          hierarchical: false,
        };
    }
  };

  // 初始化或更新网络图
  useEffect(() => {
    if (networkRef.current && memories.length > 0) {
      console.log('🎨 Building memory graph with', memories.length, 'memories');

      const data = buildNetworkData();

      const options: any = {
        layout: getLayoutOptions(),
        nodes: {
          borderWidth: 2,
          shadow: true,
          font: {
            size: 12,
            color: '#ffffff',
          },
          chosen: {
            node: (values: any) => {
              values.borderWidth = 4;
              values.shadow = true;
              values.shadowSize = 15;
            },
          },
        },
        edges: {
          smooth: {
            type: 'continuous',
            roundness: 0.5,
          },
          chosen: {
            edge: (values: any) => {
              values.width = values.width * 1.5;
            },
          },
        },
        physics: {
          enabled: layout === 'force',
          stabilization: {
            enabled: true,
            iterations: 200,
          },
          barnesHut: {
            gravitationalConstant: -9000,
            centralGravity: 0.3,
            springLength: 150,
            springConstant: 0.04,
            damping: 0.09,
            avoidOverlap: 0.5,
          },
        },
        interaction: {
          hover: true,
          selectConnectedEdges: true,
          hoverConnectedEdges: true,
          tooltipDelay: 300,
          zoomView: true,
          dragView: true,
          dragNodes: true,
        },
      };

      // 销毁现有实例
      if (networkInstance.current) {
        networkInstance.current.destroy();
      }

      // 创建新实例
      networkInstance.current = new Network(networkRef.current, data, options);

      // 绑定点击事件
      networkInstance.current.on('click', (params) => {
        if (params.nodes.length > 0) {
          const nodeId = params.nodes[0];
          const rawNode = data.nodes.get(nodeId);
          const node = Array.isArray(rawNode)
            ? ((rawNode[0] as GraphNode | undefined) ?? null)
            : ((rawNode as GraphNode | undefined) ?? null);
          const memory = node?.memoryData;

          if (memory && onNodeClick) {
            onNodeClick(memory);
          }
        }
      });

      // 自适应视图
      setTimeout(() => {
        networkInstance.current?.fit({
          animation: {
            duration: 1000,
            easingFunction: 'easeInOutQuad',
          },
        });
      }, 500);
    }

    return () => {
      if (networkInstance.current) {
        networkInstance.current.destroy();
        networkInstance.current = null;
      }
    };
  }, [memories, layout, searchText, minSimilarity, showLabels, onNodeClick]);

  const handleRefresh = () => {
    if (networkInstance.current) {
      const data = buildNetworkData();
      networkInstance.current.setData(data);
      networkInstance.current.fit();
    }
  };

  const handleFitView = () => {
    networkInstance.current?.fit({
      animation: {
        duration: 1000,
        easingFunction: 'easeInOutQuad',
      },
    });
  };

  const handleFullscreen = () => {
    if (networkRef.current) {
      if (document.fullscreenElement) {
        document.exitFullscreen();
      } else {
        networkRef.current.requestFullscreen();
      }
    }
  };

  // 图例组件
  const Legend = () => (
    <div
      style={{
        position: 'absolute',
        top: 10,
        left: 10,
        background: 'rgba(255,255,255,0.95)',
        padding: '12px',
        borderRadius: '8px',
        border: '1px solid #d9d9d9',
        fontSize: '12px',
        zIndex: 1000,
        maxWidth: '250px',
      }}
    >
      <div style={{ fontWeight: 'bold', marginBottom: '8px', color: '#1890ff' }}>
        🗺️ 记忆图谱图例
      </div>

      <div style={{ marginBottom: '8px' }}>
        <div style={{ fontWeight: 'bold', fontSize: '11px', color: '#666', marginBottom: '4px' }}>
          节点大小 = 重要性 + 检索次数
        </div>
      </div>

      <div style={{ marginBottom: '8px' }}>
        <div style={{ fontWeight: 'bold', fontSize: '11px', color: '#666', marginBottom: '4px' }}>
          重要性颜色:
        </div>
        <div style={{ marginLeft: '8px' }}>
          <div style={{ marginBottom: '2px' }}>
            <span style={{ color: '#ff4d4f' }}>●</span> 关键
          </div>
          <div style={{ marginBottom: '2px' }}>
            <span style={{ color: '#fa8c16' }}>●</span> 高
          </div>
          <div style={{ marginBottom: '2px' }}>
            <span style={{ color: '#1890ff' }}>●</span> 中
          </div>
          <div style={{ marginBottom: '2px' }}>
            <span style={{ color: '#52c41a' }}>●</span> 低
          </div>
          <div>
            <span style={{ color: '#d9d9d9' }}>●</span> 临时
          </div>
        </div>
      </div>

      <div style={{ marginBottom: '8px' }}>
        <div style={{ fontWeight: 'bold', fontSize: '11px', color: '#666', marginBottom: '4px' }}>
          类型形状:
        </div>
        <div style={{ marginLeft: '8px' }}>
          <div>■ 对话 | ● 经验 | ◆ 知识 | ★ 上下文</div>
        </div>
      </div>

      <div>
        <div style={{ fontWeight: 'bold', fontSize: '11px', color: '#666', marginBottom: '4px' }}>
          连接线:
        </div>
        <div style={{ marginLeft: '8px' }}>
          <div style={{ marginBottom: '2px' }}>
            <span style={{ color: '#52c41a', fontWeight: 'bold' }}>━━</span> 高相似度 (&gt;80%)
          </div>
          <div style={{ marginBottom: '2px' }}>
            <span style={{ color: '#1890ff', fontWeight: 'bold' }}>━━</span> 中相似度 (60-80%)
          </div>
          <div>
            <span style={{ color: '#d9d9d9', fontWeight: 'bold' }}>━━</span> 低相似度 (&lt;60%)
          </div>
        </div>
      </div>

      <div style={{ fontSize: '10px', color: '#999', marginTop: '8px', borderTop: '1px solid #f0f0f0', paddingTop: '6px' }}>
        💡 点击节点查看详情<br />
        🔍 拖拽可移动节点<br />
        🖱️ 滚轮可缩放视图
      </div>
    </div>
  );

  const filteredMemories = getFilteredMemories();
  const memoryCount = filteredMemories.length;

  // 计算连接统计
  const totalConnections = filteredMemories.reduce((sum, m) => sum + (m.links?.length || 0), 0);
  const avgConnections = memoryCount > 0 ? (totalConnections / memoryCount).toFixed(1) : 0;

  return (
    <Card
      title={
        <Space>
          <span>🗺️ 记忆连接图谱</span>
          <Badge count={memoryCount} style={{ backgroundColor: '#52c41a' }} />
          <span style={{ fontSize: '12px', color: '#999' }}>
            (平均连接: {avgConnections})
          </span>
        </Space>
      }
      style={{ height: '100%', position: 'relative' }}
      extra={
        <Space wrap>
          <Input.Search
            placeholder="搜索记忆"
            style={{ width: 200 }}
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
            allowClear
            prefix={<SearchOutlined />}
            size="small"
          />

          <Tooltip title="相似度阈值">
            <div style={{ width: 150, display: 'inline-block' }}>
              <Slider
                min={0}
                max={1}
                step={0.1}
                value={minSimilarity}
                onChange={setMinSimilarity}
                marks={{ 0: '0%', 0.5: '50%', 1: '100%' }}
                tooltip={{ formatter: (v) => `${(v! * 100).toFixed(0)}%` }}
              />
            </div>
          </Tooltip>

          <Radio.Group
            size="small"
            value={layout}
            onChange={(e) => setLayout(e.target.value)}
            options={[
              { label: '力导向', value: 'force' },
              { label: '层次', value: 'hierarchical' },
              { label: '环形', value: 'circular' },
            ]}
          />

          <Button
            size="small"
            type={showLabels ? 'primary' : 'default'}
            onClick={() => setShowLabels(!showLabels)}
          >
            {showLabels ? '隐藏标签' : '显示标签'}
          </Button>

          <Button size="small" icon={<ExpandOutlined />} onClick={handleFitView} title="适应视图" />
          <Button size="small" icon={<FullscreenOutlined />} onClick={handleFullscreen} title="全屏" />
          <Button size="small" icon={<ReloadOutlined />} onClick={handleRefresh} loading={loading}>
            刷新
          </Button>
        </Space>
      }
    >
      <Spin spinning={loading} tip="构建记忆图谱中...">
        {/* 错误提示 */}
        {error && (
          <Alert
            message="图谱加载失败"
            description={error}
            type="error"
            showIcon
            closable
            onClose={() => setError(null)}
            style={{ marginBottom: 16 }}
          />
        )}

        {/* 空状态 */}
        {memoryCount === 0 && !loading ? (
          <div style={{ height, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <Empty
              image={<InboxOutlined style={{ fontSize: 64, color: '#d9d9d9' }} />}
              imageStyle={{ height: 80 }}
              description={
                <span style={{ color: '#999' }}>
                  暂无记忆数据
                  <br />
                  <span style={{ fontSize: '12px' }}>
                    尝试调整筛选条件或保存新的记忆
                  </span>
                </span>
              }
            >
              <Button type="primary" icon={<ReloadOutlined />} onClick={handleRefresh}>
                刷新数据
              </Button>
            </Empty>
          </div>
        ) : (
          <div style={{ position: 'relative' }}>
            <div
              ref={networkRef}
              style={{
                height,
                width: '100%',
                border: '1px solid #d9d9d9',
                borderRadius: '6px',
                backgroundColor: '#fafafa',
              }}
            />
            <Legend />
          </div>
        )}
      </Spin>
    </Card>
  );
};

export default MemoryGraph;
