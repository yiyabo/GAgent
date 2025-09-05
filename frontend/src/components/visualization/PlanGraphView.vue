<template>
  <div class="plan-graph-view">
    <div class="graph-header">
      <h3>{{ planTitle || '计划可视化' }}</h3>
      <div class="graph-controls">
        <el-button size="small" @click="resetView">重置视图</el-button>
        <el-button size="small" @click="fitToScreen">适应屏幕</el-button>
      </div>
    </div>
    
    <div class="graph-container" ref="graphContainer">
      <svg ref="svgElement" width="100%" height="100%">
        <defs>
          <marker
            id="arrowhead"
            markerWidth="12"
            markerHeight="8"
            refX="11"
            refY="4"
            orient="auto"
          >
            <polygon
              points="0 0, 12 4, 0 8"
              fill="#909399"
            />
          </marker>
        </defs>
        <g class="zoom-group" :transform="transform">
          <!-- 连接线 -->
          <g class="links">
            <line
              v-for="link in links"
              :key="`${link.source}-${link.target}`"
              :x1="getNodePosition(link.source).x"
              :y1="getNodePosition(link.source).y"
              :x2="getNodePosition(link.target).x"
              :y2="getNodePosition(link.target).y"
              class="link"
              marker-end="url(#arrowhead)"
            />
          </g>
          <!-- 节点 -->
          <g class="nodes">
            <g
              v-for="node in nodes"
              :key="node.id"
              class="node"
              :transform="`translate(${node.x}, ${node.y})`"
              @click="handleNodeClick(node)"
              @mouseenter="handleNodeHover(node)"
              @mouseleave="handleNodeLeave"
            >
              <circle
                :r="getNodeRadius(node)"
                :class="getNodeClass(node)"
                :fill="getNodeColor(node)"
              />
              <text
                class="node-label"
                :y="getNodeRadius(node) + 35"
                text-anchor="middle"
                :font-size="getLabelFontSize(node)"
              >
                {{ truncateText(node.label, 15) }}
              </text>
              <!-- 状态图标 -->
              <text
                v-if="node.status"
                class="status-icon"
                :y="8"
                text-anchor="middle"
                :font-size="getIconFontSize(node)"
              >
                {{ getStatusIcon(node.status) }}
              </text>
            </g>
          </g>
        </g>
      </svg>
      
      <!-- 悬浮提示 -->
      <div
        v-if="hoveredNode"
        class="node-tooltip"
        :style="tooltipStyle"
      >
        <div class="tooltip-title">{{ hoveredNode.label }}</div>
        <div class="tooltip-content">
          <div v-if="hoveredNode.status">
            状态: {{ getStatusText(hoveredNode.status) }}
          </div>
          <div v-if="hoveredNode.description">
            {{ hoveredNode.description }}
          </div>
          <div class="tooltip-hint">点击查看详情</div>
        </div>
      </div>
    </div>
    
    <!-- 任务详情弹窗 -->
    <el-dialog
      v-model="showTaskDetail"
      :title="selectedTask?.label"
      width="60%"
      append-to-body
    >
      <div v-if="selectedTask" class="task-detail">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="任务ID">
            {{ selectedTask.id }}
          </el-descriptions-item>
          <el-descriptions-item label="状态">
            <el-tag :type="getStatusType(selectedTask.status)">
              {{ getStatusText(selectedTask.status) }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="父任务ID" v-if="selectedTask.parent_id">
            {{ selectedTask.parent_id }}
          </el-descriptions-item>
          <el-descriptions-item label="深度">
            {{ selectedTask.depth || 0 }}
          </el-descriptions-item>
          <el-descriptions-item label="描述" :span="2">
            {{ selectedTask.description || '无描述' }}
          </el-descriptions-item>
          <el-descriptions-item label="输入" :span="2" v-if="selectedTask.input">
            <pre class="task-content">{{ selectedTask.input }}</pre>
          </el-descriptions-item>
          <el-descriptions-item label="输出" :span="2" v-if="selectedTask.output">
            <pre class="task-content">{{ selectedTask.output }}</pre>
          </el-descriptions-item>
        </el-descriptions>
      </div>
    </el-dialog>
  </div>
</template>

<script>
import { ref, computed, watch, onMounted, nextTick } from 'vue'
import * as d3 from 'd3'

export default {
  name: 'PlanGraphView',
  props: {
    planData: {
      type: Object,
      default: () => ({})
    },
    planTitle: {
      type: String,
      default: ''
    }
  },
  setup(props) {
    const graphContainer = ref(null)
    const svgElement = ref(null)
    const nodes = ref([])
    const links = ref([])
    const hoveredNode = ref(null)
    const selectedTask = ref(null)
    const showTaskDetail = ref(false)
    const tooltipStyle = ref({})
    
    // 缩放和平移
    const zoom = ref(1)
    const translateX = ref(0)
    const translateY = ref(0)
    
    const transform = computed(() => 
      `translate(${translateX.value}, ${translateY.value}) scale(${zoom.value})`
    )
    
    // 处理计划数据，生成节点和连接
    const processData = () => {
      if (!props.planData || !props.planData.tasks) {
        nodes.value = []
        links.value = []
        return
      }
      
      const tasks = props.planData.tasks
      const nodeMap = new Map()
      const linkList = []
      
      // 创建节点
      tasks.forEach(task => {
        nodeMap.set(task.id, {
          id: task.id,
          label: task.label || task.description || `任务 ${task.id}`,
          status: task.status,
          parent_id: task.parent_id,
          depth: task.depth || 0,
          description: task.description,
          input: task.input,
          output: task.output,
          x: 0,
          y: 0
        })
        
        // 创建连接
        if (task.parent_id && nodeMap.has(task.parent_id)) {
          linkList.push({
            source: task.parent_id,
            target: task.id
          })
        }
      })
      
      // 使用力导向布局计算节点位置 - 调整参数适应更大的节点
      const simulation = d3.forceSimulation(Array.from(nodeMap.values()))
        .force('link', d3.forceLink(linkList)
          .id(d => d.id)
          .distance(200)) // 增加连接距离适应大节点
        .force('charge', d3.forceManyBody().strength(-1200)) // 增加排斥力
        .force('center', d3.forceCenter(400, 300))
        .force('collision', d3.forceCollide().radius(150)) // 增加碰撞半径
      
      // 运行模拟
      for (let i = 0; i < 150; i++) {
        simulation.tick()
      }
      
      nodes.value = Array.from(nodeMap.values())
      links.value = linkList
    }
    
    // 获取节点位置
    const getNodePosition = (nodeId) => {
      const node = nodes.value.find(n => n.id === nodeId)
      return node || { x: 0, y: 0 }
    }
    
    // 获取节点半径 - 大幅增大节点尺寸
    const getNodeRadius = (node) => {
      if (!node.parent_id) return 120 // 根节点非常大
      if (node.status === 'complete') return 100
      return 80 // 普通节点也要很大
    }
    
    // 获取标签字体大小
    const getLabelFontSize = (node) => {
      if (!node.parent_id) return 20 // 根节点标签更大
      return 16
    }
    
    // 获取图标字体大小
    const getIconFontSize = (node) => {
      if (!node.parent_id) return 36 // 根节点图标更大
      return 28
    }
    
    // 获取节点颜色
    const getNodeColor = (node) => {
      const statusColors = {
        'pending': '#909399',
        'processing': '#409EFF',
        'complete': '#67C23A',
        'failed': '#F56C6C',
        'pending-review': '#E6A23C'
      }
      return statusColors[node.status] || '#909399'
    }
    
    // 获取节点类名
    const getNodeClass = (node) => {
      const classes = ['node-circle']
      if (!node.parent_id) classes.push('root-node')
      if (node.status) classes.push(`status-${node.status}`)
      return classes.join(' ')
    }
    
    // 获取状态图标
    const getStatusIcon = (status) => {
      const icons = {
        'pending': '⏳',
        'processing': '🔄',
        'complete': '✅',
        'failed': '❌',
        'pending-review': '📝'
      }
      return icons[status] || ''
    }
    
    // 获取状态文本
    const getStatusText = (status) => {
      const texts = {
        'pending': '待处理',
        'processing': '处理中',
        'complete': '已完成',
        'failed': '失败',
        'pending-review': '待审核'
      }
      return texts[status] || status
    }
    
    // 获取状态类型（用于标签颜色）
    const getStatusType = (status) => {
      const types = {
        'pending': 'info',
        'processing': 'primary',
        'complete': 'success',
        'failed': 'danger',
        'pending-review': 'warning'
      }
      return types[status] || 'info'
    }
    
    // 截断文本
    const truncateText = (text, maxLength) => {
      if (!text) return ''
      return text.length > maxLength 
        ? text.substring(0, maxLength) + '...' 
        : text
    }
    
    // 处理节点点击
    const handleNodeClick = (node) => {
      selectedTask.value = node
      showTaskDetail.value = true
    }
    
    // 处理节点悬浮
    const handleNodeHover = (node) => {
      hoveredNode.value = node
      
      // 计算提示框位置
      if (graphContainer.value) {
        const rect = graphContainer.value.getBoundingClientRect()
        const x = node.x * zoom.value + translateX.value
        const y = node.y * zoom.value + translateY.value
        
        tooltipStyle.value = {
          left: `${x}px`,
          top: `${y - 100}px`
        }
      }
    }
    
    // 处理鼠标离开
    const handleNodeLeave = () => {
      hoveredNode.value = null
    }
    
    // 重置视图
    const resetView = () => {
      zoom.value = 1
      translateX.value = 0
      translateY.value = 0
    }
    
    // 适应屏幕
    const fitToScreen = () => {
      if (!graphContainer.value || nodes.value.length === 0) return
      
      const rect = graphContainer.value.getBoundingClientRect()
      const minX = Math.min(...nodes.value.map(n => n.x))
      const maxX = Math.max(...nodes.value.map(n => n.x))
      const minY = Math.min(...nodes.value.map(n => n.y))
      const maxY = Math.max(...nodes.value.map(n => n.y))
      
      const width = maxX - minX + 300 // 增加边距
      const height = maxY - minY + 300
      
      const scaleX = rect.width / width
      const scaleY = rect.height / height
      zoom.value = Math.min(scaleX, scaleY, 1.5)
      
      translateX.value = (rect.width - width * zoom.value) / 2 - minX * zoom.value + 150 * zoom.value
      translateY.value = (rect.height - height * zoom.value) / 2 - minY * zoom.value + 150 * zoom.value
    }
    
    // 监听数据变化
    watch(() => props.planData, () => {
      processData()
      nextTick(() => {
        fitToScreen()
      })
    }, { deep: true, immediate: true })
    
    // 设置拖拽和缩放
    onMounted(() => {
      if (svgElement.value) {
        const svg = d3.select(svgElement.value)
        
        const zoomBehavior = d3.zoom()
          .scaleExtent([0.1, 3])
          .on('zoom', (event) => {
            zoom.value = event.transform.k
            translateX.value = event.transform.x
            translateY.value = event.transform.y
          })
        
        svg.call(zoomBehavior)
      }
    })
    
    return {
      graphContainer,
      svgElement,
      nodes,
      links,
      hoveredNode,
      selectedTask,
      showTaskDetail,
      tooltipStyle,
      transform,
      getNodePosition,
      getNodeRadius,
      getLabelFontSize,
      getIconFontSize,
      getNodeColor,
      getNodeClass,
      getStatusIcon,
      getStatusText,
      getStatusType,
      truncateText,
      handleNodeClick,
      handleNodeHover,
      handleNodeLeave,
      resetView,
      fitToScreen
    }
  }
}
</script>

<style scoped>
.plan-graph-view {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: white;
  border-radius: 8px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.1);
}

