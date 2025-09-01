
<template>
  <div class="plan-execution">
    <div class="execution-header">
      <h1>▶️ Execute: {{ planTitle }}</h1>
      <div class="header-actions">
        <router-link 
          :to="`/plan/${planId}/edit`" 
          class="btn btn-secondary"
        >
          Back
        </router-link>
      </div>
    </div>

    <!-- Settings Panel -->
    <div class="settings-panel card">
      <h3>Execution Settings</h3>
      <div class="settings-grid">
        <label class="setting-item">
          <input type="checkbox" v-model="enableEvaluation" />
          Enable Evaluation Mode
        </label>
        <div v-if="enableEvaluation" class="settings-details">
          <label>
            Quality Threshold: 
            <input type="range" 
                   v-model.number="qualityThreshold" 
                   min="0.5" max="1" step="0.1" 
            />
            {{ qualityThreshold * 100 }}%
          </label>
          <label>
            Max Iterations: 
            <input type="number" 
                   v-model.number="maxIterations" 
                   min="1" max="10" 
            />
          </label>
        </div>
        <div class="setting-item">
          <label for="scheduler-select">Scheduling Strategy:</label>
          <select id="scheduler-select" v-model="selectedSchedule" class="scheduler-select">
            <option value="postorder">Post-Order (Default)</option>
            <option value="bfs">Breadth-First (BFS)</option>
            <option value="dag">DAG (Topological)</option>
          </select>
        </div>
      </div>
      
      <div class="execution-buttons">
        <button 
          @click="executePendingTasks"
          class="btn btn-primary"
          :disabled="loading.pending || loading.all || pendingTasks.length === 0"
        >
          {{ loading.pending ? 'Executing...' : 'Execute Pending Tasks' }}
        </button>
        <button 
          @click="rerunEntirePlan"
          class="btn btn-secondary"
          :disabled="loading.pending || loading.all || allTasks.length === 0"
        >
          {{ loading.all ? 'Executing...' : 'Rerun Entire Plan' }}
        </button>
      </div>
    </div>

    <!-- Execution Status -->
    <div class="execution-status card">
      <h3>Task Status</h3>
      <div class="stats-summary">
        <div class="status-stat">
          <span class="stat-value">{{ pendingTasks.length }}</span>
          <span class="stat-label">Pending</span>
        </div>
        <div class="status-stat">
          <span class="stat-value">{{ runningTasks.length }}</span>
          <span class="stat-label">Running</span>
        </div>
        <div class="status-stat">
          <span class="stat-value">{{ completedTasks.length }}</span>
          <span class="stat-label">Completed</span>
        </div>
      </div>

      <!-- Progress Visualization -->
      <div class="progress-viz">
        <div class="progress-header">
          <span>Overall Progress</span>
          <span>{{ calculateProgress() }}%</span>
        </div>
        <div class="progress-bar">
          <div 
            class="progress-fill" 
            :style="{ width: calculateProgress() + '%' }"
            :class="progressClass"
          ></div>
        </div>
      </div>

      <!-- Tasks Grid -->
      <div class="tasks-grid">
        <div 
          v-for="task in allTasks" 
          :key="task.id"
          class="task-executing-card"
          :class="`status-${task.status}`"
        >
          <div class="task-info">
            <div class="task-name">{{ task.name }}</div>
            <div class="task-status">{{ task.status }}</div>
          </div>
          
          <div class="task-progress">
            <div 
              class="status-badge"
              :class="getStatusClass(task.status)"
            >
              {{ task.status }}
            </div>
            <div v-if="task.evaluation" class="evaluation-result">
              <span>Score: {{ task.evaluation.score }}%</span>
            </div>
          </div>

          <div class="task-actions">
            <button 
              v-if="task.status === 'pending'"
              @click="runTask(task.id)"
              class="btn btn-sm"
            >
              Run
            </button>
            <button 
              v-if="task.status === 'completed'"
              @click="rerunTask(task.id)"
              class="btn btn-sm btn-secondary"
            >
              Rerun
            </button>
            <div v-if="task.status === 'executing'" class="spinner">
              ⏳
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Results Panel -->
    <div v-if="executionResults" class="results-panel card">
      <h3>📊 Execution Results</h3>
      
      <div class="results-summary">
        <div class="summary-item">
          <h4>Completion Summary</h4>
          <p>{{ executionResults.filter(r => r.status === 'done').length }} of 
             {{ executionResults.length }} tasks completed successfully</p>
        </div>
        
        <div v-if="enableEvaluation" class="evaluation-summary">
          <h4>Quality Metrics</h4>
          <p>Average Score: {{ averageScore }}%</p>
        </div>
      </div>

      <!-- Generated Content -->
      <div class="generated-content" v-if="planOutput">
        <h4>Generated Content</h4>
        <div 
          v-for="section in planOutput.sections" 
          :key="section.name" 
          class="content-section"
        >
          <h5>{{ section.name }}</h5>
          <div class="content-text">{{ section.content }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import { usePlansStore } from '../stores/plans.js'

const route = useRoute()
const plansStore = usePlansStore()

const planId = computed(() => {
  const param = route.params.id
  const parsed = parseInt(param)
  return isNaN(parsed) ? null : parsed
})

const planTitle = computed(() => {
  const pid = planId.value;
  if (!isNaN(pid)) {
    const plan = plansStore.plans.find(p => p.id === pid);
    return plan?.title || `Plan #${pid}`;
  }
  return decodeURIComponent(route.params.title || 'Plan');
})

// Settings
const enableEvaluation = ref(true)
const qualityThreshold = ref(0.8)
const maxIterations = ref(3)
const selectedSchedule = ref('postorder')

// State
const loading = ref({ pending: false, all: false })
const executionResults = ref(null)
const planOutput = ref(null)

// Computed properties
const allTasks = computed(() => {
  return plansStore.currentPlanTasks || []
})

const pendingTasks = computed(() => 
  allTasks.value.filter(t => t.status === 'pending')
)

const runningTasks = computed(() => 
  allTasks.value.filter(t => t.status === 'executing')
)

const completedTasks = computed(() => 
  allTasks.value.filter(t => t.status === 'done')
)

const averageScore = computed(() => {
  if (!executionResults.value) return 0
  const scores = executionResults.value.map(r => r.evaluation?.overall_score || 0)
  const sum = scores.reduce((acc, score) => acc + score, 0)
  return scores.length > 0 ? Math.round((sum / scores.length) * 100) : 0
})

onMounted(() => {
  loadPlan()
})

const loadPlan = async () => {
  if (!planId.value) {
    console.error('Invalid plan ID')
    return
  }

  await plansStore.loadPlanDetails(planId.value)
  
  try {
    planOutput.value = await plansStore.loadPlanOutput(planId.value)
  } catch (e) {
    planOutput.value = null
  }
}

const executePlan = async (rerunAll) => {
  const type = rerunAll ? 'all' : 'pending';
  if (rerunAll && allTasks.value.length === 0) {
    alert('There are no tasks in this plan to run.');
    return;
  }
  if (!rerunAll && pendingTasks.value.length === 0) {
    alert('No pending tasks to execute!');
    return;
  }

  loading.value[type] = true;
  executionResults.value = []

  try {
    const results = await plansStore.executePlan(planId.value, {
      rerun_all: rerunAll,
      schedule: selectedSchedule.value,
      enable_evaluation: enableEvaluation.value,
      evaluation_options: {
        max_iterations: maxIterations.value,
        quality_threshold: qualityThreshold.value
      }
    })

    executionResults.value = results
    
    await nextTick()
    await loadPlan()
    
  } catch (e) {
    console.error('Execution failed:', e)
    alert(`Execution failed: ${e.message}`)
  } finally {
    loading.value[type] = false;
  }
}

const executePendingTasks = () => executePlan(false);
const rerunEntirePlan = () => executePlan(true);

const runTask = async (taskId) => {
  await plansStore.rerunTask(taskId, {
    use_context: true,
    context_options: {
      include_deps: true,
      include_plan: true
    }
  })
  
  await loadPlan()
}

const rerunTask = async (taskId) => {
  await runTask(taskId)
}

const calculateProgress = () => {
  const total = allTasks.value.length
  const completed = completedTasks.value.length
  return total > 0 ? Math.round((completed / total) * 100) : 0
}

const getStatusClass = (status) => {
  return {
    pending: 'status-pending',
    executing: 'status-executing',
    done: 'status-done',
    failed: 'status-failed'
  }[status] || ''
}

const progressClass = computed(() => {
  const progress = calculateProgress()
  if (progress >= 80) return 'progress-success'
  if (progress >= 50) return 'progress-warning'
  return 'progress-info'
})
</script>

<style scoped>
.plan-execution {
  max-width: 1200px;
  margin: 0 auto;
}

.execution-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 2rem;
}

