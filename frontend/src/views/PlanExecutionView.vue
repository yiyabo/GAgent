<template>
  <div class="plan-execution-view">
    <h1>Plan Execution</h1>
    <div v-if="plan">
      <h2>{{ plan.title }}</h2>
      <p>{{ plan.description }}</p>
      <div class="main-content">
        <div class="chat-interface-container">
          <ChatInterface :initial-messages="messages" @send-message="handleSendMessage" />
        </div>
        <div class="right-panel">
          <!-- This area is intentionally left blank -->
        </div>
      </div>
    </div>
    <div v-else>
      <p>Loading plan...</p>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, computed } from 'vue';
import { useRoute } from 'vue-router';
import { usePlansStore } from '@/stores/plans';
import ChatInterface from '@/components/ChatInterface.vue';

const route = useRoute();
const plansStore = usePlansStore();

const planId = ref(parseInt(route.params.id, 10));
const plan = computed(() => plansStore.currentPlan);
const messages = computed(() => plansStore.currentChatHistory);

onMounted(() => {
  plansStore.loadPlanDetails(planId.value);
});

const handleSendMessage = async (command) => {
  try {
    await plansStore.executeAgentCommand(planId.value, command);
  } catch (error) {
    // The store now handles adding the error message to the chat, 
    // so we just need to log it here for debugging.
    console.error('Error executing agent command:', error);
  }
};

</script>

<style scoped>
.plan-execution-view {
  padding: 20px;
}

.main-content {
  display: flex;
  flex-direction: row;
  gap: 20px;
  margin-top: 20px;
}

.chat-interface-container {
  flex: 1;
  min-width: 0; /* Prevents flexbox overflow */
}

.right-panel {
  flex: 1;
  min-width: 0; /* Prevents flexbox overflow */
  border: 2px dashed #ccc;
  border-radius: 8px;
  background-color: #f9f9f9;
}
</style>