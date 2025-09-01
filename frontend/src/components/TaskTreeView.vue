<template>
  <div class="tree-container">
    <div class="tree-root">
      <div class="root-node plan-title">
        <div class="root-header">
          <h3><slot name="header">Task Tree</slot></h3>
          <div class="root-controls">
            <span class="tree-status">🌳 {{ tasks.length }} tasks ({{ visibleTasks.length }} visible)</span>
            <button @click="$emit('refresh')" class="btn-tree">🔄 刷新</button>
          </div>
        </div>
      </div>
      
      <div v-if="loading" class="loading-card">
        <div class="spinner"></div>
        <p>Loading tree structure...</p>
      </div>
      
      <div v-else-if="error" class="error-card">
        <p>{{ error }}</p>
        <button @click="$emit('refresh')" class="btn-retry">重试</button>
      </div>
      
      <div v-else-if="!tasks.length" class="empty-card">
        <p>暂无任务或加载失败</p>
        <button @click="$emit('refresh')" class="btn-retry">重新加载</button>
      </div>
      
      <div v-else class="tree-view">
        <div class="tree-tasks">
          <div class="custom-tree-view">
            <div 
              v-for="task in visibleTasks" 
              :key="task.id"
              class="tree-task-item"
              :class="{
                'is-root': task.parent_id === null || task.parent_id === 0,
                'is-expanded': isExpanded(task.id),
                'has-children': hasChildren(task.id),
                [`status-${task.status}`]: true,
                [`type-${task.task_type}`]: true
              }"
              :style="{ paddingLeft: `${task.displayLevel * 1.5 + 0.5}rem` }"
            >
              <div class="task-row" @click="toggleTaskExpansion(task)">
                <button 
                  v-if="hasChildren(task.id)"
                  class="expand-btn"
                  :class="{ 'expanded': isExpanded(task.id) }"
                >
                  <span class="expand-icon">▶</span>
                </button>
                <span v-else class="expand-spacer"></span>
                
                <div class="task-icon">
                  <span v-if="task.task_type === 'root'">📁</span>
                  <span v-else-if="task.task_type === 'composite'">📂</span>
                  <span v-else>📄</span>
                </div>
                
                <div class="task-info">
                  <div class="task-name">{{ task.shortName }}</div>
                  <div class="task-meta">
                    <span class="task-status" :class="`status-${task.status}`">
                      {{ task.status }}
                    </span>
                    <span class="task-priority">P{{ task.priority }}</span>
                    <span class="task-id">#{{ task.id }}</span>
                  </div>
                </div>
                
                <button 
                  class="detail-btn"
                  @click.stop="$emit('task-selected', task)"
                  title="查看详情"
                >
                  <span>👁️</span>
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue';

const props = defineProps({
  tasks: {
    type: Array,
    required: true,
  },
  loading: Boolean,
  error: String,
});

const emit = defineEmits(['task-selected', 'refresh']);

const expandedTasks = ref(new Set());
const visibleTasks = ref([]);

const hasChildren = (taskId) => {
  return props.tasks.some(t => t.parent_id === taskId);
};

const updateVisibleTasks = () => {
  const visible = [];
  const processTask = (task, level = 0) => {
    visible.push({ ...task, displayLevel: level });
    if (expandedTasks.value.has(task.id)) {
      const children = props.tasks.filter(t => t.parent_id === task.id);
      children.forEach(child => processTask(child, level + 1));
    }
  };

  const rootTasks = props.tasks.filter(t => t.parent_id === null || t.parent_id === 0);
  rootTasks.forEach(task => processTask(task));
  visibleTasks.value = visible;
};

const toggleTaskExpansion = (task) => {
  if (!hasChildren(task.id)) {
    emit('task-selected', task);
    return;
  }
  if (expandedTasks.value.has(task.id)) {
    expandedTasks.value.delete(task.id);
  } else {
    expandedTasks.value.add(task.id);
  }
  updateVisibleTasks();
};

const isExpanded = (taskId) => {
  return expandedTasks.value.has(taskId);
};

watch(() => props.tasks, (newTasks, oldTasks) => {
  // When tasks are loaded for the first time, expand the root nodes by default.
  if ((!oldTasks || oldTasks.length === 0) && newTasks.length > 0) {
    const rootTasks = newTasks.filter(t => t.parent_id === null || t.parent_id === 0);
    rootTasks.forEach(task => {
      if (hasChildren(task.id)) {
        expandedTasks.value.add(task.id);
      }
    });
  }
  updateVisibleTasks();
}, { deep: true, immediate: true });

</script>

