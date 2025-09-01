<template>
  <div class="tree-node-container" :style="containerStyle">
    <!-- Node line connecting to parent -->
    <div v-if="level > 0" class="parent-connection" :style="connectionStyle"></div>
    
    <!-- The actual node -->
    <div 
      class="tree-node"
      :class="nodeClass"
      @click="handleNodeClick"
      @keydown.enter="handleNodeClick"
      tabindex="0"
      role="button"
    >
      <!-- Status indicator -->
      <div class="status-indicator" :class="statusClass"></div>
      
      <!-- Node title -->
      <div class="node-title">{{ node.shortName || node.name }}</div>
      
      <!-- Expand button for composite nodes -->
      <div v-if="hasChildren" class="expand-button" @click.stop="toggleExpand">
        <span class="expand-icon" :class="{ expanded: isExpanded }">
          {{ isExpanded ? '−' : '+' }}
        </span>
      </div>
    </div>
    
    <!-- Children -->
    <div v-if="hasChildren && isExpanded" class="children-container" :style="childrenStyle">
      <div v-for="child in children" :key="child.id" class="child-connection">
        <!-- Connection line from parent to child -->
        <div class="line-connector"></div>
        
        <EnhancedTreeNode
          :node="child"
          :level="level + 1"
          :children="childrenMap[child.id] || []"
          :children-map="childrenMap"
          :expanded-nodes="expandedNodes"
          @click-task="$emit('click-task', $event)"
          @toggle-expand="$emit('toggle-expand', $event)"
        />
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  node: {
    type: Object,
    required: true
  },
  level: {
    type: Number,
    default: 0
  },
  children: {
    type: Array,
    default: () => []
  },
  childrenMap: {
    type: Object,
    default: () => ({})
  },
  expandedNodes: {
    type: Set,
    default: () => new Set()
  }
})

const emit = defineEmits(['click-task', 'toggle-expand'])

const hasChildren = computed(() => props.children && props.children.length > 0)
const isExpanded = computed(() => props.expandedNodes.has(props.node.id))

const containerStyle = computed(() => ({
  marginLeft: props.level > 0 ? '40px' : '0'
}))

const childrenStyle = computed(() => ({
  marginLeft: '20px'
}))

const connectionStyle = computed(() => ({
  left: `-${props.level * 20 + 20}px`
}))

const nodeClass = computed(() => [
  `status-${props.node.status}`,
  `type-${props.node.task_type}`,
  {
    'has-children': hasChildren.value,
    'expanded': isExpanded.value
  }
])

const statusClass = computed(() => `status-${props.node.status}`)

const handleNodeClick = () => {
  emit('click-task', props.node)
}

const toggleExpand = () => {
  if (hasChildren.value) {
    emit('toggle-expand', props.node.id)
  }
}
</script>

<style scoped>
.tree-node-container {
  position: relative;
  margin-bottom: 8px;
}

.parent-connection {
  position: absolute;
  top: 50%;
  height: 2px;
  width: 20px;
  background: #e2e8f0;
  transform: translateY(-50%);
}

.tree-node {
  display: flex;
  align-items: center;
  padding: 12px 16px;
  background: #ffffff;
  border: 2px solid #e2e8f0;
  border-radius: 20px;
  cursor: pointer;
  transition: all 0.3s ease;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
  min-height: 44px;
  position: relative;
  user-select: none;
  outline: none;
}

.tree-node:hover {
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
  border-color: #3b82f6;
}

.tree-node:focus {
  outline: 2px solid #3b82f6;
  outline-offset: 2px;
}

.tree-node.type-root {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  border-color: #667eea;
}

.tree-node.type-composite {
  background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
  color: white;
  border-color: #f093fb;
}

.tree-node.type-atomic {
  background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%);
  color: white;
  border-color: #4facfe;
}

.status-indicator {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  margin-right: 8px;
  flex-shrink: 0;
}

.status-indicator.status-done {
  background: #10b981;
}

.status-indicator.status-pending {
  background: #f59e0b;
}

.status-indicator.status-failed {
  background: #ef4444;
}

.node-title {
  flex: 1;
  font-size: 14px;
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 300px;
}

.expand-button {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.1);
  margin-left: 8px;
  cursor: pointer;
  transition: all 0.2s ease;
}

.tree-node:not(.type-root):not(.type-composite):not(.type-atomic) .expand-button {
  background: #f3f4f6;
}

.tree-node.type-root .expand-button:hover,
.tree-node.type-composite .expand-button:hover,
.tree-node.type-atomic .expand-button:hover {
  background: rgba(255, 255, 255, 0.2);
}

.expand-icon {
  font-weight: bold;
  color: #374151;
  transition: transform 0.2s ease;
}

.expand-icon.expanded {
  transform: rotate(45deg);
}

.children-container {
  position: relative;
}

.child-connection {
  position: relative;
}

.line-connector {
  position: absolute;
  top: -8px;
  left: 0;
  width: 2px;
  height: 16px;
  background: #e2e8f0;
}

.line-connector::before {
  content: '';
  position: absolute;
  top: 8px;
  left: 0;
  width: 20px;
  height: 2px;
  background: #e2e8f0;
}

/* Responsive adjustments */
@media (max-width: 768px) {
  .tree-node-container {
    margin-left: 0;
  }
  
  .tree-node {
    padding: 10px 12px;
    border-radius: 16px;
  }
  
  .node-title {
    font-size: 13px;
    max-width: 200px;
  }
  
  .children-container {
    margin-left: 15px;
  }
}
</style>