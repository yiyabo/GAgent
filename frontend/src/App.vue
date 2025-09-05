<template>
  <div id="app">
    <nav class="navbar">
      <div class="nav-brand">
        <h1>GAgent Plan Manager</h1>
      </div>
      <div class="nav-links">
        <router-link to="/" class="nav-link">Home</router-link>
        <router-link to="/plans" class="nav-link">Plans</router-link>
        <router-link to="/plans/new" class="nav-link">Generate New</router-link>
        <router-link v-if="chatLink" :to="chatLink" class="nav-link">Chat</router-link>
      </div>
    </nav>

    <main class="main-content" :class="{ 'full-width': isChatView }">
      <router-view />
    </main>
  </div>
</template>

<script setup>
import { computed, onMounted } from 'vue';
import { useRoute } from 'vue-router';
import { usePlansStore } from '@/stores/plans';

const plansStore = usePlansStore();
const route = useRoute();

const isChatView = computed(() => route.name === 'Chat');

onMounted(() => {
  // Load plans when the app mounts to ensure the link is available
  if (plansStore.plans.length === 0) {
    plansStore.loadPlans();
  }
});

const chatLink = computed(() => {
  if (plansStore.plans.length > 0) {
    // Assuming the first plan is the most relevant one
    const firstPlan = plansStore.plans[0];
    return `/plan/${firstPlan.id}/chat`;
  }
  return null; // or a fallback route
});
</script>

<style>
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
  background-color: #f5f5f5;
  color: #333;
}

.navbar {
  background: #2563eb;
  color: white;
  padding: 1rem 2rem;
  display: flex;
  justify-content: space-between;
  align-items: center;
  box-shadow: 0 2px 4px rgba(0,0,0,0.1);
}

.nav-brand h1 {
  font-size: 1.5rem;
  font-weight: 700;
}

.nav-links {
  display: flex;
  gap: 2rem;
}

.nav-link {
  color: white;
  text-decoration: none;
  padding: 0.5rem 1rem;
  border-radius: 0.5rem;
  transition: background-color 0.2s;
}

.nav-link:hover,
.nav-link.router-link-active {
  background-color: rgba(255, 255, 255, 0.2);
}

.main-content {
  max-width: 1200px;
  margin: 0 auto;
  padding: 2rem;
}

.main-content.full-width {
  max-width: none;
  padding: 0;
  margin: 0;
}

.btn {
  padding: 0.75rem 1.5rem;
  border: none;
  border-radius: 0.5rem;
  cursor: pointer;
  font-size: 1rem;
  transition: all 0.2s;
}

.btn-primary {
  background-color: #2563eb;
  color: white;
}

.btn-primary:hover {
  background-color: #1d4ed8;
}

.btn-secondary {
  background-color: #6b7280;
  color: white;
}

.btn-secondary:hover {
  background-color: #4b5563;
}

.card {
  background: white;
  border-radius: 0.5rem;
  padding: 1.5rem;
  box-shadow: 0 1px 3px rgba(0,0,0,0.1);
  margin-bottom: 1rem;
}

.form-group {
  margin-bottom: 1rem;
}

label {
  display: block;
  margin-bottom: 0.5rem;
  font-weight: 600;
}

input, textarea, select {
  width: 100%;
  padding: 0.75rem;
  border: 1px solid #d1d5db;
  border-radius: 0.5rem;
  font-size: 1rem;
}

input:focus, textarea:focus, select:focus {
  outline: none;
  border-color: #2563eb;
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.1);
}

.loading {
  text-align: center;
  padding: 2rem;
  color: #6b7280;
}

.error {
  color: #dc2626;
  background: #fef2f2;
  padding: 1rem;
  border-radius: 0.5rem;
  margin-bottom: 1rem;
}

.success {
  color: #059669;
  background: #f0fdf4;
  padding: 1rem;
  border-radius: 0.5rem;
  margin-bottom: 1rem;
}
</style>