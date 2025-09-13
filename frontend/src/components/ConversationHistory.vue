<template>
  <div class="conversation-history">
    <div class="history-header">
      <h3 v-if="isOpen">Conversations</h3>
    </div>
    <div v-if="isOpen" class="history-content">
      <button @click="startNewConversation">+ New Chat</button>
      <ul>
        <li 
          v-for="convo in conversations"
          :key="convo.id"
          :class="{ active: convo.id === selectedConversationId }"
        >
          <div class="conversation-item">
            <div 
              v-if="editingConversation !== convo.id"
              class="conversation-title"
              @click="selectConversation(convo.id)"
            >
              {{ convo.title }}
            </div>
            <input
              v-else
              v-model="editingTitle"
              @blur="saveTitle(convo.id)"
              @keyup.enter="saveTitle(convo.id)"
              @keyup.escape="cancelEdit()"
              class="title-input"
              ref="titleInput"
            />
            <div class="conversation-actions">
              <button
                v-if="editingConversation !== convo.id"
                @click.stop="startEdit(convo.id, convo.title)"
                class="action-btn edit-btn"
                title="编辑标题"
              >
                ✏️
              </button>
              <button
                @click.stop="deleteConversation(convo.id)"
                class="action-btn delete-btn"
                title="删除会话"
              >
                🗑️
              </button>
            </div>
          </div>
        </li>
      </ul>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, nextTick } from 'vue';
import { chatApi } from '../services/api.js';
import { ElMessage, ElMessageBox } from 'element-plus';

const props = defineProps({
  isOpen: {
    type: Boolean,
    default: true
  }
});

const emit = defineEmits(['selectConversation', 'toggle-sidebar', 'conversationDeleted']);

const toggle = () => {
  emit('toggle-sidebar');
};

const conversations = ref([]);
const selectedConversationId = ref(null);
const editingConversation = ref(null);
const editingTitle = ref('');
const titleInput = ref(null);

const fetchConversations = async () => {
  console.log('Fetching all conversations');
  try {
    const response = await chatApi.getAllConversations();
    return response;
  } catch (error) {
    console.error('Error fetching conversations:', error);
    return [];
  }
};

onMounted(async () => {
  conversations.value = await fetchConversations();
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
    const newConvo = await chatApi.createConversation({ title: newTitle });
    conversations.value.unshift(newConvo);
    selectConversation(newConvo.id);
  } catch (error) {
    console.error('Error creating new conversation:', error);
    ElMessage.error('创建新会话失败');
  }
};

const startEdit = (conversationId, currentTitle) => {
  editingConversation.value = conversationId;
  editingTitle.value = currentTitle;
  nextTick(() => {
    if (titleInput.value) {
      titleInput.value.focus();
      titleInput.value.select();
    }
  });
};

const saveTitle = async (conversationId) => {
  if (!editingTitle.value.trim()) {
    cancelEdit();
    return;
  }
  
  try {
    await chatApi.updateConversation(conversationId, { title: editingTitle.value.trim() });
    
    // 更新本地数据
    const convo = conversations.value.find(c => c.id === conversationId);
    if (convo) {
      convo.title = editingTitle.value.trim();
    }
    
    ElMessage.success('标题已更新');
  } catch (error) {
    console.error('Error updating conversation title:', error);
    ElMessage.error('更新标题失败');
  } finally {
    editingConversation.value = null;
    editingTitle.value = '';
  }
};

const cancelEdit = () => {
  editingConversation.value = null;
  editingTitle.value = '';
};

const deleteConversation = async (conversationId) => {
  try {
    await ElMessageBox.confirm(
      '确定要删除这个会话吗？此操作无法撤销。',
      '删除会话',
      {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        type: 'warning',
      }
    );

    await chatApi.deleteConversation(conversationId);
    
    // 从本地列表中移除
    conversations.value = conversations.value.filter(c => c.id !== conversationId);
    
    // 如果删除的是当前选中的会话
    if (selectedConversationId.value === conversationId) {
      // 选择第一个可用的会话，如果没有则创建新的
      if (conversations.value.length > 0) {
        selectConversation(conversations.value[0].id);
      } else {
        selectedConversationId.value = null;
        emit('conversationDeleted');
      }
    }
    
    ElMessage.success('会话已删除');
  } catch (error) {
    if (error !== 'cancel') {
      console.error('Error deleting conversation:', error);
      ElMessage.error('删除会话失败');
    }
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
  display: flex;
  flex-direction: column;
  height: calc(100vh - 120px); /* 减去header和padding的高度 */
  overflow: hidden;
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
  flex: 1;
  max-height: calc(100vh - 200px); /* 确保有足够的滚动空间 */
}

.conversation-history li {
  border-radius: 4px;
  margin-bottom: 0.25rem;
}

.conversation-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.75rem;
  position: relative;
}

.conversation-title {
  flex: 1;
  cursor: pointer;
  word-break: break-word;
  padding-right: 0.5rem;
}

.title-input {
  flex: 1;
  padding: 0.25rem;
  border: 1px solid #ddd;
  border-radius: 4px;
  font-size: inherit;
  background: white;
  margin-right: 0.5rem;
}

.conversation-actions {
  display: flex;
  gap: 0.25rem;
  opacity: 0;
  transition: opacity 0.2s;
}

.conversation-item:hover .conversation-actions {
  opacity: 1;
}

.action-btn {
  background: none;
  border: none;
  cursor: pointer;
  font-size: 0.8rem;
  padding: 0.25rem;
  border-radius: 3px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.action-btn:hover {
  background-color: rgba(0, 0, 0, 0.1);
}

.conversation-history li:hover {
  background-color: #e9e9e9;
}

.conversation-history li.active {
  background-color: #007bff;
  color: white;
}

.conversation-history li.active .action-btn:hover {
  background-color: rgba(255, 255, 255, 0.2);
}
</style>
