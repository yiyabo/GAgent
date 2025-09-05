<template>
  <div class="conversation-history">
    <div class="history-header">
      <h3 v-if="isOpen">Conversations</h3>
      <button @click="toggle" class="toggle-btn">{{ isOpen ? '<' : '>' }}</button>
    </div>
    <div v-if="isOpen" class="history-content">
      <button @click="startNewConversation">+ New Chat</button>
      <ul>
        <li 
          v-for="convo in conversations"
          :key="convo.id"
          @click="selectConversation(convo.id)"
          :class="{ active: convo.id === selectedConversationId }"
        >
          {{ convo.title }}
        </li>
      </ul>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue';
import { chatApi } from '../services/api.js';

const props = defineProps({
  planId: {
    type: String,
    required: true
  },
  isOpen: {
    type: Boolean,
    default: true
  }
});

const emit = defineEmits(['selectConversation', 'toggle-sidebar']);

const toggle = () => {
  emit('toggle-sidebar');
};

const conversations = ref([]);
const selectedConversationId = ref(null);

const fetchConversations = async (planId) => {
  console.log(`Fetching conversations for plan ${planId}`);
  try {
    const response = await chatApi.getPlanConversations(planId);
    return response;
  } catch (error) {
    console.error('Error fetching conversations:', error);
    return [];
  }
};

onMounted(async () => {
  conversations.value = await fetchConversations(props.planId);
  if (conversations.value.length > 0) {
    // Automatically select the first conversation if available
    selectConversation(conversations.value[0].id);
  }
});

const selectConversation = (id) => {
  selectedConversationId.value = id;
  emit('selectConversation', id);
};

const startNewConversation = async () => {
  const newTitle = `New Chat ${conversations.value.length + 1}`;
  try {
    const newConvo = await chatApi.createConversation(props.planId, newTitle);
    conversations.value.unshift(newConvo);
    selectConversation(newConvo.id);
  } catch (error) {
    console.error('Error creating new conversation:', error);
  }
};

</script>

<style scoped>
.conversation-history {
  width: 250px;
  border-right: 1px solid #ccc;
  padding: 1rem;
  background-color: #f9f9f9;
  display: flex;
  flex-direction: column;
  transition: width 0.3s ease, padding 0.3s ease;
  overflow: hidden;
}

.history-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 1rem;
}

.history-header h3 {
  margin: 0;
}

.toggle-btn {
  background: #eee;
  border: 1px solid #ccc;
  border-radius: 50%;
  width: 24px;
  height: 24px;
  cursor: pointer;
  display: flex;
  justify-content: center;
  align-items: center;
}

.history-content {
  /* New wrapper for content that will be hidden */
}

.conversation-history h3 {
  margin-top: 0;
}

.conversation-history button {
  width: 100%;
  padding: 0.5rem;
  margin-bottom: 1rem;
  background-color: #007bff;
  color: white;
  border: none;
  border-radius: 4px;
  cursor: pointer;
}

.conversation-history button:hover {
  background-color: #0056b3;
}

.conversation-history ul {
  list-style: none;
  padding: 0;
  margin: 0;
  overflow-y: auto;
}

.conversation-history li {
  padding: 0.75rem;
  cursor: pointer;
  border-radius: 4px;
}

.conversation-history li:hover {
  background-color: #e9e9e9;
}

.conversation-history li.active {
  background-color: #007bff;
  color: white;
}
</style>
