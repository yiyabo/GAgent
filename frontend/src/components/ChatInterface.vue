
<template>
  <div class="chat-container">
    <div class="messages-display">
      <div v-for="(message, index) in messages" :key="index" :class="['message', message.sender]">
        <div class="message-content">
          <span v-if="message.isStreaming" class="streaming-text">{{ message.text }}</span>
          <span v-else>{{ message.text }}</span>
        </div>
      </div>
    </div>
    <div class="chat-input">
      <input
        v-model="newMessage"
        @keyup.enter="sendMessage"
        :disabled="isStreaming"
        :placeholder="isStreaming ? 'Waiting for response...' : 'Type your command...'"
      />
      <button @click="sendMessage" :disabled="isStreaming">
        {{ isStreaming ? 'Sending...' : 'Send' }}
      </button>
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue';

const props = defineProps({
  initialMessages: {
    type: Array,
    default: () => []
  },
  useStreaming: {
    type: Boolean,
    default: true
  },
  planId: {
    type: Number,
    default: null
  }
});

const emit = defineEmits(['send-message', 'send-message-stream']);

const newMessage = ref('');
const messages = ref([]);
const isStreaming = ref(false);

watch(() => props.initialMessages, (newVal) => {
  messages.value = [...newVal];
}, { immediate: true, deep: true });

const sendMessage = () => {
  if (newMessage.value.trim() !== '' && !isStreaming.value) {
    
    if (props.useStreaming) {
      // Create placeholder for streaming response
      const streamingMessage = { 
        sender: 'agent', 
        text: '', 
        isStreaming: true 
      };
      messages.value.push(streamingMessage);
      isStreaming.value = true;
      
      emit('send-message-stream', newMessage.value, props.planId, {
        onChunk: (chunk, accumulated) => {
          // Update the streaming message with accumulated text
          const lastMessage = messages.value[messages.value.length - 1];
          if (lastMessage && lastMessage.isStreaming) {
            lastMessage.text = accumulated;
          }
        },
        onComplete: (fullText) => {
          // Mark streaming as complete
          const lastMessage = messages.value[messages.value.length - 1];
          if (lastMessage && lastMessage.isStreaming) {
            lastMessage.text = fullText;
            lastMessage.isStreaming = false;
          }
          isStreaming.value = false;
        },
        onError: (error) => {
          // Handle streaming error
          const lastMessage = messages.value[messages.value.length - 1];
          if (lastMessage && lastMessage.isStreaming) {
            lastMessage.text = `Error: ${error}`;
            lastMessage.isStreaming = false;
          }
          isStreaming.value = false;
        }
      });
    } else {
      emit('send-message', newMessage.value, props.planId);
    }
    
    newMessage.value = '';
  }
};

// Expose methods to parent component
const addMessage = (message) => {
  messages.value.push(message);
};

// Method to send a message programmatically
const sendMessageText = (text) => {
  if (text && text.trim()) {
    newMessage.value = text;
    sendMessage();
  }
};

defineExpose({ addMessage, sendMessage: sendMessageText });

</script>

<style scoped>
.chat-container {
  display: flex;
  flex-direction: column;
  border: 1px solid #ccc;
  border-radius: 8px;
  flex-grow: 1; /* Fill available space */
  min-height: 0; /* Prevent flexbox overflow */
  width: 100%;
}

.messages-display {
  flex-grow: 1;
  overflow-y: auto;
  padding: 10px;
  display: flex;
  flex-direction: column;
}

.message {
  margin-bottom: 10px;
  padding: 8px 12px;
  border-radius: 18px;
  max-width: 80%;
}

.message.user {
  background-color: #007bff;
  color: white;
  align-self: flex-end;
}

.message.agent {
  background-color: #f1f1f1;
  color: black;
  align-self: flex-start;
}

.chat-input {
  display: flex;
  padding: 10px;
  border-top: 1px solid #ccc;
}

.chat-input input {
  flex-grow: 1;
  border: 1px solid #ddd;
  border-radius: 20px;
  padding: 8px 12px;
  margin-right: 10px;
}

.chat-input button {
  border: none;
  background-color: #007bff;
  color: white;
  border-radius: 20px;
  padding: 8px 15px;
  cursor: pointer;
}

.chat-input button:hover {
  background-color: #0056b3;
}

.chat-input button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.streaming-text::after {
  content: '●';
  animation: blink 1s infinite;
  margin-left: 4px;
  opacity: 0.7;
}

@keyframes blink {
  0%, 50% { opacity: 0.7; }
  51%, 100% { opacity: 0; }
}
</style>
