<template>
  <div class="chat-view">
    <div class="chat-container">
      <!-- 左侧聊天界面 -->
      <div class="chat-panel">
        <ConversationHistory 
          v-show="showHistory"
          :is-open="showHistory"
          @select-conversation="handleSelectConversation" 
          @toggle-sidebar="toggleHistory"
          @conversation-deleted="handleConversationDeleted"
        />
        
        <div class="chat-main" :class="{ 'full-width': !showHistory }">
          <div class="chat-header">
            <el-button 
              circle
              size="small"
              @click="toggleHistory"
            >
              <i :class="showHistory ? 'el-icon-arrow-left' : 'el-icon-arrow-right'"></i>
            </el-button>
            <h3>Ghat</h3>
            <el-button 
              size="small" 
              @click="createNewConversation"
              :loading="isCreatingConversation"
              type="primary"
            >
              {{ isCreatingConversation ? 'Creating...' : 'New Conversation' }}
            </el-button>
          </div>
          
          <div v-if="isLoadingConversation" class="loading-chat">
            <i class="el-icon-loading"></i> 加载中...
          </div>
          
          <ChatInterface 
            v-else-if="selectedConversationId" 
            ref="chatInterface"
            :key="selectedConversationId" 
            :initial-messages="currentMessages"
            @send-message="handleSendMessage"
            @send-message-stream="handleSendMessage"
          />
          
          <div v-else class="no-conversation-selected">
            <el-empty description="请选择或创建一个会话">
              <el-button type="primary" @click="createNewConversation">
                创建新会话
              </el-button>
            </el-empty>
          </div>
        </div>
      </div>
      
      <!-- 右侧可视化面板 -->
      <div class="visualization-panel">
        <VisualizationPanel
          :type="visualizationType"
          :data="visualizationData"
          :config="visualizationConfig"
          @action="handleVisualizationAction"
        />
      </div>
    </div>
    
    <!-- Task Detail Modal -->
    <TaskDetailModal 
      :show="showTaskDetailModal"
      :task="selectedTaskForDetail"
      @close="closeTaskDetailModal"
      @task-rerun="handleTaskRerun"
      @task-deleted="handleTaskDeleted"
    />
  </div>
</template>

<script>
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import ConversationHistory from '../components/ConversationHistory.vue'
import ChatInterface from '../components/ChatInterface.vue'
import VisualizationPanel from '../components/VisualizationPanel.vue'
import TaskDetailModal from '../components/TaskDetailModal.vue'
import { chatApi } from '../services/api.js'
import { usePlansStore } from '../stores/plans'

