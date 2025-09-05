<template>
  <div class="status-dashboard">
    <div class="view-header">
      <h2>状态概览</h2>
    </div>
    
    <div class="status-cards">
      <el-card class="status-card">
        <div class="stat-value">{{ totalTasks }}</div>
        <div class="stat-label">总任务数</div>
      </el-card>
      
      <el-card class="status-card">
        <div class="stat-value text-success">{{ completedTasks }}</div>
        <div class="stat-label">已完成</div>
      </el-card>
      
      <el-card class="status-card">
        <div class="stat-value text-warning">{{ pendingTasks }}</div>
        <div class="stat-label">待执行</div>
      </el-card>
      
      <el-card class="status-card">
        <div class="stat-value text-danger">{{ failedTasks }}</div>
        <div class="stat-label">失败</div>
      </el-card>
    </div>
    
    <div class="status-details" v-if="data.tasks">
      <el-table :data="data.tasks" style="width: 100%">
        <el-table-column prop="id" label="ID" width="80" />
        <el-table-column prop="name" label="任务名称" />
        <el-table-column prop="status" label="状态" width="120">
          <template #default="scope">
            <el-tag :type="getStatusType(scope.row.status)">
              {{ scope.row.status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="task_type" label="类型" width="120" />
      </el-table>
    </div>
  </div>
</template>

<script>
export default {
  name: 'StatusDashboard',
  props: {
    data: {
      type: Object,
      default: () => ({})
    },
    config: {
      type: Object,
      default: () => ({})
    }
  },
  computed: {
    totalTasks() {
      return this.data.total_tasks || 0
    },
    
    completedTasks() {
      return this.data.status_count?.done || 0
    },
    
    pendingTasks() {
      return this.data.status_count?.pending || 0
    },
    
    failedTasks() {
      return this.data.status_count?.failed || 0
    }
  },
  methods: {
    getStatusType(status) {
      const types = {
        'pending': 'info',
        'processing': 'warning',
        'done': 'success',
        'failed': 'danger'
      }
      return types[status] || ''
    }
  }
}
</script>

<style scoped>
.status-dashboard {
  height: 100%;
}

.view-header {
  margin-bottom: 20px;
}

.view-header h2 {
  margin: 0;
  color: #303133;
}

.status-cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 20px;
  margin-bottom: 30px;
}

.status-card {
  text-align: center;
}

.stat-value {
  font-size: 32px;
  font-weight: bold;
  color: #303133;
  margin-bottom: 10px;
}

.stat-label {
  font-size: 14px;
  color: #909399;
}

.text-success {
  color: #67c23a;
}

.text-warning {
  color: #e6a23c;
}

.text-danger {
  color: #f56c6c;
}

.status-details {
  background: white;
  border-radius: 4px;
  padding: 20px;
}
</style>