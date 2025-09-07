import axios from 'axios'

const api = axios.create({
  baseURL: 'http://127.0.0.1:8000',
  timeout: 30000000, // 5分钟超时，匹配服务器配置
  headers: {
    'Content-Type': 'application/json',
  },
})

// Plan Management
export const plansApi = {
  async generatePlan(goal, sections, title, style, notes) {
    const payload = { goal }
    
    // Only include parameters that are actually provided
    if (sections !== undefined && sections > 0) {
      payload.sections = sections
    }
    if (title && title.trim()) {
      payload.title = title.trim()
    }
    if (style && style.trim()) {
      payload.style = style.trim()
    }
    if (notes && notes.trim()) {
      payload.notes = notes.trim()
    }
    
    const response = await api.post('/plans/propose', payload)
    return response.data
  },

  async approvePlan(title, tasks) {
    const response = await api.post('/plans/approve', {
      title,
      tasks
    })
    return response.data
  },

  async getPlans() {
    const response = await api.get('/plans')
    return response.data.plans || []
  },

  async getPlanTasks(planId) {
    const response = await api.get(`/plans/${planId}/tasks`)
    return response.data
  },

  async getChatHistory(planId) {
    const response = await api.get(`/plans/${planId}/chathistory`);
    return response.data.history || [];
  },

  async getPlanOutput(planId) {
    const response = await api.get(`/plans/by-id/${planId}/assembled`)
    return response.data
  },

  async executePlan(planId, options = {}) {
    const response = await api.post('/run', {
      plan_id: planId,
      enable_evaluation: true,
      ...options
    })
    return response.data
  },

  async deletePlan(planId) {
    const response = await api.delete(`/plans/${planId}`)
    return response.data
  },

  async executeAgentCommand(planId, command) {
    const response = await api.post('/agent/command', {
      plan_id: planId,
      command: command,
    });
    return response.data;
  },
}

// Task Management
export const tasksApi = {
  async getAllTasks() {
    const response = await api.get('/tasks')
    return response.data
  },

  async getTask(taskId) {
    const response = await api.get(`/tasks/${taskId}`)
    return response.data
  },

  async createTask(name, taskType = 'atomic', parentId = null, planId = null, prompt = null, contexts = null) {
    const payload = {
      name,
      task_type: taskType,
    };
    if (parentId !== null) {
      payload.parent_id = parentId;
    }
    if (planId !== null) {
      payload.plan_id = planId;
    }
    if (prompt && prompt.trim()) {
      payload.prompt = prompt;
    }
    if (contexts && contexts.length > 0) {
      payload.contexts = contexts;
    }
    const response = await api.post('/tasks', payload);
    return response.data;
  },

  async updateTask(taskId, updates) {
    const response = await api.put(`/tasks/${taskId}`, updates)
    return response.data
  },

  async updateTaskInput(taskId, prompt) {
    const response = await api.put(`/tasks/${taskId}/input`, { prompt })
    return response.data
  },

  async getTaskInput(taskId) {
    const response = await api.get(`/tasks/${taskId}/input`)
    return response.data.prompt
  },

  async getTaskOutput(taskId) {
    const response = await api.get(`/tasks/${taskId}/output`);
    return response.data.content;
  },

  async rerunTask(taskId, options = {}) {
    const response = await api.post(`/tasks/${taskId}/rerun`, options)
    return response.data
  },

  async rerunSubtree(taskId, options = {}) {
    const response = await api.post(`/tasks/${taskId}/rerun/subtree`, {
      include_parent: true,
      ...options
    })
    return response.data
  },

  async rerunMultipleTasks(taskIds, options = {}) {
    const response = await api.post('/tasks/rerun/selected', {
      task_ids: taskIds,
      ...options
    })
    return response.data
  },

  async moveTask(taskId, newParentId) {
    const response = await api.post(`/tasks/${taskId}/move`, {
      new_parent_id: newParentId
    })
    return response.data
  },

  async getTaskChildren(taskId) {
    const response = await api.get(`/tasks/${taskId}/children`)
    return response.data.children
  },

  async getTaskSubtree(taskId) {
    const response = await api.get(`/tasks/${taskId}/subtree`)
    return response.data.subtree
  },

  async deleteTask(taskId) {
    const response = await api.delete(`/tasks/${taskId}`);
    return response.data;
  },

  // Context related APIs moved here
  async getTaskContextSnapshots(taskId) {
    const response = await api.get(`/tasks/${taskId}/context/snapshots`)
    return response.data
  },

  async getTaskContextSnapshot(taskId, label) {
    const response = await api.get(`/tasks/${taskId}/context/snapshots/${label}`)
    return response.data
  },

  async updateTaskOutput(taskId, content) {
    const response = await api.put(`/tasks/${taskId}/output`, { content })
    return response.data
  },

  async createTaskContext(taskId, label, content) {
    const response = await api.post(`/tasks/${taskId}/context/snapshots`, { label, content })
    return response.data
  },

  async updateTaskContext(taskId, label, contextData) {
    // The payload should contain content, sections, and/or meta
    const response = await api.put(`/tasks/${taskId}/context/snapshots/${label}`, contextData)
    return response.data
  },

  async deleteTaskContext(taskId, label) {
    const response = await api.delete(`/tasks/${taskId}/context/snapshots/${label}`)
    return response.data
  },

  async regenerateTaskContext(taskId) {
    const response = await api.post(`/tasks/${taskId}/context/regenerate`);
    return response.data;
  }
}

