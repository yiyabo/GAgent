<template>
  <div class="chat-view">
    <div class="chat-container">
      <!-- 左侧聊天界面 -->
      <div class="chat-panel">
        <ConversationHistory 
          v-show="showHistory"
          :is-open="showHistory"
          :plan-id="planId" 
          @select-conversation="handleSelectConversation" 
          @toggle-sidebar="toggleHistory"
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
            >
              New Conversation
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
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import ConversationHistory from '../components/ConversationHistory.vue'
import ChatInterface from '../components/ChatInterface.vue'
import VisualizationPanel from '../components/VisualizationPanel.vue'
import TaskDetailModal from '../components/TaskDetailModal.vue'
import { chatApi } from '../services/api.js'
import api from '../services/api.js'

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
    const planId = ref(route.params.id || null)
    const selectedConversationId = ref(null)
    const showHistory = ref(false)
    const currentMessages = ref([])
    const isLoadingConversation = ref(false)
    const chatInterface = ref(null)
    const selectedTaskForDetail = ref(null)
    const showTaskDetailModal = ref(false)
    
    // 可视化相关
    const visualizationType = ref('none')
    const visualizationData = ref({})
    const visualizationConfig = ref({})
    
    const toggleHistory = () => {
      showHistory.value = !showHistory.value
    }
    
    const handleSelectConversation = async (conversationId) => {
      selectedConversationId.value = conversationId
      isLoadingConversation.value = true
      
      try {
        const conversation = await chatApi.getConversation(conversationId)
        currentMessages.value = conversation.messages || []
        
        // 显示欢迎消息
        visualizationType.value = 'help_menu'
        visualizationData.value = [
          { command: "Create Plan", description: "Create a new research plan" },
          { command: "Show Plans", description: "View all plan lists" },
          { command: "Execute Plan", description: "Execute tasks in specified plan" },
          { command: "Check Status", description: "View plan or task execution status" },
          { command: "Help", description: "Display help information" }
        ]
        
        // 如果有计划ID，在后台预加载任务数据，但不覆盖当前显示
        if (planId.value) {
          loadPlanTasks()
        }
      } catch (error) {
        console.error('Failed to load conversation:', error)
        currentMessages.value = []
      } finally {
        isLoadingConversation.value = false
      }
    }
    
    const createNewConversation = async () => {
      try {
        let currentPlanId = planId.value
        
        // 如果没有计划ID，先获取或创建一个
        if (!currentPlanId) {
          const plansResponse = await api.get('/plans')
          if (plansResponse.data.plans && plansResponse.data.plans.length > 0) {
            currentPlanId = plansResponse.data.plans[0].id
          } else {
            // 创建默认计划
            const planResponse = await api.post('/plans/propose', {
              goal: '默认研究计划',
              title: '默认计划'
            })
            currentPlanId = planResponse.data.id
          }
          planId.value = currentPlanId
        }
        
        // 创建新会话
        const response = await chatApi.createConversation(currentPlanId, {
          title: `会话 ${new Date().toLocaleString()}`
        })
        
        selectedConversationId.value = response.id
        currentMessages.value = []
        
        // 重置可视化
        visualizationType.value = 'none'
        visualizationData.value = {}
        visualizationConfig.value = {}
        
      } catch (error) {
        console.error('Failed to create conversation:', error)
        ElMessage.error('创建会话失败')
      }
    }
    
    const handleSendMessage = async (messageText) => {
      if (!selectedConversationId.value) return
      
      try {
        // 发送消息并获取响应（包含可视化指令）
        const response = await chatApi.sendMessage(selectedConversationId.value, messageText)
        
        // 更新消息列表
        if (response.message) {
          currentMessages.value.push(response.message)
        }
        
        // 更新可视化
        if (response.visualization) {
          updateVisualization(response.visualization)
        }
        
        // 处理需要执行的动作
        handleActionResult(response)
        
      } catch (error) {
        console.error('Failed to send message:', error)
        currentMessages.value.push({ 
          sender: 'agent', 
          text: '抱歉，发送消息时出现错误。' 
        })
      }
    }
    
    const handleSendMessageStream = async (messageText, callbacks) => {
      if (!selectedConversationId.value) return
      
      try {
        await chatApi.sendMessageStream(
          selectedConversationId.value,
          messageText,
          (chunk) => {
            callbacks.onChunk(chunk)
          },
          (complete) => {
            callbacks.onComplete(complete)
            // 流式响应完成后，也可能需要更新可视化
            if (complete.visualization) {
              updateVisualization(complete.visualization)
            }
          },
          callbacks.onError
        )
      } catch (error) {
        console.error('Failed to send message:', error)
        callbacks.onError('Failed to send message. Please try again.')
      }
    }
    
    const updateVisualization = async (visualization) => {
      visualizationType.value = visualization.type || 'none'
      visualizationData.value = visualization.data || {}
      visualizationConfig.value = visualization.config || {}
      
      // 如果是任务树视图，确保获取完整的任务数据
      if ((visualization.type === 'task_tree' || visualization.type === 'task_list') && planId.value) {
        // 如果已经有缓存的任务数据，直接使用
        if (window.currentPlanTasks) {
          visualizationData.value = window.currentPlanTasks
        } else {
          await loadPlanTasks()
        }
      }
    }
    
    const loadPlanTasks = async () => {
      if (!planId.value) return
      
      try {
        const response = await api.get(`/plans/${planId.value}/tasks`)
        // 格式化任务数据，添加shortName字段
        const formattedTasks = response.data.map(task => ({
          ...task,
          shortName: task.name.replace(`[计划]`, '').trim() || task.name,
        }))
        
        // 只有在当前显示任务树时才更新数据，避免覆盖帮助菜单
        if (visualizationType.value === 'task_tree' || visualizationType.value === 'task_list') {
          visualizationData.value = formattedTasks
        }
        
        // 将任务数据存储到单独的变量中，以便后续使用
        window.currentPlanTasks = formattedTasks
        
        visualizationConfig.value = { ...visualizationConfig.value, loading: false }
      } catch (error) {
        console.error('Failed to load plan tasks:', error)
        visualizationConfig.value = { ...visualizationConfig.value, loading: false, error: '加载任务失败' }
      }
    }

    const handleVisualizationAction = (action) => {
      // 处理不同类型的可视化动作
      if (action.type === 'select_task') {
        // 处理任务选择事件，显示任务详情
        showTaskDetail(action.task)
      } else if (action.type === 'refresh_tasks') {
        // 处理刷新任务事件
        loadPlanTasks()
      } else if (chatInterface.value && action.command) {
        // 将其他动作转换为聊天命令
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
    
    const handleTaskRerun = async (taskId) => {
      // Handle task rerun logic if needed
      console.log('Rerun task:', taskId)
      closeTaskDetailModal()
    }
    
    const handleTaskDeleted = () => {
      // Handle task deletion if needed
      console.log('Task deleted')
      closeTaskDetailModal()
    }
    
    const handleActionResult = (response) => {
      // 处理需要执行的后续动作
      if (response && response.action_result && response.action_result.should_execute) {
        if (response.intent === 'execute_plan') {
          const execPlanId = response.action_result.plan_id
          if (execPlanId) {
            executePlan(execPlanId)
          }
        }
      }
    }
    
    const executePlan = async (execPlanId) => {
      try {
        await api.post('/run', {
          plan_id: execPlanId,
          use_context: true,
          schedule: 'postorder'
        })
        
        ElMessage.success('计划开始执行')
        
        // 切换到执行进度视图
        visualizationType.value = 'execution_progress'
        visualizationConfig.value = {
          plan_id: execPlanId,
          autoRefresh: true,
          refreshInterval: 2000
        }
        
        // 开始刷新任务状态
        startTaskRefresh(execPlanId)
        
      } catch (error) {
        console.error('Failed to execute plan:', error)
        ElMessage.error('执行计划失败')
      }
    }
    
    const startTaskRefresh = async (execPlanId) => {
      const interval = setInterval(async () => {
        try {
          const response = await api.get(`/plans/${execPlanId}/tasks`)
          visualizationData.value = response.data
          
          // 检查是否所有任务完成
          const allDone = response.data.every(t => 
            ['done', 'complete', 'failed'].includes(t.status)
          )
          
          if (allDone) {
            clearInterval(interval)
            ElMessage.success('所有任务执行完成')
          }
        } catch (error) {
          clearInterval(interval)
        }
      }, 2000)
    }
    
    // 直接显示任务树的方法
    const showTaskTree = async () => {
      if (planId.value) {
        visualizationConfig.value = { loading: true }
        visualizationType.value = 'task_tree'
        await loadPlanTasks()
      }
    }
    
    onMounted(async () => {
      // 初始化时尝试加载第一个会话
      if (!selectedConversationId.value && planId.value) {
        try {
          const conversations = await chatApi.getConversationsForPlan(planId.value)
          if (conversations && conversations.length > 0) {
            handleSelectConversation(conversations[0].id)
          } else {
            // 如果没有会话，创建一个
            await createNewConversation()
          }
        } catch (error) {
          console.error('Failed to load initial conversation:', error)
        }
      }
      
      // 如果有planId但没有任务数据，预加载任务数据
      if (planId.value && (!visualizationData.value || Object.keys(visualizationData.value).length === 0)) {
        await loadPlanTasks()
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
      toggleHistory,
      handleSelectConversation,
      createNewConversation,
      handleSendMessage,
      handleSendMessageStream,
      updateVisualization,
      handleVisualizationAction,
      closeTaskDetailModal,
      handleTaskRerun,
      handleTaskDeleted
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
  overflow: hidden;
  background: #f5f7fa;
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