.graph-header {
  padding: 15px 20px;
  border-bottom: 1px solid #e4e7ed;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.graph-header h3 {
  margin: 0;
  color: #303133;
  font-size: 16px;
}

.graph-controls {
  display: flex;
  gap: 10px;
}

.graph-container {
  flex: 1;
  position: relative;
  overflow: hidden;
}

svg {
  cursor: grab;
}

svg:active {
  cursor: grabbing;
}

.link {
  stroke: #909399;
  stroke-width: 4; /* 增加连接线粗细 */
  fill: none;
  opacity: 0.8; /* 增加透明度 */
}

.node {
  cursor: pointer;
  transition: transform 0.2s;
}

.node:hover {
  transform: scale(1.1); /* 悬浮时放大效果 */
}

.node-circle {
  stroke: white;
  stroke-width: 6; /* 增加边框宽度 */
  transition: all 0.3s;
  filter: drop-shadow(3px 3px 6px rgba(0, 0, 0, 0.2)); /* 添加阴影效果 */
}

.node-circle:hover {
  stroke-width: 8; /* 悬浮时更粗的边框 */
  filter: drop-shadow(4px 4px 8px rgba(0, 0, 0, 0.3)) brightness(1.1);
}

.root-node {
  stroke-width: 8; /* 根节点更粗的边框 */
  filter: drop-shadow(5px 5px 10px rgba(0, 0, 0, 0.3));
}

