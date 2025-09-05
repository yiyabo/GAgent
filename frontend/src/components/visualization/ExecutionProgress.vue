<template>
  <div class="execution-progress">
    <div class="view-header">
      <h2>执行进度</h2>
      <el-tag type="success" v-if="isRunning">
        <i class="el-icon-loading"></i> 执行中
      </el-tag>
    </div>
    
    <div class="progress-summary">
      <el-progress 
        :percentage="overallProgress" 
        :status="progressStatus"
        :stroke-width="20"
      >
        <span>{{ completedCount }} / {{ totalCount }}</span>
      </el-progress>
    </div>
    
    <div class="task-list">
      <div 
        v-for="task in sortedTasks" 
        :key="task.id"
        class="task-item"
        :class="{ 'task-active': task.status === 'processing' }"
      >
        <div class="task-info">
          <span class="task-name">{{ task.name }}</span>
          <el-tag 
            size="small" 
            :type="getStatusType(task.status)"
          >
            {{ getStatusLabel(task.status) }}
          </el-tag>
        </div>
        
        <div class="task-progress" v-if="task.status === 'processing'">
          <el-progress 
            :percentage="50" 
            :show-text="false"
            :indeterminate="true"
            status="success"
          />
        </div>
      </div>
    </div>
  </div>
</template>

<script>
export default {
  name: 'ExecutionProgress',
  props: {
    tasks: {
      type: Array,
      default: () => []
    },
    planId: {
      type: [String, Number],
      default: null
    },
    autoRefresh: {
      type: Boolean,
      default: true
    },
    refreshInterval: {
      type: Number,
      default: 2000
    }
  },
  data() {
    return {
      refreshTimer: null,
      isRunning: false
    }
  },
  computed: {
    totalCount() {
      return this.tasks.length
    },
    
    completedCount() {
      return this.tasks.filter(t => t.status === 'done' || t.status === 'complete').length
    },
    
    failedCount() {
      return this.tasks.filter(t => t.status === 'failed').length
    },
    
    overallProgress() {
      if (this.totalCount === 0) return 0
      return Math.round((this.completedCount / this.totalCount) * 100)
    },
    
    progressStatus() {
      if (this.failedCount > 0) return 'exception'
      if (this.overallProgress === 100) return 'success'
      return ''
    },
    
    sortedTasks() {
      // 将正在处理的任务排在前面
      return [...this.tasks].sort((a, b) => {
        if (a.status === 'processing') return -1
        if (b.status === 'processing') return 1
        if (a.status === 'pending' && b.status !== 'pending') return -1
        if (b.status === 'pending' && a.status !== 'pending') return 1
        return 0
      })
    }
  },
  mounted() {
    this.checkRunningStatus()
    if (this.autoRefresh && this.isRunning) {
      this.startAutoRefresh()
    }
  },
  beforeUnmount() {
    this.stopAutoRefresh()
  },
  watch: {
    tasks: {
      deep: true,
      handler() {
        this.checkRunningStatus()
      }
    }
  },
  methods: {
    checkRunningStatus() {
      this.isRunning = this.tasks.some(t => 
        t.status === 'processing' || t.status === 'pending'
      )
      
      if (!this.isRunning) {
        this.stopAutoRefresh()
      }
    },
    
    startAutoRefresh() {
      this.stopAutoRefresh()
      this.refreshTimer = setInterval(() => {
        this.refreshStatus()
      }, this.refreshInterval)
    },
    
    stopAutoRefresh() {
      if (this.refreshTimer) {
        clearInterval(this.refreshTimer)
        this.refreshTimer = null
      }
    },
    
    async refreshStatus() {
      if (!this.planId) return
      
      // 触发刷新事件
      this.$emit('refresh', this.planId)
    },
    
    getStatusType(status) {
      const types = {
        'pending': 'info',
        'processing': 'warning',
        'done': 'success',
        'complete': 'success',
        'failed': 'danger'
      }
      return types[status] || ''
    },
    
    getStatusLabel(status) {
      const labels = {
        'pending': '待执行',
        'processing': '执行中',
        'done': '已完成',
        'complete': '已完成',
        'failed': '失败'
      }
      return labels[status] || status
    }
  }
}
</script>

<style scoped>
.execution-progress {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.view-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}

.view-header h2 {
  margin: 0;
  color: #303133;
}

.progress-summary {
  background: white;
  padding: 20px;
  border-radius: 4px;
  margin-bottom: 20px;
}

.task-list {
  flex: 1;
  overflow: auto;
  background: white;
  border-radius: 4px;
  padding: 20px;
}

.task-item {
  padding: 15px;
  border: 1px solid #e4e7ed;
  border-radius: 4px;
  margin-bottom: 10px;
  transition: all 0.3s;
}

.task-item:hover {
  box-shadow: 0 2px 12px 0 rgba(0, 0, 0, 0.1);
}

.task-active {
  border-color: #409eff;
  background: #f0f9ff;
}

.task-info {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
}

.task-name {
  font-size: 14px;
  color: #303133;
  flex: 1;
  margin-right: 10px;
}

.task-progress {
  margin-top: 10px;
}
</style>