
<template>
  <div class="chat-container">
    <div class="messages-display">
      <div 
        v-for="(message, index) in messages" 
        :key="index" 
        :class="['message', message.sender]"
      >
        <div class="message-content">
          <span v-if="message.isStreaming" class="streaming-text">{{ message.text }}</span>
          <span v-else>{{ message.text }}</span>
        </div>
        <button
          v-if="message.sender === 'user' && message.id"
          class="edit-button"
          type="button"
          @click="emit('edit-message', message)"
        >编辑</button>
        <div 
          v-if="message.instructions && message.instructions.length"
          class="message-instructions"
        >
          <div class="instructions-title">LLM 指令</div>
          <ol class="instructions-list">
            <li v-for="(item, idx) in message.instructions" :key="item.id || idx">
              <span class="instruction-index">{{ idx + 1 }}.</span>
              <span class="instruction-text">
                <span class="instruction-intent">{{ item.intent }}</span>
                <span class="instruction-description">{{ item.description }}</span>
                <span v-if="item.needsTool" class="instruction-tag">tool</span>
              </span>
            </li>
          </ol>
        </div>
      </div>
    </div>

    <div v-if="confirmation" class="confirmation-panel">
      <div class="confirmation-header">待执行指令</div>
      <div class="confirmation-body">
        <ol class="confirmation-instructions" v-if="confirmation.instructions?.length">
          <li v-for="(item, idx) in confirmation.instructions" :key="item.id || idx">
            <span class="instruction-index">{{ idx + 1 }}.</span>
            <div class="instruction-body">
              <span class="instruction-intent">{{ item.intent }}</span>
              <span class="instruction-description">{{ item.description }}</span>
              <span v-if="item.needsTool" class="instruction-tag">tool</span>
            </div>
          </li>
        </ol>
        <div v-else class="confirmation-empty">未解析到指令详情，确认后将直接执行。</div>
      </div>
      <el-button
        type="primary"
        size="large"
        class="confirm-button"
        :loading="isStreaming"
        @click="handleConfirmation"
      >
        确认执行
      </el-button>
    </div>
    <div class="chat-input">
      <input
        v-model="newMessage"
        @keyup.enter="sendMessage"
        :disabled="isStreaming || confirmation"
        :placeholder="isStreaming ? 'Waiting for response...' : (confirmation ? 'Please respond to the question above.' : 'Type your command...')"
      />
      <button @click="sendMessage" :disabled="isStreaming || confirmation">
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
  },
  confirmation: {
    type: Object,
    default: null
  },
  isStreaming: {
    type: Boolean,
    default: false
  }
});

const emit = defineEmits(['send-message', 'send-message-stream', 'confirmation-response', 'edit-message']);

const newMessage = ref('');
const messages = ref([]);

watch(() => props.initialMessages, (newVal) => {
  messages.value = [...newVal];
}, { immediate: true, deep: true });

const sendMessage = () => {
  if (newMessage.value.trim() !== '' && !props.isStreaming) {
    if (props.useStreaming) {
      emit('send-message-stream', newMessage.value, props.planId);
    } else {
      emit('send-message', newMessage.value, props.planId);
    }
    newMessage.value = '';
  }
};

const handleConfirmation = () => {
  emit('confirmation-response');
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
  position: relative;
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

.edit-button {
  position: absolute;
  top: 6px;
  right: 10px;
  border: none;
  background: transparent;
  color: #409eff;
  font-size: 12px;
  cursor: pointer;
}

.edit-button:hover {
  text-decoration: underline;
}

.message-instructions {
  margin-top: 8px;
  background: rgba(0, 123, 255, 0.08);
  border-radius: 12px;
  padding: 8px 12px;
}

.instructions-title {
  font-size: 12px;
  font-weight: 600;
  color: #0056b3;
  margin-bottom: 4px;
  letter-spacing: 0.5px;
}

.instructions-list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.instructions-list li {
  display: flex;
  align-items: flex-start;
  font-size: 13px;
  color: #1f2d3d;
  margin-bottom: 4px;
}

.instruction-index {
  font-weight: 600;
  margin-right: 6px;
}

.instruction-text {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.instruction-intent {
  font-weight: 600;
  color: #2b6cb0;
}

.instruction-description {
  color: #475669;
}

.instruction-tag {
  background: #f5a623;
  color: white;
  padding: 0 6px;
  border-radius: 999px;
  font-size: 11px;
  text-transform: uppercase;
}

.confirmation-panel {
  border-top: 1px solid #dcdfe6;
  padding: 16px 20px;
  background: #f0f5ff;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.confirmation-header {
  font-weight: 700;
  color: #1d39c4;
  font-size: 14px;
  letter-spacing: 0.5px;
}

.confirmation-body {
  background: white;
  border: 1px solid #d6e4ff;
  border-radius: 12px;
  padding: 12px 16px;
}

.confirmation-instructions {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.instruction-body {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  color: #475669;
}

.confirmation-empty {
  font-size: 13px;
  color: #909399;
}

.confirm-button {
  align-self: flex-end;
  min-width: 140px;
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
