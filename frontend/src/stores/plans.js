import { defineStore } from 'pinia'
import { plansApi, tasksApi } from '../services/api.js'

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
    executionStatus: {}
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
      this.planDetailsLoading = true
      this.currentChatHistory = []; // Clear previous history
      try {
        // Ensure plans are loaded first
        if (this.plans.length === 0) {
          await this.loadPlans();
        }
        this.currentPlan = this.plans.find(p => p.id === planId) || null;

        // Fetch tasks and chat history in parallel
        const [tasks, history] = await Promise.all([
          plansApi.getPlanTasks(planId),
          this.loadChatHistory(planId)
        ]);

        this.currentPlanTasks = tasks || []
        this.error = null
        return this.currentPlanTasks
      } catch (error) {
        this.error = error.message
        this.currentPlanTasks = []
      } finally {
        this.planDetailsLoading = false
      }
    },

    async loadChatHistory(planId) {
      try {
        const history = await plansApi.getChatHistory(planId);
        this.currentChatHistory = history.map(item => ({
          sender: item.sender,
          text: item.message
        }));
        return this.currentChatHistory;
      } catch (error) {
        this.error = error.message;
        this.currentChatHistory = [];
        return [];
      }
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

    // Generate and approve plan
    async generateAndApprovePlan(goal, sections, title = '', style = '', notes = '') {
      this.planGenerating = true
      try {
        // The /plans/propose endpoint now creates the plan directly
        const result = await plansApi.generatePlan(goal, sections, title, style, notes)
        
        // Reload plans list to include the new plan
        await this.loadPlans()
        
        // Return the plan title for success display
        return result.title || title || `Plan_${Date.now()}`
      } catch (error) {
        this.error = error.message
        throw error
      } finally {
        this.planGenerating = false
      }
    },

    async createTask(planId, taskData) {
      this.planDetailsLoading = true; // Set loading state
      try {
        await tasksApi.createTask(
          taskData.name,
          taskData.taskType,
          taskData.parentId,
          planId,
          taskData.prompt,
          taskData.contexts
        );
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
          await this.loadPlanDetails(this.currentPlan)
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
          await this.loadPlanDetails(this.currentPlan)
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
        
        // Update execution status
        this.executionStatus = {}
        results.forEach(result => {
          this.executionStatus[result.id] = result.status
        })
        
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
        
        this.executionStatus[taskId] = result.status
        
        // Refresh the plan if we're viewing one
        if (this.currentPlan) {
          await this.loadPlanDetails(this.currentPlan)
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
        
        results.forEach(result => {
          this.executionStatus[result.task_id] = result.status
        })
        
        // Refresh plan after re-running
        if (this.currentPlan) {
          await this.loadPlanDetails(this.currentPlan)
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
    }
  }
})