.execution-header h1 {
  font-size: 2rem;
  color: #1f2937;
}

.settings-panel {
  margin-bottom: 2rem;
}

.settings-panel h3 {
  margin-bottom: 1rem;
  color: #1f2937;
}

.settings-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 1rem;
  align-items: start;
}

.setting-item {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.scheduler-select {
  padding: 0.25rem 0.5rem;
  border-radius: 4px;
  border: 1px solid #d1d5db;
  background-color: white;
}

.settings-details {
  margin-left: 1.5rem;
  display: grid;
  gap: 0.5rem;
}

.settings-details label {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.execution-buttons {
  margin-top: 1.5rem;
  text-align: center;
  display: flex;
  justify-content: center;
  gap: 1rem;
}

.execution-status {
  margin-bottom: 2rem;
}

.stats-summary {
  display: flex;
  gap: 2rem;
  margin-bottom: 1.5rem;
}

.status-stat {
  text-align: center;
}

.status-stat .stat-value {
  font-size: 2rem;
  font-weight: bold;
  color: #2563eb;
}

.status-stat .stat-label {
  color: #6b7280;
  font-size: 0.875rem;
}

.progress-viz {
  margin-bottom: 2rem;
}

.progress-header {
  display: flex;
  justify-content: space-between;
  margin-bottom: 0.5rem;
}

.progress-bar {
  height: 8px;
  background-color: #e5e7eb;
  border-radius: 4px;
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  transition: width 0.3s ease;
}

.progress-info { background-color: #2563eb; }
.progress-warning { background-color: #f59e0b; }
.progress-success { background-color: #10b981; }

.tasks-grid {
  display: grid;
  gap: 1rem;
}

.task-executing-card {
  background: white;
  border-radius: 0.5rem;
  padding: 1.5rem;
  display: grid;
  grid-template-columns: 1fr auto auto;
  gap: 1rem;
  align-items: center;
  box-shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.1);
}

.task-info {
  min-width: 0;
}

.task-name {
  font-weight: 600;
  margin-bottom: 0.5rem;
}

.task-status {
  color: #6b7280;
  font-size: 0.875rem;
}

.task-progress {
  text-align: center;
}

.status-badge {
  padding: 0.25rem 0.75rem;
  border-radius: 0.25rem;
  font-size: 0.875rem;
  font-weight: 500;
}

.status-pending { background-color: #fef3c7; color: #f59e0b; }
.status-executing { background-color: #dbeafe; color: #2563eb; }
.status-done { background-color: #d1fae5; color: #10b981; }
.status-failed { background-color: #fee2e2; color: #ef4444; }

.evaluation-result {
  font-size: 0.875rem;
  color: #6b7280;
  margin-top: 0.25rem;
}

.task-actions {
  text-align: center;
}

.loading .spinner {
  font-size: 1.2em;
  animation: spin 2s linear infinite;
}

.results-panel {
  margin-top: 2rem;
}

.results-summary {
  display: grid;
  gap: 1rem;
  margin-bottom: 2rem;
}

.summary-item {
  padding: 1rem;
  background: #f9fafb;
  border-radius: 0.5rem;
}

.content-section {
  margin-bottom: 1.5rem;
}

.content-section h5 {
  margin-bottom: 0.5rem;
  color: #374151;
}

.content-text {
  color: #6b7280;
}

.header-actions .btn {
  padding: 0.5rem 1rem; /* Slightly smaller padding */
  font-size: 0.875rem; /* Smaller font */
  background-color: #f3f4f6; /* Lighter gray */
  color: #4b5563; /* Darker text */
  border: 1px solid #d1d5db;
  box-shadow: none; /* Remove default shadow */
  font-weight: 500;
}

.header-actions .btn:hover {
  background-color: #e5e7eb;
  border-color: #9ca3af;
  color: #1f2937;
}
</style>
