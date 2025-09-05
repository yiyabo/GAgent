<template>
  <div class="plan-detail">
    <div class="plan-header">
      <h1>{{ planTitle }}</h1>
      <div class="plan-actions">
        <button @click="showCreateTaskModal = true" class="btn btn-success">➕ Create New Task</button>
        <router-link :to="`/plan/${planId}/execute`" class="btn btn-primary">
          ▶️ Execute Plan
        </router-link>
        <router-link :to="`/plan/${planId}/chat`" class="btn btn-info">
          💬 Chat
        </router-link>
        <button @click="loadPlan" class="btn btn-outline">🔄 Refresh</button>
        <button @click="showDeleteConfirm = true" class="btn btn-danger">🗑️ Delete Plan</button>
      </div>
    </div>

    <TaskTreeView 
      :tasks="formattedTasks"
      :loading="loading"
      :error="error"
      @task-selected="selectTask"
      @refresh="loadPlan"
    >
      <template #header>{{ planTitle }}</template>
    </TaskTreeView>

    <TaskDetailModal 
      :show="selectedTaskForDetail !== null"
      :task="selectedTaskForDetail"
      @close="closeDetailModal"
      @task-rerun="runTask"
      @task-deleted="handleTaskDeleted"
    />

    <CreateTaskModal 
      :show="showCreateTaskModal"
      :planId="planId"
      :existingTasks="formattedTasks"
      @close="showCreateTaskModal = false"
      @task-created="loadPlan"
    />

    <DeleteConfirmationModal 
      :show="showDeleteConfirm"
      :planTitle="planTitle"
      @cancel="showDeleteConfirm = false"
      @confirm="deletePlan"
    />

  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { usePlansStore } from '../stores/plans.js';
import { tasksApi } from '../services/api.js';

// Import new components
import TaskTreeView from '../components/TaskTreeView.vue';
import TaskDetailModal from '../components/TaskDetailModal.vue';
import CreateTaskModal from '../components/CreateTaskModal.vue';
import DeleteConfirmationModal from '../components/DeleteConfirmationModal.vue';

const route = useRoute();
const router = useRouter();
const plansStore = usePlansStore();

// Get the plan ID from the route parameters
const planId = computed(() => parseInt(route.params.id || route.params.title));

// Reactive state for the component
const showCreateTaskModal = ref(false);
const showDeleteConfirm = ref(false);
const selectedTaskForDetail = ref(null);

// Computed properties to get data from the store
const loading = computed(() => plansStore.planDetailsLoading);
const error = computed(() => plansStore.error);
const currentPlan = computed(() => plansStore.currentPlan);
const formattedTasks = computed(() => 
  (plansStore.currentPlanTasks || []).map(task => ({
    ...task,
    shortName: task.name.replace(`[${planTitle.value}]`, '').trim() || task.name,
  }))
);
const planTitle = computed(() => currentPlan.value?.title || 'Loading Plan...');

// Actions
const loadPlan = () => {
  if (planId.value) {
    plansStore.loadPlanDetails(planId.value);
  }
};

const selectTask = (task) => {
  selectedTaskForDetail.value = task;
};

const closeDetailModal = () => {
  selectedTaskForDetail.value = null;
};

const handleTaskDeleted = () => {
  closeDetailModal();
  loadPlan(); // Refresh the plan to update the task list
};

const runTask = async (taskId) => {
  if (!taskId) return;
  try {
    await tasksApi.rerunTask(taskId);
    await loadPlan(); // Refresh data
    closeDetailModal();
  } catch (e) {
    console.error('Failed to run task:', e);
  }
};

const deletePlan = async () => {
  if (!planId.value) return;
  try {
    await plansStore.deletePlan(planId.value); // Assuming store has deletePlan action
    router.push('/');
  } catch (e) {
    console.error('Failed to delete plan:', e);
  } finally {
    showDeleteConfirm.value = false;
  }
};

// Lifecycle hook
onMounted(() => {
  loadPlan();
});

</script>

<style scoped>
.plan-detail {
  max-width: 1200px;
  margin: 0 auto;
  padding: 2rem;
}

.plan-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 2rem;
  gap: 1rem;
}

.plan-header h1 {
  font-size: 2rem;
  color: #1f2937;
  margin: 0;
  word-break: break-word;
}

.plan-actions {
  display: flex;
  gap: 1rem;
  align-items: center;
  flex-shrink: 0;
}

.btn {
  display: inline-block;
  padding: 0.75rem 1.5rem;
  border: none;
  border-radius: 0.5rem;
  font-weight: 500;
  text-decoration: none;
  cursor: pointer;
  transition: all 0.2s;
  font-size: 0.875rem;
}

.btn-primary {
  background: #3b82f6;
  color: white;
}

.btn-primary:hover {
  background: #2563eb;
}

.btn-info {
  background-color: #0ea5e9;
  color: white;
}

.btn-info:hover {
  background-color: #0284c7;
}

.btn-success {
    background-color: #10b981;
    color: white;
}

.btn-success:hover {
    background-color: #059669;
}

.btn-outline {
  background: transparent;
  color: #374151;
  border: 1px solid #d1d5db;
}

.btn-outline:hover {
  background: #f9fafb;
  border-color: #9ca3af;
}

.btn-danger {
  background: #dc2626;
  color: white;
}

.btn-danger:hover {
  background: #b91c1c;
}
</style>