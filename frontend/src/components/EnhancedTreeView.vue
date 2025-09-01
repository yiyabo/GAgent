<template>
  <div class="enhanced-tree-view">
    <div class="tree-header">
      <div class="root-indicator">
        <span class="root-icon">📁</span>
        <span class="root-title">{{ planTitle }}</span>
      </div>
    </div>
    
    <div class="tree-container">
      <enhanced-tree-node
        v-for="root in rootTasks"
        :key="root.id"
        :node="root"
        :level="0"
        :children="childrenMap[root.id] || []"
        :children-map="childrenMap"
        :expanded-nodes="expandedNodes"
        @click-task="$emit('click-task', $event)"
        @toggle-expand="toggleExpand"
      />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import EnhancedTreeNode from './EnhancedTreeNode.vue'

const props = defineProps({
  tasks: {
    type: Array,
    required: true
  },
  planTitle: {
    type: String,
    default: 'Plan'
  }
})

defineEmits(['click-task'])

const expandedNodes = ref(new Set())

// Create children map for efficient lookup
const childrenMap = computed(() => {
  const map = {}
  props.tasks.forEach(task => {
    const parentId = task.parent_id
    if (parentId !== null) {
      if (!map[parentId]) map[parentId] = []
      map[parentId].push(task)
    }
  })
  
  // Sort children by priority for display
  Object.keys(map).forEach(parentId => {
    map[parentId] = map[parentId].sort((a, b) => {
      // Sort by depth first, then by priority
      if (a.depth !== b.depth) return a.depth - b.depth
      return a.priority - b.priority
    })
  })
  
  return map
})

// Get root tasks (tasks without parents)
const rootTasks = computed(() => {
  return props.tasks
    .filter(task => task.parent_id === null || !props.tasks.find(t => t.id === task.parent_id))
    .sort((a, b) => a.priority - b.priority)
})

const toggleExpand = (taskId) => {
  if (expandedNodes.value.has(taskId)) {
    expandedNodes.value.delete(taskId)
  } else {
    expandedNodes.value.add(taskId)
  }
}

// Expand root nodes by default
onMounted(() => {
  rootTasks.value.forEach(task => {
    if (task.parent_id === null) {
      expandedNodes.value.add(task.id)
    }
  })
})
</script>

<style scoped>
.enhanced-tree-view {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', 'Helvetica Neue', Arial, sans-serif;
}

.tree-header {
  margin-bottom: 2rem;
  padding: 1rem;
  background: linear-gradient(135deg, #f8fafc, #e2e8f0);
  border-radius: 12px;
  border-left: 4px solid #667eea;
}

.root-indicator {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.root-icon {
  font-size: 1.5rem;
  width: 40px;
  height: 40px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #667eea;
  border-radius: 50%;
  color: white;
  box-shadow: 0 2px 8px rgba(102, 126, 234, 0.3);
}

.root-title {
  font-weight: 600;
  font-size: 1.25rem;
  color: #1f2937;
}

.tree-container {
  padding: 1rem 0;
}

/* Smooth animations */
* {
  transition: all 0.2s ease;
}
</style>