<style scoped>
/* ... All the styles from PlanDetailView.vue related to the tree ... */
.tree-container {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', 'Helvetica Neue', Arial, sans-serif;
}
.tree-root {
  background: white;
  border-radius: 0.75rem;
  box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
  overflow: hidden;
  max-width: 100%;
}
.root-node {
  background: linear-gradient(135deg, #667eea, #764ba2);
  color: white;
  padding: 1.5rem;
}
.root-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 1rem;
}
.root-header h3 {
  margin: 0;
  font-size: 1.5rem;
  font-weight: 600;
}
.root-controls {
  display: flex;
  align-items: center;
  gap: 1rem;
}
.tree-status {
  font-size: 0.875rem;
  opacity: 0.9;
}
.tree-tasks {
  padding: 1rem 2rem 2rem 1rem;
  background: #fafafa;
  border-radius: 0 0 0.75rem 0.75rem;
}
.btn-tree {
  background: rgba(255, 255, 255, 0.2);
  color: white;
  border: 1px solid rgba(255, 255, 255, 0.3);
  border-radius: 0.5rem;
  padding: 0.5rem 1rem;
  cursor: pointer;
  transition: background 0.2s;
  font-size: 0.875rem;
}
.btn-tree:hover {
  background: rgba(255, 255, 255, 0.3);
}
.tree-view {
  padding: 1rem;
}
.loading-card, .error-card, .empty-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 3rem;
  background: #f8fafc;
  border-radius: 0.5rem;
  margin: 2rem;
  text-align: center;
}
.spinner {
  width: 40px;
  height: 40px;
  border: 4px solid #e2e8f0;
  border-top: 4px solid #667eea;
  border-radius: 50%;
  animation: spin 1s linear infinite;
  margin-bottom: 1rem;
}
@keyframes spin {
  0% { transform: rotate(0deg); }
  100% { transform: rotate(360deg); }
}
.error-card {
  color: #dc2626;
}
.error-card p {
  margin-bottom: 1rem;
}
.btn-retry {
  background: #667eea;
  color: white;
  border: none;
  border-radius: 0.5rem;
  padding: 0.5rem 1rem;
  cursor: pointer;
  margin-top: 1rem;
  transition: background 0.2s;
}
.btn-retry:hover {
  background: #5a67d8;
}
.custom-tree-view {
  background: white;
  border-radius: 0.5rem;
  overflow: hidden;
}
.tree-task-item {
  border-bottom: 1px solid #f1f5f9;
  transition: all 0.2s ease;
}
.tree-task-item:last-child {
  border-bottom: none;
}
.tree-task-item:hover {
  background: linear-gradient(135deg, #f8fafc 0%, #f1f5f9 100%);
}
.task-row {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.875rem 0.5rem;
  cursor: pointer;
  min-height: 60px;
}
.expand-btn {
  width: 24px;
  height: 24px;
  border: none;
  background: rgba(59, 130, 246, 0.1);
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.2s ease;
  flex-shrink: 0;
}
.expand-btn:hover {
  background: rgba(59, 130, 246, 0.2);
  transform: scale(1.1);
}
.expand-btn.expanded .expand-icon {
  transform: rotate(90deg);
}
.expand-icon {
  font-size: 0.75rem;
  color: #3b82f6;
  transition: transform 0.2s ease;
}
.expand-spacer {
  width: 24px;
  height: 24px;
  flex-shrink: 0;
}
.task-icon {
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #f3f4f6, #e5e7eb);
  border-radius: 50%;
  font-size: 1.1rem;
  flex-shrink: 0;
}
.task-info {
  flex: 1;
  min-width: 0;
}
.task-name {
  font-weight: 600;
  color: #1f2937;
  font-size: 0.95rem;
  line-height: 1.4;
  margin-bottom: 0.25rem;
  word-break: break-word;
}
.task-meta {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.75rem;
  flex-wrap: wrap;
}
.task-status {
  padding: 0.2rem 0.5rem;
  border-radius: 0.25rem;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.025em;
}
.task-status.status-done {
  background: #dcfce7;
  color: #16a34a;
}
.task-status.status-pending {
  background: #fffbeb;
  color: #d97706;
}
.task-status.status-failed {
  background: #fef2f2;
  color: #dc2626;
}
.task-priority {
  padding: 0.2rem 0.5rem;
  background: #f3f4f6;
  color: #374151;
  border-radius: 0.25rem;
  font-family: monospace;
  font-weight: 600;
}
.task-id {
  color: #6b7280;
  font-family: monospace;
  font-size: 0.7rem;
}
.detail-btn {
  width: 32px;
  height: 32px;
  border: none;
  background: rgba(99, 102, 241, 0.1);
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.2s ease;
  flex-shrink: 0;
  opacity: 0;
}
.tree-task-item:hover .detail-btn {
  opacity: 1;
}
.detail-btn:hover {
  background: rgba(99, 102, 241, 0.2);
  transform: scale(1.1);
}
.detail-btn span {
  font-size: 0.9rem;
}
.tree-task-item.is-root {
  background: linear-gradient(135deg, #fef3c7 0%, #fde68a 100%);
  border-left: 4px solid #f59e0b;
  font-weight: 600;
}
.tree-task-item.is-root .task-icon {
  background: linear-gradient(135deg, #f59e0b, #d97706);
  color: white;
}
.tree-task-item.is-root .task-name {
  color: #92400e;
  font-size: 1rem;
}
.tree-task-item.type-composite {
  border-left: 3px solid #8b5cf6;
}
.tree-task-item.type-composite .task-icon {
  background: linear-gradient(135deg, #a78bfa, #8b5cf6);
  color: white;
}
.tree-task-item.type-atomic {
  border-left: 3px solid #06b6d4;
}
.tree-task-item.type-atomic .task-icon {
  background: linear-gradient(135deg, #67e8f9, #06b6d4);
  color: white;
}
.tree-task-item {
  animation: slideInFromLeft 0.3s ease-out;
}
@keyframes slideInFromLeft {
  from {
    opacity: 0;
    transform: translateX(-20px);
  }
  to {
    opacity: 1;
    transform: translateX(0);
  }
}
@media (max-width: 768px) {
  .task-row {
    padding: 0.75rem 0.25rem;
    gap: 0.5rem;
  }
  .task-meta {
    flex-direction: column;
    align-items: flex-start;
    gap: 0.25rem;
  }
  .tree-task-item {
    padding-left: 0.5rem !important;
  }
  .task-name {
    font-size: 0.875rem;
  }
}
</style>