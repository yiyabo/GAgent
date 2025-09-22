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
            <el-button
              v-if="plansStore.planGenerating"
              size="small"
              type="danger"
              @click="cancelPlanGeneration"
            >
              停止生成
            </el-button>
            <el-button
              size="small"
              type="success"
              :disabled="!activePlanId || plansStore.planGenerating || isAgentReplying"
              @click="syncCurrentPlanGraph"
            >
              Synchronization Task Graph
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
            :confirmation="confirmationRequest"
            :is-streaming="isAgentReplying"
            @send-message="handleSendMessage"
            @send-message-stream="handleSendMessage"
            @confirmation-response="handleConfirmationResponse"
            @edit-message="handleEditMessage"
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

    <el-dialog
      v-model="isEditDialogVisible"
      title="编辑消息"
      width="520px"
      :close-on-click-modal="!isResendingMessage"
      :show-close="!isResendingMessage"
      @close="closeEditDialog"
    >
      <el-input
        type="textarea"
        :rows="5"
        v-model="editedMessageText"
        :disabled="isResendingMessage"
      />
      <template #footer>
        <el-button @click="closeEditDialog" :disabled="isResendingMessage">取消</el-button>
        <el-button
          type="primary"
          @click="submitEditedMessage"
          :loading="isResendingMessage"
          :disabled="!editedMessageText || !editedMessageText.trim()"
        >
          保存并重发
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script>
import { ref, computed, onMounted, watch } from 'vue'
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
    const confirmationRequest = ref(null)
    const isAgentReplying = ref(false)
    
    const visualizationType = ref('plan_list')
    const localVisualizationData = ref([]);
    const visualizationConfig = ref({})
    const isEditDialogVisible = ref(false)
    const editingMessage = ref(null)
    const editedMessageText = ref('')
    const isResendingMessage = ref(false)

    const currentMessages = computed(() => plansStore.currentChatHistory)

    const visualizationData = computed(() => {
      if (visualizationType.value === 'task_tree') {
        return plansStore.currentPlanTasks || [];
      }
      return localVisualizationData.value || {};
    });

    const activePlanId = computed(() => {
      if (planId.value) {
        return Number(planId.value);
      }
      return plansStore.currentPlan?.id || null;
    });

    watch(selectedConversationId, (conversationId) => {
      confirmationRequest.value = null
      isEditDialogVisible.value = false
      editingMessage.value = null
      editedMessageText.value = ''
    })

    const buildInstructionItems = (steps = []) => {
      if (!Array.isArray(steps)) return []
      return steps
        .filter(step => step && step.instruction)
        .map((step, index) => {
          const intent = step.instruction.intent || 'unknown'
          const params = step.instruction.parameters || {}
          const paramEntries = Object.entries(params)
            .filter(([_, value]) => value !== undefined && value !== null && value !== '')
            .map(([key, value]) => {
              if (Array.isArray(value)) {
                return `${key}: ${value.join(', ')}`
              }
              if (typeof value === 'object') {
                try {
                  return `${key}: ${JSON.stringify(value)}`
                } catch (err) {
                  return `${key}: [object]`
                }
              }
              return `${key}: ${value}`
            })
          const description = paramEntries.length
            ? `${intent} — ${paramEntries.join('; ')}`
            : intent
          return {
            id: `${index}-${intent}`,
            intent,
            description,
            needsTool: Boolean(step.needs_tool),
            raw: step.instruction,
          }
        })
    }
    
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
    
    const applyVisualizationResponse = (response) => {
      if (!response || !response.visualization) {
        return;
      }

      const { type, data, config } = response.visualization;

      if (type === 'task_tree') {
        const updatedTasks = Array.isArray(response.action_result?.tasks)
          ? response.action_result.tasks
          : Array.isArray(data)
            ? data
            : [];
        if (Array.isArray(updatedTasks)) {
          plansStore.currentPlanTasks = [...updatedTasks];
        }
      } else {
        localVisualizationData.value = data;
      }

      visualizationType.value = type;
      visualizationConfig.value = config;

      if (type !== 'task_tree' && Array.isArray(data)) {
        localVisualizationData.value = data;
      }
    };

    const handleSendMessage = async (messageText, confirmed = false, addToHistory = true) => {
      if (!selectedConversationId.value || !messageText.trim()) return;

      isAgentReplying.value = true
      if (!confirmed) {
        confirmationRequest.value = null
      }

      if (addToHistory) {
        plansStore.addUserMessageToHistory(messageText)
      }

      const agentMessagePlaceholder = { 
        sender: 'agent', 
        text: 'Processing...', 
        isStreaming: true 
      }
      plansStore.currentChatHistory.push(agentMessagePlaceholder)

      let response
      try {
        response = await chatApi.sendMessage(
          selectedConversationId.value,
          messageText,
          planId.value,
          confirmed
        )

        const lastMessageIndex = plansStore.currentChatHistory.length - 1
        const instructionItems = buildInstructionItems(response.steps)
        const enrichedMessage = {
          ...response.message,
          isStreaming: false,
          instructions: instructionItems
        }
        plansStore.currentChatHistory[lastMessageIndex] = enrichedMessage
        
        const awaitingConfirmation = response.intent === 'confirmation_required' || response.action_result?.requires_confirmation

        if (!awaitingConfirmation) {
          applyVisualizationResponse(response)
        }

        if (awaitingConfirmation) {
          confirmationRequest.value = {
            originalMessage: messageText,
            intent: response.intent,
            data: response,
            instructions: instructionItems
          }
        } else {
          confirmationRequest.value = null
        }

        if (!awaitingConfirmation && response.action_result && response.action_result.action === 'stream') {
          plansStore.currentPlanTasks = [] // Clear previous tasks
          
          const { goal } = response.action_result.stream_payload;
          let isFirstTask = true;

          plansStore.startPlanStream(goal, {
            onData: (task) => {
              if (isFirstTask) {
                visualizationType.value = 'task_tree';
                isFirstTask = false;
              }
              if (task && task.id) {
                plansStore.upsertTaskInPlan(task);
              }
            },
            onComplete: (status) => {
              if (status === 'completed') {
                ElMessage.success('Plan generation complete!')
              } else if (status === 'cancelled') {
                ElMessage.info('Plan generation cancelled.')
              }
            },
            onError: (error) => {
              ElMessage.error(`Plan generation failed: ${error.message}`)
            }
          })
        }
      } catch (error) {
        ElMessage.error(`Failed to send message: ${error.message}`)
        const lastMessageIndex = plansStore.currentChatHistory.length - 1
        plansStore.currentChatHistory[lastMessageIndex] = { 
          sender: 'agent', 
          text: `Error: ${error.message}`, 
          isStreaming: false 
        }
      } finally {
        isAgentReplying.value = false
        // Ensure streaming is always turned off for non-streaming responses
        if (response && (!response.action_result || response.action_result.action !== 'stream')) {
            const lastMessageIndex = plansStore.currentChatHistory.length - 1
            if (plansStore.currentChatHistory[lastMessageIndex]) {
                plansStore.currentChatHistory[lastMessageIndex].isStreaming = false
            }
        }
      }
    }

    const handleConfirmationResponse = () => {
      if (!confirmationRequest.value) return

      const originalMessage = confirmationRequest.value.originalMessage
      confirmationRequest.value = null

      handleSendMessage(originalMessage, true, false)
    }

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
      selectedConversationId.value = null
      plansStore.clearChatHistory()
      visualizationType.value = 'help_menu'
    };

    const cancelPlanGeneration = async () => {
      await plansStore.cancelPlanStream();
      ElMessage.info('已取消计划生成');
    }

    const handleEditMessage = (message) => {
      if (!message || !message.id) return
      editingMessage.value = { ...message }
      editedMessageText.value = message.text || ''
      isEditDialogVisible.value = true
    }

    const closeEditDialog = () => {
      isEditDialogVisible.value = false
      editingMessage.value = null
      editedMessageText.value = ''
    }

    const submitEditedMessage = async () => {
      if (!editingMessage.value || !selectedConversationId.value) return
      isResendingMessage.value = true
      isAgentReplying.value = true

      const index = plansStore.currentChatHistory.findIndex(msg => msg.id === editingMessage.value.id)
      if (index !== -1) {
        plansStore.currentChatHistory[index] = {
          ...plansStore.currentChatHistory[index],
          text: editedMessageText.value
        }
      }

      try {
        const response = await chatApi.resendMessage(editingMessage.value.id, {
          text: editedMessageText.value,
          plan_id: planId.value,
          confirmed: false
        })

        await plansStore.loadConversationHistory(selectedConversationId.value)

        const instructionItems = buildInstructionItems(response.steps)
        if (plansStore.currentChatHistory.length > 0) {
          const lastIdx = plansStore.currentChatHistory.length - 1
          plansStore.currentChatHistory[lastIdx] = {
            ...plansStore.currentChatHistory[lastIdx],
            isStreaming: false,
            instructions: instructionItems
          }
        }

        const awaitingConfirmation = response.intent === 'confirmation_required' || response.action_result?.requires_confirmation

        if (!awaitingConfirmation) {
          applyVisualizationResponse(response)
        }

        if (awaitingConfirmation) {
          confirmationRequest.value = {
            originalMessage: editedMessageText.value,
            intent: response.intent,
            data: response,
            instructions: instructionItems
          }
        } else {
          confirmationRequest.value = null
        }

        ElMessage.success('消息已更新并重新发送')
        isEditDialogVisible.value = false
        editingMessage.value = null
        editedMessageText.value = ''
      } catch (error) {
        const detail = error?.response?.data?.detail || error.message || '未知错误'
        ElMessage.error(`重新发送失败：${detail}`)
      } finally {
        isResendingMessage.value = false
        isAgentReplying.value = false
      }
    }

    const syncCurrentPlanGraph = async () => {
      const targetPlanId = activePlanId.value
      if (!targetPlanId) {
        ElMessage.warning('请先选择一个计划');
        return;
      }

      try {
        const result = await chatApi.syncPlanGraph(targetPlanId)
        if (Array.isArray(result.tasks)) {
          plansStore.currentPlanTasks = result.tasks
        }
        if (Array.isArray(result.task_tree)) {
          visualizationType.value = 'task_tree'
          visualizationConfig.value = {
            ...(visualizationConfig.value || {}),
            title: plansStore.currentPlan?.title || `Plan ${targetPlanId}`
          }
          localVisualizationData.value = result.task_tree
        }
        await plansStore.loadPlanDetails(targetPlanId)
        ElMessage.success('任务图已同步到数据库');
      } catch (error) {
        const detail = error?.response?.data?.detail || error.message || '未知错误'
        ElMessage.error(`同步失败：${detail}`)
      }
    }

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
      plansStore,
      visualizationType,
      visualizationData,
      visualizationConfig,
      selectedTaskForDetail,
      showTaskDetailModal,
      isCreatingConversation,
      confirmationRequest,
      isAgentReplying,
      toggleHistory,
      handleSelectConversation,
      createNewConversation,
      handleSendMessage,
      handleConfirmationResponse,
      handleVisualizationAction,
      closeTaskDetailModal,
      handleConversationDeleted,
      cancelPlanGeneration,
      syncCurrentPlanGraph,
      handleEditMessage,
      closeEditDialog,
      submitEditedMessage,
      isEditDialogVisible,
      editedMessageText,
      isResendingMessage,
      activePlanId,
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
