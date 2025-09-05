<template>
  <div class="tree-task-node" :class="'priority-' + node.priority">
    <div class="task-item-content" @click="$emit('click-task', node)">
      <!-- Tree connector -->
      <div class="tree-connector">
        <div class="vertical-line"></div>
        <div class="horizontal-line"></div>
        <div class="circle-end"></div>
      </div>
      
      <!-- Task content -->
      <div class="task-content">
        <div class="task-header">
          <span class="status-badge" :class="'status-' + node.status">
            {{ node.status === 'done' ? '✅' : node.status === 'pending' ? '⏳' : '❌' }}
          </span>
          <span class="task-id">#{{ node.id }}</span>
          <span class="task-title">{{ node.shortName || node.name }}</span>
        </div>
        
        <div class="task-details">
          <span class="priority-badge" :style="{ backgroundColor: getPriorityColor(node.priority) }">
            P{{ node.priority }}
          </span>
          <span class="task-type" :class="node.task_type">{{ node.task_type }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
defineProps({
  node: {
    type: Object,
    required: true
  },
  level: {
    type: Number,
    default: 1
  }
})

defineEmits(['click-task', 'run-task'])

const getStatusColor = (status) => {
  const colors = {
    done: '#10b981',
    pending: '#f59e0b', 
    failed: '#ef4444'
  }
  return colors[status] || '#6b7280'
}

const getPriorityColor = (priority) => {
  if (priority <= 10) return '#10b981'
  if (priority <= 30) return '#f59e0b'
  if (priority <= 50) return '#d97706'
  return '#ef4444'
}
</script>

<style scoped>
.tree-task-node {
  margin-bottom: 0.5rem;
  position: relative;
}

.task-item-content {
  display: flex;
  align-items: center;
  padding: 1.25rem 1.5rem;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  border-radius: 0.75rem;
  cursor: pointer;
  transition: all 0.2s ease;
  margin-left: 1rem;
  position: relative;
}

.task-item-content:hover {
  transform: translateX(4px);
  box-shadow: 0 4px 8px rgba(0, 0, 0, 0.08);
  border-color: #667eea;
  background: #f0f9ff;
}

.task-item-content:before {
  content: '';
  position: absolute;
  left: -1rem;
  top: 50%;
  width: 1rem;
  height: 1px;
  background: #d1d5db;
  transform: translateY(-50%);
}

.tree-connector {
  width: 20px;
  height: 20px;
  margin-right: 0.5rem;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #d1d5db;
  font-weight: bold;
  font-size: 12px;
}

.vertical-line {
  width: 1px;
  height: 10px;
  background: #d1d5db;
}

.horizontal-line {
  width: 10px;
  height: 1px;
  background: #d1d5db;
  margin: 0 -1px;
}

.circle-end {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #667eea;
}

.task-content {
  display: flex;
  flex-direction: column;
  flex: 1;
  gap: 0.25rem;
}

.task-header {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.status-badge {
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  font-size: 0.8rem;
}

.status-badge.status-done { background: #dcfce7; }
.status-badge.status-pending { background: #fffbeb; }
.status-badge.status-failed { background: #fef2f2; }

.task-id {
  font-size: 0.75rem;
  color: #6b7280;
  font-family: monospace;
  font-weight: 600;
  min-width: 30px;
}

.task-title {
  font-size: 0.875rem;
  font-weight: 500;
  color: #1f2937;
  line-height: 1.3;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 400px;
}

.task-details {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.75rem;
}

.priority-badge {
  padding: 0.125rem 0.5rem;
  border-radius: 0.25rem;
  font-size: 0.6rem;
  color: white;
  font-weight: 500;
  font-family: monospace;
}

.priority-badge.priority-10 { background: #10b981; }
.priority-badge.priority-20 { background: #22c55e; }
.priority-badge.priority-30 { background: #eab308; }
.priority-badge.priority-40 { background: #f59e0b; }
.priority-badge.priority-50 { background: #f97316; }
.priority-badge.priority-60 { background: #ef4444; }
.priority-badge.priority-70 { background: #dc2626; }
.priority-badge.priority-80 { background: #b91c1c; }
.priority-badge.priority-90 { background: #991b1b; }

.task-type {
  padding: 0.125rem 0.375rem;
  border-radius: 0.25rem;
  font-size: 0.625rem;
  color: white;
  text-transform: uppercase;
  font-weight: 500;
}

.task-type.atomic { background: #3b82f6; }
.task-type.composite { background: #8b5cf6; }
.task-type.root { background: #059669; }
</style>