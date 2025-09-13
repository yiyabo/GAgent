<template>
  <div class="plans-view">
    <div class="plans-header">
      <div class="header-content">
        <h1>All Plans</h1>
        <p class="header-subtitle">Manage your AI-generated plans and track progress</p>
      </div>
      <router-link to="/plans/new" class="btn btn-primary">
        <span class="btn-icon">✨</span>
        Create New Plan
      </router-link>
    </div>

    <!-- Loading State -->
    <div v-if="loading" class="loading">
      <div class="spinner"></div>
      <p>Loading your plans...</p>
    </div>

    <!-- Error State -->
    <div v-else-if="error" class="error">
      <div class="error-icon">⚠️</div>
      <h3>Oops! Something went wrong</h3>
      <p>{{ error }}</p>
      <button @click="reloadPlans" class="btn btn-retry">Try Again</button>
    </div>

    <!-- Empty State -->
    <div v-else-if="plans.length === 0" class="empty-state">
      <div class="empty-illustration">
        <div class="empty-icon">📊</div>
        <div class="empty-sparkles">
          <span class="sparkle">✨</span>
          <span class="sparkle">⭐</span>
          <span class="sparkle">💫</span>
        </div>
      </div>
      <h3>No Plans Yet</h3>
      <p>Ready to create something amazing? Start with your first AI-generated plan.</p>
      <router-link to="/plans/new" class="btn btn-primary btn-large">
        <span class="btn-icon">🚀</span>
        Generate First Plan
      </router-link>
    </div>

    <!-- Plans Grid -->
    <div v-else class="plans-grid">
      <div 
        v-for="plan in plans" 
        :key="plan.id" 
        class="plan-card"
        :class="{ 'plan-completed': getProgress(plan.id) === 100 }"
      >
        <!-- Card Background Decoration -->
        <div class="card-decoration">
          <div class="decoration-circle decoration-circle-1"></div>
          <div class="decoration-circle decoration-circle-2"></div>
          <div class="decoration-line"></div>
        </div>

        <div class="plan-header">
          <div class="plan-title-section">
            <h3 class="plan-title">{{ plan.title }}</h3>
            <div class="plan-badge" v-if="getProgress(plan.id) === 100">
              <span class="badge-icon">✅</span>
              <span class="badge-text">Completed</span>
            </div>
          </div>
          <div class="plan-actions">
            <router-link 
              :to="`/plan/${plan?.id || encodeURIComponent(plan.title)}/edit`" 
              class="btn btn-ghost btn-sm"
              title="Edit Plan"
            >
              <span class="btn-icon">✏️</span>
            </router-link>
            <router-link 
              :to="`/plan/${plan?.id || encodeURIComponent(plan.title)}/execute`" 
              class="btn btn-primary btn-sm"
              title="Execute Plan"
            >
              <span class="btn-icon">▶️</span>
            </router-link>
          </div>
        </div>

        <div class="plan-stats">
          <div class="stat-item stat-tasks">
            <div class="stat-icon">📋</div>
            <div class="stat-info">
              <span class="stat-value">{{ getTaskCount(plan.id) }}</span>
              <span class="stat-label">Tasks</span>
            </div>
          </div>
          <div class="stat-item stat-completed">
            <div class="stat-icon">✅</div>
            <div class="stat-info">
              <span class="stat-value">{{ getCompletedCount(plan.id) }}</span>
              <span class="stat-label">Done</span>
            </div>
          </div>
          <div class="stat-item stat-pending">
            <div class="stat-icon">⏳</div>
            <div class="stat-info">
              <span class="stat-value">{{ getPendingCount(plan.id) }}</span>
              <span class="stat-label">Pending</span>
            </div>
          </div>
        </div>

        <div class="plan-progress">
          <div class="progress-header">
            <span class="progress-label">Progress</span>
            <span class="progress-percentage">{{ getProgress(plan.id) }}%</span>
          </div>
          <div class="progress-bar">
            <div 
              class="progress-fill" 
              :style="{ width: getProgress(plan.id) + '%' }"
              :class="{
                'progress-low': getProgress(plan.id) < 30,
                'progress-medium': getProgress(plan.id) >= 30 && getProgress(plan.id) < 70,
                'progress-high': getProgress(plan.id) >= 70 && getProgress(plan.id) < 100,
                'progress-complete': getProgress(plan.id) === 100
              }"
            >
              <div class="progress-shine"></div>
            </div>
          </div>
        </div>

        <div class="plan-actions-bottom">
          <button 
            @click="handleQuickExecute(plan)" 
            class="btn btn-success"
            :disabled="getPendingCount(plan.id) === 0"
            :title="getPendingCount(plan.id) === 0 ? 'No pending tasks' : 'Quick execute pending tasks'"
          >
            <span class="btn-icon">⚡</span>
            <span class="btn-text">Quick Execute</span>
            <span v-if="getPendingCount(plan.id) > 0" class="task-count">({{ getPendingCount(plan.id) }})</span>
          </button>
          <button 
            @click="confirmDelete(plan)" 
            class="btn btn-danger"
            :disabled="plansStore.loading"
            title="Delete Plan"
          >
            <span class="btn-icon">🗑️</span>
          </button>
        </div>
      </div>
    </div>

    <!-- Delete Confirmation Modal -->
    <div v-if="deleteConfirm.show" class="modal-overlay" @click="cancelDelete">
      <div class="modal-content" @click.stop>
        <div class="modal-header">
          <div class="modal-title-section">
            <div class="modal-icon">🗑️</div>
            <h3>确认删除</h3>
          </div>
          <button @click="cancelDelete" class="close-btn">&times;</button>
        </div>
        
        <div class="modal-body">
          <div class="warning-message">
            <p><strong>确定要删除计划「{{ deleteConfirm.plan?.title }}」吗？</strong></p>
            <p class="warning-text">此操作将删除该计划及所有相关任务，无法撤销。</p>
          </div>
        </div>
        
        <div class="modal-footer">
          <button @click="cancelDelete" class="btn btn-ghost">取消</button>
          <button @click="executeDeletion" class="btn btn-danger" :disabled="deleteConfirm.deleting">
            <span v-if="deleteConfirm.deleting" class="btn-icon spinning">⏳</span>
            <span v-else class="btn-icon">🗑️</span>
            {{ deleteConfirm.deleting ? '删除中...' : '确认删除' }}
          </button>
        </div>
      </div>
    </div>

    <!-- Debug Toolbar -->
    <div class="debug-toolbar">
      <button @click="reloadPlans" class="btn btn-ghost btn-sm">
        <span class="btn-icon">🔄</span>
        Refresh
      </button>
      <button @click="testConnection" class="btn btn-ghost btn-sm">
        <span class="btn-icon">🔗</span>
        Test Connection
      </button>
    </div>
  </div>