// Evaluation API
export const evaluationApi = {
  async setEvaluationConfig(taskId, config) {
    const response = await api.post(`/tasks/${taskId}/evaluation/config`, config)
    return response.data
  },

  async getEvaluationConfig(taskId) {
    const response = await api.get(`/tasks/${taskId}/evaluation/config`)
    return response.data
  },

  async getEvaluationHistory(taskId) {
    const response = await api.get(`/tasks/${taskId}/evaluation/history`)
    return response.data
  },

  async getLatestEvaluation(taskId) {
    const response = await api.get(`/tasks/${taskId}/evaluation/latest`)
    return response.data
  },

  async overrideEvaluation(taskId, payload) {
    const response = await api.post(`/tasks/${taskId}/evaluation/override`, payload)
    return response.data
  },

  async executeTaskWithEvaluation(taskId, payload = {}) {
    const response = await api.post(`/tasks/${taskId}/execute/with-evaluation`, payload)
    return response.data
  },

  async getEvaluationStats() {
    const response = await api.get('/evaluation/stats')
    return response.data
  },

  async clearEvaluationHistory(taskId) {
    const response = await api.delete(`/tasks/${taskId}/evaluation/history`)
    return response.data
  }
}

export const chatApi = {
  async getConversation(conversationId) {
    const response = await api.get(`/chat/conversations/${conversationId}`);
    return response.data;
  },

  async getAllConversations() {
    const response = await api.get(`/chat/conversations`);
    return response.data;
  },

  async createConversation(data) {
    const response = await api.post(`/chat/conversations`, data);
    return response.data;
  },

  async updateConversation(conversationId, data) {
    const response = await api.put(`/chat/conversations/${conversationId}`, data);
    return response.data;
  },

  async deleteConversation(conversationId) {
    const response = await api.delete(`/chat/conversations/${conversationId}`);
    return response.data;
  },

  async sendMessage(conversationId, message) {
    const response = await api.post(`/chat/conversations/${conversationId}/messages`, { 
      text: message,
      sender: 'user'
    });
    return response.data;
  },

  async sendMessageStream(conversationId, message, onChunk, onComplete, onError) {
    // Use fetch with ReadableStream for POST request
    const response = await fetch(`http://127.0.0.1:8000/chat/conversations/${conversationId}/messages/stream`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ sender: 'user', text: message })
    });

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    const processLine = (line) => {
      if (line.startsWith('data: ')) {
        const jsonStr = line.slice(6);
        if (jsonStr.trim()) {
          try {
            const data = JSON.parse(jsonStr);
            
            if (data.type === 'chunk') {
              onChunk?.(data.content, data.accumulated);
            } else if (data.type === 'complete') {
              onComplete?.(data.full_text, data.message_id);
            } else if (data.type === 'error') {
              onError?.(data.message);
            }
          } catch (e) {
            console.error('Failed to parse SSE data:', e);
          }
        }
      }
    };

    try {
      while (true) {
        const { done, value } = await reader.read();
        
        if (done) break;
        
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        
        // Process all complete lines
        buffer = lines.pop() || '';
        
        for (const line of lines) {
          processLine(line);
        }
      }
      
      // Process any remaining buffer
      if (buffer) {
        processLine(buffer);
      }
    } catch (error) {
      console.error('Stream reading error:', error);
      onError?.(error.message);
    } finally {
      reader.releaseLock();
    }
  }
};

// Health Check
export const healthApi = {
  async checkLLMHealth() {
    try {
      const response = await api.get('/health/llm')
      return response.data
    } catch (error) {
      console.error('Health check failed:', error)
      return { ping_ok: false, error: error.message }
    }
  }
}

// Error Handling
api.interceptors.response.use(
  response => response,
  error => {
    const message = error.response?.data?.detail || error.message;

    // For 404 errors on task outputs, resolve gracefully with empty content.
    // This is not considered an application error, as tasks may not have outputs yet.
    if (error.response?.status === 404 && error.config.url.includes('/output')) {
      return Promise.resolve({ data: { content: '' } }); 
    }
    
    // For other errors, log them (except other 404s which are also handled gracefully).
    if (error.response?.status !== 404) {
      console.error('API Error:', message);
    }
    
    // Re-throw a structured error for other error cases so they can be caught by callers.
    throw {
      ...error,
      message,
      status: error.response?.status
    };
  }
);

export default api