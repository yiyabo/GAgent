<template>
  <div class="tree-view">
    <div class="tree-container" ref="treeContainer">
      <tree-node
        v-for="task in rootTasks"
        :key="task.id"
        :node="task"
        :level="0"
        :children="childrenMap[task.id] || []"
        :all-tasks="taskMap"
        @click-task="$emit('click-task', $event)"
        @toggle-expand="toggleExpand"
      />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import TreeNode from './TreeNode.vue'

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

// Create a map for quick task lookup
const taskMap = computed(() => {
  const map = {}
  props.tasks.forEach(task => {
    map[task.id] = task
    map[task.id].expanded = expandedNodes.value.has(task.id)
  })
  return map
})

// Create children map
const childrenMap = computed(() => {
  const map = {}
  props.tasks.forEach(task => {
    const parentId = task.parent_id
    if (parentId) {
      if (!map[parentId]) map[parentId] = []
      map[parentId].push(task)
    }
  })
  return map
})

// Get root tasks (tasks without parents or tasks with parent_id = null)
const rootTasks = computed(() => {
  return props.tasks
    .filter(task => task.parent_id === null || !props.tasks.find(t => t.id === task.parent_id))
    .sort((a, b) => {
      if (a.depth !== b.depth) return a.depth - b.depth
      return a.priority - b.priority
    })
})

const toggleExpand = (taskId) => {
  if (expandedNodes.value.has(taskId)) {
    expandedNodes.value.delete(taskId)
  } else {
    expandedNodes.value.add(taskId)
  }
}
</script>

<style scoped>
.tree-view {
  padding: 2rem;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', 'Helvetica Neue', Arial, sans-serif;
}

.tree-container {
  position: relative;
  margin: 0 auto;
  max-width: 100%;
}
</style>