export default {
  name: 'ChatView',
  components: {
    ConversationHistory,
    ChatInterface,
    VisualizationPanel,
    TaskDetailModal
  },
  setup() {
    const route = useRoute()
    const router = useRouter()
    const plansStore = usePlansStore()
    
    const planId = ref(route.params.id || null)
    const selectedConversationId = ref(null)
    const showHistory = ref(true)
    const isLoadingConversation = ref(false)
    const chatInterface = ref(null)
    const selectedTaskForDetail = ref(null)
    const showTaskDetailModal = ref(false)
    const isCreatingConversation = ref(false)
    
    const visualizationType = ref('plan_list')
    const localVisualizationData = ref([]);
    const visualizationConfig = ref({})

    const currentMessages = computed(() => plansStore.currentChatHistory);

    const visualizationData = computed(() => {
      if (visualizationType.value === 'task_tree') {
        return plansStore.currentPlanTasks || [];
      }
      return localVisualizationData.value || {};
    });
    
    const toggleHistory = () => {
      showHistory.value = !showHistory.value
    }
    
    const handleSelectConversation = async (conversationId) => {
      if (!conversationId) return;
      selectedConversationId.value = conversationId
      isLoadingConversation.value = true
      await plansStore.loadConversationHistory(conversationId);
      isLoadingConversation.value = false
    }
    
    const createNewConversation = async () => {
      if (isCreatingConversation.value) return
      isCreatingConversation.value = true
      try {
        const response = await chatApi.createConversation({ title: `New Conversation` })
        await handleSelectConversation(response.id);
      } catch (error) {
        ElMessage.error(`Failed to create conversation: ${error.message}`)
      } finally {
        isCreatingConversation.value = false
      }
    }
    
    const handleSendMessage = async (messageText) => {
      if (!selectedConversationId.value || !messageText.trim()) return;

      // Use the store action to add the user message
      plansStore.addUserMessageToHistory(messageText);

      const agentMessagePlaceholder = { 
        sender: 'agent', 
        text: 'Processing...', 
        isStreaming: true 
      };
      // Manually add placeholder for agent response
      plansStore.currentChatHistory.push(agentMessagePlaceholder);

      try {
        // We need a non-streaming API endpoint for sending the initial command
        // Let's assume chatApi.sendMessage exists and returns the agent's initial response
        const response = await chatApi.sendMessage(
          selectedConversationId.value,
          messageText,
          planId.value
        );

        // Update the placeholder with the actual response
        const lastMessageIndex = plansStore.currentChatHistory.length - 1;
        plansStore.currentChatHistory[lastMessageIndex] = { ...response.message, isStreaming: false };
        
        visualizationType.value = response.visualization.type;
        localVisualizationData.value = response.visualization.data;
        visualizationConfig.value = response.visualization.config;

        // Handle stream action for plan creation
        if (response.action_result && response.action_result.action === 'stream') {
          visualizationType.value = 'task_tree';
          plansStore.currentPlanTasks = []; // Clear previous tasks
          
          const { goal } = response.action_result.stream_payload;
          
          await chatApi.proposePlanStream(
            goal,
            (task) => { // onData callback
              if (task && task.id) {
                plansStore.upsertTaskInPlan(task);
              }
            },
            () => { // onComplete callback
              ElMessage.success('Plan generation complete!');
            },
            (error) => { // onError callback
              ElMessage.error(`Plan generation failed: ${error.message}`);
            }
          );
        }
      } catch (error) {
        ElMessage.error(`Failed to send message: ${error.message}`);
        const lastMessageIndex = plansStore.currentChatHistory.length - 1;
        plansStore.currentChatHistory[lastMessageIndex] = { 
          sender: 'agent', 
          text: `Error: ${error.message}`, 
          isStreaming: false 
        };
      }
    };

    const handleVisualizationAction = async (action) => {
      if (action.type === 'select_plan') {
        planId.value = action.planId;
        await plansStore.loadPlanDetails(action.planId);
        visualizationConfig.value = { title: plansStore.currentPlan?.title || 'Plan Details' };
        visualizationType.value = 'task_tree';
      } else if (action.type === 'select_task') {
        selectedTaskForDetail.value = action.task;
        showTaskDetailModal.value = true;
      } else if (action.type === 'delete_plan') {
        try {
          await chatApi.deletePlan(action.planId);
          ElMessage.success('Plan deleted successfully.');
          const plans = await chatApi.getAllPlans();
          localVisualizationData.value = plans;
        } catch (error) {
          ElMessage.error(`Failed to delete plan: ${error.message}`);
        }
      } else if (action.type === 'show_plan_list') {
        visualizationType.value = 'plan_list';
      } else if (chatInterface.value && action.command) {
        handleSendMessage(action.command);
      }
    };
    
    const closeTaskDetailModal = () => {
      showTaskDetailModal.value = false;
      selectedTaskForDetail.value = null;
    }

    const handleConversationDeleted = () => {
      selectedConversationId.value = null;
      plansStore.clearChatHistory();
      visualizationType.value = 'help_menu';
    };

    onMounted(async () => {
      const plans = await chatApi.getAllPlans();
      localVisualizationData.value = plans;
    });
    
    return {
      selectedConversationId,
      showHistory,
      currentMessages,
      isLoadingConversation,
      chatInterface,
      visualizationType,
      visualizationData,
      visualizationConfig,
      selectedTaskForDetail,
      showTaskDetailModal,
      isCreatingConversation,
      toggleHistory,
      handleSelectConversation,
      createNewConversation,
      handleSendMessage,
      handleVisualizationAction,
      closeTaskDetailModal,
      handleConversationDeleted,
      handleTaskRerun: (taskId) => plansStore.rerunTask(taskId),
      handleTaskDeleted: () => {
        if (plansStore.currentPlan) {
          plansStore.loadPlanDetails(plansStore.currentPlan.id)
        }
      }
    }
  }
}
</script>

<style scoped>
.chat-view {
  height: calc(100vh - 60px);
  width: 100%;
}

.chat-container {
  height: 100%;
  display: flex;
  gap: 0;
}

.chat-panel {
  flex: 1;
  display: flex;
  background: white;
  border-right: 1px solid #e4e7ed;
  position: relative;
}

.chat-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  transition: margin-left 0.3s;
}

.chat-main.full-width {
  margin-left: 0;
}

.chat-header {
  padding: 15px 20px;
  border-bottom: 1px solid #e4e7ed;
  display: flex;
  align-items: center;
  gap: 15px;
  background: white;
}

.chat-header h3 {
  flex: 1;
  margin: 0;
  color: #303133;
}

.loading-chat {
  display: flex;
  justify-content: center;
  align-items: center;
  height: 100%;
  color: #909399;
  font-size: 16px;
}

.no-conversation-selected {
  display: flex;
  justify-content: center;
  align-items: center;
  height: 100%;
}

.visualization-panel {
  flex: 1.2;
  min-width: 500px;
  height: 100%;
  overflow-y: auto;
  background: #f5f7fa;
}

.visualization-panel::-webkit-scrollbar {
  width: 8px;
}

.visualization-panel::-webkit-scrollbar-track {
  background: #e4e7ed;
  border-radius: 4px;
}

.visualization-panel::-webkit-scrollbar-thumb {
  background: #909399;
  border-radius: 4px;
}

.visualization-panel::-webkit-scrollbar-thumb:hover {
  background: #606266;
}

/* 响应式布局 */
@media (max-width: 1200px) {
  .chat-container {
    flex-direction: column;
  }
  
  .chat-panel {
    height: 50%;
    border-right: none;
    border-bottom: 1px solid #e4e7ed;
  }
  
  .visualization-panel {
    height: 50%;
    min-width: auto;
  }
}
</style>
