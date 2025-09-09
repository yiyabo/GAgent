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
            :use-streaming="false"
            @send-message="handleSendMessage"
            @send-message-stream="handleSendMessageStream"
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
import { ref, computed, onMounted, watch, nextTick } from 'vue'
import { useRoute } from 'vue-router'
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
    const plansStore = usePlansStore()
    
    const planId = ref(route.params.id || null)
    const selectedConversationId = ref(null)
    const showHistory = ref(false)
    const currentMessages = ref([])
    const isLoadingConversation = ref(false)
    const chatInterface = ref(null)
    const selectedTaskForDetail = ref(null)
    const showTaskDetailModal = ref(false)
    const isCreatingConversation = ref(false)
    
    const visualizationType = ref('none')
    const localVisualizationData = ref({});
    const visualizationConfig = ref({})

    const visualizationData = computed(() => {
      // Include force update trigger in dependency
      plansStore.forceUpdateTrigger;
      
      if (visualizationType.value === 'task_tree' || visualizationType.value === 'task_list') {
        // Ensure reactive dependency on store state
        return plansStore.currentPlanTasks || [];
      }
      return localVisualizationData.value || {};
    });
    
    const toggleHistory = () => {
      showHistory.value = !showHistory.value
    }
    
    const handleSelectConversation = async (conversationId) => {
      selectedConversationId.value = conversationId
      isLoadingConversation.value = true
      try {
        const conversation = await chatApi.getConversation(conversationId)
        currentMessages.value = conversation.messages || []
        visualizationType.value = 'help_menu'
        localVisualizationData.value = [
          { command: "Create Plan", description: "Create a new research plan" },
          { command: "Show Plans", description: "View all plan lists" },
        ]
      } catch (error) {
        console.error('Failed to load conversation:', error)
        currentMessages.value = []
      } finally {
        isLoadingConversation.value = false
      }
    }
    
    const createNewConversation = async () => {
      if (isCreatingConversation.value) return
      isCreatingConversation.value = true
      try {
        const response = await chatApi.createConversation({
          title: `Conversation ${new Date().toLocaleString()}`
        })
        selectedConversationId.value = response.id
        currentMessages.value = []
        visualizationType.value = 'help_menu'
        localVisualizationData.value = [
           { command: "Create Plan", description: "Create a new research plan" },
           { command: "Show Plans", description: "View all plan lists" },
        ]
        visualizationConfig.value = {}
        ElMessage.success('New conversation created')
      } catch (error) {
        console.error('Failed to create conversation:', error)
        ElMessage.error(`Failed to create conversation: ${error.message || error}`)
      } finally {
        isCreatingConversation.value = false
      }
    }
    
    const handleSendMessage = async (messageText) => {
      if (!selectedConversationId.value) return
      
      currentMessages.value.push({
        sender: 'user',
        text: messageText,
        timestamp: new Date().toISOString()
      })
      
      try {
        const response = await chatApi.sendMessage(
          selectedConversationId.value, 
          messageText,
          (event) => { // onPlanStreamEvent
            plansStore.handlePlanStreamEvent(event);
            if (visualizationType.value !== 'task_tree') {
                visualizationType.value = 'task_tree';
            }
            // Force reactivity update
            plansStore.forceUpdate();
            nextTick(() => {
              // Trigger computed property re-evaluation
            });
          },
          (error) => { // onPlanStreamError
            console.error("Plan stream error:", error);
            ElMessage.error("Error during plan generation stream.");
            plansStore.handlePlanStreamEvent({ stage: 'fatal_error', message: 'Streaming failed' });
          }
        );
        
        if (response.initial_response) {
          currentMessages.value.push({
            sender: 'agent',
            text: response.initial_response,
            timestamp: new Date().toISOString(),
          });
        }
        
        handleActionResult(response);
        
        if (response.visualization) {
          updateVisualization(response.visualization);
        }
        
      } catch (error) {
        console.error('Failed to send message:', error)
        currentMessages.value.push({ 
          sender: 'agent', 
          text: 'Sorry, an error occurred while processing your message.',
          timestamp: new Date().toISOString()
        })
      }
    }
    
    const handleSendMessageStream = async (messageText, callbacks) => {
      if (!selectedConversationId.value) return
      
      currentMessages.value.push({
        sender: 'user',
        text: messageText,
        timestamp: new Date().toISOString()
      })
      
      try {
        await chatApi.sendMessageStream(
          selectedConversationId.value,
          messageText,
          callbacks.onChunk,
          (complete) => {
            callbacks.onComplete(complete)
            if (complete.visualization) {
              updateVisualization(complete.visualization)
            }
            if (complete.full_text) {
              currentMessages.value.push({
                sender: 'agent',
                text: complete.full_text,
                timestamp: new Date().toISOString()
              })
            }
          },
          callbacks.onError
        )
      } catch (error) {
        console.error('Failed to send message stream:', error)
        callbacks.onError('Failed to send message. Please try again.')
      }
    }

    const updateVisualization = (visualization) => {
      visualizationType.value = visualization.type || 'none'
      visualizationConfig.value = visualization.config || {}
      
      if (visualization.type !== 'task_tree' && visualization.type !== 'task_list') {
        localVisualizationData.value = visualization.data || {};
      }
    }
    
    const handleVisualizationAction = (action) => {
      if (action.type === 'select_task') {
        showTaskDetail(action.task)
      } else if (action.type === 'refresh_tasks' && plansStore.currentPlan) {
        plansStore.loadPlanDetails(plansStore.currentPlan.id);
      } else if (chatInterface.value && action.command) {
        chatInterface.value.sendMessage(action.command)
      }
    }
    
    const showTaskDetail = (task) => {
      selectedTaskForDetail.value = task
      showTaskDetailModal.value = true
    }
    
    const closeTaskDetailModal = () => {
      selectedTaskForDetail.value = null
      showTaskDetailModal.value = false
    }
    
    const handleTaskRerun = (taskId) => {
      plansStore.rerunTask(taskId);
      closeTaskDetailModal()
    }
    
    const handleTaskDeleted = () => {
      if (plansStore.currentPlan) {
        plansStore.loadPlanDetails(plansStore.currentPlan.id);
      }
      closeTaskDetailModal()
    }
    
    const handleConversationDeleted = () => {
      selectedConversationId.value = null
      currentMessages.value = []
      visualizationType.value = 'help_menu'
    }
    
    const handleActionResult = (response) => {
      if (response && response.action_result) {
        const newPlanId = response.action_result.plan_id;
        if (newPlanId && newPlanId !== planId.value) {
          planId.value = newPlanId;
          plansStore.loadPlanDetails(newPlanId);
        }
      }
    }
    
    // Watch for store state changes to ensure reactivity
    watch(
      () => plansStore.currentPlanTasks,
      (newTasks) => {
        if (visualizationType.value === 'task_tree' || visualizationType.value === 'task_list') {
          // Force re-render when tasks change
          nextTick(() => {
            // Ensure UI updates
          });
        }
      },
      { deep: true }
    );

    watch(
      () => plansStore.currentPlan,
      (newPlan) => {
        if (newPlan) {
          // Force re-render when plan changes
          nextTick(() => {
            // Ensure UI updates
          });
        }
      },
      { deep: true }
    );

    onMounted(async () => {
      if (planId.value) {
        try {
          await plansStore.loadPlanDetails(planId.value)
          // Force reactivity update after loading
          nextTick(() => {
            // Ensure UI updates after async load
          });
        } catch (error) {
          console.error('Failed to load plan details:', error)
        }
      }
      
      if (!selectedConversationId.value) {
        try {
          const conversations = await chatApi.getAllConversations()
          if (conversations && conversations.length > 0) {
            handleSelectConversation(conversations[0].id)
          } else {
            await createNewConversation()
          }
        } catch (error) {
          console.error('Failed to load initial conversation:', error)
        }
      }
    })
    
    return {
      planId,
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
      handleSendMessageStream,
      updateVisualization,
      handleVisualizationAction,
      closeTaskDetailModal,
      handleTaskRerun,
      handleTaskDeleted,
      handleConversationDeleted
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