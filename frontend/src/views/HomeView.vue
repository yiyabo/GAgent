<template>
  <div class="home-view">
    <div class="hero-section">
      <h1>GAgent Plan Manager</h1>
      <p class="subtitle">AI-powered plan generation, editing, and execution</p>
    </div>

    <div class="quick-actions">
      <div class="action-card">
        <h2>📋 Create New Plan</h2>
        <p>Generate AI-powered plans from research goals</p>
        <router-link to="/plans/new" class="btn btn-primary">
          Generate Plan
        </router-link>
      </div>

      <div class="action-card">
        <h2>🔧 Manage Plans</h2>
        <p>View and edit existing plans</p>
        <router-link to="/plans" class="btn btn-secondary">
          Browse Plans
        </router-link>
      </div>

      <div class="action-card">
        <h2>⚡ Quick Execute</h2>
        <p>Execute and monitor plan progress</p>
        <div class="recent-plans">
          <h3>Recent Plans</h3>
          <div v-if="loading" class="loading">Loading plans...</div>
          <div v-else-if="error" class="error">{{ error }}</div>
          <div v-else-if="recentPlans.length === 0" class="no-plans">
            No plans yet. Create your first plan!
          </div>
          <div v-else>
            <div 
              v-for="plan in recentPlans.slice(0, 3)" 
              :key="plan.id" 
              class="plan-link"
              @click="goToPlan(plan)"
            >
              📊 {{ plan.title }}
            </div>
          </div>
        </div>
      </div>
    </div>

    <div class="stats-section" v-if="!loading">
      <h2>📊 System Overview</h2>
      <div class="stats-grid">
        <div class="stat-card">
          <div class="stat-number">{{ totalPlans }}</div>
          <div class="stat-label">Total Plans</div>
        </div>
        <div class="stat-card">
          <div class="stat-number">{{ pendingTasks }}</div>
          <div class="stat-label">Tasks Pending</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { usePlansStore } from '../stores/plans.js'
import { computed } from 'vue'

const router = useRouter()
const plansStore = usePlansStore()

onMounted(async () => {
  await plansStore.loadPlans()
})

const recentPlans = computed(() => plansStore.plans)
const totalPlans = computed(() => plansStore.plans.length)
const pendingTasks = computed(() => {
  // This would need to be calculated based on expanded plan data
  return 'Check plans'
})

const loading = computed(() => plansStore.loading)
const error = computed(() => plansStore.error)

const goToPlan = (plan) => {
  if (plan && plan.id) {
    router.push(`/plan/${plan.id}/edit`)
  }
}
</script>

<style scoped>
.home-view {
  max-width: 1200px;
  margin: 0 auto;
  padding: 2rem 1rem;
}

.hero-section {
  text-align: center;
  margin-bottom: 3rem;
}

.hero-section h1 {
  font-size: 3rem;
  margin-bottom: 1rem;
  background: linear-gradient(to right, #2563eb, #1e40af);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}

.subtitle {
  font-size: 1.5rem;
  color: #6b7280;
  margin-bottom: 2rem;
}

.quick-actions {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 2rem;
  margin-bottom: 3rem;
}

.action-card {
  background: white;
  padding: 2rem;
  border-radius: 0.5rem;
  box-shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.1);
  text-align: center;
}

.action-card h2 {
  margin-bottom: 0.5rem;
  color: #1f2937;
}

.action-card p {
  color: #6b7280;
  margin-bottom: 1.5rem;
}

.action-card .btn {
  width: 100%;
}

.recent-plans {
  margin-top: 1rem;
  text-align: left;
}

.recent-plans h3 {
  font-size: 1.125rem;
  margin-bottom: 0.5rem;
  color: #374151;
}

.plan-link {
  padding: 0.5rem;
  margin-bottom: 0.25rem;
  border-radius: 0.25rem;
  background: #f9fafb;
  cursor: pointer;
  transition: background-color 0.2s;
}

.plan-link:hover {
  background: #f3f4f6;
}

.stats-section {
  margin-top: 3rem;
}

.stats-section h2 {
  margin-bottom: 1rem;
  color: #1f2937;
}

.stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 1rem;
}

.stat-card {
  background: white;
  padding: 1.5rem;
  border-radius: 0.5rem;
  text-align: center;
  box-shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.1);
}

.stat-number {
  font-size: 2rem;
  font-weight: bold;
  color: #2563eb;
}

.stat-label {
  color: #6b7280;
  margin-top: 0.5rem;
}

.loading {
  color: #6b7280;
  font-style: italic;
}

.error {
  color: #dc2626;
  font-weight: 500;
}

.no-plans {
  color: #9ca3af;
  font-style: italic;
}
</style>