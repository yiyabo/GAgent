<template>
  <div class="plan-execution-view">
    <h1>Plan Execution</h1>
    <div v-if="plan">
      <h2>{{ plan.title }}</h2>
      <p>{{ plan.description }}</p>
      <div class="main-content">
        <div class="chat-interface-container">
          <ChatInterface 
            :initial-messages="messages" 
            :plan-id="planId" 
            @send-message="handleSendMessageStream"
            @send-message-stream="handleSendMessageStream"
          />
        </div>
        <div class="right-panel">
          <div class="view-switcher">
            <button @click="currentView = 'list'" :class="{ active: currentView === 'list' }">List View</button>
            <button @click="currentView = 'graph'" :class="{ active: currentView === 'graph' }">Graph View</button>
          </div>
          <div class="visualization-container">
            <TaskTreeView v-if="currentView === 'list'" :tasks="tasks" />
            <PlanGraphView v-if="currentView === 'graph'" :tasks="tasks" />
          </div>
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
import { chatApi } from '@/services/api';
import ChatInterface from '@/components/ChatInterface.vue';
import TaskTreeView from '@/components/TaskTreeView.vue';
import PlanGraphView from '@/components/visualization/PlanGraphView.vue';

const route = useRoute();
const plansStore = usePlansStore();

const planId = ref(parseInt(route.params.id, 10));
const conversationId = ref(null);
const plan = computed(() => plansStore.currentPlan);
const tasks = computed(() => plansStore.currentPlanTasks);
const messages = computed(() => plansStore.currentChatHistory);
const currentView = ref('list'); // 'list' or 'graph'

onMounted(async () => {
  await plansStore.loadPlanDetails(planId.value);
  
  // Get or create a conversation for this plan
  if (plan.value) {
    try {
      // For simplicity, we'll create a new conversation each time.
      // A more robust implementation might store and retrieve this mapping.
      const conversation = await chatApi.createConversation({ title: `Plan - ${plan.value.title}` });
      conversationId.value = conversation.id;
      
      // Optional: Load existing messages if any
      const history = await chatApi.getConversation(conversation.id);
      plansStore.currentChatHistory = history.messages.map(m => ({ sender: m.sender, text: m.text }));

    } catch (error) {
      console.error("Failed to create or load conversation:", error);
    }
  }
});

// Unified handler for both streaming and non-streaming messages
const handleSendMessageStream = async (command, pId, callbacks) => {
  if (!conversationId.value) {
    const errorMsg = "Cannot send message, conversation ID is not set.";
    console.error(errorMsg);
    // If callbacks exist (from streaming), use them. Otherwise, just log.
    if (callbacks && typeof callbacks.onError === 'function') {
      callbacks.onError(errorMsg);
    }
    return;
  }
  try {
    // Ensure callbacks is an object even for non-streaming calls
    const safeCallbacks = callbacks || { onChunk: ()=>{}, onComplete: ()=>{}, onError: ()=>{} };
    await plansStore.executeAgentCommandStream(conversationId.value, pId || planId.value, command, safeCallbacks);
  } catch (error) {
    console.error('Error executing agent command stream:', error);
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
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.view-switcher {
  display: flex;
  gap: 10px;
  margin-bottom: 10px;
}

.view-switcher button {
  padding: 8px 16px;
  border: 1px solid #ccc;
  background-color: #f0f0f0;
  cursor: pointer;
  border-radius: 4px;
}

.view-switcher button.active {
  background-color: #3b82f6;
  color: white;
  border-color: #3b82f6;
}

.visualization-container {
  flex-grow: 1;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 10px;
  background-color: #f9f9f9;
  overflow: auto;
}
</style>