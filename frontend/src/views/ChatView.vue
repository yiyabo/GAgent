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
    console.log('🏁 ChatView initialized with planId from route:', planId.value)
    const selectedConversationId = ref(null)
    const showHistory = ref(false)
    const currentMessages = ref([])
    const isLoadingConversation = ref(false)
    const chatInterface = ref(null)
    const selectedTaskForDetail = ref(null)
    const showTaskDetailModal = ref(false)
    const isCreatingConversation = ref(false)
    
    // 可视化相关
    const visualizationType = ref('none')
    const visualizationData = ref({})
    const visualizationConfig = ref({})
    
    // 稳定的任务数据存储（类似 PlanDetailView）
    const stableTasksData = ref([])
    const lastTasksUpdateTime = ref(0)
    
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
      console.log('🔵 createNewConversation called!')
      if (isCreatingConversation.value) {
        console.log('Already creating conversation, skipping...')
        return
      }
      
      isCreatingConversation.value = true
      try {
        console.log('Creating new conversation...')
        
        // 创建新会话（不需要plan关联）
        const response = await chatApi.createConversation({
          title: `Conversation ${new Date().toLocaleString()}`
        })
        console.log('Conversation created:', response)
        
        selectedConversationId.value = response.id
        currentMessages.value = []
        
        // 显示帮助菜单
        visualizationType.value = 'help_menu'
        visualizationData.value = [
          { command: "Create Plan", description: "Create a new research plan" },
          { command: "Show Plans", description: "View all plan lists" },
          { command: "Execute Plan", description: "Execute tasks in specified plan" },
          { command: "Check Status", description: "View plan or task execution status" },
          { command: "Help", description: "Display help information" }
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
      
      try {
        // 先添加用户消息到消息列表
        const userMessage = {
          sender: 'user',
          text: messageText,
          timestamp: new Date().toISOString()
        }
        currentMessages.value.push(userMessage)
        
        // 发送消息并获取响应（包含可视化指令）
        const response = await chatApi.sendMessage(selectedConversationId.value, messageText)
        
        // 处理两阶段响应
        if (response.initial_response) {
          // 检查是否是casual chat
          const isCasualChat = response.action_result?.is_casual_chat
          
          // 先显示即时响应
          const initialMessage = {
            sender: 'agent',
            text: response.initial_response,
            timestamp: new Date().toISOString(),
            isInitial: true,
            isCasualChat: isCasualChat  // 标记是否为casual chat
          }
          currentMessages.value.push(initialMessage)
          
          // 如果有工具执行反馈，稍后添加
          if (response.execution_feedback) {
            setTimeout(() => {
              const feedbackMessage = {
                sender: 'agent',
                text: response.execution_feedback,
                timestamp: new Date().toISOString(),
                isFeedback: true
              }
              currentMessages.value.push(feedbackMessage)
            }, 500) // 延迟500ms显示执行结果
          }
        } else if (response.message) {
          // 兼容旧格式
          currentMessages.value.push(response.message)
        }
        
        // 先处理动作结果（更新 planId）
        handleActionResult(response)
        
        // 然后更新可视化（使用正确的 planId）
        if (response.visualization) {
          updateVisualization(response.visualization)
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
      
      try {
        // 先添加用户消息到消息列表
        const userMessage = {
          sender: 'user',
          text: messageText,
          timestamp: new Date().toISOString()
        }
        currentMessages.value.push(userMessage)
        
        await chatApi.sendMessageStream(
          selectedConversationId.value,
          messageText,
          (chunk) => {
            callbacks.onChunk(chunk)
          },
          (complete) => {
            callbacks.onComplete(complete)
            
            // 流式响应完成后，更新可视化
            if (complete.visualization) {
              updateVisualization(complete.visualization)
            }
            
            // 将完整消息同步到currentMessages（ChatInterface已处理显示）
            if (complete.full_text) {
              const agentMessage = {
                sender: 'agent',
                text: complete.full_text,
                timestamp: new Date().toISOString()
              }
              currentMessages.value.push(agentMessage)
            }
          },
          (error) => {
            callbacks.onError(error)
            // 添加错误消息
            currentMessages.value.push({ 
              sender: 'agent', 
              text: 'Sorry, an error occurred while processing your message.',
              timestamp: new Date().toISOString()
            })
          }
        )
      } catch (error) {
        console.error('Failed to send message:', error)
        callbacks.onError('Failed to send message. Please try again.')
      }
    }
    
    const updateStableTasksData = (newTasks) => {
      if (!newTasks || !Array.isArray(newTasks)) {
        console.log('❌ Invalid newTasks data:', newTasks)
        return
      }
      
      const currentTime = Date.now()
      const timeSinceLastUpdate = currentTime - lastTasksUpdateTime.value
      
      // 检查数据是否真的有变化（避免无用更新）
      const dataChanged = 
        stableTasksData.value.length !== newTasks.length ||
        !stableTasksData.value.every((task, index) => task.id === newTasks[index]?.id)
      
      if (dataChanged || timeSinceLastUpdate > 1000) { // 至少1秒间隔或数据确实变化
        console.log('📦 Updating stable tasks data:', newTasks.length, 'tasks', 'dataChanged:', dataChanged)
        
        // 格式化任务数据
        const formattedTasks = newTasks.map(task => ({
          ...task,
          shortName: task.name.replace(`[计划]`, '').trim() || task.name,
        }))
        
        const isFirstLoad = stableTasksData.value.length === 0
        console.log('🔍 Is first load?', isFirstLoad)
        
        // 保持引用稳定性：只有在数据结构真正变化时才创建新引用
        if (isFirstLoad) {
          // 首次加载 - 创建新引用
          console.log('✨ First load: Creating new array reference')
          stableTasksData.value = formattedTasks
        } else {
          // 更新现有数据，保持引用稳定性
          console.log('🔄 Updating: Keeping array reference stable')
          stableTasksData.value.splice(0, stableTasksData.value.length, ...formattedTasks)
        }
        lastTasksUpdateTime.value = currentTime
        
        // 更新缓存
        window.currentPlanTasks = stableTasksData.value
        window.currentPlanId = parseInt(planId.value)
      } else {
        console.log('📌 No significant change in tasks data, keeping stable reference')
      }
    }

    const updateVisualization = async (visualization) => {
      console.log('🎨 updateVisualization called with type:', visualization.type, 'planId:', planId.value)
      visualizationType.value = visualization.type || 'none'
      visualizationConfig.value = visualization.config || {}
      
      // 如果是任务树视图，处理任务数据
      if ((visualization.type === 'task_tree' || visualization.type === 'task_list') && planId.value) {
        console.log('📊 Task tree/list visualization detected, planId:', planId.value)
        
        // 首先检查后端是否已经提供了任务数据
        const backendTasks = visualization.data
        console.log('🔍 Backend provided tasks:', backendTasks?.length || 0, 'tasks')
        
        // 始终通过API获取最新的任务数据，确保数据格式一致
        console.log('🔄 Always loading fresh data from API for consistency')
        await loadPlanTasks()
        
        // 可选：如果后端数据可用，可以用作备用验证
        if (backendTasks && Array.isArray(backendTasks) && backendTasks.length > 0) {
          console.log('📋 Backend also provided tasks:', backendTasks.length, 'tasks (used for validation)')
        }
        
        // 设置可视化数据为稳定的任务数据引用
        visualizationData.value = stableTasksData.value
        
      } else {
        console.log('⏩ Skipping task loading - not task tree/list or no planId')
        // 对于非任务视图，设置默认数据
        visualizationData.value = visualization.data || {}
      }
    }
    
    const loadPlanTasks = async () => {
      console.log('🔍 loadPlanTasks called, planId:', planId.value)
      if (!planId.value) {
        console.log('❌ No planId, returning early')
        return
      }
      
      try {
        console.log(`📡 Fetching tasks for plan ${planId.value}`)
        const response = await api.get(`/plans/${planId.value}/tasks`)
        console.log('📦 Raw tasks response:', response.data.length, 'tasks')
        
        // 使用稳定数据更新方法
        updateStableTasksData(response.data)
        
        // 只有在当前显示任务树时才更新可视化数据
        if (visualizationType.value === 'task_tree' || visualizationType.value === 'task_list') {
          visualizationData.value = stableTasksData.value
        }
        
        visualizationConfig.value = { ...visualizationConfig.value, loading: false }
      } catch (error) {
        console.error('Failed to load plan tasks:', error)
        visualizationConfig.value = { ...visualizationConfig.value, loading: false, error: '加载任务失败' }
      }
    }

    const handleVisualizationAction = (action) => {
      console.log('🔄 handleVisualizationAction called with:', action)
      console.log('🔍 Current planId.value:', planId.value)
      
      // 处理不同类型的可视化动作
      if (action.type === 'select_task') {
        // 处理任务选择事件，显示任务详情
        showTaskDetail(action.task)
      } else if (action.type === 'refresh_tasks') {
        // 处理刷新任务事件
        console.log('🔄 Refresh tasks requested, planId:', planId.value)
        if (planId.value) {
          console.log('✅ planId exists, calling loadPlanTasks...')
          loadPlanTasks()
        } else {
          // 如果没有planId，提示用户先创建plan
          console.log('❌ No planId, showing warning')
          ElMessage.warning('请先创建一个计划，然后才能查看任务')
        }
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
    
    const handleConversationDeleted = () => {
      // 处理会话删除后的清理
      selectedConversationId.value = null
      currentMessages.value = []
      
      // 重置可视化状态
      visualizationType.value = 'help_menu'
      visualizationData.value = [
        { command: "Create Plan", description: "Create a new research plan" },
        { command: "Show Plans", description: "View all plan lists" },
        { command: "Execute Plan", description: "Execute tasks in specified plan" },
        { command: "Check Status", description: "View plan or task execution status" },
        { command: "Help", description: "Display help information" }
      ]
      visualizationConfig.value = {}
      
      console.log('All conversations deleted, showing welcome state')
    }
    
    const handleActionResult = (response) => {
      console.log('🎯 handleActionResult called with response:', response)
      console.log('🔍 Current planId.value before processing:', planId.value)
      
      // 处理需要执行的后续动作
      if (response && response.action_result) {
        console.log('📋 action_result found:', response.action_result)
        console.log('📋 action_result.plan_id:', response.action_result.plan_id)
        console.log('📋 response.intent:', response.intent)
        
        // 通用的 plan_id 同步逻辑 - 只要 action_result 中有 plan_id 就更新
        if (response.action_result.plan_id !== undefined && response.action_result.plan_id !== null) {
          const newPlanId = parseInt(response.action_result.plan_id)
          const currentPlanId = parseInt(planId.value)
          console.log('🧐 Plan ID sync check - newPlanId:', newPlanId, 'currentPlanId:', currentPlanId)
          if (!isNaN(newPlanId) && newPlanId !== currentPlanId) {
            console.log('🔄 Plan ID change detected:', planId.value, '->', newPlanId)
            planId.value = newPlanId
            console.log('✅ Updated planId.value to:', planId.value)
            // 清除任务缓存，强制重新加载
            console.log('🗑️ Clearing task cache for plan ID change')
            window.currentPlanTasks = null
            window.currentPlanId = null
          } else {
            console.log('⏹️ Plan ID unchanged or invalid newPlanId')
          }
        } else {
          console.log('❌ No plan_id found in action_result')
        }
        
        // 处理plan创建结果，维护planID
        if (response.intent === 'create_plan' && response.action_result.plan_id) {
          console.log('✅ Plan created with ID:', planId.value)
        }
        
        // 处理plan执行
        if (response.action_result.should_execute && response.intent === 'execute_plan') {
          const execPlanId = response.action_result.plan_id
          if (execPlanId) {
            console.log('✅ Switched to plan for execution:', planId.value)
            executePlan(execPlanId)
          }
        }
        
        // 处理显示特定plan的任务
        if (response.intent === 'show_tasks') {
          if (response.action_result.plan_id) {
            console.log('✅ Switched to plan for showing tasks:', planId.value)
          } else {
            console.log('❌ show_tasks intent but no plan_id found in action_result')
            console.log('❌ action_result:', response.action_result)
          }
        }
        
        // 处理查询状态 - 如果是查询特定plan的状态
        if (response.intent === 'query_status' && response.action_result.plan_id) {
          console.log('✅ Switched to plan for status query:', planId.value)
        }
        
        // 处理plan列表显示
        if (response.intent === 'list_plans' && response.action_result.plans) {
          // 如果当前没有planId，设置第一个plan作为当前plan
          if (!planId.value && response.action_result.plans.length > 0) {
            planId.value = response.action_result.plans[0].id
            console.log('Set current plan to first available:', planId.value)
          }
        }
      } else {
        console.log('❌ No action_result found in response')
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
      // 初始化时尝试加载第一个会话（不需要plan依赖）
      if (!selectedConversationId.value) {
        try {
          const conversations = await chatApi.getAllConversations()
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