.node-label {
  fill: #303133; /* 更深的标签颜色 */
  pointer-events: none;
  font-weight: 600; /* 加粗字体 */
  text-shadow: 2px 2px 4px rgba(255, 255, 255, 0.8); /* 添加文字阴影 */
}

.status-icon {
  pointer-events: none;
  filter: drop-shadow(2px 2px 4px rgba(0, 0, 0, 0.3));
}

.node-tooltip {
  position: absolute;
  background: rgba(0, 0, 0, 0.9); /* 更深的背景 */
  color: white;
  padding: 15px 20px; /* 增加内边距 */
  border-radius: 10px; /* 更大的圆角 */
  font-size: 14px; /* 稍微大一点的字体 */
  pointer-events: none;
  z-index: 100;
  max-width: 300px; /* 增加最大宽度 */
  transform: translateX(-50%);
  box-shadow: 0 6px 16px rgba(0, 0, 0, 0.4); /* 添加阴影 */
}

.tooltip-title {
  font-weight: bold;
  margin-bottom: 8px;
  color: #ffd700;
  font-size: 16px; /* 标题稍大 */
}

.tooltip-content {
  line-height: 1.6; /* 增加行高 */
}

.tooltip-hint {
  margin-top: 10px;
  font-size: 12px;
  color: #b3b3b3; /* 更淡的提示色 */
  font-style: italic;
}