</template>

<script setup>
import { onMounted, reactive, computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { usePlansStore } from '../stores/plans.js'
import { healthApi, plansApi } from '../services/api.js'

const router = useRouter()
const plansStore = usePlansStore()

const plansData = reactive({
  tasks: {},
  completed: {},
  pending: {}
})

const deleteConfirm = ref({
  show: false,
  plan: null,
  deleting: false
})

onMounted(async () => {
  await loadPlanData()
})

const plans = computed(() => plansStore.plans)
const loading = computed(() => plansStore.plansLoading)
const error = computed(() => plansStore.error)

const loadPlanData = async () => {
  await plansStore.loadPlans()
  
  for (const plan of plans.value) {
    try {
      console.log(`Loading tasks for plan ${plan.id} (${plan.title})...`)
      const tasks = await plansApi.getPlanTasks(plan.id)
      const tasksArray = Array.isArray(tasks) ? tasks : []
      console.log(`Plan ${plan.id}: ${tasksArray.length} tasks found`)
      plansData.tasks[plan.id] = tasksArray.length || 0
      plansData.completed[plan.id] = tasksArray.filter(t => t?.status === 'done').length || 0
      plansData.pending[plan.id] = tasksArray.filter(t => t?.status === 'pending').length || 0
    } catch (e) {
      console.warn(`Failed to load data for plan ${plan.id}: ${plan.title}`, e)
      plansData.tasks[plan.id] = 0
      plansData.completed[plan.id] = 0
      plansData.pending[plan.id] = 0
    }
  }
}

const getTaskCount = (planId) => plansData.tasks[planId] || 0
const getCompletedCount = (planId) => plansData.completed[planId] || 0
const getPendingCount = (planId) => plansData.pending[planId] || 0

const getProgress = (planId) => {
  const total = getTaskCount(planId)
  const completed = getCompletedCount(planId)
  return total > 0 ? Math.round((completed / total) * 100) : 0
}

const handleQuickExecute = async (plan) => {
  router.push(`/plan/${plan.id}/execute`)
}

const confirmDelete = (plan) => {
  console.log('Delete button clicked for plan:', plan.title, 'ID:', plan.id)
  deleteConfirm.value = {
    show: true,
    plan: plan,
    deleting: false
  }
}

const cancelDelete = () => {
  deleteConfirm.value = {
    show: false,
    plan: null,
    deleting: false
  }
}

const executeDeletion = async () => {
  const plan = deleteConfirm.value.plan
  if (!plan) return
  
  deleteConfirm.value.deleting = true
  
  try {
    console.log('Executing deletion for plan:', plan.title, 'ID:', plan.id)
    await plansApi.deletePlan(plan.id)
    console.log('Plan deleted successfully, reloading...')
    await plansStore.loadPlans()
    
    delete plansData.tasks[plan.id]
    delete plansData.completed[plan.id]
    delete plansData.pending[plan.id]
    
    cancelDelete()
    console.log(`Plan "${plan.title}" deleted successfully!`)
    
  } catch (error) {
    console.error('Failed to delete plan:', error)
    alert(`❌ Failed to delete plan: ${error.message || 'Unknown error'}`)
  } finally {
    deleteConfirm.value.deleting = false
  }
}

const reloadPlans = async () => {
  await plansStore.loadPlans()
  await loadPlanData()
}

const testConnection = async () => {
  try {
    const health = await healthApi.checkLLMHealth()
    alert(
      health.ping_ok 
        ? '✅ Connected successfully!' 
        : '⚠️ Connected but LLM may have issues'
    )
  } catch (e) {
    alert('❌ Connection failed: ' + e.message)
  }
}
</script>

<style scoped>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

* {
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}

.plans-view {
  max-width: 1200px;
  margin: 0 auto;
  padding: 2rem 1rem;
  background: linear-gradient(135deg, #f8fafc 0%, #e2e8f0 100%);
  min-height: 100vh;
}

/* Header Styles */
.plans-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 3rem;
  background: linear-gradient(135deg, #ffffff 0%, #f1f5f9 100%);
  padding: 2rem;
  border-radius: 1.5rem;
  box-shadow: 0 10px 25px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -2px rgba(0, 0, 0, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.5);
}

.header-content h1 {
  font-size: 2.5rem;
  font-weight: 700;
  background: linear-gradient(135deg, #1e293b, #475569);
  background-clip: text;
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  margin: 0 0 0.5rem 0;
}

.header-subtitle {
  color: #64748b;
  font-size: 1.1rem;
  margin: 0;
  font-weight: 500;
}

/* Button Styles */
.btn {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.875rem 1.5rem;
  font-size: 1rem;
  font-weight: 600;
  text-decoration: none;
  border: none;
  border-radius: 0.75rem;
  cursor: pointer;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  text-align: center;
  position: relative;
  overflow: hidden;
  white-space: nowrap;
}

.btn::before {
  content: '';
  position: absolute;
  top: 0;
  left: -100%;
  width: 100%;
  height: 100%;
  background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.2), transparent);
  transition: left 0.5s;
}

.btn:hover::before {
  left: 100%;
}

.btn-primary {
  background: linear-gradient(135deg, #3b82f6, #1d4ed8);
  color: white;
  box-shadow: 0 4px 14px 0 rgba(59, 130, 246, 0.4);
  border: 1px solid rgba(255, 255, 255, 0.1);
}

.btn-primary:hover {
  background: linear-gradient(135deg, #2563eb, #1e40af);
  transform: translateY(-2px);
  box-shadow: 0 8px 25px 0 rgba(59, 130, 246, 0.5);
}

.btn-secondary {
  background: linear-gradient(135deg, #64748b, #475569);
  color: white;
  box-shadow: 0 4px 14px 0 rgba(100, 116, 139, 0.3);
}

.btn-secondary:hover {
  background: linear-gradient(135deg, #475569, #334155);
  transform: translateY(-2px);
}

.btn-success {
  background: linear-gradient(135deg, #10b981, #059669);
  color: white;
  box-shadow: 0 4px 14px 0 rgba(16, 185, 129, 0.4);
  flex: 1;
}

.btn-success:hover:not(:disabled) {
  background: linear-gradient(135deg, #059669, #047857);
  transform: translateY(-2px);
  box-shadow: 0 8px 25px 0 rgba(16, 185, 129, 0.5);
}

.btn-success:disabled {
  background: linear-gradient(135deg, #d1fae5, #a7f3d0);
  color: #065f46;
  cursor: not-allowed;
  box-shadow: none;
  opacity: 0.6;
}

.btn-danger {
  background: linear-gradient(135deg, #ef4444, #dc2626);
  color: white;
  box-shadow: 0 4px 14px 0 rgba(239, 68, 68, 0.4);
}

.btn-danger:hover:not(:disabled) {
  background: linear-gradient(135deg, #dc2626, #b91c1c);
  transform: translateY(-2px);
  box-shadow: 0 8px 25px 0 rgba(239, 68, 68, 0.5);
}

.btn-ghost {
  background: rgba(255, 255, 255, 0.8);
  color: #475569;
  border: 1px solid rgba(71, 85, 105, 0.2);
  backdrop-filter: blur(10px);
}

.btn-ghost:hover {
  background: rgba(255, 255, 255, 0.95);
  color: #1e293b;
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
}

.btn-sm {
  padding: 0.5rem 1rem;
  font-size: 0.875rem;
}

.btn-large {
  padding: 1rem 2rem;
  font-size: 1.1rem;
}

.btn-icon {
  font-size: 1.1em;
}

.task-count {
  background: rgba(255, 255, 255, 0.2);
  padding: 0.125rem 0.375rem;
  border-radius: 0.5rem;
  font-size: 0.75rem;
  font-weight: 700;
}

/* Plan Cards Grid */
.plans-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(380px, 1fr));
  gap: 2rem;
}

/* Plan Card */
.plan-card {
  background: linear-gradient(135deg, #ffffff 0%, #f8fafc 100%);
  border-radius: 1.5rem;
  padding: 2rem;
  position: relative;
  overflow: hidden;
  transition: all 0.4s cubic-bezier(0.4, 0, 0.2, 1);
  border: 1px solid rgba(255, 255, 255, 0.5);
  box-shadow: 
    0 10px 25px -3px rgba(0, 0, 0, 0.1), 
    0 4px 6px -2px rgba(0, 0, 0, 0.05);
}

.plan-card:hover {
  transform: translateY(-8px) scale(1.02);
  box-shadow: 
    0 25px 50px -12px rgba(0, 0, 0, 0.25),
    0 10px 20px -5px rgba(0, 0, 0, 0.1);
}

.plan-card.plan-completed {
  background: linear-gradient(135deg, #f0fdf4 0%, #dcfce7 100%);
  border-color: rgba(34, 197, 94, 0.2);
}

/* Card Decoration */
.card-decoration {
  position: absolute;
  top: 0;
  right: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
  opacity: 0.6;
}

.decoration-circle {
  position: absolute;
  border-radius: 50%;
  background: linear-gradient(135deg, rgba(99, 102, 241, 0.1), rgba(168, 85, 247, 0.1));
}

.decoration-circle-1 {
  width: 80px;
  height: 80px;
  top: -20px;
  right: -20px;
}

.decoration-circle-2 {
  width: 40px;
  height: 40px;
  top: 60px;
  right: 20px;
}

.decoration-line {
  position: absolute;
  top: 40px;
  right: 40px;
  width: 60px;
  height: 2px;
  background: linear-gradient(90deg, rgba(99, 102, 241, 0.3), transparent);
  border-radius: 1px;
}

/* Plan Header */
.plan-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 1.5rem;
  position: relative;
  z-index: 1;
}

.plan-title-section {
  flex: 1;
  margin-right: 1rem;
}

.plan-title {
  font-size: 1.5rem;
  font-weight: 700;
  color: #1e293b;
  margin: 0 0 0.5rem 0;
  line-height: 1.3;
  word-break: break-word;
}

.plan-badge {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  background: linear-gradient(135deg, #10b981, #059669);
  color: white;
  padding: 0.25rem 0.75rem;
  border-radius: 1rem;
  font-size: 0.75rem;
  font-weight: 600;
  box-shadow: 0 2px 8px rgba(16, 185, 129, 0.3);
}

.plan-actions {
  display: flex;
  gap: 0.5rem;
  flex-shrink: 0;
}

/* Plan Stats */
.plan-stats {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 1rem;
  margin-bottom: 2rem;
}

.stat-item {
  background: rgba(255, 255, 255, 0.7);
  padding: 1rem;
  border-radius: 1rem;
  display: flex;
  align-items: center;
  gap: 0.75rem;
  transition: all 0.3s ease;
  border: 1px solid rgba(255, 255, 255, 0.5);
  backdrop-filter: blur(10px);
}

.stat-item:hover {
  background: rgba(255, 255, 255, 0.9);
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
}

.stat-icon {
  font-size: 1.5rem;
  opacity: 0.8;
}

.stat-info {
  display: flex;
  flex-direction: column;
}

.stat-value {
  font-size: 1.5rem;
  font-weight: 700;
  color: #1e293b;
  line-height: 1;
}

.stat-label {
  font-size: 0.75rem;
  color: #64748b;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.stat-tasks .stat-icon { color: #3b82f6; }
.stat-completed .stat-icon { color: #10b981; }
.stat-pending .stat-icon { color: #f59e0b; }

/* Progress Bar */
.plan-progress {
  margin-bottom: 2rem;
}

.progress-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 0.75rem;
}

.progress-label {
  font-size: 0.875rem;
  color: #64748b;
  font-weight: 600;
}

.progress-percentage {
  font-size: 1rem;
  font-weight: 700;
  color: #1e293b;
}

.progress-bar {
  width: 100%;
  height: 12px;
  background: rgba(226, 232, 240, 0.8);
  border-radius: 6px;
  overflow: hidden;
  position: relative;
  box-shadow: inset 0 2px 4px rgba(0, 0, 0, 0.1);
}

.progress-fill {
  height: 100%;
  border-radius: 6px;
  transition: all 0.6s cubic-bezier(0.4, 0, 0.2, 1);
  position: relative;
  overflow: hidden;
}

.progress-fill.progress-low {
  background: linear-gradient(135deg, #fbbf24, #f59e0b);
}

.progress-fill.progress-medium {
  background: linear-gradient(135deg, #3b82f6, #2563eb);
}

.progress-fill.progress-high {
  background: linear-gradient(135deg, #8b5cf6, #7c3aed);
}

.progress-fill.progress-complete {
  background: linear-gradient(135deg, #10b981, #059669);
}

.progress-shine {
  position: absolute;
  top: 0;
  left: -100%;
  width: 100%;
  height: 100%;
  background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.4), transparent);
  animation: shine 2s ease-in-out infinite;
}

@keyframes shine {
  0% { left: -100%; }
  50% { left: 100%; }
  100% { left: 100%; }
}

/* Action Buttons */
.plan-actions-bottom {
  display: flex;
  gap: 1rem;
  align-items: center;
}

/* Loading State */
.loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 4rem;
  color: #64748b;
  background: rgba(255, 255, 255, 0.8);
  border-radius: 1.5rem;
  backdrop-filter: blur(10px);
}

.spinner {
  width: 50px;
  height: 50px;
  border: 4px solid rgba(59, 130, 246, 0.3);
  border-top: 4px solid #3b82f6;
  border-radius: 50%;
  animation: spin 1s linear infinite;
  margin-bottom: 1.5rem;
}

@keyframes spin {
  0% { transform: rotate(0deg); }
  100% { transform: rotate(360deg); }
}

.loading p {
  font-size: 1.1rem;
  font-weight: 500;
}

/* Error State */
.error {
  text-align: center;
  padding: 3rem;
  background: linear-gradient(135deg, #fef2f2, #fee2e2);
  border-radius: 1.5rem;
  border: 1px solid rgba(239, 68, 68, 0.2);
}

.error-icon {
  font-size: 4rem;
  margin-bottom: 1rem;
}

.error h3 {
  color: #dc2626;
  margin-bottom: 1rem;
  font-size: 1.5rem;
}

.error p {
  color: #991b1b;
  margin-bottom: 2rem;
  font-size: 1.1rem;
}

/* Empty State */
.empty-state {
  text-align: center;
  padding: 4rem 2rem;
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.9), rgba(248, 250, 252, 0.9));
  border-radius: 2rem;
  backdrop-filter: blur(10px);
  border: 1px solid rgba(255, 255, 255, 0.5);
}

.empty-illustration {
  position: relative;
  margin-bottom: 2rem;
}

.empty-icon {
  font-size: 5rem;
  margin-bottom: 1rem;
  display: inline-block;
  animation: float 3s ease-in-out infinite;
}

.empty-sparkles {
  position: absolute;
  top: 0;
  left: 50%;
  transform: translateX(-50%);
  width: 200px;
  height: 100px;
}

.sparkle {
  position: absolute;
  font-size: 1.5rem;
  animation: sparkle 2s ease-in-out infinite;
}

.sparkle:nth-child(1) {
  top: 10px;
  left: 20px;
  animation-delay: 0s;
}

.sparkle:nth-child(2) {
  top: 30px;
  right: 30px;
  animation-delay: 0.7s;
}

.sparkle:nth-child(3) {
  top: 60px;
  left: 50%;
  animation-delay: 1.4s;
}

@keyframes float {
  0%, 100% { transform: translateY(0px); }
  50% { transform: translateY(-10px); }
}

@keyframes sparkle {
  0%, 100% { opacity: 0.3; transform: scale(0.8); }
  50% { opacity: 1; transform: scale(1.2); }
}

.empty-state h3 {
  font-size: 2rem;
  color: #1e293b;
  margin-bottom: 1rem;
  font-weight: 700;
}

.empty-state p {
  color: #64748b;
  font-size: 1.1rem;
  margin-bottom: 2rem;
  max-width: 400px;
  margin-left: auto;
  margin-right: auto;
  line-height: 1.6;
}

/* Modal Styles */
.modal-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.6);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  padding: 1rem;
  backdrop-filter: blur(8px);
  animation: modalFadeIn 0.3s ease-out;
}

@keyframes modalFadeIn {
  from { opacity: 0; }
  to { opacity: 1; }
}

.modal-content {
  background: linear-gradient(135deg, #ffffff, #f8fafc);
  border-radius: 1.5rem;
  max-width: 500px;
  width: 100%;
  max-height: 85vh;
  overflow-y: auto;
  box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.25);
  border: 1px solid rgba(255, 255, 255, 0.5);
  animation: modalSlideIn 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

@keyframes modalSlideIn {
  from { 
    opacity: 0;
    transform: translateY(-20px) scale(0.9);
  }
  to { 
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

.modal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 2rem;
  border-bottom: 1px solid rgba(226, 232, 240, 0.8);
  background: linear-gradient(135deg, #fef2f2, #fee2e2);
}

.modal-title-section {
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

.modal-icon {
  font-size: 1.5rem;
}

.modal-header h3 {
  margin: 0;
  font-size: 1.5rem;
  font-weight: 700;
  color: #1f2937;
}

.close-btn {
  background: rgba(255, 255, 255, 0.8);
  border: none;
  font-size: 1.5rem;
  cursor: pointer;
  color: #64748b;
  width: 2.5rem;
  height: 2.5rem;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s;
  backdrop-filter: blur(10px);
}

.close-btn:hover {
  background: rgba(0, 0, 0, 0.1);
  color: #1e293b;
  transform: scale(1.1);
}

.modal-body {
  padding: 2rem;
}

.warning-message p {
  margin-bottom: 1rem;
  color: #374151;
  line-height: 1.6;
  font-size: 1rem;
}

.warning-text {
  background: linear-gradient(135deg, #fef3c7, #fde68a);
  padding: 1rem;
  border-radius: 0.75rem;
  border-left: 4px solid #f59e0b;
  font-size: 0.9rem;
  color: #92400e;
}

.modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 1rem;
  padding: 2rem;
  border-top: 1px solid rgba(226, 232, 240, 0.8);
  background: rgba(248, 250, 252, 0.5);
}

/* Debug Toolbar */
.debug-toolbar {
  margin-top: 3rem;
  text-align: center;
  padding: 2rem;
  background: rgba(255, 255, 255, 0.6);
  border-radius: 1rem;
  backdrop-filter: blur(10px);
  border: 1px solid rgba(255, 255, 255, 0.5);
}

.debug-toolbar .btn {
  margin: 0 0.5rem;
}

/* Animations */
.spinning {
  animation: spin 1s linear infinite;
}

/* Responsive Design */
@media (max-width: 768px) {
  .plans-view {
    padding: 1rem 0.5rem;
  }
  
  .plans-header {
    flex-direction: column;
    gap: 1.5rem;
    text-align: center;
    padding: 1.5rem;
  }
  
  .header-content h1 {
    font-size: 2rem;
  }
  
  .plans-grid {
    grid-template-columns: 1fr;
    gap: 1.5rem;
  }
  
  .plan-card {
    padding: 1.5rem;
  }
  
  .plan-header {
    flex-direction: column;
    gap: 1rem;
  }
  
  .plan-actions {
    justify-content: center;
    width: 100%;
  }
  
  .plan-actions .btn {
    flex: 1;
  }
  
  .plan-stats {
    grid-template-columns: 1fr;
    gap: 0.75rem;
  }
  
  .plan-actions-bottom {
    flex-direction: column;
    gap: 0.75rem;
  }
  
  .btn-success {
    width: 100%;
  }
  
  .modal-content {
    margin: 0.5rem;
    border-radius: 1rem;
  }
  
  .modal-header,
  .modal-body,
  .modal-footer {
    padding: 1.5rem;
  }
  
  .modal-footer {
    flex-direction: column;
  }
}

@media (max-width: 480px) {
  .plan-card {
    padding: 1rem;
  }
  
  .plan-title {
    font-size: 1.25rem;
  }
  
  .stat-item {
    padding: 0.75rem;
  }
  
  .stat-value {
    font-size: 1.25rem;
  }
  
  .empty-state {
    padding: 2rem 1rem;
  }
  
  .empty-state h3 {
    font-size: 1.5rem;
  }
  
  .empty-icon {
    font-size: 3.5rem;
  }
}
</style>