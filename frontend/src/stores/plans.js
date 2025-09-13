import { defineStore } from 'pinia'
import { plansApi, tasksApi, chatApi } from '../services/api.js'

export const usePlansStore = defineStore('plans', {
  state: () => ({
    plans: [],
    currentPlan: null,
    currentPlanTasks: [],
    currentChatHistory: [],
    // Granular loading states
    plansLoading: false,
    planDetailsLoading: false,
    planExecuting: false,
    planGenerating: false,
    error: null,
    executionStatus: {},
    forceUpdateTrigger: 0
  }),

  getters: {
    planNames: (state) => state.plans.map(plan => plan.title),
    
    getPlanById: (state) => (planId) => {
      return state.plans.find(plan => plan.id === planId)
    },
    
    getTaskById: (state) => (taskId) => {
      return state.currentPlanTasks.find(task => task.id === parseInt(taskId))
    },
    
    plannedTasks: (state) => {
      return state.currentPlanTasks.filter(task => task.status === 'pending')
    },
    
    completedTasks: (state) => {
      return state.currentPlanTasks.filter(task => task.status === 'done')
    },
    
    executionInProgress: (state) => {
      return Object.values(state.executionStatus).some(status => status === 'executing')
    }
  },

  actions: {
    // Load all plans
    async loadPlans() {
      // Only show the full-page loader on the initial load
      if (this.plans.length === 0) {
        this.plansLoading = true
      }
      try {
        this.plans = await plansApi.getPlans()
      } catch (error) {
        this.error = error.message
      } finally {
        this.plansLoading = false // Always turn off loading state
      }
    },

    // Load specific plan details
    async loadPlanDetails(planId) {
      this.planDetailsLoading = true;
      // We fetch conversation history separately now, so clear history here.
      this.currentChatHistory = []; 
      try {
        // Ensure plans are loaded first
        if (this.plans.length === 0) {
          await this.loadPlans();
        }
        
        const planFromList = this.plans.find(p => p.id === planId);
        if (planFromList) {
          this.currentPlan = { ...planFromList };
        } else {
          this.currentPlan = null;
        }

        const tasks = await plansApi.getPlanTasks(planId);
        this.currentPlanTasks = [...(tasks || [])];
        this.error = null;
        return this.currentPlanTasks;
      } catch (error) {
        this.error = error.message;
        this.currentPlanTasks = [];
      } finally {
        this.planDetailsLoading = false;
      }
    },

    async loadConversationHistory(conversationId) {
      try {
        const conversation = await chatApi.getConversation(conversationId);
        this.currentChatHistory = conversation.messages || [];
      } catch (error) {
        this.error = `Failed to load conversation history: ${error.message}`;
        this.currentChatHistory = [];
      }
    },

    clearChatHistory() {
      this.currentChatHistory = [];
    },

    // Load plan output
    async loadPlanOutput(planId) {
      try {
        return await plansApi.getPlanOutput(planId)
      } catch (error) {
        this.error = error.message
        return null
      }
    },

    // This is now a legacy method, streaming is handled by the chat interface
    async generateAndApprovePlan(goal, sections, title = '', style = '', notes = '') {
      this.planGenerating = true
      try {
        const result = await plansApi.generatePlan(goal, sections, title, style, notes)
        await this.loadPlans()
        return result.title || title || `Plan_${Date.now()}`
      } catch (error) {
        this.error = error.message
        throw error
      } finally {
        this.planGenerating = false
      }
    },

    handlePlanStreamEvent(event) {
      console.log("Received stream event in store:", event);
      switch (event.stage) {
        case 'initialization':
          this.planGenerating = true;
          this.currentPlanTasks = [];
          this.currentPlan = null;
          break;
        case 'plan_created':
          this.currentPlan = { id: event.plan_id, title: event.title, description: '' };
          // Add to plans list if not already there
          if (!this.plans.some(p => p.id === event.plan_id)) {
            this.plans.push({ id: event.plan_id, title: event.title });
          }
          break;
        case 'root_task_created':
        case 'subtask_created':
          const newTask = {
            id: event.subtask_id || event.task_id,
            name: event.subtask_name || event.task_name,
            status: 'pending',
            parent_id: event.parent_id || null, // 确保根任务的parent_id为null
            // Add other relevant fields from the event
          };
          console.log('📝 Creating task:', newTask);
          this.currentPlanTasks = [...this.currentPlanTasks, newTask];
          break;
        case 'completed':
          this.planGenerating = false;
          // The final result might contain the full tree, so we can replace our tasks
          if (event.result && event.result.flat_tree) {
            // 确保flat_tree中的任务也有正确的parent_id字段
            const processedTasks = event.result.flat_tree.map(task => ({
              ...task,
              parent_id: task.parent_id || null // 确保parent_id字段存在
            }));
            console.log('📝 Processing flat_tree tasks:', processedTasks);
            this.currentPlanTasks = processedTasks;
          }
          this.loadPlans(); // Refresh the main plans list

          // Navigate to the new plan's execution view
          if (this.router && event.plan_id) {
            this.router.push({ 
              name: 'PlanExecute', 
              params: { id: event.plan_id } 
            });
          }
          break;
        case 'fatal_error':
          this.planGenerating = false;
          this.error = event.message;
          break;
      }
    },

    async createTask(planId, taskData) {
      this.planDetailsLoading = true; // Set loading state
      try {
        // 将 planId 添加到 taskData 对象中，并传递整个对象
        const payload = { ...taskData, planId };
        await tasksApi.createTask(payload);
        
        // After creating, reload the tasks for the current plan to show the new task
        await this.loadPlanDetails(planId);
      } catch (error) {
        this.error = error.message;
        this.planDetailsLoading = false; // Ensure loading is unset on error
        throw error; // Re-throw for the component to handle if needed
      }
    },

    // Update task information
    async updateTask(taskId, updates) {
      try {
        await tasksApi.updateTask(taskId, updates)
        
        // Refresh plan tasks if we're viewing a plan
        if (this.currentPlan) {
          await this.loadPlanDetails(this.currentPlan.id)
        }
      } catch (error) {
        this.error = error.message
      }
    },

    // Update task input/prompt
    async updateTaskPrompt(taskId, prompt) {
      try {
        await tasksApi.updateTaskInput(taskId, prompt)
      } catch (error) {
        this.error = error.message
      }
    },

    // Update task output
    async updateTaskOutput(taskId, content) {
      try {
        await tasksApi.updateTaskOutput(taskId, content)
        
        // Refresh plan tasks if we're viewing a plan
        if (this.currentPlan) {
          await this.loadPlanDetails(this.currentPlan.id)
        }
      } catch (error) {
        this.error = error.message
      }
    },

    // 任务上下文快照管理
    async createTaskContext(taskId, label, content) {
      try {
        await tasksApi.createTaskContext(taskId, label, content)
      } catch (error) {
        this.error = error.message
      }
    },

    async updateTaskContext(taskId, label, content) {
      try {
        await tasksApi.updateTaskContext(taskId, label, content)
      } catch (error) {
        this.error = error.message
      }
    },

    async deleteTaskContext(taskId, label) {
      try {
        await tasksApi.deleteTaskContext(taskId, label)
      } catch (error) {
        this.error = error.message
      }
    },

    // Execute entire plan
    async executePlan(planId, options = {}) {
      this.planExecuting = true
      try {
        const results = await plansApi.executePlan(planId, {
          enable_evaluation: true,
          ...options
        })
        
        // Update execution status using reactive assignment
        const newExecutionStatus = {}
        results.forEach(result => {
          newExecutionStatus[result.id] = result.status
        })
        this.executionStatus = { ...newExecutionStatus }
        
        // Refresh plan to get updated statuses
        await this.loadPlanDetails(planId)
        
        return results
      } catch (error) {
        this.error = error.message
        throw error
      } finally {
        this.planExecuting = false
      }
    },

    // Re-run single task
    async rerunTask(taskId, options = {}) {
      try {
        const result = await tasksApi.rerunTask(taskId, {
          use_context: true,
          ...options
        })
        
        // Use reactive assignment for execution status
        this.executionStatus = { ...this.executionStatus, [taskId]: result.status }
        
        // Refresh the plan if we're viewing one
        if (this.currentPlan) {
          await this.loadPlanDetails(this.currentPlan.id)
        }
        
        return result
      } catch (error) {
        this.error = error.message
      }
    },

    // Re-run multiple tasks
    async rerunTasks(taskIds, options = {}) {
      try {
        const results = await tasksApi.rerunMultipleTasks(taskIds, options)
        
        // Use reactive assignment for execution status updates
        const statusUpdates = {}
        results.forEach(result => {
          statusUpdates[result.task_id] = result.status
        })
        this.executionStatus = { ...this.executionStatus, ...statusUpdates }
        
        // Refresh plan after re-running
        if (this.currentPlan) {
          await this.loadPlanDetails(this.currentPlan.id)
        }
        
        return results
      } catch (error) {
        this.error = error.message
      }
    },

    async executeAgentCommand(planId, command) {
      // Add user message to history immediately for responsiveness
      this.currentChatHistory.push({ sender: 'user', text: command });
      this.planDetailsLoading = true;
      try {
        const response = await plansApi.executeAgentCommand(planId, command);
        // Add agent response to history
        this.currentChatHistory.push({ sender: 'agent', text: response.response });
        return response;
      } catch (error) {
        const errorMessage = `Error: ${error.message}`;
        this.currentChatHistory.push({ sender: 'agent', text: errorMessage });
        this.error = error.message;
        throw error; // Re-throw for the component to handle
      } finally {
        this.planDetailsLoading = false;
      }
    },

    addUserMessageToHistory(message) {
      this.currentChatHistory.push({
        sender: 'user',
        text: message,
        timestamp: new Date().toISOString()
      });
    },

    async executeAgentCommandStream(conversationId, planId, command, callbacks) {
      this.addUserMessageToHistory(command);
      this.planDetailsLoading = true;

      try {
        const initialResponse = await chatApi.sendMessageStream(
          conversationId,
          command,
          planId,
          callbacks.onChunk,
          (fullText, messageId) => { // onComplete
            const agentMessage = this.currentChatHistory.find(m => m.id === messageId);
            if (agentMessage) {
              agentMessage.text = fullText;
            } else {
              this.currentChatHistory.push({ id: messageId, sender: 'agent', text: fullText });
            }
            callbacks.onComplete(fullText, messageId);
          },
          callbacks.onError,
          (event) => this.handlePlanStreamEvent(event)
        );

        // Handle initial, non-streamed response part
        if (initialResponse && initialResponse.message) {
          this.currentChatHistory.push(initialResponse.message);
        }
        if (initialResponse && initialResponse.visualization) {
          // You might want to handle visualization updates here as well
        }

      } catch (error) {
        const errorMessage = `Error: ${error.message}`;
        this.currentChatHistory.push({ sender: 'agent', text: errorMessage });
        this.error = error.message;
        callbacks.onError(errorMessage);
      } finally {
        this.planDetailsLoading = false;
      }
    },

    // Force update mechanism as fallback
    forceUpdate() {
      this.forceUpdateTrigger++
    },

    getReactiveData() {
      // Trigger dependency on forceUpdateTrigger
      this.forceUpdateTrigger
      return {
        plans: this.plans,
        currentPlan: this.currentPlan,
        currentPlanTasks: this.currentPlanTasks,
        executionStatus: this.executionStatus
      }
    }
  }
})
