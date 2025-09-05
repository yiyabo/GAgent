<template>
  <div class="plan-details">
    <div class="plan-header">
      <h3>{{ data.title || '计划详情' }}</h3>
      <div class="plan-meta">
        <el-tag type="info">ID: {{ data.id }}</el-tag>
        <el-tag type="success">任务数: {{ data.total_tasks || 0 }}</el-tag>
        <el-tag type="warning">最大深度: {{ data.max_layer || 0 }}</el-tag>
      </div>
    </div>
    
    <div class="plan-actions" v-if="config.showActions">
      <el-button 
        type="primary" 
        @click="executePlan"
        :disabled="!data.id"
      >
        执行计划
      </el-button>
      <el-button 
        @click="viewTasks"
        :disabled="!data.id"
      >
        查看任务树
      </el-button>
      <el-button 
        type="danger" 
        plain
        @click="deletePlan"
        :disabled="!data.id"
      >
        删除计划
      </el-button>
    </div>
    
    <div class="plan-summary">
      <el-alert 
        :title="`计划「${data.title}」已成功创建`"
        type="success"
        :description="`包含 ${data.total_tasks || 0} 个任务，分为 ${data.max_layer || 0} 层`"
        show-icon
        :closable="false"
      />
    </div>
    
    <div class="next-steps">
      <h4>下一步操作：</h4>
      <ul>
        <li>输入"执行计划 {{ data.id }}"来开始执行</li>
        <li>输入"显示任务"查看任务详情</li>
        <li>输入"查询状态"查看执行进度</li>
      </ul>
    </div>
  </div>
</template>

<script>
export default {
  name: 'PlanDetails',
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
  methods: {
    executePlan() {
      this.$emit('action', {
        type: 'execute_plan',
        plan_id: this.data.id,
        command: `执行计划 ${this.data.id}`
      })
    },
    viewTasks() {
      this.$emit('action', {
        type: 'view_tasks',
        plan_id: this.data.id,
        command: `显示任务 ${this.data.id}`
      })
    },
    deletePlan() {
      this.$confirm('确定要删除这个计划吗？', '警告', {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        type: 'warning'
      }).then(() => {
        this.$emit('action', {
          type: 'delete_plan',
          plan_id: this.data.id,
          command: `删除计划 ${this.data.id}`
        })
      }).catch(() => {})
    }
  }
}
</script>

<style scoped>
.plan-details {
  padding: 20px;
  height: 100%;
  overflow-y: auto;
}

.plan-header {
  margin-bottom: 20px;
}

.plan-header h3 {
  margin: 0 0 10px 0;
  color: #303133;
}

.plan-meta {
  display: flex;
  gap: 10px;
}

.plan-actions {
  margin: 20px 0;
  display: flex;
  gap: 10px;
}

.plan-summary {
  margin: 20px 0;
}

.next-steps {
  margin-top: 30px;
  padding: 15px;
  background: #f5f7fa;
  border-radius: 4px;
}

.next-steps h4 {
  margin: 0 0 10px 0;
  color: #606266;
}

.next-steps ul {
  margin: 0;
  padding-left: 20px;
  color: #909399;
}

.next-steps li {
  margin: 5px 0;
}
</style>