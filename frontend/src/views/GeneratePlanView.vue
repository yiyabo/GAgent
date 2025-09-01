<template>
  <div class="generate-plan">
    <div class="header">
      <h1>🎯 Generate New Plan</h1>
      <p>Let AI create a structured plan from your research goal</p>
    </div>

    <!-- Step 1: Goal Input -->
    <div v-if="currentStep === 'input'" class="step-card">
      <h2>Step 1: Define Your Research Goal</h2>
      
      <form @submit.prevent="generateProposal" class="goal-form">
        <div class="form-group">
          <label for="goal">Research Goal *</label>
          <textarea
            id="goal"
            v-model="goal"
            placeholder="e.g., Analyze causal relationships in healthcare policies"
            rows="4"
            required
            :disabled="loading"
          ></textarea>
        </div>

        <div class="form-group">
          <label for="title">Plan Title (optional)</label>
          <input
            id="title"
            v-model="planTitle"
            placeholder="Custom plan title, or leave blank for AI suggestion"
            :disabled="loading"
          />
        </div>

        <div class="form-row">
          <div class="form-group">
            <label for="sections">Number of Sections (optional)</label>
            <input
              id="sections"
              type="number"
              v-model.number="sections"
              min="1"
              max="20"
              placeholder=""
              :disabled="loading"
            />
          </div>

          <div class="form-group">
            <label for="style">Writing Style (optional)</label>
            <select id="style" v-model="style" :disabled="loading">
              <option value="">Auto-detect</option>
              <option value="academic">Academic</option>
              <option value="technical">Technical</option>
              <option value="business">Business</option>
              <option value="casual">Casual</option>
            </select>
          </div>
        </div>

        <div class="form-group">
          <label for="notes">Additional Context (optional)</label>
          <textarea
            id="notes"
            v-model="notes"
            placeholder="Any additional context or special requirements"
            rows="3"
            :disabled="loading"
          ></textarea>
        </div>

        <button 
          type="submit" 
          class="btn btn-primary"
          :disabled="loading || !goal"
        >
          {{ loading ? 'Creating...' : 'Create Plan' }}
        </button>
      </form>
    </div>

    <!-- Loading State -->
    <div v-if="loading" class="loading-state">
      <div class="spinner"></div>
      <h3>{{ loadingMessage }}</h3>
      <p>{{ loadingSubtitle }}</p>
    </div>

    <!-- Error State -->
    <div v-if="error" class="error-state">
      <h3>❌ Generation Failed</h3>
      <p>{{ error }}</p>
      <div class="error-actions">
        <button @click="resetToInput" class="btn btn-secondary">
          Try Again
        </button>
        <router-link to="/plans" class="btn btn-primary">
          View Existing Plans
        </router-link>
      </div>
    </div>

    <!-- Success State -->
    <div v-if="successPlanTitle" class="success-state">
      <h3>✅ Plan Created Successfully!</h3>
      <p>Plan "{{ successPlanTitle }}" has been created successfully.</p>
      <div class="success-actions">
        <router-link 
          to="/plans" 
          class="btn btn-primary"
        >
          View Plans
        </router-link>
        <router-link to="/plans/new" class="btn btn-secondary">
          Create Another
        </router-link>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { plansApi } from '../services/api.js'
import { usePlansStore } from '../stores/plans.js'

const plansStore = usePlansStore()

// Reactive state
const goal = ref('')
const planTitle = ref('')
const finalPlanTitle = ref('')
const sections = ref('')
const style = ref('')
const notes = ref('')

const currentStep = ref('input')
const loading = ref(false)
const loadingMessage = ref('')
const loadingSubtitle = ref('')
const successPlanTitle = ref('')
const error = ref('')

onMounted(() => {
  goal.value = ''
  resetForm()
})

const resetForm = () => {
  goal.value = ''
  planTitle.value = ''
  finalPlanTitle.value = ''
  sections.value = ''
  style.value = ''
  notes.value = ''
  currentStep.value = 'input'
  error.value = ''
  successPlanTitle.value = ''
}

const generateProposal = async () => {
  if (!goal.value.trim()) {
    error.value = 'Please enter a research goal'
    return
  }

  loading.value = true
  loadingMessage.value = 'Generating Plan Structure...'
  loadingSubtitle.value = 'AI is analyzing your goal and creating tasks...'
  error.value = ''

  try {
    // Step 1: Generate plan and create actual tasks immediately via plansStore
    const planTitleResult = await plansStore.generateAndApprovePlan(
      goal.value.trim(),
      sections.value > 0 ? sections.value : undefined,
      planTitle.value.trim() || undefined,
      style.value.trim() || undefined,
      notes.value.trim() || undefined
    )
    
    successPlanTitle.value = planTitleResult
    currentStep.value = 'success'
    loading.value = false
  } catch (e) {
    loading.value = false
    error.value = e.message || 'Failed to generate plan'
  }
}


const resetToInput = () => {
  resetForm()
}

</script>

<style scoped>
.generate-plan {
  max-width: 800px;
  margin: 0 auto;
  padding: 2rem;
}

.header {
  text-align: center;
  margin-bottom: 3rem;
}

.header h1 {
  font-size: 2.5rem;
  margin-bottom: 0.5rem;
  color: #1f2937;
}

.header p {
  color: #6b7280;
  font-size: 1.125rem;
}

.step-card {
  background: white;
  border-radius: 0.5rem;
  padding: 2rem;
  box-shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.1);
  margin-bottom: 2rem;
}

.step-card h2 {
  margin-bottom: 1.5rem;
  color: #1f2937;
}

.goal-form {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}

.form-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1rem;
}

.proposed-plan {
  max-width: 600px;
}

.plan-title-input {
  font-size: 1.25rem;
  font-weight: 600;
  margin-bottom: 1rem;
}

.tasks-list {
  margin-bottom: 1.5rem;
}

.task-item {
  display: grid;
  grid-template-columns: auto 1fr auto;
  gap: 1rem;
  align-items: start;
  background: #f9fafb;
  padding: 1rem;
  border-radius: 0.5rem;
  margin-bottom: 1rem;
}

.task-number {
  width: 2rem;
  height: 2rem;
  background: #e5e7eb;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: bold;
  color: #374151;
  font-size: 0.875rem;
}

.task-content {
  min-width: 0;
}

.task-name {
  font-weight: 600;
  margin-bottom: 0.5rem;
}

.task-prompt {
  resize: vertical;
  min-height: 60px;
}

.task-priority {
  min-width: 80px;
}

.task-priority label {
  font-size: 0.875rem;
  font-weight: 600;
  margin-bottom: 0.25rem;
}

.priority-input {
  width: 100%;
}

.plan-actions {
  display: flex;
  gap: 1rem;
  justify-content: flex-end;
  margin-top: 2rem;
}

.loading-state {
  text-align: center;
  padding: 4rem 0;
}

.spinner {
  display: inline-block;
  width: 40px;
  height: 40px;
  border: 3px solid #f3f3f3;
  border-top: 3px solid #2563eb;
  border-radius: 50%;
  animation: spin 2s linear infinite;
  margin-bottom: 1rem;
}

@keyframes spin {
  0% { transform: rotate(0deg); }
  100% { transform: rotate(360deg); }
}

.error-state, .success-state {
  text-align: center;
  padding: 2rem;
}

.error-state h3, .success-state h3 {
  margin-bottom: 1rem;
}

.error-actions, .success-actions {
  display: flex;
  gap: 1rem;
  justify-content: center;
  margin-top: 2rem;
}

@media (max-width: 768px) {
  .form-row {
    grid-template-columns: 1fr;
  }
}
</style>