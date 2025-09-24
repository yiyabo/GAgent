import React, { useEffect, useRef, useState } from 'react';
import { Network } from 'vis-network';
import { DataSet } from 'vis-data';
import { Card, Spin, Button, Space, Select, Input, message, Badge } from 'antd';
import { ReloadOutlined, ExpandOutlined } from '@ant-design/icons';
import { tasksApi } from '@api/tasks';

interface DAGVisualizationProps {
  onNodeClick?: (taskId: number, taskData: any) => void;
  onNodeDoubleClick?: (taskId: number, taskData: any) => void;
  height?: string;
  interactive?: boolean;
  showToolbar?: boolean;
}

interface Task {
  id: number;
  name: string;
  status: string;
  task_type: string;
  depth: number;
  parent_id?: number;
  path?: string;
  priority?: number;
}

const DAGVisualization: React.FC<DAGVisualizationProps> = ({
  onNodeClick,
  onNodeDoubleClick,
}) => {
  const networkRef = useRef<HTMLDivElement>(null);
  const networkInstance = useRef<Network | null>(null);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchText, setSearchText] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [stats, setStats] = useState<any>(null);

  // 状态颜色映射
  const getStatusColor = (status: string) => {
    switch (status) {
      case 'completed':
      case 'done':
        return '#52c41a'; // 绿色
      case 'running':
      case 'executing':
        return '#1890ff'; // 蓝色
      case 'pending':
        return '#faad14'; // 橙色
      case 'failed':
      case 'error':
        return '#ff4d4f'; // 红色
      default:
        return '#d9d9d9'; // 灰色
    }
  };

  // 任务类型形状映射 (Agent工作流程优化，安全处理undefined)
  const getNodeShape = (taskType?: string) => {
    if (!taskType) return 'dot';
    
    switch (taskType.toUpperCase()) {
      case 'ROOT':
        return 'star';     // ⭐ ROOT任务用星形，更突出
      case 'COMPOSITE':
        return 'box';      // 📦 COMPOSITE任务用方形
      case 'ATOMIC':
        return 'ellipse';  // ⚪ ATOMIC任务用椭圆
      default:
        return 'dot';
    }
  };

  // 获取节点大小 (根据任务重要性，安全处理undefined)
  const getNodeSize = (taskType?: string, hasChildren: boolean = false) => {
    if (!taskType) return 15;
    
    switch (taskType.toUpperCase()) {
      case 'ROOT':
        return 40;  // ROOT最大
      case 'COMPOSITE':
        return hasChildren ? 30 : 25;  // COMPOSITE中等，有子节点时稍大
      case 'ATOMIC':
        return 20;  // ATOMIC最小
      default:
        return 15;
    }
  };

  // 加载任务数据
  const loadTasks = async () => {
    try {
      setLoading(true);
      console.log('🔄 Loading tasks for DAG visualization...');
      
      const [allTasks, taskStats] = await Promise.all([
        tasksApi.getAllTasks(),
        tasksApi.getTaskStats()
      ]);
      
      console.log('📊 Raw tasks data:', allTasks);
      console.log('📈 Task stats:', taskStats);
      console.log('📋 Tasks count:', allTasks.length);
      
      if (allTasks && allTasks.length > 0) {
        console.log('✅ 前5个任务示例:', allTasks.slice(0, 5));
        setTasks(allTasks);
        console.log(`✅ 加载了 ${allTasks.length} 个任务`);
      } else {
        console.warn('⚠️ 未获取到任务数据或数据为空');
      }
      
      setStats(taskStats);
    } catch (error: any) {
      console.error('❌ Failed to load tasks:', error);
      console.error('❌ Error details:', error.response?.data || error.message);
      message.error(`加载任务数据失败: ${error.message}`);
    } finally {
      setLoading(false);
    }
  };

  // 构建网络图数据
  const buildNetworkData = () => {
    let filteredTasks = tasks;

    // 应用搜索过滤
    if (searchText) {
      filteredTasks = filteredTasks.filter(task =>
        task.name.toLowerCase().includes(searchText.toLowerCase())
      );
    }

    // 应用状态过滤
    if (statusFilter !== 'all') {
      filteredTasks = filteredTasks.filter(task => task.status === statusFilter);
    }

    // 构建节点 (Agent工作流程优化)
    const nodes = filteredTasks.map(task => {
      // 检查是否有子节点
      const hasChildren = filteredTasks.some(t => t.parent_id === task.id);
      
      // 智能缩短名称，优先保留关键词
      let displayName = task.name;
      if (task.name.length > 50) {
        // 移除"ROOT:"、"COMPOSITE:"、"ATOMIC:"前缀
        const cleanName = task.name.replace(/^(ROOT|COMPOSITE|ATOMIC):\s*/i, '');
        displayName = cleanName.length > 45 
          ? cleanName.substring(0, 45) + '...' 
          : cleanName;
      }
      
      // 根据任务类型设置不同的边框颜色 (安全处理undefined)
      const getBorderColor = (taskType?: string) => {
        if (!taskType) return '#d9d9d9'; // 默认灰色
        
        switch (taskType.toUpperCase()) {
          case 'ROOT': return '#722ed1';     // 紫色 - ROOT
          case 'COMPOSITE': return '#1890ff'; // 蓝色 - COMPOSITE  
          case 'ATOMIC': return '#52c41a';    // 绿色 - ATOMIC
          default: return '#d9d9d9';
        }
      };
      
      return {
        id: task.id,
        label: displayName,
        title: `🔍 任务详情\n━━━━━━━━━━━━━━━━\n📋 ID: ${task.id}\n📝 名称: ${task.name}\n🎯 状态: ${task.status}\n📦 类型: ${task.task_type}\n📊 层级: ${task.depth}\n${hasChildren ? '👥 包含子任务' : ''}`,
        color: {
          background: getStatusColor(task.status),
          border: getBorderColor(task.task_type),
        },
        shape: getNodeShape(task.task_type),
        size: getNodeSize(task.task_type, hasChildren),
        level: task.depth,
        font: { 
          size: task.task_type?.toUpperCase() === 'ROOT' ? 14 : 12, 
          color: '#ffffff',
          face: 'Arial',
          strokeWidth: 1,
          strokeColor: '#000000'
        },
        borderWidth: task.task_type?.toUpperCase() === 'ROOT' ? 3 : 2,
        shadow: {
          enabled: true,
          color: 'rgba(0,0,0,0.3)',
          size: task.task_type?.toUpperCase() === 'ROOT' ? 15 : 10,
          x: 2,
          y: 2
        },
        taskData: task,
      };
    });

    // 构建边（基于parent_id关系，Agent工作流程优化）
    const edges: any[] = [];
    filteredTasks.forEach(task => {
      if (task.parent_id) {
        // 确保父节点也在过滤后的任务中
        const parentTask = filteredTasks.find(t => t.id === task.parent_id);
        if (parentTask) {
          // 根据关系类型设置不同的边样式
          const getEdgeStyle = (fromType: string, toType: string) => {
            if (fromType.toUpperCase() === 'ROOT' && toType.toUpperCase() === 'COMPOSITE') {
              return {
                color: { color: '#722ed1' },  // 紫色 - ROOT到COMPOSITE
                width: 3,
                dashes: false
              };
            } else if (fromType.toUpperCase() === 'COMPOSITE' && toType.toUpperCase() === 'ATOMIC') {
              return {
                color: { color: '#1890ff' },  // 蓝色 - COMPOSITE到ATOMIC  
                width: 2,
                dashes: false
              };
            } else {
              return {
                color: { color: '#52c41a' },  // 绿色 - 其他关系
                width: 2,
                dashes: false
              };
            }
          };
          
          const edgeStyle = getEdgeStyle(parentTask.task_type, task.task_type);
          
          edges.push({
            from: task.parent_id,
            to: task.id,
            arrows: { 
              to: { 
                enabled: true, 
                scaleFactor: 1.2,
                type: 'arrow'
              } 
            },
            ...edgeStyle,
            smooth: { 
              type: 'cubicBezier',
              forceDirection: 'vertical',
              roundness: 0.4
            },
            label: `${parentTask.task_type} → ${task.task_type}`,
            font: { size: 10, color: '#666', strokeWidth: 0 },
            labelHighlightBold: false,
          });
        }
      }
    });

    return {
      nodes: new DataSet(nodes),
      edges: new DataSet(edges),
    };
  };

  // 初始化或更新网络图
  useEffect(() => {
    if (networkRef.current && tasks.length > 0) {
      console.log('🎨 Building network visualization with', tasks.length, 'tasks');
      
      const data = buildNetworkData();
      
      const options: any = {
        layout: {
          hierarchical: {
            direction: 'UD',           // 从上到下
            sortMethod: 'directed',    // 有向图排序
            nodeSpacing: 200,          // 增大节点间距，Agent工作流程需要更多空间
            levelSeparation: 120,      // 增大层级间距
            treeSpacing: 250,          // 树间距
            blockShifting: true,       // 允许块移动优化
            edgeMinimization: true,    // 边最小化
            parentCentralization: true // 父节点居中
          },
        },
        nodes: {
          borderWidth: 2,
          shadow: {
            enabled: true,
            color: 'rgba(0,0,0,0.3)',
            size: 10,
            x: 2,
            y: 2
          },
          chosen: {
            node: (values: any, _id: any, _selected: any, _hovering: any) => {
              values.borderWidth = 4;
              values.shadow = true;
              values.shadowColor = 'rgba(0,0,0,0.5)';
              values.shadowSize = 15;
            }
          }
        },
        edges: {
          width: 2,
          arrows: { to: { enabled: true, scaleFactor: 1.2 } },
          smooth: { 
            type: 'cubicBezier',
            forceDirection: 'vertical',
            roundness: 0.4
          },
          chosen: {
            edge: (values: any, _id: any, _selected: any, _hovering: any) => {
              values.width = 4;
              values.color = '#ff6b6b';
            }
          }
        },
        physics: {
          enabled: false,            // 禁用物理引擎，使用严格的层次布局
        },
        interaction: {
          hover: true,               // 启用悬停效果
          selectConnectedEdges: true, // 选择关联边
          hoverConnectedEdges: true, // 悬停时高亮关联边
          tooltipDelay: 300,         // 工具提示延迟
          zoomView: true,            // 允许缩放
          dragView: true,            // 允许拖拽视图
          dragNodes: false,          // 禁用拖拽节点（保持层次结构）
        },
        // Agent工作流程专用配置
        configure: {
          enabled: false
        },
        locale: 'zh',
      };

      // 销毁现有实例
      if (networkInstance.current) {
        networkInstance.current.destroy();
      }

      // 创建新实例
      networkInstance.current = new Network(networkRef.current, data, options);

      // 绑定事件
      networkInstance.current.on('click', (params) => {
        if (params.nodes.length > 0) {
          const nodeId = params.nodes[0];
          const task = tasks.find(t => t.id === nodeId);
          console.log('🖱️ Node clicked:', nodeId, task);
          
          if (task && onNodeClick) {
            onNodeClick(nodeId, task);
          }
        }
      });

      networkInstance.current.on('doubleClick', (params) => {
        if (params.nodes.length > 0) {
          const nodeId = params.nodes[0];
          const task = tasks.find(t => t.id === nodeId);
          console.log('🖱️ Node double-clicked:', nodeId, task);
          
          if (task && onNodeDoubleClick) {
            onNodeDoubleClick(nodeId, task);
          }
        }
      });

      // 自适应视图
      setTimeout(() => {
        networkInstance.current?.fit();
      }, 500);
    }

    return () => {
      if (networkInstance.current) {
        networkInstance.current.destroy();
        networkInstance.current = null;
      }
    };
  }, [tasks, searchText, statusFilter, onNodeClick, onNodeDoubleClick]);

  // 组件挂载时加载数据
  useEffect(() => {
    loadTasks();
  }, []);

  // 监听任务更新事件（从聊天系统等地方触发）
  useEffect(() => {
    const handleTasksUpdated = (event: CustomEvent) => {
      console.log('🔄 DAG收到任务更新事件:', event.detail);
      // 自动刷新任务数据
      loadTasks();
    };

    window.addEventListener('tasksUpdated', handleTasksUpdated as EventListener);
    
    return () => {
      window.removeEventListener('tasksUpdated', handleTasksUpdated as EventListener);
    };
  }, []);

  const handleRefresh = () => {
    loadTasks();
  };

  const handleFitView = () => {
    networkInstance.current?.fit();
  };

  // Agent工作流程图例组件
  const AgentLegend = () => (
    <div style={{ 
      position: 'absolute', 
      top: 10, 
      left: 10, 
      background: 'rgba(255,255,255,0.95)', 
      padding: '12px', 
      borderRadius: '8px',
      border: '1px solid #d9d9d9',
      fontSize: '12px',
      zIndex: 1000,
      maxWidth: '200px'
    }}>
      <div style={{ fontWeight: 'bold', marginBottom: '8px', color: '#1890ff' }}>
        🤖 Agent工作流程图例
      </div>
      <div style={{ marginBottom: '4px' }}>
        <span style={{ color: '#722ed1' }}>⭐</span> ROOT - 目标任务
      </div>
      <div style={{ marginBottom: '4px' }}>
        <span style={{ color: '#1890ff' }}>📦</span> COMPOSITE - 复合任务
      </div>
      <div style={{ marginBottom: '4px' }}>
        <span style={{ color: '#52c41a' }}>⚪</span> ATOMIC - 原子任务
      </div>
      <div style={{ fontSize: '10px', color: '#666', marginTop: '6px' }}>
        💡 点击节点查看详情
      </div>
    </div>
  );

  return (
    <Card 
      title={
        <Space>
          <span>🤖 Agent任务依赖图</span>
          {stats && (
            <Badge count={stats.total} style={{ backgroundColor: '#52c41a' }} />
          )}
        </Space>
      }
      style={{ height: '100%', position: 'relative' }}
      extra={
        <Space wrap>
          <Input.Search
            placeholder="搜索任务"
            style={{ width: 200 }}
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
            onSearch={(value) => setSearchText(value)}
            allowClear
          />
          <Select
            placeholder="状态筛选"
            style={{ width: 120 }}
            value={statusFilter}
            onChange={setStatusFilter}
            options={[
              { label: '全部', value: 'all' },
              { label: '待执行', value: 'pending' },
              { label: '执行中', value: 'running' },
              { label: '已完成', value: 'done' },
              { label: '失败', value: 'failed' },
            ]}
          />
          <Button 
            icon={<ExpandOutlined />} 
            onClick={handleFitView}
            title="适应视图"
          />
          <Button 
            icon={<ReloadOutlined />} 
            onClick={handleRefresh}
            loading={loading}
          >
            刷新
          </Button>
        </Space>
      }
    >
      <Spin spinning={loading} tip="加载任务数据中...">
        <div style={{ position: 'relative' }}>
          <div 
            ref={networkRef} 
            style={{ 
              height: 'calc(100vh - 200px)', 
              width: '100%',
              border: '1px solid #d9d9d9',
              borderRadius: '6px',
              backgroundColor: '#fafafa',
            }} 
          />
          {/* Agent工作流程图例 */}
          <AgentLegend />
        </div>
      </Spin>
    </Card>
  );
};

export default DAGVisualization;