.task-detail {
  padding: 10px;
}

.task-content {
  background: #f5f7fa;
  padding: 15px; /* 增加内边距 */
  border-radius: 8px; /* 更大的圆角 */
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 300px; /* 增加最大高度 */
  overflow-y: auto;
  font-family: 'Courier New', monospace;
  font-size: 13px; /* 稍大的字体 */
  line-height: 1.5;
}

/* 状态特定样式 */
.status-pending .node-circle {
  stroke: #909399;
}

.status-processing .node-circle {
  stroke: #409EFF;
  animation: pulse 2s infinite;
}

.status-complete .node-circle {
  stroke: #67C23A;
}

.status-failed .node-circle {
  stroke: #F56C6C;
  animation: shake 0.8s ease-in-out;
}

.status-pending-review .node-circle {
  stroke: #E6A23C;
}

/* 动画效果 */
@keyframes pulse {
  0% {
    box-shadow: 0 0 0 0 rgba(64, 158, 255, 0.7);
    transform: scale(1);
  }
  50% {
    box-shadow: 0 0 0 12px rgba(64, 158, 255, 0.3);
    transform: scale(1.03);
  }
  100% {
    box-shadow: 0 0 0 0 rgba(64, 158, 255, 0);
    transform: scale(1);
  }
}

@keyframes shake {
  0%, 100% { transform: translateX(0); }
  25% { transform: translateX(-3px); }
  75% { transform: translateX(3px); }
}

/* 响应式调整 */
@media (max-width: 768px) {
  .graph-header {
    padding: 10px 15px;
  }
  
  .graph-header h3 {
    font-size: 14px;
  }
  
  .node-tooltip {
    font-size: 12px;
    padding: 12px 16px;
    max-width: 250px;
  }
  
  .tooltip-title {
    font-size: 14px;
  }
  
  /* 在小屏幕上缩小节点 */
  .node-circle {
    transform: scale(0.8);
  }
  
  .node-label {
    font-size: 14px !important;
  }
  
  .status-icon {
    font-size: 20px !important;
  }
}

/* 改善可访问性 */
.node:focus {
  outline: 4px solid #409EFF;
  outline-offset: 4px;
}

.node-circle {
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

/* 连接线动画效果 */
.link {
  transition: all 0.3s ease;
}

.link:hover {
  stroke-width: 6;
  opacity: 1;
  stroke: #409EFF;
}

/* 节点组合效果 */
.nodes {
  filter: drop-shadow(0 0 10px rgba(0, 0, 0, 0.1));
}

/* 增强根节点的视觉效果 */
.root-node {
  stroke: #FFD700 !important; /* 金色边框 */
  filter: drop-shadow(0 0 15px rgba(255, 215, 0, 0.5));
}

/* 节点内文字居中对齐 */
.status-icon {
  dominant-baseline: central;
}

/* 加载状态动画 */
@keyframes rotate {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.status-processing .status-icon {
  animation: rotate 2s linear infinite;
}

/* 节点标签背景增强可读性 */
.node-label {
  paint-order: stroke fill;
  stroke: white;
  stroke-width: 3px;
  stroke-linejoin: round;
}

/* 悬浮时的连接线高亮 */
.node:hover ~ .links .link,
.links .link:hover {
  stroke: #409EFF;
  stroke-width: 6;
  opacity: 1;
}

/* 工具提示箭头效果 */
.node-tooltip::after {
  content: '';
  position: absolute;
  top: 100%;
  left: 50%;
  margin-left: -8px;
  border: 8px solid transparent;
  border-top-color: rgba(0, 0, 0, 0.9);
}
</style>
